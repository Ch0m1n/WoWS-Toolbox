# Format research and third-party references

The decoder in this folder is an independent implementation written for this
task. It uses no copied source files or imported modules from the projects
below.

## SkaceKamen/wot-model-converter

- Repository: <https://github.com/SkaceKamen/wot-model-converter>
- Locally inspected commit:
  `79abff5c376ad76f442d2259c0980cfacbeb1187`
- Relevant files:
  - `wot/ModelReader.py`
  - `wot/VertexTypes.py`
- Use in this task: behavioral reference for the legacy BigWorld trailing
  section table, packed-normal interpretation, axis mapping, and triangle
  winding.
- License finding: no `LICENSE`, `COPYING`, or equivalent license file exists
  in that commit. Because no redistribution license was found, its source code
  was not copied into this decoder.

## Gamemodels3D/TheRipperNotes

- Format notes:
  <https://github.com/Gamemodels3D/TheRipperNotes/blob/main/Engines/BigWorld/04.format-primitives.md>
- Use in this task: independent cross-reference for the BigWorld
  primitives/sectioned-file family.

## Wargaming assets

World of Warships: Legends models and textures remain Wargaming assets. This
tool does not include or redistribute those assets. Generated exports should be
kept within the permissions granted by the game's terms and applicable law.
