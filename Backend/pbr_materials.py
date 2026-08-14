from __future__ import annotations

"""Supplement wowsunpack ship GLBs with the game's full-resolution PBR maps.

The bundled exporter deliberately embeds only base color images in its GLB.  This
module resolves the corresponding MFM asset paths, extracts the sibling `_n`,
`_mg`, and `_ao` textures, and publishes viewer/DCC friendly PNG channels.
"""

from collections import defaultdict
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

from PIL import Image, ImageChops, ImageOps, ImageStat


MFM_LINE = re.compile(r"^\(A\)\s+(/.+\.mfm)\s+\d+\s+bytes\s*$", re.IGNORECASE)
MFM_STRIP_SUFFIXES = ("_skinned", "_wire", "_dead", "_blaze", "_alpha")
CHANNEL_SUFFIXES = {"normal": "_n", "metallic_gloss": "_mg", "ao": "_ao"}
SCHEMA = "wows-toolbox-pbr-materials/v2"


def safe_name(value: str, fallback: str = "Texture") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_.()\[\] -]+", "_", value).strip(" ._")
    return (cleaned or fallback)[:120]


def texture_base_names(stem: str) -> list[str]:
    names = [stem]
    folded = stem.casefold()
    for suffix in MFM_STRIP_SUFFIXES:
        if folded.endswith(suffix):
            stripped = stem[: -len(suffix)]
            if stripped and stripped not in names:
                names.append(stripped)
    return names


def parse_mfm_index(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        match = MFM_LINE.match(line.strip())
        if match:
            paths.append(match.group(1).replace("\\", "/"))
    return sorted(set(paths), key=str.casefold)


def mfm_alias_index(paths: Iterable[str]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        stem = PurePosixPath(path).stem
        for alias in texture_base_names(stem):
            key = alias.casefold()
            if path not in aliases[key]:
                aliases[key].append(path)
    return dict(aliases)


def image_name(document: dict, index: int) -> str:
    image = document.get("images", [])[index]
    value = str(image.get("name") or image.get("uri") or f"Texture_{index:03d}")
    return PurePosixPath(value.replace("\\", "/")).stem


def material_image_index(document: dict, material: dict) -> int | None:
    info = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if not isinstance(info, dict) or "index" not in info:
        return None
    textures = document.get("textures", [])
    texture_index = int(info["index"])
    if texture_index < 0 or texture_index >= len(textures):
        return None
    texture = textures[texture_index]
    if "source" in texture:
        return int(texture["source"])
    basis = texture.get("extensions", {}).get("KHR_texture_basisu", {})
    return int(basis["source"]) if "source" in basis else None


def candidate_sets(mfm_path: str, preferred_stem: str) -> list[dict[str, list[str]]]:
    """Return ordered, coherent PBR path sets for one material."""

    path = PurePosixPath(mfm_path)
    directory = path.parent
    directories: list[PurePosixPath] = []
    if directory.name.casefold() == "textures":
        directories.append(directory)
    else:
        directories.append(directory.parent / "textures")
        directories.append(directory)
        directories.append(directory / "TILED")

    bases: list[str] = []
    for value in [preferred_stem, *texture_base_names(path.stem)]:
        if value and value.casefold() not in {item.casefold() for item in bases}:
            bases.append(value)

    result: list[dict[str, list[str]]] = []
    seen: set[tuple[str, str]] = set()
    for base in bases:
        for candidate_dir in directories:
            key = (candidate_dir.as_posix().casefold(), base.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    channel: [
                        f"/{candidate_dir.as_posix().lstrip('/')}/{base}{suffix}.dd0",
                        f"/{candidate_dir.as_posix().lstrip('/')}/{base}{suffix}.dds",
                    ]
                    for channel, suffix in CHANNEL_SUFFIXES.items()
                }
            )
    return result


def _normal_square_lookup() -> list[int]:
    return [min(255, round(((value / 127.5 - 1.0) ** 2) * 255.0)) for value in range(256)]


def _normal_sqrt_lookup() -> list[int]:
    return [round((0.5 + 0.5 * math.sqrt(value / 255.0)) * 255.0) for value in range(256)]


def reconstruct_tangent_normal(image: Image.Image) -> Image.Image:
    """Preserve signed tangent X/Y and reconstruct the conventional positive Z."""

    red, green, _blue, alpha = image.convert("RGBA").split()
    x_squared = red.point(_normal_square_lookup())
    y_squared = green.point(_normal_square_lookup())
    remaining = ImageChops.invert(ImageChops.add(x_squared, y_squared))
    z_axis = remaining.point(_normal_sqrt_lookup())
    return Image.merge("RGBA", (red, green, z_axis, alpha))


def split_metallic_gloss(image: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Return source MG, roughness, and metalness images.

    Observed WoWS PBS `_mg` maps use red as gloss/smoothness and green as
    metalness. DCC roughness is therefore `1-R`; metalness is copied from G.  The untouched MG
    map is retained beside the derived maps so the source channel contract is not
    lost and can be revised without re-extracting the game package.
    """

    source = image.convert("RGBA")
    red, green, _blue, _alpha = source.split()
    roughness_channel = ImageOps.invert(red)
    roughness = Image.merge("RGB", (roughness_channel,) * 3)
    metalness = Image.merge("RGB", (green,) * 3)
    return source, roughness, metalness


def specular_from_metallic_gloss(image: Image.Image) -> Image.Image:
    """Return the legacy WoWS/GM3D specular-gloss view of an `_mg` map."""

    red = image.convert("RGBA").getchannel("R")
    return Image.merge("RGB", (red,) * 3)


def extract_ambient_occlusion(image: Image.Image) -> tuple[Image.Image, str]:
    """Extract AO from the RGB channel that actually contains image data.

    Standard PC/Legends textures normally store AO in R. Newer indexed
    Korabli materials have been observed with an almost constant R/B pair and
    their AO payload in G, so select the channel with the largest variance.
    """

    channels = image.convert("RGBA").split()[:3]
    deviations = [float(ImageStat.Stat(channel).stddev[0]) for channel in channels]
    index = max(range(3), key=lambda item: deviations[item])
    selected = channels[index]
    return Image.merge("RGB", (selected,) * 3), "RGB"[index]


def _resize(image: Image.Image, max_size: int) -> tuple[Image.Image, bool]:
    if max_size <= 0 or max(image.size) <= max_size:
        return image, False
    scale = max_size / max(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS), True


def _cache_prefix(cache_root: Path, logical_path: str) -> Path:
    digest = hashlib.sha256(logical_path.casefold().encode("utf-8")).hexdigest()[:20]
    return cache_root / "maps" / digest


def _cache_outputs(cache_root: Path, logical_path: str, channel: str) -> dict[str, Path]:
    prefix = _cache_prefix(cache_root, logical_path)
    if channel == "normal":
        return {"normal": prefix.with_name(prefix.name + "_normal.png")}
    if channel == "ao":
        # v2 invalidates the old R-only AO conversion cache.
        return {"ao": prefix.with_name(prefix.name + "_ao_v2.png")}
    return {
        "metallic_gloss": prefix.with_name(prefix.name + "_metallic_gloss.png"),
        "specular": prefix.with_name(prefix.name + "_specular.png"),
        "roughness": prefix.with_name(prefix.name + "_roughness.png"),
        "metalness": prefix.with_name(prefix.name + "_metalness.png"),
    }


def _valid_cached(cache_root: Path, logical_path: str, channel: str) -> bool:
    outputs = _cache_outputs(cache_root, logical_path, channel)
    return all(path.is_file() and path.stat().st_size > 0 for path in outputs.values())


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def load_mfm_paths(exporter: Path, game_dir: Path, cache_root: Path) -> tuple[list[str], bool]:
    cache_file = cache_root / "mfm-paths.json"
    if cache_file.is_file():
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            paths = payload.get("paths")
            if payload.get("schema") == SCHEMA and isinstance(paths, list) and paths:
                return [str(item) for item in paths], True
        except (OSError, json.JSONDecodeError):
            pass

    result = _run([str(exporter), "--game-dir", str(game_dir), "list", "", "--assets"])
    paths = parse_mfm_index(result.stdout)
    if not paths:
        raise RuntimeError("The asset index did not return any MFM material paths")
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_suffix(f".json.{os.getpid()}.part")
    temporary.write_text(
        json.dumps({"schema": SCHEMA, "paths": paths}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, cache_file)
    return paths, False


def _chunks(paths: Iterable[str], max_characters: int = 22000, max_items: int = 140):
    chunk: list[str] = []
    characters = 0
    for path in paths:
        extra = len(path) + 3
        if chunk and (len(chunk) >= max_items or characters + extra > max_characters):
            yield chunk
            chunk = []
            characters = 0
        chunk.append(path)
        characters += extra
    if chunk:
        yield chunk


def _extract_paths(exporter: Path, game_dir: Path, raw_root: Path, paths: Iterable[str]) -> int:
    unique = sorted(set(paths), key=str.casefold)
    calls = 0
    for chunk in _chunks(unique):
        _run([str(exporter), "--game-dir", str(game_dir), "extract", "-o", str(raw_root), *chunk])
        calls += 1
    return calls


def _raw_path(raw_root: Path, logical_path: str) -> Path:
    return raw_root.joinpath(*PurePosixPath(logical_path.lstrip("/")).parts)


def _channel_from_path(logical_path: str) -> str:
    folded = PurePosixPath(logical_path).stem.casefold()
    for channel, suffix in CHANNEL_SUFFIXES.items():
        if folded.endswith(suffix):
            return channel
    raise ValueError(f"Unsupported PBR channel path: {logical_path}")


def _convert_to_cache(
    source: Path,
    cache_root: Path,
    logical_path: str,
    channel: str,
    max_size: int,
) -> tuple[dict[str, Path], bool]:
    outputs = _cache_outputs(cache_root, logical_path, channel)
    if _valid_cached(cache_root, logical_path, channel):
        return outputs, False
    next(iter(outputs.values())).parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        converted, resized = _resize(opened.convert("RGBA"), max_size)
        if channel == "normal":
            images = {"normal": reconstruct_tangent_normal(converted)}
        elif channel == "ao":
            ao, _source_channel = extract_ambient_occlusion(converted)
            images = {"ao": ao}
        else:
            source_mg, roughness, metalness = split_metallic_gloss(converted)
            images = {
                "metallic_gloss": source_mg,
                "specular": specular_from_metallic_gloss(source_mg),
                "roughness": roughness,
                "metalness": metalness,
            }
        for name, image in images.items():
            target = outputs[name]
            temporary = target.with_suffix(f".png.{os.getpid()}.part")
            image.save(temporary, format="PNG", compress_level=3)
            os.replace(temporary, target)
    return outputs, resized


def _publish(cache_path: Path, texture_dir: Path, stem: str, role: str) -> str:
    texture_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(cache_path.name.encode("utf-8")).hexdigest()[:8]
    target = texture_dir / f"{safe_name(stem)}_{digest}_{role}.png"
    if not target.is_file():
        shutil.copy2(cache_path, target)
    return f"textures/{target.name}"


def prepare_pbr_materials(
    document: dict,
    *,
    exporter: Path,
    game_dir: Path,
    texture_dir: Path,
    work_dir: Path,
    cache_root: Path,
    max_size: int = 0,
) -> dict:
    """Extract, convert, and publish PBR maps aligned to GLB material indices."""

    materials = document.get("materials", [])
    contract: dict = {
        "schema": SCHEMA,
        "source_contract": {
            "normal": "WoWS signed tangent XY in RG; positive Z reconstructed",
            "metallic_gloss": "source `_mg` retained; inferred R=gloss, G=metalness",
            "specular": "GM3D-compatible grayscale view of `_mg`.R",
            "roughness": "1 - `_mg`.R",
            "metalness": "`_mg`.G",
            "ao": "highest-information RGB channel from `_ao` (R on standard PBS, often G on indexed PBS)",
        },
        "materials": [None] * len(materials),
        "coverage": {"materials": len(materials), "pbr_materials": 0, "channels": 0},
        "cache": {},
        "warnings": [],
    }
    paths, index_reused = load_mfm_paths(exporter, game_dir, cache_root)
    aliases = mfm_alias_index(paths)
    contract["cache"]["mfm_index_reused"] = index_reused
    raw_root = work_dir / "pbr-raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    availability_file = cache_root / "availability.json"
    try:
        availability_payload = json.loads(availability_file.read_text(encoding="utf-8"))
        unavailable = set(availability_payload.get("unavailable", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        unavailable = set()

    resolutions: list[dict | None] = []
    for material in materials:
        source_index = material_image_index(document, material)
        if source_index is None:
            resolutions.append(None)
            continue
        stem = image_name(document, source_index)
        candidates = aliases.get(stem.casefold(), [])
        resolutions.append(
            {
                "stem": stem,
                "image_index": source_index,
                "sets": [item for path in candidates for item in candidate_sets(path, stem)],
            }
        )

    # Prefer the highest-resolution `.dd0` source.  Only unresolved channels fall
    # back to the small `.dds` mip tail in a second pass.
    requested_dd0 = []
    for resolution in resolutions:
        if resolution:
            for candidate in resolution["sets"]:
                for channel, values in candidate.items():
                    if values[0] not in unavailable and not _valid_cached(cache_root, values[0], channel):
                        requested_dd0.append(values[0])
    extraction_calls = _extract_paths(exporter, game_dir, raw_root, requested_dd0) if requested_dd0 else 0
    unavailable.update(
        logical
        for logical in requested_dd0
        if not _raw_path(raw_root, logical).is_file()
        and not _valid_cached(cache_root, logical, _channel_from_path(logical))
    )

    def available(logical: str, channel: str) -> bool:
        return _valid_cached(cache_root, logical, channel) or (
            logical not in unavailable and _raw_path(raw_root, logical).is_file()
        )

    requested_dds: list[str] = []
    for resolution in resolutions:
        if not resolution:
            continue
        for channel in CHANNEL_SUFFIXES:
            if any(available(candidate[channel][0], channel) for candidate in resolution["sets"]):
                continue
            requested_dds.extend(
                candidate[channel][1]
                for candidate in resolution["sets"]
                if candidate[channel][1] not in unavailable
                and not _valid_cached(cache_root, candidate[channel][1], channel)
            )
    if requested_dds:
        extraction_calls += _extract_paths(exporter, game_dir, raw_root, requested_dds)
        unavailable.update(
            logical
            for logical in requested_dds
            if not _raw_path(raw_root, logical).is_file()
            and not _valid_cached(cache_root, logical, _channel_from_path(logical))
        )
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary_availability = availability_file.with_suffix(f".json.{os.getpid()}.part")
    temporary_availability.write_text(
        json.dumps({"schema": SCHEMA, "unavailable": sorted(unavailable)}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_availability, availability_file)

    resized_count = 0
    published: set[str] = set()
    for material_index, resolution in enumerate(resolutions):
        if not resolution:
            continue
        selected: dict[str, str] = {}
        for candidate in resolution["sets"]:
            found = {
                channel: next(
                    (logical for logical in values if available(logical, channel)),
                    None,
                )
                for channel, values in candidate.items()
            }
            if any(found.values()):
                selected = {key: value for key, value in found.items() if value}
                if len(selected) == len(CHANNEL_SUFFIXES):
                    break
        if not selected:
            continue
        entry = {
            "material_index": material_index,
            "material_name": str(materials[material_index].get("name") or f"Material_{material_index:03d}"),
            "base_image": resolution["stem"],
            "maps": {},
            "sources": selected,
        }
        for channel, logical in selected.items():
            cache_outputs = _cache_outputs(cache_root, logical, channel)
            if not _valid_cached(cache_root, logical, channel):
                cache_outputs, resized = _convert_to_cache(
                    _raw_path(raw_root, logical), cache_root, logical, channel, max_size
                )
                resized_count += int(resized)
            for role, cache_path in cache_outputs.items():
                relative = _publish(cache_path, texture_dir, resolution["stem"], role)
                entry["maps"][role] = relative
                published.add(relative)
        contract["materials"][material_index] = entry
        contract["coverage"]["pbr_materials"] += 1
        contract["coverage"]["channels"] += len(entry["maps"])

    contract["cache"].update(
        {
            "extraction_calls": extraction_calls,
            "converted_maps_reused": sum(
                1
                for resolution in resolutions
                if resolution
                for candidate in resolution["sets"]
                for channel, values in candidate.items()
                if any(_valid_cached(cache_root, value, channel) for value in values)
            ),
            "maps_resized": resized_count,
        }
    )
    contract["texture_files"] = sorted(published)
    if contract["coverage"]["pbr_materials"] == 0:
        contract["warnings"].append("No matching PBR material maps were found")
    shutil.rmtree(raw_root, ignore_errors=True)
    return contract
