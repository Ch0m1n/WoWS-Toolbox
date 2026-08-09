#!/usr/bin/env python3
"""Convert wowsunpack exact armor metadata into the toolbox viewer sidecar."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ARMOR_COLORS = (
    "#6ED1B0",
    "#95D27F",
    "#AAC966",
    "#C0C150",
    "#E2C33E",
    "#E1AB36",
    "#E39031",
    "#E67331",
    "#DC4E30",
    "#B92F30",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def thickness_label(value: float) -> str:
    if value <= 0:
        return "두께 정보 없음"
    rounded = round(value)
    if abs(value - rounded) < 0.005:
        return f"{rounded} mm"
    return f"{value:.2f}".rstrip("0").rstrip(".") + " mm"


def color_for_thickness(value: float) -> str:
    if value <= 0:
        return "#C8C8C8"
    limits = (14, 16, 24, 26, 28, 33, 75, 160, 399)
    for index, limit in enumerate(limits):
        if value <= limit:
            return ARMOR_COLORS[index]
    return ARMOR_COLORS[-1]


def transform_point(point: Iterable[Any], matrix: Any) -> tuple[float, float, float]:
    x, y, z = (finite_number(value) for value in point)
    if not isinstance(matrix, list) or len(matrix) != 16:
        return x, y, z
    # glTF matrices are column-major.
    return (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )


def normalized_layers(value: Any, fallback: float) -> tuple[float, ...]:
    if not isinstance(value, list):
        return (fallback,) if fallback > 0 else ()
    layers = tuple(
        round(finite_number(item), 4)
        for item in value
        if finite_number(item) > 0
    )
    return layers or ((fallback,) if fallback > 0 else ())


def convert(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != "wowsunpack-interactive-armor/v1":
        raise ValueError("지원하지 않는 정확 장갑 메타데이터 형식이에요.")
    meshes = payload.get("meshes")
    if not isinstance(meshes, list):
        raise ValueError("정확 장갑 메시에 meshes 배열이 없어요.")

    grouped: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    triangle_total = 0
    source_meshes: list[str] = []
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        name = str(mesh.get("name") or "Armor")
        source_meshes.append(name)
        positions = mesh.get("positions")
        indices = mesh.get("indices")
        metadata = mesh.get("triangle_info")
        matrix = mesh.get("transform")
        if not isinstance(positions, list) or not isinstance(indices, list) or not isinstance(metadata, list):
            continue
        for triangle_index, info in enumerate(metadata):
            base = triangle_index * 3
            if base + 2 >= len(indices) or not isinstance(info, dict):
                continue
            thickness = round(finite_number(info.get("thickness_mm")), 4)
            zone = str(info.get("zone") or "미분류")[:80]
            material_name = str(info.get("material_name") or "미상")[:120]
            material_id = int(finite_number(info.get("material_id"), -1))
            layers = normalized_layers(info.get("layers"), thickness)
            hidden = bool(info.get("hidden"))
            key = (zone, thickness, material_id, material_name, layers, hidden)
            points: list[tuple[float, float, float]] = []
            valid = True
            for offset in range(3):
                index = int(finite_number(indices[base + offset], -1))
                if index < 0 or index >= len(positions):
                    valid = False
                    break
                point = positions[index]
                if not isinstance(point, list) or len(point) < 3:
                    valid = False
                    break
                points.append(transform_point(point[:3], matrix))
            if not valid:
                continue
            # The exact export is already right-handed, Y-up, and -Z-forward.
            # Keep the source winding because no axis swap is needed.
            for point in points:
                grouped[key].extend(round(value, 6) for value in point)
            triangle_total += 1

    if not grouped:
        raise ValueError("표시할 수 있는 정확 장갑 삼각형이 없어요.")

    thicknesses = sorted({key[1] for key in grouped})
    bucket_ids = {value: index for index, value in enumerate(thicknesses)}
    buckets = [
        {
            "id": bucket_ids[value],
            "label": thickness_label(value),
            "min_mm": value,
            "max_mm": value,
            "thickness_mm": value,
            "exact": value > 0,
            "color": color_for_thickness(value),
        }
        for value in thicknesses
    ]
    groups = []
    for key, flat_positions in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][3]),
    ):
        zone, thickness, material_id, material_name, layers, hidden = key
        groups.append(
            {
                "zone": zone,
                "bucket": bucket_ids[thickness],
                "thickness_mm": thickness,
                "layers_mm": list(layers),
                "material_id": material_id,
                "material_name": material_name,
                "hidden_in_game": hidden,
                "positions": flat_positions,
                "triangle_count": len(flat_positions) // 9,
            }
        )

    return {
        "schema": "wows-toolbox-armor-viewer/v3",
        "coordinate_system": {
            "axis_forward": "-Z",
            "axis_up": "Y",
            "handedness": "right",
            "space": "viewer",
        },
        "exact_thickness": True,
        "buckets": buckets,
        "zones": sorted({group["zone"] for group in groups}),
        "groups": groups,
        "source_objects": source_meshes,
        "triangle_count": triangle_total,
    }


def main() -> int:
    args = arguments()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    converted = convert(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(converted, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "triangles": converted["triangle_count"],
                "groups": len(converted["groups"]),
                "exact_thickness": True,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
