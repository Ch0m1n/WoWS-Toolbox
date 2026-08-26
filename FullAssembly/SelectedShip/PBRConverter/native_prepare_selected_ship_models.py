#!/usr/bin/env python3
"""Prepare selected Legends ship component OBJs without launching Blender."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

import batch_selected_ship_models as base


class NativeBatchError(RuntimeError):
    pass


DEFAULT_WORKERS = 2
DEFAULT_MODEL_TIMEOUT_SECONDS = 300.0
STATUS_INTERVAL_SECONDS = 15.0
TIMEOUT_EXIT_CODE = 124


def emit_progress(percent: int, message: str) -> None:
    print(
        "[PROGRESS] "
        + json.dumps(
            {"stage": "extract", "percent": percent, "message": message},
            ensure_ascii=False,
        ),
        flush=True,
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def engine_fingerprint(converter: Path, decoder_root: Path) -> str:
    digest = hashlib.sha256(b"wows-toolbox-native-obj-prepare/v1\0")
    for path in sorted(
        [converter, Path(__file__).resolve(), *decoder_root.glob("*.py")],
        key=lambda item: str(item).casefold(),
    ):
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--extracted-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--decoder-root",
        type=Path,
        default=base._default_decoder_root(here),
    )
    parser.add_argument(
        "--converter",
        type=Path,
        default=base._default_converter(here),
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--model-timeout-seconds",
        type=float,
        default=DEFAULT_MODEL_TIMEOUT_SECONDS,
        help="maximum wall-clock time for one component converter process",
    )
    return parser.parse_args(argv)


def _process_creation_flags() -> int:
    """Keep CPU-heavy converters from starving the Windows desktop shell."""

    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_converter(
    command: Sequence[str], timeout_seconds: float
) -> tuple[int, str, str, bool]:
    """Run one converter with a hard timeout and reap it on expiry."""

    try:
        run = subprocess.run(
            list(command),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            creationflags=_process_creation_flags(),
        )
        return run.returncode, run.stdout, run.stderr, False
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
        detail = (
            f"converter timed out after {timeout_seconds:g}s and was terminated"
        )
        stderr = f"{stderr.rstrip()}\n{detail}\n" if stderr else detail + "\n"
        return TIMEOUT_EXIT_CODE, stdout, stderr, True


def emit_batch_event(event: str, **fields: Any) -> None:
    print(
        "[NATIVE_BATCH] "
        + json.dumps({"event": event, **fields}, ensure_ascii=False),
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv)
    if args.workers < 1 or args.workers > 8:
        raise NativeBatchError("--workers must be between 1 and 8")

    if args.model_timeout_seconds <= 0 or args.model_timeout_seconds > 3600:
        raise NativeBatchError(
            "--model-timeout-seconds must be greater than 0 and at most 3600"
        )
    mapping_path = args.mapping.resolve()
    extracted_root = args.extracted_root.resolve()
    output_root = args.output_root.resolve()
    decoder_root = args.decoder_root.resolve()
    converter = args.converter.resolve()
    if not mapping_path.is_file():
        raise NativeBatchError(f"mapping not found: {mapping_path}")
    if not extracted_root.is_dir():
        raise NativeBatchError(f"extracted root not found: {extracted_root}")
    if not converter.is_file():
        raise NativeBatchError(f"converter not found: {converter}")

    mapping = base._load_json(mapping_path)
    base._require_accepted_mapping(mapping)
    uses = base.collect_used_models(mapping)
    records = base._model_records(mapping)
    mapping_sha256 = base._sha256(mapping_path)
    fingerprint_cache: dict[Path, dict[str, Any]] = {}
    output_root.mkdir(parents=True, exist_ok=True)
    shared_textures = output_root / "_shared_textures"
    prepared: list[dict[str, Any]] = []
    required_textures: set[str] = set()

    for use in uses:
        model_path = use["model_path"]
        model_record = records.get(model_path)
        if model_record is None:
            raise NativeBatchError(f"used model record missing: {model_path}")
        output_key = base._safe_output_key(model_path)
        model_output = output_root / output_key
        manifest = base.make_manifest(
            use, model_record, extracted_root, fingerprint_cache
        )
        manifest_path = model_output / f"{output_key}.manifest.json"
        write_json(manifest_path, manifest)
        for model in manifest["models"]:
            for render_set in model["render_sets"]:
                required_textures.update(render_set["texture_maps"].values())
        prepared.append(
            {
                "use": use,
                "model_path": model_path,
                "output_key": output_key,
                "model_output": model_output,
                "manifest": manifest,
                "manifest_path": manifest_path,
            }
        )

    emit_progress(24, f"Blender 없이 함선 부품 {len(prepared)}개를 준비하는 중")

    state_lock = threading.Lock()
    active: dict[str, dict[str, Any]] = {}
    started_count = 0

    def prepare(item: dict[str, Any]) -> tuple[int, str, str, bool, float]:
        nonlocal started_count
        item["model_output"].mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-B",
            str(converter),
            str(item["manifest_path"]),
            "--output-dir",
            str(item["model_output"]),
            "--name",
            item["output_key"],
            "--decoder-root",
            str(decoder_root),
            "--no-blend",
            "--shared-texture-dir",
            str(shared_textures),
            "--skip-blender",
        ]
        component_started = time.monotonic()
        with state_lock:
            started_count += 1
            sequence = started_count
            active[item["output_key"]] = {
                "model_path": item["model_path"],
                "sequence": sequence,
                "started": component_started,
            }
        emit_batch_event(
            "component_start",
            sequence=sequence,
            total=len(prepared),
            model_path=item["model_path"],
            timeout_seconds=args.model_timeout_seconds,
        )
        try:
            code, stdout, stderr, timed_out = run_converter(
                command, args.model_timeout_seconds
            )
        except Exception as exc:
            code = -1
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}\n"
            timed_out = False
        elapsed = time.monotonic() - component_started
        log_path = item["model_output"] / f"{item['output_key']}.native.log"
        log_path.write_text(
            stdout + "\n--- STDERR ---\n" + stderr,
            encoding="utf-8",
        )
        with state_lock:
            active.pop(item["output_key"], None)
        emit_batch_event(
            "component_complete",
            sequence=sequence,
            total=len(prepared),
            model_path=item["model_path"],
            returncode=code,
            timed_out=timed_out,
            elapsed_seconds=round(elapsed, 3),
            log=str(log_path),
        )
        return code, stdout, stderr, timed_out, elapsed

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    completed = 0

    def collect_result(
        future: concurrent.futures.Future[tuple[int, str, str, bool, float]],
        item: dict[str, Any],
    ) -> None:
        nonlocal completed
        error = None
        try:
            code, _stdout, stderr, timed_out, _elapsed = future.result()
            if timed_out:
                error = (
                    f"converter timeout after "
                    f"{args.model_timeout_seconds:g}s: {item['model_path']}"
                )
            elif code:
                error = f"converter exit {code}: {stderr.strip()}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        completed += 1
        emit_progress(
            24 + round(42 * completed / max(1, len(prepared))),
            f"Blender 없는 부품 변환 {completed}/{len(prepared)}",
        )

        key = item["output_key"]
        model_output = item["model_output"]
        obj = model_output / f"{key}.obj"
        mtl = model_output / f"{key}.mtl"
        material_manifest = model_output / f"{key}.blender-input.json"
        validation_path = model_output / f"{key}.validation.json"
        record: dict[str, Any] = {
            "categories": item["use"]["categories"],
            "primary_category": item["use"]["primary_category"],
            "references": item["use"]["references"],
            "model_path": item["model_path"],
            "output_key": key,
            "manifest": str(item["manifest_path"]),
            "output_obj": str(obj),
            "output_mtl": str(mtl),
            "material_manifest": str(material_manifest),
            "validation": str(validation_path),
            "selected_render_sets": item["manifest"]["source"][
                "selected_render_sets"
            ],
        }
        if error is None:
            try:
                validation = base._load_json(validation_path)
                material_data = base._load_json(material_manifest)
                accepted = (
                    obj.is_file()
                    and obj.stat().st_size > 0
                    and mtl.is_file()
                    and validation.get("status") == "OBJ_ONLY"
                    and validation.get("missing_render_sets") == 0
                    and validation.get("matched_render_sets")
                    == record["selected_render_sets"]
                    and isinstance(material_data.get("materials"), list)
                    and isinstance(material_data.get("objects"), list)
                )
                if not accepted:
                    error = "native component OBJ acceptance failed"
                else:
                    record.update(
                        status="OK",
                        matched_render_sets=validation["matched_render_sets"],
                        missing_render_sets=0,
                        missing_maps=len(validation.get("missing_maps", [])),
                        output_obj_bytes=obj.stat().st_size,
                        output_obj_sha256=base._sha256(obj),
                        material_count=len(material_data["materials"]),
                        material_policy_passed=True,
                    )
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                json.JSONDecodeError,
            ) as exc:
                error = f"native validation error: {exc}"
        if error is not None:
            record.update(status="FAILED", error=error)
            failures.append(record)
        results.append(record)

    worker_count = max(1, min(args.workers, len(prepared)))
    emit_batch_event(
        "batch_start",
        models=len(prepared),
        workers=worker_count,
        model_timeout_seconds=args.model_timeout_seconds,
        process_priority=("below_normal" if os.name == "nt" else "normal"),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(prepare, item): item for item in prepared}
        pending = set(futures)
        while pending:
            done, pending = concurrent.futures.wait(
                pending,
                timeout=STATUS_INTERVAL_SECONDS,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if done:
                for future in done:
                    collect_result(future, futures[future])
                continue

            now = time.monotonic()
            with state_lock:
                active_snapshot = [dict(value) for value in active.values()]
            active_snapshot.sort(key=lambda value: value["started"])
            oldest = active_snapshot[0] if active_snapshot else None
            oldest_seconds = (
                round(now - float(oldest["started"]), 1) if oldest else 0.0
            )
            oldest_model = str(oldest["model_path"]) if oldest else None
            emit_batch_event(
                "batch_heartbeat",
                completed=completed,
                total=len(prepared),
                active=len(active_snapshot),
                oldest_active_seconds=oldest_seconds,
                oldest_model=oldest_model,
            )
            detail = (
                f" · 최장 {oldest_seconds:g}초: {Path(oldest_model).name}"
                if oldest_model
                else ""
            )
            emit_progress(
                24 + round(42 * completed / max(1, len(prepared))),
                (
                    f"Blender 없는 부품 변환 {completed}/{len(prepared)}"
                    f" · 실행 중 {len(active_snapshot)}개{detail}"
                ),
            )

    results.sort(key=lambda item: item["model_path"].casefold())
    logical_paths = []
    for logical_path in sorted(required_textures):
        source = extracted_root / Path(
            *PurePosixPath(logical_path.replace("/", "/")).parts
        )
        logical_paths.append({"path": logical_path, "present": source.is_file()})
    missing = [item["path"] for item in logical_paths if not item["present"]]
    required_path = output_root / "selected_ship_required_textures.json"
    write_json(
        required_path,
        {
            "schema": "wows-legends-selected-ship-required-textures/v1",
            "source_mapping": str(mapping_path),
            "source_mapping_sha256": mapping_sha256,
            "extracted_root": str(extracted_root),
            "logical_paths": logical_paths,
            "count": len(logical_paths),
            "missing": missing,
        },
    )

    accepted = not failures and not missing and len(results) == len(uses)
    summary = {
        "schema": "wows-legends-selected-ship-native-obj-batch/v1",
        "mode": "native_obj",
        "source_mapping": str(mapping_path),
        "source_mapping_sha256": mapping_sha256,
        "conversion_engine_fingerprint": engine_fingerprint(converter, decoder_root),
        "workers": worker_count,
        "model_timeout_seconds": args.model_timeout_seconds,
        "mapping_static_assembly_accepted": True,
        "extracted_root": str(extracted_root),
        "output_root": str(output_root),
        "required_texture_paths": str(required_path),
        "required_texture_count": len(logical_paths),
        "required_texture_missing": missing,
        "model_count": len(uses),
        "expected_models": len(uses),
        "result_models": len(results),
        "strict_validation": {
            "accepted": accepted,
            "passed_models": sum(item["status"] == "OK" for item in results),
            "failed_models": len(failures),
            "total_selected_render_sets": sum(
                int(item["selected_render_sets"]) for item in results
            ),
            "total_matched_render_sets": sum(
                int(item.get("matched_render_sets", 0)) for item in results
            ),
            "total_missing_render_sets": sum(
                int(item.get("missing_render_sets", 0)) for item in results
            ),
            "total_missing_maps": sum(
                int(item.get("missing_maps", 0)) for item in results
            ),
        },
        "timings_seconds": {"total": round(time.perf_counter() - started, 3)},
        "results": results,
    }
    summary_path = output_root / "selected_ship_pbr_models.summary.json"
    write_json(summary_path, summary)
    print(json.dumps(summary["strict_validation"], ensure_ascii=False, indent=2))
    print(summary_path)
    if not accepted:
        raise NativeBatchError(
            f"native OBJ preparation failed: models={len(failures)}, textures={len(missing)}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (NativeBatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None