#!/usr/bin/env python3
"""Extract and assemble one selected Legends ship with its static equipment.

This pipeline resolves the chosen ship through GameParams.data and assets.bin,
then extracts only the LOD0 geometry and texture resources referenced by that
resolved mapping.  The game installation is read-only.  Actual output writes
require ``--execute`` and an existing run is never reused unless
``--overwrite`` is explicit.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = Path(__file__).resolve().parent
SELECTED_ROOT = HERE.parent
FULL_ASSEMBLY_ROOT = SELECTED_ROOT.parent
TOOLBOX_ROOT = FULL_ASSEMBLY_ROOT.parent
TICON_ROOT = FULL_ASSEMBLY_ROOT / "Ticonderoga1990"
NATIVE_EXTRACTOR = TOOLBOX_ROOT / "BlenderExtractor" / "blender_extractor"
GEOMETRY_DECODER = TOOLBOX_ROOT / "BlenderExtractor" / "geometry_decoder"
MAPPING_RUNNER = (
    TICON_ROOT / "Mapping" / "build_selected_ship_assembly.py"
)
RESOURCE_BUILDER = HERE / "build_resource_profile.py"
PBR_BATCH = (
    SELECTED_ROOT / "PBRConverter" / "batch_selected_ship_models.py"
)
NATIVE_PBR_BATCH = (
    SELECTED_ROOT / "PBRConverter" / "native_prepare_selected_ship_models.py"
)
NATIVE_SCENE_ASSEMBLER = (
    SELECTED_ROOT / "BlenderSceneAssembler" / "native_obj_assembler.py"
)
PLAN_BUILDER = (
    SELECTED_ROOT
    / "BlenderSceneAssembler"
    / "build_selected_ship_scene_plan.py"
)
SCENE_ASSEMBLER = (
    TICON_ROOT / "BlenderSceneAssembler" / "assemble_scene.py"
)
SYSTEM_PATHS = (
    "content/assets.bin",
    "content/GameParams.data",
    "content/prototypes.index.data",
    "content/prototypes.data",
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


class PipelineError(RuntimeError):
    """A safety, discovery, conversion, or acceptance check failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def file_checksums(path: Path) -> tuple[int, str]:
    crc = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            crc = binascii.crc32(chunk, crc)
            digest.update(chunk)
    return crc & 0xFFFFFFFF, digest.hexdigest().upper()

def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
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


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _remove_pipeline_directory(path: Path, run_root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved.parent != run_root.resolve():
        raise PipelineError(f"refusing cleanup outside run root: {resolved}")
    if not resolved.is_dir():
        return {"path": str(resolved), "files": 0, "bytes": 0}
    files = [item for item in resolved.rglob("*") if item.is_file()]
    stats = {
        "path": str(resolved),
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
    }
    shutil.rmtree(resolved)
    return stats


def validate_output_location(game_dir: Path, output_root: Path) -> None:
    game = game_dir.resolve()
    output = output_root.resolve()
    if output == Path(output.anchor):
        raise PipelineError(f"output cannot be a drive root: {output}")
    if output == game or _inside(output, game) or _inside(game, output):
        raise PipelineError(
            "output and game directories must be disjoint; refusing "
            f"game={game}, output={output}"
        )


def validate_run_root(output_root: Path, run_root: Path) -> Path:
    lexical_output = Path(os.path.abspath(output_root))
    requested = Path(os.path.abspath(run_root))
    if os.path.normcase(str(requested.parent)) != os.path.normcase(
        str(lexical_output)
    ):
        raise PipelineError(f"unsafe run output target: {run_root}")

    output = output_root.resolve()
    expected = output / requested.name
    resolved = run_root.resolve(strict=False)
    if resolved != expected or resolved.parent != output:
        raise PipelineError(
            f"run output is a link or junction outside the output root: {run_root}"
        )
    return resolved


def safe_slug(ship_key: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", ship_key).strip("._-")
    if not value or value in {".", ".."}:
        raise PipelineError(f"ship key cannot form a safe output name: {ship_key!r}")
    return value


def validate_resource_definitions(
    resources: Any,
) -> list[dict[str, Any]]:
    if not isinstance(resources, list):
        raise PipelineError("resource profile resources must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(resources):
        if not isinstance(raw, dict):
            raise PipelineError(f"resource {index} is not an object")
        kind = raw.get("kind")
        if kind not in {"system_sidecar", "geometry", "texture"}:
            raise PipelineError(f"resource {index} has invalid kind {kind!r}")
        path = raw.get("path")
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
        item = dict(raw)
        item["path"] = clean
        normalized.append(item)
    system = {
        item["path"].casefold()
        for item in normalized
        if item["kind"] == "system_sidecar"
    }
    if system != {path.casefold() for path in SYSTEM_PATHS}:
        raise PipelineError("resource profile has an unexpected system-sidecar set")
    if not any(item["kind"] == "geometry" for item in normalized):
        raise PipelineError("resource profile contains no LOD0 geometry")
    return normalized


def locate_exact_resources(
    game_dir: Path,
    resources: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], AssetEntry]]:
    wanted = {str(item["path"]).casefold(): item for item in resources}
    matches: defaultdict[str, list[AssetEntry]] = defaultdict(list)
    scan_errors: list[str] = []
    try:
        for entry in iter_assets(
            game_dir,
            skip_errors=False,
            errors=scan_errors,
        ):
            folded = entry.virtual_path.casefold()
            if folded in wanted:
                matches[folded].append(entry)
    except (OSError, ValueError) as exc:
        raise PipelineError(f"IDX scan failed: {exc}") from exc
    if scan_errors:
        raise PipelineError("IDX scan reported errors: " + "; ".join(scan_errors))

    missing = [
        definition["path"]
        for folded, definition in wanted.items()
        if not matches.get(folded)
    ]
    if missing:
        raise PipelineError(
            f"{len(missing)} referenced resources are missing: "
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
    definition: Mapping[str, Any],
    entry: AssetEntry,
    extracted_root: Path,
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


def run_checked(
    label: str,
    command: Sequence[str],
    *,
    log_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    """Run a pipeline child while forwarding its output to the GUI in real time."""

    started = time.monotonic()
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        list(command),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=environment,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()
    output: list[str] = []

    def read_output() -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = started + timeout
    timed_out = False
    reader_done = False
    while not reader_done:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            line = output_queue.get(timeout=min(0.2, remaining))
        except queue.Empty:
            continue
        if line is None:
            reader_done = True
            continue
        output.append(line)
        print(line, end="", flush=True)

    if timed_out:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    else:
        remaining = max(0.001, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    reader.join(timeout=1)
    while True:
        try:
            line = output_queue.get_nowait()
        except queue.Empty:
            break
        if line is not None:
            output.append(line)
            print(line, end="", flush=True)
    if process.stdout is not None:
        process.stdout.close()

    elapsed = time.monotonic() - started
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
    log = log_dir / f"{safe_label}.log"
    log.write_text("".join(output), encoding="utf-8")
    result = {
        "label": label,
        "command": list(command),
        "returncode": process.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "log": str(log),
    }
    if timed_out:
        raise PipelineError(f"{label} timed out after {timeout}s; see {log}")
    if process.returncode != 0:
        raise PipelineError(
            f"{label} failed with exit {process.returncode}; see {log}"
        )
    return result


def emit_progress(percent: int, message: str) -> None:
    payload = {"stage": "extract", "percent": percent, "message": message}
    print("[PROGRESS] " + json.dumps(payload, ensure_ascii=False), flush=True)
def mapping_acceptance(path: Path, ship_key: str) -> dict[str, Any]:
    mapping = load_object(path)
    if mapping.get("schema") != "wows-legends-static-ship-assembly/v1":
        raise PipelineError("mapping has an unexpected schema")
    ship = mapping.get("ship")
    if not isinstance(ship, dict):
        raise PipelineError("mapping ship metadata is missing")
    resolved_ship_key = ship.get("ship_key")
    requested_ship_key = ship.get("requested_ship_key")
    key_resolution = ship.get("key_resolution")
    exact_match = resolved_ship_key == ship_key
    requested_index = re.match(r"^(P[A-Z]+\d+)(?:_|$)", ship_key)
    resolved_index = (
        re.match(r"^(P[A-Z]+\d+)(?:_|$)", resolved_ship_key)
        if isinstance(resolved_ship_key, str)
        else None
    )
    verified_fallback = (
        requested_ship_key == ship_key
        and key_resolution == "ship_index_fallback"
        and requested_index is not None
        and resolved_index is not None
        and requested_index.group(1) == resolved_index.group(1)
        and resolved_ship_key != ship_key
    )
    if not exact_match and not verified_fallback:
        raise PipelineError(
            f"mapping ship mismatch: requested={ship_key!r}, "
            f"resolved={resolved_ship_key!r}, "
            f"mapping_requested={requested_ship_key!r}, "
            f"resolution={key_resolution!r}"
        )
    validation = mapping.get("validation")
    if not isinstance(validation, dict):
        raise PipelineError("mapping validation is missing")
    failures = {
        "static_assembly_acceptance": validation.get(
            "static_assembly_acceptance"
        )
        is not True,
        "missing_combat_hardpoints": bool(
            validation.get("missing_combat_hardpoints")
        ),
        "duplicate_combat_hardpoint_sources": bool(
            validation.get("duplicate_combat_hardpoint_sources")
        ),
        "model_uber_parse_failures": bool(
            validation.get("model_uber_parse_failures")
        ),
        "unresolved_render_set_fields": bool(
            validation.get("unresolved_render_set_fields")
        ),
        "unresolved_texture_paths": bool(
            validation.get("unresolved_texture_paths")
        ),
        "all_output_matrices_finite": validation.get(
            "all_output_matrices_finite"
        )
        is not True,
    }
    failed = [name for name, state in failures.items() if state]
    if failed:
        raise PipelineError(
            "selected-ship mapping acceptance failed: " + ", ".join(failed)
        )
    return {
        "sha256": sha256_file(path),
        "requested_ship_key": ship_key,
        "resolved_ship_key": resolved_ship_key,
        "key_resolution": key_resolution or "exact",
        **validation,
    }


def pbr_acceptance(path: Path) -> dict[str, Any]:
    summary = load_object(path)
    strict = summary.get("strict_validation")
    if not isinstance(strict, dict) or strict.get("accepted") is not True:
        raise PipelineError("selected-ship PBR batch acceptance did not pass")
    expected = summary.get("model_count")
    results = summary.get("result_models")
    if not isinstance(expected, int) or expected <= 0 or results != expected:
        raise PipelineError(
            f"PBR model count mismatch: expected={expected}, results={results}"
        )
    if summary.get("required_texture_missing"):
        raise PipelineError("PBR batch reports missing declared textures")
    return strict


def scene_acceptance(path: Path) -> dict[str, Any]:
    validation = load_object(path)
    if validation.get("ok") is not True:
        raise PipelineError("Blender scene validation did not pass")
    combined = validation.get("combined_obj")
    if not isinstance(combined, dict) or combined.get("ok") is not True:
        raise PipelineError("combined visible-state OBJ validation did not pass")
    if combined.get("mode") != "editable_obj_only":
        raise PipelineError("scene output is not editable OBJ-only mode")
    checks = combined.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise PipelineError("combined OBJ contract checks failed")
    paths = {
        "obj": Path(str(combined.get("obj", ""))).resolve(),
        "mtl": Path(str(combined.get("mtl", ""))).resolve(),
        "textures": Path(str(combined.get("texture_directory", ""))).resolve(),
    }
    if (
        not paths["obj"].is_file()
        or not paths["mtl"].is_file()
        or not paths["textures"].is_dir()
    ):
        raise PipelineError("combined OBJ/MTL/textures output is missing")
    if int(combined.get("vertices", 0)) <= 0 or int(
        combined.get("faces", 0)
    ) <= 0:
        raise PipelineError("combined OBJ contains no geometry")
    return {
        "accepted": True,
        "obj": {
            "path": str(paths["obj"]),
            "bytes": paths["obj"].stat().st_size,
            "sha256": sha256_file(paths["obj"]),
        },
        "mtl": {
            "path": str(paths["mtl"]),
            "bytes": paths["mtl"].stat().st_size,
            "sha256": sha256_file(paths["mtl"]),
        },
        "texture_files": len(
            [path for path in paths["textures"].iterdir() if path.is_file()]
        ),
        "vertices": combined["vertices"],
        "faces": combined["faces"],
        "editable_mesh_objects": combined.get("editable_mesh_objects"),
        "material_slots": combined.get("material_slots"),
        "texture_source_images": combined.get("texture_source_images"),
        "unique_texture_files": combined.get("unique_texture_files"),
        "deduplicated_texture_sources": combined.get("deduplicated_texture_sources"),
        "included_visible_mounts": len(
            combined.get("included_visible_mounts", [])
        ),
        "excluded_hidden_mounts": len(
            combined.get("excluded_hidden_mounts", [])
        ),
    }


def _system_definitions() -> list[dict[str, str]]:
    return [
        {"kind": "system_sidecar", "path": path} for path in SYSTEM_PATHS
    ]


def _extract_located(
    located: Sequence[tuple[Mapping[str, Any], AssetEntry]],
    extracted_root: Path,
    *,
    overwrite: bool,
    max_single_mib: int,
    skip_existing_system: bool = False,
) -> tuple[list[Any], list[dict[str, Any]]]:
    results = []
    crc_records: list[dict[str, Any]] = []
    for definition, entry in located:
        target = extracted_root.joinpath(
            *PurePosixPath(entry.virtual_path).parts
        )
        if (
            skip_existing_system
            and definition["kind"] == "system_sidecar"
            and target.is_file()
        ):
            actual_crc, actual_sha256 = file_checksums(target)
            if actual_crc != entry.file_info.crc32:
                raise PipelineError(
                    f"existing sidecar CRC mismatch: {entry.virtual_path}"
                )
            crc_records.append(
                {
                    "path": entry.virtual_path,
                    "kind": definition["kind"],
                    "crc32": f"{actual_crc:08X}",
                    "sha256": actual_sha256,
                    "bytes": target.stat().st_size,
                    "status": "reused",
                }
            )
            continue
        result = extract_asset(
            entry,
            extracted_root,
            execute=True,
            overwrite=overwrite,
            max_unpacked_size=max_single_mib * 1024 * 1024,
        )
        actual_crc, actual_sha256 = file_checksums(result.target)
        if actual_crc != entry.file_info.crc32:
            raise PipelineError(
                f"post-write CRC mismatch for {entry.virtual_path}"
            )
        results.append(result)
        crc_records.append(
            {
                "path": entry.virtual_path,
                "kind": definition["kind"],
                "crc32": f"{actual_crc:08X}",
                "sha256": actual_sha256,
                "bytes": result.target.stat().st_size,
                "status": result.status,
            }
        )
    return results, crc_records


def execute_pipeline(
    args: argparse.Namespace,
    system_located: Sequence[tuple[Mapping[str, Any], AssetEntry]],
) -> dict[str, Any]:
    game_dir = args.game_dir.resolve()
    output_root = args.output_root.resolve()
    slug = safe_slug(args.run_slug or args.ship_key)
    run_root = validate_run_root(output_root, output_root / f"{slug}_Full")
    extracted = run_root / "extracted"
    generated = run_root / "generated"
    pbr = run_root / "pbr"
    scene = run_root / "scene"
    logs = run_root / "logs"
    manifest = run_root / "selected_ship_full.pipeline.json"
    if run_root.exists() and not args.overwrite:
        raise PipelineError(
            f"run output already exists; use --overwrite explicitly: {run_root}"
        )
    if run_root.exists() and args.overwrite:
        run_root = validate_run_root(output_root, run_root)
        shutil.rmtree(run_root)
    if not args.native_obj and not args.blender.is_file():
        raise PipelineError(f"Blender executable not found: {args.blender}")
    components = [
        MAPPING_RUNNER,
        RESOURCE_BUILDER,
        NATIVE_PBR_BATCH if args.native_obj else PBR_BATCH,
        NATIVE_SCENE_ASSEMBLER if args.native_obj else PLAN_BUILDER,
    ]
    if not args.native_obj:
        components.append(SCENE_ASSEMBLER)
    for path in components:
        if not path.is_file():
            raise PipelineError(f"pipeline component is missing: {path}")
    run_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        manifest,
        {
            "schema": "wows-legends-selected-ship-full-run/v1",
            "mode": "executing",
            "status": "in_progress",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ship_key": args.ship_key,
            "game_dir": str(game_dir),
            "run_root": str(run_root),
            "writes_game_directory": False,
            "overwrite": bool(args.overwrite),
            "acceptance": {"passed": False},
        },
    )

    emit_progress(6, "Legends 시스템 리소스를 준비하는 중")
    system_results, system_crc = _extract_located(
        system_located,
        extracted,
        overwrite=args.overwrite,
        max_single_mib=args.max_single_mib,
    )
    emit_progress(8, "함선 조립 정보와 무장 배치를 분석하는 중")
    mapping = generated / "selected_ship_static_assembly.json"
    acceptance = generated / "mapping_acceptance.md"
    step_results = [
        run_checked(
            "Selected ship mapping",
            [
                str(args.python.resolve()),
                "-B",
                str(MAPPING_RUNNER),
                "--ship-key",
                args.ship_key,
                *(["--selected-model-path", args.selected_model_path] if args.selected_model_path else []),
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
                str(acceptance),
            ],
            log_dir=logs,
            timeout=900,
        )
    ]
    mapping_validation = mapping_acceptance(mapping, args.ship_key)
    emit_progress(14, "함선에 필요한 부품과 텍스처를 계산하는 중")

    resource_profile = generated / "selected_ship_resources.json"
    step_results.append(
        run_checked(
            "Selected ship resource profile",
            [
                str(args.python.resolve()),
                "-B",
                str(RESOURCE_BUILDER),
                "--mapping",
                str(mapping),
                "--output",
                str(resource_profile),
            ],
            log_dir=logs,
            timeout=300,
        )
    )
    profile = load_object(resource_profile)
    resources = validate_resource_definitions(profile.get("resources"))
    located = locate_exact_resources(game_dir, resources)
    emit_progress(20, f"필요한 리소스 {len(located)}개를 추출하는 중")
    asset_results, asset_crc = _extract_located(
        located,
        extracted,
        overwrite=args.overwrite,
        max_single_mib=args.max_single_mib,
        skip_existing_system=True,
    )
    extraction_manifest = write_manifest(
        [*system_results, *asset_results],
        extracted,
        "selected_ship_extraction.json",
    )

    emit_progress(24, "함선 부품 OBJ와 재질을 변환하는 중")
    batch_summary = pbr / "selected_ship_pbr_models.summary.json"
    batch_command = [
        str(args.python.resolve()),
        "-B",
        str(NATIVE_PBR_BATCH if args.native_obj else PBR_BATCH),
        "--mapping",
        str(mapping),
        "--extracted-root",
        str(extracted),
        "--output-root",
        str(pbr),
        "--decoder-root",
        str(GEOMETRY_DECODER.resolve()),
    ]
    if args.native_obj:
        batch_command.extend(["--workers", "4"])
    else:
        batch_command.extend(
            [
                "--conversion-cache-root",
                str((args.cache_root / "LegendsPBR").resolve()),
                "--blender",
                str(args.blender.resolve()),
                "--reuse-existing",
            ]
        )
    step_results.append(
        run_checked(
            (
                "Selected ship native OBJ batch"
                if args.native_obj
                else "Selected ship PBR batch"
            ),
            batch_command,
            log_dir=logs,
            timeout=3600,
        )
    )
    pbr_validation = pbr_acceptance(batch_summary)
    emit_progress(70, "함선 부품 변환이 끝나 최종 배치를 만드는 중")

    output_stem = safe_slug(str(profile.get("ship_key") or args.ship_key))
    output_obj = scene / f"{output_stem}_Editable.obj"
    scene_validation_path = scene / f"{output_stem}.validation.json"
    if args.native_obj:
        emit_progress(76, "Blender 없이 개별 부품을 편집 가능한 OBJ로 조립하는 중")
        step_results.append(
            run_checked(
                "Native Python scene assembly",
                [
                    str(args.python.resolve()),
                    "-B",
                    str(NATIVE_SCENE_ASSEMBLER),
                    "--assembly",
                    str(mapping),
                    "--batch-summary",
                    str(batch_summary),
                    "--visibility-profile",
                    args.visibility_profile,
                    "--obj",
                    str(output_obj),
                    "--validation",
                    str(scene_validation_path),
                ],
                log_dir=logs,
                timeout=1800,
            )
        )
    else:
        scene_plan = generated / "selected_ship_scene_plan.json"
        step_results.append(
            run_checked(
                "Selected ship scene plan",
                [
                    str(args.python.resolve()),
                    "-B",
                    str(PLAN_BUILDER),
                    "--assembly",
                    str(mapping),
                    "--batch-summary",
                    str(batch_summary),
                    "--visibility-profile",
                    args.visibility_profile,
                    "--output",
                    str(scene_plan),
                    "--output-combined-obj",
                    str(output_obj),
                    "--validation-json",
                    str(scene_validation_path),
                ],
                log_dir=logs,
                timeout=300,
            )
        )
        emit_progress(76, "개별 부품을 편집 가능한 OBJ로 조립하는 중")
        step_results.append(
            run_checked(
                "Blender scene assembly",
                [
                    str(args.blender.resolve()),
                    "--background",
                    "--factory-startup",
                    "--python",
                    str(SCENE_ASSEMBLER),
                    "--",
                    "--plan",
                    str(scene_plan),
                    "--obj-only",
                    "--editable-objects",
                    "--obj",
                    str(output_obj),
                    "--validation",
                    str(scene_validation_path),
                ],
                log_dir=logs,
                timeout=1800,
            )
        )
    assembled = scene_acceptance(scene_validation_path)

    archived_extraction_manifest = generated / "selected_ship_extraction.json"
    shutil.copy2(extraction_manifest, archived_extraction_manifest)
    cleanup = {"keep_work_files": bool(args.keep_work_files), "removed": []}
    if not args.keep_work_files:
        cleanup["removed"] = [
            _remove_pipeline_directory(extracted, run_root),
            _remove_pipeline_directory(pbr, run_root),
        ]

    payload = {
        "schema": "wows-legends-selected-ship-full-run/v1",
        "mode": "editable-obj-only-native" if args.native_obj else "editable-obj-only",
        "status": "completed",
        "ship_key": args.ship_key,
        "game_dir": str(game_dir),
        "writes_game_directory": False,
        "run_root": str(run_root),
        "overwrite": bool(args.overwrite),
        "resource_profile": {
            "path": str(resource_profile),
            "sha256": sha256_file(resource_profile),
            "counts": profile.get("expected_counts"),
        },
        "extraction_manifest": str(archived_extraction_manifest),
        "cleanup": cleanup,
        "crc_verified_resources": [*system_crc, *asset_crc],
        "steps": step_results,
        "acceptance": {
            "mapping": mapping_validation,
            "pbr": pbr_validation,
            "scene": assembled,
            "assembled_blend": None,
            "assembled_glb": None,
            "passed": assembled["accepted"],
        },
        "limitations": profile.get("limitations", []),
        "pipeline_manifest": str(manifest),
    }
    write_json_atomic(manifest, payload)
    return payload


def record_failure_manifest(args: argparse.Namespace, exc: BaseException) -> None:
    if not getattr(args, "execute", False):
        return
    try:
        output_root = args.output_root.resolve()
        slug = safe_slug(args.run_slug or args.ship_key)
        run_root = output_root / f"{slug}_Full"
        manifest = run_root / "selected_ship_full.pipeline.json"
        if not run_root.is_dir():
            return
        try:
            payload = load_object(manifest) if manifest.is_file() else {}
        except (OSError, ValueError, json.JSONDecodeError, PipelineError):
            payload = {}
        logs = run_root / "logs"
        payload.update(
            {
                "schema": "wows-legends-selected-ship-full-run/v1",
                "mode": "editable-obj-only-native" if args.native_obj else "editable-obj-only",
                "status": "failed",
                "ship_key": args.ship_key,
                "selected_model_path": args.selected_model_path,
                "run_root": str(run_root),
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "logs": [
                        str(path)
                        for path in sorted(logs.glob("*.log"))
                        if path.is_file()
                    ] if logs.is_dir() else [],
                },
                "acceptance": {"passed": False},
            }
        )
        write_json_atomic(manifest, payload)
    except Exception as manifest_exc:
        print(
            "warning: failed to record selected-ship failure manifest: "
            f"{type(manifest_exc).__name__}: {manifest_exc}",
            file=sys.stderr,
            flush=True,
        )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", required=True, type=Path)
    parser.add_argument("--ship-key", required=True)
    parser.add_argument("--selected-model-path")
    parser.add_argument("--run-slug")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
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
    parser.add_argument("--keep-work-files", action="store_true")
    parser.add_argument(
        "--native-obj",
        action="store_true",
        help="assemble editable OBJ/MTL/PNG with Python only; Blender is not used",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        game_dir = args.game_dir.resolve()
        output_root = args.output_root.resolve()
        args.cache_root = (args.cache_root or (output_root / ".toolbox-cache")).resolve()
        if not (game_dir / "res_packages").is_dir():
            raise PipelineError(f"res_packages not found under {game_dir}")
        if args.max_single_mib <= 0:
            raise PipelineError("--max-single-mib must be positive")
        validate_output_location(game_dir, output_root)
        slug = safe_slug(args.run_slug or args.ship_key)
        system_definitions = _system_definitions()
        system_located = locate_exact_resources(game_dir, system_definitions)
        run_root = validate_run_root(output_root, output_root / f"{slug}_Full")
        dry_plan = {
            "schema": "wows-legends-selected-ship-full-plan/v1",
            "mode": "execute" if args.execute else "dry-run",
            "ship_key": args.ship_key,
            "game_dir": str(game_dir),
            "writes_game_directory": False,
            "output_root": str(output_root),
            "run_root": str(run_root),
            "overwrite": bool(args.overwrite),
            "output_contract": {
                "format": "editable OBJ + MTL + lossless deduplicated PNG",
                "blend": False,
                "glb": False,
                "separate_objects": True,
                "assembly_engine": (
                    "native_python_obj/v1" if args.native_obj else "blender"
                ),
            },
            "keep_work_files": bool(args.keep_work_files),
            "system_sidecars": [
                planned_resource(definition, entry, run_root / "extracted")
                for definition, entry in system_located
            ],
            "dynamic_resolution": (
                "Execution parses the selected GameParams ship and ModelUber "
                "records, then scans all IDX files for only the referenced "
                "LOD0 geometry and textures."
            ),
            "safety": {
                "dry_run_default": True,
                "execute_requires_opt_in": True,
                "game_directory_read_only": True,
                "output_disjoint_from_game": True,
                "overwrite_requires_opt_in": True,
            },
        }
        if not args.execute:
            print(json.dumps(dry_plan, ensure_ascii=False, indent=2))
            return 0
        result = execute_pipeline(args, system_located)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        PipelineError,
        ExtractionError,
        FileNotFoundError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        record_failure_manifest(args, exc)
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
