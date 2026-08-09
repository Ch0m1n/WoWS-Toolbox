#!/usr/bin/env python3
"""Asset-free pure-Python self-test for the Ticon scene-plan builder."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path, PurePosixPath

import build_ticon_scene_plan


HERE = Path(__file__).resolve().parent
PROFILE_SOURCE = HERE.parent / "Mapping" / "ticonderoga_1990_profile_manifest.json"
IDENTITY = {
    "column_major": [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]
}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _model_path(prefix: str, index: int) -> str:
    return f"content/synthetic/{prefix}{index}/{prefix}{index}.model"


def _synthetic_assembly() -> dict:
    hull = [
        {"role": "mesh", "path": _model_path("Hull", index)}
        for index in range(4)
    ]
    combat = [
        {
            "hardpoint": f"HP_COMBAT_{index:02d}",
            "category": "Synthetic",
            "component": f"Component{index:02d}",
            "model_path": _model_path("Combat", index % 8),
            "corrected_gltf_rh_y_up_matrix": IDENTITY,
        }
        for index in range(17)
    ]
    misc = [
        {
            "instance_name": f"MP_MISC_{index:02d}",
            "model_path": _model_path("Misc", index % 8),
            "visibility_condition": "dock" if index >= 8 else "always",
            "corrected_gltf_rh_y_up_matrix": IDENTITY,
        }
        for index in range(10)
    ]
    overlays = [
        {
            "parent_hardpoint": f"HP_COMBAT_{index:02d}",
            "model_path": _model_path("Misc", 7),
            "role": "synthetic_action_overlay",
            "corrected_gltf_rh_y_up_matrix": IDENTITY,
        }
        for index in range(2)
    ]
    return {
        "ship": {"ship_key": "PXSD307_Ticonderoga_1990"},
        "hull_parts": hull,
        "combat_mounts": combat,
        "misc_instances": misc,
        "runtime_action_overlays": overlays,
    }


def _model_paths(assembly: dict) -> list[tuple[str, str]]:
    pairs = [
        ("hull", item["path"])
        for item in assembly["hull_parts"]
        if item["role"] == "mesh"
    ]
    pairs.extend(
        ("combat", item["model_path"])
        for item in assembly["combat_mounts"]
    )
    pairs.extend(
        ("misc", item["model_path"])
        for item in assembly["misc_instances"]
    )
    pairs.extend(
        ("misc", item["model_path"])
        for item in assembly["runtime_action_overlays"]
    )
    unique: dict[str, tuple[str, str]] = {}
    for category, model_path in pairs:
        unique[PurePosixPath(model_path).stem] = (category, model_path)
    return [unique[stem] for stem in sorted(unique)]


def main() -> int:
    profile = json.loads(PROFILE_SOURCE.read_text(encoding="utf-8-sig"))
    assembly = _synthetic_assembly()
    with tempfile.TemporaryDirectory(prefix="ticon_scene_plan_") as temp_value:
        temp_dir = Path(temp_value)
        assembly_path = temp_dir / "synthetic_assembly.json"
        _write_json(assembly_path, assembly)
        profile["canonical_output"]["sha256"] = _sha256(assembly_path)
        profile_path = temp_dir / "synthetic_profile.json"
        _write_json(profile_path, profile)

        results = []
        for category, model_path in _model_paths(assembly):
            stem = PurePosixPath(model_path).stem
            output_glb = temp_dir / f"{stem}.glb"
            output_glb.write_bytes(b"synthetic-plan-test")
            results.append(
                {
                    "category": category,
                    "model_path": model_path,
                    "output_glb": str(output_glb),
                    "status": "OK",
                }
            )
        assert len(results) == 20
        summary_path = temp_dir / "batch.summary.json"
        _write_json(
            summary_path,
            {
                "schema": "synthetic-ticon-scene-plan-test/v1",
                "strict_validation": {"accepted": True},
                "results": results,
            },
        )

        harbor = build_ticon_scene_plan.build_plan(
            assembly_path,
            profile_path,
            summary_path,
            visibility_profile="harbor_dock",
        )
        assert harbor["output_combined_obj"] == "Ticonderoga1990_Combined.obj"
        assert harbor["counts"] == {
            "hull_glbs": 4,
            "mounts": 29,
            "combat": 17,
            "misc": 10,
            "runtime_overlay": 2,
            "default_visible": 27,
            "default_hidden": 2,
        }
        assert len(harbor["hull_glbs"]) == 4
        assert len(harbor["mounts"]) == 29

        neutral = build_ticon_scene_plan.build_plan(
            assembly_path,
            profile_path,
            summary_path,
            visibility_profile="neutral_battle_intact",
        )
        assert neutral["counts"]["default_visible"] == 25
        assert neutral["counts"]["default_hidden"] == 4

    print(
        json.dumps(
            {
                "ok": True,
                "asset_free": True,
                "harbor_counts": harbor["counts"],
                "neutral_counts": neutral["counts"],
                "unique_batch_models": len(results),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
