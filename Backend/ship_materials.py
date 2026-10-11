"""Build-specific hull material classification; code names are never a detector."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from game_archive import extract_entry, find_entry

SHIP_MATERIAL_CATALOG_VERSION = 1


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as file:
        temporary = Path(file.name)
        file.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_identity(game: Path, build: int) -> dict[str, Any]:
    indexes = sorted((game / "bin" / str(build) / "idx").glob("*.idx"))
    return {
        "game": str(game.resolve()).casefold(),
        "build": build,
        "indexes": [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in indexes],
    }


def annotate_ship_materials(
    rows: list[dict[str, Any]], game: Path, build: int, entries: dict,
    *, cache_root: Path | None = None, backend_root: Path | None = None,
    refresh: bool = False,
) -> None:
    """Failure preserves a usable catalog with explicit unknown status.

    Cache identity includes install path, build, all IDX stamps, requested models
    and probe binary hash. Explicit refresh retries unresolved models as well.
    """
    for row in rows:
        row.update(ShipMaterialCatalogVersion=SHIP_MATERIAL_CATALOG_VERSION,
                   ShipMaterialStatus="unknown", ShipMaterialEvidence=[],
                   ShipMaterialReason="Material scan unavailable")
    models = sorted({row.get("ModelPath", "") for row in rows if row.get("ModelPath")})
    if not models:
        return
    try:
        backend = backend_root or Path(__file__).resolve().parent
        probe = backend / "ship_material_probe.exe"
        source = _source_identity(game, build)
        root = cache_root or Path(os.environ.get("LOCALAPPDATA", str(game.parent))) / "WoWSToolbox" / "Cache"
        directory = root / "Korabli" / str(build)
        directory.mkdir(parents=True, exist_ok=True)
        identity = dict(source, version=SHIP_MATERIAL_CATALOG_VERSION, models=models,
                        probe=hashlib.sha256(probe.read_bytes()).hexdigest())
        cache = directory / "ship_materials.json"
        results = None
        if cache.is_file() and not refresh:
            try:
                saved = json.loads(cache.read_text(encoding="utf-8"))
                if saved.get("identity") == identity:
                    _validate_results(saved["models"], models)
                    results = saved["models"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if results is None:
            # Decode from this installation on the first scan of a new identity.
            # Only the small classification JSON persists; avoid a second 200+ MB
            # assets cache and stale same-build data from another installation.
            with tempfile.TemporaryDirectory(dir=directory, prefix="material-probe-") as request_dir:
                request = Path(request_dir) / "models.json"
                request.write_text(json.dumps(models), encoding="utf-8")
                assets = Path(request_dir) / "assets.bin"
                assets.write_bytes(extract_entry(game, find_entry(entries, ("assets.bin",))))
                process = subprocess.run([str(probe), str(assets), str(request)],
                                         capture_output=True, text=True, encoding="utf-8",
                                         check=True, timeout=120)
            payload = json.loads(process.stdout)
            if payload.get("schema") != "ship-material-probe/v1":
                raise ValueError("Unsupported material probe response")
            results = payload["models"]
            _validate_results(results, models)
            if any(value["status"] != "unknown" for value in results.values()):
                _atomic_json(cache, {"identity": identity, "models": results})
        _validate_results(results, models)
        for row in rows:
            result = results.get(row.get("ModelPath", ""))
            if result is None:
                row["ShipMaterialReason"] = "Hull model unavailable"
                continue
            row.update(ShipMaterialStatus=result["status"],
                       ShipMaterialEvidence=result.get("indexed_materials", []),
                       ShipMaterialReason="; ".join(result.get("errors", [])) if result["status"] == "unknown" else "",
                       ShipMaterialCheckedCount=result.get("materials_checked", 0))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"[WARN] Ship 2.0 material scan unavailable: {error}", file=sys.stderr)


def _validate_results(results: Any, models: list[str]) -> None:
    if not isinstance(results, dict) or set(results) != set(models):
        raise ValueError("Incomplete material probe response")
    for result in results.values():
        if not isinstance(result, dict) or result.get("status") not in {"indexed", "legacy", "unknown"}:
            raise ValueError("Invalid material probe status")
        evidence = result.get("indexed_materials", [])
        if not isinstance(evidence, list) or not all(isinstance(p, str) for p in evidence):
            raise ValueError("Invalid material evidence")
        if result["status"] == "indexed" and not evidence:
            raise ValueError("Indexed material status needs evidence")
        count = result.get("materials_checked", 0)
        errors = result.get("errors", [])
        if not isinstance(count, int) or count < 0 or not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
            raise ValueError("Invalid material diagnostics")
        if result["status"] == "legacy" and (count == 0 or errors):
            raise ValueError("Traditional status needs a complete material inspection")
