#!/usr/bin/env python3
"""Build and optionally convert every render model used by a selected ship.

This is the generic downstream counterpart of the verified Ticonderoga batch.
It consumes an already-built mapping and extracted logical resources. It does
not inspect or modify the game installation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


class BatchError(RuntimeError):
    """Raised when a generic mapping cannot be converted safely."""


TEXTURE_PROPERTY_CHANNELS = {
    "diffuseMap": "a",
    "normalMap": "n",
    "metallicGlossMap": "mg",
    "ambientOcclusionMap": "ao",
    "detailMap": "detail",
}
CATEGORY_ORDER = ("hull", "combat", "misc", "runtime_overlay")
IDENTITY_COLUMN_MAJOR = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


def emit_progress(percent: int, message: str) -> None:
    payload = {"stage": "extract", "percent": percent, "message": message}
    print("[PROGRESS] " + json.dumps(payload, ensure_ascii=False), flush=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _file_fingerprint(
    path: Path, cache: dict[Path, dict[str, Any]] | None = None
) -> dict[str, Any]:
    resolved = path.resolve()
    if cache is not None and resolved in cache:
        return cache[resolved]
    value = {"bytes": resolved.stat().st_size, "sha256": _sha256(resolved)}
    if cache is not None:
        cache[resolved] = value
    return value


def _normalize_model_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatchError(f"invalid model path: {value!r}")
    text = value.strip().replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise BatchError(f"unsafe model path: {value!r}")
    normalized = path.as_posix()
    if not normalized.endswith(".model"):
        raise BatchError(f"model path must end in .model: {value!r}")
    return normalized


def _array(mapping: Mapping[str, Any], name: str) -> list[Any]:
    value = mapping.get(name, [])
    if not isinstance(value, list):
        raise BatchError(f"{name} must be an array")
    return value


def _require_accepted_mapping(mapping: Mapping[str, Any]) -> None:
    schema = mapping.get("schema")
    if schema != "wows-legends-static-ship-assembly/v1":
        raise BatchError(f"unsupported mapping schema: {schema!r}")
    validation = mapping.get("validation")
    if not isinstance(validation, dict):
        raise BatchError("mapping.validation must be an object")
    if validation.get("static_assembly_acceptance") is not True:
        reasons = {
            key: value
            for key, value in validation.items()
            if value not in (True, [], {}, None)
        }
        raise BatchError(f"mapping static assembly is not accepted: {reasons}")


def collect_used_models(mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collect unique render-bearing models without any fixed ship counts."""

    collected: dict[str, dict[str, Any]] = {}

    def add(category: str, item: object, field: str) -> None:
        if not isinstance(item, dict):
            raise BatchError(f"{category} entry must be an object")
        if item.get("render_required", True) is False:
            return
        path = _normalize_model_path(item.get(field))
        record = collected.setdefault(
            path,
            {
                "model_path": path,
                "categories": [],
                "references": 0,
            },
        )
        if category not in record["categories"]:
            record["categories"].append(category)
        record["references"] += 1

    for item in _array(mapping, "hull_parts"):
        if not isinstance(item, dict):
            raise BatchError("hull_parts entry must be an object")
        if item.get("role") == "mesh":
            add("hull", item, "path")
    for item in _array(mapping, "combat_mounts"):
        add("combat", item, "model_path")
    for item in _array(mapping, "misc_instances"):
        add("misc", item, "model_path")
    for item in _array(mapping, "runtime_action_overlays"):
        add("runtime_overlay", item, "model_path")

    if not collected:
        raise BatchError("mapping contains no render-bearing used models")

    def sort_key(record: Mapping[str, Any]) -> tuple[int, str]:
        categories = record["categories"]
        priority = min(CATEGORY_ORDER.index(value) for value in categories)
        return priority, str(record["model_path"]).casefold()

    result = sorted(collected.values(), key=sort_key)
    for record in result:
        record["primary_category"] = min(record["categories"], key=CATEGORY_ORDER.index)
    return result


def _model_records(mapping: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    value = mapping.get("models")
    if not isinstance(value, dict):
        raise BatchError("mapping.models must be an object keyed by model path")
    result: dict[str, dict[str, Any]] = {}
    for raw_path, record in value.items():
        path = _normalize_model_path(raw_path)
        if path in result:
            raise BatchError(f"duplicate normalized model path: {path}")
        if not isinstance(record, dict):
            raise BatchError(f"model record must be an object: {path}")
        result[path] = record
    return result


def _semantic_include(
    model_path: str, render_set: Mapping[str, Any]
) -> tuple[bool, str, str | None]:
    """Resolve explicit mapping semantics; never infer damage from a name."""

    include = render_set.get("include_in_intact")
    semantic = render_set.get("damage_semantic", "unknown")
    rule = render_set.get("semantic_rule")
    if include is not None and not isinstance(include, bool):
        raise BatchError(f"{model_path}: include_in_intact must be bool/null")
    if semantic not in {"intact", "damage", "unknown"}:
        raise BatchError(f"{model_path}: unsupported damage_semantic {semantic!r}")
    if rule is not None and not isinstance(rule, str):
        raise BatchError(f"{model_path}: semantic_rule must be a string")
    if include is True and semantic == "damage":
        raise BatchError(f"{model_path}: render set is both included and damage")
    if include is False and semantic == "intact":
        raise BatchError(f"{model_path}: render set is both excluded and intact")
    if include is not None:
        return include, semantic, rule
    if semantic == "intact":
        return True, semantic, rule
    if semantic == "damage":
        return False, semantic, rule
    section = render_set.get("vertices_section")
    raise BatchError(
        f"{model_path}: render set {section!r} has unknown damage semantics"
    )


def _lod0_visual(model_path: str, model_record: Mapping[str, Any]) -> dict[str, Any]:
    model_uber = model_record.get("model_uber")
    if not isinstance(model_uber, dict):
        raise BatchError(f"{model_path}: model_uber is missing")
    visuals = model_uber.get("visual_prototypes")
    if not isinstance(visuals, list):
        raise BatchError(f"{model_path}: visual_prototypes must be an array")
    lod0 = [
        item
        for item in visuals
        if isinstance(item, dict) and item.get("lod_index") == 0
    ]
    if len(lod0) != 1:
        raise BatchError(f"{model_path}: expected one LOD0 visual, got {len(lod0)}")
    return lod0[0]


def _rigid_render_set_transform(
    model_record: Mapping[str, Any], render_set: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return the visual-node transform for a single-palette rigid render set."""

    if render_set.get("skinned") is not False:
        return None
    palette = render_set.get("skin_node_palette")
    if not isinstance(palette, list) or len(palette) != 1:
        return None
    palette_entry = palette[0]
    if not isinstance(palette_entry, dict):
        return None
    node_name = palette_entry.get("name")
    if not isinstance(node_name, str) or not node_name:
        return None

    model_uber = model_record.get("model_uber")
    visual_nodes = (
        model_uber.get("visual_nodes") if isinstance(model_uber, dict) else None
    )
    nodes = visual_nodes.get("nodes") if isinstance(visual_nodes, dict) else None
    if not isinstance(nodes, list):
        return None
    matches = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("name") == node_name
    ]
    if len(matches) != 1:
        return None
    world = matches[0].get("world_matrix")
    matrix = world.get("column_major") if isinstance(world, dict) else None
    if not isinstance(matrix, list) or len(matrix) != 16:
        return None
    try:
        values = tuple(float(value) for value in matrix)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    if all(
        abs(value - identity) <= 1e-7
        for value, identity in zip(values, IDENTITY_COLUMN_MAJOR)
    ):
        return None
    return {
        "rigid_node_name": node_name,
        "rigid_node_world_matrix": list(values),
        "rigid_node_transform_basis": "ModelUber XYZ, column-major world matrix",
    }


def make_manifest(
    use: Mapping[str, Any],
    model_record: Mapping[str, Any],
    extracted_root: Path,
    fingerprint_cache: dict[Path, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model_path = _normalize_model_path(use.get("model_path"))
    visual = _lod0_visual(model_path, model_record)
    geometry_path = visual.get("derived_geometry_path")
    if not isinstance(geometry_path, str) or not geometry_path:
        raise BatchError(f"{model_path}: LOD0 visual has no derived_geometry_path")
    logical_geometry = PurePosixPath(geometry_path.replace("\\", "/"))
    if logical_geometry.is_absolute() or ".." in logical_geometry.parts:
        raise BatchError(f"{model_path}: unsafe geometry path {geometry_path!r}")
    geometry = (extracted_root / Path(*logical_geometry.parts)).resolve()
    try:
        geometry.relative_to(extracted_root.resolve())
    except ValueError as exc:
        raise BatchError(
            f"{model_path}: geometry resolves outside extracted root"
        ) from exc
    if not geometry.is_file():
        raise BatchError(f"{model_path}: extracted geometry missing: {geometry}")

    source_sets = visual.get("render_sets")
    if not isinstance(source_sets, list) or not source_sets:
        raise BatchError(f"{model_path}: LOD0 render_sets must be non-empty")
    prototypes = model_record["model_uber"].get("material_prototypes")
    if not isinstance(prototypes, list):
        raise BatchError(f"{model_path}: material_prototypes must be an array")

    selected: list[dict[str, Any]] = []
    excluded = 0
    semantic_counts = {"intact": 0, "damage": 0, "unknown": 0}
    for index, raw_render_set in enumerate(source_sets):
        if not isinstance(raw_render_set, dict):
            raise BatchError(f"{model_path}: render set {index} is not an object")
        included, semantic, rule = _semantic_include(model_path, raw_render_set)
        semantic_counts[semantic] += 1
        if not included:
            excluded += 1
            continue

        required = (
            "vertices_section",
            "indices_section",
            "material_mfm_path",
            "material_name",
        )
        missing = [key for key in required if not raw_render_set.get(key)]
        if missing:
            raise BatchError(f"{model_path}: render set missing {missing}")
        prototype_index = raw_render_set.get("material_prototype_index")
        if not isinstance(prototype_index, int) or not (
            0 <= prototype_index < len(prototypes)
        ):
            raise BatchError(
                f"{model_path}: invalid material prototype index {prototype_index!r}"
            )
        prototype = prototypes[prototype_index]
        if not isinstance(prototype, dict):
            raise BatchError(
                f"{model_path}: material prototype {prototype_index} is invalid"
            )
        if prototype.get("mfm_path") != raw_render_set["material_mfm_path"]:
            raise BatchError(
                f"{model_path}: render-set/prototype MFM mismatch at {prototype_index}"
            )

        texture_maps: dict[str, str] = {}
        properties = prototype.get("properties", [])
        if not isinstance(properties, list):
            raise BatchError(
                f"{model_path}: material prototype properties must be an array"
            )
        for prop in properties:
            if not isinstance(prop, dict):
                raise BatchError(f"{model_path}: material property must be an object")
            channel = TEXTURE_PROPERTY_CHANNELS.get(prop.get("name"))
            if channel is None or prop.get("type") != "texture":
                continue
            value = prop.get("value")
            logical_path = value.get("path") if isinstance(value, dict) else None
            if not isinstance(logical_path, str) or not logical_path:
                raise BatchError(
                    f"{model_path}: texture property {prop.get('name')!r} "
                    "has no logical path"
                )
            logical_texture = PurePosixPath(logical_path.replace("\\", "/"))
            if logical_texture.is_absolute() or ".." in logical_texture.parts:
                raise BatchError(f"{model_path}: unsafe texture path {logical_path!r}")
            texture_maps[channel] = logical_texture.as_posix()

        item = {key: raw_render_set[key] for key in required}
        item.update(
            {
                "texture_maps": texture_maps,
                "material_fx_path": prototype.get("fx_path"),
                "material_properties": properties,
                "damage_semantic": semantic,
                "include_in_intact": True,
                "semantic_rule": rule,
            }
        )
        rigid_transform = _rigid_render_set_transform(model_record, raw_render_set)
        if rigid_transform is not None:
            item.update(rigid_transform)
        selected.append(item)

    if not selected:
        raise BatchError(f"{model_path}: no static-intact render sets selected")

    texture_inputs: list[dict[str, Any]] = []
    logical_textures = sorted(
        {
            logical_path
            for render_set in selected
            for logical_path in render_set["texture_maps"].values()
        }
    )
    for logical_path in logical_textures:
        parts = PurePosixPath(logical_path).parts
        texture_path = (extracted_root / Path(*parts)).resolve()
        try:
            texture_path.relative_to(extracted_root.resolve())
        except ValueError as exc:
            raise BatchError(
                f"{model_path}: texture resolves outside extracted root"
            ) from exc
        present = texture_path.is_file()
        fingerprint = (
            _file_fingerprint(texture_path, fingerprint_cache) if present else None
        )
        texture_inputs.append(
            {
                "logical_path": logical_path,
                "present": present,
                "bytes": fingerprint["bytes"] if fingerprint else None,
                "sha256": fingerprint["sha256"] if fingerprint else None,
            }
        )

    return {
        "schema": "wows-legends-selected-ship-pbr-manifest/v1",
        "name": PurePosixPath(model_path).stem,
        "source": {
            "categories": list(use.get("categories", [])),
            "primary_category": use.get("primary_category"),
            "model_path": model_path,
            "lod_index": 0,
            "mapping_render_sets": len(source_sets),
            "selected_render_sets": len(selected),
            "excluded_render_sets": excluded,
            "semantic_counts": semantic_counts,
            "input_fingerprints": {
                "geometry_bytes": _file_fingerprint(
                    geometry, fingerprint_cache
                )["bytes"],
                "geometry_sha256": _file_fingerprint(
                    geometry, fingerprint_cache
                )["sha256"],
                "textures": texture_inputs,
            },
            "selection_policy": (
                "mapping include_in_intact/damage_semantic; no filename regex"
            ),
        },
        "texture_root": str(extracted_root.resolve()),
        "models": [
            {
                "name": PurePosixPath(model_path).stem,
                "geometry": str(geometry),
                "render_sets": selected,
            }
        ],
    }


def _safe_output_key(model_path: str) -> str:
    stem = PurePosixPath(model_path).stem
    cleaned = (
        "".join(
            value if value.isalnum() or value in "._-" else "_" for value in stem
        ).strip("._-")
        or "model"
    )
    suffix = hashlib.sha256(model_path.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:64]}__{suffix}"


def _validate_glb(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        header = stream.read(12)
    if len(header) != 12:
        return {"valid": False, "reason": "truncated header"}
    magic, version, declared_length = struct.unpack("<4sII", header)
    actual_length = path.stat().st_size
    return {
        "valid": (
            magic == b"glTF" and version == 2 and declared_length == actual_length
        ),
        "magic": magic.decode("ascii", "replace"),
        "version": version,
        "declared_length": declared_length,
        "actual_length": actual_length,
    }


def _conversion_cache_key(
    manifest: dict[str, Any], engine_fingerprint: str = "unspecified"
) -> str:
    source = manifest.get("source", {})
    payload = {
        "schema": manifest.get("schema"),
        "model_path": source.get("model_path"),
        "input_fingerprints": source.get("input_fingerprints"),
        "models": [
            {
                "name": model.get("name"),
                "render_sets": model.get("render_sets"),
            }
            for model in manifest.get("models", [])
            if isinstance(model, dict)
        ],
        "cache_contract": 2,
        "engine_fingerprint": engine_fingerprint,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _conversion_engine_fingerprint(
    converter: Path, decoder_root: Path, blender: Path
) -> str:
    """Invalidate cached GLBs when conversion code or Blender changes."""

    digest = hashlib.sha256(b"wows-toolbox-legends-pbr-engine/v2\0")
    package_root = converter.parents[3].resolve()
    source_roots = [
        converter.parent,
        converter.parents[2] / "Ticonderoga1990" / "PBRConverter",
        decoder_root,
    ]
    source_files: set[Path] = {converter}
    for root in source_roots:
        if root.is_dir():
            source_files.update(path for path in root.rglob("*.py") if path.is_file())
    for path in sorted(source_files, key=lambda value: str(value).casefold()):
        resolved = path.resolve()
        try:
            identity = resolved.relative_to(package_root).as_posix()
        except ValueError:
            identity = resolved.name
        digest.update(identity.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    try:
        stat = blender.stat()
        blender_identity = f"{blender.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        blender_identity = str(blender.resolve())
    digest.update(blender_identity.encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _materialize_cached_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _try_conversion_cache(
    cache_root: Path | None,
    cache_key: str,
    output_glb: Path,
    validation_path: Path,
) -> bool:
    if cache_root is None:
        return False
    entry = cache_root / cache_key[:2] / cache_key
    cached_glb = entry / "model.glb"
    cached_validation = entry / "validation.json"
    integrity_path = entry / "integrity.json"
    if not all(path.is_file() for path in (cached_glb, cached_validation, integrity_path)):
        return False
    try:
        integrity = _load_json(integrity_path)
        validation = _load_json(cached_validation)
        acceptance = validation.get("acceptance", {})
        if (
            integrity.get("schema") != "wows-toolbox-conversion-cache/v1"
            or integrity.get("cache_key") != cache_key
            or integrity.get("model_glb_sha256") != _sha256(cached_glb)
            or integrity.get("validation_sha256") != _sha256(cached_validation)
            or validation.get("status") != "OK"
            or not isinstance(acceptance, dict)
            or acceptance.get("passed") is not True
            or not _validate_glb(cached_glb).get("valid")
        ):
            return False
        _materialize_cached_file(cached_glb, output_glb)
        _materialize_cached_file(cached_validation, validation_path)
        return (
            _sha256(output_glb) == integrity["model_glb_sha256"]
            and _sha256(validation_path) == integrity["validation_sha256"]
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _publish_conversion_cache(
    cache_root: Path | None,
    cache_key: str,
    output_glb: Path,
    validation_path: Path,
) -> None:
    if cache_root is None:
        return
    entry = cache_root / cache_key[:2] / cache_key
    entry.mkdir(parents=True, exist_ok=True)
    for source, name in ((output_glb, "model.glb"), (validation_path, "validation.json")):
        target = entry / name
        temporary = entry / f".{name}.{os.getpid()}.part"
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    _write_json(
        entry / "integrity.json",
        {
            "schema": "wows-toolbox-conversion-cache/v1",
            "cache_key": cache_key,
            "model_glb_sha256": _sha256(entry / "model.glb"),
            "validation_sha256": _sha256(entry / "validation.json"),
        },
    )

def _load_reuse_results(
    summary_path: Path,
    mapping_sha256: str,
    extracted_root: Path,
    engine_fingerprint: str,
) -> dict[str, dict[str, Any]]:
    if not summary_path.is_file():
        return {}
    previous = _load_json(summary_path)
    strict = previous.get("strict_validation")
    if (
        previous.get("schema") != "wows-legends-selected-ship-pbr-batch/v1"
        or previous.get("source_mapping_sha256") != mapping_sha256
        or previous.get("conversion_engine_fingerprint") != engine_fingerprint
        or not isinstance(strict, dict)
        or strict.get("accepted") is not True
    ):
        return {}
    previous_root = previous.get("extracted_root")
    if not isinstance(previous_root, str) or os.path.normcase(
        str(Path(previous_root).resolve())
    ) != os.path.normcase(str(extracted_root)):
        return {}
    results = previous.get("results")
    if not isinstance(results, list):
        return {}
    reusable: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "OK":
            continue
        model_path = _normalize_model_path(result.get("model_path"))
        if model_path in reusable:
            raise BatchError(f"previous summary has duplicate model path: {model_path}")
        reusable[model_path] = result
    return reusable


def _summary_result_matches(
    previous: dict[str, Any] | None,
    manifest_sha256: str,
    output_key: str,
    output_glb: Path,
    validation_path: Path,
) -> bool:
    return bool(
        previous is not None
        and previous.get("manifest_sha256") == manifest_sha256
        and previous.get("output_key") == output_key
        and validation_path.is_file()
        and output_glb.is_file()
        and previous.get("output_glb_sha256") == _sha256(output_glb)
        and previous.get("validation_sha256") == _sha256(validation_path)
    )


def _default_decoder_root(here: Path) -> Path:
    return here.parents[2] / "BlenderExtractor" / "geometry_decoder"


def _default_converter(here: Path) -> Path:
    return here / "convert_selected_ship.py"


def _build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--extracted-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--conversion-cache-root", type=Path)
    parser.add_argument(
        "--blender",
        type=Path,
        default=Path(r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe"),
    )
    parser.add_argument(
        "--decoder-root",
        type=Path,
        default=_default_decoder_root(here),
    )
    parser.add_argument(
        "--converter",
        type=Path,
        default=_default_converter(here),
    )
    parser.add_argument("--manifests-only", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--prepare-workers", type=int, default=4)
    parser.add_argument("--blender-batch-size", type=int, default=8)
    parser.add_argument("--reuse-existing", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    batch_started = time.perf_counter()
    args = _build_parser().parse_args(argv)
    if args.workers < 1 or args.workers > 4:
        raise BatchError("--workers must be between 1 and 4")
    if args.prepare_workers < 1 or args.prepare_workers > 8:
        raise BatchError("--prepare-workers must be between 1 and 8")
    if args.blender_batch_size < 1 or args.blender_batch_size > 32:
        raise BatchError("--blender-batch-size must be between 1 and 32")
    mapping_path = args.mapping.resolve()
    extracted_root = args.extracted_root.resolve()
    output_root = args.output_root.resolve()
    conversion_cache_root = (
        args.conversion_cache_root.resolve()
        if args.conversion_cache_root is not None
        else None
    )
    if not extracted_root.is_dir():
        raise BatchError(f"extracted root not found: {extracted_root}")
    mapping = _load_json(mapping_path)
    _require_accepted_mapping(mapping)
    uses = collect_used_models(mapping)
    mapping_sha256 = _sha256(mapping_path)
    model_records = _model_records(mapping)
    output_root.mkdir(parents=True, exist_ok=True)

    summary_path = output_root / "selected_ship_pbr_models.summary.json"
    converter = args.converter.resolve()
    if not args.manifests_only and not converter.is_file():
        raise BatchError(f"converter not found: {converter}")
    engine_fingerprint = _conversion_engine_fingerprint(
        converter, args.decoder_root.resolve(), args.blender.resolve()
    )
    reusable = (
        _load_reuse_results(
            summary_path, mapping_sha256, extracted_root, engine_fingerprint
        )
        if args.reuse_existing
        else {}
    )
    results: list[dict[str, Any]] = []
    required_textures: set[str] = set()
    prepared: list[dict[str, Any]] = []
    fingerprint_cache: dict[Path, dict[str, Any]] = {}
    manifest_started = time.perf_counter()

    for use in uses:
        model_path = use["model_path"]
        model_record = model_records.get(model_path)
        if model_record is None:
            raise BatchError(f"used model record missing: {model_path}")
        output_key = _safe_output_key(model_path)
        model_output = output_root / output_key
        manifest = make_manifest(
            use, model_record, extracted_root, fingerprint_cache
        )
        manifest_path = model_output / f"{output_key}.manifest.json"
        _write_json(manifest_path, manifest)
        for model in manifest["models"]:
            for render_set in model["render_sets"]:
                required_textures.update(render_set["texture_maps"].values())
        manifest_sha256 = _sha256(manifest_path)
        output_glb = model_output / f"{output_key}.glb"
        validation_path = model_output / f"{output_key}.validation.json"
        previous = reusable.get(model_path)
        summary_reused = _summary_result_matches(
            previous,
            manifest_sha256,
            output_key,
            output_glb,
            validation_path,
        )
        cache_key = _conversion_cache_key(manifest, engine_fingerprint)
        cache_reused = False
        if not args.manifests_only and not summary_reused:
            cache_reused = _try_conversion_cache(
                conversion_cache_root, cache_key, output_glb, validation_path
            )
        prepared.append(
            {
                "use": use,
                "model_path": model_path,
                "output_key": output_key,
                "model_output": model_output,
                "manifest": manifest,
                "manifest_path": manifest_path,
                "manifest_sha256": manifest_sha256,
                "output_glb": output_glb,
                "validation_path": validation_path,
                "conversion_cache_key": cache_key,
                "cache_reused": cache_reused,
                "summary_reused": summary_reused,
                "conversion_exit": None,
            }
        )

    manifest_seconds = time.perf_counter() - manifest_started
    conversion_items = [
        item
        for item in prepared
        if not args.manifests_only
        and not item["summary_reused"]
        and not item["cache_reused"]
    ]
    for item in conversion_items:
        item["output_glb"].unlink(missing_ok=True)
        item["validation_path"].unlink(missing_ok=True)
    reused_count = len(prepared) - len(conversion_items)
    emit_progress(
        24,
        f"함선 부품 {len(prepared)}개 준비 중"
        + (f" · 기존 결과 {reused_count}개 재사용" if reused_count else ""),
    )

    def prepare_one(item: dict[str, Any]) -> int:
        command = [
            sys.executable,
            str(converter),
            str(item["manifest_path"]),
            "--output-dir",
            str(item["model_output"]),
            "--name",
            item["output_key"],
            "--blender",
            str(args.blender.resolve()),
            "--decoder-root",
            str(args.decoder_root.resolve()),
            "--no-blend",
            "--shared-texture-dir",
            str(output_root / "_shared_textures"),
            "--skip-blender",
        ]
        run = subprocess.run(command, text=True, capture_output=True)
        (item["model_output"] / f"{item['output_key']}.batch-convert.log").write_text(
            run.stdout + "\n--- STDERR ---\n" + run.stderr,
            encoding="utf-8",
        )
        return run.returncode

    prepare_seconds = 0.0
    blender_seconds = 0.0
    if conversion_items:
        prepare_started = time.perf_counter()
        worker_count = max(
            1, min(args.prepare_workers, len(conversion_items))
        )
        print(
            f"Preparing {len(conversion_items)} models with {worker_count} workers",
            flush=True,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_items = {
                pool.submit(prepare_one, item): item for item in conversion_items
            }
            prepared_count = 0
            for future in concurrent.futures.as_completed(future_items):
                item = future_items[future]
                try:
                    item["conversion_exit"] = int(future.result())
                except Exception as exc:
                    item["conversion_exit"] = -1
                    item["worker_error"] = f"{type(exc).__name__}: {exc}"
                    print(
                        f"[ERROR] prepare worker failed for {item['model_path']}: "
                        f"{item['worker_error']}",
                        flush=True,
                    )
                prepared_count += 1
                print(
                    f"[PREPARED {prepared_count}/{len(conversion_items)}] "
                    f"{item['model_path']}",
                    flush=True,
                )
                emit_progress(
                    24 + round(10 * prepared_count / len(conversion_items)),
                    f"부품 변환 준비 {prepared_count}/{len(conversion_items)}",
                )

        prepare_seconds = time.perf_counter() - prepare_started
        blender_items = [
            item for item in conversion_items if item["conversion_exit"] == 0
        ]
        blender_script = (
            converter.parents[2]
            / "Ticonderoga1990"
            / "PBRConverter"
            / "blender_pbr.py"
        ).resolve()
        if not blender_script.is_file():
            raise BatchError(f"Blender PBR worker not found: {blender_script}")
        batches = [
            blender_items[index : index + args.blender_batch_size]
            for index in range(0, len(blender_items), args.blender_batch_size)
        ]

        def convert_batch(
            batch_index: int, batch: list[dict[str, Any]]
        ) -> tuple[int, Path]:
            command = [
                str(args.blender.resolve()),
                "--background",
                "--factory-startup",
                "--python",
                str(blender_script),
                "--",
                *[
                    str(
                        item["model_output"]
                        / f"{item['output_key']}.blender-input.json"
                    )
                    for item in batch
                ],
            ]
            run = subprocess.run(command, text=True, capture_output=True)
            batch_log = output_root / f"blender-batch-{batch_index:03d}.log"
            batch_log.write_text(
                run.stdout + "\n--- STDERR ---\n" + run.stderr,
                encoding="utf-8",
            )
            for item in batch:
                component_log = (
                    item["model_output"] / f"{item['output_key']}.blender.log"
                )
                component_log.write_text(
                    f"Batch log: {batch_log}\nExit code: {run.returncode}\n",
                    encoding="utf-8",
                )
            return run.returncode, batch_log

        if batches:
            blender_started = time.perf_counter()
            batch_workers = max(1, min(args.workers, len(batches)))
            print(
                f"Converting {len(blender_items)} models in {len(batches)} "
                f"Blender batches ({batch_workers} parallel, "
                f"up to {args.blender_batch_size} models per startup)",
                flush=True,
            )
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=batch_workers
            ) as pool:
                future_batches = {
                    pool.submit(convert_batch, index, batch): (index, batch)
                    for index, batch in enumerate(batches, start=1)
                }
                converted_count = 0
                for future in concurrent.futures.as_completed(future_batches):
                    index, batch = future_batches[future]
                    try:
                        batch_exit, batch_log = future.result()
                    except Exception as exc:
                        batch_exit, batch_log = -1, Path("<batch worker exception>")
                        worker_error = f"{type(exc).__name__}: {exc}"
                        print(
                            f"[ERROR] batch worker {index} failed: {worker_error}",
                            flush=True,
                        )
                        for item in batch:
                            item["batch_worker_error"] = worker_error
                    for item in batch:
                        item_ok = False
                        if item["validation_path"].is_file() and item["output_glb"].is_file():
                            try:
                                item_ok = (
                                    _load_json(item["validation_path"]).get("status")
                                    == "OK"
                                )
                            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                                item_ok = False
                        item["conversion_exit"] = 0 if item_ok else batch_exit or -1
                    converted_count += len(batch)
                    print(
                        f"[{converted_count}/{len(blender_items)}] "
                        f"Blender batch {index}/{len(batches)} complete; log={batch_log}",
                        flush=True,
                    )
                    emit_progress(
                        34 + round(34 * converted_count / len(blender_items)),
                        f"Blender 부품 변환 {converted_count}/{len(blender_items)}",
                    )
            blender_seconds = time.perf_counter() - blender_started
    validation_started = time.perf_counter()
    emit_progress(69, "부품별 변환 결과를 검증하는 중")
    for item in prepared:
        use = item["use"]
        model_path = item["model_path"]
        output_key = item["output_key"]
        manifest = item["manifest"]
        manifest_path = item["manifest_path"]
        manifest_sha256 = item["manifest_sha256"]
        output_glb = item["output_glb"]
        validation_path = item["validation_path"]
        conversion_cache_key = item["conversion_cache_key"]
        cache_reused = item["cache_reused"]
        result: dict[str, Any] = {
            "categories": use["categories"],
            "primary_category": use["primary_category"],
            "references": use["references"],
            "model_path": model_path,
            "output_key": output_key,
            "manifest": str(manifest_path),
            "mapping_render_sets": manifest["source"]["mapping_render_sets"],
            "selected_render_sets": manifest["source"]["selected_render_sets"],
            "excluded_render_sets": manifest["source"]["excluded_render_sets"],
            "manifest_sha256": manifest_sha256,
            "semantic_counts": manifest["source"]["semantic_counts"],
            "output_glb": str(output_glb),
            "validation": str(validation_path),
        }
        if args.manifests_only:
            result["status"] = "MANIFEST_ONLY"
            results.append(result)
            continue
        if item["conversion_exit"] not in (None, 0):
            result.update(
                status="FAILED",
                error=f"converter exit {item['conversion_exit']}",
            )
            results.append(result)
            continue
        if not validation_path.is_file() or not output_glb.is_file():
            result.update(
                status="FAILED",
                error="expected validation or GLB output missing",
            )
            results.append(result)
            continue
        validation = _load_json(validation_path)
        acceptance = validation.get("acceptance", {})
        glb_container = _validate_glb(output_glb)
        accepted = (
            validation.get("status") == "OK"
            and isinstance(acceptance, dict)
            and acceptance.get("passed") is True
            and acceptance.get("render_set_to_geometry_part_missing") == 0
            and acceptance.get("material_policy_passed") is True
            and validation.get("render_set_count")
            == manifest["source"]["selected_render_sets"]
            and glb_container["valid"]
        )
        if not accepted:
            result.update(
                status="FAILED",
                error="strict validation acceptance failed",
            )
            results.append(result)
            continue
        if not cache_reused:
            _publish_conversion_cache(
                conversion_cache_root,
                conversion_cache_key,
                output_glb,
                validation_path,
            )
        result.update(
            {
                "status": "OK",
                "conversion_cache_key": conversion_cache_key,
                "conversion_cache_reused": cache_reused,
                "summary_reused": item["summary_reused"],
                "matched_render_sets": validation.get("matched_render_sets", 0),
                "missing_render_sets": int(validation.get("missing_render_sets", 0)),
                "missing_maps": len(validation.get("missing_maps", [])),
                "output_glb_bytes": output_glb.stat().st_size,
                "output_glb_sha256": _sha256(output_glb),
                "validation_sha256": _sha256(validation_path),
                "glb_container": glb_container,
                "material_count": len(validation.get("material_policy", [])),
                "material_policy_passed": True,
            }
        )
        results.append(result)
    required_report = {
        "schema": "wows-legends-selected-ship-required-textures/v1",
        "source_mapping": str(mapping_path),
        "source_mapping_sha256": mapping_sha256,
        "extracted_root": str(extracted_root),
        "logical_paths": [
            {
                "path": logical_path,
                "present": (
                    extracted_root
                    / Path(*PurePosixPath(logical_path.replace("\\", "/")).parts)
                ).is_file(),
            }
            for logical_path in sorted(required_textures)
        ],
    }
    required_report["count"] = len(required_report["logical_paths"])
    required_report["missing"] = [
        item["path"] for item in required_report["logical_paths"] if not item["present"]
    ]
    required_path = output_root / "selected_ship_required_textures.json"
    _write_json(required_path, required_report)

    failures = [item for item in results if item["status"] == "FAILED"]
    categories = {
        category: sum(category in item["categories"] for item in results)
        for category in CATEGORY_ORDER
    }
    complete = (
        not args.manifests_only
        and not failures
        and len(results) == len(uses)
        and not required_report["missing"]
    )
    validation_seconds = time.perf_counter() - validation_started
    total_seconds = time.perf_counter() - batch_started
    timings = {
        "manifest": round(manifest_seconds, 3),
        "prepare": round(prepare_seconds, 3),
        "blender": round(blender_seconds, 3),
        "validation": round(validation_seconds, 3),
        "total": round(total_seconds, 3),
    }
    summary = {
        "schema": "wows-legends-selected-ship-pbr-batch/v1",
        "source_mapping": str(mapping_path),
        "source_mapping_sha256": mapping_sha256,
        "conversion_engine_fingerprint": engine_fingerprint,
        "mapping_static_assembly_accepted": True,
        "extracted_root": str(extracted_root),
        "output_root": str(output_root),
        "required_texture_paths": str(required_path),
        "required_texture_count": required_report["count"],
        "required_texture_missing": required_report["missing"],
        "model_count": len(uses),
        "expected_models": len(uses),
        "result_models": len(results),
        "categories": categories,
        "timings_seconds": timings,
        "strict_validation": {
            "accepted": complete,
            "passed_models": sum(item["status"] == "OK" for item in results),
            "failed_models": len(failures),
            "total_selected_render_sets": sum(
                item["selected_render_sets"] for item in results
            ),
            "total_matched_render_sets": sum(
                int(item.get("matched_render_sets", 0)) for item in results
            ),
            "total_missing_render_sets": sum(
                int(item.get("missing_render_sets", 0)) for item in results
            ),
            "total_missing_maps": sum(
                int(item.get("missing_maps", 0)) for item in results
            ),
        },
        "results": results,
    }
    _write_json(summary_path, summary)
    print(json.dumps(summary["strict_validation"], indent=2))
    print("[TIMING] " + json.dumps(timings, ensure_ascii=False))
    print(summary_path)

    if failures:
        raise BatchError(
            f"{len(failures)} model conversions failed: "
            + ", ".join(item["model_path"] for item in failures)
        )
    if not args.manifests_only and required_report["missing"]:
        raise BatchError(
            f"{len(required_report['missing'])} required textures are missing"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BatchError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
