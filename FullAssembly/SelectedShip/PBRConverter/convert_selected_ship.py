#!/usr/bin/env python3
"""Run the reusable PBR converter with mapping-owned damage semantics.

The verified Ticon converter normally rejects render-set names containing
``patch``/``crack``/``dead``. A selected-ship mapping has already resolved
those names into explicit ``include_in_intact`` and ``damage_semantic`` fields,
so this adapter disables only that second filename heuristic.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Sequence


def _load_core():
    path = (
        Path(__file__).resolve().parents[2]
        / "Ticonderoga1990"
        / "PBRConverter"
        / "convert.py"
    )
    spec = importlib.util.spec_from_file_location(
        "selected_ship_pbr_converter_core", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import reusable converter: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    core = _load_core()
    core.DAMAGE_RE = re.compile(r"(?!x)x")
    return int(core.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
