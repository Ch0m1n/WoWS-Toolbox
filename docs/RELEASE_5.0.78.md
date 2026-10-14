# WoWS Toolbox 5.0.78

- Fix PC catalogs loading as one ship and repeatedly refreshing on Windows PowerShell 5.1. Catalog arrays now load consistently on 5.1 and 7; automatic schema refresh retries are bounded.
- Start normal extraction directly after the existing plan checks. Confirm only actual replacement of existing output. Open the optional plan table from Workflow tools → Preview extraction plan.
- Show PC, Korabli and Legends nation/class labels in the selected UI language, and rebuild older category caches once.
- Fix picker callbacks losing private helpers or queue-count variables after their defining scope returns.
- Improve Blitz import progress with total bundle counts and detect imported model resources missing from older design metadata.
- Include the previously prepared workflow improvements: quality presets, catalog snapshots, diagnostics, output validation/provenance, interrupted batch review, and viewer session metadata.

Verification: 134 Python tests; catalog and extraction-start regressions on PowerShell 5.1/7; picker/deferred callback and workbench tests; packaged WPF GUI/queue checks; Node viewer workflow tests. Automated checks do not establish visual parity for every ship/game.

Install over the existing version after closing WoWS Toolbox. Existing settings and exported models are retained.
