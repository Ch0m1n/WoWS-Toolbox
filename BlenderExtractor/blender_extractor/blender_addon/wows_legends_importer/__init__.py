"""Blender import bridge for assets extracted by legends-assets.

Raw BigWorld/Legends geometry is intentionally rejected here.  The add-on only
imports Blender-supported interchange formats and loads DDS images.  That keeps
an extracted-but-undecoded ``.geometry`` from being mistaken for a usable mesh.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement
from bpy_extras.io_utils import ImportHelper


bl_info = {
    "name": "WoWS Legends Extracted Assets",
    "author": "Codex",
    "version": (0, 1, 0),
    "blender": (3, 5, 0),
    "location": "File > Import",
    "description": (
        "Import extracted GLB/glTF/FBX/OBJ files and load DDS textures; "
        "raw Legends geometry is reported as unsupported"
    ),
    "category": "Import-Export",
}


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


def _import_model(path: Path) -> None:
    extension = path.suffix.casefold()
    if extension in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif extension == ".obj":
        # Blender 4.x moved OBJ to wm.obj_import; retain the 3.5 fallback.
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(
                filepath=str(path), forward_axis="NEGATIVE_Y", up_axis="Z"
            )
        else:
            bpy.ops.import_scene.obj(
                filepath=str(path), axis_forward="-Y", axis_up="Z"
            )
    else:
        raise ValueError(f"not a directly importable model: {path}")


def _load_texture(path: Path) -> None:
    # Loading an image is truthful and reversible.  Automatic material binding
    # is avoided because Legends' channel meaning comes from .mfm/assets.bin.
    bpy.data.images.load(str(path), check_existing=True)


def _process_paths(paths: list[Path]) -> dict[str, object]:
    imported: list[str] = []
    textures: list[str] = []
    unsupported: list[str] = []
    failed: list[dict[str, str]] = []
    for path in paths:
        extension = path.suffix.casefold()
        if extension in RAW_EXTENSIONS:
            unsupported.append(str(path))
            continue
        try:
            if extension in MODEL_EXTENSIONS:
                _import_model(path)
                imported.append(str(path))
            elif extension in TEXTURE_EXTENSIONS:
                _load_texture(path)
                textures.append(str(path))
            else:
                unsupported.append(str(path))
        except Exception as exc:  # Blender operators expose varied exceptions.
            failed.append({"path": str(path), "error": str(exc)})
    return {
        "models_imported": imported,
        "textures_loaded": textures,
        "unsupported": unsupported,
        "failed": failed,
    }


class IMPORT_SCENE_OT_wows_legends_assets(Operator, ImportHelper):
    bl_idname = "import_scene.wows_legends_assets"
    bl_label = "WoWS Legends Extracted Assets"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ""
    filter_glob: StringProperty(
        default=(
            "*.glb;*.gltf;*.fbx;*.obj;*.dds;*.dd0;*.dd1;*.png;*.tga;"
            "*.jpg;*.jpeg;*.geometry;*.primitive;*.primitives;*.model;*.visual"
        ),
        options={"HIDDEN"},
    )
    files: CollectionProperty(type=OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")

    def execute(self, _context):
        base = Path(self.directory)
        paths = [base / item.name for item in self.files]
        if not paths and self.filepath:
            paths = [Path(self.filepath)]
        result = _process_paths(paths)
        imported = len(result["models_imported"])
        textures = len(result["textures_loaded"])
        unsupported = len(result["unsupported"])
        failed = len(result["failed"])
        if unsupported:
            self.report(
                {"WARNING"},
                f"{unsupported} raw/unsupported files skipped; convert geometry first",
            )
        if failed:
            self.report({"ERROR"}, f"{failed} files failed to import")
        self.report(
            {"INFO"},
            f"Imported {imported} models; loaded {textures} textures",
        )
        return {"FINISHED"} if imported or textures or unsupported else {"CANCELLED"}


class IMPORT_SCENE_OT_wows_legends_manifest(Operator, ImportHelper):
    bl_idname = "import_scene.wows_legends_manifest"
    bl_label = "WoWS Legends Extraction Manifest"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, _context):
        manifest_path = Path(self.filepath)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            rows = payload.get("results", [])
            paths = [
                Path(row["target"])
                for row in rows
                if row.get("status") == "extracted" and row.get("target")
            ]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.report({"ERROR"}, f"Invalid extraction manifest: {exc}")
            return {"CANCELLED"}
        result = _process_paths(paths)
        imported = len(result["models_imported"])
        textures = len(result["textures_loaded"])
        unsupported = len(result["unsupported"])
        failed = len(result["failed"])
        if unsupported:
            self.report(
                {"WARNING"},
                f"{unsupported} raw descriptor/geometry files were not imported",
            )
        if failed:
            self.report({"ERROR"}, f"{failed} manifest entries failed")
        self.report(
            {"INFO"},
            f"Manifest: {imported} models imported, {textures} textures loaded",
        )
        return {"FINISHED"}


CLASSES = (
    IMPORT_SCENE_OT_wows_legends_assets,
    IMPORT_SCENE_OT_wows_legends_manifest,
)


def _menu_import(self, _context):
    self.layout.operator(
        IMPORT_SCENE_OT_wows_legends_assets.bl_idname,
        text="WoWS Legends Extracted Assets",
    )
    self.layout.operator(
        IMPORT_SCENE_OT_wows_legends_manifest.bl_idname,
        text="WoWS Legends Extraction Manifest",
    )


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
