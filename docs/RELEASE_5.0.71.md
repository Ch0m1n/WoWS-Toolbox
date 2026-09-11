# 5.0.71 — Korabli Ships 2.0 texture update

- Connects the independently implemented BC7Prep decoder to normal texture
  loading, preserving the authored top-mip size instead of always falling back
  to the standalone low-resolution mip.
- Uses high-resolution art in indexed ship-material colour baking, including
  tested hull, superstructure, gun, director and boat materials.
- Preserves RGBA and the DDS linear/sRGB format metadata.
- Rejects malformed containers, unvalidated flags/padding and mode 8; these
  inputs retain the existing lower-mip fallback rather than aborting the ship.
- Keeps extraction cache isolation keyed by the exporter executable hash.
  Re-extract ships to get the new output; existing OBJ/MTL files are unchanged.

This is not a claim of identical in-game lighting or complete support for every
future Ships 2.0 asset. Deferred normal-only decals and renderer-specific effects
retain their existing compatibility limitations. No proprietary texture SDK or
Unreal Engine runtime is bundled.

## Known armor-viewer limitation

Ships 2.0 hull armor is not yet supported by this release. Songun and Krasnoe
Znamya currently export turret armor only; the viewer's armor-available label
does not mean that every hull armor zone is present. Model/texture extraction
remains usable. Hull armor support is being developed separately.

## Installer

`WoWS-Toolbox-Setup-5.0.71.exe` (19,711,280 bytes)

SHA-256: `9A45C9D670DB47FC5B5CDAF35569D17634608A7DADF915F86BE530B91B4FF8CE`
