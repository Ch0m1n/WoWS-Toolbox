"""Hull-compatible research loadouts for PC and Korabli ships.

Upgrade IDs are user selections; component IDs are resolved by intersecting the
chosen upgrade with the chosen hull. Never borrow another hull's mount layout.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
import threading

MODULE_CATALOG_VERSION = 1
_cache_lock = threading.Lock()


def validate_module_id(value):
    value = str(value or "top").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", value) or ".." in value:
        raise ValueError("Invalid ship module identifier")
    return value


def _names(value):
    if isinstance(value, str):
        return [value] if value else []
    return [s for s in (value or []) if isinstance(s, str) and s]


def module_options(data):
    upgrades = {str(k): v for k, v in data.get("ShipUpgradeInfo", {}).items()
                if isinstance(v, dict) and isinstance(v.get("components"), dict)}

    def depth(key, seen=()):
        if key in seen:
            raise ValueError("Cyclic ship research dependencies")
        prev = upgrades.get(key, {}).get("prev", "")
        return 1 + depth(prev, (*seen, key)) if prev in upgrades else 0

    def ordered(kind):
        return sorted((k for k, v in upgrades.items() if v.get("ucType") == kind),
                      key=lambda k: (depth(k), k))

    hull_keys = ordered("_Hull")
    hulls = []
    for number, key in enumerate(hull_keys):
        components = upgrades[key]["components"]
        hull_names = _names(components.get("hull"))
        if not hull_names:
            continue
        hull_data = data.get(hull_names[0], {})
        model = hull_data.get("model", "")
        if not model:
            continue
        hull = {"Id": key, "Name": chr(65 + number) if number < 26 else str(number + 1),
                "Component": hull_names[0], "ModelPath": model}
        for field, kind in (("Artillery", "_Artillery"), ("Torpedoes", "_Torpedoes")):
            allowed = _names(components.get(field.lower()))
            options = []
            for upgrade in ordered(kind):
                matches = [s for s in _names(upgrades[upgrade]["components"].get(field.lower()))
                           if s in allowed]
                if len(matches) > 1:
                    raise ValueError(f"Ambiguous {key}/{upgrade} component mapping")
                if not matches:
                    continue
                options.append({"Id": upgrade, "Component": matches[0]})
            # Special ships may declare components only on the hull upgrade.
            if not options:
                options = [{"Id": name, "Component": name} for name in allowed]
            for i, option in enumerate(options):
                comp = data.get(option["Component"], {})
                mounts = [v for k, v in comp.items() if k.startswith("HP_")
                          and isinstance(v, dict) and v.get("model")]
                option.update(Name=chr(65 + i) if i < 26 else str(i + 1),
                              Mounts=len(mounts),
                              Models=sorted({v.get("name", Path(v["model"]).stem) for v in mounts}))
            hull[field] = options
        hulls.append(hull)
    return {"Version": MODULE_CATALOG_VERSION, "Hulls": hulls}


def resolve_options(options, hull="top", artillery="top", torpedoes="top"):
    selections = [validate_module_id(s) for s in (hull, artillery, torpedoes)]
    hulls = options["Hulls"]
    if not hulls:
        if any(s != "top" for s in selections):
            raise ValueError("This ship has no selectable research modules")
        return {"hull_upgrade": "", "artillery_component": "", "torpedo_component": ""}
    selected = hulls[-1] if selections[0] == "top" else next(
        (v for v in hulls if v["Id"] == selections[0]), None)
    if selected is None:
        raise ValueError("Selected hull is unavailable; refresh the ship catalog")
    result = {"hull_upgrade": selected["Id"], "model_path": selected["ModelPath"]}
    for field, selection, target in zip(("Artillery", "Torpedoes"), selections[1:],
                                         ("artillery_component", "torpedo_component")):
        choices = selected[field]
        choice = (choices[-1] if choices else None) if selection == "top" else next(
            (v for v in choices if v["Id"] == selection), None)
        if choice is None and selection != "top":
            raise ValueError(f"Selected {field} is incompatible with this hull")
        result[target] = choice["Component"] if choice else ""
    return result


@lru_cache(maxsize=2)
def _load_options(path, size, stamp):
    from game_archive import decode_game_params, root_params
    # Persist only compact research metadata so subsequent exports do not decode
    # the complete GameParams again. Invalidate on source stamp and schema.
    cache = Path(path).with_name("ship_modules_v1.json")
    identity = [MODULE_CATALOG_VERSION, size, stamp]
    with _cache_lock:
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload.get("identity") == identity and isinstance(payload.get("ships"), dict):
                return payload["ships"]
        except (OSError, ValueError):
            pass
        params = root_params(decode_game_params(Path(path).read_bytes()))
        ships = {}
        for data in params.values():
            if not isinstance(data, dict) or not isinstance(data.get("ShipUpgradeInfo"), dict):
                continue
            try:
                ships[str(data.get("index", ""))] = module_options(data)
            except ValueError as exc:
                ships[str(data.get("index", ""))] = {"Error": str(exc)}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache.parent,
                                             prefix="ship-modules-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({"identity": identity, "ships": ships}, file)
            os.replace(temporary, cache)
        except OSError:
            pass  # Read-only caches still permit extraction.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return ships


def resolve_loadout(params_path, ship_index, hull="top", artillery="top", torpedoes="top"):
    path = Path(params_path)
    stat = path.stat()
    options = _load_options(str(path.resolve()), stat.st_size, stat.st_mtime_ns).get(ship_index)
    if options is None:
        raise ValueError("Ship research data is missing")
    if "Error" in options:
        raise ValueError(options["Error"])
    return resolve_options(options, hull, artillery, torpedoes)


def loadout_key(args):
    """Short, collision-resistant identity, also distinct from all pre-feature caches."""
    values = [str(getattr(args, name, "") or "top") for name in
              ("hull_upgrade", "artillery_upgrade", "torpedo_upgrade")]
    return "modules-" + hashlib.sha256(json.dumps([MODULE_CATALOG_VERSION, values]).encode()).hexdigest()[:16]
