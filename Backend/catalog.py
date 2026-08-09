from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from mo_reader import MoCatalog, read_mo  # noqa: E402
from game_archive import (  # noqa: E402
    decode_game_params,
    extract_entry,
    find_entry,
    latest_build,
    read_archive_index,
    root_params,
)


NATION_KO = {
    "USA": "미국",
    "Japan": "일본",
    "USSR": "소련",
    "Russia": "소련",
    "Germany": "독일",
    "UK": "영국",
    "United_Kingdom": "영국",
    "France": "프랑스",
    "Italy": "이탈리아",
    "PanAsia": "범아시아",
    "Pan_Asia": "범아시아",
    "PanAmerica": "범아메리카",
    "Pan_America": "범아메리카",
    "Europe": "유럽",
    "Commonwealth": "영연방",
    "Netherlands": "네덜란드",
    "Spain": "스페인",
    "Soviet_Russia": "소련",
}

CLASS_KO = {
    "Battleship": "전함",
    "Cruiser": "순양함",
    "Destroyer": "구축함",
    "AirCarrier": "항공모함",
    "AircraftCarrier": "항공모함",
    "Submarine": "잠수함",
    "Auxiliary": "보조함",
}


def _translation(game_dir: Path, language: str) -> tuple[MoCatalog, str]:
    build, _ = latest_build(game_dir)
    languages = [language, "ko", "en", "ru"]
    seen: set[str] = set()
    for token in languages:
        if token in seen:
            continue
        seen.add(token)
        mo = (
            game_dir
            / "bin"
            / str(build)
            / "res"
            / "texts"
            / token
            / "LC_MESSAGES"
            / "global.mo"
        )
        if mo.is_file():
            return read_mo(mo), token
    return MoCatalog(), "internal"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _model_path(value: Any) -> str:
    if isinstance(value, str) and value.lower().endswith(".model"):
        return value
    if isinstance(value, dict):
        preferred = value.get("model")
        if isinstance(preferred, str) and preferred.lower().endswith(".model"):
            return preferred
        for item in value.values():
            found = _model_path(item)
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _model_path(item)
            if found:
                return found
    return ""


def _fallback_name(param_key: str, index: str) -> str:
    tail = param_key
    if tail.startswith(index + "_"):
        tail = tail[len(index) + 1 :]
    elif "_" in tail:
        tail = tail.split("_", 1)[1]
    return tail.replace("_", " ").strip() or index


def pc_catalog(game_dir: Path, language: str, source: str) -> list[dict[str, Any]]:
    build, entries = read_archive_index(game_dir)
    entry = find_entry(entries, ("GameParams.data", "GameParams_py2.data"))
    raw = extract_entry(game_dir, entry)
    params = root_params(decode_game_params(raw))
    translations, translation_language = _translation(game_dir, language)

    rows: list[dict[str, Any]] = []
    for param_key, param_value in params.items():
        data = _dict(param_value)
        type_info = _dict(data.get("typeinfo"))
        if type_info.get("type") != "Ship":
            continue

        index = str(data.get("index") or str(param_key).split("_", 1)[0])
        internal_name = str(data.get("name") or param_key)
        ids = f"IDS_{index}"
        localized = translations.gettext(ids)
        if not localized or not localized.strip() or localized == ids:
            localized = _fallback_name(str(param_key), index)
        else:
            localized = localized.strip()

        nation_raw = str(type_info.get("nation") or "")
        class_raw = str(type_info.get("species") or "")
        tier_value = data.get("level")
        try:
            tier = int(tier_value)
        except (TypeError, ValueError):
            tier = 0

        model_path = ""
        for key, item in data.items():
            if str(key).endswith("_Hull"):
                model_path = _model_path(item)
                if model_path:
                    break
        if not model_path:
            model_path = _model_path(data)
        resource = ""
        if model_path:
            normalized = model_path.replace("\\", "/")
            parts = normalized.rsplit("/", 2)
            resource = parts[-2] if len(parts) >= 2 else Path(normalized).stem

        rows.append(
            {
                "Source": source,
                "Build": build,
                "ShipCode": index,
                "LocalizedName": localized,
                "LocalizedLanguage": translation_language,
                "InternalName": internal_name,
                "VariantLabel": _fallback_name(str(param_key), index),
                "ShipResource": resource or internal_name,
                "Nation": NATION_KO.get(nation_raw, nation_raw or "기타"),
                "NationRaw": nation_raw,
                "ShipClass": CLASS_KO.get(class_raw, class_raw or "기타"),
                "ShipClassRaw": class_raw,
                "Tier": tier,
                "GameParamsKey": str(param_key),
                "GameParamsIndex": index,
                "ModelPath": model_path,
                "Id": data.get("id"),
                "Supported": bool(model_path),
            }
        )

    rows.sort(
        key=lambda row: (
            row["LocalizedName"].casefold(),
            row["Tier"],
            row["ShipCode"],
        )
    )
    return rows


def legends_catalog(toolbox_root: Path, game_dir: Path, language: str) -> list[dict[str, Any]]:
    script = (
        toolbox_root
        / "BlenderExtractor"
        / "geometry_decoder"
        / "ship_catalog.py"
    )
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            "--game-dir",
            str(game_dir),
            "--language",
            language,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    legacy_rows = json.loads(process.stdout)
    rows: list[dict[str, Any]] = []
    for legacy in legacy_rows:
        ship_code = str(legacy.get("ship_code") or "")
        resource = str(legacy.get("hull_resource") or "")
        suffix_match = re.match(r"^[A-Z]S[A-Z]\d+_(.+)$", resource)
        suffix = suffix_match.group(1) if suffix_match else resource
        game_params_key = (
            f"{ship_code}_{suffix}" if ship_code and suffix else ship_code
        )
        variant = str(
            legacy.get("variant_label")
            or legacy.get("display_label")
            or resource
            or ship_code
        )
        localized = str(legacy.get("localized_name") or "").strip() or variant
        nation_raw = str(legacy.get("nation") or "")
        class_raw = str(legacy.get("class") or "")
        nation = NATION_KO.get(nation_raw, nation_raw or "기타")
        ship_class = CLASS_KO.get(class_raw, class_raw or "기타")
        tier_value = legacy.get("tier")
        try:
            tier = int(tier_value) if tier_value is not None else 0
        except (TypeError, ValueError):
            tier = 0
        rows.append(
            {
                "Source": "legends",
                "Build": "",
                "ShipCode": ship_code,
                "LocalizedName": localized,
                "LocalizedLanguage": str(
                    legacy.get("localized_language") or language
                ),
                "InternalName": game_params_key or resource,
                "VariantLabel": variant,
                "ShipResource": resource,
                "Nation": nation,
                "NationRaw": str(legacy.get("nation_code") or nation_raw),
                "ShipClass": ship_class,
                "ShipClassRaw": str(legacy.get("class_code") or class_raw),
                "Tier": tier,
                "GameParamsKey": game_params_key,
                "GameParamsIndex": ship_code,
                "ModelPath": str(legacy.get("hull_resource_path") or ""),
                "Id": legacy.get("id"),
                "Supported": bool(legacy.get("selectable")),
            }
        )
    rows.sort(
        key=lambda row: (
            row["LocalizedName"].casefold(),
            row["Tier"],
            row["ShipCode"],
        )
    )
    return rows
def main() -> int:
    parser = argparse.ArgumentParser(description="WoWS Toolbox unified ship catalog")
    parser.add_argument("--source", choices=("legends", "pc", "korabli"), required=True)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--toolbox-root", type=Path, required=True)
    parser.add_argument("--language", default="ko")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.source == "legends":
        rows = legends_catalog(args.toolbox_root, args.game_dir, args.language)
    else:
        rows = pc_catalog(args.game_dir, args.language, args.source)

    encoded = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(
            json.dumps(
                {"ok": True, "count": len(rows), "output": str(args.output)},
                ensure_ascii=False,
            )
        )
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
