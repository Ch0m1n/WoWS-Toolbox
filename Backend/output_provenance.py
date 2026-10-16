"""Portable extraction provenance and bounded, offline artifact validation.

Only declared output files and OBJ/MTL dependencies are inspected. Game data,
logs and installation paths are never embedded in the portable manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any

SCHEMA = "wows-toolbox-output/v1"
MANIFEST_NAME = "extraction.manifest.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_child(root: Path, value: str, *, base: Path | None = None) -> Path:
    """Reject traversal, symlinks and Windows junctions before opening a file."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (base or root) / candidate
    # abspath is lexical: do not resolve links before checking each component.
    candidate = Path(os.path.abspath(candidate))
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or (
            current.exists() and getattr(current.stat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("linked output reference")
    candidate.resolve().relative_to(root)
    return candidate


def inspect_outputs(root: Path, result: dict, *, expected: list | None = None) -> dict:
    root = root.resolve()
    issues: list[dict] = []
    artifacts: dict[str, dict] = {}
    visited: set[Path] = set()

    def issue(code: str, file: str | None, severity: str = "error") -> None:
        issues.append({"code": code, "file": file, "severity": severity})

    if result.get("ok") is False:
        issue("exporter_validation_failed", None)

    def visit(value: str, role: str, *, base: Path | None = None) -> Path | None:
        try:
            path = safe_child(root, value, base=base)
        except (ValueError, OSError):
            issue("outside_or_linked_reference", None)
            return None
        relative = path.relative_to(root).as_posix()
        if path in visited:
            return path
        visited.add(path)
        try:
            if not path.is_file() or path.stat().st_size == 0:
                issue("missing_or_empty_" + role, relative)
                return None
            artifacts[relative] = {"path": relative, "role": role,
                                   "bytes": path.stat().st_size, "sha256": digest(path)}
        except OSError:
            issue("unreadable_" + role, relative)
            return None
        return path

    obj_value = result.get("obj") or result.get("obj_file")
    if not obj_value:
        issue("missing_obj", None)
    else:
        obj = visit(str(obj_value), "obj")
        if obj:
            has_faces = False
            has_material_library = False
            mtls: list[Path] = []
            try:
                with obj.open(encoding="utf-8-sig", errors="replace") as stream:
                    for line in stream:
                        parts = line.strip().split(maxsplit=1)
                        tag, tail = (parts + [""])[:2] if parts else ("", "")
                        if tag == "f":
                            has_faces = True
                        if tag == "mtllib":
                            has_material_library = True
                            # Our exporters write one filename per declaration;
                            # prefer an existing filename with spaces over splitting.
                            try:
                                whole = safe_child(root, tail.strip().strip('"'), base=obj.parent)
                                names = [tail.strip().strip('"')] if whole.is_file() else shlex.split(tail, posix=False)
                            except (ValueError, OSError):
                                issue("outside_or_linked_reference", None)
                                continue
                            for name in names:
                                path = visit(name.strip('"'), "mtl", base=obj.parent)
                                if path:
                                    mtls.append(path)
                if not has_faces:
                    issue("obj_has_no_faces", obj.relative_to(root).as_posix())
                if not has_material_library and (result.get("mtl") or result.get("mtl_file") or result.get("material_count")):
                    issue("obj_missing_material_library", obj.relative_to(root).as_posix())
                for mtl in mtls:
                    with mtl.open(encoding="utf-8-sig", errors="replace") as stream:
                        for line in stream:
                            text = line.strip()
                            if text.startswith("# wows_map_"):
                                text = text[2:].removeprefix("wows_")
                            parts = text.split(maxsplit=1)
                            tag, tail = (parts + [""])[:2] if parts else ("", "")
                            if tag.startswith("map_") or tag in {"bump", "disp", "norm", "refl"}:
                                # External MTL option syntax is not guessed as a filename.
                                if tail.lstrip().startswith("-"):
                                    issue("texture_options_unverified", mtl.relative_to(root).as_posix(), "warning")
                                else:
                                    visit(tail.strip().strip('"'), "texture", base=mtl.parent)
            except (OSError, ValueError):
                issue("unreadable_model_references", obj.relative_to(root).as_posix())
    for key, role in (("mtl", "mtl"), ("mtl_file", "mtl"), ("glb", "glb")):
        if result.get(key):
            visit(str(result[key]), role)
    armor = result.get("armor") or {}
    if armor.get("available") and armor.get("path"):
        visit(str(armor["path"]), "armor")
    if result.get("armor_export_error") or result.get("armor_sidecar_error"):
        issue("optional_armor_failed", None, "warning")
    if expected is not None:
        for saved in expected:
            path = visit(saved["path"], saved.get("role", "artifact"))
            if path:
                current = artifacts[path.relative_to(root).as_posix()]
                if current["sha256"] != saved["sha256"] or current["bytes"] != saved["bytes"]:
                    issue("artifact_changed", saved["path"])
    status = "failed" if any(i["severity"] == "error" for i in issues) else ("warning" if issues else "complete")
    return {"status": status, "issues": issues, "artifacts": sorted(artifacts.values(), key=lambda a: a["path"]),
            "scope": "file_integrity_and_references_only"}


def finalize_output(args: Any, result: dict) -> dict:
    root = Path(result["output_dir"]).resolve()
    root.relative_to(Path(args.output_root).resolve())
    validation = inspect_outputs(root, result)
    quality = result.get("quality_contract") or {}
    toolbox = Path(args.toolbox_root)
    version = None
    launcher_source = toolbox / "Launcher/WoWSToolboxLauncher.cs"
    if launcher_source.is_file():
        match = re.search(r'AssemblyInformationalVersion\("([^\"]+)"\)', launcher_source.read_text(encoding="utf-8-sig"))
        version = match.group(1) if match else None
    if version is None:
        gui = toolbox / "GUI/WoWSToolboxGUI.ps1"
        if gui.is_file():
            match = re.search(r"\$script:AppVersion = '([^']+)'", gui.read_text(encoding="utf-8-sig"))
            version = match.group(1) if match else None
    tools = {}
    for name in (("wowsunpack.exe",) if args.source in {"pc", "korabli"} else ()):
        path = toolbox / "Backend" / name
        if path.is_file():
            tools[name] = digest(path)
    model = result.get("obj") or result.get("obj_file")
    manifest = {
        "schema": SCHEMA, "application_version": version,
        "source": {"game": args.source, "build": result.get("build") or (result.get("cache") or {}).get("build")},
        "request_identity": getattr(args, "request_identity", None),
        "selection": {key: getattr(args, key, None) for key in ("ship_index", "ship_key", "selected_model_path", "hull_upgrade", "artillery_upgrade", "torpedo_upgrade")},
        "requested": {key: getattr(args, key, None) for key in ("formats", "lod", "texture_max_size", "camouflage", "camouflage_color_scheme")},
        "applied": {"formats": result.get("formats", ["obj"]),
                    "lod": quality.get("model_lod", result.get("lod")),
                    "texture_max_size": quality.get("texture_max_size"),
                    "camouflage": result.get("camouflage", quality.get("camouflage")),
                    "camouflage_color_scheme": result.get("camouflage_color_scheme", quality.get("camouflage_color_scheme")),
                    "modules": result.get("modules")},
        "tool_sha256": tools,
        "model": None,
        "armor_available": (result.get("armor") or {}).get("available"),
        "counts": {key: result.get(key) for key in ("object_count", "material_count", "texture_count", "face_count")},
        "artifacts": validation.pop("artifacts"), "validation": validation,
    }
    manifest["requested"]["lod"] = getattr(args, "requested_lod", getattr(args, "lod", None))
    manifest["requested"]["texture_max_size"] = getattr(args, "requested_texture_max_size", getattr(args, "texture_max_size", None))
    if model:
        try:
            manifest["model"] = safe_child(root, str(model)).relative_to(root).as_posix()
        except (ValueError, OSError):
            pass
    path = root / MANIFEST_NAME
    atomic_json(path, manifest)
    result.update(provenance_manifest=str(path), output_validation=validation)
    if validation["status"] == "failed":
        raise RuntimeError("Output validation failed; see extraction.manifest.json")
    return result


def revalidate(path: Path) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Manifest is too large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("artifacts"), list):
        raise ValueError("Unsupported output manifest")
    for entry in manifest["artifacts"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or Path(entry["path"]).is_absolute():
            raise ValueError("Invalid artifact path")
        if not isinstance(entry.get("bytes"), int) or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256"))):
            raise ValueError("Invalid artifact fingerprint")
    result = {"obj": manifest.get("model")}
    # Rechecking the files cannot repair a recorded optional armor extraction failure.
    if any(issue.get("code") == "optional_armor_failed" for issue in manifest.get("validation", {}).get("issues", [])):
        result["armor_export_error"] = "recorded optional failure"
    return inspect_outputs(path.parent, result, expected=manifest["artifacts"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recheck exported model files without game data or an exporter")
    parser.add_argument("manifest", type=Path)
    options = parser.parse_args()
    result = revalidate(options.manifest)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)
