# World of Warships: Legends legacy geometry decoder

This is a minimal, read-only-input decoder for the sectioned BigWorld
`.geometry` files found in the Steam build of *World of Warships: Legends*.
It exports positions, packed normals, UV0, triangle indices, and primitive-group
names to Wavefront OBJ. The Blender validation script can import the OBJ, export
a GLB, and save a `.blend` project.

For every decoded mesh, sampled triangle winding is compared with averaged vertex
normals. A globally inverted skinned normal set is flipped before OBJ output, while
already aligned hull and static-part normals are preserved.

## Verified sample

Input:

```text
..\ASB007_Texas_1944.geometry
```

The sample was extracted from:

```text
D:\SteamLibrary\steamapps\common\World of Warships Legends\
  res_packages\T4_PASB705_Texas_1944.idx
```

Logical resource path:

```text
content/gameplay/usa/ship/battleship/
  ASB007_Texas_1944/ASB007_Texas_1944.geometry
```

The decoder expects already-extracted `.geometry` files. It never writes to the
game installation.

## Decode one file

```powershell
python .\decode_geometry.py `
  ..\ASB007_Texas_1944.geometry `
  .\validation_output\ASB007_Texas_1944.obj
```

`--intact-lod 0` is enabled by default. It retains joint `patch` meshes and
exterior `crack` surfaces ending in `DeckHouseShape` or `HullShape`, while
dropping bare damage `crack` and `dead` parts. Both `_lod1` and `_lodShape1`
naming orders are recognized. Untagged/LOD0 parts stay selected; when LOD0 is
absent, selection falls back to the smallest available LOD number. Use
`--all-parts` to disable this selection.

## Merge component files

Place all input paths before the final OBJ output path:

```powershell
python .\decode_geometry.py `
  .\Ship_Bow.geometry `
  .\Ship_MidFront.geometry `
  .\Ship_MidBack.geometry `
  .\Ship_Stern.geometry `
  .\Ship_merged.obj `
  --intact-lod 0 `
  --report .\Ship_merged.decode.json
```

The same operation is available to Python callers through
`decode_geometry_files(input_paths, intact_lod=0)`, followed by
`write_obj(parts, output)`. For intact LOD0-3 ship exports, pass only
Bow/MidFront/MidBack/Stern. The exact base `.geometry` is a complete LOD4 hull
and would overlap those segmented components. For LOD4 or later, pass only the
base `.geometry`.

## Run the Blender 3.5 validation

```powershell
pwsh -NoLogo -NoProfile -File .\validate_texas.ps1
```

The validation wrapper produces:

- an OBJ;
- a decoder JSON report;
- a Blender JSON report;
- a GLB exported by Blender 3.5;
- a `.blend` saved by Blender 3.5.

Direct Blender script usage:

```text
blender.exe --background --factory-startup --python blender_validate.py -- \
  input.obj report.json [output.glb] [output.blend] \
  [base_diffuse.dds] [deckhouse_diffuse.dds]
```

The importer explicitly uses `axis_forward=-Y` and `axis_up=Z`, because the OBJ
is already Z-up. When both DDS paths are supplied, objects containing
`DeckHouse` use the DeckHouse diffuse and all other hull objects use the base
diffuse. Both images are packed into the `.blend`.

## End-to-end IDX/PKG ship export

The wrapper imports the verified package parser from
`../blender_extractor/legends_assets/core.py`. It searches one ship IDX by
filename term, selects exactly five hull geometries and two diffuse DDS files,
validates the observed `(compression, storage) == (5, 1)` container, extracts
outside the game directory with decoded-size and CRC32 checks, selects the
correct geometry inputs for the requested LOD, then builds OBJ/GLB/BLEND.
It is a dry run unless `-Execute` is supplied.

```powershell
# Inspect the exact seven-file plan; writes nothing.
pwsh -NoLogo -NoProfile -File .\Extract-LegendsShip.ps1 `
  -ShipIndex Ticonderoga `
  -OutputRoot .\ship_exports\Ticonderoga

# Perform CRC-checked extraction and Blender 3.5 background export.
pwsh -NoLogo -NoProfile -File .\Extract-LegendsShip.ps1 `
  -ShipIndex Ticonderoga `
  -OutputRoot .\ship_exports\Ticonderoga `
  -Execute
```

LOD input policy is fixed in the wrapper:

- LOD0-3: Bow, MidFront, MidBack, and Stern only;
- LOD4 or later: complete base `.geometry` only.

The game directory is read-only. Outputs include extraction/decode/Blender JSON
reports, hashes, Blender log, OBJ, GLB, and packed BLEND.

## Supported data

- trailing BigWorld section tables;
- paired `*.vertices` / `*.indices` sections;
- `list` (16-bit) and `list32` (32-bit) indices;
- these vertex layouts:
  - `set3/xyznuvtbpc`
  - `set3/xyznuvpc`
  - `set3/xyznuvrpc`
  - `set3/xyznuviiiwwtbpc`
  - `xyznuvtb`
  - `xyznuv`
  - `xyznuviiiwwtb`

For `set3/*`, UV0 is decoded as two half floats with the observed `+0.5` bias;
V is flipped for OBJ/Blender. Only position, normal, and first UV coordinates
are decoded from geometry. Tangents, skin weights, armor/collision sections,
animation, and complete ship-component placement are not implemented.

The end-to-end wrapper uses the sibling verified Legends IDX/PKG parser for the
observed `ISFP 0x01010005` / StorageType=1 chunk container. It does not parse
`GameParams.data` or `assets.bin`, so guns, turrets, radar, misc mounts, full
scene hierarchy, and their transforms remain out of scope. Material support is
limited to the base and DeckHouse diffuse maps with a name-based heuristic;
AO, metal/gloss, normal, glass, and wire materials are not reconstructed.

See `THIRD_PARTY_NOTICES.md` for format-research references and license notes.
