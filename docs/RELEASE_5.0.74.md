# WoWS Toolbox 5.0.74

- Preserves original material detail when a tiled camouflage uses a shared Tile UV setting or implicit identity transform; previously these parts received a flat tile replacement. Also parses direct-text UV scales.
- Gives concurrent PBR PNG conversions unique temporary files, validates an existing winner, and retries brief Windows file locks rather than dropping supplemental texture channels.

- Supports Korabli's named color-palette XML elements as well as PC's `colorScheme/name` format, fixing La Rochelle permanent-camouflage exports with the Diane and Duperre palettes.

- Detects BC7Prep texture payloads before PNG conversion. Unsupported layouts use the authored DDS fallback and record the resolution in the PBR warnings, preventing noisy normal and gloss maps.
- Applies each mounted gun/director's GameParams misc selection and battle preset. This removes inactive ammunition boxes, event barrel accessories and duplicate radar dishes; hull fittings remain available.
- Places mounted accessories using the authored hardpoint frame rather than the mesh-only bind correction.
- Reads unified hull splash data and corrects upper `InclinCit` faces only when the authored citadel/casemate hit boxes establish their location. Armor thickness, layers and geometry are preserved.

- Automatically retires older numeric PC and Korabli game-data cache folders after the selected build's cache is successfully created or validated. Newer builds, unrelated cache folders and redirected paths are preserved; failed cache preparation does not trigger cleanup.

- Adds a Settings button to import Blitz bundles, OBB and optional ship-name data through ADB into a fresh snapshot. Successful imports save the new path and refresh the catalog; cancelled or failed transfers preserve the previous selection.

- Smooths low-resolution permanent-camouflage masks with the same linear repeat sampling used by the game renderer, removing enlarged pixel stair-steps on high-resolution hull textures such as Bismarck '41.
- Keeps ship-authored camouflage scope intact: hull-only masks are no longer incorrectly wrapped around unrelated gun, deckhouse, director, or equipment UV layouts.
- Retains full-resolution base texture detail while baking camouflage into portable OBJ/GLB materials.
