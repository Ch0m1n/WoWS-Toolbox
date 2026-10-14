//! Parser for `camouflages.xml` — camouflage definitions including color schemes.
//!
//! The game's camouflage system defines texture overrides in a large XML file
//! (`camouflages.xml` in the VFS root). Each `<camouflage>` entry maps a name
//! (e.g. `mat_Steel`) to per-part albedo texture paths. Tiled camouflages also
//! reference a `colorScheme` that provides 4 RGBA colors used to colorize a
//! repeating tile pattern texture.

use std::collections::{HashMap, HashSet};
use std::io::Read;

use vfs::VfsPath;

/// A color scheme with 4 RGBA colors (linear space).
///
/// The tile texture acts as a color-indexed mask: Black/R/G/B zones map to
/// color0/color1/color2/color3 respectively.
pub struct ColorScheme {
    pub name: String,
    pub colors: [[f32; 4]; 4],
}

/// UV scale/offset transform for a part category in a tiled camo.
#[derive(Clone, Debug)]
pub struct UvTransform {
    pub scale: [f32; 2],
    pub offset: [f32; 2],
}

impl Default for UvTransform {
    fn default() -> Self {
        Self {
            scale: [1.0, 1.0],
            offset: [0.0, 0.0],
        }
    }
}

/// A parsed camouflage entry from `camouflages.xml`.
pub struct CamouflageEntry {
    /// Name, e.g. "mat_Steel" or "camo_CN_NY_2018_02_tile".
    pub name: String,
    /// Whether this camo uses UV tiling (tile texture + colorScheme).
    pub tiled: bool,
    /// Whether the texture is a color-index mask that must use a color scheme.
    /// Some ship-specific permanent camos use palettes without UV tiling.
    pub use_color_scheme: bool,
    /// Per-part albedo texture paths. Key = part category (lowercase, e.g. "hull"),
    /// Value = VFS path to the albedo DDS. For tiled camos, typically just "tile".
    pub textures: HashMap<String, String>,
    /// Ordered color schemes. The first item is the default
    /// palette and later items are the alternate palettes exposed by the game.
    pub color_schemes: Vec<String>,
    /// Per-part UV transforms (for tiled camos). Key = part category in lowercase.
    pub uv_transforms: HashMap<String, UvTransform>,
    /// Ship group names this entry applies to (empty = default/fallback).
    pub ship_groups: Vec<String>,
    /// Exact GameParams vehicle names this entry applies to.
    pub target_ships: Vec<String>,
}

/// Classify an MFM stem into a camouflage part category.
///
/// The camouflages.xml UV section uses categories like Tile (=hull), DeckHouse,
/// Gun, Director, Plane, Float, Misc, Bulge. MFM stems use prefixes like
/// `JSB039_Yamato_1945_Hull` or `JGA010_25mm_Type96`.
pub fn classify_part_category(mfm_stem: &str) -> &'static str {
    // Check suffix-based patterns first (hull parts end with _Hull, _DeckHouse, etc.)
    let lower = mfm_stem.to_lowercase();
    if lower.ends_with("_hull") || lower.ends_with("_hull_wire") {
        return "tile"; // "Tile" in XML = hull
    }
    if lower.ends_with("_deckhouse") {
        return "deckhouse";
    }
    if lower.contains("_bulge") {
        return "bulge";
    }

    // Prefix-based patterns for turrets/equipment (2-letter nation + category code)
    // Extract the category code (position 2..4 of the stem, e.g. "GA" from "JGA010...")
    let bytes = mfm_stem.as_bytes();
    if bytes.len() >= 4 && bytes[0].is_ascii_uppercase() {
        let cat = &mfm_stem[1..3];
        match cat {
            // Main/secondary guns
            "GM" | "GS" | "GA" => return "gun",
            // Directors / fire control
            "D0" | "D1" => return "director",
            // Rangefinders
            "F0" | "F1" => return "director",
            // Radars / sensors
            "RS" => return "misc",
            _ => {}
        }
    }

    // Fallback: hull/tile for ship body parts (prefix matches ship code pattern)
    if lower.contains("_hull") {
        return "tile";
    }

    // Default to tile (hull) — the most common category
    "tile"
}

/// Parsed camouflage database from `camouflages.xml`.
pub struct CamouflageDb {
    /// Multiple entries per camo name (different ship groups have different UV values).
    entries: HashMap<String, Vec<CamouflageEntry>>,
    color_schemes: HashMap<String, ColorScheme>,
    /// Ship group → set of ship index names (e.g. "IJN_group_5" → {"PJSB018_Yamato_1944", ...}).
    ship_groups: HashMap<String, HashSet<String>>,
}

impl CamouflageDb {
    /// Load and parse `camouflages.xml` from the VFS.
    pub fn load(vfs: &VfsPath) -> Option<Self> {
        let mut xml_bytes = Vec::new();
        vfs.join("camouflages.xml")
            .ok()?
            .open_file()
            .ok()?
            .read_to_end(&mut xml_bytes)
            .ok()?;
        let xml_str = String::from_utf8_lossy(&xml_bytes);
        Self::parse(&xml_str)
    }

    fn parse(xml: &str) -> Option<Self> {
        let doc = roxmltree::Document::parse(xml).ok()?;

        // Parse <shipgroups.xml> section: group name → set of ship index names.
        let mut ship_groups: HashMap<String, HashSet<String>> = HashMap::new();
        if let Some(sg_node) = doc
            .root()
            .children()
            .find(|n| n.is_element())
            .and_then(|data| data.children().find(|n| n.has_tag_name("shipgroups.xml")))
        {
            for group_node in sg_node.children().filter(|n| n.is_element()) {
                let group_name = group_node.tag_name().name().to_string();
                if let Some(ships_node) = group_node.children().find(|n| n.has_tag_name("ships"))
                    && let Some(text) = ships_node.text()
                {
                    let indices: HashSet<String> =
                        text.split_whitespace().map(|s| s.to_string()).collect();
                    ship_groups.insert(group_name, indices);
                }
            }
        }

        // Parse color schemes.
        let mut color_schemes = HashMap::new();
        // PC uses <colorScheme><name>...; Korabli also uses named
        // palette elements such as <colorSchemeDiane> under colorschemes.xml.
        for cs_node in doc.descendants().filter(|n| {
            n.has_tag_name("colorScheme")
                || (n.is_element() && n.parent().is_some_and(|p| p.has_tag_name("colorschemes.xml")))
        }) {
            let name = child_text(&cs_node, "name").map(str::trim)
                .unwrap_or_else(|| cs_node.tag_name().name());
            if name.is_empty() {
                continue;
            }

            let mut colors = [[0.0f32; 4]; 4];
            for (i, color) in colors.iter_mut().enumerate() {
                let tag = format!("color{i}");
                if let Some(text) = child_text(&cs_node, &tag) {
                    let parts: Vec<f32> = text
                        .split_whitespace()
                        .filter_map(|s| s.parse().ok())
                        .collect();
                    if parts.len() >= 4 {
                        *color = [parts[0], parts[1], parts[2], parts[3]];
                    }
                }
            }

            color_schemes.insert(
                name.to_string(),
                ColorScheme {
                    name: name.to_string(),
                    colors,
                },
            );
        }

        // Parse camouflage entries. Same name may appear multiple times with
        // different <shipGroups>, so we collect them into Vec per name.
        let mut entries: HashMap<String, Vec<CamouflageEntry>> = HashMap::new();
        for camo_node in doc.descendants().filter(|n| n.has_tag_name("camouflage")) {
            let Some(name) = child_text(&camo_node, "name").map(|s| s.trim()) else {
                continue;
            };
            if name.is_empty() {
                continue;
            }
            let tiled = child_text(&camo_node, "tiled")
                .map(|s| s.trim().eq_ignore_ascii_case("true"))
                .unwrap_or(false);
            let use_color_scheme = child_text(&camo_node, "useColorScheme")
                .map(|s| s.trim().eq_ignore_ascii_case("true"))
                .unwrap_or(false);

            let mut textures = HashMap::new();
            if let Some(tex_node) = camo_node.children().find(|n| n.has_tag_name("Textures")) {
                for child in tex_node.children().filter(|n| n.is_element()) {
                    let tag = child.tag_name().name();
                    // Skip MGN (metallic/gloss/normal) and animmap entries.
                    if tag.ends_with("_mgn") || tag.ends_with("_animmap") {
                        continue;
                    }
                    if let Some(path) = child.text().map(|t| t.trim().to_string())
                        && !path.is_empty()
                    {
                        textures.insert(tag.to_lowercase(), path);
                    }
                }
            }

            // Multiple colorSchemes nodes represent the default and alternate
            // palettes. Keep their source order; selecting only the first node
            // made the in-game secondary color impossible to export.
            let mut color_schemes = Vec::new();
            for node in camo_node
                .children()
                .filter(|n| n.has_tag_name("colorSchemes"))
            {
                if let Some(name) = node
                    .text()
                    .map(str::trim)
                    .and_then(|text| text.split_whitespace().next())
                    .filter(|name| !name.is_empty())
                    && !color_schemes.iter().any(|existing| existing == name)
                {
                    color_schemes.push(name.to_string());
                }
            }

            // Parse <shipGroups> text: space-separated group names.
            let camo_ship_groups: Vec<String> = child_text(&camo_node, "shipGroups")
                .map(|s| s.split_whitespace().map(|g| g.to_string()).collect())
                .unwrap_or_default();

            let target_ships: Vec<String> = camo_node
                .children()
                .filter(|n| n.has_tag_name("targetShip"))
                .filter_map(|n| n.text())
                .flat_map(str::split_whitespace)
                .map(str::to_string)
                .collect();

            // Parse UV transforms per part category.
            let mut uv_transforms = HashMap::new();
            if let Some(uv_node) = camo_node.children().find(|n| n.has_tag_name("UV")) {
                for child in uv_node.children().filter(|n| n.is_element()) {
                    let tag = child.tag_name().name().to_lowercase();
                    let scale = child_text(&child, "scale").or_else(|| child.text())
                        .map(|s| {
                            let parts: Vec<f32> = s
                                .split_whitespace()
                                .filter_map(|v| v.parse().ok())
                                .collect();
                            if parts.len() >= 2 {
                                [parts[0], parts[1]]
                            } else {
                                [1.0, 1.0]
                            }
                        })
                        .unwrap_or([1.0, 1.0]);
                    let offset = child_text(&child, "offset")
                        .map(|s| {
                            let parts: Vec<f32> = s
                                .split_whitespace()
                                .filter_map(|v| v.parse().ok())
                                .collect();
                            if parts.len() >= 2 {
                                [parts[0], parts[1]]
                            } else {
                                [0.0, 0.0]
                            }
                        })
                        .unwrap_or([0.0, 0.0]);
                    uv_transforms.insert(tag, UvTransform { scale, offset });
                }
            }

            entries
                .entry(name.to_string())
                .or_default()
                .push(CamouflageEntry {
                    name: name.to_string(),
                    tiled,
                    use_color_scheme,
                    textures,
                    color_schemes,
                    uv_transforms,
                    ship_groups: camo_ship_groups,
                    target_ships,
                });
        }

        Some(Self {
            entries,
            color_schemes,
            ship_groups,
        })
    }

    /// Look up a camouflage by name, resolving the correct ship-group-specific
    /// entry for the given ship index (e.g. "PJSB018_Yamato_1944").
    ///
    /// If `ship_index` is provided, returns the entry whose ship groups contain
    /// a group that includes the ship. Falls back to the entry with no ship
    /// groups (default), or the first entry if no match.
    pub fn get(&self, name: &str, ship_index: Option<&str>) -> Option<&CamouflageEntry> {
        let variants = self.entries.get(name)?;
        // Exact targetShip bindings are more specific than nation/group rules.
        // camo_permanent_1 has hundreds of ship-specific entries, so falling
        // back to the first entry applies another ship's textures and palette.
        if let Some(idx) = ship_index {
            if let Some(entry) = variants
                .iter()
                .find(|entry| entry.target_ships.iter().any(|target| target == idx))
            {
                return Some(entry);
            }

            // Find the variant whose ship groups match.
            for entry in variants {
                if entry.ship_groups.is_empty() {
                    continue;
                }
                for group_name in &entry.ship_groups {
                    if let Some(members) = self.ship_groups.get(group_name)
                        && members.contains(idx)
                    {
                        return Some(entry);
                    }
                }
            }
        }

        // A truly generic entry has neither an exact target nor a ship group.
        // If a selector was supplied, never borrow a different ship's targeted
        // entry as a fallback.
        variants
            .iter()
            .find(|e| e.ship_groups.is_empty() && e.target_ships.is_empty())
            .or_else(|| ship_index.is_none().then(|| variants.first()).flatten())
    }

    /// Look up a color scheme by name.
    pub fn color_scheme(&self, name: &str) -> Option<&ColorScheme> {
        self.color_schemes.get(name)
    }

    /// Number of unique camouflage names in the database.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the database is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Total number of camouflage entries (including ship-group variants).
    pub fn total_entries(&self) -> usize {
        self.entries.values().map(|v| v.len()).sum()
    }
}

fn child_text<'a>(node: &'a roxmltree::Node, tag: &str) -> Option<&'a str> {
    node.children().find(|n| n.has_tag_name(tag))?.text()
}

#[cfg(test)]
mod tests {
    use super::CamouflageDb;

    const XML: &str = r#"
        <data>
          <shipgroups.xml><GROUP_A><ships>PAAA001_First</ships></GROUP_A></shipgroups.xml>
          <colorschemes.xml>
            <colorScheme><name>scheme_default</name><color0>1 0 0 1</color0></colorScheme>
            <colorScheme><name>scheme_alt</name><color0>0 0 1 1</color0></colorScheme>
          </colorschemes.xml>
          <camouflages.xml>
            <camouflage>
              <name>camo_permanent_1</name>
              <tiled>false</tiled>
              <useColorScheme>True</useColorScheme>
              <targetShip>PAAA001_First</targetShip>
              <Textures><Tile>first.dds</Tile></Textures>
              <colorSchemes>scheme_default<colorUI>1 0 0 1</colorUI></colorSchemes>
              <colorSchemes>scheme_alt<colorUI>0 0 1 1</colorUI></colorSchemes>
            </camouflage>
            <camouflage>
              <name>camo_permanent_1</name>
              <tiled>true</tiled>
              <targetShip>PBBB002_Second</targetShip>
              <Textures><Tile>second.dds</Tile></Textures>
              <colorSchemes>second_only</colorSchemes>
            </camouflage>
          </camouflages.xml>
        </data>
    "#;

    #[test]
    fn korabli_named_palettes_keep_both_colors_and_alpha() {
        let db = CamouflageDb::parse(r#"<data><colorschemes.xml>
          <colorSchemeDiane><color0>0.15 0.251 0.135 1</color0><color1>0 0 0 0</color1></colorSchemeDiane>
          <colorSchemeDuperre><color0>0.128 0.155 0.166 1</color0></colorSchemeDuperre>
          <colorScheme><name>pc_palette</name><color0>1 0 0 0.5</color0></colorScheme>
        </colorschemes.xml><camouflages.xml><camouflage><name>camo_permanent_1</name>
          <targetShip>PFSC211_La_Rochelle</targetShip><useColorScheme>true</useColorScheme>
          <colorSchemes>colorSchemeDiane</colorSchemes><colorSchemes>colorSchemeDuperre</colorSchemes>
        </camouflage></camouflages.xml></data>"#).unwrap();
        let entry = db.get("camo_permanent_1", Some("PFSC211_La_Rochelle")).unwrap();
        assert_eq!(entry.color_schemes.len(), 2);
        assert_eq!(db.color_scheme(&entry.color_schemes[0]).unwrap().colors[0], [0.15,0.251,0.135,1.0]);
        assert_eq!(db.color_scheme(&entry.color_schemes[0]).unwrap().colors[1], [0.0;4]);
        assert_eq!(db.color_scheme(&entry.color_schemes[1]).unwrap().colors[0], [0.128,0.155,0.166,1.0]);
        assert_eq!(db.color_scheme("pc_palette").unwrap().colors[0], [1.0,0.0,0.0,0.5]);
    }

    #[test]
    fn exact_target_ship_wins_and_all_color_schemes_are_kept() {
        let db = CamouflageDb::parse(XML).expect("fixture should parse");
        let entry = db
            .get("camo_permanent_1", Some("PAAA001_First"))
            .expect("first ship should resolve");
        assert_eq!(
            entry.textures.get("tile").map(String::as_str),
            Some("first.dds")
        );
        assert!(!entry.tiled);
        assert!(entry.use_color_scheme);
        assert_eq!(entry.color_schemes, ["scheme_default", "scheme_alt"]);
    }

    #[test]
    fn targeted_entry_never_falls_back_to_a_different_ship() {
        let db = CamouflageDb::parse(XML).expect("fixture should parse");
        assert!(
            db.get("camo_permanent_1", Some("PCCC003_Missing"))
                .is_none()
        );
    }
}
