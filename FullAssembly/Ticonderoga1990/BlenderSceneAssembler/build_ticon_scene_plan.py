#!/usr/bin/env python3
"""Build a Blender assembly plan from the verified Ticonderoga mapping.

The builder joins three sources:

1. the canonical static assembly JSON (placement and model identity);
2. the verified profile manifest (visibility policy);
3. the per-model batch summary (model stem -> generated GLB).

The default ``harbor_dock`` profile keeps both dock-only Mk141 caps visible and
keeps both AM5058 ShooterLaunchAction overlays in the plan but hidden.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Sequence


EXPECTED_COUNTS = {
    "hull": 4,
    "combat": 17,
    "misc": 10,
    "runtime_overlay": 2,
    "mounts": 29,
}


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _resolve_summary_path(value: str, summary_dir: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = summary_dir / path
    return Path(os.path.abspath(str(path)))


def _model_stem(model_path: str) -> str:
    if not isinstance(model_path, str) or not model_path:
        raise ValueError(f"invalid model path: {model_path!r}")
    return PurePosixPath(model_path.replace("\\", "/")).stem


def _batch_glb_lookup(
    batch_summary: Mapping[str, Any], summary_dir: Path
) -> Dict[str, str]:
    strict = batch_summary.get("strict_validation")
    if not isinstance(strict, dict) or strict.get("accepted") is not True:
        raise ValueError("batch summary strict_validation.accepted is not true")

    results = batch_summary.get("results")
    if not isinstance(results, list):
        raise ValueError("batch summary results must be an array")

    lookup: Dict[str, str] = {}
    model_by_stem: Dict[str, str] = {}
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise ValueError(f"batch result {index} is not an object")
        if result.get("status") != "OK":
            raise ValueError(
                f"batch result {index} is not OK: {result.get('status')!r}"
            )
        model_path = result.get("model_path")
        output_glb = result.get("output_glb")
        if not isinstance(model_path, str) or not isinstance(output_glb, str):
            raise ValueError(
                f"batch result {index} lacks model_path/output_glb strings"
            )
        stem = _model_stem(model_path)
        glb_path = _resolve_summary_path(output_glb, summary_dir)
        if not glb_path.is_file():
            raise FileNotFoundError(f"batch GLB does not exist: {glb_path}")
        previous = lookup.get(stem)
        if previous is not None and os.path.normcase(previous) != os.path.normcase(
            str(glb_path)
        ):
            raise ValueError(
                f"ambiguous model stem {stem!r}: "
                f"{model_by_stem[stem]!r} and {model_path!r}"
            )
        lookup[stem] = str(glb_path)
        model_by_stem[stem] = model_path

    return lookup


def _glb_for(model_path: str, lookup: Mapping[str, str]) -> str:
    stem = _model_stem(model_path)
    try:
        return lookup[stem]
    except KeyError as exc:
        raise KeyError(
            f"no batch results.output_glb for model stem {stem!r} "
            f"({model_path})"
        ) from exc


def _matrix(item: Mapping[str, Any]) -> list[float]:
    matrix_object = item.get("corrected_gltf_rh_y_up_matrix")
    if not isinstance(matrix_object, dict):
        raise ValueError("assembly item lacks corrected_gltf_rh_y_up_matrix")
    values = matrix_object.get("column_major")
    if not isinstance(values, list) or len(values) != 16:
        raise ValueError("assembly item matrix must contain 16 values")
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("assembly item matrix contains a non-finite value")
    return result


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _check_count(name: str, values: Iterable[Any]) -> list[Any]:
    items = list(values)
    expected = EXPECTED_COUNTS[name]
    if len(items) != expected:
        raise ValueError(f"{name}: expected {expected}, got {len(items)}")
    return items


def build_plan(
    assembly_path: Path,
    profile_path: Path,
    batch_summary_path: Path,
    visibility_profile: str = "harbor_dock",
    output_blend: str = "ticonderoga_1990_harbor.blend",
    output_glb: str = "ticonderoga_1990_harbor.glb",
    output_combined_obj: str = "Ticonderoga1990_Combined.obj",
    validation_json: str = "ticonderoga_1990_harbor.validation.json",
) -> Dict[str, Any]:
    assembly_path = Path(os.path.abspath(str(assembly_path)))
    profile_path = Path(os.path.abspath(str(profile_path)))
    batch_summary_path = Path(os.path.abspath(str(batch_summary_path)))

    assembly = _load_json(assembly_path)
    profile = _load_json(profile_path)
    batch_summary = _load_json(batch_summary_path)

    if profile.get("profile_id") != "ticonderoga_1990_verified_profile":
        raise ValueError("profile is not ticonderoga_1990_verified_profile")
    if profile.get("generic_ship_exporter") is not False:
        raise ValueError("profile must explicitly be Ticonderoga-specific")

    ship = assembly.get("ship")
    if not isinstance(ship, dict):
        raise ValueError("assembly ship metadata is missing")
    ship_key = ship.get("ship_key")
    if ship_key != profile.get("ship_key"):
        raise ValueError(
            f"ship key mismatch: assembly={ship_key!r}, "
            f"profile={profile.get('ship_key')!r}"
        )

    canonical_output = profile.get("canonical_output")
    if not isinstance(canonical_output, dict):
        raise ValueError("profile canonical_output is missing")
    expected_sha = str(canonical_output.get("sha256", "")).upper()
    actual_sha = _sha256(assembly_path)
    if expected_sha and actual_sha != expected_sha:
        raise ValueError(
            f"assembly SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
        )

    profiles = profile.get("visibility_profiles")
    if not isinstance(profiles, dict):
        raise ValueError("profile visibility_profiles is missing")
    policy = profiles.get(visibility_profile)
    if not isinstance(policy, dict):
        raise ValueError(
            f"unknown visibility profile {visibility_profile!r}; "
            f"available: {sorted(profiles)}"
        )
    dock_visible = policy.get("dock_misc")
    overlay_visible = policy.get("runtime_action_overlays")
    if not isinstance(dock_visible, bool):
        raise ValueError(
            f"{visibility_profile}.dock_misc must resolve to a bool"
        )
    if not isinstance(overlay_visible, bool):
        raise ValueError(
            f"{visibility_profile}.runtime_action_overlays must resolve to a bool"
        )

    glb_lookup = _batch_glb_lookup(
        batch_summary, batch_summary_path.parent
    )

    hull_parts = _check_count(
        "hull",
        (
            item
            for item in _array(assembly.get("hull_parts"), "hull_parts")
            if isinstance(item, dict) and item.get("role") == "mesh"
        ),
    )
    hull_glbs = []
    hull_records = []
    for item in hull_parts:
        model_path = item.get("path")
        if not isinstance(model_path, str):
            raise ValueError("hull mesh entry lacks path")
        glb = _glb_for(model_path, glb_lookup)
        hull_glbs.append(glb)
        hull_records.append({"model_path": model_path, "output_glb": glb})

    combat_items = _check_count(
        "combat", _array(assembly.get("combat_mounts"), "combat_mounts")
    )
    misc_items = _check_count(
        "misc", _array(assembly.get("misc_instances"), "misc_instances")
    )
    overlay_items = _check_count(
        "runtime_overlay",
        _array(
            assembly.get("runtime_action_overlays"),
            "runtime_action_overlays",
        ),
    )

    mounts: list[Dict[str, Any]] = []
    for item in combat_items:
        if not isinstance(item, dict):
            raise ValueError("combat mount entry must be an object")
        model_path = item.get("model_path")
        hardpoint = item.get("hardpoint")
        category = item.get("category")
        if not all(isinstance(value, str) for value in (model_path, hardpoint, category)):
            raise ValueError("combat mount lacks model_path/hardpoint/category")
        mounts.append(
            {
                "hardpoint": hardpoint,
                "category": f"Combat_{category}",
                "model_glb": _glb_for(model_path, glb_lookup),
                "matrix": _matrix(item),
                "visible": True,
                "assembly_kind": "combat",
                "source_model_path": model_path,
                "source_component": item.get("component"),
            }
        )

    for item in misc_items:
        if not isinstance(item, dict):
            raise ValueError("misc instance entry must be an object")
        model_path = item.get("model_path")
        instance_name = item.get("instance_name")
        condition = item.get("visibility_condition")
        if not isinstance(model_path, str) or not isinstance(instance_name, str):
            raise ValueError("misc instance lacks model_path/instance_name")
        if condition == "always":
            visible = True
        elif condition == "dock":
            visible = dock_visible
        else:
            raise ValueError(
                f"unsupported misc visibility_condition: {condition!r}"
            )
        mounts.append(
            {
                "hardpoint": instance_name,
                "category": "Misc_Dock" if condition == "dock" else "Misc",
                "model_glb": _glb_for(model_path, glb_lookup),
                "matrix": _matrix(item),
                "visible": visible,
                "assembly_kind": "misc",
                "visibility_condition": condition,
                "source_model_path": model_path,
                "source_hull_model_path": item.get("source_hull_model_path"),
            }
        )

    for sequence, item in enumerate(overlay_items):
        if not isinstance(item, dict):
            raise ValueError("runtime overlay entry must be an object")
        model_path = item.get("model_path")
        parent_hardpoint = item.get("parent_hardpoint")
        if not isinstance(model_path, str) or not isinstance(
            parent_hardpoint, str
        ):
            raise ValueError(
                "runtime overlay lacks model_path/parent_hardpoint"
            )
        mounts.append(
            {
                "hardpoint": f"{parent_hardpoint}_AM5058_ACTION_{sequence + 1}",
                "category": "RuntimeOverlay",
                "model_glb": _glb_for(model_path, glb_lookup),
                "matrix": _matrix(item),
                "visible": overlay_visible,
                "assembly_kind": "runtime_overlay",
                "visibility_condition": "launch_action_state",
                "parent_hardpoint": parent_hardpoint,
                "source_model_path": model_path,
                "role": item.get("role"),
            }
        )

    _check_count("mounts", mounts)
    category_counts = {
        "combat": sum(item["assembly_kind"] == "combat" for item in mounts),
        "misc": sum(item["assembly_kind"] == "misc" for item in mounts),
        "runtime_overlay": sum(
            item["assembly_kind"] == "runtime_overlay" for item in mounts
        ),
    }
    default_visible = sum(item["visible"] for item in mounts)
    default_hidden = len(mounts) - default_visible

    if category_counts != {
        "combat": EXPECTED_COUNTS["combat"],
        "misc": EXPECTED_COUNTS["misc"],
        "runtime_overlay": EXPECTED_COUNTS["runtime_overlay"],
    }:
        raise AssertionError(f"unexpected category counts: {category_counts}")
    if visibility_profile == "harbor_dock":
        if default_visible != 27 or default_hidden != 2:
            raise AssertionError(
                "harbor_dock must have 27 visible mounts and 2 hidden overlays"
            )

    return {
        "schema": "wows-legends-blender-assembly-plan/v2",
        "profile_id": profile["profile_id"],
        "visibility_profile": visibility_profile,
        "ship_key": ship_key,
        "source_files": {
            "assembly": str(assembly_path),
            "assembly_sha256": actual_sha,
            "profile_manifest": str(profile_path),
            "batch_summary": str(batch_summary_path),
        },
        "counts": {
            "hull_glbs": len(hull_glbs),
            "mounts": len(mounts),
            **category_counts,
            "default_visible": default_visible,
            "default_hidden": default_hidden,
        },
        "hull_records": hull_records,
        "hull_glbs": hull_glbs,
        "mounts": mounts,
        "output_blend": output_blend,
        "output_glb": output_glb,
        "output_combined_obj": output_combined_obj,
        "validation_json": validation_json,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--profile-manifest", required=True, type=Path)
    parser.add_argument("--batch-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--visibility-profile",
        default="harbor_dock",
        choices=("harbor_dock", "neutral_battle_intact"),
    )
    parser.add_argument(
        "--output-blend", default="ticonderoga_1990_harbor.blend"
    )
    parser.add_argument("--output-glb", default="ticonderoga_1990_harbor.glb")
    parser.add_argument(
        "--output-combined-obj", default="Ticonderoga1990_Combined.obj"
    )
    parser.add_argument(
        "--validation-json",
        default="ticonderoga_1990_harbor.validation.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = build_plan(
        args.assembly,
        args.profile_manifest,
        args.batch_summary,
        visibility_profile=args.visibility_profile,
        output_blend=args.output_blend,
        output_glb=args.output_glb,
        output_combined_obj=args.output_combined_obj,
        validation_json=args.validation_json,
    )
    output = Path(os.path.abspath(str(args.output)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "profile_id": plan["profile_id"],
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
