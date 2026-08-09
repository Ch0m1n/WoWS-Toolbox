# Third-party notice

Format reference:

- **Simi4/WoT-Blender-Addons**
- Repository: <https://github.com/Simi4/WoT-Blender-Addons>
- Reference commit: `e711c6478cdb23503df8b17baac43db147b999d8`
- Relevant file:
  `tank_viewer/map_viewer/compiled_space/anca_reader/__init__.py`
- License: Do What The Fuck You Want To Public License, Version 2

The local implementation adds bounds checks, preserves decoded values, reads
the Legends duration field as a float, emits a versioned JSON schema, and makes
the unsupported streamed payload/standalone `.anim` boundary explicit.

World of Warships: Legends assets are not included. Users must supply files
from their own installation and follow the game's applicable terms.
