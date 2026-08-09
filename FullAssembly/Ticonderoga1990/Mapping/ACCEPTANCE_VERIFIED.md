# Ticonderoga 1990 verified static assembly acceptance

Result: **PASS**

Profile: `ticonderoga_1990_verified_profile`

Canonical runner: `build_ticonderoga_assembly_v2.py`

Canonical output:
`ticonderoga_1990_static_assembly.json`

Output SHA-256:
`690F0EC02AB455D7DB7094A5E7D7052F0BC1F6110A87B5CEB6BB106B2C276337`

## Checks

| Check | Result |
|---|---:|
| GameParams combat hardpoints expected/resolved | **17 / 17** |
| Missing combat hardpoints | **0** |
| Duplicate hardpoint sources | **0** |
| Hull ModelUber prototypes | **10** |
| Detailed ModelUber models | **26** |
| Authored `MP_*` instances | **10** |
| `MP_*` always/dock split | **8 / 2** |
| Runtime VLS action overlays | **2** |
| Parsed render sets | **194** |
| Resolved texture properties | **155 / 155** |
| Unresolved render-set fields | **0** |
| Non-finite output matrices | **0** |

The full 17-hardpoint placement table and ten authored misc placements are in
`ACCEPTANCE.md`; the machine-readable source of truth is the canonical JSON.

## Visibility acceptance

Two `AM5058.model` instances are attached to `HP_AGM_1` and `HP_AGM_2`.
GameParams classifies them as `ShooterLaunchAction` front models. Their exact
matrices are preserved in `runtime_action_overlays`, but they are not forced
visible in an intact static scene. A replay-aware consumer may enable them from
the corresponding VLS launch/hatch state.

The two `AM5042` Mk141 cap instances occur only in
`ASC307_Ticonderoga_1990_MidBack_ports_dock.model` and carry
`visibility_condition: "dock"`:

- neutral battle-intact scene: hidden;
- harbor/dock scene: visible;
- replay/runtime scene: determined by scene state.

The remaining eight authored misc instances carry
`visibility_condition: "always"`.

This condition handling is part of acceptance. “Complete assembly” means every
authored part and transform is preserved; it does not mean mutually exclusive
runtime/dock parts are all visible simultaneously.

## Reproducibility acceptance

Use `build_ticonderoga_assembly_v2.py`, not the shared core directly. The v2
entry point corrects the Legends v0 MaterialPrototype property layout
(`u16` property count at `+0x0c`, type 0 bool, type 1 int) before invoking the
read-only core.

The canonical output and the independently reproduced output have identical
SHA-256 hashes. Source records store stable `content/<filename>` logical names
plus size and SHA-256; machine-local input paths are not embedded.

The path-variance regression generated the mapping twice from the same four
read-only source contents through two different input directories. Both files
were byte-identical with SHA-256
`690F0EC02AB455D7DB7094A5E7D7052F0BC1F6110A87B5CEB6BB106B2C276337`.

## Scope boundary

Accepted:

- intact static hull hierarchy;
- all 17 combat mount placements and correction matrices;
- all authored misc placements with visibility predicates;
- runtime overlay placements retained but disabled by default;
- ModelUber LOD/VisualNodes/render-set/material/texture data.

Not accepted as implemented runtime behavior:

- replay event decoding;
- dynamic aiming or radar animation;
- launch/hatch timing;
- damage/dead swaps;
- particles, wakes, or destruction effects.

The binary readers are reusable for the tested Legends build, but this
composition profile is Ticonderoga-specific. It is not yet a generic
`--ship-key` / `--ship-model-dir` exporter.
