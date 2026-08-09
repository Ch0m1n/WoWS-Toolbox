# Legends ANCA minimal decoder

This independent, standard-library-only probe reads animation containers found
in a user-owned Steam installation of **World of Warships: Legends**. It emits
JSON intended for later Blender binding by object or pose-bone name.

## Implemented and locally verified

- ANCA packed container entry version 6
- animation duration, identifiers, and channel table
- channel types 1–5 from the public format reference
- inline scale/position/rotation keys plus index tables
- streamed-channel fallback scale/position/quaternion
- length-prefixed trailing stream block
- container preload boundary equals the streamed payload start
- Blender-facing target/pivot map at
  `animation.blender_pivot_channels`
- strict bounds and collection-count checks

## Honest support boundary

The trailing streamed payload is length-checked and fingerprinted, but its
bit-packed keyframes are not decoded yet. Streamed channels therefore provide
a verified fallback pivot and binding name, **not animated Blender F-curves**.

Standalone `.anim` files use another format and are deliberately rejected.
The AM5058 VLS hatch animations are not marked as supported.

The output also keeps engine-space values raw. The model importer must supply:

- the actual node hierarchy
- BigWorld-to-Blender coordinate conversion
- local/world transform policy
- time-unit/FPS conversion

Quaternion arrays are exposed as raw `x,y,z,w`; Blender expects `w,x,y,z`.

## Usage

```powershell
python .\decode_anca.py `
  "D:\path\to\AM5048.anca" `
  --output ".\AM5048.channels.json"
```

List packed entries:

```powershell
python .\decode_anca.py "D:\path\to\AM5048.anca" --list-sections
```

Validate samples:

```powershell
python .\validate_samples.py `
  "D:\path\to\AM5048.anca" `
  "D:\path\to\AM5049.anca"
```

## Public-source basis

The format was independently implemented from the public `anca_reader` in
Simi4/WoT-Blender-Addons under WTFPL v2. See
`THIRD_PARTY_NOTICES.md`. Local game files were used only for validation; no
game asset is bundled.
