#!/usr/bin/env python3
"""Extract one Legends hull safely and build OBJ/GLB/BLEND outside the game.

The package reader is imported from the sibling ``blender_extractor`` folder.
Only the five hull geometry files plus base/DeckHouse diffuse DDS files are
selected. Every extracted payload is size/CRC checked by legends_assets.core.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

TOOL_DIR = Path(__file__).resolve().parent
BLENDER_EXTRACTOR_DIR = TOOL_DIR.parent / "blender_extractor"
if str(BLENDER_EXTRACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_EXTRACTOR_DIR))

from legends_assets.core import (  # noqa: E402
    AssetEntry,
    ExtractionError,
    assets_from_index,
    ensure_within_root,
    extract_asset,
    parse_legends_idx,
    safe_output_path,
    validate_glb,
    write_manifest,
)

from decode_geometry import (  # noqa: E402
    build_report,
    decode_geometry_files,
    write_obj,
)

HULL_SUFFIXES = ("", "_Bow", "_MidFront", "_MidBack", "_Stern")
LIMITATIONS = [
    "Only hull geometry is decoded; guns, turrets, radar, and misc mounts are omitted.",
    "Only base and DeckHouse diffuse color maps are used; AO, metal/gloss, normal maps, glass, and wire materials are not reconstructed.",
    "Diffuse assignment is a name-based heuristic: DeckHouse objects use the DeckHouse map and every other hull object uses the base map.",
    "GameParams.data and assets.bin are not parsed, so full mount transforms and scene hierarchy are not reconstructed.",
    "Damage variants are excluded by default and component coordinates are used as stored in the hull geometry.",
]


def _is_inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def find_ship_index_file(game_dir: Path, index_filename: str) -> Path:
    """Resolve one case-insensitive exact IDX basename without accepting paths."""
    requested = index_filename.strip()
    if (
        not requested
        or "/" in requested
        or "\\" in requested
        or Path(requested).name != requested
        or Path(requested).suffix.casefold() != ".idx"
    ):
        raise ExtractionError(
            "--ship-index-file must be one exact .idx basename from the catalog"
        )
    package_dir = game_dir / "res_packages"
    matches = [
        path
        for path in package_dir.glob("*.idx")
        if path.is_file() and path.name.casefold() == requested.casefold()
    ]
    if not matches:
        raise ExtractionError(f"exact ship IDX file was not found: {requested}")
    if len(matches) != 1:
        raise ExtractionError(
            f"exact ship IDX filename is not unique: {requested}"
        )
    return matches[0]


def find_ship_index(game_dir: Path, terms: Sequence[str]) -> Path:
    folded_terms = [term.strip().casefold() for term in terms if term.strip()]
    if not folded_terms:
        raise ExtractionError("at least one --ship-index term is required")
    package_dir = game_dir / "res_packages"
    matches = [
        path
        for path in sorted(package_dir.glob("*.idx"))
        if path.is_file()
        and all(term in path.stem.casefold() for term in folded_terms)
    ]
    if not matches:
        raise ExtractionError(
            "no ship IDX filename matched every term: " + ", ".join(terms)
        )
    if len(matches) != 1:
        names = ", ".join(path.name for path in matches[:20])
        raise ExtractionError(
            f"ship IDX selection is ambiguous ({len(matches)} matches): {names}"
        )
    return matches[0]


def load_index_entries(game_dir: Path, idx_path: Path) -> list[AssetEntry]:
    package_dir = game_dir / "res_packages"
    package_lookup = {
        path.name.casefold(): path
        for path in package_dir.glob("*.pkg")
        if path.is_file()
    }
    index = parse_legends_idx(idx_path)
    return list(assets_from_index(index, package_dir, package_lookup))


def _path_parts(entry: AssetEntry) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(entry.virtual_path).parts)


def find_complete_hull_geometries(
    entries: Sequence[AssetEntry],
) -> list[tuple[str, str, list[AssetEntry]]]:
    """Return every complete five-file hull as (parent path, base, entries)."""
    geometry_by_folder: dict[str, dict[str, AssetEntry]] = {}
    folder_display: dict[str, str] = {}
    folder_path_display: dict[str, str] = {}

    for entry in entries:
        path = PurePosixPath(entry.virtual_path)
        parts = _path_parts(entry)
        if entry.extension != ".geometry" or "ship" not in parts:
            continue
        if len(path.parts) < 2:
            continue
        folder = path.parent.name
        stem = path.stem
        expected = {f"{folder}{suffix}".casefold() for suffix in HULL_SUFFIXES}
        if stem.casefold() not in expected:
            continue
        key = path.parent.as_posix().casefold()
        geometry_by_folder.setdefault(key, {})[stem.casefold()] = entry
        folder_display[key] = folder
        folder_path_display[key] = path.parent.as_posix()

    complete: list[tuple[str, str, list[AssetEntry]]] = []
    for key, by_stem in geometry_by_folder.items():
        base = folder_display[key]
        expected = [f"{base}{suffix}".casefold() for suffix in HULL_SUFFIXES]
        if all(name in by_stem for name in expected):
            complete.append(
                (
                    folder_path_display[key],
                    base,
                    [by_stem[name] for name in expected],
                )
            )
    complete.sort(key=lambda item: (item[1].casefold(), item[0].casefold()))
    return complete


def _select_diffuse_entries(
    entries: Sequence[AssetEntry], base: str
) -> list[AssetEntry]:
    diffuse_entries: list[AssetEntry] = []
    candidate_groups = (
        (f"{base}_a.dds", f"{base}_Hull_a.dds"),
        (f"{base}_DeckHouse_a.dds",),
    )
    for candidates in candidate_groups:
        selected: list[AssetEntry] = []
        for diffuse_name in candidates:
            matches = [
                entry
                for entry in entries
                if entry.extension == ".dds"
                and PurePosixPath(entry.virtual_path).name.casefold()
                == diffuse_name.casefold()
                and "ship" in _path_parts(entry)
                and "textures" in _path_parts(entry)
            ]
            if len(matches) > 1:
                raise ExtractionError(
                    f"expected at most one ship diffuse {diffuse_name}, "
                    f"found {len(matches)}"
                )
            if matches:
                selected = matches
                break
        if not selected:
            raise ExtractionError(
                "expected one ship diffuse from: " + ", ".join(candidates)
            )
        diffuse_entries.extend(selected)
    return diffuse_entries


def select_hull_assets(
    entries: Sequence[AssetEntry],
    ship_resource: str | None = None,
) -> tuple[str, list[AssetEntry], list[AssetEntry]]:
    """Find one exact complete hull and its base/DeckHouse diffuse maps."""
    complete = find_complete_hull_geometries(entries)
    if not complete:
        raise ExtractionError(
            "IDX contains no complete five-file hull geometry set"
        )
    if ship_resource is not None:
        requested = ship_resource.strip().casefold()
        if not requested or "/" in requested or "\\" in requested:
            raise ExtractionError(
                "--ship-resource must be one exact resource name from the catalog"
            )
        complete = [
            item for item in complete if item[1].casefold() == requested
        ]
        if not complete:
            raise ExtractionError(
                f"IDX contains no complete hull resource named {ship_resource!r}"
            )
    if len(complete) != 1:
        names = ", ".join(item[1] for item in complete)
        raise ExtractionError(f"IDX contains multiple complete hull sets: {names}")

    _, base, ordered_geometry = complete[0]
    diffuse_entries = _select_diffuse_entries(entries, base)
    return base, ordered_geometry, diffuse_entries

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_blender(
    blender: Path,
    input_obj: Path,
    report_json: Path,
    output_glb: Path,
    output_blend: Path,
    base_diffuse: Path,
    deckhouse_diffuse: Path,
    log_path: Path,
) -> None:
    if not blender.is_file():
        raise FileNotFoundError(f"Blender executable not found: {blender}")
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python",
        str(TOOL_DIR / "blender_validate.py"),
        "--",
        str(input_obj),
        str(report_json),
        str(output_glb),
        str(output_blend),
        str(base_diffuse),
        str(deckhouse_diffuse),
    ]
    completed = subprocess.run(
        command,
        check=False,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise ExtractionError(
            f"Blender background validation failed with exit code "
            f"{completed.returncode}; see {log_path}"
        )
    validate_glb(output_glb)
    blend_header = b""
    if output_blend.is_file():
        with output_blend.open("rb") as stream:
            blend_header = stream.read(7)
    if blend_header != b"BLENDER":
        raise ExtractionError(f"invalid or missing Blender project: {output_blend}")


def plan_payload(
    game_dir: Path,
    idx_path: Path,
    base: str,
    entries: Sequence[AssetEntry],
    output_root: Path,
) -> dict[str, object]:
    return {
        "mode": "dry-run",
        "game_dir": str(game_dir),
        "writes_game_directory": False,
        "index": str(idx_path),
        "ship_resource": base,
        "output_root": str(output_root),
        "selected_assets": [
            {
                **entry.to_dict(),
                "target": str(safe_output_path(output_root, entry.virtual_path)),
            }
            for entry in entries
        ],
        "limitations": LIMITATIONS,
    }


def execute_pipeline(args: argparse.Namespace) -> dict[str, object]:
    game_dir = args.game_dir.resolve()
    output_root = args.output_root.resolve()
    if args.intact_lod < 0:
        raise ExtractionError("--intact-lod must be zero or greater")
    if not (game_dir / "res_packages").is_dir():
        raise FileNotFoundError(f"res_packages not found under {game_dir}")
    if _is_inside(output_root, game_dir):
        raise ExtractionError(
            f"output root must be outside the game directory: {output_root}"
        )

    if args.ship_index_file is not None:
        idx_path = find_ship_index_file(game_dir, args.ship_index_file)
    else:
        idx_path = find_ship_index(game_dir, args.ship_index)
    index_entries = load_index_entries(game_dir, idx_path)
    base, geometry_entries, diffuse_entries = select_hull_assets(
        index_entries, args.ship_resource
    )
    selected_entries = [*geometry_entries, *diffuse_entries]
    unsupported_storage = [
        entry
        for entry in selected_entries
        if (
            entry.file_info.compression_type_1,
            entry.file_info.compression_type_2,
        )
        != (5, 1)
    ]
    if unsupported_storage:
        details = ", ".join(
            f"{entry.virtual_path}="
            f"({entry.file_info.compression_type_1},"
            f"{entry.file_info.compression_type_2})"
            for entry in unsupported_storage
        )
        raise ExtractionError(
            "selected assets use an unverified package storage variant: " + details
        )

    plan = plan_payload(
        game_dir, idx_path, base, selected_entries, output_root
    )
    if not args.execute:
        return plan

    export_dir = ensure_within_root(output_root / "exports" / base, output_root)
    output_obj = export_dir / f"{base}_intact_lod{args.intact_lod}.obj"
    decode_report_path = export_dir / f"{base}.decode.json"
    blender_report_path = export_dir / f"{base}.blender.json"
    output_glb = export_dir / f"{base}.glb"
    output_blend = export_dir / f"{base}.blend"
    blender_log = export_dir / "blender_background.log"
    pipeline_manifest = export_dir / "ship_export_manifest.json"

    planned_results = [
        extract_asset(entry, output_root, execute=False)
        for entry in selected_entries
    ]
    if not args.overwrite:
        planned_outputs = [
            *(result.target for result in planned_results),
            output_root / "extraction_manifest.json",
            output_obj,
            decode_report_path,
            pipeline_manifest,
        ]
        if not args.no_blender:
            planned_outputs.extend(
                [blender_report_path, output_glb, output_blend, blender_log]
            )
        existing = [path for path in planned_outputs if path.exists()]
        if existing:
            raise ExtractionError(
                "refusing partial overwrite; targets already exist: "
                + ", ".join(str(path) for path in existing)
            )

    results = [
        extract_asset(
            entry,
            output_root,
            execute=True,
            overwrite=args.overwrite,
            max_unpacked_size=args.max_single_mib * 1024 * 1024,
        )
        for entry in selected_entries
    ]
    extraction_manifest = write_manifest(
        results, output_root, "extraction_manifest.json"
    )

    verified_assets: list[dict[str, object]] = []
    for result in results:
        actual_crc = binascii.crc32(result.target.read_bytes()) & 0xFFFFFFFF
        expected_crc = result.entry.file_info.crc32
        if actual_crc != expected_crc:
            raise ExtractionError(
                f"post-write CRC mismatch for {result.target}: "
                f"{actual_crc:08x} != {expected_crc:08x}"
            )
        verified_assets.append(
            {
                **result.to_dict(),
                "actual_crc32": f"{actual_crc:08x}",
                "crc_verified": True,
                "sha256": _sha256(result.target),
            }
        )

    geometry_results = results[: len(HULL_SUFFIXES)]
    if 0 <= args.intact_lod <= 3:
        # The exact base file is a complete low-detail LOD4 hull. Mixing it
        # with segmented high-detail components duplicates the ship.
        geometry_targets = [result.target for result in geometry_results[1:5]]
        geometry_input_policy = "segmented Bow/MidFront/MidBack/Stern"
    else:
        geometry_targets = [geometry_results[0].target]
        geometry_input_policy = "complete base geometry (LOD4+)"

    diffuse_results = results[len(HULL_SUFFIXES) :]
    if len(diffuse_results) != 2:
        raise ExtractionError(
            f"expected two selected diffuse results, found {len(diffuse_results)}"
        )
    base_diffuse_target = diffuse_results[0].target
    deckhouse_diffuse_target = diffuse_results[1].target

    export_dir.mkdir(parents=True, exist_ok=True)

    decoded_files, parts = decode_geometry_files(
        geometry_targets, intact_lod=args.intact_lod
    )
    temporary_obj = output_obj.with_name(f".{output_obj.name}.part")
    try:
        with temporary_obj.open("wb") as output:
            totals = write_obj(parts, output)
        os.replace(temporary_obj, output_obj)
    finally:
        temporary_obj.unlink(missing_ok=True)
    decode_report = build_report(decoded_files, output_obj, parts, totals)
    _write_json_atomic(decode_report_path, decode_report)

    if not args.no_blender:
        run_blender(
            args.blender.resolve(),
            output_obj,
            blender_report_path,
            output_glb,
            output_blend,
            base_diffuse_target,
            deckhouse_diffuse_target,
            blender_log,
        )

    pipeline: dict[str, object] = {
        "mode": "executed",
        "game_dir": str(game_dir),
        "writes_game_directory": False,
        "index": str(idx_path),
        "ship_resource": base,
        "output_root": str(output_root),
        "extraction_manifest": str(extraction_manifest),
        "assets": verified_assets,
        "geometry": {
            "inputs": [str(path) for path in geometry_targets],
            "input_policy": geometry_input_policy,
            "intact_lod": args.intact_lod,
            "obj": str(output_obj),
            "decode_report": str(decode_report_path),
            "totals": totals,
        },
        "blender": {
            "skipped": args.no_blender,
            "executable": str(args.blender.resolve()),
            "report": str(blender_report_path) if not args.no_blender else None,
            "glb": str(output_glb) if not args.no_blender else None,
            "blend": str(output_blend) if not args.no_blender else None,
            "log": str(blender_log) if not args.no_blender else None,
            "base_diffuse": str(base_diffuse_target),
            "deckhouse_diffuse": str(deckhouse_diffuse_target),
            "material_rule": "DeckHouse name uses deck map; all other hull objects use base map",
        },
        "limitations": LIMITATIONS,
    }
    if not args.no_blender:
        pipeline["blender"].update(
            {
                "glb_bytes": output_glb.stat().st_size,
                "glb_sha256": _sha256(output_glb),
                "blend_bytes": output_blend.stat().st_size,
                "blend_sha256": _sha256(output_blend),
            }
        )

    pipeline["pipeline_manifest"] = str(pipeline_manifest)
    _write_json_atomic(pipeline_manifest, pipeline)
    return pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find one WoWS Legends ship IDX, CRC-extract five hull geometry "
            "files plus base/DeckHouse diffuse DDS, merge OBJ, and optionally build "
            "GLB/BLEND with Blender 3.5. Dry-run unless --execute is supplied."
        )
    )
    parser.add_argument("--game-dir", required=True, type=Path)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--ship-index-file",
        help="exact IDX basename returned by ship_catalog.py",
    )
    selector.add_argument(
        "--ship-index",
        action="append",
        help="IDX filename term; repeat to narrow the match",
    )
    parser.add_argument(
        "--ship-resource",
        help=(
            "exact complete hull resource returned by ship_catalog.py; "
            "required when an IDX contains multiple hull variants"
        ),
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--blender",
        type=Path,
        default=Path(
            r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe"
        ),
    )
    parser.add_argument("--intact-lod", type=int, default=0)
    parser.add_argument("--max-single-mib", type=int, default=512)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-blender", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = execute_pipeline(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (ExtractionError, FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
