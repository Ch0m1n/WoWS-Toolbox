"""Read-only World of Warships: Legends asset extraction helpers."""

from .core import (
    AssetEntry,
    ExtractionError,
    FormatSupport,
    IdxFormatError,
    UnsafePathError,
    classify_asset,
    extract_asset,
    iter_assets,
    parse_legends_idx,
)

__all__ = [
    "AssetEntry",
    "ExtractionError",
    "FormatSupport",
    "IdxFormatError",
    "UnsafePathError",
    "classify_asset",
    "extract_asset",
    "iter_assets",
    "parse_legends_idx",
]

__version__ = "0.1.0"
