#!/usr/bin/env python3
"""Read-only, deterministic ship catalog for a WoWS Legends installation.

The installed GameParams ship keys and their exact live Hull model paths are
the source of truth. A row is selectable when that live model also resolves to
a complete five-part hull geometry set with supported package storage. Fixed
legacy diffuse filenames are not required because extraction resolves textures
from ModelUber material references. The GameParams payload is decoded in memory;
no game files are written. Geometry decoding and final assembly validation run
only during extraction.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import pickle
import re
import struct
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

TOOL_DIR = Path(__file__).resolve().parent
BLENDER_EXTRACTOR_DIR = TOOL_DIR.parent / "blender_extractor"
if str(BLENDER_EXTRACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_EXTRACTOR_DIR))

from legends_assets.core import (  # noqa: E402
    AssetEntry,
    ExtractionError,
    IdxFormatError,
    UnsafePathError,
    assets_from_index,
    parse_legends_idx,
    read_asset_bytes,
)


HULL_SUFFIXES = ("", "_Bow", "_MidFront", "_MidBack", "_Stern")
SHIP_INDEX_RE = re.compile(
    r"(?:^|_)(?:T(?P<tier>\d+)(?:L)?_)?"
    r"(?P<ship_code>P(?P<nation>[A-Z])S(?P<class>[A-Z])(?P<number>\d+))"
    r"(?:_(?P<label>.+))?$",
    re.IGNORECASE,
)
HULL_RESOURCE_RE = re.compile(
    r"^(?P<nation>[A-Z])S(?P<class>[A-Z])(?P<number>\d+)"
    r"(?:_(?P<label>.+))?$",
    re.IGNORECASE,
)
UPDATE_RE = re.compile(r"^zupd(?P<number>\d+)(?:_|$)", re.IGNORECASE)
SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:_[A-Za-z]{2,4})?$")
SHIP_KEY_RE = re.compile(
    r"^(?P<ship_code>P(?P<nation>[A-Z])S(?P<class>[ABCDS])(?P<number>\d+))"
    r"(?:_|$)",
    re.IGNORECASE,
)

MO_MAGIC_LITTLE = b"\xde\x12\x04\x95"
MO_MAGIC_BIG = b"\x95\x04\x12\xde"
MO_HEADER_SIZE = 7 * 4
MAX_MO_FILE_BYTES = 64 * 1024 * 1024
MAX_MO_MESSAGES = 1_000_000
MAX_MO_HASH_ENTRIES = 2_000_000
MAX_GAME_PARAMS_BYTES = 128 * 1024 * 1024
GAME_PARAMS_PATHS = ("content/GameParams.data", "content/GameParams_py2.data")
MODERN_ERA_MARKER = "modernera"

NATIONS = {
    "A": "USA",
    "B": "United Kingdom",
    "F": "France",
    "G": "Germany",
    "H": "Netherlands",
    "I": "Italy",
    "J": "Japan",
    "R": "USSR",
    "S": "Spain",
    "U": "Commonwealth",
    "V": "Pan-America",
    "W": "Europe",
    "Z": "Pan-Asia",
}
SHIP_CLASSES = {
    "A": "Aircraft carrier",
    "B": "Battleship",
    "C": "Cruiser",
    "D": "Destroyer",
    "S": "Submarine",
}


class NeutralObject:
    """Inert target for Cython objects in the trusted local game archive."""

    def __init__(self, *args: Any) -> None:
        self.constructor_args = args
        self.state: Any = None

    def __setstate__(self, state: Any) -> None:
        self.state = state


def _neutral_factory(module: str, name: str, *args: Any) -> NeutralObject:
    return NeutralObject(module, name, *args)


class InertGameParamsUnpickler(pickle.Unpickler):
    """Prevent GameParams pickle data from resolving imported callables."""

    def find_class(self, module: str, name: str) -> Any:
        if module in ("__builtin__", "builtins") and name in (
            "set",
            "frozenset",
        ):
            return {"set": set, "frozenset": frozenset}[name]
        if name.startswith("__pyx_unpickle_"):
            return functools.partial(_neutral_factory, module, name)
        return type(name, (NeutralObject,), {"__module__": "__inert__"})


class GameParamsFormatError(ValueError):
    """Raised when Legends GameParams data is absent or malformed."""


class MoFormatError(ValueError):
    """Raised when a GNU MO file is structurally unsafe or unsupported."""


def normalize_language(language: str) -> str:
    """Return a path-safe game language token in canonical lowercase form."""
    if not isinstance(language, str) or LANGUAGE_RE.fullmatch(language) is None:
        raise ValueError(
            "language must match LL or LLL_RR using ASCII letters only "
            f"(received {language!r})"
        )
    return language.lower()


def find_global_mo(game_dir: Path, language: str) -> tuple[Path, str]:
    """Resolve global.mo inside the game, preferring res/texts over texts."""
    normalized = normalize_language(language)
    game_root = game_dir.resolve()
    candidates = (
        game_root / "res" / "texts" / normalized / "LC_MESSAGES" / "global.mo",
        game_root / "texts" / normalized / "LC_MESSAGES" / "global.mo",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(game_root)
        except ValueError as exc:
            raise ValueError(
                f"global.mo resolves outside the game directory: {candidate}"
            ) from exc
        return resolved, normalized
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"global.mo for language {normalized!r} was not found; searched: {searched}"
    )


def _checked_mo_range(
    file_size: int,
    offset: int,
    length: int,
    *,
    label: str,
) -> tuple[int, int]:
    """Validate a bounded byte range without trusting MO-provided offsets."""
    if offset < 0 or length < 0:
        raise MoFormatError(f"{label} has a negative offset or length")
    end = offset + length
    if offset > file_size or end > file_size:
        raise MoFormatError(f"{label} range {offset}:{end} exceeds MO size {file_size}")
    return offset, end


def read_mo_exact(
    mo_path: Path,
    message_keys: Iterable[str],
) -> dict[str, str]:
    """Read only explicitly requested GNU MO keys with strict UTF-8 values.

    The empty metadata message and every unrelated entry are deliberately
    left undecoded. This makes the reader tolerant of malformed gettext
    metadata while preserving exact-key semantics for ship names.
    """
    requested: dict[bytes, str] = {}
    for key in message_keys:
        if not isinstance(key, str) or not key or "\x00" in key:
            raise ValueError(f"invalid MO message key: {key!r}")
        encoded = key.encode("utf-8")
        requested[encoded] = key
    if not requested:
        return {}

    size = mo_path.stat().st_size
    if size > MAX_MO_FILE_BYTES:
        raise MoFormatError(
            f"MO file is too large ({size} bytes; maximum {MAX_MO_FILE_BYTES})"
        )
    data = mo_path.read_bytes()
    if len(data) != size:
        raise MoFormatError(
            f"MO file size changed while reading ({size} -> {len(data)})"
        )
    if len(data) < MO_HEADER_SIZE:
        raise MoFormatError(
            f"MO header is truncated ({len(data)} < {MO_HEADER_SIZE} bytes)"
        )

    magic_bytes = data[:4]
    if magic_bytes == MO_MAGIC_LITTLE:
        byte_order = "<"
    elif magic_bytes == MO_MAGIC_BIG:
        byte_order = ">"
    else:
        raise MoFormatError(f"invalid GNU MO magic bytes: {magic_bytes.hex()}")
    (
        _magic,
        revision,
        message_count,
        original_table_offset,
        translation_table_offset,
        hash_size,
        hash_offset,
    ) = struct.unpack_from(f"{byte_order}7I", data, 0)

    major_revision = revision >> 16
    if major_revision not in (0, 1):
        raise MoFormatError(f"unsupported GNU MO major revision {major_revision}")
    if message_count > MAX_MO_MESSAGES:
        raise MoFormatError(
            f"MO message count {message_count} exceeds {MAX_MO_MESSAGES}"
        )
    table_bytes = message_count * 8
    _checked_mo_range(
        len(data),
        original_table_offset,
        table_bytes,
        label="original string table",
    )
    _checked_mo_range(
        len(data),
        translation_table_offset,
        table_bytes,
        label="translation string table",
    )
    if hash_size > MAX_MO_HASH_ENTRIES:
        raise MoFormatError(
            f"MO hash entry count {hash_size} exceeds {MAX_MO_HASH_ENTRIES}"
        )
    if hash_size:
        _checked_mo_range(
            len(data),
            hash_offset,
            hash_size * 4,
            label="hash table",
        )

    result: dict[str, str] = {}
    for index in range(message_count):
        original_length, original_offset = struct.unpack_from(
            f"{byte_order}2I",
            data,
            original_table_offset + index * 8,
        )
        translation_length, translation_offset = struct.unpack_from(
            f"{byte_order}2I",
            data,
            translation_table_offset + index * 8,
        )
        original_start, original_end = _checked_mo_range(
            len(data),
            original_offset,
            original_length,
            label=f"original string {index}",
        )
        translation_start, translation_end = _checked_mo_range(
            len(data),
            translation_offset,
            translation_length,
            label=f"translation string {index}",
        )
        if original_end >= len(data) or data[original_end] != 0:
            raise MoFormatError(
                f"original string {index} lacks its terminating NUL byte"
            )
        if translation_end >= len(data) or data[translation_end] != 0:
            raise MoFormatError(
                f"translation string {index} lacks its terminating NUL byte"
            )

        original_bytes = data[original_start:original_end]
        exact_key = requested.get(original_bytes)
        if exact_key is None:
            continue
        translation_bytes = data[translation_start:translation_end]
        try:
            translation = translation_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MoFormatError(
                f"translation for exact key {exact_key!r} is not UTF-8"
            ) from exc
        previous = result.get(exact_key)
        if previous is not None and previous != translation:
            raise MoFormatError(f"MO contains conflicting duplicate key {exact_key!r}")
        result[exact_key] = translation
    return result


def _usable_localized_name(value: str | None) -> str | None:
    """Treat gettext missing sentinels as absent without altering valid text."""
    if value is None or value.strip() in {"", "!"}:
        return None
    return value


def localized_name_for_code(
    messages: Mapping[str, str],
    ship_code: str,
) -> str | None:
    """Prefer the exact playable name key, then its exact _FULL fallback."""
    key = f"IDS_{ship_code}"
    primary = _usable_localized_name(messages.get(key))
    if primary is not None:
        return primary
    return _usable_localized_name(messages.get(f"{key}_FULL"))


def parse_index_identity(index_filename: str) -> dict[str, object] | None:
    """Parse the ship code embedded in base, zupd, and T8L-style filenames."""
    stem = Path(index_filename).stem
    match = SHIP_INDEX_RE.search(stem)
    if match is None:
        return None
    label_token = match.group("label") or match.group("ship_code")
    return {
        "ship_code": match.group("ship_code").upper(),
        "nation_code": match.group("nation").upper(),
        "class_code": match.group("class").upper(),
        "tier": (int(match.group("tier")) if match.group("tier") is not None else None),
        "label": _humanize(label_token),
    }


def _humanize(value: str) -> str:
    return " ".join(part for part in value.split("_") if part)


def _safe_slug(value: str) -> str:
    slug = SAFE_SLUG_RE.sub("_", value).strip("._-")
    if not slug or slug in {".", ".."}:
        raise ValueError(f"cannot create a safe output slug from {value!r}")
    return slug


def _update_sequence(index_filename: str) -> int:
    match = UPDATE_RE.match(Path(index_filename).stem)
    return int(match.group("number")) if match is not None else -1


def _path_parts(entry: AssetEntry) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(entry.virtual_path).parts)


def find_complete_hull_geometries(
    entries: Sequence[AssetEntry],
) -> list[tuple[str, str, list[AssetEntry]]]:
    """Return every complete hull as (virtual parent, base, ordered geometry)."""
    by_parent: dict[str, dict[str, AssetEntry]] = {}
    parent_display: dict[str, str] = {}
    base_display: dict[str, str] = {}
    for entry in entries:
        path = PurePosixPath(entry.virtual_path)
        if (
            entry.extension != ".geometry"
            or "ship" not in _path_parts(entry)
            or len(path.parts) < 2
        ):
            continue
        base = path.parent.name
        expected = {f"{base}{suffix}".casefold() for suffix in HULL_SUFFIXES}
        if path.stem.casefold() not in expected:
            continue
        key = path.parent.as_posix().casefold()
        by_parent.setdefault(key, {})[path.stem.casefold()] = entry
        parent_display[key] = path.parent.as_posix()
        base_display[key] = base

    complete: list[tuple[str, str, list[AssetEntry]]] = []
    for key, by_stem in by_parent.items():
        base = base_display[key]
        names = [f"{base}{suffix}".casefold() for suffix in HULL_SUFFIXES]
        if all(name in by_stem for name in names):
            complete.append(
                (parent_display[key], base, [by_stem[name] for name in names])
            )
    complete.sort(key=lambda item: (item[1].casefold(), item[0].casefold()))
    return complete


def _flatten_strings(value: Any) -> list[str]:
    """Return strings from inert GameParams values without following cycles."""
    result: list[str] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            result.append(item)
            return
        if not isinstance(item, (dict, tuple, list, set, frozenset, NeutralObject)):
            return
        object_id = id(item)
        if object_id in seen:
            return
        seen.add(object_id)
        if isinstance(item, dict):
            for key, nested in item.items():
                visit(key)
                visit(nested)
        elif isinstance(item, (tuple, list, set, frozenset)):
            for nested in item:
                visit(nested)
        else:
            visit(item.constructor_args)
            visit(item.state)

    visit(value)
    return result


def decode_game_params(payload: bytes) -> dict[str, Any]:
    """Decode Legends GameParams bytes with an import-blocking unpickler."""
    if len(payload) > MAX_GAME_PARAMS_BYTES:
        raise GameParamsFormatError(
            f"GameParams payload is too large ({len(payload)} bytes)"
        )
    if not payload.startswith(b"%bin"):
        raise GameParamsFormatError("GameParams.data lacks the %bin header")
    try:
        root = InertGameParamsUnpickler(
            io.BytesIO(payload[4:]), encoding="latin1"
        ).load()
    except (
        EOFError,
        pickle.UnpicklingError,
        AttributeError,
        TypeError,
        ValueError,
    ) as exc:
        raise GameParamsFormatError(f"GameParams decode failed: {exc}") from exc
    if not isinstance(root, dict):
        raise GameParamsFormatError("GameParams root is not a dictionary")
    return root


def read_game_params(
    package_dir: Path,
    package_lookup: Mapping[str, Path],
) -> dict[str, Any]:
    """Read only the live GameParams payload from system_data packages."""
    system_index = next(
        (
            path
            for path in package_dir.glob("*.idx")
            if path.is_file() and path.name.casefold() == "system_data.idx"
        ),
        None,
    )
    if system_index is None:
        raise FileNotFoundError("system_data.idx was not found")
    entries = list(
        assets_from_index(parse_legends_idx(system_index), package_dir, package_lookup)
    )
    by_path = {entry.virtual_path.casefold(): entry for entry in entries}
    selected = next(
        (
            by_path[path.casefold()]
            for path in GAME_PARAMS_PATHS
            if path.casefold() in by_path
        ),
        None,
    )
    if selected is None:
        raise FileNotFoundError(
            "system_data.idx contains neither GameParams.data nor GameParams_py2.data"
        )
    return decode_game_params(
        read_asset_bytes(selected, max_unpacked_size=MAX_GAME_PARAMS_BYTES)
    )


def game_params_hull_models(
    root: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    """Return exact playable ship key, Hull component, and live model path."""
    discovered: dict[tuple[str, str], tuple[str, str, str]] = {}
    for raw_key in sorted(root, key=lambda value: str(value).casefold()):
        ship_key = str(raw_key)
        match = SHIP_KEY_RE.match(ship_key)
        if match is None:
            continue
        ship = root[raw_key]
        nation_code = match.group("nation").upper()
        if nation_code not in NATIONS:
            if nation_code != "X":
                continue
            strings = _flatten_strings(ship)
            if not any(
                text.casefold() == MODERN_ERA_MARKER for text in strings
            ):
                continue
        state = getattr(ship, "state", None)
        if (
            not isinstance(state, (tuple, list))
            or len(state) <= 2
            or not isinstance(state[2], dict)
        ):
            continue
        for raw_component, value in state[2].items():
            component = str(raw_component)
            if not component.casefold().endswith("_hull"):
                continue
            models = sorted(
                {
                    text.strip().replace("\\", "/")
                    for text in _flatten_strings(value)
                    if text.strip().casefold().endswith(".model")
                    and "/ship/" in text.replace("\\", "/").casefold()
                    and not text.strip().casefold().endswith("_dead.model")
                },
                key=str.casefold,
            )
            for model_path in models:
                pair = (ship_key.casefold(), model_path.casefold())
                candidate = (ship_key, component, model_path)
                previous = discovered.get(pair)
                if previous is None or component.casefold() < previous[1].casefold():
                    discovered[pair] = candidate
    return sorted(
        discovered.values(),
        key=lambda item: (item[0].casefold(), item[2].casefold()),
    )


def _game_params_catalog_rows(
    package_rows: Iterable[dict[str, object]],
    root: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Bind package geometry to exact live GameParams ship/Hull relationships."""
    by_model: dict[str, dict[str, object]] = {}
    for row in package_rows:
        model_path = str(row.get("model_path") or "")
        if not model_path:
            continue
        key = model_path.casefold()
        previous = by_model.get(key)
        rank = (
            bool(row["selectable"]),
            int(row["_update_sequence"]),
            str(row["index_filename"]).casefold(),
        )
        previous_rank = (
            (
                bool(previous["selectable"]),
                int(previous["_update_sequence"]),
                str(previous["index_filename"]).casefold(),
            )
            if previous is not None
            else None
        )
        if previous_rank is None or rank > previous_rank:
            by_model[key] = row

    rows: list[dict[str, object]] = []
    for ship_key, component, model_path in game_params_hull_models(root):
        match = SHIP_KEY_RE.match(ship_key)
        assert match is not None
        ship_code = match.group("ship_code").upper()
        nation_code = match.group("nation").upper()
        class_code = match.group("class").upper()
        if nation_code == "X":
            model_identity = HULL_RESOURCE_RE.match(PurePosixPath(model_path).stem)
            if model_identity is not None:
                model_nation = model_identity.group("nation").upper()
                model_class = model_identity.group("class").upper()
                if model_nation in NATIONS:
                    nation_code = model_nation
                if model_class in SHIP_CLASSES:
                    class_code = model_class
        package_row = by_model.get(model_path.casefold())
        if package_row is None:
            model = PurePosixPath(model_path)
            resource = model.stem
            display, _model_nation, _model_class = _variant_display(resource, None)
            combined_identity = f"{ship_key}::{resource}"
            rows.append(
                {
                    "ship_code": ship_code,
                    "game_params_key": ship_key,
                    "hull_component": component,
                    "model_path": model_path,
                    "id": combined_identity,
                    "index_filename": "",
                    "resource_path": "",
                    "hull_resource": resource,
                    "hull_resource_path": model.parent.as_posix(),
                    "output_slug": _safe_slug(combined_identity),
                    "display_label": display,
                    "variant_label": display,
                    "nation": NATIONS[nation_code],
                    "nation_code": nation_code,
                    "class": SHIP_CLASSES[class_code],
                    "class_code": class_code,
                    "tier": None,
                    "support_level": "unsupported",
                    "support_reason": (
                        "live GameParams Hull model has no complete five-part "
                        "geometry set in the installed packages"
                    ),
                    "selectable": False,
                }
            )
            continue

        row = dict(package_row)
        combined_identity = f"{ship_key}::{row['hull_resource']}"
        row.update(
            {
                "ship_code": ship_code,
                "game_params_key": ship_key,
                "hull_component": component,
                "model_path": model_path,
                "id": combined_identity,
                "output_slug": _safe_slug(combined_identity),
                "nation": NATIONS[nation_code],
                "nation_code": nation_code,
                "class": SHIP_CLASSES[class_code],
                "class_code": class_code,
                "support_level": (
                    "full-assembly" if row["selectable"] else "unsupported"
                ),
                "support_reason": (
                    "exact live GameParams Hull model resolves to a complete "
                    "five-part geometry set; ModelUber textures and payload "
                    "integrity are validated during extraction"
                    if row["selectable"]
                    else row["support_reason"]
                ),
            }
        )
        row.pop("_update_sequence", None)
        rows.append(row)

    rows.sort(
        key=lambda row: (
            not bool(row["selectable"]),
            str(row["nation"]).casefold(),
            str(row["class"]).casefold(),
            row["tier"] is None,
            int(row["tier"]) if row["tier"] is not None else 999,
            str(row["variant_label"]).casefold(),
            str(row["id"]).casefold(),
        )
    )
    return rows


def _diffuse_entries(
    entries: Sequence[AssetEntry], base: str
) -> tuple[list[AssetEntry], str | None]:
    selected: list[AssetEntry] = []
    candidate_groups = (
        (f"{base}_a.dds", f"{base}_Hull_a.dds"),
        (f"{base}_DeckHouse_a.dds",),
    )
    for candidates in candidate_groups:
        chosen: list[AssetEntry] = []
        for filename in candidates:
            matches = [
                entry
                for entry in entries
                if entry.extension == ".dds"
                and PurePosixPath(entry.virtual_path).name.casefold()
                == filename.casefold()
                and "ship" in _path_parts(entry)
                and "textures" in _path_parts(entry)
            ]
            if len(matches) > 1:
                return (
                    [],
                    f"expected at most one ship diffuse {filename}, "
                    f"found {len(matches)}",
                )
            if matches:
                chosen = matches
                break
        if not chosen:
            return (
                [],
                "expected one ship diffuse from: " + ", ".join(candidates),
            )
        selected.extend(chosen)
    return selected, None


def _variant_display(hull_resource: str, tier: int | None) -> tuple[str, str, str]:
    match = HULL_RESOURCE_RE.match(hull_resource)
    if match is None:
        label = _humanize(hull_resource)
        code = hull_resource.split("_", 1)[0]
        display = f"{label} [{code}]"
        return display, "?", "?"
    code = hull_resource.split("_", 1)[0]
    label = _humanize(match.group("label") or hull_resource)
    display = f"{label} [{code}]"
    if tier is not None:
        display += f" — Tier {tier}"
    return (
        display,
        match.group("nation").upper(),
        match.group("class").upper(),
    )


def _candidate_row(
    index_path: Path,
    identity: dict[str, object],
    hull: tuple[str, str, list[AssetEntry]],
    all_entries: Sequence[AssetEntry],
    duplicate_base: bool,
) -> dict[str, object]:
    parent, base, geometry_entries = hull
    _, diffuse_error = _diffuse_entries(all_entries, base)
    storage_ok = bool(geometry_entries) and all(
        (
            entry.file_info.compression_type_1,
            entry.file_info.compression_type_2,
        )
        == (5, 1)
        for entry in geometry_entries
    )
    selectable = not duplicate_base and storage_ok
    if duplicate_base:
        reason = (
            "same hull resource basename occurs under multiple virtual parents; "
            "exact resource selection would be ambiguous"
        )
    elif not storage_ok:
        reason = "package storage variant is not verified"
    elif diffuse_error is not None:
        reason = (
            "catalog metadata found a complete five-part hull with expected "
            "storage flags; legacy diffuse-name probe did not match "
            f"({diffuse_error}), but the full assembly pipeline resolves "
            "textures from ModelUber material references"
        )
    else:
        reason = (
            "catalog metadata found a complete five-part hull with expected "
            "storage flags; textures are resolved from ModelUber material "
            "references and payload CRC and geometry decode are validated "
            "only during extraction"
        )

    display, nation_code, class_code = _variant_display(
        base,
        identity["tier"],  # type: ignore[arg-type]
    )
    combined_identity = f"{index_path.stem}::{base}"
    path_digest = hashlib.sha256(parent.casefold().encode("utf-8")).hexdigest()[:8]
    if duplicate_base:
        combined_identity += f"::{path_digest}"
    return {
        "ship_code": str(identity["ship_code"]),
        "id": combined_identity,
        "index_filename": index_path.name,
        "resource_path": PurePosixPath("res_packages", index_path.name).as_posix(),
        "hull_resource": base,
        "hull_resource_path": parent,
        "model_path": (PurePosixPath(parent) / f"{base}.model").as_posix(),
        "output_slug": _safe_slug(combined_identity),
        "display_label": display,
        "variant_label": display,
        "nation": NATIONS.get(nation_code, nation_code),
        "nation_code": nation_code,
        "class": SHIP_CLASSES.get(class_code, class_code),
        "class_code": class_code,
        "tier": identity["tier"],
        "support_level": "full-assembly" if selectable else "unsupported",
        "support_reason": reason,
        "selectable": selectable,
        "_update_sequence": _update_sequence(index_path.name),
    }


def _unsupported_index_row(
    index_path: Path,
    identity: dict[str, object],
    reason: str,
) -> dict[str, object]:
    index_identity = index_path.stem
    nation_code = str(identity["nation_code"])
    class_code = str(identity["class_code"])
    tier = identity["tier"]
    display = str(identity["label"])
    if tier is not None:
        display += f" — Tier {tier}"
    return {
        "ship_code": str(identity["ship_code"]),
        "id": index_identity,
        "index_filename": index_path.name,
        "resource_path": PurePosixPath("res_packages", index_path.name).as_posix(),
        "hull_resource": None,
        "hull_resource_path": None,
        "output_slug": _safe_slug(index_identity),
        "display_label": display,
        "variant_label": display,
        "nation": NATIONS.get(nation_code, nation_code),
        "nation_code": nation_code,
        "class": SHIP_CLASSES.get(class_code, class_code),
        "class_code": class_code,
        "tier": tier,
        "support_level": "unsupported",
        "support_reason": reason,
        "selectable": False,
        "_update_sequence": _update_sequence(index_path.name),
    }


def deduplicate_catalog(
    rows: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Choose one source IDX per internal hull resource deterministically.

    Selectable data wins, then the highest zupd number, then the
    lexicographically greatest exact IDX filename. Unsupported rows without a
    discovered hull use their parsed package ship code and remain independent.
    """
    chosen: dict[str, dict[str, object]] = {}
    for row in rows:
        hull_resource = row["hull_resource"]
        key = (
            f"hull:{str(hull_resource).casefold()}"
            if hull_resource is not None
            else f"index:{str(row['id']).casefold()}"
        )
        previous = chosen.get(key)
        if previous is None:
            chosen[key] = row
            continue
        rank = (
            bool(row["selectable"]),
            int(row["_update_sequence"]),
            str(row["index_filename"]).casefold(),
        )
        previous_rank = (
            bool(previous["selectable"]),
            int(previous["_update_sequence"]),
            str(previous["index_filename"]).casefold(),
        )
        if rank > previous_rank:
            chosen[key] = row

    result: list[dict[str, object]] = []
    for row in chosen.values():
        clean = dict(row)
        clean.pop("_update_sequence", None)
        result.append(clean)
    result.sort(
        key=lambda row: (
            not bool(row["selectable"]),
            str(row["nation"]).casefold(),
            str(row["class"]).casefold(),
            row["tier"] is None,
            int(row["tier"]) if row["tier"] is not None else 999,
            str(row["display_label"]).casefold(),
            str(row["id"]).casefold(),
        )
    )
    return result


def localize_catalog_rows(
    rows: Iterable[dict[str, object]],
    messages: Mapping[str, str],
    language: str,
) -> list[dict[str, object]]:
    """Attach exact in-game names while preserving internal variant labels."""
    normalized = normalize_language(language)
    localized_rows: list[dict[str, object]] = []
    for row in rows:
        clean = dict(row)
        ship_code = str(clean["ship_code"])
        variant_label = str(clean["variant_label"])
        localized_name = localized_name_for_code(messages, ship_code)
        clean["localization_key"] = f"IDS_{ship_code}"
        clean["localized_name"] = localized_name
        clean["localized_language"] = normalized
        clean["display_label"] = localized_name or variant_label
        localized_rows.append(clean)

    localized_rows.sort(
        key=lambda row: (
            str(
                row["localized_name"]
                if row["localized_name"] is not None
                else row["variant_label"]
            ).casefold(),
            str(row["variant_label"]).casefold(),
            str(row["id"]).casefold(),
        )
    )
    return localized_rows


def build_catalog(
    game_dir: Path,
    *,
    language: str = "ko",
    supported_only: bool = False,
) -> list[dict[str, object]]:
    game_root = game_dir.resolve()
    mo_path, normalized_language = find_global_mo(game_root, language)
    package_dir = game_root / "res_packages"
    if not package_dir.is_dir():
        raise FileNotFoundError(f"res_packages not found under {game_root}")
    package_lookup = {
        path.name.casefold(): path
        for path in package_dir.glob("*.pkg")
        if path.is_file()
    }

    rows: list[dict[str, object]] = []
    for idx_path in sorted(
        package_dir.glob("*.idx"), key=lambda path: path.name.casefold()
    ):
        if not idx_path.is_file():
            continue
        identity = parse_index_identity(idx_path.name)
        if identity is None:
            continue
        try:
            entries = list(
                assets_from_index(
                    parse_legends_idx(idx_path), package_dir, package_lookup
                )
            )
            hulls = find_complete_hull_geometries(entries)
            if not hulls:
                rows.append(
                    _unsupported_index_row(
                        idx_path,
                        identity,
                        "IDX contains no complete five-part hull geometry set",
                    )
                )
                continue
            base_counts = Counter(base.casefold() for _, base, _ in hulls)
            for hull in hulls:
                rows.append(
                    _candidate_row(
                        idx_path,
                        identity,
                        hull,
                        entries,
                        base_counts[hull[1].casefold()] > 1,
                    )
                )
        except (IdxFormatError, UnsafePathError, FileNotFoundError, OSError) as exc:
            rows.append(
                _unsupported_index_row(idx_path, identity, f"IDX scan failed: {exc}")
            )

    game_params = read_game_params(package_dir, package_lookup)
    result = _game_params_catalog_rows(rows, game_params)
    if supported_only:
        result = [row for row in result if bool(row["selectable"])]
    message_keys = {
        key
        for row in result
        for key in (
            f"IDS_{row['ship_code']}",
            f"IDS_{row['ship_code']}_FULL",
        )
    }
    messages = read_mo_exact(mo_path, message_keys)
    return localize_catalog_rows(result, messages, normalized_language)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print a read-only WoWS Legends ship catalog as a JSON array. "
            "Only the live GameParams payload is read; no files are written."
        )
    )
    parser.add_argument("--game-dir", required=True, type=Path)
    parser.add_argument(
        "--language",
        default="ko",
        help="game localization token such as ko, en, ja, or pt_br",
    )
    parser.add_argument(
        "--supported-only",
        action="store_true",
        help="omit live Hull rows without a complete five-part geometry set",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        catalog = build_catalog(
            args.game_dir,
            language=args.language,
            supported_only=args.supported_only,
        )
        print(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ExtractionError, FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
