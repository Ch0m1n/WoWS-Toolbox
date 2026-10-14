"""Offline metadata tools. No exporter, download or message-sending actions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from output_provenance import atomic_json, digest, revalidate, safe_child
from workflow import fingerprint


def read_json(path: Path, limit=8 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise ValueError("Metadata file exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_snapshot(rows: list, root: Path, source: str, label="") -> Path:
    if source not in {"pc", "korabli", "legends", "blitz"} or not isinstance(rows, list) or not rows:
        raise ValueError("Invalid catalog snapshot")
    fields = ("Source", "Build", "ShipCode", "GameParamsKey", "GameParamsIndex", "ModelPath", "HullUpgrade",
              "ShipResource", "LocalizedName", "NationRaw", "ShipClassRaw", "Tier", "ShipMaterialStatus",
              "ShipMaterialDiagnostic", "ShipMaterialEvidence", "ShipMaterialIndexStamp", "ShipMaterialProbeHash")
    selected = [{key: row.get(key) for key in fields} for row in rows]
    payload = {"schema": "wows-toolbox-catalog-snapshot/v1", "source": source,
               "label": str(label)[:100], "rows": selected}
    token = fingerprint(payload)
    directory = root / "catalog-snapshots" / source
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (token + ".json")
    if not path.exists():
        payload["created"] = int(time.time())
        payload["identity"] = token
        atomic_json(path, payload)
    pins = read_json(directory / "pins.json") if (directory / "pins.json").is_file() else []
    existing = sorted((p for p in directory.glob("*.json") if p.name != "pins.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    keep = {p.name for p in existing[:10]} | set(pins) | {path.name}
    for old in existing:
        if old.name not in keep:
            safe_child(directory.resolve(), str(old))
            old.unlink()  # small JSON metadata only; never game/cache assets
    return path


def compare_snapshots(left: Path, right: Path) -> dict:
    a, b = read_json(left), read_json(right)
    if a.get("schema") != "wows-toolbox-catalog-snapshot/v1" or b.get("schema") != a["schema"]:
        raise ValueError("Unsupported snapshot schema")
    if a["source"] != b["source"]:
        raise ValueError("Cross-source comparison is unsupported")
    def keyed(rows):
        result, ambiguous = {}, []
        for row in rows:
            key = (row.get("GameParamsKey") or row.get("GameParamsIndex") or row.get("ShipCode"), row.get("ShipResource"), row.get("HullUpgrade"))
            if key in result:
                ambiguous.append(list(key))
            result[key] = row
        return result, ambiguous
    old, ambiguous_old = keyed(a["rows"])
    new, ambiguous_new = keyed(b["rows"])
    for key in {tuple(k) for k in ambiguous_old + ambiguous_new}:
        old.pop(key, None)
        new.pop(key, None)
    changed = []
    for key in sorted(old.keys() & new.keys(), key=str):
        fields = [name for name in old[key] if old[key].get(name) != new[key].get(name)]
        if fields:
            changed.append({"key": list(key), "fields": fields,
                            "material_change_certainty": "unverified" if "unknown" in
                            {old[key].get("ShipMaterialStatus"), new[key].get("ShipMaterialStatus")} else "observed"})
    return {"source": a["source"], "added": [list(k) for k in new.keys() - old.keys()],
            "removed": [list(k) for k in old.keys() - new.keys()], "changed": changed,
            "ambiguous": ambiguous_old + ambiguous_new, "official_history": False}


def pin_snapshot(path: Path, pinned: bool):
    if read_json(path).get("schema") != "wows-toolbox-catalog-snapshot/v1":
        raise ValueError("Unsupported snapshot")
    registry = path.parent / "pins.json"
    values = set(read_json(registry) if registry.exists() else [])
    if pinned:
        values.add(path.name)
    else:
        values.discard(path.name)
    atomic_json(registry, sorted(values))


def diagnostics(toolbox: Path, state: Path) -> dict:
    expected = {}
    manifest = toolbox / "MANIFEST.sha256"
    names = ("Backend/wowsunpack.exe", "Backend/ship_material_probe.exe", "Backend/output_provenance.py",
             "Runtime/Python/python.exe", "Viewer/Runtime/WebView2Loader.dll",
             "Viewer/Runtime/Microsoft.Web.WebView2.Core.dll")
    if manifest.is_file():
        with manifest.open(encoding="utf-8-sig") as stream:
            for line in stream:
                parts = line.strip().split(" *", 1)
                if len(parts) == 2 and parts[1].replace("\\", "/") in names:
                    expected[parts[1].replace("\\", "/")] = parts[0]
    components = []
    for name in names:
        path = toolbox / name
        actual = digest(path) if path.is_file() else None
        status = "missing" if actual is None else ("unverified" if name not in expected else
                                                     ("verified" if actual == expected[name] else "hash_mismatch"))
        components.append({"component": name, "status": status})
    report = state / "last-batch-report.json"
    last = read_json(report) if report.is_file() else {}
    return {"schema": "wows-toolbox-share-diagnostics/v1", "python": sys.version.split()[0],
            "components": components, "last_batch": {name: last.get(name) for name in
                                                       ("success_count", "failure_count", "cancelled")},
            "probe_schema": "ship-material-probe/v1" if (toolbox / "Backend/ship_material_probe.exe").is_file() else None}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # denied: conservatively active
        code = ctypes.c_ulong()
        try:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def recovery(state: Path, output_root: Path, *, acknowledge=False) -> dict:
    journal = state / "batch-journal.json"
    lock = state / "batch-journal.lock"
    if lock.is_file() and process_alive(int(read_json(lock).get("pid", 0))):
        raise RuntimeError("Another active batch owns this journal")
    if not journal.is_file():
        return {"items": [], "requires_review": False}
    data = read_json(journal)
    if data.get("schema") != "wows-toolbox-journal/v1":
        raise ValueError("Unsupported journal")
    rows = []
    for entry in data["items"]:
        status = "retry_after_replan"
        try:
            output = safe_child(output_root.resolve(), entry["output"])
            path = output / "extraction.manifest.json"
            manifest = read_json(path)
            if entry.get("request_identity") and manifest.get("request_identity") == entry["request_identity"] and revalidate(path)["status"] != "failed":
                status = "verified_output_recheck_current_conditions"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        rows.append({"index": entry["index"], "name": entry["name"], "recorded_state": entry["state"], "recommendation": status})
    if acknowledge:
        if lock.is_file() and process_alive(int(read_json(lock).get("pid", 0))):
            raise RuntimeError("Active batch owns this journal")
        if lock.exists():
            os.rename(lock, state / ("batch-journal-stale-" + uuid.uuid4().hex + ".json"))
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps({"pid": os.getpid(), "recovery": True}).encode())
            if read_json(journal) != data:
                raise RuntimeError("Journal changed; review recovery again")
            # Retain the reviewed journal; never delete output or silently retry.
            archive = state / ("batch-journal-reviewed-" + uuid.uuid4().hex + ".json")
            atomic_json(archive, data)
            for entry in data["items"]:
                if entry["state"] in {"running", "queued", "interrupted"}:
                    entry["state"] = "reviewed"
            atomic_json(journal, data)
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)
    return {"items": rows, "requires_review": any(r["recorded_state"] in {"running", "queued", "interrupted"} for r in rows)}


def compare_outputs(left: Path, right: Path) -> dict:
    a, b = read_json(left), read_json(right)
    if a.get("schema") != "wows-toolbox-output/v1" or b.get("schema") != a["schema"]:
        raise ValueError("Unsupported provenance schema")
    return {"conditions": {key: {"left": a.get(key), "right": b.get(key), "equal": a.get(key) == b.get(key)}
                           for key in ("source", "selection", "requested", "applied", "counts", "armor_available")},
            "visual_equivalence_claimed": False}


def main():
    parser = argparse.ArgumentParser(description="Local metadata workbench")
    parser.add_argument("command", choices=("snapshot", "snapshots", "snapshot-compare", "pin", "diagnostics", "recovery", "validate", "compare"))
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--toolbox-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--other", type=Path)
    parser.add_argument("--source", default="korabli")
    parser.add_argument("--label", default="")
    parser.add_argument("--acknowledge", action="store_true")
    args = parser.parse_args()
    if args.command == "snapshot":
        result = {"snapshot": str(save_snapshot(read_json(args.file), args.state_root, args.source, args.label))}
    elif args.command == "snapshots":
        result = {"snapshots": [str(p) for p in (args.state_root / "catalog-snapshots").glob("*/*.json") if p.name != "pins.json"]}
    elif args.command == "snapshot-compare":
        result = compare_snapshots(args.file, args.other)
    elif args.command == "pin":
        pin_snapshot(args.file, True)
        result = {"pinned": True}
    elif args.command == "diagnostics":
        result = diagnostics(args.toolbox_root, args.state_root)
    elif args.command == "recovery":
        result = recovery(args.state_root, args.output_root, acknowledge=args.acknowledge)
    elif args.command == "validate":
        result = revalidate(args.file)
    else:
        result = compare_outputs(args.file, args.other)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
