"""Batch importer for Blender.

Example:
    blender --background --python blender_batch_import.py -- \
      --manifest output/extraction_manifest.json \
      --output-root output \
      --blend-out output/scene.blend

Only GLB/glTF/FBX/OBJ files are imported. DDS-family files are loaded as images.
Raw .geometry/.model/.visual/.primitives files stop with an explicit warning.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


MODEL_EXTENSIONS = {".glb", ".gltf", ".fbx", ".obj"}
TEXTURE_EXTENSIONS = {".dds", ".dd0", ".dd1", ".png", ".tga", ".jpg", ".jpeg"}
RAW_EXTENSIONS = {
    ".geometry",
    ".primitive",
    ".primitives",
    ".primitives_processed",
    ".model",
    ".visual",
}


def within_root(path: Path, root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"output escapes --output-root: {resolved_path} (root {resolved_root})"
        ) from exc
    return resolved_path


def import_model(path: Path) -> None:
    extension = path.suffix.casefold()
    if extension in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif extension == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(
                filepath=str(path), forward_axis="NEGATIVE_Y", up_axis="Z"
            )
        else:
            bpy.ops.import_scene.obj(
                filepath=str(path), axis_forward="-Y", axis_up="Z"
            )
    else:
        raise ValueError(f"unsupported model extension: {path}")


def manifest_paths(path: Path) -> list[Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Path(row["target"])
        for row in payload.get("results", [])
        if row.get("status") == "extracted" and row.get("target")
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--blend-out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    paths = list(args.input)
    if args.manifest:
        paths.extend(manifest_paths(args.manifest))
    if not paths:
        print("error: no manifest entries or --input files", file=sys.stderr)
        return 2

    imported = 0
    textures = 0
    unsupported: list[str] = []
    failures: list[str] = []
    for path in paths:
        extension = path.suffix.casefold()
        if extension in RAW_EXTENSIONS:
            unsupported.append(str(path))
            continue
        try:
            if extension in MODEL_EXTENSIONS:
                import_model(path)
                imported += 1
            elif extension in TEXTURE_EXTENSIONS:
                bpy.data.images.load(str(path), check_existing=True)
                textures += 1
            else:
                unsupported.append(str(path))
        except Exception as exc:
            failures.append(f"{path}: {exc}")

    for path in unsupported:
        print(f"unsupported raw/non-importable asset: {path}", file=sys.stderr)
    for failure in failures:
        print(f"import failed: {failure}", file=sys.stderr)

    blend_out = within_root(args.blend_out, args.output_root)
    blend_out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_out))
    print(
        json.dumps(
            {
                "models_imported": imported,
                "textures_loaded": textures,
                "unsupported": len(unsupported),
                "failed": len(failures),
                "blend": str(blend_out),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not failures else 3


if __name__ == "__main__":
    blender_argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    raise SystemExit(main(blender_argv))
