//! Constants for GameParams pickle dictionary keys.
//!
//! These replace the hardcoded `HashableValue::String("...".to_string().into())`
//! patterns scattered throughout `provider.rs` and `main.rs`.

// Ship top-level keys
pub const SHIP_UPGRADE_INFO: &str = "ShipUpgradeInfo";
pub const SHIP_ABILITIES: &str = "ShipAbilities";
pub const A_HULL: &str = "A_Hull";

// Upgrade dict keys
pub const UC_TYPE: &str = "ucType";
pub const COMPONENTS: &str = "components";

// ucType values
pub const UC_TYPE_HULL: &str = "_Hull";
pub const UC_TYPE_ARTILLERY: &str = "_Artillery";
pub const UC_TYPE_TORPEDOES: &str = "_Torpedoes";

// Component type keys (inside "components" dict)
pub const COMP_HULL: &str = "hull";
pub const COMP_ARTILLERY: &str = "artillery";
pub const COMP_SECONDARY_ARTILLERY: &str = "secondaryArtillery";
pub const COMP_ATBA: &str = "atba";
pub const COMP_AIR_DEFENSE: &str = "airDefense";
pub const COMP_DIRECTORS: &str = "directors";
pub const COMP_FINDERS: &str = "finders";
pub const COMP_RADARS: &str = "radars";
pub const COMP_TORPEDOES: &str = "torpedoes";
pub const COMP_DEPTH_CHARGES: &str = "depthCharges";
pub const COMP_AIR_ARMAMENT: &str = "airArmament";

/// Typed representation of component type keys.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(
    feature = "rkyv",
    derive(rkyv::Archive, rkyv::Serialize, rkyv::Deserialize)
)]
#[cfg_attr(feature = "rkyv", rkyv(derive(Hash, PartialEq, Eq)))]
pub enum ComponentType {
    #[cfg_attr(feature = "serde", serde(rename = "hull"))]
    Hull,
    #[cfg_attr(feature = "serde", serde(rename = "artillery"))]
    Artillery,
    #[cfg_attr(feature = "serde", serde(rename = "secondaryArtillery"))]
    SecondaryArtillery,
    #[cfg_attr(feature = "serde", serde(rename = "atba"))]
    Atba,
    #[cfg_attr(feature = "serde", serde(rename = "airDefense"))]
    AirDefense,
    #[cfg_attr(feature = "serde", serde(rename = "directors"))]
    Directors,
    #[cfg_attr(feature = "serde", serde(rename = "finders"))]
    Finders,
    #[cfg_attr(feature = "serde", serde(rename = "radars"))]
    Radars,
    #[cfg_attr(feature = "serde", serde(rename = "torpedoes"))]
    Torpedoes,
    #[cfg_attr(feature = "serde", serde(rename = "depthCharges"))]
    DepthCharges,
    #[cfg_attr(feature = "serde", serde(rename = "airArmament"))]
    AirArmament,
}

impl ComponentType {
    /// All known component types.
    pub const ALL: &[ComponentType] = &[
        Self::Hull,
        Self::Artillery,
        Self::SecondaryArtillery,
        Self::Atba,
        Self::AirDefense,
        Self::Directors,
        Self::Finders,
        Self::Radars,
        Self::Torpedoes,
        Self::DepthCharges,
        Self::AirArmament,
    ];

    /// The raw string key used in GameParams dictionaries.
    pub fn key(&self) -> &'static str {
        match self {
            Self::Hull => "hull",
            Self::Artillery => "artillery",
            Self::SecondaryArtillery => "secondaryArtillery",
            Self::Atba => "atba",
            Self::AirDefense => "airDefense",
            Self::Directors => "directors",
            Self::Finders => "finders",
            Self::Radars => "radars",
            Self::Torpedoes => "torpedoes",
            Self::DepthCharges => "depthCharges",
            Self::AirArmament => "airArmament",
        }
    }
}

impl std::fmt::Display for ComponentType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Hull => write!(f, "Hull"),
            Self::Artillery => write!(f, "Main Battery"),
            Self::SecondaryArtillery => write!(f, "Secondary Artillery"),
            Self::Atba => write!(f, "Secondaries"),
            Self::AirDefense => write!(f, "AA"),
            Self::Directors => write!(f, "Directors"),
            Self::Finders => write!(f, "Finders"),
            Self::Radars => write!(f, "Radars"),
            Self::Torpedoes => write!(f, "Torpedoes"),
            Self::DepthCharges => write!(f, "Depth Charges"),
            Self::AirArmament => write!(f, "Aircraft Armament"),
        }
    }
}

/// All component type keys.
pub const ALL_COMPONENT_TYPES: &[&str] = &[
    COMP_HULL,
    COMP_ARTILLERY,
    COMP_SECONDARY_ARTILLERY,
    COMP_ATBA,
    COMP_AIR_DEFENSE,
    COMP_DIRECTORS,
    COMP_FINDERS,
    COMP_RADARS,
    COMP_TORPEDOES,
    COMP_DEPTH_CHARGES,
    COMP_AIR_ARMAMENT,
];

/// Component types that have 3D models (mounted on hull hardpoints).
pub const MODEL_COMPONENT_TYPES: &[&str] = &[
    COMP_HULL,
    COMP_ARTILLERY,
    COMP_SECONDARY_ARTILLERY,
    COMP_ATBA,
    COMP_AIR_DEFENSE,
    COMP_DIRECTORS,
    COMP_FINDERS,
    COMP_RADARS,
    COMP_TORPEDOES,
    COMP_DEPTH_CHARGES,
    COMP_AIR_ARMAMENT,
];

// Data field keys
pub const MODEL: &str = "model";
pub const ARMOR: &str = "armor";
pub const HIT_LOCATION_GROUPS: &str = "hitLocationGroups";
pub const HL_TYPE: &str = "hlType";
pub const MAX_HP: &str = "maxHP";
pub const REGENERATED_HP_PART: &str = "regeneratedHPPart";
pub const SPLASH_BOXES: &str = "splashBoxes";
pub const THICKNESS: &str = "thickness";
pub const DRAFT: &str = "draft";
pub const DOCK_Y_OFFSET: &str = "dockYOffset";
pub const VISIBILITY_FACTOR: &str = "visibilityFactor";
pub const VISIBILITY_FACTOR_BY_PLANE: &str = "visibilityFactorByPlane";
pub const MAX_DIST: &str = "maxDist";
pub const AMMO_LIST: &str = "ammoList";
pub const CAMOUFLAGE: &str = "camouflage";
pub const PERMOFLAGES: &str = "permoflages";
pub const TITLE: &str = "title";

// HP_ mount prefix
pub const HP_PREFIX: &str = "HP_";

// typeinfo keys
pub const TYPEINFO: &str = "typeinfo";
pub const TYPEINFO_TYPE: &str = "type";
pub const TYPEINFO_NATION: &str = "nation";
pub const TYPEINFO_SPECIES: &str = "species";

// Param identity keys
pub const PARAM_ID: &str = "id";
pub const PARAM_INDEX: &str = "index";
pub const PARAM_NAME: &str = "name";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secondary_artillery_is_a_separate_exported_component() {
        // Songun's 150mm mounts are under secondaryArtillery, while its
        // 105mm mounts are under atba. Both must be enumerated.
        assert_ne!(ComponentType::SecondaryArtillery, ComponentType::Atba);
        assert_eq!(ComponentType::SecondaryArtillery.key(), "secondaryArtillery");
        assert!(ComponentType::ALL.contains(&ComponentType::SecondaryArtillery));
        assert!(ALL_COMPONENT_TYPES.contains(&COMP_SECONDARY_ARTILLERY));
        assert!(MODEL_COMPONENT_TYPES.contains(&COMP_SECONDARY_ARTILLERY));
    }

    #[test]
    fn component_registries_agree() {
        let typed: Vec<_> = ComponentType::ALL.iter().map(ComponentType::key).collect();
        assert_eq!(typed, ALL_COMPONENT_TYPES);
        assert_eq!(typed, MODEL_COMPONENT_TYPES);
    }
}
