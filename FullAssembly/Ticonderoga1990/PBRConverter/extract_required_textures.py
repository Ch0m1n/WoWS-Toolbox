#!/usr/bin/env python3
"""CRC-verify or extract the exact logical texture list for the PBR batch.

Dry-run is the default. IDX/PKG inputs are opened read-only and all writes are
confined to the supplied extracted output root.
"""

from __future__ import annotations

import argparse
import binascii
import json
import sys
from pathlib import Path
from typing import Sequence


class RequiredTextureError(RuntimeError):
    pass


def find_native_extractor() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "blender_extractor",
        here.parents[2] / "BlenderExtractor" / "blender_extractor",
    ]
    for candidate in candidates:
        if (candidate / "legends_assets" / "core.py").is_file():
            return candidate
    raise RequiredTextureError("bundled native IDX/PKG extractor was not found")



def load_asset_api():
    root = find_native_extractor()
    sys.path.insert(0, str(root))
    try:
        from legends_assets.core import extract_asset, iter_assets
    finally:
        sys.path.pop(0)
    return extract_asset, iter_assets


def crc32(path: Path) -> int:
    value = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value = binascii.crc32(chunk, value)
    return value & 0xFFFFFFFF


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required-list", type=Path, required=True)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    required_list = args.required_list.resolve()
    game_dir = args.game_dir.resolve()
    output_root = args.output_root.resolve()
    payload = json.loads(required_list.read_text(encoding="utf-8"))
    logical_paths = sorted({item["path"] for item in payload["logical_paths"]})
    if not logical_paths or len(logical_paths) > 256:
        raise RequiredTextureError(
            f"refusing unexpected required texture count: {len(logical_paths)}"
        )
    if any(
        not path.startswith("content/") or not path.casefold().endswith(".dds")
        for path in logical_paths
    ):
        raise RequiredTextureError("required list contains a non-content DDS path")

    extract_asset, iter_assets = load_asset_api()
    wanted = {path.casefold(): path for path in logical_paths}
    found = {}
    for entry in iter_assets(game_dir):
        folded = entry.virtual_path.casefold()
        if folded in wanted:
            if folded in found:
                raise RequiredTextureError(
                    f"duplicate IDX entry for {entry.virtual_path}"
                )
            found[folded] = entry
        if len(found) == len(wanted):
            break

    missing_idx = [wanted[key] for key in wanted.keys() - found.keys()]
    results = []
    for folded, logical_path in sorted(wanted.items(), key=lambda item: item[1]):
        entry = found.get(folded)
        if entry is None:
            results.append({"path": logical_path, "status": "MISSING_FROM_IDX"})
            continue
        target = output_root.joinpath(*Path(logical_path).parts)
        expected = entry.file_info.crc32
        if target.is_file():
            actual = crc32(target)
            if actual == expected:
                status = "EXISTING_CRC_OK"
            elif not args.execute or not args.overwrite:
                status = "EXISTING_CRC_MISMATCH"
            else:
                extracted = extract_asset(
                    entry, output_root, execute=True, overwrite=True
                )
                actual = crc32(extracted.target)
                status = "OVERWRITTEN_CRC_OK" if actual == expected else "CRC_FAILED"
        elif not args.execute:
            actual = None
            status = "DRY_RUN_MISSING"
        else:
            extracted = extract_asset(entry, output_root, execute=True)
            actual = crc32(extracted.target)
            status = "EXTRACTED_CRC_OK" if actual == expected else "CRC_FAILED"
        results.append(
            {
                "path": logical_path,
                "idx": entry.idx_path.name,
                "package": entry.package_path.name,
                "target": str(target),
                "expected_crc32": f"{expected:08x}",
                "actual_crc32": f"{actual:08x}" if actual is not None else None,
                "status": status,
            }
        )

    failures = [
        item
        for item in results
        if item["status"]
        not in {
            "EXISTING_CRC_OK",
            "EXTRACTED_CRC_OK",
            "OVERWRITTEN_CRC_OK",
            "DRY_RUN_MISSING",
        }
    ]
    report = {
        "schema": "wows-legends-required-texture-extraction/v1",
        "mode": "execute" if args.execute else "dry-run",
        "game_dir": str(game_dir),
        "game_directory_access": "read-only",
        "output_root": str(output_root),
        "required": len(logical_paths),
        "idx_resolved": len(found),
        "missing_idx": missing_idx,
        "crc_ok": sum("CRC_OK" in item["status"] for item in results),
        "failures": failures,
        "results": results,
    }
    write_json(args.report.resolve(), report)
    print(json.dumps({key: report[key] for key in ("mode", "required", "idx_resolved", "crc_ok")}, indent=2))
    if failures or missing_idx:
        raise RequiredTextureError(
            f"required texture verification failed for {len(failures) + len(missing_idx)} entries"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RequiredTextureError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from None
