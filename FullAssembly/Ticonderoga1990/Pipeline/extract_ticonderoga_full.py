#!/usr/bin/env python3
"""Run the verified Ticonderoga 1990 extraction and Blender assembly profile.

This is deliberately *not* a generic ship-complete exporter.  It accepts only
the bundled, versioned Ticonderoga profile.  The default mode scans the local
IDX files and prints an exact CRC-bearing plan without writing anything.
``--execute`` is required before extraction or generated outputs are written.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


HERE = Path(__file__).resolve().parent
PROFILE_ROOT = HERE.parent
FULL_ASSEMBLY_ROOT = PROFILE_ROOT.parent
TOOLBOX_ROOT = FULL_ASSEMBLY_ROOT.parent
NATIVE_EXTRACTOR = (
    TOOLBOX_ROOT / "BlenderExtractor" / "blender_extractor"
)
GEOMETRY_DECODER = (
    TOOLBOX_ROOT / "BlenderExtractor" / "geometry_decoder"
)

if str(NATIVE_EXTRACTOR) not in sys.path:
    sys.path.insert(0, str(NATIVE_EXTRACTOR))

from legends_assets.core import (  # noqa: E402
    AssetEntry,
    ExtractionError,
    extract_asset,
    iter_assets,
    normalize_virtual_path,
    write_manifest,
)


RESOURCE_PROFILE = (
    PROFILE_ROOT / "Profiles" / "ticonderoga_1990_resources.json"
)
ASSEMBLY_PROFILE = (
    PROFILE_ROOT / "Mapping" / "ticonderoga_1990_profile_manifest.json"
)
MAPPING_RUNNER = (
    PROFILE_ROOT / "Mapping" / "build_ticonderoga_assembly_v2.py"
)
PBR_BATCH = PROFILE_ROOT / "PBRConverter" / "batch_ticon_models.py"
PLAN_BUILDER = (
    PROFILE_ROOT / "BlenderSceneAssembler" / "build_ticon_scene_plan.py"
)
SCENE_ASSEMBLER = (
    PROFILE_ROOT / "BlenderSceneAssembler" / "assemble_scene.py"
)
EXPECTED_SYSTEM_PATHS = {
    "content/assets.bin",
    "content/GameParams.data",
    "content/prototypes.index.data",
    "content/prototypes.data",
}
PROFILE_ID = "ticonderoga_1990_verified_profile"
EXPECTED_GEOMETRY_COUNT = 20


class PipelineError(RuntimeError):
    """A safety, profile, extraction, or downstream acceptance check failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"JSON root must be an object: {path}")
    return value


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _initialize_execution_manifest(
    run_root: Path,
    *,
    game_dir: Path,
    overwrite: bool,
    visibility_profile: str,
) -> Path:
    """Fail closed before an executing run can replace any artifacts.

    Removing the exact old final manifest is the first overwrite mutation. If
    the process is interrupted before the atomic in-progress write completes,
    the final manifest is absent rather than retaining stale passed=true state.
    """
    manifest = run_root / "Ticonderoga1990.pipeline.json"
    previous_invalidated = False
    if overwrite and (manifest.exists() or manifest.is_symlink()):
        manifest.unlink()
        previous_invalidated = True
    run_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        manifest,
        {
            "schema": "wows-legends-ticonderoga-full-run/v1",
            "mode": "executing",
            "status": "in_progress",
            "profile_id": PROFILE_ID,
            "game_dir": str(game_dir),
            "run_root": str(run_root),
            "overwrite": bool(overwrite),
            "visibility_profile": visibility_profile,
            "previous_final_manifest_invalidated": previous_invalidated,
            "acceptance": {"passed": False},
        },
    )
    return manifest


def _is_inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_output_location(game_dir: Path, output_root: Path) -> None:
    game = game_dir.resolve()
    output = output_root.resolve()
    if output == game or _is_inside(output, game) or _is_inside(game, output):
        raise PipelineError(
            "output and game directories must be disjoint; refusing "
            f"game={game}, output={output}"
        )
    if output == Path(output.anchor):
        raise PipelineError(f"output cannot be a drive root: {output}")


def validate_resource_profile(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    if profile.get("schema") != (
        "wows-legends-ticonderoga-1990-resource-profile/v1"
    ):
        raise PipelineError("unexpected resource profile schema")
    if profile.get("profile_id") != PROFILE_ID:
        raise PipelineError("resource profile is not the verified Ticon profile")
    if profile.get("generic_ship_complete") is not False:
        raise PipelineError(
            "resource profile must explicitly reject generic completeness"
        )
    resources = profile.get("resources")
    if not isinstance(resources, list):
        raise PipelineError("resource profile resources must be an array")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts: defaultdict[str, int] = defaultdict(int)
    for index, raw in enumerate(resources):
        if not isinstance(raw, dict):
            raise PipelineError(f"resource {index} is not an object")
        kind = raw.get("kind")
        path = raw.get("path")
        if kind not in {"system_sidecar", "geometry", "texture"}:
            raise PipelineError(f"resource {index} has invalid kind {kind!r}")
        if not isinstance(path, str):
            raise PipelineError(f"resource {index} path is not a string")
        try:
            clean = normalize_virtual_path(path)
        except ValueError as exc:
            raise PipelineError(f"resource {index}: {exc}") from exc
        folded = clean.casefold()
        if folded in seen:
            raise PipelineError(f"duplicate resource path: {clean}")
        seen.add(folded)
        counts[kind] += 1
        item = dict(raw)
        item["path"] = clean
        normalized.append(item)

    system = {
        item["path"]
        for item in normalized
        if item["kind"] == "system_sidecar"
    }
    if system != EXPECTED_SYSTEM_PATHS:
        raise PipelineError(
            f"system sidecar set differs from verified set: {sorted(system)}"
        )
    if counts["geometry"] != EXPECTED_GEOMETRY_COUNT:
        raise PipelineError(
            f"expected {EXPECTED_GEOMETRY_COUNT} LOD0 geometries, "
            f"got {counts['geometry']}"
        )
    if counts["texture"] <= 0:
        raise PipelineError("profile declares no textures")
    for item in normalized:
        suffix = PurePosixPath(item["path"]).suffix.casefold()
        expected = {
            "geometry": ".geometry",
            "texture": ".dds",
        }.get(item["kind"])
        if expected and suffix != expected:
            raise PipelineError(
                f"{item['kind']} path has wrong extension: {item['path']}"
            )

    declared = profile.get("expected_counts")
    actual_counts = {
        "system_sidecars": counts["system_sidecar"],
        "lod0_geometry": counts["geometry"],
        "declared_textures": counts["texture"],
        "total": len(normalized),
    }
    if not isinstance(declared, dict) or any(
        declared.get(key) != value for key, value in actual_counts.items()
    ):
        raise PipelineError(
            f"resource expected_counts mismatch: declared={declared}, "
            f"actual={actual_counts}"
        )
    return normalized


def locate_exact_resources(
    game_dir: Path, resources: Sequence[Mapping[str, Any]]
) -> list[tuple[Mapping[str, Any], AssetEntry]]:
    wanted = {str(item["path"]).casefold(): item for item in resources}
    matches: defaultdict[str, list[AssetEntry]] = defaultdict(list)
    scan_errors: list[str] = []
    try:
        for entry in iter_assets(
            game_dir, skip_errors=False, errors=scan_errors
        ):
            folded = entry.virtual_path.casefold()
            if folded in wanted:
                matches[folded].append(entry)
    except (OSError, ValueError) as exc:
        raise PipelineError(f"IDX scan failed: {exc}") from exc
    if scan_errors:
        raise PipelineError("IDX scan reported errors: " + "; ".join(scan_errors))

    missing = [
        item["path"]
        for folded, item in wanted.items()
        if not matches.get(folded)
    ]
    if missing:
        raise PipelineError(
            f"{len(missing)} verified-profile resources are missing: "
            + ", ".join(missing[:12])
        )

    selected: list[tuple[Mapping[str, Any], AssetEntry]] = []
    for folded, definition in wanted.items():
        candidates = matches[folded]
        if len(candidates) != 1:
            details = ", ".join(
                f"{entry.idx_path.name}/"
                f"{entry.package_path.name}@0x{entry.file_info.offset:X}"
                for entry in candidates
            )
            raise PipelineError(
                f"ambiguous virtual resource {definition['path']!r}: {details}"
            )
        entry = candidates[0]
        compression = (
            entry.file_info.compression_type_1,
            entry.file_info.compression_type_2,
        )
        if compression != (5, 1):
            raise PipelineError(
                f"unverified storage {compression} for {entry.virtual_path}"
            )
        selected.append((definition, entry))
    selected.sort(
        key=lambda pair: (
            str(pair[0]["kind"]),
            pair[1].virtual_path.casefold(),
        )
    )
    return selected


def planned_resource(
    definition: Mapping[str, Any], entry: AssetEntry, extracted_root: Path
) -> dict[str, Any]:
    target = extracted_root.joinpath(
        *PurePosixPath(entry.virtual_path).parts
    ).resolve()
    return {
        "kind": definition["kind"],
        "path": entry.virtual_path,
        "source_index": entry.idx_path.name,
        "source_package": entry.package_path.name,
        "packed_size": entry.file_info.packed_size,
        "unpacked_size": entry.file_info.unpacked_size,
        "crc32": f"{entry.file_info.crc32:08X}",
        "compression": [
            entry.file_info.compression_type_1,
            entry.file_info.compression_type_2,
        ],
        "target": str(target),
    }


def command_plan(
    python: Path,
    blender: Path,
    run_root: Path,
    visibility_profile: str,
) -> list[dict[str, Any]]:
    extracted = run_root / "extracted"
    generated = run_root / "generated"
    pbr = run_root / "pbr"
    scene = run_root / "scene"
    mapping = generated / "ticonderoga_1990_static_assembly.json"
    batch_summary = pbr / "ticon_full_pbr_models.summary.json"
    plan = generated / "ticonderoga_1990_scene_plan.json"
    return [
        {
            "step": "CRC extraction",
            "writes": str(extracted),
            "execute_opt_in": True,
        },
        {
            "step": "Ticon mapping v2",
            "command": [
                str(python),
                "-B",
                str(MAPPING_RUNNER),
                "--game-params",
                str(extracted / "content" / "GameParams.data"),
                "--assets",
                str(extracted / "content" / "assets.bin"),
                "--prototype-index",
                str(extracted / "content" / "prototypes.index.data"),
                "--prototype-data",
                str(extracted / "content" / "prototypes.data"),
                "--output",
                str(mapping),
                "--acceptance",
                str(generated / "mapping_acceptance.md"),
            ],
        },
        {
            "step": "20-model explicit PBR batch",
            "command": [
                str(python),
                "-B",
                str(PBR_BATCH),
                "--mapping",
                str(mapping),
                "--extracted-root",
                str(extracted),
                "--output-root",
                str(pbr),
                "--blender",
                str(blender),
                "--decoder-root",
                str(GEOMETRY_DECODER),
            ],
        },
        {
            "step": "Ticon scene plan",
            "command": [
                str(python),
                "-B",
                str(PLAN_BUILDER),
                "--assembly",
                str(mapping),
                "--profile-manifest",
                str(ASSEMBLY_PROFILE),
                "--batch-summary",
                str(batch_summary),
                "--visibility-profile",
                visibility_profile,
                "--output",
                str(plan),
            ],
        },
        {
            "step": "Blender scene assembly",
            "command": [
                str(blender),
                "--background",
                "--factory-startup",
                "--python",
                str(SCENE_ASSEMBLER),
                "--",
                "--plan",
                str(plan),
                "--output",
                str(scene / "Ticonderoga1990.blend"),
                "--glb",
                str(scene / "Ticonderoga1990.glb"),
                "--obj",
                str(scene / "Ticonderoga1990_Combined.obj"),
                "--validation",
                str(scene / "Ticonderoga1990.validation.json"),
            ],
        },
    ]


def run_checked(
    label: str,
    command: Sequence[str],
    *,
    log_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    child_environment = os.environ.copy()
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        list(command),
        check=False,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child_environment,
    )
    elapsed = time.monotonic() - started
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(
        character if character.isalnum() else "_"
        for character in label
    ).strip("_")
    log = log_dir / f"{safe_label}.log"
    log.write_text(completed.stdout, encoding="utf-8")
    result = {
        "label": label,
        "command": list(command),
        "returncode": completed.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "log": str(log),
    }
    if completed.returncode != 0:
        raise PipelineError(
            f"{label} failed with exit {completed.returncode}; see {log}"
        )
    return result


def _mapping_acceptance(path: Path) -> dict[str, Any]:
    mapping = load_object(path)
    profile = load_object(ASSEMBLY_PROFILE)
    canonical = profile.get("canonical_output")
    if not isinstance(canonical, dict):
        raise PipelineError("assembly profile has no canonical_output")
    expected_sha = str(canonical.get("sha256", "")).upper()
    actual_sha = sha256_file(path)
    if not expected_sha or actual_sha != expected_sha:
        raise PipelineError(
            "mapping canonical SHA-256 mismatch: "
            f"expected {expected_sha or '<missing>'}, got {actual_sha}"
        )
    if mapping.get("schema") != "wows-legends-static-ship-assembly/v1":
        raise PipelineError("mapping schema is not the verified assembly schema")

    expected_sources = {
        "GameParams.data": "content/GameParams.data",
        "assets.bin": "content/assets.bin",
        "prototypes.index.data": "content/prototypes.index.data",
        "prototypes.data": "content/prototypes.data",
    }
    sources = mapping.get("source_files")
    if not isinstance(sources, dict) or set(sources) != set(expected_sources):
        raise PipelineError("mapping source file set differs from verified set")
    for label, logical_source in expected_sources.items():
        record = sources[label]
        if not isinstance(record, dict):
            raise PipelineError(f"mapping source record is invalid: {label}")
        if "workspace_copy" in record:
            raise PipelineError(f"mapping embeds a machine-local path: {label}")
        if record.get("logical_source") != logical_source:
            raise PipelineError(f"mapping logical source mismatch: {label}")
        digest = record.get("sha256")
        try:
            digest_valid = isinstance(digest, str) and len(digest) == 64
            if digest_valid:
                int(digest, 16)
        except ValueError:
            digest_valid = False
        if not digest_valid or not isinstance(record.get("size"), int):
            raise PipelineError(f"mapping source content identity is invalid: {label}")

    validation = mapping.get("validation")
    if not isinstance(validation, dict):
        raise PipelineError("mapping has no validation object")
    required = {
        "resolved_combat_hardpoints": 17,
        "hull_part_models": 10,
        "render_sets_parsed": 194,
        "texture_properties_parsed": 155,
    }
    for key, expected in required.items():
        if validation.get(key) != expected:
            raise PipelineError(
                f"mapping {key}: expected {expected}, got {validation.get(key)}"
            )
    if validation.get("static_assembly_acceptance") is not True:
        raise PipelineError("mapping static assembly acceptance is not true")
    if validation.get("unresolved_render_set_fields") != []:
        raise PipelineError("mapping contains unresolved render-set fields")
    if validation.get("unresolved_texture_paths") != []:
        raise PipelineError("mapping contains unresolved texture paths")
    return {"canonical_sha256": actual_sha, **validation}


def _pbr_acceptance(path: Path) -> dict[str, Any]:
    summary = load_object(path)
    strict = summary.get("strict_validation")
    if not isinstance(strict, dict) or strict.get("accepted") is not True:
        raise PipelineError("PBR batch strict validation did not pass")
    if summary.get("expected_models") != 20:
        raise PipelineError("PBR batch expected_models is not 20")
    if summary.get("result_models") != 20:
        raise PipelineError("PBR batch result_models is not 20")
    if summary.get("required_texture_missing") != []:
        raise PipelineError("PBR batch reports missing declared textures")
    return strict


def _combined_obj_acceptance(scene_validation_path: Path) -> dict[str, Any]:
    validation = load_object(scene_validation_path)
    if validation.get("ok") is not True:
        raise PipelineError("Blender scene validation did not pass")
    combined = validation.get("combined_obj")
    if not isinstance(combined, dict) or combined.get("ok") is not True:
        raise PipelineError("combined visible-state OBJ validation did not pass")
    checks = combined.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise PipelineError("combined OBJ contract checks are incomplete or failed")
    preservation = combined.get("original_blend_preservation")
    if not isinstance(preservation, dict) or preservation.get("ok") is not True:
        raise PipelineError("original BLEND hidden-state preservation failed")

    obj_path = Path(str(combined.get("obj", ""))).resolve()
    mtl_path = Path(str(combined.get("mtl", ""))).resolve()
    texture_dir = Path(str(combined.get("texture_directory", ""))).resolve()
    if not obj_path.is_file() or not mtl_path.is_file() or not texture_dir.is_dir():
        raise PipelineError("combined OBJ/MTL/textures output is missing")
    if combined.get("mtllib") != mtl_path.name:
        raise PipelineError("OBJ mtllib is not a relative MTL basename")
    if combined.get("unified_mesh_objects") != 1:
        raise PipelineError("combined OBJ is not one unified mesh object")
    if int(combined.get("vertices", 0)) <= 0 or int(combined.get("faces", 0)) <= 0:
        raise PipelineError("combined OBJ has no vertex/face geometry")
    if int(combined.get("uv_records", 0)) <= 0:
        raise PipelineError("combined OBJ has no UV records")
    if int(combined.get("normal_records", 0)) <= 0:
        raise PipelineError("combined OBJ has no normal records")
    material_slots = int(combined.get("material_slots", 0))
    expected_materials = int(combined.get("expected_materials", 0))
    used_materials = combined.get("usemtl")
    mtl_materials = combined.get("mtl_materials")
    clean_reimport = combined.get("clean_reimport")
    clean_materials = (
        int(clean_reimport.get("materials", 0))
        if isinstance(clean_reimport, dict)
        else 0
    )
    if (
        material_slots <= 0
        or material_slots != expected_materials
        or not isinstance(used_materials, list)
        or material_slots != len(set(used_materials))
        or not isinstance(mtl_materials, list)
        or material_slots != len(set(mtl_materials))
        or material_slots != clean_materials
    ):
        raise PipelineError(
            "combined OBJ material assignments were not preserved exactly"
        )

    map_references = combined.get("map_references")
    texture_files = combined.get("texture_files")
    if not isinstance(map_references, list) or not map_references:
        raise PipelineError("combined MTL has no map_* references")
    if not isinstance(texture_files, list) or not texture_files:
        raise PipelineError("combined OBJ texture namespace is empty")
    for reference in map_references:
        if (
            not isinstance(reference, str)
            or not reference.startswith("textures/")
            or not reference.endswith(".png")
            or "\\" in reference
            or ".." in reference.split("/")
            or Path(reference).is_absolute()
        ):
            raise PipelineError(f"non-portable MTL texture reference: {reference!r}")
        if not (mtl_path.parent / Path(reference.replace("/", os.sep))).is_file():
            raise PipelineError(f"missing referenced OBJ texture: {reference}")
    if len({Path(item).name.casefold() for item in texture_files}) != len(texture_files):
        raise PipelineError("combined OBJ texture filename collision")

    mounts = validation.get("mounts")
    if not isinstance(mounts, dict):
        raise PipelineError("scene validation mount summary is missing")
    hidden = combined.get("excluded_hidden_mounts")
    visible = combined.get("included_visible_mounts")
    if not isinstance(hidden, list) or len(hidden) != mounts.get("default_hidden"):
        raise PipelineError("combined OBJ hidden-mount exclusion count mismatch")
    if not isinstance(visible, list) or len(visible) != mounts.get("default_visible"):
        raise PipelineError("combined OBJ visible-mount inclusion count mismatch")
    if combined.get("hidden_names_present_in_obj_or_mtl") != []:
        raise PipelineError("hidden overlay names leaked into OBJ/MTL")

    texture_hashes = []
    for relative in sorted(set(map_references)):
        path = mtl_path.parent / Path(relative.replace("/", os.sep))
        texture_hashes.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "accepted": True,
        "obj": {
            "path": str(obj_path),
            "bytes": obj_path.stat().st_size,
            "sha256": sha256_file(obj_path),
        },
        "mtl": {
            "path": str(mtl_path),
            "bytes": mtl_path.stat().st_size,
            "sha256": sha256_file(mtl_path),
        },
        "textures": texture_hashes,
        "vertices": combined["vertices"],
        "faces": combined["faces"],
        "uv_records": combined["uv_records"],
        "normal_records": combined["normal_records"],
        "material_slots": combined["material_slots"],
        "included_visible_mounts": len(visible),
        "excluded_hidden_mounts": len(hidden),
        "bounds_max_delta": combined.get("obj_bounds_max_delta"),
        "clean_reimport": combined.get("clean_reimport"),
        "limitations": combined.get("limitations", []),
    }


def execute_pipeline(
    args: argparse.Namespace,
    resources: Sequence[Mapping[str, Any]],
    located: Sequence[tuple[Mapping[str, Any], AssetEntry]],
) -> dict[str, Any]:
    game_dir = args.game_dir.resolve()
    output_root = args.output_root.resolve()
    run_root = output_root / "Ticonderoga1990_Verified"
    extracted = run_root / "extracted"
    generated = run_root / "generated"
    pbr = run_root / "pbr"
    scene = run_root / "scene"
    logs = run_root / "logs"
    if run_root.exists() and not args.overwrite:
        raise PipelineError(
            f"run output already exists; use --overwrite explicitly: {run_root}"
        )
    if not args.blender.is_file():
        raise PipelineError(f"Blender executable not found: {args.blender}")
    manifest = _initialize_execution_manifest(
        run_root,
        game_dir=game_dir,
        overwrite=bool(args.overwrite),
        visibility_profile=args.visibility_profile,
    )

    results = []
    for _, entry in located:
        results.append(
            extract_asset(
                entry,
                extracted,
                execute=True,
                overwrite=args.overwrite,
                max_unpacked_size=args.max_single_mib * 1024 * 1024,
            )
        )
    extraction_manifest = write_manifest(
        results, extracted, "verified_profile_extraction.json"
    )
    crc_results = []
    for result in results:
        actual = binascii.crc32(result.target.read_bytes()) & 0xFFFFFFFF
        expected = result.entry.file_info.crc32
        if actual != expected:
            raise PipelineError(
                f"post-write CRC mismatch for {result.entry.virtual_path}: "
                f"{actual:08X} != {expected:08X}"
            )
        crc_results.append(
            {
                "path": result.entry.virtual_path,
                "crc32": f"{actual:08X}",
                "sha256": sha256_file(result.target),
                "bytes": result.target.stat().st_size,
            }
        )

    plan = command_plan(
        args.python.resolve(),
        args.blender.resolve(),
        run_root,
        args.visibility_profile,
    )
    mapping_path = generated / "ticonderoga_1990_static_assembly.json"
    batch_summary = pbr / "ticon_full_pbr_models.summary.json"
    scene_validation = scene / "Ticonderoga1990.validation.json"
    output_blend = scene / "Ticonderoga1990.blend"
    output_glb = scene / "Ticonderoga1990.glb"

    step_results = []
    mapping_step = plan[1]
    step_results.append(
        run_checked(
            mapping_step["step"],
            mapping_step["command"],
            log_dir=logs,
            timeout=900,
        )
    )
    mapping_validation = _mapping_acceptance(mapping_path)

    pbr_step = plan[2]
    step_results.append(
        run_checked(
            pbr_step["step"],
            pbr_step["command"],
            log_dir=logs,
            timeout=1800,
        )
    )
    pbr_validation = _pbr_acceptance(batch_summary)

    for item in plan[3:]:
        step_results.append(
            run_checked(
                item["step"],
                item["command"],
                log_dir=logs,
                timeout=900,
            )
        )
    if not scene_validation.is_file():
        raise PipelineError("Blender scene validation JSON is missing")
    if not output_blend.is_file() or not output_glb.is_file():
        raise PipelineError("assembled Blender/GLB output is missing")
    combined_obj_validation = _combined_obj_acceptance(scene_validation)

    payload = {
        "schema": "wows-legends-ticonderoga-full-run/v1",
        "mode": "executed",
        "status": "completed",
        "profile_id": PROFILE_ID,
        "generic_ship_complete": False,
        "game_dir": str(game_dir),
        "writes_game_directory": False,
        "run_root": str(run_root),
        "overwrite": bool(args.overwrite),
        "resource_profile": {
            "path": str(RESOURCE_PROFILE),
            "sha256": sha256_file(RESOURCE_PROFILE),
            "resource_count": len(resources),
        },
        "extraction_manifest": str(extraction_manifest),
        "crc_verified_resources": crc_results,
        "steps": step_results,
        "acceptance": {
            "mapping": mapping_validation,
            "pbr": pbr_validation,
            "scene_validation": str(scene_validation),
            "assembled_blend": {
                "path": str(output_blend),
                "bytes": output_blend.stat().st_size,
                "sha256": sha256_file(output_blend),
            },
            "assembled_glb": {
                "path": str(output_glb),
                "bytes": output_glb.stat().st_size,
                "sha256": sha256_file(output_glb),
            },
            "combined_visible_obj": combined_obj_validation,
            "passed": combined_obj_validation["accepted"],
        },
        "limitations": [
            "Verified only for the bundled Ticonderoga 1990 profile.",
            "This does not establish generic complete-ship support.",
            "ANCA streamed keys and standalone .anim remain incomplete.",
            "OBJ/MTL is a visible-state interoperability export and cannot preserve "
            "the full Principled/PBR shader graph or runtime animation semantics.",
        ],
    }
    payload["pipeline_manifest"] = str(manifest)
    write_json_atomic(manifest, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--python", type=Path, default=Path(sys.executable)
    )
    parser.add_argument(
        "--blender",
        type=Path,
        default=Path(
            r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe"
        ),
    )
    parser.add_argument(
        "--visibility-profile",
        choices=("harbor_dock", "neutral_battle_intact"),
        default="harbor_dock",
    )
    parser.add_argument("--max-single-mib", type=int, default=512)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        game_dir = args.game_dir.resolve()
        output_root = args.output_root.resolve()
        if not (game_dir / "res_packages").is_dir():
            raise PipelineError(f"res_packages not found under {game_dir}")
        validate_output_location(game_dir, output_root)
        profile = load_object(RESOURCE_PROFILE)
        resources = validate_resource_profile(profile)
        located = locate_exact_resources(game_dir, resources)
        run_root = output_root / "Ticonderoga1990_Verified"
        plan = {
            "schema": "wows-legends-ticonderoga-full-plan/v1",
            "mode": "execute" if args.execute else "dry-run",
            "profile_id": PROFILE_ID,
            "profile_status": "Ticonderoga-specific verified profile",
            "generic_ship_complete": False,
            "game_dir": str(game_dir),
            "writes_game_directory": False,
            "output_root": str(output_root),
            "run_root": str(run_root),
            "overwrite": bool(args.overwrite),
            "resource_profile": {
                "path": str(RESOURCE_PROFILE),
                "sha256": sha256_file(RESOURCE_PROFILE),
                "expected_counts": profile["expected_counts"],
            },
            "resources": [
                planned_resource(definition, entry, run_root / "extracted")
                for definition, entry in located
            ],
            "commands": command_plan(
                args.python.resolve(),
                args.blender.resolve(),
                run_root,
                args.visibility_profile,
            ),
            "safety": {
                "dry_run_default": True,
                "execute_requires_opt_in": True,
                "game_directory_read_only": True,
                "output_disjoint_from_game": True,
                "overwrite_requires_opt_in": True,
            },
            "limitations": profile["limitations"],
        }
        if not args.execute:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        result = execute_pipeline(args, resources, located)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        PipelineError,
        ExtractionError,
        FileNotFoundError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
