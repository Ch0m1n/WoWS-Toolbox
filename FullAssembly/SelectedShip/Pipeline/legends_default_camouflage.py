#!/usr/bin/env python3
"""Resolve a Legends ship's authored default exterior into extractable textures.

The active exterior is selected by GameParams ``state[27]`` upstream.  This
module deliberately ignores the optional/purchasable exterior list and only
turns that already-selected appearance into a small rendering profile.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


MAT_CAMO_PREFIX = "content/gameplay/common/camouflage/textures/matCamo/"
MASK_PREFIX = "content/gameplay/common/camouflage/textures/"

# Game style identifiers do not always repeat the DDS stem.  Keep aliases
# deterministic: a missing alias is safer than silently choosing a wrong skin.
MATERIAL_STYLE_ALIASES: dict[str, tuple[str, ...]] = {
    "mat_steelstyle2021": ("mat_Steel_01",),
    "mat_golden_tint": ("mat_Gold_01", "mat_Golden_01"),
    # Active Legends exterior styles whose authored material names differ from
    # the installed matCamo DDS stems. The list is intentionally explicit:
    # guessing a nearby material can turn one event skin into another one.
    "mat_blackfriday_2019": ("mat_Black_01",),
    "mat_blackfriday_2021": ("mat_Black_01",),
    "mat_blackfriday_2022": ("mat_Black_02",),
    "mat_ge25_tint": ("mat_GW25",),
    "mat_snowskins_2022": ("mat_White_Snow_01",),
    "mat_snowskins_2023": ("mat_White_Snow_01",),
    "mat_snowskins_2024": ("mat_White_Snow_01",),
    "mat_snowskins_2025": ("mat_White_Snow_01",),
    "mat_pirates_2025_tint": ("mat_Pirate_Grey",),
    "mat_space_b8d": ("mat_space",),
    "mat_hw24_suzuya_tint": ("mat_Suzuya_HW24",),
    "mat_postap_2025": ("mat_PostAp2025",),
    "mat_black01_tint": ("mat_Black_01",),
    "mat_goldenmonth2025": ("MC_ZF_6_GoldenMonth",),
    "mat_indomitable_azur_24_tint": ("mat_AzurLane_24_Indomitable",),
    "mat_ba_binah": ("MC_BA_Binah",),
    "mat_red01_tint": ("mat_Red_01",),
    "mat_azurlane_2025_z_23": ("mat_Blue_Azur",),
    "mat_hw24_kansas_tint": ("mat_Kansas_HW24",),
    "mat_montana_blue_archive_tint": ("mat_Montana_Hoshino",),
    "mat_black_tint2": ("mat_Black_02",),
    "mat_azurbrown": ("mat_Azur_Brown",),
    "mat_azurlane_2025_tallinn": ("mat_Azur_Talin",),
    "mat_gambia_me": ("mat_ME",),
    "mat_azurpurple": ("mat_Purple_Azur",),
    "mat_ba_vladivostok": ("MC_PRES319_BA_Vladivostok",),
    "mat_gambia_me1": ("mat_ME1",),
    "mat_orjen_2024": ("mat_Orjen",),
    "mat_alaska_blackfriday_2024": ("mat_BlackFriday24",),
    "mat_duckskin_2025": ("mat_ID26",),
    "mat_ba_brest": ("MC_BA_Brest",),
    "mat_smaland_postap_2024": ("mat_PostAp2025",),
    "mat_azurlane_2025_vittorio": ("mat_White_Azur",),
    "mat_petropavlovsk_postap_2024": ("mat_Postap_01",),
}


# Fallback palettes are used only when the active authored exterior is an RGB
# mask camouflage and its encrypted Legends palette table is unavailable.
# Values are deliberately restrained naval colours; the exact mask still comes
# from the selected ship's installed game assets.
NATION_PALETTES: dict[str, tuple[str, str, str, str]] = {
    "A": ("#4f5f67", "#9aa5a8", "#33424a", "#707b7e"),
    "B": ("#59666a", "#a3a8a5", "#35484b", "#7f8580"),
    "F": ("#586b73", "#9aa9ab", "#36484f", "#7a888b"),
    "G": ("#515e61", "#9ba19e", "#303b3e", "#737a77"),
    "I": ("#58645f", "#a7aaa1", "#3a4640", "#7d837a"),
    "J": ("#59615c", "#9da29a", "#38443d", "#787e75"),
    "R": ("#53636a", "#99a3a4", "#33464e", "#707d80"),
}
DEFAULT_PALETTE = ("#54646a", "#a0a7a5", "#34464b", "#747e7e")


def _native_exterior(mapping: Mapping[str, Any]) -> Mapping[str, Any] | None:
    ship = mapping.get("ship")
    if isinstance(ship, Mapping) and isinstance(ship.get("native_exterior"), Mapping):
        return ship["native_exterior"]
    exterior = mapping.get("native_exterior")
    return exterior if isinstance(exterior, Mapping) else None


def _styles(mapping: Mapping[str, Any]) -> list[str]:
    exterior = _native_exterior(mapping)
    if not isinstance(exterior, Mapping):
        return []
    values = exterior.get("camouflage_styles", exterior.get("material_tints", []))
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if isinstance(value, str)
            and value.strip()
            and value.startswith(("mat_", "camo_"))
        )
    )


def _material_candidates(style: str) -> list[str]:
    candidates = list(MATERIAL_STYLE_ALIASES.get(style.casefold(), ()))
    candidates.append(style)
    if style.casefold().endswith("_tint"):
        candidates.append(style[:-5])
    if style.casefold().endswith("style2021"):
        candidates.append(style[: -len("Style2021")])
    return list(dict.fromkeys(value for value in candidates if value))


def _material_assets(
    style: str, available_paths: Mapping[str, str]
) -> dict[str, str] | None:
    for stem in _material_candidates(style):
        base = MAT_CAMO_PREFIX + stem
        diffuse = None
        for candidate in (base + "_a.dds", base + ".dds"):
            diffuse = available_paths.get(candidate.casefold())
            if diffuse:
                break
        if not diffuse:
            continue
        maps = {"a": diffuse}
        for suffix in ("_mgn.dds", "_mg.dds"):
            extra = available_paths.get((base + suffix).casefold())
            if extra:
                maps["mg"] = extra
                break
        return maps
    return None


def _mask_number(style: str) -> int:
    match = re.fullmatch(r"camo_permanent_(\d+)", style, flags=re.IGNORECASE)
    return max(1, int(match.group(1))) if match else 1


def _mask_suffixes(style: str) -> tuple[str, ...]:
    folded = style.casefold()
    if folded == "camo_wowsl_preorder":
        return ("camo_console",)
    if folded == "camo_wowsl_disk":
        return ("camo_02", "camo_01")
    if folded == "camo_console_alpha":
        return ("camo_vd", "camo_01")
    if folded in {"camo_hw2023_badguy_01", "camo_hw2024_badguy_01"}:
        return ("camo_02", "camo_01")
    number = _mask_number(style)
    return (f"camo_{number:02d}", f"camo{number:02d}")


def _diffuse_stem(logical: str) -> str | None:
    name = PurePosixPath(logical.replace("\\", "/")).name
    if not name.casefold().endswith("_a.dds"):
        return None
    return name[:-6]


def _mask_stem(logical: str, suffixes: Sequence[str]) -> str | None:
    name = PurePosixPath(logical.replace("\\", "/")).name
    if not name.casefold().endswith(".dds"):
        return None
    stem = name[:-4]
    folded = stem.casefold()
    for suffix in suffixes:
        marker = "_" + suffix.casefold()
        if folded.endswith(marker):
            return stem[: -len(marker)]
    return None


def _identity_tokens(mapping: Mapping[str, Any]) -> list[str]:
    ship = mapping.get("ship")
    if not isinstance(ship, Mapping):
        return []
    values: list[str] = []
    identity = ship.get("display_identity")
    if isinstance(identity, str) and identity.strip():
        values.append(identity.strip())
    ship_key = ship.get("ship_key")
    if isinstance(ship_key, str) and "_" in ship_key:
        values.append(ship_key.split("_", 1)[1])
    return list(
        dict.fromkeys(
            re.sub(r"[^a-z0-9]+", "", value.casefold())
            for value in values
            if re.sub(r"[^a-z0-9]+", "", value.casefold())
        )
    )


def _ship_diffuse_stems(base_texture_paths: Sequence[str]) -> list[str]:
    stems: list[str] = []
    for logical in base_texture_paths:
        normalized = logical.replace("\\", "/")
        stem = _diffuse_stem(normalized)
        if stem and "/ship/" in normalized.casefold():
            stems.append(stem)
    return list(dict.fromkeys(stems))


def _mask_candidates(
    mapping: Mapping[str, Any],
    base_texture_paths: Sequence[str],
    style: str,
    available_paths: Mapping[str, str],
) -> list[dict[str, str]]:
    suffixes = _mask_suffixes(style)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for logical in base_texture_paths:
        stem = _diffuse_stem(logical)
        if stem is None:
            continue
        for suffix in suffixes:
            wanted = f"{MASK_PREFIX}{stem}_{suffix}.dds"
            resolved = available_paths.get(wanted.casefold())
            if not resolved or resolved.casefold() in seen:
                continue
            seen.add(resolved.casefold())
            found.append(
                {
                    "base_texture_stem": stem,
                    "mask": resolved,
                }
            )
            break

    # A playable Legends alias can reuse an older hull while its mask is named
    # after the alias itself. Shinonome/Fubuki is the canonical example.
    identities = _identity_tokens(mapping)
    ship_stems = _ship_diffuse_stems(base_texture_paths)
    if not identities or not ship_stems:
        return found
    identity_masks: list[tuple[str, str]] = []
    for resolved in available_paths.values():
        mask_base = _mask_stem(resolved, suffixes)
        if mask_base is None:
            continue
        normalized = re.sub(r"[^a-z0-9]+", "", mask_base.casefold())
        if any(identity in normalized for identity in identities):
            identity_masks.append((mask_base, resolved))
    matched_stems = {
        item["base_texture_stem"].casefold()
        for item in found
        if isinstance(item.get("base_texture_stem"), str)
    }
    if (
        len(ship_stems) == 1
        and len(identity_masks) == 1
        and ship_stems[0].casefold() not in matched_stems
    ):
        resolved = identity_masks[0][1]
        if resolved.casefold() not in seen:
            seen.add(resolved.casefold())
            found.append(
                {
                    "base_texture_stem": ship_stems[0],
                    "mask": resolved,
                }
            )
        return found

    roles = ("deckhouse", "deck_house", "bulbous", "bulge", "hull", "deck")
    for mask_base, resolved in identity_masks:
        folded_mask = mask_base.casefold()
        role = next((value for value in roles if value in folded_mask), None)
        if role is None:
            continue
        compact_role = role.replace("_", "")
        matches = [
            stem
            for stem in ship_stems
            if compact_role in re.sub(r"[^a-z0-9]+", "", stem.casefold())
        ]
        if (
            len(matches) != 1
            or matches[0].casefold() in matched_stems
            or resolved.casefold() in seen
        ):
            continue
        seen.add(resolved.casefold())
        found.append({"base_texture_stem": matches[0], "mask": resolved})
    return found


def resolve_default_camouflage(
    mapping: Mapping[str, Any],
    base_texture_paths: Sequence[str],
    available_virtual_paths: Iterable[str],
) -> dict[str, Any] | None:
    """Return a rendering profile plus the additional resources it needs."""

    styles = _styles(mapping)
    if not styles:
        return None
    available = {
        path.replace("\\", "/").casefold(): path.replace("\\", "/")
        for path in available_virtual_paths
        if isinstance(path, str) and path.casefold().endswith(".dds")
    }
    exterior = _native_exterior(mapping)
    exterior_id = exterior.get("id") if isinstance(exterior, Mapping) else None
    warnings: list[str] = []

    # Azur Lane and related collaboration skins can ship their appearance as
    # authored replacement models. In that schema `camo_white_tint2` is a
    # routing marker, not a white albedo to paint over every material.
    model_replacements = (
        exterior.get("model_replacements")
        if isinstance(exterior, Mapping)
        else None
    )
    if (
        isinstance(model_replacements, Mapping)
        and model_replacements
        and any(style.casefold() == "camo_white_tint2" for style in styles)
    ):
        return {
            "schema": "wows-legends-default-camouflage/v1",
            "exterior_id": exterior_id,
            "style": "camo_white_tint2",
            "mode": "authored_models",
            "resources": [],
            "warnings": warnings,
        }

    for style in styles:
        if not style.startswith("mat_"):
            continue
        maps = _material_assets(style, available)
        # matCamo textures are shader layers, not replacements for every
        # diffuse/MG map used by a ship. Treating one of these tiling textures
        # as the complete material erased all authored detail on special ships
        # such as Mecklenburg G and Aki SE. Until the game shader's per-part
        # blend controls are available, preserve each model's authored maps.
        # Keep the resolved layer paths as diagnostics only; they are not
        # extraction inputs and must never be written over the base maps.
        material_warnings = list(warnings)
        if maps:
            material_warnings.append(
                "material-style DDS is a shader layer; authored model textures "
                "were preserved instead of applying a destructive global override"
            )
        else:
            material_warnings.append(
                f"active material style has no deterministic DDS alias: {style}"
            )
        return {
            "schema": "wows-legends-default-camouflage/v1",
            "exterior_id": exterior_id,
            "style": style,
            "mode": "preserve_authored_textures",
            "style_texture_maps": maps or {},
            "resources": [],
            "warnings": material_warnings,
        }

    for style in styles:
        if not style.startswith("camo_"):
            continue
        masks = _mask_candidates(mapping, base_texture_paths, style, available)
        if masks:
            ship = mapping.get("ship")
            ship_key = (
                str(ship.get("ship_key") or "")
                if isinstance(ship, Mapping)
                else ""
            )
            nation = ship_key[1:2].upper() if len(ship_key) > 1 else ""
            palette = NATION_PALETTES.get(nation, DEFAULT_PALETTE)
            return {
                "schema": "wows-legends-default-camouflage/v1",
                "exterior_id": exterior_id,
                "style": style,
                "mode": "palette_mask",
                "masks": masks,
                "palette": list(palette),
                "blend": 0.88,
                "resources": sorted(
                    {item["mask"] for item in masks}, key=str.casefold
                ),
                "warnings": warnings + [
                    "installed ship mask used with the built-in naval fallback palette"
                ],
            }
        warnings.append(f"active camouflage style has no installed ship mask: {style}")

    return {
        "schema": "wows-legends-default-camouflage/v1",
        "exterior_id": exterior_id,
        "style": styles[0],
        "mode": "unresolved",
        "resources": [],
        "warnings": warnings,
    }


def resource_definitions(profile: Mapping[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(profile, Mapping):
        return []
    resources = profile.get("resources", [])
    if not isinstance(resources, list):
        return []
    return [
        {"kind": "texture", "path": value, "source": "active_default_camouflage"}
        for value in resources
        if isinstance(value, str) and value
    ]
