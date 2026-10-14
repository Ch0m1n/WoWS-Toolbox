"""Local execution plans, bounded retry guidance, journals and safe publishing."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from output_provenance import atomic_json, digest, revalidate, safe_child


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def source_identity(args) -> dict:
    root = args.game_dir
    identity = {"game": str(root).casefold(), "source": args.source, "build": None, "files": []}
    if args.source in {"pc", "korabli"}:
        from game_archive import latest_build
        build, directory = latest_build(root)
        identity["build"] = build
        files = sorted((directory / "idx").glob("*.idx"))
    elif args.source == "blitz":
        from blitz_assets import resolve_blitz_layout
        layout = resolve_blitz_layout(root)
        files = [layout.bundle_root / "BundlePackInfo.bytes"] + sorted(layout.body_root.glob("*.ab"))
        if layout.obb_path:
            files.append(layout.obb_path)
    else:
        files = [root / "WorldOfWarshipsLegends.exe"] + sorted((root / "res_packages").glob("*.pkg"))
    for path in files:
        if path.is_file():
            identity["files"].append([str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns])
    identity["tools"] = {name: digest(args.toolbox_root / "Backend" / name) for name in
                         ("wowsunpack.exe", "native_glb_export.py", "blitz_extract.py")
                         if (args.toolbox_root / "Backend" / name).is_file()}
    return identity


def request_identity(args, source=None) -> str:
    return fingerprint({"source": source if source is not None else source_identity(args),
                        "selection": {key: getattr(args, key, None) for key in
                                      ("ship_index", "ship_key", "selected_model_path", "ship_resource", "hull_upgrade")},
                        "options": {key: getattr(args, key, None) for key in
                                    ("formats", "lod", "texture_max_size", "camouflage", "camouflage_color_scheme")}})


def retry_guidance(error: Exception) -> dict:
    text = str(error).casefold()
    code, action = "extraction_failed", "inspect_log_then_retry"
    if isinstance(error, PermissionError):
        code, action = "file_locked_or_denied", "close_editor_check_permissions"
    elif isinstance(error, FileNotFoundError):
        code, action = "missing_component_or_path", "check_paths_and_components"
    elif isinstance(error, OSError) and getattr(error, "errno", None) == 28 or "disk" in text or "디스크" in text:
        code, action = "insufficient_disk", "free_space_then_replan"
    elif "unsupported" in text or "지원하지" in text or "미지원" in text:
        code, action = "unsupported_request", "change_supported_options"
    elif "validation failed" in text or "검증" in text:
        code, action = "output_validation_failed", "inspect_manifest_then_reexport"
    elif "oodle" in text or "복원" in text:
        code, action = "decode_failed", "verify_game_resources_and_decoder"
    elif "identity changed" in text:
        code, action = "conditions_changed", "recalculate_plan"
    return {"code": code, "action": action, "automatic_retry": False,
            "resume_partial_files": False}


def estimate_basis(history, source):
    from batch_extract import history_estimate
    seconds, size = history_estimate(history, source)
    samples = []
    for row in history.get(source, []) if isinstance(history.get(source, []), list) else []:
        try:
            value = float(row.get("seconds", 0))
            if math.isfinite(value) and value > 0:
                samples.append(value)
        except (TypeError, ValueError, AttributeError):
            pass
    samples = samples[-12:]
    return {"seconds": seconds, "bytes": size, "sample_count": len(samples),
            "basis": "recent_median" if samples else "default",
            "observed_seconds_range": [min(samples), max(samples)] if len(samples) >= 5 else None,
            "cache_conditions_accounted": False, "temporary_space_accounted": False}


def verified_existing(path, identity):
    manifest = path / "extraction.manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data.get("request_identity") == identity and revalidate(manifest)["status"] != "failed"
    except (OSError, ValueError, KeyError, TypeError):
        return False


def make_plan(manifest: dict, history: dict | None = None, failures: list | None = None) -> tuple[dict, list]:
    from batch_extract import item_namespace, output_for, item_contract, compatibility
    from extract_ship import validate_output_location, validate_output_child
    common, items = manifest["common"], manifest["items"]
    if not isinstance(common, dict) or not isinstance(items, list) or not items:
        raise ValueError("Invalid or empty batch request")
    history = history if isinstance(history, dict) else {}
    failures = failures if isinstance(failures, list) else []
    policy = common.get("output_policy", "replace" if common.get("overwrite") else "new")
    if policy not in {"new", "replace", "skip_verified"}:
        raise ValueError("Unsupported output policy")
    rows, args_list, reserved, identities = [], [], set(), {}
    for index, item in enumerate(items, 1):
        args = item_namespace(common, item)
        args.game_dir, args.output_root = validate_output_location(args.game_dir, args.output_root)
        args.output_policy = policy
        contract = item_contract(args, index)
        key = (args.source, str(args.game_dir))
        if key not in identities:
            compat = compatibility(args)
            identities[key] = (compat, source_identity(args) if compat["ok"] else None)
        compat, source = identities[key]
        identity = request_identity(args, source) if source is not None else None
        args.overwrite = policy != "new"
        output = output_for(args, reserved)
        action = "replace" if policy == "replace" and output.exists() else "new"
        if policy == "skip_verified" and output.exists():
            if verified_existing(output, identity):
                action = "skip_verified"
            else:
                reserved.discard(str(output).casefold())
                args.overwrite = False
                output = output_for(args, reserved)
        output = validate_output_child(args.output_root, output)
        args.planned_output = output
        args.source_identity = source
        args.request_identity = identity
        args.output_action = action
        blockers = ([] if contract["ok"] else [contract["message"]]) + ([] if compat["ok"] else [compat["message"]])
        rows.append({"index": index, "source": args.source, "name": args.display_name,
                     "build": compat["build"] if args.source in {"pc", "korabli"} else None,
                     "request_identity": identity, "source_identity": fingerprint(source),
                     "requested": {"formats": args.formats, "lod": args.requested_lod,
                                   "texture_max_size": args.requested_texture_max_size, "camouflage": args.camouflage,
                                   "camouflage_color_scheme": args.camouflage_color_scheme},
                     "applied": {"formats": args.formats, "lod": args.lod, "texture_max_size": args.texture_max_size},
                     "output": str(output), "action": action, "existing": output.exists(),
                     "cache": "unverified", "blockers": blockers, "estimate": estimate_basis(history, args.source),
                     "output_stamp": output.stat().st_mtime_ns if output.exists() else None,
                     "existing_files": output_inventory(output)})
        args.output_inventory = rows[-1]["existing_files"]
        prior = next((row for row in (failures or []) if row.get("source") == args.source
                      and row.get("ship_index") == args.ship_index and row.get("ship_key") == args.ship_key
                      and row.get("model_path") == args.selected_model_path), None)
        if prior:
            rows[-1]["retry"] = {"guidance": prior.get("guidance"), "previous_requested": prior.get("requested"),
                                  "settings_changed": prior.get("requested") != rows[-1]["requested"],
                                  "conditions_changed": prior.get("request_identity") != identity}
        args_list.append(args)
    plan = {"schema": "wows-toolbox-plan/v1", "items": rows,
            "ok": not any(row["blockers"] for row in rows), "output_policy": policy}
    plan["fingerprint"] = fingerprint(plan)
    return plan, args_list


def output_inventory(output: Path) -> list:
    if not output.exists():
        return []
    result = []
    for path in output.rglob('*'):
        safe_child(output.parent.resolve(), str(path))
        if path.is_file():
            result.append([path.relative_to(output).as_posix(), path.stat().st_size, path.stat().st_mtime_ns])
        if len(result) > 10000:
            raise ValueError("Output inventory exceeds the preview limit")
    return sorted(result)


class Journal:
    def __init__(self, root: Path, plan: dict):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "batch-journal.json"
        self.lock = root / "batch-journal.lock"
        # An active or interrupted owner cannot be overwritten by another batch.
        self.fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(self.fd, json.dumps({"pid": os.getpid(), "started": int(time.time())}).encode())
        self.data = {"schema": "wows-toolbox-journal/v1", "run": uuid.uuid4().hex,
                     "plan": plan["fingerprint"], "owner": os.getpid(),
                     "items": [dict(row, state="queued") for row in plan["items"]]}
        try:
            if self.path.is_file():
                old = json.loads(self.path.read_text(encoding="utf-8"))
                if any(row.get("state") in {"queued", "running", "interrupted"} for row in old.get("items", [])):
                    raise RuntimeError("Interrupted journal requires recovery review before a new batch")
            atomic_json(self.path, self.data)
        except Exception:
            self.close()
            raise

    def update(self, index, state, **fields):
        self.data["items"][index].update(state=state, **fields)
        atomic_json(self.path, self.data)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            self.lock.unlink(missing_ok=True)


def run_staged(args, output: Path, extractor):
    from extract_ship import validate_output_child
    if getattr(args, "output_inventory", None) is not None and output_inventory(output) != args.output_inventory:
        raise RuntimeError("Output identity changed; recalculate plan")
    if getattr(args, "source_identity", None) is not None and source_identity(args) != args.source_identity:
        raise RuntimeError("Source identity changed; recalculate plan")
    if getattr(args, "output_action", None) == "skip_verified":
        if not verified_existing(output, args.request_identity):
            raise RuntimeError("Output identity changed; recalculate plan")
        data = json.loads((output / "extraction.manifest.json").read_text(encoding="utf-8"))
        return {"output_dir": str(output), "obj": str(output / data["model"]), "skipped_verified": True,
                "provenance_manifest": str(output / "extraction.manifest.json"), "output_validation": data["validation"]}
    stage_parent = args.output_root / ".staging" / uuid.uuid4().hex
    stage = stage_parent / output.name
    stage_parent.mkdir(parents=True)
    staged = copy.copy(args)
    staged.output_root = stage_parent
    staged.overwrite = False
    try:
        result = extractor(staged, stage)
    except Exception as error:
        error.staged_output = str(stage)
        raise
    actual = Path(result["output_dir"]).resolve()
    actual.relative_to(stage_parent.resolve())
    if getattr(args, "source_identity", None) is not None and source_identity(args) != args.source_identity:
        raise RuntimeError("Source identity changed during extraction; recalculate plan")
    validate_output_child(args.output_root, output)
    if getattr(args, "output_inventory", None) is not None and output_inventory(output) != args.output_inventory:
        raise RuntimeError("Output identity changed during extraction; recalculate plan")
    backup = None
    if output.exists():
        if getattr(args, "output_action", "new") != "replace":
            raise RuntimeError("Output identity changed; recalculate plan")
        safe_child(args.output_root.resolve(), str(output.resolve()))
        backup = args.output_root / ".backups" / (output.name + "-" + uuid.uuid4().hex)
        backup.parent.mkdir(exist_ok=True)
        os.replace(output, backup)
    try:
        os.replace(actual, output)
    except OSError:
        if backup is not None:
            os.replace(backup, output)
        raise
    old_prefix = str(actual)
    def relocate(value):
        if isinstance(value, str) and (value == old_prefix or value.startswith(old_prefix + os.sep)):
            return str(output) + value[len(old_prefix):]
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [relocate(v) for v in value]
        return value
    result = relocate(result)
    if backup:
        result["previous_output_backup"] = str(backup)
    # Preserve existing report consumers after relocating a staged result.
    reports = [output / "export.json"]
    if result.get("report_file"):
        reports.append(safe_child(output.resolve(), result["report_file"]))
    for report in reports:
        if report.is_file():
            atomic_json(report, relocate(json.loads(report.read_text(encoding="utf-8"))))
    return result
