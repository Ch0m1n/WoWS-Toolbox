# Legends ModelUber v0 render parser

This folder is an independent, read-only parser and verification fixture for
the Steam build of World of Warships: Legends inspected on 2026-08-03.

## What it does

1. Resolves resource paths and string IDs through `assets.bin` v0.
2. Finds a model's external blob through `prototypes.index.data`.
3. Parses ModelUber variants, model IDs, VisualNodes, geometry descriptors,
   render sets, palettes/correction nodes, and material prototypes.
4. Computes every VisualNode world matrix as `parent_world * local`.
5. Checks LOD0 vertex/index names against the extracted `.geometry` section
   table and checks each MFM/material pair against the model material table.
6. Emits the Ticonderoga root, four mesh components, four ports components,
   and conditional dock-ports component in one exact manifest.

The parser never writes to the game installation. Its inputs are extracted
workspace copies.

## Verified Ticonderoga result

- ModelUber records: **10 / 10**
- Render-bearing LOD0 hull components: **4**
- Exact LOD0 section-to-MFM bindings: **31**
- Geometry section existence checks: **62 / 62**
- Render material-pair checks: **31 / 31**
- Palette/correction-node checks: **31 / 31**
- Combat hardpoints with local/world matrices: **17 / 17**
- Non-finite matrices: **0**
- Pointer or variant-chain errors: **0**
- Acceptance: **PASS**

The root model's LOD0 descriptor intentionally has zero render sets. It is the
assembly/root node; Bow, MidFront, MidBack, and Stern provide the detailed
LOD0 hull geometry.

## Important byte-layout clarification

The observed render record really is 0x20 bytes:

| Offset | Field |
|---:|---|
| `0x00` | `u64` MFM resource ID |
| `0x08` | `u32` material-name string ID |
| `0x0C` | `u32` vertices-section string ID |
| `0x10` | `u32` indices-section string ID |
| `0x14` | `u8` skinned |
| `0x15` | `u8` palette count |
| `0x16` | two zero padding bytes |
| `0x18` | `i64` palette pointer relative to this render record |

A six-byte pad plus the `i64` pointer would not fit a 0x20-byte record. All
Ticonderoga records verify the two-byte pad and pointer offsets above.

Material property count is the low `u16` at material-header `+0x0C`; the high
`u16` is a separate flag word. Property type tag `0` is bool and tag `1` is
int. Port-only ModelUber records have `material_count == 0` and legally store
zero in all three optional material pointer slots.

## Files

- `modeluber_parser.py` — reusable bounds-checked parser.
- `build_ticon_lod0_manifest.py` — deterministic Ticonderoga manifest builder.
- `test_modeluber_parser.py` — pure and optional real-data regression tests.

Generated `ticon_lod0_section_mfm.json` contains exact bindings, matrices,
hardpoints, materials and validation, but it is a large user-local research
output and is deliberately not bundled.

## Rebuild

상위 pipeline은 mapping 코어 안의 같은 ModelUber 해석을 자동으로 사용해요.
이 독립 manifest 도구를 실행하려면 이미 외부 출력에 추출한 사용자 소유
sidecar/geometry와 생성 mapping을 명시하세요.

```powershell
python .\build_ticon_lod0_manifest.py `
  --assets 'D:\Exports\extracted\content\assets.bin' `
  --index 'D:\Exports\extracted\content\prototypes.index.data' `
  --prototypes 'D:\Exports\extracted\content\prototypes.data' `
  --geometry-dir 'D:\Exports\extracted\content\gameplay\usa\ship\cruiser\ASC307_Ticonderoga_1990' `
  --reference 'D:\Exports\generated\ticonderoga_1990_static_assembly.json' `
  --output 'D:\Exports\generated\ticon_lod0_section_mfm.json'
```

## Test

이 디렉터리에서 실행하세요.

```powershell
python -B -m unittest -v test_modeluber_parser.py
```

자산 없는 배포 패키지에서는 pure matrix test가 실행되고 real-data test 6개는
명시적으로 skip돼요. 검증용 사용자 소유 fixture가 있는 연구 환경에서는
7/7이 통과했어요.
