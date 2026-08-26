from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from blitz_assets import serialized_files
from blitz_extract import (
    _local_matrix,
    _unity_world_to_obj_matrix,
    _unitypy,
    _world_matrices,
)


def _inspect_body(task: tuple[str, dict[str, Any], str]) -> dict[str, Any]:
    relative_path, record, bundle_root_text = task
    path = Path(bundle_root_text) / Path(relative_path)
    try:
        environment = _unitypy().load(str(path))
        candidates = serialized_files(environment)
        if not candidates:
            raise RuntimeError("bundle has no serialized file")
        serialized = candidates[0][1]
        transforms: dict[int, dict[str, Any]] = {}
        game_object_transforms: dict[int, int] = {}
        mesh_game_objects: list[int] = []
        for reader in serialized.objects.values():
            if reader.type.name == "Transform":
                transform = reader.parse_as_object()
                transform_id = int(reader.path_id)
                game_object_id = int(transform.m_GameObject.m_PathID)
                transforms[transform_id] = {
                    "parent_id": int(transform.m_Father.m_PathID),
                    "local": _local_matrix(transform),
                }
                game_object_transforms[game_object_id] = transform_id
            elif reader.type.name == "MeshFilter":
                mesh_filter = reader.parse_as_object()
                mesh_game_objects.append(int(mesh_filter.m_GameObject.m_PathID))

        worlds = _world_matrices(transforms)
        changed = 0
        lateral = 0
        max_lateral_delta = 0.0
        for game_object_id in mesh_game_objects:
            transform_id = game_object_transforms.get(game_object_id)
            if transform_id is None:
                continue
            old = worlds[transform_id]
            new = _unity_world_to_obj_matrix(old)
            difference = max(
                abs(old[row][column] - new[row][column])
                for row in range(4)
                for column in range(4)
            )
            if difference > 1e-6:
                changed += 1
            lateral_delta = abs(old[0][3] - new[0][3])
            if lateral_delta > 1e-6:
                lateral += 1
                max_lateral_delta = max(max_lateral_delta, lateral_delta)

        return {
            "ok": True,
            "path": relative_path,
            "id": record.get("Id"),
            "name": record.get("LocalizedName"),
            "resource": record.get("ShipResource"),
            "class": record.get("ShipClassRaw") or "Unknown",
            "changed_meshes": changed,
            "lateral_meshes": lateral,
            "max_lateral_delta": round(max_lateral_delta, 6),
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": relative_path,
            "id": record.get("Id"),
            "name": record.get("LocalizedName"),
            "class": record.get("ShipClassRaw") or "Unknown",
            "error": str(exc),
        }


def audit(catalog_path: Path, bundle_root: Path, workers: int) -> dict[str, Any]:
    records = json.loads(catalog_path.read_text(encoding="utf-8"))
    supported = [
        record
        for record in records
        if record.get("Supported") and record.get("ModelPath")
    ]
    unique: dict[str, dict[str, Any]] = {}
    for record in supported:
        relative = str(record["ModelPath"]).replace("\\", "/")
        unique.setdefault(relative, record)

    tasks = [
        (relative, record, str(bundle_root))
        for relative, record in sorted(unique.items())
    ]
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_inspect_body, task) for task in tasks]
        for completed, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if completed % 50 == 0 or completed == len(futures):
                print(
                    f"[AUDIT] {completed}/{len(futures)}",
                    file=sys.stderr,
                    flush=True,
                )

    valid = [item for item in results if item["ok"]]
    errors = [item for item in results if not item["ok"]]
    summary: Counter[str] = Counter()
    by_class: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for item in valid:
        affected = item["changed_meshes"] > 0
        summary["ships_scanned"] += 1
        summary["ships_affected" if affected else "ships_unchanged"] += 1
        summary["mesh_instances_affected"] += item["changed_meshes"]
        summary["mesh_instances_lateral"] += item["lateral_meshes"]
        class_stats = by_class[item["class"]]
        class_stats["ships"] += 1
        class_stats["affected" if affected else "unchanged"] += 1
        class_stats["mesh_instances_affected"] += item["changed_meshes"]

    carriers = sorted(
        (item for item in valid if item["class"] == "AirCarrier"),
        key=lambda item: (str(item["name"]).casefold(), str(item["id"])),
    )
    largest = sorted(
        valid,
        key=lambda item: (
            item["max_lateral_delta"],
            item["changed_meshes"],
            str(item["name"]),
        ),
        reverse=True,
    )[:20]
    return {
        "schema": "wows-toolbox-blitz-axis-audit/v1",
        "catalog": str(catalog_path),
        "bundle_root": str(bundle_root),
        "catalog_records_supported": len(supported),
        "unique_body_bundles": len(unique),
        "summary": dict(summary),
        "by_class": {key: dict(value) for key, value in sorted(by_class.items())},
        "carriers": {
            "count": len(carriers),
            "affected_count": sum(item["changed_meshes"] > 0 for item in carriers),
            "unchanged_count": sum(item["changed_meshes"] == 0 for item in carriers),
            "ships": carriers,
        },
        "largest_lateral_corrections": largest,
        "errors": errors,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description="Audit Blitz body bundles for the Unity-to-OBJ basis mismatch."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
    )
    args = parser.parse_args()
    result = audit(
        args.catalog.resolve(),
        args.bundle_root.resolve(),
        args.workers,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
