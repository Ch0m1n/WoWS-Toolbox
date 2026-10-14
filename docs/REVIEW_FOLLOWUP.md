# Follow-up to the 5.0.76 review

## Implemented scope

- Optional Korabli material decoding catches `RuntimeError` locally. A successful
  GameParams catalog remains selectable with unknown material status.
- Catalog diagnostics schema 2 records a stable failure code, inspected material
  and visual counts, evidence paths, and partial inspection errors. The picker
  shows a localized reason; local tooltips include the model and evidence paths.
- The picker shows hidden checked selections and offers a checked-only filter.
  Clearing visible selections retains checked rows outside the current view.
- Single and batch extraction for all four sources create
  `extraction.manifest.json`. It records source/build when available, stable ship
  identifiers, requested/applied options, app version, applicable exporter hash,
  relative artifact paths, sizes, SHA-256 hashes, and validation results.
- Required OBJ/MTL/texture failures fail the queue item and retain its output
  directory and diagnostics. Optional armor extraction failures are warnings.
  Unknown armor availability is null, never zero thickness.
- Material picker tests are now part of the standard self-test entry point.

## Offline verification

Run the bundled Python with `Backend/output_provenance.py` and the absolute path
of an extraction manifest. It prints JSON and exits 1 on validation failure.
It does not need a game installation or invoke an exporter. It does not modify
the model or the saved manifest. Referenced files outside the output directory,
symlinks and Windows reparse points are rejected before reading.

The manifest contains an allowlist of metadata, not installation paths or logs.
Application version is read from launcher source or the shipped GUI version.
The manifest does not contain itself in its artifact hashes. Build information
that is unavailable from an adapter remains null. Existing outputs are not
automatically migrated. MTL texture options not emitted by our exporters are
reported as unverified instead of guessed.

## Verification limits and later work

Validation checks file integrity and model references, not shader appearance,
camouflage authenticity, radar orientation, mesh topology or game licensing.
The published installer is unchanged by these source changes.

## Remaining proposals implemented as local MVPs

- Per-item execution plan before export: source/build, requested and applied
  quality, camouflage, output action/path, cache uncertainty, blockers, estimates
  and prior failure setting changes. Preview invokes no exporter and creates no
  output. Execution checks the approved plan again.
- Failure guidance gives stable next actions. Retries replan; no automatic retry
  loop or reuse of partial model files.
- Korabli picker: selected/unknown material reinspection merges only the same
  installation, IDX, model-set and helper identity. Search presets store raw
  country/class values. Exact code, tier range and plain variant include/exclude
  filters are available from the search tools menu. Checked rows are preserved.
- Workbench: named quality presets, catalog snapshots, snapshot differences and
  pinning. Snapshots keep ten recent files plus pins; only small JSON metadata is
  retained, never an old assets.bin. Ambiguous keys are reported and excluded from
  inferred changes. Unknown material changes are unverified, not official history.
- Batch journal records queued/running/completed/failed/interrupted states and
  rejects simultaneous ownership. Workbench recovery reviews existing manifests,
  archives the reviewed journal and requires a fresh plan before another batch.
- GUI batch replacement exports into a staging directory, validates first, then
  renames the previous output into .backups. Publish failure restores that backup.
  User edits detected since planning prevent replacement. Failed staging files
  remain available for diagnostics. Single legacy CLI overwrite semantics are
  unchanged; use batch planning for the protected replacement workflow.
- Sharing diagnostics uses a selectable allowlist preview: component status,
  Python version and optional batch counts. No free-form logs, usernames, local
  paths, queue names, game assets or secrets are collected into this package.
- Estimate display explains default versus recent median, sample count and
  observed time range when at least five samples exist. Cache reuse and temporary
  space are explicitly not accounted for; these are estimates, not guarantees.
- Viewer shows both model conditions, part/material/triangle counts and bounds
  in viewer world coordinates. This is structural comparison, not pixel or mesh
  equivalence. Portable sessions save camera, hidden parts, inspector width and
  plain-text notes; exact OBJ SHA identity and available part keys are required.

## Entry points

Use **Workbench** in the queue toolbar for presets, snapshots, diagnostics,
recovery and offline verification. Use **search tools** in the ship picker for
search presets, advanced filters and selective material reinspection. Starting
queue extraction opens the plan with new / verified skip / backup and replace.
Viewer notes and session controls are at the bottom left of the model viewport.

## Material probe follow-up

The helper now rejects unknown material checksums instead of assuming legacy
stride. The complete modified Rust library, Cargo lockfile and helper example are
included under Backend/probe-source with build instructions. GUI in-memory
identity also checks installation token, IDX stamps and helper hash.
Directory-sibling fallback is reported as inspection scope; it is not silently
presented as selected-visual certainty. ID-zero semantics still lack a verified
fixture and are not claimed as resolved.

Actual cached Korabli build 8868916 assets were inspected with the rebuilt
helper: 898 unique models, 9 indexed / 881 legacy / 8 unknown. These are observed
results for that input, not a code-prefix rule for future releases.

## Verification and delivery

75 affected Python tests passed. PowerShell 5.1/7 picker / GUI and workbench tests, queue tests and a Node viewer
session harness are used. These changes are included in the 5.0.78 release package. See RELEASE_5.0.78.md for the current delivery and verification summary. Visual game parity and a full
interactive end-to-end export for every source remain outside the automated
metadata/workflow checks.
