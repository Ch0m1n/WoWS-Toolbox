"""Command-line interface for the safe Legends asset pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from . import __version__
from .core import (
    BIGWORLD_DESCRIPTOR_EXTENSIONS,
    BIGWORLD_MESH_EXTENSIONS,
    BLENDER_DIRECT_EXTENSIONS,
    BLENDER_TEXTURE_EXTENSIONS,
    MATERIAL_EXTENSIONS,
    AssetEntry,
    ExtractionError,
    ExtractionResult,
    assets_from_index,
    extract_asset,
    iter_assets,
    matches_asset,
    parse_legends_idx,
    run_geometry_converter,
    summarize_assets,
    write_manifest,
)
from .exporters import run_ship_exporter


TOOL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = TOOL_ROOT / "output"
SYSTEM_DATA_REQUIRED_PATHS = {"content/gameparams.data", "content/assets.bin"}


def _game_dir(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not (path / "res_packages").is_dir():
        raise argparse.ArgumentTypeError(f"res_packages not found under {path}")
    return path


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _selected_entries(
    game_dir: Path,
    *,
    patterns: Sequence[str],
    extensions: Sequence[str],
    packages: Sequence[str],
    index_pattern: str = "*.idx",
) -> list[AssetEntry]:
    return [
        entry
        for entry in iter_assets(game_dir, index_pattern=index_pattern)
        if matches_asset(
            entry,
            patterns=patterns,
            extensions=extensions,
            package_filters=packages,
        )
    ]


def _enforce_plan_limits(
    entries: Sequence[AssetEntry],
    *,
    max_files: int,
    max_total_mib: int,
) -> None:
    if len(entries) > max_files:
        raise ExtractionError(
            f"selection has {len(entries)} files; --max-files is {max_files}"
        )
    total = sum(entry.file_info.unpacked_size for entry in entries)
    limit = max_total_mib * 1024 * 1024
    if total > limit:
        raise ExtractionError(
            f"selection expands to {total} bytes; --max-total-mib allows {limit}"
        )


def _extract_many(
    entries: Sequence[AssetEntry],
    *,
    output_root: Path,
    execute: bool,
    overwrite: bool,
    max_single_mib: int,
    manifest: str | None,
) -> list[ExtractionResult]:
    max_single = max_single_mib * 1024 * 1024
    results = [
        extract_asset(
            entry,
            output_root,
            execute=execute,
            overwrite=overwrite,
            max_unpacked_size=max_single,
        )
        for entry in entries
    ]
    if execute and manifest:
        write_manifest(results, output_root, manifest)
    return results


def command_probe(args: argparse.Namespace) -> int:
    errors: list[str] = []
    entries = list(iter_assets(args.game_dir, skip_errors=True, errors=errors))
    package_dir = args.game_dir / "res_packages"
    summary = summarize_assets(entries)
    summary.update(
        {
            "game_dir": str(args.game_dir),
            "idx_files": len(list(package_dir.glob("*.idx"))),
            "pkg_files": len(list(package_dir.glob("*.pkg"))),
            "parse_errors": errors,
            "verified_idx_variant": "ISFP marker 0x01010005, header version 0x40",
            "writes_game_directory": False,
        }
    )
    _json_print(summary)
    return 0 if not errors else 2


def command_list(args: argparse.Namespace) -> int:
    entries = _selected_entries(
        args.game_dir,
        patterns=args.pattern,
        extensions=_csv(args.extensions),
        packages=args.package,
        index_pattern=args.index_pattern,
    )
    entries.sort(key=lambda entry: (entry.virtual_path.casefold(), entry.idx_path.name))
    shown = entries[: args.limit]
    if args.json:
        _json_print(
            {
                "matched": len(entries),
                "shown": len(shown),
                "assets": [entry.to_dict() for entry in shown],
            }
        )
    else:
        for entry in shown:
            print(
                f"{entry.virtual_path}\t{entry.file_info.unpacked_size}\t"
                f"{entry.support.value}\t{entry.idx_path.name}"
            )
        if len(entries) > len(shown):
            print(f"... {len(entries) - len(shown)} more matches", file=sys.stderr)
    return 0


def command_extract(args: argparse.Namespace) -> int:
    extensions = _csv(args.extensions)
    if not args.pattern and not extensions and not args.package:
        raise ExtractionError(
            "refusing unbounded extraction; provide --pattern, --extensions, "
            "or --package"
        )
    entries = _selected_entries(
        args.game_dir,
        patterns=args.pattern,
        extensions=extensions,
        packages=args.package,
        index_pattern=args.index_pattern,
    )
    entries.sort(key=lambda entry: (entry.idx_path.name, entry.virtual_path.casefold()))
    _enforce_plan_limits(
        entries, max_files=args.max_files, max_total_mib=args.max_total_mib
    )
    results = _extract_many(
        entries,
        output_root=args.output_root,
        execute=args.execute,
        overwrite=args.overwrite,
        max_single_mib=args.max_single_mib,
        manifest=args.manifest,
    )
    _json_print(
        {
            "mode": "execute" if args.execute else "dry-run",
            "output_root": str(args.output_root.resolve()),
            "count": len(results),
            "unpacked_bytes": sum(
                result.entry.file_info.unpacked_size for result in results
            ),
            "results": [result.to_dict() for result in results],
        }
    )
    return 0


def command_sample(args: argparse.Namespace) -> int:
    extensions = _csv(args.extensions)
    if not extensions:
        raise ExtractionError("--extensions must contain at least one format")
    candidates = _selected_entries(
        args.game_dir,
        patterns=args.pattern,
        extensions=extensions,
        packages=args.package,
    )
    grouped: dict[str, list[AssetEntry]] = defaultdict(list)
    for entry in candidates:
        grouped[entry.extension].append(entry)
    selected: list[AssetEntry] = []
    for extension in extensions:
        normalized = extension.casefold()
        if not normalized.startswith("."):
            normalized = f".{normalized}"
        group = sorted(
            grouped.get(normalized, []),
            key=lambda entry: (
                entry.file_info.unpacked_size,
                entry.virtual_path.casefold(),
            ),
        )
        selected.extend(group[: args.per_extension])
    _enforce_plan_limits(
        selected, max_files=args.max_files, max_total_mib=args.max_total_mib
    )
    results = _extract_many(
        selected,
        output_root=args.output_root,
        execute=args.execute,
        overwrite=args.overwrite,
        max_single_mib=args.max_single_mib,
        manifest=args.manifest,
    )
    _json_print(
        {
            "mode": "execute" if args.execute else "dry-run",
            "requested_extensions": extensions,
            "count": len(results),
            "results": [result.to_dict() for result in results],
        }
    )
    return 0


def _ship_indexes(game_dir: Path, terms: Sequence[str]) -> list[Path]:
    package_dir = game_dir / "res_packages"
    folded_terms = [term.casefold() for term in terms if term.strip()]
    if not folded_terms:
        raise ExtractionError("at least one --ship-index term is required")
    matches = [
        path
        for path in sorted(package_dir.glob("*.idx"))
        if all(term in path.stem.casefold() for term in folded_terms)
    ]
    if not matches:
        raise ExtractionError(
            f"no IDX filename matched all terms: {', '.join(terms)}"
        )
    return matches


def command_extract_ship(args: argparse.Namespace) -> int:
    package_dir = args.game_dir / "res_packages"
    package_lookup = {
        path.name.casefold(): path
        for path in package_dir.glob("*.pkg")
        if path.is_file()
    }
    matched_indexes = _ship_indexes(args.game_dir, args.ship_index)
    entries: list[AssetEntry] = []
    for idx_path in matched_indexes:
        entries.extend(
            assets_from_index(
                parse_legends_idx(idx_path), package_dir, package_lookup
            )
        )

    if args.include_system_data:
        system_idx = package_dir / "system_data.idx"
        if not system_idx.is_file():
            raise FileNotFoundError(f"required index not found: {system_idx}")
        for entry in assets_from_index(
            parse_legends_idx(system_idx), package_dir, package_lookup
        ):
            if entry.virtual_path.casefold() in SYSTEM_DATA_REQUIRED_PATHS:
                entries.append(entry)

    deduped: dict[tuple[str, str, int], AssetEntry] = {}
    for entry in entries:
        key = (
            entry.package_path.name.casefold(),
            entry.virtual_path.casefold(),
            entry.file_info.offset,
        )
        deduped[key] = entry
    entries = sorted(
        deduped.values(),
        key=lambda entry: (entry.idx_path.name, entry.virtual_path.casefold()),
    )
    _enforce_plan_limits(
        entries, max_files=args.max_files, max_total_mib=args.max_total_mib
    )
    results = _extract_many(
        entries,
        output_root=args.output_root,
        execute=args.execute,
        overwrite=args.overwrite,
        max_single_mib=args.max_single_mib,
        manifest=args.manifest,
    )
    _json_print(
        {
            "mode": "execute" if args.execute else "dry-run",
            "matched_indexes": [path.name for path in matched_indexes],
            "included_system_data": args.include_system_data,
            "count": len(results),
            "unpacked_bytes": sum(
                result.entry.file_info.unpacked_size for result in results
            ),
            "results": [result.to_dict() for result in results],
        }
    )
    return 0


def command_convert_geometry(args: argparse.Namespace) -> int:
    result = run_geometry_converter(
        args.input,
        args.output,
        args.converter,
        args.output_root,
        execute=args.execute,
    )
    _json_print(result)
    return 0 if result["status"] in {"dry-run", "converted-and-validated"} else 3


def command_export_ship(args: argparse.Namespace) -> int:
    result = run_ship_exporter(
        args.output_root,
        args.ship,
        args.output,
        args.exporter,
        execute=args.execute,
        hull_upgrade=args.hull,
        lod=args.lod,
        with_turrets=not args.no_turrets,
        with_textures=not args.no_textures,
        include_damage=args.include_damage,
        verbose=not args.quiet,
    )
    _json_print(result)
    return 0 if result["status"] in {"dry-run", "exported-and-validated"} else 3


def command_support(_: argparse.Namespace) -> int:
    _json_print(
        {
            "native_package_read": {
                "idx": "verified for Steam Legends marker 0x01010005 only",
                "pkg": "verified chunk table, raw DEFLATE/raw blocks, size and CRC",
            },
            "formats": {
                "blender_direct": sorted(BLENDER_DIRECT_EXTENSIONS),
                "blender_texture": sorted(BLENDER_TEXTURE_EXTENSIONS),
                "external_converter_required": sorted(BIGWORLD_MESH_EXTENSIONS),
                "descriptor_only_not_mesh": sorted(BIGWORLD_DESCRIPTOR_EXTENSIONS),
                "material_descriptor": sorted(MATERIAL_EXTENSIONS),
            },
            "important_limit": (
                ".model/.visual are descriptors, not meshes. Legends legacy "
                ".geometry is not compatible with the tested PC converter and "
                "is never reported as converted without a validated GLB."
            ),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legends-assets",
        description=(
            "Read-only WoWS: Legends package inventory/extraction and Blender staging"
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="inventory the complete install")
    probe.add_argument("--game-dir", required=True, type=_game_dir)
    probe.set_defaults(func=command_probe)

    listing = subparsers.add_parser("list", help="list matching virtual assets")
    listing.add_argument("--game-dir", required=True, type=_game_dir)
    listing.add_argument("--pattern", action="append", default=[])
    listing.add_argument("--extensions")
    listing.add_argument("--package", action="append", default=[])
    listing.add_argument("--index-pattern", default="*.idx")
    listing.add_argument("--limit", type=int, default=200)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=command_list)

    def extraction_options(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT
        )
        target.add_argument("--max-files", type=int, default=1000)
        target.add_argument("--max-total-mib", type=int, default=2048)
        target.add_argument("--max-single-mib", type=int, default=2048)
        target.add_argument("--manifest", default="extraction_manifest.json")
        target.add_argument("--execute", action="store_true")
        target.add_argument("--overwrite", action="store_true")

    extract = subparsers.add_parser(
        "extract", help="extract selected assets; dry-run unless --execute"
    )
    extract.add_argument("--game-dir", required=True, type=_game_dir)
    extract.add_argument("--pattern", action="append", default=[])
    extract.add_argument("--extensions")
    extract.add_argument("--package", action="append", default=[])
    extract.add_argument("--index-pattern", default="*.idx")
    extraction_options(extract)
    extract.set_defaults(func=command_extract)

    sample = subparsers.add_parser(
        "sample", help="extract the smallest N files of each requested extension"
    )
    sample.add_argument("--game-dir", required=True, type=_game_dir)
    sample.add_argument(
        "--extensions", default="geometry,visual,model,dds,obj,fbx,gltf,glb"
    )
    sample.add_argument("--per-extension", type=int, default=1)
    sample.add_argument("--pattern", action="append", default=[])
    sample.add_argument("--package", action="append", default=[])
    extraction_options(sample)
    sample.set_defaults(func=command_sample)

    ship = subparsers.add_parser(
        "extract-ship",
        help="extract every file from IDX names matching ship terms",
    )
    ship.add_argument("--game-dir", required=True, type=_game_dir)
    ship.add_argument("--ship-index", action="append", required=True)
    ship.add_argument(
        "--include-system-data",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    extraction_options(ship)
    ship.set_defaults(func=command_extract_ship)

    geometry = subparsers.add_parser(
        "convert-geometry",
        help="EXPERIMENTAL: run PC wows-geometry-cli; dry-run by default",
    )
    geometry.add_argument("--input", required=True, type=Path)
    geometry.add_argument("--output", required=True, type=Path)
    geometry.add_argument("--converter", required=True, type=Path)
    geometry.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT
    )
    geometry.add_argument("--execute", action="store_true")
    geometry.set_defaults(func=command_convert_geometry)

    exporter = subparsers.add_parser(
        "export-ship",
        help="EXPERIMENTAL: run PC wows-gltf-exporter on extracted files",
    )
    exporter.add_argument("--ship", required=True)
    exporter.add_argument("--output", required=True, type=Path)
    exporter.add_argument("--exporter", required=True, type=Path)
    exporter.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT
    )
    exporter.add_argument("--hull")
    exporter.add_argument("--lod", type=int)
    exporter.add_argument("--no-turrets", action="store_true")
    exporter.add_argument("--no-textures", action="store_true")
    exporter.add_argument("--include-damage", action="store_true")
    exporter.add_argument("--quiet", action="store_true")
    exporter.add_argument("--execute", action="store_true")
    exporter.set_defaults(func=command_export_ship)

    support = subparsers.add_parser("support", help="print truthful format support")
    support.set_defaults(func=command_support)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ExtractionError,
        FileNotFoundError,
        ValueError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
