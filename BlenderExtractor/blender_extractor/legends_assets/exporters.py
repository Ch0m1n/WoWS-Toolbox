"""Opt-in external exporter wrappers.

These wrappers are deliberately separate from the native extractor.  The public
PC WoWS converters are not assumed to support Legends' legacy section-table
geometry.  A process exit is never treated as success unless a valid GLB exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .core import ExtractionError, ensure_within_root, validate_glb


def run_ship_exporter(
    extracted_root: Path | str,
    ship_name: str,
    output_path: Path | str,
    exporter_path: Path | str,
    *,
    execute: bool = False,
    hull_upgrade: str | None = None,
    lod: int | None = None,
    with_turrets: bool = True,
    with_textures: bool = True,
    include_damage: bool = False,
    verbose: bool = True,
) -> dict[str, object]:
    """Opt in to the public wows-gltf-exporter and validate the resulting GLB."""

    root = Path(extracted_root).resolve()
    target = ensure_within_root(Path(output_path), root)
    exporter = Path(exporter_path).resolve()
    allowed_names = {"wows-gltf-exporter", "wows-gltf-exporter.exe"}
    if exporter.name.casefold() not in allowed_names:
        raise ValueError(
            "exporter must be named wows-gltf-exporter or wows-gltf-exporter.exe"
        )
    if not ship_name.strip():
        raise ValueError("ship name must not be empty")
    if target.suffix.casefold() != ".glb":
        raise ValueError("ship exporter output must use .glb")

    game_params = ensure_within_root(root / "content" / "GameParams.data", root)
    assets_bin = ensure_within_root(root / "content" / "assets.bin", root)
    command = [
        str(exporter),
        "-W",
        str(root),
        "-s",
        ship_name,
        "-o",
        str(target),
        "-g",
        str(game_params),
        "-a",
        str(assets_bin),
    ]
    if hull_upgrade:
        command.extend(["-H", hull_upgrade])
    if lod is not None:
        if lod < -1 or lod > 4:
            raise ValueError("LOD must be from -1 through 4")
        command.extend(["-L", str(lod)])
    if not with_turrets:
        command.append("-t")
    if not with_textures:
        command.append("-T")
    if include_damage:
        command.append("-D")
    if verbose:
        command.append("-v")

    result: dict[str, object] = {
        "status": "dry-run",
        "command": command,
        "extracted_root": str(root),
        "ship": ship_name,
        "output": str(target),
        "compatibility": (
            "EXPERIMENTAL: wows-gltf-exporter 0.2.1 is a PC WoWS tool. "
            "Legends legacy section-table .geometry has been observed to crash "
            "wows-geometry-cli 0.2.1, so this is never the default pipeline."
        ),
    }
    if not execute:
        return result
    if not exporter.is_file():
        raise FileNotFoundError(f"exporter not found: {exporter}")
    if not game_params.is_file():
        raise FileNotFoundError(f"extracted GameParams.data not found: {game_params}")
    if not assets_bin.is_file():
        raise FileNotFoundError(f"extracted assets.bin not found: {assets_bin}")

    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    result.update(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    if completed.returncode != 0:
        result["status"] = "exporter-failed"
        return result
    if not target.is_file():
        result["status"] = "exporter-produced-no-file"
        return result
    try:
        validate_glb(target)
    except ExtractionError as exc:
        result["status"] = "invalid-exporter-output"
        result["validation_error"] = str(exc)
        return result
    result["status"] = "exported-and-validated"
    result["output_bytes"] = target.stat().st_size
    return result
