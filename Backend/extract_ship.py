from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True

from compat_cache import prepare_game_params_cache  # noqa: E402
from game_archive import (  # noqa: E402
    latest_build,
    prepare_korabli_cache,
    progress,
)
from runtime_i18n import translate_line, translate_text  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SUPPORTED_FORMATS = {"obj"}


def parse_formats(value: str) -> set[str]:
    formats = {
        item.strip().casefold()
        for item in value.split(",")
        if item.strip()
    } or {"obj"}
    unsupported = formats - SUPPORTED_FORMATS
    if unsupported:
        raise ValueError(
            "지원하지 않는 출력 형식: " + ", ".join(sorted(unsupported))
        )
    return formats


def prefetch_cancelled(args: argparse.Namespace) -> bool:
    promoted = getattr(args, "promoted_event", None)
    cancel_check = getattr(args, "cancel_check", None)
    return bool(
        callable(cancel_check)
        and cancel_check()
        and promoted is not None
        and not promoted.is_set()
    )


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    return (cleaned or "ship")[:100]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_output_location(game_dir: Path, output_root: Path) -> tuple[Path, Path]:
    """Return canonical paths after rejecting destructive/overlapping layouts."""
    game = game_dir.resolve()
    output = output_root.resolve()
    if output.parent == output:
        raise ValueError("드라이브 루트는 출력 폴더로 사용할 수 없어요")
    if output == game:
        raise ValueError("게임 폴더 자체는 출력 폴더로 사용할 수 없어요")
    if _is_relative_to(output, game):
        raise ValueError("출력 폴더는 게임 설치 폴더 밖에 있어야 해요")
    if _is_relative_to(game, output):
        raise ValueError("게임 설치 폴더의 상위 폴더는 출력 폴더로 사용할 수 없어요")
    return game, output


def validate_output_child(output_root: Path, output_dir: Path) -> Path:
    """Resolve one extractor-owned child without following a junction outside it."""
    root = output_root.resolve()
    requested = Path(os.path.abspath(output_dir))
    expected = root / requested.name
    if requested.parent != root:
        raise ValueError(f"출력 대상이 출력 루트의 바로 아래가 아니에요: {output_dir}")
    resolved = output_dir.resolve(strict=False)
    if resolved != expected or resolved.parent != root:
        raise ValueError(
            "출력 대상이 외부 폴더를 가리키는 링크 또는 연결 지점이에요: "
            f"{output_dir}"
        )
    return resolved


def run_stream(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    label: str = "",
    cancel_check=None,
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, name="wows-process-output", daemon=True)
    reader.start()
    try:
        while True:
            if callable(cancel_check) and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise RuntimeError("다음 함선의 미리 준비 작업을 취소했어요")
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                break
            rendered = line.rstrip()
            if label:
                rendered = f"[{label}] {rendered}"
            print(translate_line(rendered), flush=True)
        code = process.wait()
        if code:
            raise subprocess.CalledProcessError(code, command)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reader.join(timeout=1)
        process.stdout.close()
        if reader.is_alive():
            reader.join(timeout=1)

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def valid_glb(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                return False
            magic, version, declared_length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or declared_length != path.stat().st_size:
                return False
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                return False
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            if chunk_type != 0x4E4F534A or chunk_length <= 0:
                return False
            document = json.loads(stream.read(chunk_length).decode("utf-8", "replace").rstrip(" \t\r\n\x00"))
        return bool(document.get("meshes"))
    except (OSError, ValueError, TypeError, struct.error, json.JSONDecodeError):
        return False


def valid_exact_armor_json(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return (
            payload.get("schema") == "wowsunpack-interactive-armor/v1"
            and isinstance(payload.get("meshes"), list)
            and bool(payload["meshes"])
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def publish_exact_armor_sidecar(
    armor_metadata: Path,
    final_path: Path,
    converter: Path,
    env: dict[str, str],
) -> tuple[dict | None, str | None]:
    """Publish exact armor metadata atomically without risking the model export.

    Blender may already have written a coarser fallback sidecar at ``final_path``.
    The exact converter therefore writes to a temporary sibling and replaces the
    fallback only after the converted document has passed its schema checks.
    """
    temporary = final_path.with_name(
        final_path.name + f".{os.getpid()}.exact.part"
    )
    temporary.unlink(missing_ok=True)
    try:
        run_stream(
            [
                sys.executable,
                str(converter),
                "--input",
                str(armor_metadata),
                "--output",
                str(temporary),
            ],
            env=env,
            label="ARMOR",
        )
        payload = json.loads(temporary.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "wows-toolbox-armor-viewer/v3"
            or payload.get("exact_thickness") is not True
            or not isinstance(payload.get("groups"), list)
        ):
            raise ValueError("정확 장갑 보조 파일의 형식이 올바르지 않아요")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, final_path)
        return payload, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        temporary.unlink(missing_ok=True)


def materialize_cache(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)

def next_output_dir(root: Path, stem: str, overwrite: bool) -> Path:
    candidate = root / stem
    if overwrite or not candidate.exists():
        return candidate
    serial = 2
    while True:
        numbered = root / f"{stem}_{serial:02d}"
        if not numbered.exists():
            return numbered
        serial += 1


def next_legends_output_dir(
    root: Path, run_slug: str, overwrite: bool
) -> tuple[Path, str]:
    base_slug = safe_name(run_slug)
    candidate = root / f"{base_slug}_Full"
    if overwrite or not candidate.exists():
        return candidate, base_slug
    serial = 2
    while True:
        numbered_slug = f"{base_slug}_{serial:02d}"
        numbered = root / f"{numbered_slug}_Full"
        if not numbered.exists():
            return numbered, numbered_slug
        serial += 1


def extract_legends(args: argparse.Namespace, output_dir: Path) -> dict:
    formats = parse_formats(args.formats)
    requested_lod = getattr(args, "requested_lod", getattr(args, "lod", 0))
    requested_texture_max_size = getattr(
        args,
        "requested_texture_max_size",
        getattr(args, "texture_max_size", 0),
    )
    if requested_lod != 0 or requested_texture_max_size != 0:
        print(
            "[WARN] Legends는 ModelUber LOD0과 선언된 원본 크기 컬러 "
            "텍스처로 고정해 추출해요.",
            flush=True,
        )
    requested_run_root = validate_output_child(args.output_root, output_dir)
    expected_run_root = validate_output_child(
        args.output_root,
        args.output_root / f"{safe_name(args.run_slug)}_Full",
    )
    if requested_run_root != expected_run_root:
        raise ValueError(
            "Legends 출력 폴더와 run-slug가 일치하지 않아요: "
            f"{requested_run_root.name} != {expected_run_root.name}"
        )
    if formats != {"obj"}:
        raise ValueError(
            "Blender 없는 테스트 경로는 현재 OBJ 출력만 지원해요"
        )
    pipeline = (
        args.toolbox_root
        / "FullAssembly"
        / "SelectedShip"
        / "Pipeline"
        / "extract_selected_ship_full.py"
    )
    progress("extract", 4, "Legends 함선 리소스를 계산하는 중")
    command = [
        sys.executable,
        "-B",
        str(pipeline),
        "--game-dir",
        str(args.game_dir),
        "--ship-key",
        args.ship_key,
        "--run-slug",
        args.run_slug,
        "--output-root",
        str(output_dir.parent),
        "--python",
        sys.executable,
        "--visibility-profile",
        "harbor_dock",
        "--cache-root",
        str(args.cache_root or (Path(os.environ.get("LOCALAPPDATA", str(args.output_root))) / "WoWSToolbox" / "Cache")),
        "--native-obj",
        "--execute",
    ]
    if args.selected_model_path:
        command.extend(["--selected-model-path", args.selected_model_path])
    run_root = expected_run_root
    manifest = run_root / "selected_ship_full.pipeline.json"
    retry_incomplete = False
    if run_root.exists() and not args.overwrite and manifest.is_file():
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            retry_incomplete = not bool(
                previous.get("acceptance", {}).get("passed")
            )
        except (OSError, ValueError, TypeError):
            retry_incomplete = False
    if args.overwrite or retry_incomplete:
        command.append("--overwrite")
    if retry_incomplete:
        progress(
            "retry",
            3,
            "이전 미완료 작업 폴더를 안전하게 이어서 다시 쓰는 중",
        )
    run_stream(command)

    if not manifest.is_file():
        raise FileNotFoundError(f"Legends 결과 매니페스트가 없어요: {manifest}")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    pipeline_ship_key = str(payload.get("ship_key") or "")
    if pipeline_ship_key and pipeline_ship_key.casefold() != args.ship_key.casefold():
        raise RuntimeError(
            f"Legends 변형 교차 오염 방지 실패: 요청 {args.ship_key}, 결과 {pipeline_ship_key}"
        )
    scene = payload.get("acceptance", {}).get("scene", {})
    combined = scene.get("combined_obj", scene)
    obj_value = combined.get("obj")
    mtl_value = combined.get("mtl")
    if isinstance(obj_value, dict):
        obj_value = obj_value.get("path")
    if isinstance(mtl_value, dict):
        mtl_value = mtl_value.get("path")
    obj_path = Path(str(obj_value or ""))
    mtl_path = Path(str(mtl_value or ""))
    result = {
        "ok": bool(payload.get("acceptance", {}).get("passed")),
        "source": "legends",
        "output_dir": str(run_root),
        "obj": str(obj_path) if "obj" in formats else None,
        "mtl": str(mtl_path) if "obj" in formats and mtl_path.is_file() else None,
        "intermediate_obj": str(obj_path),
        "object_count": combined.get("editable_mesh_objects"),
        "blend_created": False,
        "pipeline_manifest": str(manifest),
        "formats": sorted(formats),
        "quality_contract": {
            "requested_model_lod": requested_lod,
            "model_lod": 0,
            "highest_mesh_lod": True,
            "requested_texture_max_size": requested_texture_max_size,
            "texture_max_size": 0,
            "original_textures": True,
            "source_policy_enforced": True,
            "source_policy": (
                "ModelUber LOD0 render sets and declared source textures"
            ),
        },
        "selection_contract": {
            "requested": args.ship_key,
            "resolved": pipeline_ship_key or args.ship_key,
            "exact_match": not pipeline_ship_key or pipeline_ship_key.casefold() == args.ship_key.casefold(),
            "variant": args.ship_resource or args.selected_model_path,
        },
    }
    progress("complete", 100, "Legends 편집형 모델 추출 완료")
    return result


def _export_glb_to_cache(
    command: list[str],
    *,
    env: dict[str, str],
    output_glb: Path,
    cache_glb: Path,
    label: str,
    invalid_message: str,
    cancel_check=None,
) -> float:
    """Run one independent exporter and atomically publish its validated GLB."""

    started = time.perf_counter()
    output_glb.unlink(missing_ok=True)
    run_stream(command, env=env, label=label, cancel_check=cancel_check)
    if not valid_glb(output_glb) and "--lod" in command:
        lod_index = command.index("--lod") + 1
        if lod_index < len(command) and command[lod_index] != "0":
            requested_lod = command[lod_index]
            output_glb.unlink(missing_ok=True)
            raise RuntimeError(
                f"{label} LOD {requested_lod} 메시가 없어요. "
                "최고 품질인 LOD0을 선택해 다시 추출해 주세요."
            )
    if not valid_glb(output_glb):
        output_glb.unlink(missing_ok=True)
        raise RuntimeError(invalid_message)
    cache_glb.parent.mkdir(parents=True, exist_ok=True)
    temporary_cache = cache_glb.with_suffix(
        cache_glb.suffix + f".{os.getpid()}.{label.lower()}.part"
    )
    temporary_cache.unlink(missing_ok=True)
    try:
        shutil.copy2(output_glb, temporary_cache)
        os.replace(temporary_cache, cache_glb)
    finally:
        temporary_cache.unlink(missing_ok=True)
    return time.perf_counter() - started


def _run_export_jobs(
    jobs: dict[str, tuple[list[str], Path, Path, str]],
    env: dict[str, str],
    *,
    cancel_check=None,
) -> tuple[dict[str, float], dict[str, BaseException], float]:
    started = time.perf_counter()
    durations: dict[str, float] = {}
    errors: dict[str, BaseException] = {}
    if jobs:
        with ThreadPoolExecutor(
            max_workers=len(jobs), thread_name_prefix="wows-export"
        ) as executor:
            future_kinds = {
                executor.submit(
                    _export_glb_to_cache,
                    command,
                    env=env,
                    output_glb=output_glb,
                    cache_glb=cache_glb,
                    label=kind.upper(),
                    invalid_message=invalid_message,
                    cancel_check=cancel_check,
                ): kind
                for kind, (command, output_glb, cache_glb, invalid_message) in jobs.items()
            }
            for future in as_completed(future_kinds):
                kind = future_kinds[future]
                try:
                    durations[kind] = future.result()
                except Exception as exc:
                    errors[kind] = exc
    return durations, errors, time.perf_counter() - started


def extract_pc_family(args: argparse.Namespace, output_dir: Path) -> dict:
    total_started = time.perf_counter()
    formats = parse_formats(args.formats)
    backend = args.toolbox_root / "Backend"
    exporter = backend / "wowsunpack.exe"
    armor_exporter = backend / "wowsunpack_armor.exe"
    native_exporter = backend / "native_glb_export.py"
    if not exporter.is_file():
        raise FileNotFoundError(f"함선 내보내기 엔진이 없어요: {exporter}")
    if not native_exporter.is_file():
        raise FileNotFoundError(f"Blender 없는 OBJ 변환기가 없어요: {native_exporter}")
    if not formats.issubset({"obj", "glb"}):
        raise ValueError("WoWS Toolbox는 Blender 없이 OBJ/원본 GLB만 지원해요")

    env = os.environ.copy()
    cache_base = args.cache_root or (
        Path(os.environ.get("LOCALAPPDATA", str(args.output_root)))
        / "WoWSToolbox"
        / "Cache"
    )
    cache_prepare_started = time.perf_counter()
    if args.source == "korabli":
        cache_info = prepare_korabli_cache(
            args.game_dir,
            cache_base / "Korabli",
            oodle_dll=args.oodle_dll,
        )
        env["WOWS_GAME_PARAMS_OVERRIDE"] = cache_info["game_params"]
        env["WOWS_ASSETS_BIN_OVERRIDE"] = cache_info["assets_bin"]
        env["WOWS_OODLE_DLL"] = cache_info["oodle_dll"]
    else:
        cache_info = prepare_game_params_cache(args.game_dir, cache_base, args.source)
        env["WOWS_GAME_PARAMS_OVERRIDE"] = cache_info["game_params"]
    cache_prepare_seconds = time.perf_counter() - cache_prepare_started
    if prefetch_cancelled(args):
        raise RuntimeError("다음 함선의 미리 준비 작업을 취소했어요")

    resolved_output = validate_output_child(args.output_root, output_dir)
    if args.overwrite and resolved_output.exists():
        # Revalidate immediately before the only recursive delete in this backend.
        resolved_output = validate_output_child(args.output_root, resolved_output)
        shutil.rmtree(resolved_output)
    output_dir = resolved_output
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / ".work"
    work_dir.mkdir(parents=True, exist_ok=True)
    glb = work_dir / (safe_name(args.ship_index) + ".glb")
    armor_glb = work_dir / (safe_name(args.ship_index) + ".armor.glb")
    armor_metadata = work_dir / (safe_name(args.ship_index) + ".armor.exact.json")
    obj = output_dir / (safe_name(args.display_name or args.ship_index) + ".obj")
    report_path = output_dir / "export.json"

    build, _ = latest_build(args.game_dir)
    exporter_fingerprint = file_sha256(exporter)[:16]
    glb_cache = (
        cache_base
        / "ShipGLB"
        / args.source
        / str(build)
        / exporter_fingerprint
        / f"lod-{args.lod}"
        / (safe_name(args.ship_index) + ".glb")
    )
    glb_cache_reused = valid_glb(glb_cache)

    armor_cache = None
    armor_metadata_cache = None
    armor_cache_reused = False
    armor_ready = False
    armor_error = None
    if armor_exporter.is_file():
        armor_fingerprint = file_sha256(armor_exporter)[:16]
        armor_cache = (
            cache_base
            / "ArmorGLB"
            / args.source
            / str(build)
            / armor_fingerprint
            / f"lod-{args.lod}"
            / (safe_name(args.ship_index) + ".glb")
        )
        armor_metadata_cache = armor_cache.with_suffix(".exact.json")
        armor_cache_reused = (
            valid_glb(armor_cache)
            and valid_exact_armor_json(armor_metadata_cache)
        )
    else:
        armor_error = "장갑 내보내기 엔진이 없어요"

    if glb_cache_reused:
        materialize_cache(glb_cache, glb)
    if (
        armor_cache_reused
        and armor_cache is not None
        and armor_metadata_cache is not None
    ):
        materialize_cache(armor_cache, armor_glb)
        materialize_cache(armor_metadata_cache, armor_metadata)
        armor_ready = True

    jobs: dict[str, tuple[list[str], Path, Path, str]] = {}
    if not glb_cache_reused:
        jobs["model"] = (
            [
                str(exporter),
                "--game-dir",
                str(args.game_dir),
                "export-ship",
                args.ship_index,
                "--lod",
                str(args.lod),
                "--output",
                str(glb),
            ],
            glb,
            glb_cache,
            "중간 GLB가 만들어지지 않았거나 손상됐어요",
        )
    if armor_cache is not None and not armor_cache_reused:
        jobs["armor"] = (
            [
                str(armor_exporter),
                "--game-dir",
                str(args.game_dir),
                "export-ship",
                args.ship_index,
                "--lod",
                str(args.lod),
                "--no-textures",
                "--armor-json",
                str(armor_metadata),
                "--output",
                str(armor_glb),
            ],
            armor_glb,
            armor_cache,
            "장갑 내보내기가 유효한 GLB를 만들지 못했어요",
        )

    parallel_model_armor = set(jobs) == {"model", "armor"}
    if parallel_model_armor:
        progress("extract", 8, "선체·무장과 장갑을 동시에 계산하는 중")
    elif "model" in jobs:
        progress("extract", 8, "선체와 무장 배치를 계산하는 중")
    elif "armor" in jobs:
        progress("armor", 60, "장갑 구역과 두께 메시를 계산하는 중")
    else:
        progress("extract", 58, "같은 게임 빌드의 모델·장갑 캐시를 재사용하는 중")

    export_durations, export_errors, export_wall_seconds = _run_export_jobs(
        jobs,
        env,
        cancel_check=lambda: prefetch_cancelled(args),
    )
    armor_failure = export_errors.get("armor")
    if armor_failure is not None:
        armor_glb.unlink(missing_ok=True)
        armor_metadata.unlink(missing_ok=True)
        if isinstance(armor_failure, subprocess.CalledProcessError):
            armor_error = f"exit {armor_failure.returncode}"
        else:
            armor_error = str(armor_failure)
        print(f"[WARN] 장갑 메시 추출을 건너뛰어요: {armor_error}", flush=True)
    elif "armor" in jobs:
        armor_ready = valid_glb(armor_glb) and valid_exact_armor_json(armor_metadata)
        if (
            armor_ready
            and armor_metadata_cache is not None
        ):
            armor_metadata_cache.parent.mkdir(parents=True, exist_ok=True)
            temporary_metadata_cache = armor_metadata_cache.with_suffix(
                armor_metadata_cache.suffix + f".{os.getpid()}.armor.part"
            )
            temporary_metadata_cache.unlink(missing_ok=True)
            try:
                shutil.copy2(armor_metadata, temporary_metadata_cache)
                os.replace(temporary_metadata_cache, armor_metadata_cache)
            finally:
                temporary_metadata_cache.unlink(missing_ok=True)

    model_failure = export_errors.get("model")
    if model_failure is not None:
        glb.unlink(missing_ok=True)
        raise model_failure
    if not valid_glb(glb):
        raise RuntimeError("중간 GLB가 만들어지지 않았거나 손상됐어요")

    progress(
        "convert",
        72,
        "Blender 없이 선택한 형식의 편집 파트로 변환하는 중",
    )
    native_command = [
        sys.executable,
        "-B",
        str(native_exporter),
        "--input",
        str(glb),
        "--output",
        str(obj),
        "--report",
        str(report_path),
        "--formats",
        args.formats,
        "--texture-max-size",
        str(args.texture_max_size),
        "--texture-library",
        str(cache_base / "SharedTextures"),
    ]
    prefetch_event = getattr(args, "prefetch_event", None)
    if prefetch_event is not None:
        prefetch_event.set()
    native_started = time.perf_counter()
    if prefetch_cancelled(args):
        raise RuntimeError("다음 함선의 미리 준비 작업을 취소했어요")
    run_stream(native_command, env=env, label="NATIVE")
    native_seconds = time.perf_counter() - native_started
    if not report_path.is_file():
        raise FileNotFoundError("모델 변환 보고서가 만들어지지 않았어요")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("ok"):
        raise RuntimeError("모델 변환 검증을 통과하지 못했어요")

    armor_sidecar_error = None
    if armor_ready and valid_exact_armor_json(armor_metadata):
        exact_armor_path = obj.with_suffix(".armor.json")
        exact_payload, armor_sidecar_error = publish_exact_armor_sidecar(
            armor_metadata,
            exact_armor_path,
            backend / "armor_sidecar.py",
            env,
        )
        if exact_payload is not None:
            report["armor"] = {
                "available": True,
                "path": str(exact_armor_path),
                "groups": len(exact_payload.get("groups", [])),
                "triangles": int(exact_payload.get("triangle_count", 0)),
                "zones": exact_payload.get("zones", []),
                "exact_thickness": True,
                "coordinate_system": exact_payload.get("coordinate_system"),
            }
        else:
            print(
                "[WARN] 정확 장갑 보조 파일 변환을 건너뛰고 "
                f"기존 장갑 데이터를 유지해요: {armor_sidecar_error}",
                flush=True,
            )

    if not args.keep_glb:
        try:
            glb.unlink()
            armor_glb.unlink(missing_ok=True)
            armor_metadata.unlink(missing_ok=True)
            work_dir.rmdir()
        except OSError:
            pass

    total_seconds = time.perf_counter() - total_started
    report.update(
        {
            "source": args.source,
            "ship_index": args.ship_index,
            "display_name": args.display_name,
            "output_dir": str(output_dir),
            "selection_contract": {
                "requested": args.ship_index,
                "resolved": args.ship_index,
                "exact_match": True,
                "source": args.source,
            },
            "quality_contract": {
                "model_lod": args.lod,
                "highest_mesh_lod": args.lod == 0,
                "texture_max_size": args.texture_max_size,
                "original_textures": args.texture_max_size == 0,
                "source_policy": "wowsunpack export-ship LOD index",
            },
            "cache": cache_info,
            "glb_kept": bool(args.keep_glb),
            "ship_glb_cache": str(glb_cache),
            "exporter_fingerprint": exporter_fingerprint,
            "ship_glb_cache_reused": glb_cache_reused,
            "armor_glb_cache": str(armor_cache) if armor_cache else None,
            "armor_metadata_cache": (
                str(armor_metadata_cache) if armor_metadata_cache else None
            ),
            "armor_glb_cache_reused": armor_cache_reused,
            "armor_export_error": armor_error,
            "armor_sidecar_error": armor_sidecar_error,
            "cold_export_parallelized": parallel_model_armor,
            "timings": {
                "cache_prepare_seconds": round(cache_prepare_seconds, 3),
                "model_export_seconds": round(export_durations["model"], 3)
                if "model" in export_durations
                else None,
                "armor_export_seconds": round(export_durations["armor"], 3)
                if "armor" in export_durations
                else None,
                "export_wall_seconds": round(export_wall_seconds, 3),
                "native_convert_seconds": round(native_seconds, 3),
                "blender_seconds": 0.0,
                "total_seconds": round(total_seconds, 3),
            },
        }
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(
        "complete",
        100,
        f"편집 파트 {report['object_count']}개 · {','.join(sorted(formats)).upper()} 추출 완료",
    )
    return report

def main() -> int:
    parser = argparse.ArgumentParser(description="WoWS Toolbox selected ship extractor")
    parser.add_argument("--source", choices=("legends", "pc", "korabli"), required=True)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--toolbox-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--ship-key", default="")
    parser.add_argument("--selected-model-path", default="")
    parser.add_argument("--ship-resource", default="")
    parser.add_argument("--run-slug", default="")
    parser.add_argument("--ship-index", default="")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--oodle-dll", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-glb", action="store_true")
    parser.add_argument("--lod", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--formats", default="obj")
    parser.add_argument("--texture-max-size", type=int, choices=(0, 1024, 2048, 4096), default=0)
    args = parser.parse_args()

    args.game_dir = args.game_dir.resolve()
    args.toolbox_root = args.toolbox_root.resolve()
    args.output_root = args.output_root.resolve()
    if args.oodle_dll:
        args.oodle_dll = args.oodle_dll.resolve()
    if args.cache_root:
        args.cache_root = args.cache_root.resolve()
    args.game_dir, args.output_root = validate_output_location(
        args.game_dir, args.output_root
    )

    if args.source == "legends" and not args.ship_key:
        parser.error("--ship-key가 필요해요")
    if args.source == "legends" and not args.run_slug:
        variant = args.ship_resource or Path(args.selected_model_path).stem or args.ship_key
        args.run_slug = safe_name(f"{args.ship_key}_{variant}")
    if args.source != "legends" and not args.ship_index:
        parser.error("--ship-index가 필요해요")

    if args.source == "legends":
        output_dir, args.run_slug = next_legends_output_dir(
            args.output_root, args.run_slug, args.overwrite
        )
    else:
        stem = safe_name(
            f"{args.display_name or args.ship_index or args.ship_key}_{args.ship_index or args.ship_key}"
        )
        output_dir = next_output_dir(args.output_root, stem, args.overwrite)
    output_dir = validate_output_child(args.output_root, output_dir)
    args.output_root.mkdir(parents=True, exist_ok=True)

    if args.source == "legends":
        result = extract_legends(args, output_dir)
    else:
        result = extract_pc_family(args, output_dir)
    print("[RESULT] " + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(
            f"[ERROR] 하위 추출 단계가 종료 코드 {exc.returncode}로 실패했어요. "
            "바로 위 로그의 첫 오류를 확인해 주세요.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(exc.returncode) from None
    except Exception as exc:
        print("[ERROR] " + translate_text(str(exc)), file=sys.stderr, flush=True)
        raise SystemExit(1) from None

