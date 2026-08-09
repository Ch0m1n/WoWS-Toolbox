# Selected-ship downstream assembly

These tools consume an accepted selected-ship assembly mapping and an extracted
resource tree. They do not scan or modify the game installation.

## 1. Convert every used model to PBR GLB

```powershell
python FullAssembly\SelectedShip\PBRConverter\batch_selected_ship_models.py `
  --mapping <selected_ship.assembly.json> `
  --extracted-root <selected_ship_extracted_root> `
  --output-root <selected_ship_pbr_output>
```

Optional switches:

- `--blender <blender.exe>`
- `--decoder-root <geometry_decoder_directory>`
- `--converter <convert_selected_ship.py>`
- `--manifests-only` validates mapping-owned model/material selection without
  starting Blender.
- `--reuse-existing` revalidates existing GLB and validation outputs only when
  the mapping, geometry, and selected texture fingerprints still match.

The batch discovers unique render-bearing models dynamically from
`hull_parts(role=mesh)`, `combat_mounts`, `misc_instances`, and
`runtime_action_overlays`. There are no ship-specific part or weapon counts.
The mapping's `include_in_intact` and `damage_semantic` fields are authoritative:
for example, an intact render set containing `patch` in its name is retained.

Both downstream stages reject mappings whose static-assembly acceptance did not
pass.
Default reports:

- `<output-root>\selected_ship_pbr_models.summary.json`
- `<output-root>\selected_ship_required_textures.json`

## 2. Build a dynamic Blender scene plan

```powershell
python FullAssembly\SelectedShip\BlenderSceneAssembler\build_selected_ship_scene_plan.py `
  --assembly <selected_ship.assembly.json> `
  --batch-summary <selected_ship_pbr_models.summary.json> `
  --output <selected_ship.scene-plan.json> `
  --visibility-profile harbor_dock
```

Built-in visibility profiles:

- `harbor_dock`: always and dock objects visible; runtime overlays hidden.
- `neutral_battle_intact`: only always-visible objects shown.
- `overlay_debug`: dock objects and runtime overlays both shown.

The mapping may add profiles in `visibility_profiles`. Each profile supplies
boolean `dock` and `overlay` values. The generated plan accepts optional
`--output-blend`, `--output-glb`, `--output-combined-obj`, and
`--validation-json` overrides; absolute paths are supported.

## 3. Assemble in Blender

The plan is compatible with the verified reusable assembler:

```powershell
<blender.exe> --background --factory-startup `
  --python FullAssembly\Ticonderoga1990\BlenderSceneAssembler\assemble_scene.py `
  -- --plan <selected_ship.scene-plan.json> --obj-only --editable-objects `
  --obj <ship_Editable.obj> --validation <ship.validation.json>
```

The resulting OBJ contains separate mesh objects for hull pieces and realized
mount occurrences. It uses portable MTL/PNG references and content-hash texture
deduplication. No final BLEND or GLB is written in this mode. Source game assets
are never overwritten.

## Tests

```powershell
python -m unittest discover `
  -s FullAssembly\SelectedShip\PBRConverter -p "test_*.py" -v

python -m unittest discover `
  -s FullAssembly\SelectedShip\BlenderSceneAssembler -p "test_*.py" -v
```
