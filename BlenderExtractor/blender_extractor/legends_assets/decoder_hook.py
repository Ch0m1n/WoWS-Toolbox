"""Stable hook for a custom Legends legacy-geometry decoder.

The native package extractor does not pretend that raw ``.geometry`` is a mesh.
An optional decoder module can be integrated without changing package parsing by
exposing:

    decode_geometry(input_path: pathlib.Path, output_dir: pathlib.Path)
        -> Iterable[pathlib.Path]

Every returned file must stay below ``output_dir`` and use OBJ, glTF, or GLB.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Iterable, Protocol

from .core import ExtractionError, ensure_within_root, validate_glb


class GeometryDecoder(Protocol):
    def decode_geometry(
        self,
        input_path: Path,
        output_dir: Path,
    ) -> Iterable[Path]: ...


def run_decoder_hook(
    input_path: Path | str,
    output_dir: Path | str,
    *,
    module_name: str = "legends_assets.geometry_decoder",
    execute: bool = False,
) -> dict[str, object]:
    source = Path(input_path).resolve()
    root = Path(output_dir).resolve()
    if source.suffix.casefold() != ".geometry":
        raise ValueError("decoder hook accepts only .geometry input")
    result: dict[str, object] = {
        "status": "dry-run",
        "module": module_name,
        "input": str(source),
        "output_dir": str(root),
        "contract": "decode_geometry(Path, Path) -> Iterable[Path]",
    }
    if not execute:
        return result
    if not source.is_file():
        raise FileNotFoundError(f"geometry input not found: {source}")
    module = importlib.import_module(module_name)
    decoder = getattr(module, "decode_geometry", None)
    if not callable(decoder):
        raise TypeError(f"{module_name} does not expose callable decode_geometry")
    root.mkdir(parents=True, exist_ok=True)
    generated = [Path(path) for path in decoder(source, root)]
    if not generated:
        result["status"] = "decoder-produced-no-files"
        return result

    verified: list[str] = []
    for path in generated:
        resolved = ensure_within_root(path, root)
        if resolved.suffix.casefold() not in {".obj", ".gltf", ".glb"}:
            raise ExtractionError(
                f"decoder returned unsupported interchange format: {resolved}"
            )
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise ExtractionError(f"decoder output missing or empty: {resolved}")
        if resolved.suffix.casefold() == ".glb":
            validate_glb(resolved)
        verified.append(str(resolved))
    result["status"] = "decoded-and-validated"
    result["outputs"] = verified
    return result
