#!/usr/bin/env python3
"""Build a static assembly mapping for a selected Legends ship.

The module reads only extracted copies of ``GameParams.data``, ``assets.bin``,
``prototypes.index.data``, and ``prototypes.data``.  It reuses the corrected
Legends-v0 parser from ``build_ticonderoga_assembly_v2.py`` while removing the
Ticonderoga ship key, model paths, component counts, and hardpoint assumptions.

The generated document intentionally keeps the
``wows-legends-static-ship-assembly/v1`` contract used by the existing
converter/assembler:

* ``models``
* ``hull_parts``
* ``combat_mounts``
* ``misc_instances``
* ``runtime_action_overlays``
* ``validation``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# This runner patches the v0 MaterialPrototype property layout before exposing
# the core parser.  Importing the uncorrected module directly breaks on several
# valid Burke hull models.
import build_ticonderoga_assembly_v2 as corrected_runner  # noqa: E402


core = corrected_runner.core


COMPONENT_SUFFIX_CATEGORIES = (
    ("UnguidedMissiles", "vertical_launch_system"),
    ("GuidedMissiles", "guided_missile_launcher"),
    ("AirDefense", "air_defense"),
    ("AirArmament", "air_armament"),
    ("AirSupport", "air_support"),
    ("Torpedoes", "torpedo_launcher"),
    ("Artillery", "main_artillery"),
    ("Directors", "director"),
    ("Radars", "radar"),
    ("ATBA", "secondary_artillery"),
)

HULL_SEGMENT_RE = re.compile(
    r"^(?P<root>.+?)_(?P<segment>Bow|MidFront|MidBack|Stern)"
    r"(?P<ports>_ports)?(?P<dock>_dock)?$"
)
LOD_TOKEN_RE = re.compile(r"_lod(?:shape)?\d+", re.IGNORECASE)
MODEL_KEY_RE = re.compile(r"MP_([A-Z]+\d+)", re.IGNORECASE)


def _unique_strings(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _resolve_ship_key(root: dict[str, Any], requested_key: str) -> str:
    """Resolve a catalog hull variant to the playable GameParams ship key."""

    if requested_key in root:
        return requested_key
    index_match = re.match(r"^(P[A-Z]+\d+)(?:_|$)", requested_key)
    if index_match is None:
        raise KeyError(f"ship key absent from GameParams.data: {requested_key}")
    index = index_match.group(1)
    candidates = sorted(
        str(key)
        for key in root
        if str(key) == index or str(key).startswith(index + "_")
    )
    if len(candidates) == 1:
        return candidates[0]
    requested_tokens = {
        token.casefold()
        for token in requested_key[len(index) :].strip("_").split("_")
        if token and not token.isdigit()
    }
    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        candidate_tokens = {
            token.casefold()
            for token in candidate[len(index) :].strip("_").split("_")
            if token and not token.isdigit()
        }
        scored.append((len(requested_tokens & candidate_tokens), candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored and scored[0][0] > 0 and (
        len(scored) == 1 or scored[0][0] > scored[1][0]
    ):
        return scored[0][1]
    raise KeyError(
        "ship key absent from GameParams.data and no unique index fallback: "
        f"requested={requested_key}, candidates={candidates}"
    )


def _ship_components(root: dict[str, Any], ship_key: str) -> dict[str, Any]:
    ship = root[ship_key]
    state = getattr(ship, "state", None)
    if (
        not isinstance(state, (tuple, list))
        or len(state) <= 2
        or not isinstance(state[2], dict)
    ):
        raise TypeError(f"{ship_key} does not expose a ship component dictionary")
    return state[2]

def _available_model_path(
    path: str, assets: Any, prototypes: Any
) -> bool:
    resource_id = assets.resource_id(path)
    return resource_id is not None and prototypes.locate(resource_id) is not None


def _component_category(component: str) -> tuple[str, str] | None:
    folded = component.casefold()
    for suffix, category in COMPONENT_SUFFIX_CATEGORIES:
        if folded == suffix.casefold() or folded.endswith("_" + suffix.casefold()):
            return suffix, category
    return None


def _variant_family(component: str, suffix: str) -> str:
    if component.casefold() == suffix.casefold():
        return ""
    return component[: -(len(suffix) + 1)].upper()


def _normalize_selected_model_path(value: str) -> str:
    text = value.strip().replace("\\", "/").rstrip("/")
    if not text:
        raise ValueError("selected catalog model path is empty")
    path = PurePosixPath(text)
    if path.suffix.casefold() == ".model":
        return path.as_posix()
    return (path / f"{path.name}.model").as_posix()


def _hull_candidates(
    components: dict[str, Any], assets: Any, prototypes: Any
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for component, value in components.items():
        if not str(component).casefold().endswith("_hull"):
            continue
        candidates = _unique_strings(
            text.replace("\\", "/")
            for text in core.flatten_strings(value)
            if isinstance(text, str)
            and text.casefold().endswith(".model")
            and "/ship/" in text.replace("\\", "/").casefold()
            and not text.casefold().endswith("_dead.model")
            and _available_model_path(text.replace("\\", "/"), assets, prototypes)
        )
        found.extend((str(component), path) for path in candidates)
    return found


def _select_hull_model_path(
    components: dict[str, Any],
    assets: Any,
    prototypes: Any,
    selected_model_path: str | None = None,
) -> tuple[str, str]:
    candidates = _hull_candidates(components, assets, prototypes)
    if selected_model_path:
        selected_model_path = _normalize_selected_model_path(selected_model_path)
        requested = selected_model_path.casefold()
        exact = [
            (component, path)
            for component, path in candidates
            if path.casefold() == requested
        ]
        if not exact:
            raise ValueError(
                "selected catalog model does not resolve to a live hull "
                f"component: selected={selected_model_path!r}, candidates={candidates}"
            )
        exact.sort(
            key=lambda item: (
                {"a_hull": 0, "ab_hull": 1, "b_hull": 2}.get(
                    item[0].casefold(), 3
                ),
                item[0].casefold(),
            )
        )
        return exact[0]
    if not candidates:
        raise KeyError("ship has no prototype-backed live Hull component")

    def rank(item: tuple[str, str]) -> tuple[int, str, str]:
        component, path = item
        priority = {
            "a_hull": 0,
            "ab_hull": 1,
            "b_hull": 2,
            "hull": 3,
        }.get(component.casefold(), 4)
        return priority, component.casefold(), path.casefold()

    candidates.sort(key=rank)
    best_priority = rank(candidates[0])[0]
    best = [item for item in candidates if rank(item)[0] == best_priority]
    if len(best) != 1:
        raise ValueError(
            "no exact catalog model was supplied and the preferred hull is "
            f"ambiguous: {best}"
        )
    return best[0]


def _component_rank(component: str, suffix: str, hull_component: str) -> tuple[int, str]:
    family = _variant_family(component, suffix)
    hull_family = hull_component[: -len("_Hull")].upper()
    if family == hull_family:
        return 0, component.casefold()
    if family.startswith("AB"):
        return 1, component.casefold()
    # Legends uses numbered module-family prefixes such as A1_Artillery and
    # B2_Artillery. The digit identifies a module choice inside the selected
    # hull family; it is not a different hull. Treating A1 as unrelated to
    # A_Hull drops every HP_AGM mount on ships such as Alaska.
    if re.fullmatch(re.escape(hull_family) + r"\d+", family):
        return 2, component.casefold()
    if hull_family.startswith("A") and family == "A":
        return 2, component.casefold()
    if hull_family.startswith("B") and family == "B":
        return 2, component.casefold()
    if family in {"R", ""}:
        return 3, component.casefold()
    return 10, component.casefold()

def discover_hull_model_paths(
    hull_model_path: str, assets: Any, prototypes: Any
) -> list[str]:
    """Discover intact root/segment/ports models in the hull resource folder."""

    root_path = PurePosixPath(hull_model_path)
    root_stem = root_path.stem
    parent = root_path.parent
    discovered: list[str] = []
    for candidate in assets.path_to_id:
        parsed = PurePosixPath(candidate)
        if parsed.parent != parent or parsed.suffix != ".model":
            continue
        if not _available_model_path(candidate, assets, prototypes):
            continue
        stem = parsed.stem
        if stem == root_stem:
            discovered.append(candidate)
            continue
        match = HULL_SEGMENT_RE.fullmatch(stem)
        if match and match.group("root") == root_stem:
            discovered.append(candidate)

    if hull_model_path not in discovered:
        raise ValueError(f"hull root prototype is unavailable: {hull_model_path}")

    segment_order = {"Bow": 1, "MidFront": 2, "MidBack": 3, "Stern": 4}

    def sort_key(path: str) -> tuple[int, int, str]:
        stem = PurePosixPath(path).stem
        if stem == root_stem:
            return (0, 0, stem)
        match = HULL_SEGMENT_RE.fullmatch(stem)
        if match is None:
            return (99, 99, stem)
        variant = 2 if match.group("dock") else 1 if match.group("ports") else 0
        return (segment_order[match.group("segment")], variant, stem)

    return sorted(set(discovered), key=sort_key)


def hull_part_contract(
    path: str, hull_model_path: str, available_paths: set[str]
) -> dict[str, Any]:
    root_stem = PurePosixPath(hull_model_path).stem
    stem = PurePosixPath(path).stem
    if path == hull_model_path:
        return {
            "role": "root",
            "parent_path": None,
            "render_required": False,
            "note": "whole-ship far LOD and root markers",
        }

    match = HULL_SEGMENT_RE.fullmatch(stem)
    if match is None or match.group("root") != root_stem:
        raise ValueError(f"unexpected hull sibling model: {path}")
    segment_stem = f"{root_stem}_{match.group('segment')}"
    segment_path = str(PurePosixPath(path).with_name(segment_stem + ".model"))
    if match.group("ports"):
        if segment_path not in available_paths:
            raise ValueError(f"ports model lacks its segment model: {path}")
        return {
            "role": "conditional_ports" if match.group("dock") else "ports",
            "parent_path": segment_path,
            "render_required": False,
            "note": (
                "dock-state authored attachment nodes"
                if match.group("dock")
                else "authored hardpoint/effect/misc attachment nodes"
            ),
        }
    return {
        "role": "mesh",
        "parent_path": hull_model_path,
        "render_required": True,
        "note": f"{match.group('segment')} detailed segment",
    }


def _model_asset_code(path: str) -> str:
    stem = PurePosixPath(path).stem
    return stem[:-5] if stem.casefold().endswith("_dead") else stem


def _mount_from_item(
    component: str, category: str, hardpoint: str, item: Any
) -> dict[str, Any]:
    model_paths = _unique_strings(
        text
        for text in core.flatten_strings(item)
        if isinstance(text, str) and text.endswith(".model")
    )
    dead = [path for path in model_paths if path.endswith("_dead.model")]
    live = [path for path in model_paths if path not in dead]
    hp_asset_code = (
        hardpoint[3:] if hardpoint.startswith("HP_") else hardpoint
    ).split("_", 1)[0]
    primary = [
        path
        for path in live
        if _model_asset_code(path).upper().startswith(hp_asset_code.upper())
    ]
    if len(primary) != 1 and len(live) == 1:
        primary = list(live)
    if len(primary) != 1:
        raise ValueError(
            f"{component}/{hardpoint}: expected one live model matching "
            f"{hp_asset_code}, got {primary}; all live={live}"
        )
    model_path = primary[0]
    return {
        "component": component,
        "category": category,
        "hardpoint": hardpoint,
        "model_path": model_path,
        "dead_model_paths": dead,
        "action_model_paths": [path for path in live if path != model_path],
        "selection_evidence": {
            "hardpoint_asset_code": hp_asset_code,
            "candidate_model_paths": model_paths,
            "rule": (
                "select the sole live basename beginning with the HP asset code; "
                "remaining live models are recorded as runtime auxiliaries"
            ),
        },
    }


def gameparams_mounts(
    root: dict[str, Any],
    ship_key: str,
    assets: Any,
    prototypes: Any,
    selected_model_path: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested_ship_key = ship_key
    ship_key = _resolve_ship_key(root, requested_ship_key)
    components = _ship_components(root, ship_key)
    if selected_model_path:
        selected_model_path = _normalize_selected_model_path(selected_model_path)
    hull_component, hull_model_path = _select_hull_model_path(
        components, assets, prototypes, selected_model_path
    )

    grouped: dict[str, list[tuple[str, str, str]]] = {}
    discovered_components: list[str] = []
    for raw_component in components:
        component = str(raw_component)
        classified = _component_category(component)
        if classified is None:
            continue
        suffix, category = classified
        discovered_components.append(component)
        grouped.setdefault(category, []).append((component, suffix, category))

    mounts: list[dict[str, Any]] = []
    mapped_components: list[str] = []
    skipped_components: list[dict[str, str]] = []
    for category, choices in grouped.items():
        inspected: list[tuple[tuple[str, str, str], list[dict[str, Any]]]] = []
        for choice in choices:
            component = choice[0]
            hp_candidates = []
            for candidate_mapping in core.find_hp_dicts(components[component]):
                hp_items = {
                    key: value
                    for key, value in candidate_mapping.items()
                    if isinstance(key, str) and key.startswith("HP_")
                }
                if hp_items:
                    hp_candidates.append(hp_items)
            inspected.append((choice, hp_candidates))
        inspected.sort(
            key=lambda pair: (
                _component_rank(pair[0][0], pair[0][1], hull_component)[0],
                -max((len(value) for value in pair[1]), default=0),
                _component_rank(pair[0][0], pair[0][1], hull_component)[1],
            )
        )
        selected = next(
            (
                pair
                for pair in inspected
                if _component_rank(pair[0][0], pair[0][1], hull_component)[0] < 10
                and pair[1]
            ),
            None,
        )
        if selected is None:
            for choice, _hp_candidates in inspected:
                reason = (
                    "different hull variant"
                    if _component_rank(choice[0], choice[1], hull_component)[0] >= 10
                    else "component has no HP model dictionary"
                )
                skipped_components.append(
                    {"component": choice[0], "reason": reason}
                )
            continue
        (component, _, _), hp_candidates = selected
        for choice, candidates in inspected:
            if choice[0] == component:
                continue
            reason = (
                f"superseded by {component}"
                if candidates
                else "component has no HP model dictionary"
            )
            skipped_components.append(
                {"component": choice[0], "reason": reason}
            )
        mapping = max(hp_candidates, key=lambda item: (len(item), sorted(item)))
        mapped_components.append(component)
        for hardpoint, item in mapping.items():
            mounts.append(_mount_from_item(component, category, hardpoint, item))
    mounts.sort(key=lambda item: core.natural_key(item["hardpoint"]))
    return mounts, {
        "ship_key": ship_key,
        "requested_ship_key": requested_ship_key,
        "key_resolution": (
            "exact" if ship_key == requested_ship_key
            else "ship_index_fallback"
        ),
        "component_order": list(components.keys()),
        "discovered_mount_components": discovered_components,
        "mapped_components": mapped_components,
        "skipped_mount_components": skipped_components,
        "hull_component": hull_component,
        "hull_model_path": hull_model_path,
        "selected_model_path": selected_model_path,
        "selected_model_exact": (
            not selected_model_path
            or hull_model_path.casefold()
            == selected_model_path.replace("\\", "/").casefold()
        ),
    }

def classify_render_set(name: str | None) -> dict[str, Any]:
    """Give every render set an explicit intact/damage decision."""

    if not name:
        return {
            "damage_semantic": "unknown",
            "include_in_intact": None,
            "semantic_rule": "missing render-set section name",
        }
    normalized = re.sub(
        r"\.(?:indices|vertices)$", "", name, flags=re.IGNORECASE
    )
    normalized = LOD_TOKEN_RE.sub("", normalized)
    normalized = re.sub(r"shape$", "", normalized, flags=re.IGNORECASE)
    folded = normalized.casefold()
    if "dead" in folded:
        return {
            "damage_semantic": "damage",
            "include_in_intact": False,
            "semantic_rule": "dead variant",
        }
    if "_hide" in folded:
        return {
            "damage_semantic": "damage",
            "include_in_intact": False,
            "semantic_rule": "hidden torn-metal damage mesh",
        }
    if "_crack_" in folded:
        exterior = folded.endswith(("_deckhouse", "_hull"))
        return {
            "damage_semantic": "intact" if exterior else "damage",
            "include_in_intact": exterior,
            "semantic_rule": (
                "exterior DeckHouse/Hull break-joint face"
                if exterior
                else "inner/bare crack cross-section"
            ),
        }
    # Patch primitives close intact segmented hull joints.  They must not be
    # lumped into damage-only geometry.
    if "_patch_" in folded:
        return {
            "damage_semantic": "intact",
            "include_in_intact": True,
            "semantic_rule": "intact segmented-hull joint patch",
        }
    return {
        "damage_semantic": "intact",
        "include_in_intact": True,
        "semantic_rule": "ordinary render set",
    }


def annotate_render_sets(model: dict[str, Any]) -> None:
    for visual in model["model_uber"].get("visual_prototypes", []):
        lod_index = visual.get("lod_index")
        for render_set in visual.get("render_sets", []):
            semantic = classify_render_set(
                render_set.get("indices_section")
                or render_set.get("vertices_section")
                or render_set.get("render_set_name")
            )
            render_set.update(semantic)
            render_set["lod_index"] = lod_index


def safe_model_record(
    path: str, assets: Any, prototypes: Any
) -> dict[str, Any]:
    """Parse a full ModelUber record, retaining nodes if a full parse fails."""

    try:
        record = core.model_record(path, assets, prototypes)
        record["model_uber_parse_error"] = None
    except Exception as exc:  # Preserve exact mapping nodes for diagnostics.
        resource_id = assets.resource_id(path)
        if resource_id is None:
            raise
        blob, location = prototypes.blob(resource_id)
        nodes = core.parse_nodes(blob, assets)
        record = {
            "path": path,
            "resource_id": core.hex64(resource_id),
            "prototype_location": {
                "index": location["index"],
                "data_offset": location["data_offset"],
                "size": location["size"],
                "index_trailing_u32": core.hex32(
                    location["index_trailing_u32"]
                ),
                "index_trailing_u32_semantics": (
                    "unknown/checksum-like; not treated as a type hash"
                ),
            },
            "model_uber": {
                "format": "Legends external ModelUberProto v0 (nodes-only fallback)",
                "visual_nodes": nodes,
                "visual_prototypes": [],
                "material_prototypes": [],
            },
            "model_uber_parse_error": f"{type(exc).__name__}: {exc}",
        }
    annotate_render_sets(record)
    return record


def _asset_nation(hull_model_path: str) -> str | None:
    parts = PurePosixPath(hull_model_path).parts
    try:
        gameplay = parts.index("gameplay")
    except ValueError:
        return None
    return parts[gameplay + 1] if gameplay + 1 < len(parts) else None


def resolve_asset_key_model_path(
    asset_key: str,
    hull_model_path: str,
    assets: Any,
    prototypes: Any,
) -> tuple[str | None, list[str]]:
    suffix = f"/{asset_key}/{asset_key}.model".casefold()
    candidates = sorted(
        path
        for path in assets.path_to_id
        if path.casefold().endswith(suffix)
        and _available_model_path(path, assets, prototypes)
    )
    if len(candidates) <= 1:
        return (candidates[0] if candidates else None), candidates
    nation = _asset_nation(hull_model_path)
    same_nation = (
        [
            path
            for path in candidates
            if f"/gameplay/{nation}/" in path
        ]
        if nation
        else []
    )
    if len(same_nation) == 1:
        return same_nation[0], candidates
    common = [path for path in candidates if "/gameplay/common/" in path]
    if len(common) == 1:
        return common[0], candidates
    return None, candidates


def _render_sets(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        render_set
        for visual in model["model_uber"].get("visual_prototypes", [])
        for render_set in visual.get("render_sets", [])
    ]


def _texture_properties(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        prop
        for material in model["model_uber"].get("material_prototypes", [])
        for prop in material.get("properties", [])
        if prop.get("type") == "texture"
    ]


def build(args: argparse.Namespace) -> dict[str, Any]:
    assets = core.AssetsV0(args.assets)
    prototypes = core.PrototypeIndex(
        args.prototype_index, args.prototype_data
    )
    game_params = core.load_game_params(args.game_params)
    gp_mounts, gp_metadata = gameparams_mounts(
        game_params, args.ship_key, assets, prototypes, args.selected_model_path
    )

    hull_model_path = gp_metadata["hull_model_path"]
    hull_paths = discover_hull_model_paths(
        hull_model_path, assets, prototypes
    )
    hull_path_set = set(hull_paths)
    models: dict[str, dict[str, Any]] = {
        path: safe_model_record(path, assets, prototypes)
        for path in hull_paths
    }

    hp_sources: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    mp_nodes: list[tuple[str, dict[str, Any], str]] = []
    unresolved_mp_nodes: list[dict[str, Any]] = []
    misc_key_paths: dict[str, str] = {}
    for path in hull_paths:
        for node in models[path]["model_uber"]["visual_nodes"]["nodes"]:
            name = node.get("name") or ""
            if name.startswith("HP_"):
                hp_sources.setdefault(name, []).append((path, node))
            if not name.startswith("MP_"):
                continue
            match = MODEL_KEY_RE.match(name)
            if match is None:
                unresolved_mp_nodes.append(
                    {
                        "source_hull_model_path": path,
                        "source_node_index": node["index"],
                        "instance_name": name,
                        "reason": "MP node does not begin with MP_<asset key>",
                    }
                )
                continue
            key = match.group(1).upper()
            model_path, candidates = resolve_asset_key_model_path(
                key, hull_model_path, assets, prototypes
            )
            if model_path is None:
                unresolved_mp_nodes.append(
                    {
                        "source_hull_model_path": path,
                        "source_node_index": node["index"],
                        "instance_name": name,
                        "asset_key": key,
                        "candidate_model_paths": candidates,
                        "reason": "asset-key model is missing or ambiguous",
                    }
                )
                continue
            misc_key_paths[key] = model_path
            mp_nodes.append((path, node, key))

    combat_paths = {item["model_path"] for item in gp_mounts}
    action_paths = {
        path
        for item in gp_mounts
        for path in item["action_model_paths"]
    }
    misc_paths = set(misc_key_paths.values())
    all_model_paths = set(hull_paths) | combat_paths | action_paths | misc_paths
    unresolved_model_paths: list[str] = []
    for path in sorted(all_model_paths):
        if path in models:
            continue
        if not _available_model_path(path, assets, prototypes):
            unresolved_model_paths.append(path)
            continue
        models[path] = safe_model_record(path, assets, prototypes)

    corrections = {
        path: core.correction_for(models[path])
        for path in sorted(combat_paths | action_paths | misc_paths)
        if path in models
    }

    combat_mounts: list[dict[str, Any]] = []
    for sequence, mount in enumerate(gp_mounts):
        hardpoint = mount["hardpoint"]
        sources = hp_sources.get(hardpoint, [])
        if len(sources) != 1 or mount["model_path"] not in models:
            continue
        source_path, node = sources[0]
        correction = corrections[mount["model_path"]]
        corrected = core.mat_mul(
            node["world_matrix"]["column_major"],
            correction["correction_matrix"]["column_major"],
        )
        item = dict(mount)
        item.update(
            {
                "sequence": sequence,
                "render_required": True,
                "source_hull_model_path": source_path,
                "source_hull_model_resource_id": models[source_path][
                    "resource_id"
                ],
                "source_hull_prototype_location": models[source_path][
                    "prototype_location"
                ],
                "source_node_index": node["index"],
                "source_node_parent_index": node["parent_index"],
                "hp_world_matrix": node["world_matrix"],
                "model_resource_id": models[mount["model_path"]]["resource_id"],
                "model_prototype_location": models[mount["model_path"]][
                    "prototype_location"
                ],
                "correction": correction,
                "corrected_gltf_rh_y_up_matrix": core.matrix_record(corrected),
            }
        )
        combat_mounts.append(item)

    action_overlays: list[dict[str, Any]] = []
    for mount in combat_mounts:
        for action_path in mount["action_model_paths"]:
            if action_path not in models:
                continue
            correction = corrections[action_path]
            corrected = core.mat_mul(
                mount["hp_world_matrix"]["column_major"],
                correction["correction_matrix"]["column_major"],
            )
            action_overlays.append(
                {
                    "parent_hardpoint": mount["hardpoint"],
                    "role": "GameParams auxiliary/action model",
                    "static_default_policy": "recorded but not forced visible",
                    "render_required": True,
                    "model_path": action_path,
                    "model_resource_id": models[action_path]["resource_id"],
                    "model_prototype_location": models[action_path][
                        "prototype_location"
                    ],
                    "source_hull_model_path": mount[
                        "source_hull_model_path"
                    ],
                    "hp_world_matrix": mount["hp_world_matrix"],
                    "correction": correction,
                    "corrected_gltf_rh_y_up_matrix": core.matrix_record(
                        corrected
                    ),
                }
            )

    misc_instances: list[dict[str, Any]] = []
    for sequence, (source_path, node, key) in enumerate(mp_nodes):
        path = misc_key_paths[key]
        if path not in models:
            continue
        correction = corrections[path]
        corrected = core.mat_mul(
            node["world_matrix"]["column_major"],
            correction["correction_matrix"]["column_major"],
        )
        misc_instances.append(
            {
                "sequence": sequence,
                "instance_name": node["name"],
                "asset_key": key,
                "role": (
                    node["name"][len(f"MP_{key}_") :]
                    if node["name"].startswith(f"MP_{key}_")
                    else node["name"]
                ),
                "visibility_condition": (
                    "dock"
                    if source_path.endswith("_ports_dock.model")
                    else "always"
                ),
                "render_required": True,
                "source_hull_model_path": source_path,
                "source_hull_model_resource_id": models[source_path][
                    "resource_id"
                ],
                "source_node_index": node["index"],
                "source_node_parent_index": node["parent_index"],
                "model_path": path,
                "model_resource_id": models[path]["resource_id"],
                "model_prototype_location": models[path][
                    "prototype_location"
                ],
                "mp_world_matrix": node["world_matrix"],
                "correction": correction,
                "corrected_gltf_rh_y_up_matrix": core.matrix_record(corrected),
            }
        )

    hull_parts: list[dict[str, Any]] = []
    for path in hull_paths:
        contract = hull_part_contract(path, hull_model_path, hull_path_set)
        nodes = models[path]["model_uber"]["visual_nodes"]["nodes"]
        hull_parts.append(
            {
                "path": path,
                "resource_id": models[path]["resource_id"],
                "prototype_location": models[path]["prototype_location"],
                **contract,
                "attachment_matrix": core.matrix_record(
                    list(core.IDENTITY)
                ),
                "authored_hp_nodes": [
                    node["name"]
                    for node in nodes
                    if (node["name"] or "").startswith("HP_")
                ],
                "authored_mp_nodes": [
                    node["name"]
                    for node in nodes
                    if (node["name"] or "").startswith("MP_")
                ],
            }
        )

    expected_hps = [item["hardpoint"] for item in gp_mounts]
    resolved_hps = [item["hardpoint"] for item in combat_mounts]
    missing_hps = sorted(
        set(expected_hps) - set(resolved_hps), key=core.natural_key
    )
    duplicate_hps = {
        hardpoint: [source for source, _ in sources]
        for hardpoint, sources in hp_sources.items()
        if hardpoint in expected_hps and len(sources) != 1
    }
    main_artillery_discovered = any(
        _component_category(str(component)) == ("Artillery", "main_artillery")
        for component in gp_metadata["discovered_mount_components"]
    )
    authored_main_hps = sorted(
        (
            hardpoint
            for hardpoint in hp_sources
            if hardpoint.upper().startswith("HP_AGM_")
        ),
        key=core.natural_key,
    )
    resolved_main_hps = {
        item["hardpoint"]
        for item in combat_mounts
        if item["category"] == "main_artillery"
    }
    unmapped_main_hps = (
        sorted(set(authored_main_hps) - resolved_main_hps, key=core.natural_key)
        if main_artillery_discovered
        else []
    )

    required_render_paths = {
        part["path"] for part in hull_parts if part["render_required"]
    } | combat_paths | action_paths | misc_paths
    model_uber_parse_failures = {
        path: models[path]["model_uber_parse_error"]
        for path in sorted(required_render_paths & models.keys())
        if models[path]["model_uber_parse_error"]
    }
    required_without_render_sets = sorted(
        path
        for path in required_render_paths & models.keys()
        if not _render_sets(models[path])
    )
    required_render_sets = [
        render_set
        for path in required_render_paths & models.keys()
        for render_set in _render_sets(models[path])
    ]
    unresolved_render_sets = [
        {
            "material_mfm_path": item.get("material_mfm_path"),
            "material_name": item.get("material_name"),
            "vertices_section": item.get("vertices_section"),
            "indices_section": item.get("indices_section"),
        }
        for item in required_render_sets
        if not all(
            (
                item.get("material_mfm_path"),
                item.get("material_name"),
                item.get("vertices_section"),
                item.get("indices_section"),
            )
        )
    ]
    unknown_render_semantics = [
        item.get("render_set_name")
        for item in required_render_sets
        if item.get("include_in_intact") is None
    ]
    required_texture_properties = [
        prop
        for path in required_render_paths & models.keys()
        for prop in _texture_properties(models[path])
    ]
    unresolved_textures = [
        prop
        for prop in required_texture_properties
        if not prop.get("value", {}).get("path")
    ]

    source_files = {
        label: core.source_file_record(label, path)
        for label, path in (
            ("GameParams.data", args.game_params),
            ("assets.bin", args.assets),
            ("prototypes.index.data", args.prototype_index),
            ("prototypes.data", args.prototype_data),
        )
    }
    output_instances = combat_mounts + action_overlays + misc_instances
    validations = {
        "expected_combat_hardpoints": len(expected_hps),
        "resolved_combat_hardpoints": len(resolved_hps),
        "missing_combat_hardpoints": missing_hps,
        "duplicate_combat_hardpoint_sources": duplicate_hps,
        "authored_main_artillery_hardpoints": authored_main_hps,
        "unmapped_main_artillery_hardpoints": unmapped_main_hps,
        "unique_combat_model_paths": len(combat_paths),
        "action_overlay_instances": len(action_overlays),
        "authored_mp_nodes": len(mp_nodes) + len(unresolved_mp_nodes),
        "misc_instances": len(misc_instances),
        "unresolved_mp_nodes": unresolved_mp_nodes,
        "hull_part_models": len(hull_parts),
        "hull_mesh_models": sum(
            item["role"] == "mesh" for item in hull_parts
        ),
        "unresolved_model_paths": unresolved_model_paths,
        "all_referenced_model_prototypes_resolved": (
            len(models) == len(all_model_paths) and not unresolved_model_paths
        ),
        "model_uber_parse_failures": model_uber_parse_failures,
        "required_render_models_without_render_sets": (
            required_without_render_sets
        ),
        "render_sets_parsed": len(required_render_sets),
        "unresolved_render_set_fields": unresolved_render_sets,
        "unknown_render_semantics": unknown_render_semantics,
        "texture_properties_parsed": len(required_texture_properties),
        "unresolved_texture_paths": unresolved_textures,
        "all_output_matrices_finite": all(
            item["corrected_gltf_rh_y_up_matrix"]["finite"]
            for item in output_instances
        ),
    }
    validations["static_assembly_acceptance"] = all(
        (
            validations["expected_combat_hardpoints"]
            == validations["resolved_combat_hardpoints"],
            not missing_hps,
            not duplicate_hps,
            not unmapped_main_hps,
            validations["hull_mesh_models"] > 0,
            not unresolved_mp_nodes,
            validations["all_referenced_model_prototypes_resolved"],
            not model_uber_parse_failures,
            not required_without_render_sets,
            not unresolved_render_sets,
            not unknown_render_semantics,
            not unresolved_textures,
            validations["all_output_matrices_finite"],
        )
    )

    ship_key_match = re.match(r"(P[A-Z]+\d+)", args.ship_key)
    display_identity = (
        args.ship_key.split("_", 1)[1].replace("_", " ")
        if "_" in args.ship_key
        else args.ship_key
    )
    hull_parts_path = PurePosixPath(hull_model_path).parts
    try:
        ship_token_index = hull_parts_path.index("ship")
    except ValueError:
        ship_token_index = -1
    nation = _asset_nation(hull_model_path)
    ship_class = (
        hull_parts_path[ship_token_index + 1]
        if ship_token_index >= 0
        and ship_token_index + 1 < len(hull_parts_path)
        else None
    )

    return {
        "schema": "wows-legends-static-ship-assembly/v1",
        "generated_by": "build_selected_ship_assembly.py",
        "source_files": source_files,
        "ship": {
            **gp_metadata,
            "index": ship_key_match.group(1) if ship_key_match else None,
            "display_identity": display_identity,
            "asset_nation": nation,
            "asset_class": ship_class,
        },
        "coordinate_system": {
            "source": "BigWorld right-handed Y-up; bow is -Z",
            "target": "glTF 2.0 right-handed Y-up",
            "axis_conversion": "identity",
            "matrix_storage": "column-major",
            "placement_formula": (
                "corrected = HP/MP_world * inverse_rotation("
                "Rotate_Y_BlendBone or Root_BlendBone); identity if absent"
            ),
            "hull_segment_policy": (
                "direct sibling Bow/MidFront/MidBack/Stern geometry and ports "
                "are authored in one ship coordinate space and use identity"
            ),
        },
        "binary_layout": {
            "assets_magic": "BDWB",
            "assets_version": "0x01000000",
            "prototype_index": {
                "count": prototypes.count,
                "seed_or_checksum": core.hex32(
                    prototypes.seed_or_checksum
                ),
                "key": "assets path selfId (u64, sorted)",
                "value": (
                    "packed u64(data_offset<<32 | size) + trailing u32"
                ),
            },
        },
        "hull_parts": hull_parts,
        "combat_mounts": combat_mounts,
        "runtime_action_overlays": action_overlays,
        "misc_instances": misc_instances,
        "models": {path: models[path] for path in sorted(models)},
        "validation": validations,
        "scope": {
            "accepted": (
                "static intact LOD assembly: discovered detailed hull segments, "
                f"{len(combat_mounts)} GameParams combat mounts, and "
                f"{len(misc_instances)} authored MP_* misc instances"
            ),
            "recorded_not_forced_visible": (
                f"{len(action_overlays)} GameParams auxiliary/action models"
            ),
            "not_claimed": (
                "runtime firing animation, hatch state, damage/dead swaps, "
                "particle effects, wakes, or dynamic aiming"
            ),
        },
    }


def acceptance_markdown(data: dict[str, Any]) -> str:
    validation = data["validation"]
    result = "PASS" if validation["static_assembly_acceptance"] else "FAIL"
    rows = "\n".join(
        f"| `{item['hardpoint']}` | `{PurePosixPath(item['model_path']).stem}` | "
        f"`{PurePosixPath(item['source_hull_model_path']).stem}` | "
        f"{tuple(round(value, 6) for value in item['hp_world_matrix']['translation_xyz'])} |"
        for item in data["combat_mounts"]
    )
    return f"""# {data['ship']['display_identity']} static assembly acceptance

Result: **{result}**

- Ship key: `{data['ship']['ship_key']}`
- GameParams combat HP expected/resolved: **{validation['expected_combat_hardpoints']} / {validation['resolved_combat_hardpoints']}**
- Missing HP: `{validation['missing_combat_hardpoints']}`
- Duplicate HP sources: `{validation['duplicate_combat_hardpoint_sources']}`
- Hull models / renderable segments: **{validation['hull_part_models']} / {validation['hull_mesh_models']}**
- Authored/resolved `MP_*`: **{validation['authored_mp_nodes']} / {validation['misc_instances']}**
- Runtime auxiliary overlays: **{validation['action_overlay_instances']}**
- ModelUber parse failures: `{validation['model_uber_parse_failures']}`
- Required models without render sets: `{validation['required_render_models_without_render_sets']}`
- Unknown intact/damage semantics: `{validation['unknown_render_semantics']}`
- All output matrices finite: **{validation['all_output_matrices_finite']}**

## Combat placement

| HP | model | source ports model | BigWorld/glTF translation (x,y,z) |
|---|---|---|---|
{rows}

All placements come from GameParams model references and authored HP/MP world
matrices in the selected ship's discovered hull/ports prototypes. No guessed
mount transforms are emitted.
"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ship-key", required=True)
    parser.add_argument("--selected-model-path")
    parser.add_argument("--game-params", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--prototype-index", type=Path, required=True)
    parser.add_argument("--prototype-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = build(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.acceptance is not None:
        args.acceptance.parent.mkdir(parents=True, exist_ok=True)
        args.acceptance.write_text(
            acceptance_markdown(result), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "acceptance": (
                    str(args.acceptance)
                    if args.acceptance is not None
                    else None
                ),
                "validation": result["validation"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["validation"]["static_assembly_acceptance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
