# WoWS Toolbox 5.0.79

- Fix Eenhoorn permanent camouflage extraction. Legacy hull materials such as HSB305_Eenhoorn_1950 have no _Hull suffix; the exporter now recognizes these hull stems while preserving the original materials on unrelated parts.
- Load the highest intact streamed camouflage mask (.dd0, .dd1, .dd2) before the low-resolution .dds tail. Previously the output PNG could retain full dimensions while its camouflage pattern was baked from a smaller mask.
- Retain validated fallback when a higher mip is damaged or unavailable.
- The exporter fingerprint changes automatically, so re-extraction uses fresh model caches. Previously exported files are not modified by installation.
- Includes the catalog compatibility, bounded refresh, direct extraction, picker and language fixes released in 5.0.78.

Verification: 39 Rust export tests passed, including bare hull recognition, rejecting unrelated equipment, highest-mip selection and damaged-mip fallback. Actual PC public-test build 13438520 Eenhoorn and Grote Geus camouflage exports and OBJ conversion passed (43 and 35 editable parts respectively). Grote Geus DeckHouse provides a 2048 mask above its 512 DDS tail; Eenhoorn provides a 4096 mask. Automated checks cover extraction and file structure, not full visual parity for every ship.

Close WoWS Toolbox before installing. Existing settings and exported models are retained. Re-extract camouflage models to use the corrected exporter.
