from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
import json
import math
import os
import shutil
import statistics
import sys
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

from blitz_assets import resolve_blitz_layout  # noqa: E402
from blitz_extract import extract_blitz  # noqa: E402
from extract_ship import (  # noqa: E402
    extract_legends,
    extract_pc_family,
    latest_build,
    next_output_dir,
    safe_name,
    ship_output_stem,
    validate_camouflage_selection,
    validate_camouflage_color_scheme,
    validate_output_child,
    validate_output_location,
)
from runtime_i18n import translate_payload, translate_text  # noqa: E402


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def emit(kind: str, payload: dict) -> None:
    print(f"[{kind}] " + json.dumps(translate_payload(payload), ensure_ascii=False), flush=True)


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


def available_memory() -> int | None:
    if os.name != "nt":
        return None
    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.available_physical)
    return None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.part")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def control_state(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"paused": False, "cancel": False}
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "paused": bool(payload.get("paused")),
        "cancel": bool(payload.get("cancel")),
    }


def wait_for_control(path: Path | None) -> bool:
    announced = False
    while True:
        state = control_state(path)
        if state["cancel"]:
            emit("BATCH", {"event": "cancel_requested"})
            return False
        if not state["paused"]:
            if announced:
                emit("BATCH", {"event": "resumed"})
            return True
        if not announced:
            emit("BATCH", {"event": "paused", "message": "현재 함선 뒤에서 대기열을 일시 정지했어요"})
            announced = True
        time.sleep(0.25)


def history_estimate(history: dict, source: str) -> tuple[float, int]:
    rows = history.get(source, [])
    if not isinstance(rows, list):
        rows = []
    seconds: list[float] = []
    sizes: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            elapsed = float(row.get("seconds", 0))
        except (TypeError, ValueError, OverflowError):
            elapsed = 0.0
        if math.isfinite(elapsed) and elapsed > 0:
            seconds.append(elapsed)
        try:
            size = int(row.get("bytes", 0))
        except (TypeError, ValueError, OverflowError):
            size = 0
        if size > 0:
            sizes.append(size)
    default_seconds = {"legends": 480.0, "pc": 75.0, "korabli": 80.0, "blitz": 90.0}.get(source, 90.0)
    default_bytes = {"legends": 1_200_000_000, "pc": 800_000_000, "korabli": 800_000_000, "blitz": 250_000_000}.get(source, 800_000_000)
    return (
        statistics.median(seconds[-12:]) if seconds else default_seconds,
        int(statistics.median(sizes[-12:])) if sizes else default_bytes,
    )


def directory_size(root: Path) -> int:
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    except OSError:
        pass
    return total


def item_namespace(common: dict, item: dict) -> SimpleNamespace:
    def p(value) -> Path | None:
        return Path(value).resolve() if value else None

    source = str(item["source"])
    ship_key = str(item.get("ship_key", ""))
    run_slug = str(item.get("run_slug", ""))
    if source == "legends" and not run_slug:
        variant = str(item.get("ship_resource", "")) or Path(str(item.get("selected_model_path", ""))).stem
        run_slug = safe_name(f"{ship_key}_{variant or ship_key}")
    requested_lod = int(common.get("lod", 0))
    requested_texture_max_size = int(common.get("texture_max_size", 0))

    return SimpleNamespace(
        source=source,
        game_dir=p(item["game_dir"]),
        toolbox_root=p(common["toolbox_root"]),
        output_root=p(common["output_root"]),
        ship_key=ship_key,
        selected_model_path=str(item.get("selected_model_path", "")),
        ship_resource=str(item.get("ship_resource", "")),
        run_slug=run_slug,
        ship_index=str(item.get("ship_index", "")),
        hull_upgrade=str(item.get("hull_upgrade", "")),
        camouflage=validate_camouflage_selection(
            item.get("camouflage", common.get("camouflage", "default"))
        ),
        display_name=str(item.get("display_name", "")),
        camouflage_color_scheme=validate_camouflage_color_scheme(
            item.get("camouflage_color_scheme", common.get("camouflage_color_scheme", ""))
        ),
        oodle_dll=p(common.get("oodle_dll")),
        cache_root=p(common.get("cache_root")),
        overwrite=bool(common.get("overwrite", False)),
        keep_glb=bool(common.get("keep_glb", False)),
        requested_lod=requested_lod,
        lod=0 if source == "legends" else requested_lod,
        formats=str(common.get("formats", "obj")),
        requested_texture_max_size=requested_texture_max_size,
        texture_max_size=(
            0 if source == "legends" else requested_texture_max_size
        ),
        prefetch_event=threading.Event(),
        promoted_event=threading.Event(),
        cancel_check=lambda: False,
    )


def output_for(args: SimpleNamespace, reserved: set[str]) -> Path:
    if args.source == "legends":
        base_slug = safe_name(args.run_slug or args.ship_key)
        run_slug = base_slug
        candidate = args.output_root / f"{run_slug}_Full"
        serial = 2
        while ((candidate.exists() and not args.overwrite) or
               str(candidate).casefold() in reserved):
            run_slug = f"{base_slug}_{serial:02d}"
            candidate = args.output_root / f"{run_slug}_Full"
            serial += 1
        args.run_slug = run_slug
        reserved.add(str(candidate).casefold())
        return candidate

    stem = ship_output_stem(
        args.display_name,
        args.ship_index,
        args.ship_key,
        args.camouflage,
        args.camouflage_color_scheme,
    )
    candidate = next_output_dir(args.output_root, stem, args.overwrite)
    serial = 2
    while str(candidate).casefold() in reserved:
        candidate = args.output_root / f"{stem}_{serial:02d}"
        serial += 1
    reserved.add(str(candidate).casefold())
    return candidate


def compatibility(args: SimpleNamespace, previous: dict | None = None) -> dict:
    result = {
        "schema": "wows-toolbox-compatibility/v2",
        "source": args.source,
        "game_dir": str(args.game_dir),
        "ok": args.game_dir.is_dir(),
        "build": None,
        "build_changed": False,
        "idx_count": 0,
        "package_count": 0,
        "message": "",
    }
    if not result["ok"]:
        result["message"] = "게임 폴더가 없어요"
        return result
    try:
        if args.source in {"pc", "korabli"}:
            build, build_dir = latest_build(args.game_dir)
            idx_dir = build_dir / "idx"
            idx_files = list(idx_dir.glob("*.idx")) if idx_dir.is_dir() else []
            package_root = args.game_dir / "res_packages"
            packages = list(package_root.glob("*.pkg")) if package_root.is_dir() else []
            result["build"] = build
            result["idx_count"] = len(idx_files)
            result["package_count"] = len(packages)
            result["ok"] = bool(idx_files) and bool(packages)
            result["message"] = (
                f"IDX {len(idx_files)}개·패키지 {len(packages)}개 구조 확인 완료"
                if result["ok"]
                else "IDX 또는 패키지 파일을 찾지 못했어요"
            )
        elif args.source == "blitz":
            layout = resolve_blitz_layout(args.game_dir)
            body_files = list(layout.body_root.glob("*.ab"))
            marker = layout.bundle_root / "BundlePackInfo.bytes"
            result["build"] = (
                int(marker.stat().st_mtime)
                if marker.is_file()
                else int(layout.body_root.stat().st_mtime)
            )
            result["package_count"] = len(body_files)
            result["ok"] = bool(body_files) and layout.obb_path is not None
            result["message"] = (
                f"Blitz body {len(body_files)}개·기본 OBB 구조 확인 완료"
                if result["ok"]
                else "Blitz body 또는 기본 OBB를 찾지 못했어요"
            )
        else:
            executable = args.game_dir / "WorldOfWarshipsLegends.exe"
            package_root = args.game_dir / "res_packages"
            packages = list(package_root.glob("*.pkg")) if package_root.is_dir() else []
            result["package_count"] = len(packages)
            result["build"] = int(executable.stat().st_mtime) if executable.is_file() else None
            result["ok"] = executable.is_file() and bool(packages)
            result["message"] = (
                f"Legends 실행 파일·패키지 {len(packages)}개 확인 완료"
                if result["ok"]
                else "Legends 실행 파일 또는 패키지가 없어요"
            )
        if previous:
            old_build = previous.get("build")
            result["build_changed"] = old_build is not None and old_build != result["build"]
            if result["build_changed"] and result["ok"]:
                result["message"] += " · 게임 빌드 변경 감지, 새 캐시로 검증해요"
    except Exception as exc:
        result["ok"] = False
        result["message"] = str(exc)
    return result


def item_contract(args: SimpleNamespace, index: int) -> dict:
    errors: list[str] = []
    if args.source == "legends":
        if getattr(args, "camouflage", "default") != "default":
            errors.append("Legends 영구 위장 추출은 아직 지원하지 않아요")
        if not args.ship_key:
            errors.append("Legends 함선 키가 비어 있어요")
        if getattr(args, "camouflage_color_scheme", ""):
            errors.append("Legends 영구 위장 색상표 추출은 아직 지원하지 않아요")
        model_path = args.selected_model_path.replace("\\", "/").strip("/")
        if model_path:
            segments = [segment for segment in model_path.split("/") if segment]
            if ".." in segments or not model_path.casefold().startswith(
                "content/gameplay/"
            ):
                errors.append("Legends 선택 모델 경로가 안전하지 않아요")
        pipeline = (
            args.toolbox_root
            / "FullAssembly"
            / "SelectedShip"
            / "Pipeline"
            / "extract_selected_ship_full.py"
        )
        if not pipeline.is_file():
            errors.append("Legends 추출 파이프라인이 없어요")
    elif args.source == "blitz":
        if not args.ship_index:
            errors.append("Blitz 함선 식별자가 비어 있어요")
        model_path = args.selected_model_path.replace("\\", "/").strip("/")
        segments = [segment for segment in model_path.split("/") if segment]
        if ".." in segments or not model_path.casefold().startswith("prefab/ship/body/"):
            errors.append("Blitz 선택 body 경로가 안전하지 않아요")
        if getattr(args, "camouflage_color_scheme", ""):
            errors.append("Blitz 도색 색상표 선택은 지원하지 않아요")
        if not (args.toolbox_root / "Backend" / "blitz_extract.py").is_file():
            errors.append("Blitz 추출 엔진이 없어요")
    else:
        if not args.ship_index:
            errors.append("함선 IDX 식별자가 비어 있어요")
        if ".." in args.hull_upgrade or any(
            separator in args.hull_upgrade for separator in ("/", "\\")
        ):
            errors.append("선체 업그레이드 식별자가 안전하지 않아요")
        for engine_name in ("wowsunpack.exe", "native_glb_export.py"):
            if not (args.toolbox_root / "Backend" / engine_name).is_file():
                errors.append(f"추출 엔진이 없어요: {engine_name}")
    return {
        "schema": "wows-toolbox-item-contract/v1",
        "index": index,
        "source": args.source,
        "name": args.display_name,
        "ok": not errors,
        "message": "선택 함선 계약 확인 완료" if not errors else " / ".join(errors),
    }


def run_one(args: SimpleNamespace, output_dir: Path) -> dict:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.source in {"legends", "blitz"}:
        args.prefetch_event.set()
    if args.source == "legends":
        return extract_legends(args, output_dir)
    if args.source == "blitz":
        return extract_blitz(args, output_dir)
    return extract_pc_family(args, output_dir)


def update_history(history: dict, source: str, seconds: float, result: dict) -> None:
    output = Path(result.get("output_dir", ""))
    size = directory_size(output) if output.is_dir() else 0
    rows = history.get(source)
    if not isinstance(rows, list):
        rows = []
        history[source] = rows
    rows.append({"seconds": round(seconds, 3), "bytes": size, "time": int(time.time())})
    del rows[:-30]


def main() -> int:
    parser = argparse.ArgumentParser(description="WoWS Toolbox persistent queue extractor")
    parser.add_argument("--manifest", type=Path, required=True)
    args_cli = parser.parse_args()
    manifest_path = args_cli.manifest.resolve()
    manifest = load_json(manifest_path, None)
    if not isinstance(manifest, dict):
        raise ValueError("대기열 매니페스트를 읽지 못했어요")
    common = manifest.get("common", {})
    items = manifest.get("items", [])
    if not isinstance(common, dict):
        raise ValueError("대기열 공통 설정 형식이 잘못됐어요")
    if not isinstance(items, list) or not items:
        raise ValueError("대기열이 비어 있어요")

    output_root = Path(common["output_root"]).resolve()
    state_root = Path(common.get("state_root") or manifest_path.parent).resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    control_file = Path(common["control_file"]).resolve() if common.get("control_file") else None
    history_path = state_root / "batch-history.json"
    compatibility_path = state_root / "compatibility-history.json"
    history = load_json(history_path, {})
    compatibility_history = load_json(compatibility_path, {})
    if not isinstance(history, dict):
        history = {}
    if not isinstance(compatibility_history, dict):
        compatibility_history = {}
    namespaces = [item_namespace(common, item) for item in items]
    for item in namespaces:
        item.game_dir, item.output_root = validate_output_location(
            item.game_dir, output_root
        )
    output_root.mkdir(parents=True, exist_ok=True)
    for item in namespaces:
        item.cancel_check = (
            lambda path=control_file: bool(control_state(path)["cancel"])
        )
    namespaces[0].promoted_event.set()
    reserved: set[str] = set()
    outputs = [
        validate_output_child(output_root, output_for(item, reserved))
        for item in namespaces
    ]

    item_contract_rows = [
        item_contract(item, index)
        for index, item in enumerate(namespaces, start=1)
    ]
    for row in item_contract_rows:
        emit("CONTRACT", row)
    broken_contracts = [row for row in item_contract_rows if not row["ok"]]
    if broken_contracts:
        raise RuntimeError(
            "선택 함선 사전 검사를 통과하지 못했어요: "
            + " / ".join(row["message"] for row in broken_contracts)
        )

    compatibility_rows = []
    seen_sources: set[tuple[str, str]] = set()
    for item in namespaces:
        key = (item.source, str(item.game_dir).casefold())
        if key in seen_sources:
            continue
        seen_sources.add(key)
        history_key = f"{item.source}|{str(item.game_dir).casefold()}"
        previous = compatibility_history.get(history_key)
        row = compatibility(item, previous)
        compatibility_rows.append(row)
        compatibility_history[history_key] = row
        emit("COMPAT", row)
    save_json(compatibility_path, compatibility_history)
    broken = [row for row in compatibility_rows if not row["ok"]]
    if broken:
        raise RuntimeError("게임 호환성 검사를 통과하지 못했어요: " + " / ".join(row["message"] for row in broken))

    estimated_seconds = sum(history_estimate(history, item.source)[0] for item in namespaces)
    estimated_bytes = sum(history_estimate(history, item.source)[1] for item in namespaces)
    disk = shutil.disk_usage(output_root)
    memory = available_memory()
    preflight = {
        "event": "preflight",
        "items": len(items),
        "item_contracts_ok": all(row["ok"] for row in item_contract_rows),
        "estimated_seconds": round(estimated_seconds),
        "estimated_bytes": estimated_bytes,
        "free_bytes": disk.free,
        "available_memory": memory,
        "disk_ok": disk.free >= int(estimated_bytes * 1.2),
        "memory_ok": memory is None or memory >= 2 * 1024**3,
    }
    emit("BATCH", preflight)
    if not preflight["disk_ok"]:
        raise RuntimeError("예상 출력 용량보다 디스크 여유 공간이 부족해요")
    if not preflight["memory_ok"]:
        emit(
            "BATCH",
            {
                "event": "warning",
                "message": "사용 가능한 메모리가 2GB보다 적어요. 다른 프로그램을 닫아 주세요",
            },
        )

    started = time.perf_counter()
    successes: list[dict] = []
    failures: list[dict] = []
    emit("BATCH", {"event": "start", "count": len(items), "prefetch": True})

    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wows-queue")
    current_index = 0
    launch_times: dict[int, float] = {0: time.perf_counter()}
    current_future: Future = executor.submit(run_one, namespaces[0], outputs[0])
    prefetched: tuple[int, Future] | None = None
    try:
        while current_index < len(items):
            current_args = namespaces[current_index]
            current_output = outputs[current_index]
            item_started = launch_times.get(current_index, time.perf_counter())
            emit("BATCH", {
                "event": "item_start",
                "index": current_index + 1,
                "count": len(items),
                "name": current_args.display_name,
                "source": current_args.source,
                "output_dir": str(current_output),
            })

            while not current_args.prefetch_event.wait(0.1) and not current_future.done():
                if control_state(control_file)["cancel"]:
                    break
            next_index = current_index + 1
            if (
                next_index < len(items)
                and current_args.source not in {"legends", "blitz"}
                and namespaces[next_index].source not in {"legends", "blitz"}
                and not control_state(control_file)["cancel"]
            ):
                emit("BATCH", {
                    "event": "prefetch",
                    "index": next_index + 1,
                    "name": namespaces[next_index].display_name,
                })
                launch_times[next_index] = time.perf_counter()
                prefetched = (
                    next_index,
                    executor.submit(run_one, namespaces[next_index], outputs[next_index]),
                )

            try:
                result = current_future.result()
                elapsed = time.perf_counter() - item_started
                update_history(history, current_args.source, elapsed, result)
                successes.append({"index": current_index, "result": result, "seconds": elapsed})
                remaining = namespaces[current_index + 1 :]
                eta = sum(history_estimate(history, item.source)[0] for item in remaining)
                emit("BATCH", {
                    "event": "item_complete",
                    "index": current_index + 1,
                    "count": len(items),
                    "name": current_args.display_name,
                    "seconds": round(elapsed, 3),
                    "eta_seconds": round(eta),
                    "result": result,
                })
            except Exception as exc:
                elapsed = time.perf_counter() - item_started
                failure = {
                    "index": current_index + 1,
                    "name": current_args.display_name,
                    "source": current_args.source,
                    "seconds": round(elapsed, 3),
                    "error_type": type(exc).__name__,
                    "message": translate_text(str(exc)),
                    "traceback": traceback.format_exc(),
                }
                failures.append(failure)
                emit("BATCH", {"event": "item_failed", **{k: v for k, v in failure.items() if k != "traceback"}})

            save_json(history_path, history)
            if not wait_for_control(control_file):
                break
            if prefetched is not None and prefetched[0] == current_index + 1:
                namespaces[prefetched[0]].promoted_event.set()
                current_index, current_future = prefetched
                prefetched = None
            else:
                current_index += 1
                if current_index >= len(items):
                    break
                launch_times[current_index] = time.perf_counter()
                current_future = executor.submit(
                    run_one,
                    namespaces[current_index],
                    outputs[current_index],
                )
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    report = {
        "schema": "wows-toolbox-batch/v1",
        "ok": not failures,
        "cancelled": bool(control_state(control_file)["cancel"]),
        "manifest": str(manifest_path),
        "started": int(time.time() - (time.perf_counter() - started)),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "success_count": len(successes),
        "failure_count": len(failures),
        "successes": successes,
        "failures": failures,
        "compatibility": compatibility_rows,
        "item_contracts": item_contract_rows,
        "preflight": preflight,
    }
    report_path = state_root / "last-batch-report.json"
    save_json(report_path, report)
    emit("BATCH", {
        "event": "summary",
        "ok": report["ok"],
        "cancelled": report["cancelled"],
        "success_count": len(successes),
        "failure_count": len(failures),
        "elapsed_seconds": report["elapsed_seconds"],
        "report": str(report_path),
    })
    return 0 if not failures else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit("BATCH", {
            "event": "fatal",
            "error_type": type(exc).__name__,
            "message": translate_text(str(exc)),
        })
        print("[ERROR] " + translate_text(str(exc)), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
