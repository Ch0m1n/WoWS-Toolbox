# Research modules

PC and Korabli queue entries can select **Hull**, **Main battery**, and
**Torpedoes** independently. Each defaults to **Fully upgraded**, resolved from
the installed client's research dependencies. Engine and fire-control choices
are not exposed. Legends and Blitz retain their existing fixed configurations.

## Usage

1. Refresh the ship catalog once to load module metadata.
2. Add ships and select a row in the extraction queue.
3. Click **Select modules** below the output quality controls.
4. Select A/B module cards in the Hull, Main battery and Torpedoes columns.
   Checkmarks show the active configuration; **Fully upgrade all** restores defaults.
   Changes apply immediately to the queued ship.

Hull changes update the available weapon upgrades. A compatible research choice
is retained; an incompatible choice resets to Fully upgraded for the new hull.
AA, secondary guns, directors, and other mounted equipment follow the selected
hull's component configuration. Ships without alternative modules show a single selected card. Missing
torpedo armament is shown as No armament.

The default choice is dynamic: an old queue without module fields uses fully
upgraded modules. Saved explicit choices remain explicit. A stale/foreign module
ID fails extraction rather than silently exporting a different module.

## Mapping and provenance

Selections store research upgrade IDs, not raw turret names. The backend
intersects the selected hull's allowed components with the chosen weapon
upgrade's components. For Charles Martel (`PFSC108`), researched guns resolve to
`A2_Artillery` on hull A and `B2_Artillery` on hull B.

The source engine accepts resolved `--artillery-component` and
`--torpedo-component` overrides and validates hull compatibility. Visible models
and exact armor use that same engine and selection. Hull model directories also
follow the chosen hull when they differ.

All three choices participate in output directory suffixes, GLB/armor cache keys,
request identities, queue files, and portable manifests. The selected concrete
components appear under `modules` in `export.json` and `applied.modules` in the
portable manifest. Old caches are not reused for the new fully upgraded default.

Compact module metadata is cached beside compatible GameParams, invalidated by
schema version and GameParams file size/time. The first extraction may build
this metadata; subsequent extractions reuse it.

## Verification

- `Backend/test_ship_modules.py`: research ordering, mixed hull/gun combinations,
  incompatible torpedoes, missing armament, ambiguous mapping, and identity separation.
- `examples/Test-ShipModules.ps1`: actual WPF selectors, default state, compatible
  selection retention, incompatible selection reset, busy state, fixed sources,
  and queue JSON roundtrip.

Available in WoWS Toolbox 5.0.84.
