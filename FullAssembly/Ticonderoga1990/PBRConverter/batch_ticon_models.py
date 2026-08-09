#!/usr/bin/env python3
"""Generate and convert the 20 unique Ticonderoga static-assembly models."""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Sequence


class BatchError(RuntimeError):
    pass


DAMAGE_RE = re.compile(r"(?:crack|patch|dead)", re.IGNORECASE)
TEXTURE_PROPERTY_CHANNELS = {
    "diffuseMap": "a",
    "normalMap": "n",
    "metallicGlossMap": "mg",
    "ambientOcclusionMap": "ao",
    "detailMap": "detail",
}


def resolve(path: Path, base: Path) -> Path:
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def default_decoder_root(here: Path) -> Path:
    candidates = [
        here.parent / "geometry_decoder",
        here.parents[2] / "BlenderExtractor" / "geometry_decoder",
    ]
    for candidate in candidates:
        if (candidate / "decode_geometry.py").is_file():
            return candidate
    return candidates[-1]




def used_models(mapping: dict) -> list[tuple[str, str]]:
    hull = [
        ("hull", item["path"])
        for item in mapping["hull_parts"]
        if item.get("role") == "mesh"
    ]
    combat = [
        ("combat", path)
        for path in sorted({item["model_path"] for item in mapping["combat_mounts"]})
    ]
    misc_paths = {item["model_path"] for item in mapping["misc_instances"]}
    misc_paths.update(item["model_path"] for item in mapping["runtime_action_overlays"])
    misc = [("misc", path) for path in sorted(misc_paths)]
    result = hull + combat + misc
    if len(hull) != 4 or len(combat) != 8 or len(misc) != 8:
        raise BatchError(
            f"expected 4 hull + 8 combat + 8 misc models, got "
            f"{len(hull)} + {len(combat)} + {len(misc)}"
        )
    if len(result) != len({path for _, path in result}):
        raise BatchError("used model list contains duplicates")
    return result


def make_manifest(
    category: str,
    model_path: str,
    model_record: dict,
    extracted_root: Path,
) -> dict:
    model_uber = model_record["model_uber"]
    visual_prototypes = model_uber["visual_prototypes"]
    lod0 = [item for item in visual_prototypes if item.get("lod_index") == 0]
    if len(lod0) != 1:
        raise BatchError(f"{model_path}: expected one LOD0 visual, got {len(lod0)}")
    visual = lod0[0]
    geometry_path = visual.get("derived_geometry_path")
    if not geometry_path:
        raise BatchError(f"{model_path}: LOD0 visual has no derived geometry path")
    geometry = (extracted_root / geometry_path).resolve()
    if not geometry.is_file():
        raise BatchError(f"{model_path}: extracted geometry missing: {geometry}")

    intact = [
        render_set
        for render_set in visual["render_sets"]
        if not DAMAGE_RE.search(render_set["vertices_section"])
    ]
    if not intact:
        raise BatchError(f"{model_path}: no intact LOD0 render sets")
    render_sets = []
    for render_set in intact:
        required = (
            "vertices_section",
            "indices_section",
            "material_mfm_path",
            "material_name",
        )
        missing = [key for key in required if not render_set.get(key)]
        if missing:
            raise BatchError(f"{model_path}: render set missing {missing}")
        prototype_index = render_set.get("material_prototype_index")
        prototypes = model_uber["material_prototypes"]
        if not isinstance(prototype_index, int) or not (0 <= prototype_index < len(prototypes)):
            raise BatchError(
                f"{model_path}: invalid material prototype index {prototype_index}"
            )
        prototype = prototypes[prototype_index]
        if prototype.get("mfm_path") != render_set["material_mfm_path"]:
            raise BatchError(
                f"{model_path}: render-set/prototype MFM mismatch at {prototype_index}"
            )
        texture_maps: dict[str, str] = {}
        for prop in prototype.get("properties", []):
            channel = TEXTURE_PROPERTY_CHANNELS.get(prop.get("name"))
            if channel is None or prop.get("type") != "texture":
                continue
            value = prop.get("value")
            logical_path = value.get("path") if isinstance(value, dict) else None
            if not logical_path:
                raise BatchError(
                    f"{model_path}: declared texture property {prop.get('name')} "
                    "has no logical path"
                )
            texture_maps[channel] = logical_path
        item = {key: render_set[key] for key in required}
        item["texture_maps"] = texture_maps
        item["material_fx_path"] = prototype.get("fx_path")
        item["material_properties"] = prototype.get("properties", [])
        render_sets.append(item)

    stem = Path(model_path).stem
    return {
        "schema": 1,
        "name": stem,
        "source": {
            "category": category,
            "model_path": model_path,
            "lod_index": 0,
            "mapping_render_sets": len(visual["render_sets"]),
            "intact_render_sets": len(render_sets),
            "damage_render_sets_excluded": len(visual["render_sets"]) - len(render_sets),
        },
        "texture_root": str(extracted_root),
        "models": [
            {
                "name": stem,
                "geometry": str(geometry),
                "render_sets": render_sets,
            }
        ],
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def validate_glb_container(path: Path) -> dict:
    with path.open("rb") as stream:
        header = stream.read(12)
    if len(header) != 12:
        return {"valid": False, "reason": "truncated header"}
    magic, version, declared_length = struct.unpack("<4sII", header)
    actual_length = path.stat().st_size
    valid = magic == b"glTF" and version == 2 and declared_length == actual_length
    return {
        "valid": valid,
        "magic": magic.decode("ascii", "replace"),
        "version": version,
        "declared_length": declared_length,
        "actual_length": actual_length,
    }


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--blender",
        type=Path,
        default=Path(r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe"),
    )
    parser.add_argument(
        "--decoder-root",
        type=Path,
        default=default_decoder_root(here),
    )
    parser.add_argument("--manifests-only", action="store_true")
    parser.add_argument("--reuse-existing", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mapping_path = args.mapping.resolve()
    extracted_root = args.extracted_root.resolve()
    output_root = args.output_root.resolve()
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    selected = used_models(mapping)
    output_root.mkdir(parents=True, exist_ok=True)
    converter = Path(__file__).with_name("convert.py").resolve()

    results = []
    required_texture_paths: set[str] = set()
    for category, model_path in selected:
        if model_path not in mapping["models"]:
            raise BatchError(f"model record missing from mapping: {model_path}")
        stem = Path(model_path).stem
        model_output = output_root / stem
        manifest = make_manifest(
            category, model_path, mapping["models"][model_path], extracted_root
        )
        manifest_path = model_output / f"{stem}.manifest.json"
        write_json(manifest_path, manifest)
        for model in manifest["models"]:
            for render_set in model["render_sets"]:
                required_texture_paths.update(render_set["texture_maps"].values())
        result = {
            "category": category,
            "model_path": model_path,
            "manifest": str(manifest_path),
            "mapping_render_sets": manifest["source"]["mapping_render_sets"],
            "intact_render_sets": manifest["source"]["intact_render_sets"],
            "damage_render_sets_excluded": manifest["source"][
                "damage_render_sets_excluded"
            ],
            "output_glb": str(model_output / f"{stem}.glb"),
            "validation": str(model_output / f"{stem}.validation.json"),
        }
        if args.manifests_only:
            result["status"] = "MANIFEST_ONLY"
            results.append(result)
            continue

        validation_path = Path(result["validation"])
        output_glb = Path(result["output_glb"])
        reuse = args.reuse_existing and validation_path.is_file() and output_glb.is_file()
        if not reuse:
            command = [
                sys.executable,
                str(converter),
                str(manifest_path),
                "--output-dir",
                str(model_output),
                "--name",
                stem,
                "--blender",
                str(args.blender.resolve()),
                "--decoder-root",
                str(args.decoder_root.resolve()),
            ]
            run = subprocess.run(command, text=True, capture_output=True)
            (model_output / f"{stem}.batch-convert.log").write_text(
                run.stdout + "\n--- STDERR ---\n" + run.stderr, encoding="utf-8"
            )
            if run.returncode != 0:
                result["status"] = "FAILED"
                result["error"] = f"converter exit {run.returncode}"
                results.append(result)
                continue

        if not validation_path.is_file() or not output_glb.is_file():
            result["status"] = "FAILED"
            result["error"] = "expected validation or GLB output missing"
            results.append(result)
            continue
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        acceptance = validation.get("acceptance", {})
        if (
            validation.get("status") != "OK"
            or not acceptance.get("passed")
            or acceptance.get("render_set_to_geometry_part_missing") != 0
            or not acceptance.get("material_policy_passed")
            or validation.get("render_set_count")
            != manifest["source"]["intact_render_sets"]
            or not validate_glb_container(output_glb)["valid"]
        ):
            result["status"] = "FAILED"
            result["error"] = "strict validation acceptance failed"
            results.append(result)
            continue
        result.update(
            {
                "status": "OK",
                "matched_render_sets": validation["matched_render_sets"],
                "missing_render_sets": validation["missing_render_sets"],
                "missing_maps": len(validation["missing_maps"]),
                "output_glb_bytes": output_glb.stat().st_size,
                "glb_container": validate_glb_container(output_glb),
                "material_count": len(validation.get("material_policy", [])),
                "material_policy_passed": acceptance.get(
                    "material_policy_passed", False
                ),
            }
        )
        results.append(result)

    required_report = {
        "schema": "wows-legends-required-textures/v1",
        "source_mapping": str(mapping_path),
        "extracted_root": str(extracted_root),
        "logical_paths": [
            {
                "path": logical_path,
                "present": (extracted_root / logical_path).is_file(),
            }
            for logical_path in sorted(required_texture_paths)
        ],
    }
    required_report["count"] = len(required_report["logical_paths"])
    required_report["missing"] = [
        item["path"] for item in required_report["logical_paths"] if not item["present"]
    ]
    required_path = output_root / "ticon_required_texture_paths.json"
    write_json(required_path, required_report)

    failures = [result for result in results if result["status"] == "FAILED"]
    summary = {
        "schema": "wows-legends-ticon-pbr-batch/v1",
        "source_mapping": str(mapping_path),
        "extracted_root": str(extracted_root),
        "output_root": str(output_root),
        "required_texture_paths": str(required_path),
        "required_texture_count": required_report["count"],
        "required_texture_missing": required_report["missing"],
        "proprietary_asset_policy": (
            "generated model outputs remain under work and must not be packaged"
        ),
        "expected_models": 20,
        "result_models": len(results),
        "categories": {
            category: sum(result["category"] == category for result in results)
            for category in ("hull", "combat", "misc")
        },
        "strict_validation": {
            "passed_models": sum(result["status"] == "OK" for result in results),
            "failed_models": len(failures),
            "total_intact_render_sets": sum(
                result["intact_render_sets"] for result in results
            ),
            "total_matched_render_sets": sum(
                result.get("matched_render_sets", 0) for result in results
            ),
            "total_missing_render_sets": sum(
                result.get("missing_render_sets", 0) for result in results
            ),
            "total_missing_maps": sum(
                result.get("missing_maps", 0) for result in results
            ),
            "total_materials": sum(
                result.get("material_count", 0) for result in results
            ),
            "material_policy_failed_models": sum(
                not result.get("material_policy_passed", False)
                for result in results
                if result.get("status") == "OK"
            ),
            "accepted": (
                not args.manifests_only
                and not failures
                and len(results) == 20
                and not required_report["missing"]
            ),
        },
        "results": results,
    }
    summary_path = output_root / "ticon_full_pbr_models.summary.json"
    write_json(summary_path, summary)
    print(json.dumps(summary["strict_validation"], indent=2))
    print(summary_path)
    if failures:
        raise BatchError(
            f"{len(failures)} model conversions failed: "
            + ", ".join(Path(item["model_path"]).stem for item in failures)
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BatchError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from None
