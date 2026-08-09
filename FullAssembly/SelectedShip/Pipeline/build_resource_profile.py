#!/usr/bin/env python3
"""Build an exact extraction profile from a selected-ship assembly mapping.

The mapping is produced from GameParams.data plus the Legends v0
assets/prototypes sidecars.  This step does not read packages and does not
extract anything.  It only turns the resolved ModelUber records into a
deduplicated list of system sidecars, LOD0 geometries, and declared textures.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SCHEMA = "wows-legends-selected-ship-resource-profile/v1"
SYSTEM_PATHS = (
    "content/assets.bin",
    "content/GameParams.data",
    "content/prototypes.index.data",
    "content/prototypes.data",
)
TEXTURE_PROPERTY_NAMES = {
    "ambientOcclusionMap",
    "detailMap",
    "diffuseMap",
    "metallicGlossMap",
    "normalMap",
}
_LOD_RE = re.compile(r"_lod(?:shape)?\d+", re.IGNORECASE)
_DEAD_RE = re.compile(r"dead", re.IGNORECASE)
_HIDE_RE = re.compile(r"(?:^|_)hide", re.IGNORECASE)
_CRACK_RE = re.compile(r"_crack_", re.IGNORECASE)


class ProfileError(RuntimeError):
    """The mapping cannot produce a complete static resource profile."""


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ProfileError(f"JSON root must be an object: {path}")
    return value


def _normalize_path(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{context} is not a non-empty logical path")
    raw = value.replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == ".." or ":" in part or "\0" in part:
            raise ProfileError(f"{context} contains an unsafe path: {value!r}")
        parts.append(part)
    if not parts:
        raise ProfileError(f"{context} resolves to an empty logical path")
    return PurePosixPath(*parts).as_posix()


def is_intact_render_set(render_set: Mapping[str, Any]) -> bool:
    """Apply the intact static-hull naming contract.

    Patch meshes are authored joint covers and remain visible.  Dead and hide
    variants are removed.  Bare/internal crack meshes are removed, while the
    exterior ``*_DeckHouse`` and ``*_Hull`` joint faces remain.
    """

    labels = [
        str(render_set.get(key) or "")
        for key in ("vertices_section", "indices_section", "render_set_name")
    ]
    joined = " ".join(labels)
    if _DEAD_RE.search(joined) or _HIDE_RE.search(joined):
        return False
    if not _CRACK_RE.search(joined):
        return True

    for label in labels:
        normalized = _LOD_RE.sub("", label)
        normalized = re.sub(
            r"\.(?:vertices|indices)$", "", normalized, flags=re.IGNORECASE
        )
        normalized = re.sub(r"shape$", "", normalized, flags=re.IGNORECASE)
        if normalized.casefold().endswith(("_deckhouse", "_hull")):
            return True
    return False


def _used_models(mapping: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "hull": set(),
        "combat": set(),
        "misc": set(),
        "runtime_overlay": set(),
    }
    hull_parts = mapping.get("hull_parts")
    if not isinstance(hull_parts, list):
        raise ProfileError("mapping hull_parts must be an array")
    for item in hull_parts:
        if isinstance(item, dict) and item.get("role") == "mesh":
            result["hull"].add(
                _normalize_path(item.get("path"), context="hull model path")
            )

    for field, category in (
        ("combat_mounts", "combat"),
        ("misc_instances", "misc"),
        ("runtime_action_overlays", "runtime_overlay"),
    ):
        values = mapping.get(field)
        if not isinstance(values, list):
            raise ProfileError(f"mapping {field} must be an array")
        for item in values:
            if not isinstance(item, dict):
                raise ProfileError(f"mapping {field} contains a non-object")
            result[category].add(
                _normalize_path(
                    item.get("model_path"),
                    context=f"{field} model path",
                )
            )
    if not result["hull"]:
        raise ProfileError("mapping contains no detailed hull mesh models")
    return result


def _one_lod0_visual(model_path: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
    parse_error = record.get("model_uber_parse_error")
    if parse_error:
        raise ProfileError(f"{model_path}: ModelUber parse failed: {parse_error}")
    model_uber = record.get("model_uber")
    if not isinstance(model_uber, dict):
        raise ProfileError(f"{model_path}: model_uber is missing")
    visuals = model_uber.get("visual_prototypes")
    if not isinstance(visuals, list):
        raise ProfileError(f"{model_path}: visual_prototypes is missing")
    lod0 = [
        item
        for item in visuals
        if isinstance(item, dict) and item.get("lod_index") == 0
    ]
    if len(lod0) != 1:
        raise ProfileError(
            f"{model_path}: expected one LOD0 visual prototype, got {len(lod0)}"
        )
    return lod0[0]


def _texture_paths(
    model_path: str,
    record: Mapping[str, Any],
    visual: Mapping[str, Any],
) -> set[str]:
    model_uber = record["model_uber"]
    prototypes = model_uber.get("material_prototypes")
    if not isinstance(prototypes, list):
        raise ProfileError(f"{model_path}: material_prototypes is missing")
    render_sets = visual.get("render_sets")
    if not isinstance(render_sets, list):
        raise ProfileError(f"{model_path}: LOD0 render_sets is missing")

    result: set[str] = set()
    intact_count = 0
    for render_set in render_sets:
        if not isinstance(render_set, dict):
            raise ProfileError(f"{model_path}: render set is not an object")
        if not is_intact_render_set(render_set):
            continue
        intact_count += 1
        index = render_set.get("material_prototype_index")
        if not isinstance(index, int) or not (0 <= index < len(prototypes)):
            raise ProfileError(
                f"{model_path}: invalid material prototype index {index!r}"
            )
        prototype = prototypes[index]
        if not isinstance(prototype, dict):
            raise ProfileError(
                f"{model_path}: material prototype {index} is not an object"
            )
        properties = prototype.get("properties")
        if not isinstance(properties, list):
            raise ProfileError(
                f"{model_path}: material prototype {index} has no properties"
            )
        for prop in properties:
            if (
                not isinstance(prop, dict)
                or prop.get("type") != "texture"
                or prop.get("name") not in TEXTURE_PROPERTY_NAMES
            ):
                continue
            value = prop.get("value")
            logical_path = value.get("path") if isinstance(value, dict) else None
            result.add(
                _normalize_path(
                    logical_path,
                    context=f"{model_path} texture {prop.get('name')}",
                )
            )
    if intact_count == 0:
        raise ProfileError(f"{model_path}: no intact LOD0 render sets")
    return result


def build_profile(mapping: Mapping[str, Any]) -> dict[str, Any]:
    if mapping.get("schema") != "wows-legends-static-ship-assembly/v1":
        raise ProfileError("unexpected assembly mapping schema")
    ship = mapping.get("ship")
    if not isinstance(ship, dict):
        raise ProfileError("mapping ship metadata is missing")
    ship_key = ship.get("ship_key")
    if not isinstance(ship_key, str) or not ship_key:
        raise ProfileError("mapping ship_key is missing")
    models = mapping.get("models")
    if not isinstance(models, dict):
        raise ProfileError("mapping models must be an object")

    categories = _used_models(mapping)
    path_categories: dict[str, set[str]] = {}
    for category, paths in categories.items():
        for model_path in paths:
            path_categories.setdefault(model_path, set()).add(category)

    geometry_records: dict[str, dict[str, Any]] = {}
    texture_paths: set[str] = set()
    model_records: list[dict[str, Any]] = []
    for model_path in sorted(path_categories, key=str.casefold):
        record = models.get(model_path)
        if not isinstance(record, dict):
            raise ProfileError(f"mapping model record missing: {model_path}")
        visual = _one_lod0_visual(model_path, record)
        geometry_path = _normalize_path(
            visual.get("derived_geometry_path"),
            context=f"{model_path} LOD0 geometry",
        )
        texture_paths.update(_texture_paths(model_path, record, visual))
        geometry = geometry_records.setdefault(
            geometry_path,
            {
                "kind": "geometry",
                "path": geometry_path,
                "lod_index": 0,
                "model_paths": [],
                "categories": [],
            },
        )
        geometry["model_paths"].append(model_path)
        geometry["categories"] = sorted(
            set(geometry["categories"]) | path_categories[model_path]
        )
        model_records.append(
            {
                "path": model_path,
                "categories": sorted(path_categories[model_path]),
                "geometry_path": geometry_path,
                "intact_lod0_render_sets": sum(
                    1
                    for item in visual["render_sets"]
                    if isinstance(item, dict) and is_intact_render_set(item)
                ),
            }
        )

    resources: list[dict[str, Any]] = [
        {"kind": "system_sidecar", "path": path} for path in SYSTEM_PATHS
    ]
    resources.extend(
        geometry_records[path]
        for path in sorted(geometry_records, key=str.casefold)
    )
    resources.extend(
        {"kind": "texture", "path": path}
        for path in sorted(texture_paths, key=str.casefold)
    )
    seen: set[str] = set()
    for item in resources:
        folded = item["path"].casefold()
        if folded in seen:
            raise ProfileError(f"duplicate logical resource: {item['path']}")
        seen.add(folded)

    counts = {
        "system_sidecars": len(SYSTEM_PATHS),
        "lod0_geometry": len(geometry_records),
        "declared_textures": len(texture_paths),
        "unique_models": len(model_records),
        "total": len(resources),
    }
    return {
        "schema": SCHEMA,
        "profile_id": f"{ship_key.casefold()}_static_intact",
        "ship_key": ship_key,
        "status": "generated-from-resolved-mapping",
        "selection": {
            "lod_index": 0,
            "damage_variants": False,
            "patch_joint_covers": True,
            "exterior_crack_joint_faces": True,
        },
        "expected_counts": counts,
        "model_counts": {
            category: len(paths) for category, paths in categories.items()
        },
        "models": model_records,
        "resources": resources,
        "limitations": [
            "Static intact geometry only; runtime aiming and firing animation are not reconstructed.",
            "OBJ/MTL cannot preserve the complete runtime shader graph.",
        ],
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        profile = build_profile(_load_object(args.mapping.resolve()))
        _write_json(args.output.resolve(), profile)
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(args.output.resolve()),
                    "ship_key": profile["ship_key"],
                    "expected_counts": profile["expected_counts"],
                    "model_counts": profile["model_counts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ProfileError) as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
