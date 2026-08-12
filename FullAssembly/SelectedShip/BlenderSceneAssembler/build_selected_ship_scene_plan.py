#!/usr/bin/env python3
"""Build a dynamic Blender scene plan from a selected-ship mapping.

The resulting plan is consumed by the verified reusable
``Ticonderoga1990/BlenderSceneAssembler/assemble_scene.py`` implementation.
No ship-specific part counts or model stems are assumed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


BUILTIN_VISIBILITY_PROFILES = {
    "harbor_dock": {"dock": True, "overlay": False},
    "neutral_battle_intact": {"dock": False, "overlay": False},
    "overlay_debug": {"dock": True, "overlay": True},
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _normalize_model_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid model path: {value!r}")
    path = PurePosixPath(value.strip().replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe model path: {value!r}")
    result = path.as_posix()
    if not result.endswith(".model"):
        raise ValueError(f"model path must end in .model: {value!r}")
    return result


def _resolve_summary_path(value: str, summary_dir: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = summary_dir / path
    return Path(os.path.abspath(str(path)))


def _array(mapping: Mapping[str, Any], name: str) -> list[Any]:
    value = mapping.get(name, [])
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _require_accepted_mapping(mapping: Mapping[str, Any]) -> None:
    schema = mapping.get("schema")
    if schema != "wows-legends-static-ship-assembly/v1":
        raise ValueError(f"unsupported mapping schema: {schema!r}")
    validation = mapping.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("mapping.validation must be an object")
    if validation.get("static_assembly_acceptance") is not True:
        raise ValueError("mapping static assembly is not accepted")


def _batch_glb_lookup(summary: Mapping[str, Any], summary_dir: Path) -> dict[str, str]:
    strict = summary.get("strict_validation")
    if not isinstance(strict, dict) or strict.get("accepted") is not True:
        raise ValueError("batch strict_validation.accepted is not true")
    results = summary.get("results")
    if not isinstance(results, list):
        raise ValueError("batch results must be an array")
    model_count = summary.get("model_count")
    if not isinstance(model_count, int) or model_count != len(results):
        raise ValueError(
            f"batch model count mismatch: {model_count!r} vs {len(results)}"
        )

    lookup: dict[str, str] = {}
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise ValueError(f"batch result {index} must be an object")
        if result.get("status") != "OK":
            raise ValueError(f"batch result {index} is not OK")
        model_path = _normalize_model_path(result.get("model_path"))
        output_glb = result.get("output_glb")
        if not isinstance(output_glb, str) or not output_glb:
            raise ValueError(f"batch result {index} lacks output_glb")
        glb = _resolve_summary_path(output_glb, summary_dir)
        if not glb.is_file():
            raise FileNotFoundError(f"batch GLB does not exist: {glb}")
        if model_path in lookup:
            raise ValueError(f"duplicate batch model path: {model_path}")
        lookup[model_path] = str(glb)
    return lookup


def _glb_for(model_path: object, lookup: Mapping[str, str]) -> str:
    normalized = _normalize_model_path(model_path)
    try:
        return lookup[normalized]
    except KeyError as exc:
        raise KeyError(f"no converted GLB for {normalized}") from exc


def _matrix(item: Mapping[str, Any]) -> list[float]:
    matrix_object = item.get("corrected_gltf_rh_y_up_matrix")
    if matrix_object is None:
        matrix_object = item.get("matrix")
    if isinstance(matrix_object, dict):
        values = matrix_object.get("column_major")
    else:
        values = matrix_object
    if not isinstance(values, list) or len(values) != 16:
        raise ValueError("assembly item matrix must contain 16 values")
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("assembly item matrix contains a non-finite value")
    return result


def _visibility_policy(
    assembly: Mapping[str, Any], profile_name: str
) -> dict[str, bool]:
    raw_profiles = assembly.get("visibility_profiles", {})
    if raw_profiles is not None and not isinstance(raw_profiles, dict):
        raise ValueError("visibility_profiles must be an object")
    raw = raw_profiles.get(profile_name) if isinstance(raw_profiles, dict) else None
    if raw is None:
        raw = BUILTIN_VISIBILITY_PROFILES.get(profile_name)
    if not isinstance(raw, dict):
        available = sorted(
            set(BUILTIN_VISIBILITY_PROFILES)
            | set(raw_profiles if isinstance(raw_profiles, dict) else {})
        )
        raise ValueError(
            f"unknown visibility profile {profile_name!r}; available={available}"
        )
    dock = raw.get("dock", raw.get("dock_misc"))
    overlay = raw.get("overlay", raw.get("runtime_action_overlays"))
    if not isinstance(dock, bool) or not isinstance(overlay, bool):
        raise ValueError(f"{profile_name}: dock/overlay visibility must be bool")
    return {"dock": dock, "overlay": overlay}


def _is_visible(condition: object, policy: Mapping[str, bool]) -> bool:
    if condition in (None, "always"):
        return True
    if condition == "dock":
        return policy["dock"]
    if condition in (
        "overlay",
        "runtime_overlay",
        "launch_action_state",
    ):
        return policy["overlay"]
    raise ValueError(f"unsupported visibility condition: {condition!r}")


def _ship_key(assembly: Mapping[str, Any]) -> str:
    ship = assembly.get("ship")
    if isinstance(ship, dict) and isinstance(ship.get("ship_key"), str):
        value = ship["ship_key"]
    else:
        value = assembly.get("ship_key")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("mapping ship_key is missing")
    return value.strip()


def _safe_ship_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned or "SelectedShip"


def build_plan(
    assembly_path: Path,
    batch_summary_path: Path,
    visibility_profile: str = "harbor_dock",
    output_blend: str | None = None,
    output_glb: str | None = None,
    output_combined_obj: str | None = None,
    validation_json: str | None = None,
) -> dict[str, Any]:
    assembly_path = Path(os.path.abspath(str(assembly_path))).resolve()
    batch_summary_path = Path(os.path.abspath(str(batch_summary_path))).resolve()
    assembly = _load_json(assembly_path)
    summary = _load_json(batch_summary_path)
    _require_accepted_mapping(assembly)
    assembly_sha = _sha256(assembly_path)
    expected_sha = str(summary.get("source_mapping_sha256", "")).upper()
    if not expected_sha or expected_sha != assembly_sha:
        raise ValueError(
            f"batch/mapping SHA-256 mismatch: "
            f"batch={expected_sha!r}, mapping={assembly_sha}"
        )

    lookup = _batch_glb_lookup(summary, batch_summary_path.parent)
    policy = _visibility_policy(assembly, visibility_profile)
    ship_key = _ship_key(assembly)
    safe_ship = _safe_ship_name(ship_key)

    hull_glbs: list[str] = []
    hull_records: list[dict[str, Any]] = []
    for item in _array(assembly, "hull_parts"):
        if not isinstance(item, dict):
            raise ValueError("hull_parts entry must be an object")
        if item.get("role") != "mesh" or item.get("render_required", True) is False:
            continue
        model_path = _normalize_model_path(item.get("path"))
        glb = _glb_for(model_path, lookup)
        hull_glbs.append(glb)
        hull_records.append(
            {
                "model_path": model_path,
                "output_glb": glb,
                "role": "mesh",
            }
        )
    if not hull_glbs:
        raise ValueError("mapping contains no render-bearing hull mesh")

    mounts: list[dict[str, Any]] = []
    skipped_non_rendering = 0
    for item in _array(assembly, "combat_mounts"):
        if not isinstance(item, dict):
            raise ValueError("combat mount entry must be an object")
        if item.get("render_required", True) is False:
            skipped_non_rendering += 1
            continue
        model_path = _normalize_model_path(item.get("model_path"))
        hardpoint = item.get("hardpoint")
        category = item.get("category")
        if not isinstance(hardpoint, str) or not hardpoint:
            raise ValueError("combat mount lacks hardpoint")
        if not isinstance(category, str) or not category:
            raise ValueError("combat mount lacks category")
        condition = item.get("visibility_condition", "always")
        mounts.append(
            {
                "hardpoint": hardpoint,
                "category": f"Combat_{category}",
                "model_glb": _glb_for(model_path, lookup),
                "matrix": _matrix(item),
                "visible": _is_visible(condition, policy),
                "assembly_kind": "combat",
                "visibility_condition": condition,
                "source_model_path": model_path,
                "source_component": item.get("component"),
            }
        )

    for item in _array(assembly, "misc_instances"):
        if not isinstance(item, dict):
            raise ValueError("misc instance entry must be an object")
        if item.get("render_required", True) is False:
            skipped_non_rendering += 1
            continue
        model_path = _normalize_model_path(item.get("model_path"))
        instance_name = item.get("instance_name")
        if not isinstance(instance_name, str) or not instance_name:
            raise ValueError("misc instance lacks instance_name")
        condition = item.get("visibility_condition", "always")
        mounts.append(
            {
                "hardpoint": instance_name,
                "category": "Misc_Dock" if condition == "dock" else "Misc",
                "model_glb": _glb_for(model_path, lookup),
                "matrix": _matrix(item),
                "visible": _is_visible(condition, policy),
                "assembly_kind": "misc",
                "visibility_condition": condition,
                "source_model_path": model_path,
                "source_hull_model_path": item.get("source_hull_model_path"),
            }
        )

    for sequence, item in enumerate(_array(assembly, "runtime_action_overlays")):
        if not isinstance(item, dict):
            raise ValueError("runtime overlay entry must be an object")
        if item.get("render_required", True) is False:
            skipped_non_rendering += 1
            continue
        model_path = _normalize_model_path(item.get("model_path"))
        parent = item.get("parent_hardpoint")
        if not isinstance(parent, str) or not parent:
            raise ValueError("runtime overlay lacks parent_hardpoint")
        condition = item.get("visibility_condition", "overlay")
        instance_name = item.get("instance_name")
        if not isinstance(instance_name, str) or not instance_name:
            instance_name = f"{parent}_ACTION_{sequence + 1}"
        mounts.append(
            {
                "hardpoint": instance_name,
                "category": "RuntimeOverlay",
                "model_glb": _glb_for(model_path, lookup),
                "matrix": _matrix(item),
                "visible": _is_visible(condition, policy),
                "assembly_kind": "runtime_overlay",
                "visibility_condition": condition,
                "parent_hardpoint": parent,
                "source_model_path": model_path,
                "role": item.get("role"),
            }
        )

    category_counts = {
        "combat": sum(item["assembly_kind"] == "combat" for item in mounts),
        "misc": sum(item["assembly_kind"] == "misc" for item in mounts),
        "runtime_overlay": sum(
            item["assembly_kind"] == "runtime_overlay" for item in mounts
        ),
    }
    visible = sum(bool(item["visible"]) for item in mounts)
    hidden = len(mounts) - visible
    return {
        "schema": "wows-legends-blender-assembly-plan/v2",
        "profile_id": f"selected_ship_{safe_ship}",
        "visibility_profile": visibility_profile,
        "visibility_policy": policy,
        "ship_key": ship_key,
        "source_files": {
            "assembly": str(assembly_path),
            "assembly_sha256": assembly_sha,
            "batch_summary": str(batch_summary_path),
        },
        "counts": {
            "hull_glbs": len(hull_glbs),
            "mounts": len(mounts),
            **category_counts,
            "default_visible": visible,
            "default_hidden": hidden,
            "skipped_non_rendering": skipped_non_rendering,
        },
        "hull_records": hull_records,
        "hull_glbs": hull_glbs,
        "mounts": mounts,
        "output_blend": output_blend or f"{safe_ship}.blend",
        "output_glb": output_glb or f"{safe_ship}.glb",
        "output_combined_obj": (output_combined_obj or f"{safe_ship}_Combined.obj"),
        "validation_json": (validation_json or f"{safe_ship}.validation.json"),
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--batch-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--visibility-profile",
        default="harbor_dock",
        help=(
            "built-in harbor_dock/neutral_battle_intact/overlay_debug or "
            "a mapping visibility_profiles key"
        ),
    )
    parser.add_argument("--output-blend")
    parser.add_argument("--output-glb")
    parser.add_argument("--output-combined-obj")
    parser.add_argument("--validation-json")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = build_plan(
        args.assembly,
        args.batch_summary,
        visibility_profile=args.visibility_profile,
        output_blend=args.output_blend,
        output_glb=args.output_glb,
        output_combined_obj=args.output_combined_obj,
        validation_json=args.validation_json,
    )
    output = Path(os.path.abspath(str(args.output)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "ship_key": plan["ship_key"],
                "visibility_profile": plan["visibility_profile"],
                "counts": plan["counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
