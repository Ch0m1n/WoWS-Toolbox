# Korabli 2.0 material structure

The ship picker displays **2.0**, **Traditional**, or **Unverified** for Korabli.
Its material filter combines with name, nation, class, tier, favorites, and recent
filters. Checked ships remain queued when filters change.

This is a data-derived material classification, not a claim that every matching
ship has been officially marketed as “Ships 2.0”. It does not classify by ship
code, `_2` filename suffix, resource number, split hull, or empty root LODs.

`Backend/ship_material_probe.exe` reads decoded assets.bin. It follows the
selected ModelPrototype to its VisualPrototype. For node-only split hull roots,
it examines drawable sibling visuals in that exact model directory, excluding
shadow records and `/lods/`, as the existing exporter does. Render-set MFM IDs
are followed to their actual material inputs. A positive result requires
nonempty `materialIdMap`, `albedoArray`, `MGArray`, and `artMap` bindings on at
least one referenced material. This detects shared materials on derived hulls.

Traditional means referenced materials were inspected successfully without
finding that combination. Missing models, unknown layouts, or incomplete reads
yield Unverified instead of a negative result. A positive observed binding can
still establish 2.0 structure if other materials could not be inspected.

Python adds `ShipMaterialCatalogVersion`, `ShipMaterialStatus`, material path
evidence, and a diagnostic reason to Korabli catalog rows. Other games retain
their catalog behavior. The Korabli catalog filename version advances to v2;
in-memory rows are also refreshed when the installed build or schema changes.

Small results are cached under `Cache/Korabli/<build>/ship_materials.json`.
Cache identity includes installation path, build, IDX names/sizes/timestamps,
requested model paths, schema, and probe binary SHA-256. The decoded assets used
for inspection are temporary; an additional persistent assets.bin is not kept.
The GUI's catalog refresh uses `--refresh-materials` to retry even cached unknown
entries. Scan failures leave extraction selection available and show Unverified.

## Rebuilding the helper

The helper links the same modified MIT-licensed `landaire/wowsunpack` library as
the exporter; it does not replace the two exporter executables. Source is shipped
as `Backend/ship_material_probe.rs`. Put that file in `examples/ship_material_probe.rs`
in the modified wowsunpack source checkout and run:

```powershell
cargo build --offline --release --example ship_material_probe
```

Copy `target/release/examples/ship_material_probe.exe` to `Backend/`.
The library must include the current model/visual split-layout parsers.

## Verification

- `Backend/test_ship_materials.py`: cache reuse/invalidation, missing probe,
  unknown status, evidence requirements, temporary assets cleanup.
- `examples/Test-ShipMaterialPicker.ps1`: loads the real picker XAML and filter
  script under both languages, verifies all four filters, label search, queue
  preservation and other-source isolation. Run with `pwsh -STA -NoProfile`.
- Korabli PT build 8868916: 1,179 rows, 9 indexed / 1,159 traditional / 11 unknown.
  The nine indexed rows include the eight main model families and PRSB999's
  shared-material Krasnoe Znamya wheelhouse variant. These are results for that
  build, not a hardcoded list or a guarantee about future shader formats.
