//! High-level ship model export API.
//!
//! Provides [`ShipAssets`] (shared expensive resources, created once) and
//! [`ShipModelContext`] (a fully-loaded ship, ready for GLB export).
//!
//! # Quick start
//! ```no_run
//! use wowsunpack::export::ship::{ShipAssets, ShipExportOptions};
//! use rootcause::prelude::Report;
//! # fn main() -> Result<(), Report> {
//! # let vfs: vfs::VfsPath = todo!();
//! let assets = ShipAssets::load(&vfs)?;
//! let ctx = assets.load_ship("Yamato", &ShipExportOptions::default())?;
//! let mut file = std::fs::File::create("yamato.glb")?;
//! ctx.export_glb(&mut file)?;
//! # Ok(())
//! # }
//! ```

use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::path::Path;
use std::sync::Arc;

use rootcause::prelude::*;
use vfs::VfsPath;

use crate::data::ResourceLoader;
use crate::game_params::keys;
use crate::game_params::provider::GameMetadataProvider;
use crate::game_params::types::{ArmorMap, GameParamProvider, MountPoint, Vehicle};
use crate::models::assets_bin::{self, PrototypeDatabase};
use crate::models::geometry;
use crate::models::model;
use crate::models::skeleton_ext;
use crate::models::visual::{self, VisualPrototype};

use super::camouflage::{self, CamouflageDb};
use super::gltf_export::{self, InteractiveArmorMesh, SubModel, TextureSet};
use super::texture;

fn read_assets_bin_bytes(vfs: &VfsPath) -> Result<Vec<u8>, Report> {
    if let Ok(path) = std::env::var("WOWS_ASSETS_BIN_OVERRIDE") {
        return Ok(std::fs::read(&path).context("Could not read assets.bin override")?);
    }

    let mut bytes = Vec::new();
    vfs.join("content/assets.bin")
        .context("VFS path error")?
        .open_file()
        .context("Could not find content/assets.bin in VFS")?
        .read_to_end(&mut bytes)?;
    Ok(bytes)
}
// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// Options controlling ship model export.
#[derive(Debug, Clone)]
pub struct ShipExportOptions {
    /// LOD level (0 = highest detail). Default: 0.
    pub lod: usize,
    /// Hull upgrade selection. `None` = first/stock hull.
    /// Accepts full upgrade name (e.g. "PJUH911_Yamato_1944") or a prefix
    /// match against the hull component name (e.g. "B").
    pub hull: Option<String>,
    /// Whether to embed textures in the GLB. Default: true.
    pub textures: bool,
    /// Export the damaged/destroyed hull state instead of intact.
    /// When true, crack geometry is included and patch geometry is excluded.
    /// Default: false (intact hull).
    pub damaged: bool,
    /// Include gameplay armor-shell geometry. Disabled for normal editable exports.
    pub include_armor: bool,
    /// Include every camouflage material variant. Disabled to keep exports compact.
    pub include_camo_variants: bool,
    /// Camouflage scheme to promote to the default exported material.
    /// The special value "native" selects the ship's first permanent camouflage.
    pub camouflage: Option<String>,
    /// Exact palette identifier from the selected camouflage entry.
    /// None selects the first/default palette.
    pub camouflage_color_scheme: Option<String>,
    /// Module overrides: component type key (e.g. "artillery") to component name.
    /// Overrides the default component for specific types.
    pub module_overrides:
        std::collections::HashMap<crate::game_params::keys::ComponentType, String>,
}

impl Default for ShipExportOptions {
    fn default() -> Self {
        Self {
            lod: 0,
            hull: None,
            textures: true,
            damaged: false,
            include_armor: false,
            include_camo_variants: false,
            camouflage: None,
            camouflage_color_scheme: None,
            module_overrides: std::collections::HashMap::new(),
        }
    }
}

/// Resolved ship identity information.
#[derive(Debug, Clone)]
pub struct ShipInfo {
    /// Model directory name, e.g. "JSB039_Yamato_1945".
    pub model_dir: String,
    /// Translated display name if translations are loaded, e.g. "Yamato".
    pub display_name: Option<String>,
    /// GameParam index key, e.g. "PJSB018".
    pub param_index: String,
}

/// Summary of a hull upgrade for listing purposes.
#[derive(Debug, Clone)]
pub struct HullUpgradeInfo {
    /// Upgrade name (GameParam key), e.g. "PJUH911_Yamato_1944".
    pub name: String,
    /// Components in this upgrade: (type_key, component_name, mount_count).
    pub components: Vec<(String, String, usize)>,
}

// ---------------------------------------------------------------------------
// ShipAssets — shared expensive resources (created once)
// ---------------------------------------------------------------------------

/// Shared game assets for ship export operations.
///
/// Creating this is the expensive step (~18 seconds for GameParams parsing).
/// Reuse a single instance across multiple ship exports.
pub struct ShipAssets {
    assets_bin_bytes: Vec<u8>,
    vfs: VfsPath,
    metadata: Arc<GameMetadataProvider>,
    camo_db: Option<CamouflageDb>,
}

impl ShipAssets {
    /// Load shared assets from the VFS.
    ///
    /// This is expensive (~18 seconds) because it parses GameParams.
    /// Create once and reuse for multiple ships.
    pub fn load(vfs: &VfsPath) -> Result<Self, Report> {
        let assets_bin_bytes = read_assets_bin_bytes(vfs)?;

        let metadata =
            Arc::new(GameMetadataProvider::from_vfs(vfs).context("Failed to load GameParams")?);

        let camo_db = CamouflageDb::load(vfs);

        Ok(Self {
            assets_bin_bytes,
            vfs: vfs.clone(),
            metadata,
            camo_db,
        })
    }

    /// Load shared assets from the VFS, reusing an already-loaded [`GameMetadataProvider`].
    ///
    /// This skips the expensive GameParams parse that [`Self::load`] performs,
    /// making it suitable when the caller already has metadata available.
    pub fn from_vfs_with_metadata(
        vfs: &VfsPath,
        metadata: Arc<GameMetadataProvider>,
    ) -> Result<Self, Report> {
        let assets_bin_bytes = read_assets_bin_bytes(vfs)?;

        let camo_db = CamouflageDb::load(vfs);

        Ok(Self {
            assets_bin_bytes,
            vfs: vfs.clone(),
            metadata,
            camo_db,
        })
    }

    /// Load shared assets directly from a World of Warships installation directory.
    ///
    /// This is a convenience wrapper that builds the VFS (idx files + assets.bin overlay)
    /// from the game directory, then calls [`Self::load`]. It uses the latest build
    /// found in the `bin/` directory.
    ///
    /// For callers who already have a VFS, use [`Self::load`] instead.
    pub fn from_game_dir(game_dir: &Path) -> Result<Self, Report> {
        let vfs = crate::game_data::build_game_vfs(game_dir)?;
        Self::load(&vfs)
    }

    /// Set translations for display name resolution.
    ///
    /// # Panics
    /// Panics if the inner metadata Arc has been cloned (i.e. there are other owners).
    /// Call this before sharing the assets.
    pub fn set_translations(&mut self, catalog: gettext::Catalog) {
        Arc::get_mut(&mut self.metadata)
            .expect("cannot set translations: GameMetadataProvider is shared")
            .set_translations(catalog);
    }

    /// Access the underlying `GameMetadataProvider`.
    pub fn metadata(&self) -> &GameMetadataProvider {
        &self.metadata
    }

    /// Access the underlying VFS root.
    pub fn vfs(&self) -> &VfsPath {
        &self.vfs
    }

    /// Find a ship by name (fuzzy display-name match or exact model dir).
    pub fn find_ship(&self, name: &str) -> Result<ShipInfo, Report> {
        let db = self.db()?;
        let self_id_index = db.build_self_id_index();

        // Strategy 1: try direct match against assets.bin paths.
        let needle = format!("/{name}/");
        let has_direct = db.paths_storage.iter().any(|e| {
            e.name.ends_with(".visual") && {
                // Reconstruct is expensive; just check if any visual file's
                // full path contains the needle. We only need one hit.
                let idx = db
                    .paths_storage
                    .iter()
                    .position(|x| std::ptr::eq(x, e))
                    .unwrap();
                db.reconstruct_path(idx, &self_id_index).contains(&needle)
            }
        });

        if has_direct {
            // Direct model dir match — no GameParams needed for identity.
            // Try to find the GameParam for richer info.
            let param = self.metadata.params().iter().find(|p| {
                p.vehicle()
                    .and_then(|v| v.model_path())
                    .map(|mp| mp.contains(name))
                    .unwrap_or(false)
            });

            return Ok(ShipInfo {
                model_dir: name.to_string(),
                display_name: param.and_then(|p| {
                    self.metadata
                        .localized_name_from_param(p)
                        .map(|s: &str| s.to_string())
                }),
                param_index: param.map(|p| p.index().to_string()).unwrap_or_default(),
            });
        }

        // Strategy 2: exact param index match via GameParams.
        if let Some(param) = self.metadata.game_param_by_index(name)
            && let Some(vehicle) = param.vehicle()
            && let Some(model_path) = vehicle.model_path()
        {
            let dir = model_path
                .rsplit_once('/')
                .map(|(d, _)| d)
                .unwrap_or(model_path);
            let model_dir = dir.rsplit('/').next().unwrap_or(dir);
            return Ok(ShipInfo {
                model_dir: model_dir.to_string(),
                display_name: self
                    .metadata
                    .localized_name_from_param(&param)
                    .map(|s: &str| s.to_string()),
                param_index: param.index().to_string(),
            });
        }

        // Strategy 3: fuzzy display name match via GameParams.
        let normalized_input = unidecode::unidecode(name).to_lowercase();
        let mut matches: Vec<(String, String, String)> = Vec::new();

        for param in self.metadata.params() {
            let vehicle = match param.vehicle() {
                Some(v) => v,
                None => continue,
            };
            let model_path = match vehicle.model_path() {
                Some(p) => p,
                None => continue,
            };

            let display_name = self
                .metadata
                .localized_name_from_param(param)
                .map(|s: &str| s.to_string())
                .unwrap_or_else(|| param.index().to_string());

            let normalized_display = unidecode::unidecode(&display_name).to_lowercase();
            if normalized_display.contains(&normalized_input) {
                let dir = model_path
                    .rsplit_once('/')
                    .map(|(d, _)| d)
                    .unwrap_or(model_path);
                let dir_name = dir.rsplit('/').next().unwrap_or(dir);
                matches.push((
                    display_name,
                    param.index().to_string(),
                    dir_name.to_string(),
                ));
            }
        }

        match matches.len() {
            0 => bail!(
                "No ship found matching '{name}'. Try using the model directory name \
                 (e.g. 'JSB039_Yamato_1945')."
            ),
            1 => Ok(ShipInfo {
                model_dir: matches[0].2.clone(),
                display_name: Some(matches[0].0.clone()),
                param_index: matches[0].1.clone(),
            }),
            _ => {
                // If all matches share the same model dir, use it.
                let unique_dirs: HashSet<&str> =
                    matches.iter().map(|(_, _, d)| d.as_str()).collect();
                if unique_dirs.len() == 1 {
                    return Ok(ShipInfo {
                        model_dir: matches[0].2.clone(),
                        display_name: Some(matches[0].0.clone()),
                        param_index: matches[0].1.clone(),
                    });
                }

                let listing: Vec<String> = matches
                    .iter()
                    .map(|(display, idx, dir)| format!("  {display} ({idx}) -> {dir}"))
                    .collect();
                bail!(
                    "Multiple ships match '{name}':\n{}\nPlease refine your search \
                     or use the model directory name directly.",
                    listing.join("\n")
                );
            }
        }
    }

    /// List hull upgrades for a ship.
    pub fn list_hull_upgrades(&self, name: &str) -> Result<Vec<HullUpgradeInfo>, Report> {
        let info = self.find_ship(name)?;
        let vehicle = self.find_vehicle(&info.model_dir)?;

        let Some(upgrades) = vehicle.hull_upgrades() else {
            return Ok(Vec::new());
        };

        let mut result = Vec::new();
        let mut sorted: Vec<_> = upgrades.iter().collect();
        sorted.sort_by_key(|(k, _)| (*k).clone());

        for (upgrade_name, config) in sorted {
            let mut components = Vec::new();
            for ct in keys::ComponentType::ALL {
                let comp = config.component_name(*ct).unwrap_or("(none)").to_string();
                let mount_count = config.mounts(*ct).map(|m| m.len()).unwrap_or(0);
                components.push((ct.to_string(), comp, mount_count));
            }
            result.push(HullUpgradeInfo {
                name: upgrade_name.clone(),
                components,
            });
        }

        Ok(result)
    }

    /// List available camouflage texture schemes for a ship.
    pub fn list_texture_schemes(&self, name: &str) -> Result<Vec<String>, Report> {
        let info = self.find_ship(name)?;
        let db = self.db()?;
        let self_id_index = db.build_self_id_index();

        // Collect visuals for the ship model dir.
        let visual_paths = self.find_visual_paths(&db, &self_id_index, &info.model_dir);
        let sub_models = self.load_sub_models(&db, &self_id_index, &visual_paths)?;

        // Also load turret models to include their stems.
        let vehicle = self
            .metadata
            .params()
            .iter()
            .find(|param| param.index() == info.param_index)
            .and_then(|param| param.vehicle())
            .or_else(|| self.find_vehicle(&info.model_dir).ok());
        let mount_points = vehicle
            .and_then(|v| self.select_hull_mount_points(v, None, &std::collections::HashMap::new()))
            .unwrap_or_default();
        let turret_data = self.load_turret_models(&db, &self_id_index, &mount_points)?;

        let mut all_stems = Vec::new();
        for smd in &sub_models {
            for mfm in collect_mfm_info(&smd.visual, &db) {
                all_stems.push(mfm.stem);
            }
        }
        for tmd in &turret_data {
            for mfm in collect_mfm_info(&tmd.visual, &db) {
                all_stems.push(mfm.stem);
            }
        }

        let mut schemes = texture::discover_texture_schemes(&self.vfs, &all_stems);

        // Also include material-based camo scheme display names.
        let ship_index = if info.param_index.is_empty() {
            self.find_ship_index(&info.model_dir)
        } else {
            Some(info.param_index.clone())
        };
        let ship_idx = ship_index.as_deref();
        let ship_param_name = ship_idx.and_then(|index| {
            self.metadata
                .params()
                .iter()
                .find(|param| param.index() == index)
                .map(|param| param.name().to_string())
        });
        let ship_selector = ship_param_name.as_deref().or(ship_idx);
        let mat_camos = self.discover_mat_camo_schemes(&info.model_dir, ship_idx, ship_selector);
        for scheme in &mat_camos {
            let tag = if scheme.tiled { "tiled" } else { "mat_camo" };
            if scheme.color_schemes.len() > 1 {
                for (color_id, _) in &scheme.color_schemes {
                    schemes.push(format!("{} [{}] ({})", scheme.display_name, color_id, tag));
                }
            } else {
                schemes.push(format!("{} ({})", scheme.display_name, tag));
            }
        }

        // Include universal camos (PCEC entries available to all ships).
        let universal = self.discover_universal_camo_schemes(ship_selector);
        for scheme in &universal {
            let tag = if scheme.tiled { "tiled" } else { "mat_camo" };
            if scheme.color_schemes.len() > 1 {
                for (color_id, _) in &scheme.color_schemes {
                    schemes.push(format!(
                        "{} [{}] (universal/{})",
                        scheme.display_name, color_id, tag
                    ));
                }
            } else {
                schemes.push(format!("{} (universal/{})", scheme.display_name, tag));
            }
        }

        Ok(schemes)
    }

    /// Load a complete ship model, ready for export.
    pub fn load_ship(
        &self,
        name: &str,
        options: &ShipExportOptions,
    ) -> Result<ShipModelContext, Report> {
        let info = self.find_ship(name)?;
        // Several event/clone ships intentionally share the same model directory.
        // Looking the Vehicle up by that directory is ambiguous and used to make
        // the selected ship depend on HashMap iteration order. In the unlucky
        // case an unarmed sibling was selected and the exported GLB contained the
        // hull only. Prefer the exact Param resolved by find_ship and retain the
        // directory lookup solely for old metadata without a Param index.
        let vehicle = self
            .metadata
            .params()
            .iter()
            .find(|param| param.index() == info.param_index)
            .and_then(|param| param.vehicle())
            .or_else(|| self.find_vehicle(&info.model_dir).ok());
        self.load_ship_inner(info, vehicle, options)
    }

    /// Load a ship using a [`Vehicle`] reference instead of a name lookup.
    ///
    /// This is useful when the caller already has a `Vehicle` from their own
    /// GameParams processing and wants to skip the name-based search.
    pub fn load_ship_from_vehicle(
        &self,
        vehicle: &Vehicle,
        options: &ShipExportOptions,
    ) -> Result<ShipModelContext, Report> {
        let model_path = vehicle
            .model_path()
            .ok_or_else(|| rootcause::report!("Vehicle has no model_path"))?;
        // model_path is like "content/gameplay/nation/ship/DIR_NAME/file.model"
        let dir = model_path
            .rsplit_once('/')
            .map(|(d, _)| d)
            .unwrap_or(model_path);
        let model_dir = dir.rsplit('/').next().unwrap_or(dir);

        let param = self.metadata.params().iter().find(|p| {
            p.vehicle()
                .and_then(|v| v.model_path())
                .map(|mp| mp.contains(model_dir))
                .unwrap_or(false)
        });

        let info = ShipInfo {
            model_dir: model_dir.to_string(),
            display_name: param.and_then(|p| {
                self.metadata
                    .localized_name_from_param(p)
                    .map(|s: &str| s.to_string())
            }),
            param_index: param.map(|p| p.index().to_string()).unwrap_or_default(),
        };

        self.load_ship_inner(info, Some(vehicle), options)
    }

    fn load_ship_inner(
        &self,
        info: ShipInfo,
        vehicle: Option<&Vehicle>,
        options: &ShipExportOptions,
    ) -> Result<ShipModelContext, Report> {
        let db = self.db()?;
        let self_id_index = db.build_self_id_index();

        // Find all .visual files in the model directory.
        let visual_paths = self.find_visual_paths(&db, &self_id_index, &info.model_dir);
        if visual_paths.is_empty() {
            bail!("No .visual files found for '{}'.", info.model_dir);
        }

        // Load hull sub-models.
        let hull_parts = self.load_sub_models(&db, &self_id_index, &visual_paths)?;
        // Ships 2.0 roots can have no drawable LODs but still own the
        // unified armor shell. Keep these bytes separate from editable parts.
        let mut hull_armor_geometry = Vec::new();
        let mut seen_armor_paths = HashSet::new();
        for (name, _) in &visual_paths {
            let Ok(visual) = resolve_visual_prototype(&db, &format!("{name}.visual"), &self_id_index) else { continue };
            if !visual.lods.is_empty() { continue; }
            let Some(index) = self_id_index.get(&visual.merged_geometry_path_id) else { continue };
            let path = db.reconstruct_path(*index, &self_id_index);
            if !seen_armor_paths.insert(path.clone()) { continue; }
            let Ok(mut file) = self.vfs.join(&path).and_then(|p| p.open_file()) else { continue };
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).context("Could not read root armor geometry")?;
            let geometry = geometry::parse_geometry(&bytes).context("Could not parse root armor geometry")?;
            if !geometry.armor_models.is_empty() { hull_armor_geometry.push(bytes); }
        }

        // Load turret/mount models from GameParams.
        let mount_points: Vec<MountPoint> = vehicle
            .and_then(|v| {
                self.select_hull_mount_points(v, options.hull.as_deref(), &options.module_overrides)
            })
            .unwrap_or_default();

        let mut loaded = self.load_mounts(&db, &self_id_index, &mount_points, &hull_parts)?;
        self.load_model_point_attachments(&db, &self_id_index, &hull_parts, &mount_points, &mut loaded)?;
        let turret_models = loaded.turret_models;
        let mounts = loaded.mounts;

        // Resolve material-based camouflage schemes (ship-specific + universal).
        let ship_index = if info.param_index.is_empty() {
            self.find_ship_index(&info.model_dir)
        } else {
            Some(info.param_index.clone())
        };
        let ship_idx = ship_index.as_deref();
        let ship_param_name = ship_idx.and_then(|index| {
            self.metadata
                .params()
                .iter()
                .find(|param| param.index() == index)
                .map(|param| param.name().to_string())
        });
        let ship_selector = ship_param_name.as_deref().or(ship_idx);
        let mut mat_camo_schemes =
            self.discover_mat_camo_schemes(&info.model_dir, ship_idx, ship_selector);
        let native_camouflage = mat_camo_schemes
            .first()
            .map(|scheme| scheme.display_name.clone());
        let requested_native = options
            .camouflage
            .as_deref()
            .map(|selection| selection.eq_ignore_ascii_case("native"))
            .unwrap_or(false);
        if !requested_native {
            mat_camo_schemes.extend(self.discover_universal_camo_schemes(ship_selector));
        }

        let mut resolved_options = options.clone();
        if requested_native {
            resolved_options.camouflage = Some(native_camouflage.ok_or_else(|| {
                rootcause::report!("This ship does not declare a permanent camouflage.")
            })?);
        } else if let Some(selection) = options.camouflage.as_deref() {
            if let Some(scheme) = mat_camo_schemes.iter().find(|scheme| {
                scheme.display_name.eq_ignore_ascii_case(selection)
                    || scheme.param_name.eq_ignore_ascii_case(selection)
                    || scheme.camouflage_name.eq_ignore_ascii_case(selection)
            }) {
                // Texture promotion uses display names internally. Accept stable
                // GameParams IDs from the GUI and normalize them here.
                resolved_options.camouflage = Some(scheme.display_name.clone());
            }
        }

        if let Some(color_selection) = options.camouflage_color_scheme.as_deref() {
            let camo_selection = resolved_options.camouflage.as_deref().ok_or_else(|| {
                rootcause::report!("A camouflage color scheme requires a selected camouflage.")
            })?;
            let scheme = mat_camo_schemes
                .iter()
                .find(|scheme| scheme.display_name.eq_ignore_ascii_case(camo_selection))
                .ok_or_else(|| {
                    rootcause::report!(
                        "Camouflage color scheme '{}' cannot be resolved because camouflage '{}' was not found.",
                        color_selection,
                        camo_selection
                    )
                })?;
            let canonical = scheme
                .color_schemes
                .iter()
                .find(|(name, _)| name.eq_ignore_ascii_case(color_selection))
                .map(|(name, _)| name.clone())
                .ok_or_else(|| {
                    let available = scheme
                        .color_schemes
                        .iter()
                        .map(|(name, _)| name.as_str())
                        .collect::<Vec<_>>()
                        .join(", ");
                    rootcause::report!(
                        "Color scheme '{}' is not available for '{}'. Available: {}",
                        color_selection,
                        scheme.display_name,
                        available
                    )
                })?;
            resolved_options.camouflage_color_scheme = Some(canonical);
        }

        // Extract armor thickness map and hit locations from GameParams.
        let armor_map = vehicle.and_then(|v| v.armor().cloned());
        let hit_locations = vehicle.and_then(|v| v.hit_locations().cloned());

        let root_splash_bytes = vehicle.and_then(|v| v.model_path()).and_then(|model| {
            let path = model.replace('\\', "/").replace(".model", ".splash");
            let mut file = self.vfs.join(&path).and_then(|p| p.open_file()).ok()?;
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).ok()?;
            Some(bytes)
        });
        Ok(ShipModelContext {
            vfs: self.vfs.clone(),
            assets_bin_bytes: self.assets_bin_bytes.clone(),
            hull_parts,
            hull_armor_geometry,
            root_splash_bytes,
            turret_models,
            mounts,
            info,
            options: resolved_options,
            mat_camo_schemes,
            armor_map,
            hit_locations,
        })
    }

    // --- Internal helpers ---

    /// Discover material-based camo schemes available for a ship via GameParams.
    ///
    /// Follows: Vehicle.permoflages → Exterior.camouflage → camouflages.xml entry.
    /// Returns owned `MatCamoScheme` data (no lifetimes).
    fn discover_mat_camo_schemes(
        &self,
        model_dir: &str,
        ship_index: Option<&str>,
        ship_selector: Option<&str>,
    ) -> Vec<MatCamoScheme> {
        let camo_db = match &self.camo_db {
            Some(db) => db,
            None => return Vec::new(),
        };
        // Clone/event ships can share the same model directory. Resolve the
        // exact Vehicle selected by the catalog first; falling back to the
        // model directory alone can pick a sibling and silently expose the
        // wrong permanent-camouflage list.
        let vehicle = ship_index
            .and_then(|index| {
                self.metadata
                    .params()
                    .iter()
                    .find(|param| param.index() == index)
                    .and_then(|param| param.vehicle())
            })
            .or_else(|| self.find_vehicle(model_dir).ok());
        let Some(vehicle) = vehicle else {
            return Vec::new();
        };

        let mut result = Vec::new();
        let mut seen_camo_names = HashSet::new();

        for permo_name in vehicle.permoflages() {
            // permoflages entries are param names (e.g. "PCEM017_Steel_10lvl"), not indices.
            let param = self
                .metadata
                .game_param_by_name(permo_name)
                .or_else(|| self.metadata.game_param_by_index(permo_name));
            let Some(param) = param else {
                continue;
            };
            let Some(exterior) = param.exterior() else {
                continue;
            };
            let Some(camo_name) = exterior.camouflage() else {
                continue;
            };

            // Deduplicate by camo name (multiple exteriors can share the same camo).
            if !seen_camo_names.insert(camo_name.to_string()) {
                continue;
            }

            let Some(entry) = camo_db.get(camo_name, ship_selector) else {
                continue;
            };
            if entry.textures.is_empty() {
                continue;
            }

            // Build display name from translation.
            // Exterior entries use IDS_{NAME_UPPER} as the translation key
            // (e.g. "PCEM017_Steel_10lvl" → "IDS_PCEM017_STEEL_10LVL").
            let ids_key = format!("IDS_{}", permo_name.to_uppercase());
            let display_name = self
                .metadata
                .localized_name_from_id(&ids_key)
                .filter(|s| s != &ids_key) // gettext returns key as-is when not found
                .or_else(|| {
                    // Fallback: try IDS_{index}
                    self.metadata
                        .localized_name_from_param(&param)
                        .filter(|s| !s.starts_with("IDS_"))
                        .map(|s| s.to_string())
                })
                .unwrap_or_else(|| camo_name.to_string());

            // Collect unique texture paths (most mat_camos reuse one texture for all parts).
            let mut unique_paths: Vec<String> = Vec::new();
            let mut seen_paths = HashSet::new();
            for path in entry.textures.values() {
                if seen_paths.insert(path.clone()) {
                    unique_paths.push(path.clone());
                }
            }
            unique_paths.sort();

            let color_schemes = if entry.tiled || entry.use_color_scheme {
                entry
                    .color_schemes
                    .iter()
                    .filter_map(|name| {
                        camo_db
                            .color_scheme(name)
                            .map(|scheme| (name.clone(), scheme.colors))
                    })
                    .collect()
            } else {
                Vec::new()
            };

            result.push(MatCamoScheme {
                display_name,
                param_name: permo_name.to_string(),
                camouflage_name: camo_name.to_string(),
                texture_paths: unique_paths,
                texture_paths_by_category: entry.textures.clone(),
                tiled: entry.tiled,
                use_color_scheme: entry.use_color_scheme,
                color_schemes,
                uv_transforms: entry.uv_transforms.clone(),
            });
        }

        result
    }

    /// Discover universal camouflage schemes (PCEC entries available to all ships).
    ///
    /// These are not referenced by any ship's `permoflages` list — they're
    /// universally applicable. Deduplicated by camouflage name.
    fn discover_universal_camo_schemes(&self, ship_selector: Option<&str>) -> Vec<MatCamoScheme> {
        let camo_db = match &self.camo_db {
            Some(db) => db,
            None => return Vec::new(),
        };

        let mut result = Vec::new();
        let mut seen_camo_names = HashSet::new();

        for param in self.metadata.params() {
            let name = param.name();
            if !name.starts_with("PCEC") {
                continue;
            }
            let Some(exterior) = param.exterior() else {
                continue;
            };
            let Some(camo_name) = exterior.camouflage() else {
                continue;
            };
            if !seen_camo_names.insert(camo_name.to_string()) {
                continue;
            }

            let Some(entry) = camo_db.get(camo_name, ship_selector) else {
                continue;
            };
            if entry.textures.is_empty() {
                continue;
            }

            let ids_key = format!("IDS_{}", name.to_uppercase());
            let display_name = self
                .metadata
                .localized_name_from_id(&ids_key)
                .filter(|s| s != &ids_key)
                .unwrap_or_else(|| camo_name.to_string());

            let color_schemes = if entry.tiled || entry.use_color_scheme {
                entry
                    .color_schemes
                    .iter()
                    .filter_map(|name| {
                        camo_db
                            .color_scheme(name)
                            .map(|scheme| (name.clone(), scheme.colors))
                    })
                    .collect()
            } else {
                Vec::new()
            };

            let mut unique_paths: Vec<String> = Vec::new();
            let mut seen_paths = HashSet::new();
            for path in entry.textures.values() {
                if seen_paths.insert(path.clone()) {
                    unique_paths.push(path.clone());
                }
            }
            unique_paths.sort();

            result.push(MatCamoScheme {
                display_name,
                param_name: name.to_string(),
                camouflage_name: camo_name.to_string(),
                texture_paths: unique_paths,
                texture_paths_by_category: entry.textures.clone(),
                tiled: entry.tiled,
                use_color_scheme: entry.use_color_scheme,
                color_schemes,
                uv_transforms: entry.uv_transforms.clone(),
            });
        }

        result
    }

    /// Re-parse the PrototypeDatabase from owned bytes.
    fn db(&self) -> Result<PrototypeDatabase<'_>, Report> {
        Ok(assets_bin::parse_assets_bin(&self.assets_bin_bytes)
            .context("Failed to parse assets.bin")?)
    }

    /// Find a Vehicle by model directory name.
    fn find_vehicle(&self, model_dir: &str) -> Result<&crate::game_params::types::Vehicle, Report> {
        self.metadata
            .params()
            .iter()
            .filter_map(|p| p.vehicle())
            .find(|v| {
                v.model_path()
                    .map(|mp| mp.contains(model_dir))
                    .unwrap_or(false)
            })
            .ok_or_else(|| rootcause::report!("Ship '{}' not found in GameParams", model_dir))
    }

    /// Find the ship param's index name (e.g. "PJSB018_Yamato_1944") from model directory.
    fn find_ship_index(&self, model_dir: &str) -> Option<String> {
        self.metadata
            .params()
            .iter()
            .find(|p| {
                p.vehicle()
                    .and_then(|v| v.model_path())
                    .map(|mp| mp.contains(model_dir))
                    .unwrap_or(false)
            })
            .map(|p| p.index().to_string())
    }

    /// Scan paths_storage for .visual files in a directory matching the name.
    fn find_visual_paths(
        &self,
        db: &PrototypeDatabase<'_>,
        self_id_index: &HashMap<u64, usize>,
        model_dir: &str,
    ) -> Vec<(String, String)> {
        let needle = format!("/{model_dir}/");
        let mut result = Vec::new();

        for (i, entry) in db.paths_storage.iter().enumerate() {
            if !entry.name.ends_with(".visual") {
                continue;
            }
            let full_path = db.reconstruct_path(i, self_id_index);
            if full_path.starts_with('@') || full_path.contains("/lods/") {
                continue;
            }
            if full_path.contains(&needle) {
                let sub_name = entry
                    .name
                    .strip_suffix(".visual")
                    .unwrap_or(&entry.name)
                    .to_string();
                result.push((sub_name, full_path));
            }
        }

        result.sort_by(|a, b| a.0.cmp(&b.0));
        result
    }

    /// Load and parse all sub-models from (name, full_path) pairs.
    fn load_sub_models(
        &self,
        db: &PrototypeDatabase<'_>,
        self_id_index: &HashMap<u64, usize>,
        visual_paths: &[(String, String)],
    ) -> Result<Vec<OwnedSubModel>, Report> {
        let mut result: Vec<OwnedSubModel> = Vec::new();

        for (sub_name, full_path) in visual_paths {
            // Split-layout clients publish mapped content records alongside
            // @content shadows. Resolve normal visuals by canonical resource
            // leaf while retaining full_path for diagnostics.
            let visual_lookup = format!("{sub_name}.visual");
            let vp = match resolve_visual_prototype(db, &visual_lookup, self_id_index) {
                Ok(visual) => visual,
                Err(e) => {
                    eprintln!("Warning: skipping '{full_path}': {e}");
                    continue;
                }
            };

            if std::env::var_os("WOWS_DEBUG_VISUAL").is_some() {
                let mapped_names = vp
                    .nodes
                    .name_map_name_ids
                    .iter()
                    .map(|id| db.strings.get_string_by_id(*id).unwrap_or("<unknown>"))
                    .collect::<Vec<_>>();
                let node_names = vp
                    .nodes
                    .name_ids
                    .iter()
                    .map(|id| db.strings.get_string_by_id(*id).unwrap_or("<unknown>"))
                    .collect::<Vec<_>>();
                eprintln!(
                    "DEBUG visual {} mapped={:?} nodes={:?}",
                    sub_name, mapped_names, node_names
                );
            }

            // Current Korabli builds keep ship weapon hardpoints in node-only
            // `_ports.visual` records. Ships 2.0 also adds state-specific
            // `_ports_base_*` placeholders with no geometry. Merge only the
            // canonical ports nodes; the state variants duplicate/override
            // those nodes for the live renderer and must not become GLB parts.
            if (vp.merged_geometry_path_id == u64::MAX || vp.render_sets.is_empty())
                && let Some((target_name, ports_variant)) = sub_name.split_once("_ports")
            {
                if ports_variant.is_empty() {
                    if let Some(target) = result.iter_mut().find(|part| part.name == target_name) {
                        let node_offset = u16::try_from(target.visual.nodes.name_ids.len())
                            .context("Too many visual nodes while merging ports")?;
                        target
                            .visual
                            .nodes
                            .name_map_name_ids
                            .extend(vp.nodes.name_map_name_ids);
                        target.visual.nodes.name_map_node_ids.extend(
                            vp.nodes
                                .name_map_node_ids
                                .into_iter()
                                .map(|index| index.saturating_add(node_offset)),
                        );
                        target.visual.nodes.name_ids.extend(vp.nodes.name_ids);
                        target.visual.nodes.matrices.extend(vp.nodes.matrices);
                        target
                            .visual
                            .nodes
                            .parent_ids
                            .extend(vp.nodes.parent_ids.into_iter().map(|parent| {
                                if parent == u16::MAX {
                                    parent
                                } else {
                                    parent.saturating_add(node_offset)
                                }
                            }));
                    } else {
                        eprintln!(
                            "Warning: no matching part '{target_name}' for ports visual '{full_path}'"
                        );
                    }
                } else {
                    eprintln!("Skipping node-only ports state visual '{full_path}'");
                }
                continue;
            }

            // Other node-only state helpers are valid assets but not geometry.
            // Ignore them instead of failing the whole ship export on the
            // sentinel mergedGeometryPathId used by Ships 2.0.
            if vp.merged_geometry_path_id == u64::MAX && vp.render_sets.is_empty() {
                eprintln!("Skipping node-only visual '{full_path}'");
                continue;
            }

            // Ships 2.0 keeps the ship-level attachment hierarchy in a root
            // visual with no drawable LODs. The actual hull geometry lives in
            // sibling Bow/MidFront/MidBack/Stern visuals. Trying to open the
            // root's legacy mergedGeometryPathId produces a nonexistent
            // `<ship>.geometry` path and aborts the whole export.
            if vp.lods.is_empty() {
                eprintln!("Skipping sub-model '{sub_name}' because it has 0 LODs");
                continue;
            }

            let geom_path_idx =
                self_id_index
                    .get(&vp.merged_geometry_path_id)
                    .ok_or_else(|| {
                        rootcause::report!(
                            "Could not resolve mergedGeometryPathId 0x{:016X} for {}",
                            vp.merged_geometry_path_id,
                            sub_name
                        )
                    })?;
            let geom_full_path = db.reconstruct_path(*geom_path_idx, self_id_index);

            let mut geom_bytes = Vec::new();
            self.vfs
                .join(&geom_full_path)
                .context("VFS path error")?
                .open_file()
                .context_with(|| format!("Could not open geometry: {geom_full_path}"))?
                .read_to_end(&mut geom_bytes)?;

            // Try loading the .splash file (same directory, same base name).
            let splash_bytes = if geom_full_path.ends_with(".geometry") {
                let splash_path = format!(
                    "{}.splash",
                    &geom_full_path[..geom_full_path.len() - ".geometry".len()]
                );
                let mut buf = Vec::new();
                match self.vfs.join(&splash_path).and_then(|p| p.open_file()) {
                    Ok(mut f) => {
                        let _ = f.read_to_end(&mut buf);
                        Some(buf)
                    }
                    Err(_) => None,
                }
            } else {
                None
            };

            result.push(OwnedSubModel {
                name: sub_name.clone(),
                visual: vp,
                geom_bytes,
                splash_bytes,
            });
        }

        Ok(result)
    }

    /// Select mount points for the chosen hull upgrade, with optional module overrides.
    fn select_hull_mount_points(
        &self,
        vehicle: &crate::game_params::types::Vehicle,
        hull_selection: Option<&str>,
        module_overrides: &std::collections::HashMap<
            crate::game_params::keys::ComponentType,
            String,
        >,
    ) -> Option<Vec<MountPoint>> {
        let upgrades = vehicle.hull_upgrades()?;
        let mut sorted: Vec<_> = upgrades.iter().collect();
        sorted.sort_by_key(|(k, _)| (*k).clone());

        let selected = if let Some(sel) = hull_selection {
            sorted
                .iter()
                .find(|(name, _)| *name == sel || name.to_lowercase().contains(&sel.to_lowercase()))
                .or_else(|| {
                    let prefix = format!("{sel}_");
                    sorted.iter().find(|(_, config)| {
                        config
                            .component_name(keys::ComponentType::Hull)
                            .map(|n| n.starts_with(&prefix))
                            .unwrap_or(false)
                    })
                })
                .copied()
        } else {
            sorted.first().copied()
        };

        selected.map(|(_, config)| {
            if module_overrides.is_empty() {
                config.all_mount_points().cloned().collect()
            } else {
                config
                    .mount_points_with_overrides(module_overrides)
                    .cloned()
                    .collect()
            }
        })
    }

    /// Load turret models and build mount resolution data.
    fn load_mounts(
        &self,
        db: &PrototypeDatabase<'_>,
        self_id_index: &HashMap<u64, usize>,
        mount_points: &[MountPoint],
        hull_parts: &[OwnedSubModel],
    ) -> Result<LoadedMounts, Report> {
        // Collect hardpoint transforms from hull visuals.
        let mut hp_transforms: HashMap<String, [f32; 16]> = HashMap::new();
        for smd in hull_parts {
            for &name_id in &smd.visual.nodes.name_map_name_ids {
                if let Some(name) = db.strings.get_string_by_id(name_id)
                    && name.starts_with("HP_")
                    && let Some(xform) = smd.visual.find_hardpoint_transform(name, &db.strings)
                {
                    hp_transforms.insert(name.to_string(), xform);
                }
            }
        }

        // Load unique turret models.
        let (turret_models, turret_model_index) =
            self.load_turret_models_deduped(db, self_id_index, mount_points)?;

        // Map hull HP names to turret model paths so we can find the parent
        // turret visual for compound hardpoints.
        let hp_to_model_path: HashMap<&str, &str> = mount_points
            .iter()
            .filter(|mi| !mi.model_path().is_empty() && hp_transforms.contains_key(mi.hp_name()))
            .map(|mi| (mi.hp_name(), mi.model_path()))
            .collect();

        // Build resolved mounts.
        let mut mounts = Vec::new();
        for mi in mount_points {
            let Some(&model_idx) = turret_model_index.get(mi.model_path()) else {
                continue;
            };

            // Resolve transform: simple HP from hull directly, compound HP
            // by composing parent (hull) and child (turret visual) transforms.
            // A mount is compound iff its HP name is NOT in the hull node tree.
            let (hull_transform, child_hp_transform) =
                if let Some(&xform) = hp_transforms.get(mi.hp_name()) {
                    (xform, None)
                } else {
                    match resolve_compound_hp(
                        mi.hp_name(),
                        &hp_transforms,
                        &hp_to_model_path,
                        &turret_model_index,
                        &turret_models,
                        &db.strings,
                    ) {
                        Some(result) => result,
                        None => {
                            eprintln!("Warning: could not resolve hardpoint '{}'", mi.hp_name());
                            continue;
                        }
                    }
                };
            let hp_transform = match child_hp_transform {
                None => hull_transform,
                Some(child_xform) => mat4_mul_col_major(&hull_transform, &child_xform),
            };

            // The turret model's Rotate_Y_BlendBone encodes its rest-pose
            // facing direction. To place the model correctly at the hardpoint
            // we undo this rest-pose rotation so the hardpoint's own orientation
            // takes over: visual_transform = hp_transform * inverse(bone_rotation).
            // Armor geometry is already aligned with the hardpoint, so it uses
            // the raw transform without rotation correction.
            let armor_transform = Some(hp_transform);
            let turret_visual = &turret_models[model_idx].visual;
            let yaw_correction = turret_visual
                .find_node_local_matrix("Rotate_Y_BlendBone", &db.strings)
                .map(|bone_local| mat4_rotation_inverse(&bone_local));
            let base_transform = match yaw_correction {
                Some(inv) => mat4_mul_col_major(&hp_transform, &inv),
                None => hp_transform,
            };

            let visual_transform = Some(base_transform);

            // Build per-vertex barrel pitch if pitchDeadZones applies.
            let min_pitch = mi.min_pitch_at_yaw(0.0);
            let barrel_pitch = if min_pitch > 0.0 {
                build_barrel_pitch(turret_visual, &db.strings, min_pitch)
            } else {
                None
            };

            mounts.push(ResolvedMount {
                hp_name: mi.hp_name().to_string(),
                turret_model_index: model_idx,
                transform: visual_transform,
                armor_transform,
                mount_armor: mi.mount_armor().cloned(),
                species: mi.species(),
                barrel_pitch,
            });
        }

        Ok(LoadedMounts {
            turret_models,
            turret_model_index,
            mounts,
        })
    }

    /// MP nodes reference authored miscellaneous models, independently of
    /// GameParams weapons. Expand them on both the hull and each mounted gun.
    fn load_model_point_attachments(
        &self, db: &PrototypeDatabase<'_>, index: &HashMap<u64, usize>,
        hull: &[OwnedSubModel], mount_points: &[MountPoint], loaded: &mut LoadedMounts,
    ) -> Result<(), Report> {
        let mut paths = HashMap::new();
        for (i, entry) in db.paths_storage.iter().enumerate() {
            let Some(stem) = entry.name.strip_suffix(".model") else { continue };
            let path = db.reconstruct_path(i, index);
            if !path.starts_with('@') && !path.contains("/lods/") {
                paths.entry(stem.to_string()).or_insert(path);
            }
        }
        let mut queue = Vec::new();
        let collect = |part: &OwnedSubModel, parent: Option<[f32;16]>, prefix: &str, selection: Option<&MountPoint>,
                       queue: &mut Vec<(String,String,[f32;16])>| {
            for (name, local) in collect_part_model_points(db, index, &paths, part) {
                let Some(stem) = model_point_stem(&name) else {continue};
                if selection.is_some_and(|m| !m.allows_misc(&name, stem)) { continue; }
                // Descriptive MP_RM3502_Rangefinder_8m maps to the authored
                // misc/RM3502/RM3502.model resource, not a descriptive filename.
                let Some(path) = paths.get(stem).or_else(|| {
                    let code = stem.split('_').next()?;
                    paths.get(code).filter(|path| path.contains("/misc/"))
                }) else {
                    eprintln!("Warning: unresolved model point {prefix}/{name}");
                    continue;
                };
                let transform = parent.map(|p|mat4_mul_col_major(&p,&local)).unwrap_or(local);
                queue.push((format!("{prefix}/{name}"),path.clone(),transform));
            }
        };
        for part in hull {collect(part,None,&part.name,None,&mut queue);}
        for mount in &loaded.mounts {
            // Socket matrices belong to the authored skeleton. The mesh-only
            // inverse bind yaw in mount.transform must not rotate them again.
            let Some(selection) = mount_points.iter().find(|m| m.hp_name() == mount.hp_name) else {continue};
            collect(&loaded.turret_models[mount.turret_model_index],mount.armor_transform,&mount.hp_name,Some(selection),&mut queue);
        }
        let mut count = 0;
        for (name,path,transform) in queue {
            let model_idx = if let Some(&i) = loaded.turret_model_index.get(&path) { i } else {
                match self.load_single_turret(db,index,&path) {
                    Ok(part) => {
                        let i=loaded.turret_models.len();
                        loaded.turret_models.push(part);
                        loaded.turret_model_index.insert(path.clone(),i);
                        i
                    },
                    Err(e) => {eprintln!("Warning: attachment {name}: {e}");continue;}
                }
            };
            loaded.mounts.push(ResolvedMount {
                hp_name:name,turret_model_index:model_idx,transform:Some(transform),
                armor_transform:Some(transform),mount_armor:None,species:None,barrel_pitch:None,
            });
            count += 1;
        }
        eprintln!("Resolved {count} authored model-point attachment placements");
        Ok(())
    }

    /// Load unique turret models, deduplicating by model path.
    fn load_turret_models_deduped(
        &self,
        db: &PrototypeDatabase<'_>,
        self_id_index: &HashMap<u64, usize>,
        mount_points: &[MountPoint],
    ) -> Result<(Vec<OwnedSubModel>, HashMap<String, usize>), Report> {
        let mut index_map: HashMap<String, usize> = HashMap::new();
        let mut models = Vec::new();

        for mi in mount_points {
            if mi.model_path().is_empty() || index_map.contains_key(mi.model_path()) {
                continue;
            }

            match self.load_single_turret(db, self_id_index, mi.model_path()) {
                Ok(smd) => {
                    let idx = models.len();
                    index_map.insert(mi.model_path().to_string(), idx);
                    models.push(smd);
                }
                Err(e) => {
                    eprintln!("Warning: could not load turret '{}': {e}", mi.model_path());
                }
            }
        }

        Ok((models, index_map))
    }

    /// Load turret models (non-deduplicating variant for texture listing).
    fn load_turret_models(
        &self,
        db: &PrototypeDatabase<'_>,
        self_id_index: &HashMap<u64, usize>,
        mount_points: &[MountPoint],
    ) -> Result<Vec<OwnedSubModel>, Report> {
        let (models, _) = self.load_turret_models_deduped(db, self_id_index, mount_points)?;
        Ok(models)
    }

    /// Load a single turret model from its .model path.
    fn load_single_turret(
        &self,
        db: &PrototypeDatabase<'_>,
        self_id_index: &HashMap<u64, usize>,
        model_path: &str,
    ) -> Result<OwnedSubModel, Report> {
        let visual_path = model_path.replace('\\', "/").replace(".model", ".visual");

        let visual_lookup = visual_path.rsplit('/').next().unwrap_or(&visual_path);

        let vp = resolve_visual_prototype(db, visual_lookup, self_id_index)
            .context("Failed to parse turret visual")?;

        let geom_path_idx = self_id_index
            .get(&vp.merged_geometry_path_id)
            .ok_or_else(|| {
                rootcause::report!("Could not resolve geometry for turret '{}'", visual_path)
            })?;
        let geom_full_path = db.reconstruct_path(*geom_path_idx, self_id_index);

        let mut geom_bytes = Vec::new();
        self.vfs
            .join(&geom_full_path)
            .context("VFS path error")?
            .open_file()
            .context_with(|| format!("Could not open turret geometry: {geom_full_path}"))?
            .read_to_end(&mut geom_bytes)?;

        let model_short_name = model_path
            .rsplit('/')
            .next()
            .unwrap_or(model_path)
            .strip_suffix(".model")
            .unwrap_or(model_path);

        Ok(OwnedSubModel {
            name: model_short_name.to_string(),
            visual: vp,
            geom_bytes,
            splash_bytes: None,
        })
    }
}

// ---------------------------------------------------------------------------
// ShipModelContext — fully-loaded ship, ready for export
// ---------------------------------------------------------------------------

/// A fully-loaded ship model. Owns all bytes and parsed visuals.
///
/// Created via [`ShipAssets::load_ship()`]. Call [`export_glb()`](Self::export_glb)
/// to write the model to a file or buffer.
pub struct ShipModelContext {
    vfs: VfsPath,
    assets_bin_bytes: Vec<u8>,
    hull_parts: Vec<OwnedSubModel>,
    hull_armor_geometry: Vec<Vec<u8>>,
    root_splash_bytes: Option<Vec<u8>>,
    turret_models: Vec<OwnedSubModel>,
    mounts: Vec<ResolvedMount>,
    info: ShipInfo,
    options: ShipExportOptions,
    mat_camo_schemes: Vec<MatCamoScheme>,
    /// Armor thickness map from GameParams.  See [`ArmorMap`].
    armor_map: Option<ArmorMap>,
    /// Hit location zones from GameParams, keyed by zone name (e.g. "Citadel").
    hit_locations: Option<HashMap<String, crate::game_params::types::HitLocation>>,
}

/// Resolve a visual suffix to VisualPrototype record data.
///
/// If the suffix resolves to blob 1 (VisualPrototype), returns the data directly.
/// If it resolves to blob 3 (ModelPrototype), parses the ModelPrototype and follows
/// its `visual_resource_id` to look up the actual VisualPrototype.
fn model_point_stem(name: &str) -> Option<&str> {
    let stem = name.strip_prefix("MP_")?;
    let stem = stem.split_once("_INDEX_").map(|(base,_)|base).unwrap_or(stem);
    Some(stem.rsplit_once('.').filter(|(_,suffix)| !suffix.is_empty() && suffix.bytes().all(|b| b.is_ascii_digit())).map(|(base,_)|base).unwrap_or(stem))
}

fn collect_part_model_points(
    db: &PrototypeDatabase<'_>, index: &HashMap<u64,usize>, paths: &HashMap<String,String>, part: &OwnedSubModel,
) -> Vec<(String,[f32;16])> {
    let mut result = Vec::new();
    let mut seen = HashSet::new();
    for &id in &part.visual.nodes.name_map_name_ids {
        let Some(name) = db.strings.get_string_by_id(id) else { continue; };
        if name.starts_with("MP_") && let Some(matrix) = part.visual.find_hardpoint_transform(name,&db.strings) {
            seen.insert(name.to_string());
            result.push((name.to_string(),matrix));
        }
    }
    let extension_nodes = (|| -> Result<Vec<skeleton_ext::ExtensionNode>, Report> {
        let Some(path) = paths.get(&part.name) else { return Ok(Vec::new()); };
        let (loc,_) = db.resolve_path(path,index).context("Missing model for skeleton extensions")?;
        let blob = &db.databases[loc.blob_index];
        // Split-layout Ships 2.0 models use their own visual node hierarchy.
        if blob.prototype_magic != 0xA957_6F28 || blob.prototype_checksum != 0x184C_73FC { return Ok(Vec::new()); }
        let data = db.get_prototype_data(loc,model::MODEL_ITEM_SIZE)?;
        let references = model::parse_model_skeleton_refs(data)?;
        let mut nodes = Vec::new();
        for resource in references {
            let Some(mapping) = db.lookup_r2p(resource) else { continue; };
            let location = db.decode_r2p_value(mapping)?;
            let blob = &db.databases[location.blob_index];
            if blob.prototype_magic != skeleton_ext::MAGIC || blob.prototype_checksum != skeleton_ext::CHECKSUM {
                eprintln!("Warning: unsupported skeleton extension for {}",part.name); continue;
            }
            let data = db.get_prototype_data(location,skeleton_ext::ITEM_SIZE)?;
            let parsed = skeleton_ext::parse_extension(data).map_err(|reason| rootcause::report!("{reason}"))?;
            nodes.extend(parsed);
        }
        Ok(nodes)
    })();
    let nodes = match extension_nodes {
        Ok(nodes) => nodes,
        Err(error) => { eprintln!("Warning: skeleton extensions for {}: {error:?}",part.name); return result; }
    };
    for (i,node) in nodes.iter().enumerate() {
        let Some(name) = db.strings.get_string_by_id(node.name_id) else { continue; };
        if !name.starts_with("MP_") || seen.contains(name) { continue; }
        let matrix = skeleton_ext::resolve_world_transform(i,&nodes,|id| {
            let parent = db.strings.get_string_by_id(id)?;
            part.visual.find_hardpoint_transform(parent,&db.strings)
        });
        if let Some(matrix) = matrix {
            seen.insert(name.to_string()); result.push((name.to_string(),matrix));
        } else { eprintln!("Warning: unresolved skeleton parent for {}/{name}",part.name); }
    }
    result
}

#[cfg(test)]
mod model_point_stem_tests {
    use super::model_point_stem;
    #[test] fn resolves_authored_instance_names_without_changing_effect_nodes() {
        assert_eq!(model_point_stem("MP_ZM001_Searchlight.001"),Some("ZM001_Searchlight"));
        assert_eq!(model_point_stem("MP_FM701_Ammunition_Box_INDEX_1"),Some("FM701_Ammunition_Box"));
        assert_eq!(model_point_stem("MP_Resource.variant"),Some("Resource.variant"));
        assert_eq!(model_point_stem("EP_Fire_1"),None);
    }
}

fn resolve_visual_prototype(
    db: &PrototypeDatabase<'_>,
    visual_suffix: &str,
    self_id_index: &HashMap<u64, usize>,
) -> Result<visual::VisualPrototype, Report> {
    const VISUAL_MAGIC: u32 = 0x480D_C57B;
    const MODEL_MAGIC: u32 = 0xA957_6F28;

    fn parse_location(
        db: &PrototypeDatabase<'_>,
        location: crate::models::assets_bin::PrototypeLocation,
        full_path: &str,
        self_id_index: &HashMap<u64, usize>,
    ) -> Result<visual::VisualPrototype, Report> {
        const VISUAL_MAGIC: u32 = 0x480D_C57B;
        const VISUAL_V2_CHECKSUM: u32 = 0xA012_5AEA;
        const VISUAL_NODES_V2_MAGIC: u32 = 0xD9BB_9F4A;
        let entry = &db.databases[location.blob_index];
        if entry.prototype_magic != VISUAL_MAGIC {
            bail!(
                "'{}' resolved to blob {} with non-visual magic 0x{:08X}",
                full_path,
                location.blob_index,
                entry.prototype_magic
            );
        }

        if entry.prototype_checksum == VISUAL_V2_CHECKSUM {
            let visual_data = db
                .get_prototype_data(location, visual::VISUAL_V2_ITEM_SIZE)
                .context("Failed to get split visual prototype data")?;
            let shadow_path = format!("@{}", full_path.trim_start_matches('@'));
            let (nodes_location, resolved_shadow_path) = db
                .resolve_path(&shadow_path, self_id_index)
                .context_with(|| format!("Could not resolve split visual nodes: {shadow_path}"))?;
            let nodes_entry = &db.databases[nodes_location.blob_index];
            if nodes_entry.prototype_magic != VISUAL_NODES_V2_MAGIC {
                bail!(
                    "Split visual nodes '{}' resolved to magic 0x{:08X}",
                    resolved_shadow_path,
                    nodes_entry.prototype_magic
                );
            }
            let nodes_data = db
                .get_prototype_data(nodes_location, visual::VISUAL_V2_NODES_ITEM_SIZE)
                .context("Failed to get split visual node data")?;
            return Ok(visual::parse_visual_v2(visual_data, nodes_data)
                .context_with(|| format!("Failed to parse split visual {full_path}"))?);
        }

        let data = db
            .get_prototype_data(location, visual::VISUAL_ITEM_SIZE)
            .context("Failed to get visual prototype data")?;
        Ok(visual::parse_visual(data)
            .context_with(|| format!("Failed to parse VisualPrototype {full_path}"))?)
    }

    // Split-layout clients contain both `content/...visual` and
    // `@content/...visual` entries with the same leaf name. Prefer the mapped
    // non-shadow visual explicitly instead of relying on path insertion order.
    let suffix_leaf = visual_suffix.rsplit('/').next().unwrap_or(visual_suffix);
    let preferred = db
        .paths_storage
        .iter()
        .enumerate()
        .filter(|(_, entry)| entry.name.ends_with(suffix_leaf))
        .filter_map(|(index, entry)| {
            let path = db.reconstruct_path(index, self_id_index);
            if path.starts_with('@') || !path.ends_with(visual_suffix) {
                return None;
            }
            let value = db.lookup_r2p(entry.self_id)?;
            let location = db.decode_r2p_value(value).ok()?;
            let magic = db.databases[location.blob_index].prototype_magic;
            (magic == VISUAL_MAGIC).then_some((location, path))
        })
        .next();
    let (location, full_path) = match preferred {
        Some(found) => found,
        None => db
            .resolve_path(visual_suffix, self_id_index)
            .context_with(|| format!("Could not resolve visual: {visual_suffix}"))?,
    };
    let prototype_magic = db.databases[location.blob_index].prototype_magic;

    match prototype_magic {
        VISUAL_MAGIC => parse_location(db, location, &full_path, self_id_index),
        MODEL_MAGIC => {
            let is_v2 = db.databases[location.blob_index].prototype_checksum == model::MODEL_V2_CHECKSUM;
            let model_data = db
                .get_prototype_data(location, if is_v2 { model::MODEL_V2_ITEM_SIZE } else { model::MODEL_ITEM_SIZE })
                .context("Failed to get model prototype data")?;
            let visual_resource_id = if is_v2 {
                model::parse_model_v2_visual_id(model_data)?
            } else {
                model::parse_model(model_data)
                    .context_with(|| format!("Failed to parse ModelPrototype for {visual_suffix}"))?.visual_resource_id
            };
            if visual_resource_id == 0 {
                bail!(
                    "ModelPrototype for '{}' has null visualResourceId",
                    visual_suffix
                );
            }
            let r2p_value = db.lookup_r2p(visual_resource_id).ok_or_else(|| {
                rootcause::report!(
                    "visualResourceId 0x{:016X} from ModelPrototype '{}' not found in r2p map",
                    visual_resource_id,
                    visual_suffix
                )
            })?;
            let visual_location = db
                .decode_r2p_value(r2p_value)
                .context("Failed to decode visual r2p value")?;
            let visual_path_index = self_id_index.get(&visual_resource_id).ok_or_else(|| {
                rootcause::report!(
                    "visualResourceId 0x{:016X} has no path entry",
                    visual_resource_id
                )
            })?;
            let visual_path = db.reconstruct_path(*visual_path_index, self_id_index);
            parse_location(db, visual_location, &visual_path, self_id_index)
        }
        other => bail!(
            "'{}' resolved to blob {} with unsupported prototype magic 0x{:08X}",
            visual_suffix,
            location.blob_index,
            other
        ),
    }
}
impl ShipModelContext {
    /// Ship identity information.
    pub fn info(&self) -> &ShipInfo {
        &self.info
    }

    /// Hull part names (sub-model names).
    pub fn hull_part_names(&self) -> Vec<&str> {
        self.hull_parts.iter().map(|p| p.name.as_str()).collect()
    }

    /// Number of mounted components (turrets, AA, etc.).
    pub fn mount_count(&self) -> usize {
        self.mounts.len()
    }

    /// Number of unique turret/mount 3D models.
    pub fn unique_turret_count(&self) -> usize {
        self.turret_models.len()
    }

    /// Armor thickness map from GameParams.  See [`ArmorMap`].
    pub fn armor_map(&self) -> Option<&ArmorMap> {
        self.armor_map.as_ref()
    }

    /// Raw geometry bytes for hull parts, for inspection.
    pub fn hull_geom_bytes(&self) -> Vec<&[u8]> {
        self.hull_parts
            .iter()
            .map(|p| p.geom_bytes.as_slice())
            .collect()
    }

    /// Hull armor sources, including non-drawable Ships 2.0 roots.
    /// Do not use these as editable mesh parts.
    pub fn hull_armor_sources(&self) -> Vec<(&str, &[u8])> {
        self.hull_parts.iter().map(|part| (part.name.as_str(), part.geom_bytes.as_slice()))
            .chain(self.hull_armor_geometry.iter().map(|bytes| ("Unified hull armor", bytes.as_slice())))
            .collect()
    }

    /// Raw geometry bytes for unique turret/mount models.
    pub fn turret_geom_bytes(&self) -> Vec<&[u8]> {
        self.turret_models
            .iter()
            .map(|p| p.geom_bytes.as_slice())
            .collect()
    }

    /// Names of unique turret/mount models.
    pub fn turret_model_names(&self) -> Vec<&str> {
        self.turret_models.iter().map(|p| p.name.as_str()).collect()
    }

    /// Number of LOD levels available for hull meshes.
    pub fn hull_lod_count(&self) -> usize {
        self.hull_parts
            .iter()
            .map(|p| p.visual.lods.len())
            .max()
            .unwrap_or(1)
    }

    /// Hit location zones from GameParams (e.g. "Citadel" → HitLocation).
    pub fn hit_locations(
        &self,
    ) -> Option<&HashMap<String, crate::game_params::types::HitLocation>> {
        self.hit_locations.as_ref()
    }

    /// Raw splash file bytes for hull parts (if available).
    pub fn hull_splash_bytes(&self) -> Option<&[u8]> {
        self.root_splash_bytes.as_deref().or_else(|| self.hull_parts
            .iter()
            .find_map(|p| p.splash_bytes.as_deref()))
    }

    /// Build interactive armor meshes with per-triangle metadata.
    ///
    /// Returns one [`InteractiveArmorMesh`] per armor model found in the hull
    /// geometry. Each mesh contains the renderable triangle soup plus
    /// [`ArmorTriangleInfo`](gltf_export::ArmorTriangleInfo) entries aligned
    /// 1:1 with triangles, so a viewer can look up material name, thickness,
    /// and zone on hover/click.
    pub fn interactive_armor_meshes(&self) -> Result<Vec<InteractiveArmorMesh>, Report> {
        let mut result = Vec::new();
        for bytes in &self.hull_armor_geometry {
            let geom = geometry::parse_geometry(bytes).context("Failed to parse root armor")?;
            for model in &geom.armor_models {
                result.push(InteractiveArmorMesh::from_armor_model(model, self.armor_map.as_ref(), None));
            }
        }

        // Hull armor (already in world space).
        for part in &self.hull_parts {
            let geom = geometry::parse_geometry(&part.geom_bytes)
                .context("Failed to parse hull geometry for interactive armor")?;
            for armor_model in &geom.armor_models {
                result.push(InteractiveArmorMesh::from_armor_model(
                    armor_model,
                    self.armor_map.as_ref(),
                    None,
                ));
            }
        }

        // A legacy collision-material name is not always the physical zone.
        // Correct upper-deck InclinCit faces only when authored hit boxes prove
        // they are above the citadel and inside the casemate.
        if let (Some(raw), Some(locations)) = (self.hull_splash_bytes(), &self.hit_locations)
            && let Ok(boxes) = geometry::parse_splash_file(raw)
        {
            let cit_names: Vec<_> = locations.values().filter(|v| v.hl_type() == "citadel_hitlocation")
                .flat_map(|v| v.splash_boxes()).collect();
            let cas_names: Vec<_> = locations.iter().filter(|(k,_)| k.as_str() == "Cas")
                .flat_map(|(_,v)| v.splash_boxes()).collect();
            let cit: Vec<_> = boxes.iter().filter(|b| cit_names.iter().any(|n| n.as_str()==b.name)).collect();
            let cas: Vec<_> = boxes.iter().filter(|b| cas_names.iter().any(|n| n.as_str()==b.name)).collect();
            for mesh in &mut result { correct_upper_citadel_labels(mesh, &cit, &cas); }
        }

        // Turret armor: instance per mount.
        let turret_geoms: Vec<_> = self
            .turret_models
            .iter()
            .map(|part| {
                geometry::parse_geometry(&part.geom_bytes)
                    .context("Failed to parse turret geometry for interactive armor")
            })
            .collect::<Result<_, _>>()?;

        for mount in &self.mounts {
            let geom = &turret_geoms[mount.turret_model_index];
            for armor_model in &geom.armor_models {
                let mut mesh = InteractiveArmorMesh::from_armor_model(
                    armor_model,
                    self.armor_map.as_ref(),
                    mount.mount_armor.as_ref(),
                );
                mesh.transform = mount.armor_transform.map(gltf_export::negate_z_transform);
                mesh.name = format!("{} [{}]", mesh.name, mount.hp_name);
                result.push(mesh);
            }
        }

        Ok(result)
    }
    /// Collect hull visual meshes for interactive display.
    ///
    /// Returns one [`InteractiveHullMesh`](gltf_export::InteractiveHullMesh) per
    /// render set (hull parts + mounted turrets). LOD 0 is used.
    /// Base albedo textures are baked into per-vertex colors when available.
    pub fn interactive_hull_meshes(&self) -> Result<Vec<gltf_export::InteractiveHullMesh>, Report> {
        let db = assets_bin::parse_assets_bin(&self.assets_bin_bytes)
            .context("Failed to parse assets.bin for hull meshes")?;

        let lod = self.options.lod;
        let damaged = self.options.damaged;
        let mut result = Vec::new();

        // Hull parts (no transform, already in world space).
        for part in &self.hull_parts {
            let geom = geometry::parse_geometry(&part.geom_bytes)
                .context("Failed to parse hull geometry for hull meshes")?;
            let meshes =
                gltf_export::collect_hull_meshes(&part.visual, &geom, &db, lod, damaged, None)?;
            result.extend(meshes);
        }

        // Mounted turrets (with mount transforms).
        for mount in &self.mounts {
            let part = &self.turret_models[mount.turret_model_index];
            let geom = geometry::parse_geometry(&part.geom_bytes)
                .context("Failed to parse turret geometry for hull meshes")?;
            let mut meshes = gltf_export::collect_hull_meshes(
                &part.visual,
                &geom,
                &db,
                lod,
                damaged,
                mount.barrel_pitch.as_ref(),
            )?;
            for mesh in &mut meshes {
                mesh.transform = mount.transform.map(gltf_export::negate_z_transform);
                mesh.name = format!("{} [{}]", mesh.name, mount.hp_name);
            }
            result.extend(meshes);
        }

        // Bake base albedo textures into per-vertex colors.
        // Cache decoded images by MFM path to avoid re-loading the same texture.
        let mut texture_cache: HashMap<String, Option<image_dds::image::RgbaImage>> =
            HashMap::new();

        for mesh in &mut result {
            let mfm_path = match &mesh.mfm_path {
                Some(p) => p.clone(),
                None => continue,
            };
            if mesh.uvs.len() != mesh.positions.len() {
                continue;
            }

            let image = texture_cache.entry(mfm_path.clone()).or_insert_with(|| {
                let png_bytes = texture::load_base_albedo_png(&self.vfs, &mfm_path)?;
                image_dds::image::load_from_memory(&png_bytes)
                    .ok()
                    .map(|image| image.to_rgba8())
            });

            if let Some(img) = image {
                let width = img.width();
                let height = img.height();
                if width == 0 || height == 0 {
                    continue;
                }

                let mut colors = Vec::with_capacity(mesh.uvs.len());
                for uv in &mesh.uvs {
                    // Wrap UVs into [0, 1) range and sample the image.
                    let u = uv[0].rem_euclid(1.0);
                    let v = uv[1].rem_euclid(1.0);
                    let x = ((u * width as f32) as u32).min(width - 1);
                    let y = ((v * height as f32) as u32).min(height - 1);
                    let pixel = img.get_pixel(x, y);
                    colors.push([
                        pixel[0] as f32 / 255.0,
                        pixel[1] as f32 / 255.0,
                        pixel[2] as f32 / 255.0,
                        1.0, // alpha will be set by the viewer
                    ]);
                }
                mesh.colors = colors;
            }
        }

        Ok(result)
    }

    /// Export the loaded ship model to GLB format.
    pub fn export_glb(&self, writer: &mut impl Write) -> Result<(), Report> {
        let db = assets_bin::parse_assets_bin(&self.assets_bin_bytes)
            .context("Failed to re-parse assets.bin")?;

        // Parse geometries (scoped borrows — no self-referential issue).
        let hull_geoms: Vec<geometry::MergedGeometry<'_>> = self
            .hull_parts
            .iter()
            .map(|part| {
                geometry::parse_geometry(&part.geom_bytes)
                    .context(format!("Failed to parse hull geometry: {}", part.name))
            })
            .collect::<Result<_, _>>()?;

        let turret_geoms: Vec<geometry::MergedGeometry<'_>> = self
            .turret_models
            .iter()
            .map(|part| {
                geometry::parse_geometry(&part.geom_bytes)
                    .context(format!("Failed to parse turret geometry: {}", part.name))
            })
            .collect::<Result<_, _>>()?;

        // Build SubModel list.
        let mut sub_models: Vec<SubModel<'_>> = Vec::new();

        // Hull sub-models.
        for (data, geom) in self.hull_parts.iter().zip(hull_geoms.iter()) {
            sub_models.push(SubModel {
                name: data.name.clone(),
                visual: &data.visual,
                geometry: geom,
                transform: None,
                group: "Hull",
                barrel_pitch: None,
            });
        }

        // Mounted components.
        for mount in &self.mounts {
            let turret_data = &self.turret_models[mount.turret_model_index];
            let turret_geom = &turret_geoms[mount.turret_model_index];

            sub_models.push(SubModel {
                name: format!("{} ({})", mount.hp_name, turret_data.name),
                visual: &turret_data.visual,
                geometry: turret_geom,
                transform: mount.transform,
                group: mount_group(mount.species),
                barrel_pitch: mount.barrel_pitch.clone(),
            });
        }

        // Load textures.
        let texture_set = if self.options.textures {
            let mut all_mfm_infos = Vec::new();
            for sub in &sub_models {
                all_mfm_infos.extend(collect_mfm_info(sub.visual, &db));
            }
            let mut tex_set = build_texture_set(&all_mfm_infos, &self.vfs);
            if !self.options.include_camo_variants && self.options.camouflage.is_none() {
                tex_set.camo_schemes.clear();
                tex_set.tiled_uv_transforms.clear();
            }
            let per_ship_count = tex_set.camo_schemes.len();

            // Merge material-based camo textures (mat_Steel, mat_Yamato_KoF, etc.).
            if (self.options.include_camo_variants || self.options.camouflage.is_some())
                && !self.mat_camo_schemes.is_empty()
            {
                let stems: Vec<String> = {
                    let mut s = HashSet::new();
                    for info in &all_mfm_infos {
                        s.insert(info.stem.clone());
                    }
                    s.into_iter().collect()
                };

                for scheme in &self.mat_camo_schemes {
                    let palette_required = scheme.tiled || scheme.use_color_scheme;
                    let selected_color = if palette_required {
                        self.options
                            .camouflage_color_scheme
                            .as_deref()
                            .filter(|_| {
                                self.options.camouflage.as_deref()
                                    == Some(scheme.display_name.as_str())
                            })
                            .and_then(|selected| {
                                scheme
                                    .color_schemes
                                    .iter()
                                    .find(|(name, _)| name == selected)
                            })
                            .or_else(|| scheme.color_schemes.first())
                    } else {
                        None
                    };
                    if palette_required && selected_color.is_none() {
                        eprintln!(
                            "  Warning: camouflage {} requires a color scheme but none was found",
                            scheme.display_name
                        );
                        continue;
                    }

                    let mut png_by_path: HashMap<String, Vec<u8>> = HashMap::new();
                    let mut scheme_textures = HashMap::new();
                    for stem in &stems {
                        let path = camouflage_texture_path_for_stem(scheme, stem);
                        let Some(path) = path else {
                            continue;
                        };

                        let png = if let Some(cached) = png_by_path.get(path) {
                            cached.clone()
                        } else {
                            let Some(dds) = texture::load_authored_texture_dds(&self.vfs, path) else {
                                continue;
                            };
                            let converted = if let Some((_, colors)) = selected_color {
                                texture::bake_tiled_camo_png(&dds, colors)
                            } else {
                                texture::dds_to_png(&dds)
                            };
                            match converted {
                                Ok(png) => {
                                    png_by_path.insert(path.clone(), png.clone());
                                    png
                                }
                                Err(error) => {
                                    eprintln!(
                                        "  Warning: failed to decode camouflage texture {}: {error}",
                                        path
                                    );
                                    continue;
                                }
                            }
                        };
                        scheme_textures.insert(stem.clone(), png);
                    }

                    if scheme_textures.is_empty() {
                        continue;
                    }
                    let scheme_stems: Vec<String> = scheme_textures.keys().cloned().collect();
                    let scheme_idx = tex_set.camo_schemes.len();
                    tex_set
                        .camo_schemes
                        .push((scheme.display_name.clone(), scheme_textures));
                    if scheme.tiled {
                        // Tile is the shared UV setting used by Korabli. Even
                        // with no explicit scale, these are paint masks and must
                        // be composited over the original material albedo.
                        for stem in &scheme_stems {
                            tex_set.tiled_uv_transforms.insert(
                                (scheme_idx, stem.clone()),
                                camouflage_uv_transform_for_stem(scheme, stem),
                            );
                        }
                    } else if scheme.use_color_scheme {
                        // Ship-specific paint masks use the material's normal UVs.
                        // Mark them with an identity transform so promotion blends
                        // the paint over the original albedo instead of replacing it.
                        for stem in &scheme_stems {
                            tex_set
                                .tiled_uv_transforms
                                .insert((scheme_idx, stem.clone()), [1.0, 1.0, 0.0, 0.0]);
                        }
                    }
                }

                let mat_count = tex_set.camo_schemes.len() - per_ship_count;
                eprintln!(
                    "  Texture variants: {} per-ship, {} material-based",
                    per_ship_count, mat_count
                );
            }

            if let Some(selection) = self.options.camouflage.as_deref() {
                promote_camouflage_to_base(&mut tex_set, &all_mfm_infos, selection)?;
            }
            tex_set
        } else {
            TextureSet::empty()
        };

        // Collect armor meshes from hull AND turret geometries with thickness data.
        let armor_map = self.armor_map.as_ref();
        let mut armor_meshes: Vec<gltf_export::ArmorSubModel> = Vec::new();
        for bytes in &self.hull_armor_geometry {
            let geom = geometry::parse_geometry(bytes).context("Failed to parse root armor")?;
            for model in &geom.armor_models {
                armor_meshes.extend(gltf_export::armor_sub_models_by_zone(model, armor_map, None));
            }
        }
        // Hull armor (already in world space, no transform needed).
        for geom in &hull_geoms {
            for am in &geom.armor_models {
                armor_meshes.extend(gltf_export::armor_sub_models_by_zone(am, armor_map, None));
            }
        }

        // Turret armor: instance per mount with that mount's transform.
        for mount in &self.mounts {
            let turret_geom = &turret_geoms[mount.turret_model_index];
            for am in &turret_geom.armor_models {
                let mut subs = gltf_export::armor_sub_models_by_zone(
                    am,
                    armor_map,
                    mount.mount_armor.as_ref(),
                );
                for s in &mut subs {
                    s.transform = mount.armor_transform;
                    s.name = format!("{} [{}]", s.name, mount.hp_name);
                }
                armor_meshes.extend(subs);
            }
        }

        if !self.options.include_armor {
            armor_meshes.clear();
        }

        gltf_export::export_ship_glb(
            &sub_models,
            &armor_meshes,
            &db,
            self.options.lod,
            &texture_set,
            self.options.damaged,
            writer,
        )
        .context("Failed to export ship GLB")?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

/// Owns visual + geometry bytes for one sub-model (no lifetime parameters).
struct OwnedSubModel {
    name: String,
    visual: VisualPrototype,
    geom_bytes: Vec<u8>,
    /// Raw `.splash` file bytes (only present for base hull models).
    splash_bytes: Option<Vec<u8>>,
}

/// Result of [`ShipAssets::load_mounts`].
struct LoadedMounts {
    turret_models: Vec<OwnedSubModel>,
    #[allow(dead_code)]
    turret_model_index: HashMap<String, usize>,
    mounts: Vec<ResolvedMount>,
}

/// A mount instance with resolved transform.
struct ResolvedMount {
    hp_name: String,
    turret_model_index: usize,
    /// Visual transform with yaw correction.
    transform: Option<[f32; 16]>,
    /// Raw hardpoint transform without model rotation (for armor geometry).
    armor_transform: Option<[f32; 16]>,
    /// Per-mount armor map for turret shell surfaces (from `A_Artillery.HP_XXX.armor`).
    mount_armor: Option<crate::game_params::types::ArmorMap>,
    /// Mount species from GameParams `typeinfo.species`.
    species: Option<crate::game_params::types::MountSpecies>,
    /// Per-vertex barrel pitch configuration (if pitchDeadZones applies).
    barrel_pitch: Option<super::gltf_export::BarrelPitch>,
}

/// Pre-resolved material-based camouflage scheme (owned data, no lifetimes).
struct MatCamoScheme {
    /// Display name for the variant (translated or fallback).
    display_name: String,
    /// Stable GameParams Exterior identifier (for example PCEM017_Steel_10lvl).
    param_name: String,
    /// Camouflage entry identifier referenced by the Exterior record.
    camouflage_name: String,
    /// Albedo texture VFS paths from camouflages.xml.
    texture_paths: Vec<String>,
    /// Albedo texture VFS paths keyed by lowercase part category.
    texture_paths_by_category: HashMap<String, String>,
    /// Whether this is a tiled camo (uses UV tiling via KHR_texture_transform).
    tiled: bool,
    /// Whether non-tiled texture masks also need palette replacement.
    use_color_scheme: bool,
    /// Ordered palette IDs and colors.
    color_schemes: Vec<(String, [[f32; 4]; 4])>,
    /// Per-part UV transforms for tiled camos. Key = part category (lowercase).
    uv_transforms: HashMap<String, camouflage::UvTransform>,
}

/// Resolve the authored paint texture for one material stem.
///
/// Tiled camouflage uses a shared tile as a deliberate fallback. Ship-specific
/// palette masks do not: a Hull mask must never be painted across deckhouses,
/// guns, or other equipment that has no corresponding XML texture entry.
fn camouflage_texture_path_for_stem<'a>(
    scheme: &'a MatCamoScheme,
    stem: &str,
) -> Option<&'a String> {
    let category = camouflage::classify_part_category(stem);
    if scheme.tiled || !scheme.use_color_scheme {
        return scheme
            .texture_paths_by_category
            .get(category)
            .or_else(|| scheme.texture_paths_by_category.get("tile"))
            .or_else(|| scheme.texture_paths_by_category.get("misc"))
            .or_else(|| scheme.texture_paths.first());
    }

    if category != "tile" {
        return scheme.texture_paths_by_category.get(category);
    }

    let lower = stem.to_ascii_lowercase();
    if lower.ends_with("_hull")
        || lower.ends_with("_hull_wire")
        || lower.contains("_hull_")
        || is_bare_ship_hull_stem(stem)
    {
        scheme
            .texture_paths_by_category
            .get("hull")
            .or_else(|| scheme.texture_paths_by_category.get("tile"))
    } else {
        None
    }
}
// Legacy hull albedos can be named HSB305_Eenhoorn_1950 without a Hull suffix.
// Restrict this fallback to a ship resource prefix and an authored year; equipment
// and named DeckHouse/Bulge/other parts must not receive the hull-only mask.
fn is_bare_ship_hull_stem(stem: &str) -> bool {
    let Some((prefix, rest)) = stem.split_once('_') else { return false; };
    let bytes = prefix.as_bytes();
    let year = rest.rsplit('_').next().unwrap_or("");
    bytes.len() == 6 && bytes[0].is_ascii_alphabetic()
        && bytes[1].eq_ignore_ascii_case(&b'S')
        && matches!(bytes[2].to_ascii_uppercase(), b'B' | b'C' | b'D' | b'A' | b'S')
        && bytes[3..].iter().all(u8::is_ascii_digit)
        && year.len() == 4 && year.bytes().all(|b| b.is_ascii_digit())
}

fn camouflage_uv_transform_for_stem(scheme: &MatCamoScheme, stem: &str) -> [f32;4] {
    let cat = camouflage::classify_part_category(stem);
    scheme.uv_transforms.get(cat).or_else(|| scheme.uv_transforms.get("tile"))
        .map(|t| [t.scale[0], t.scale[1], t.offset[0], t.offset[1]])
        .unwrap_or([1.0,1.0,0.0,0.0])
}
#[cfg(test)]
mod camouflage_texture_path_tests {
    use super::{camouflage_texture_path_for_stem, camouflage_uv_transform_for_stem, MatCamoScheme};
    use std::collections::HashMap;

    fn scheme(tiled: bool, use_color_scheme: bool, category: &str) -> MatCamoScheme {
        MatCamoScheme {
            display_name: "test".to_string(),
            param_name: "test_param".to_string(),
            camouflage_name: "test_camo".to_string(),
            texture_paths: vec!["paint.dds".to_string()],
            texture_paths_by_category: HashMap::from([(
                category.to_string(),
                "paint.dds".to_string(),
            )]),
            tiled,
            use_color_scheme,
            color_schemes: Vec::new(),
            uv_transforms: HashMap::new(),
        }
    }

    #[test]
    fn shared_tile_and_missing_uv_still_composite_original_albedo() {
        let mut s = scheme(true, true, "tile");
        assert_eq!(camouflage_uv_transform_for_stem(&s, "FGM001_Turret"), [1.0,1.0,0.0,0.0]);
        s.uv_transforms.insert("tile".into(), crate::export::camouflage::UvTransform {scale:[2.0,3.0], offset:[0.1,0.2]});
        assert_eq!(camouflage_uv_transform_for_stem(&s, "FGM001_Turret"), [2.0,3.0,0.1,0.2]);
    }
    #[test]
    fn ship_specific_hull_mask_does_not_bleed_into_other_parts() {
        let scheme = scheme(false, true, "hull");
        assert!(camouflage_texture_path_for_stem(
            &scheme,
            "GSB301_Bismarck_41_1941_Hull"
        )
        .is_some());
        assert!(camouflage_texture_path_for_stem(
            &scheme,
            "GSB301_Bismarck_41_1941_DeckHouse"
        )
        .is_none());
        assert!(camouflage_texture_path_for_stem(&scheme, "GGM123_Turret").is_none());
        assert!(camouflage_texture_path_for_stem(&scheme, "UnknownEquipment").is_none());
    }

    #[test]
    fn legacy_bare_hull_receives_mask_without_painting_other_parts() {
        let s = scheme(false, true, "hull");
        assert!(camouflage_texture_path_for_stem(&s, "HSB305_Eenhoorn_1950").is_some());
        assert!(camouflage_texture_path_for_stem(&s, "HSB302_Grote_Geus_1945").is_some());
        for part in ["HSB305_Eenhoorn_1950_DeckHouse", "HSB305_Eenhoorn_1950_Bulge", "HGM009_283mm54_RF", "UnknownEquipment"] {
            assert!(camouflage_texture_path_for_stem(&s, part).is_none(), "{part}");
        }
    }

    #[test]
    fn tiled_camouflage_keeps_the_shared_tile_fallback() {
        let scheme = scheme(true, true, "tile");
        assert!(camouflage_texture_path_for_stem(&scheme, "GGM123_Turret").is_some());
        assert!(camouflage_texture_path_for_stem(&scheme, "UnknownEquipment").is_some());
    }
}
// ---------------------------------------------------------------------------
// Shared helpers (pub so main.rs export-model can use them too)
// ---------------------------------------------------------------------------

/// Resolved MFM info: stem (leaf name without `.mfm`) and full VFS path.
pub struct MfmInfo {
    pub stem: String,
    pub full_path: String,
    pub authored_albedo: Option<String>,
    pub authored_textures: HashMap<String, String>,
}

/// Collect MFM stems and full paths from a visual's render sets.
pub fn collect_mfm_info(visual: &VisualPrototype, db: &PrototypeDatabase<'_>) -> Vec<MfmInfo> {
    let self_id_index = db.build_self_id_index();
    let mut result = Vec::new();
    let mut seen = HashSet::new();

    for rs in &visual.render_sets {
        if rs.material_mfm_path_id == 0 {
            continue;
        }
        let Some(&path_idx) = self_id_index.get(&rs.material_mfm_path_id) else {
            continue;
        };
        let mfm_name = &db.paths_storage[path_idx].name;
        let full_path = db.reconstruct_path(path_idx, &self_id_index);
        let stem = mfm_name.strip_suffix(".mfm").unwrap_or(mfm_name);

        if seen.insert(full_path.clone()) {
            let authored_textures = (|| {
                let location = db.decode_r2p_value(db.lookup_r2p(rs.material_mfm_path_id)?).ok()?;
                let blob = &db.databases[location.blob_index];
                if blob.prototype_magic != 0x5069_C471 { return None; }
                let stride = if blob.prototype_checksum == 0x755D_585C { 0x88 } else { 0x78 };
                let record = blob.data.get(16 + location.record_index * stride..)?;
                let mut bindings = HashMap::new();
                for (name, path) in texture::material_texture_ids(record)? {
                    if let (Some(name), Some(&index)) = (db.strings.get_string_by_id(name), self_id_index.get(&path)) {
                        bindings.insert(name.to_string(), db.reconstruct_path(index, &self_id_index));
                    }
                }
                Some(bindings)
            })().unwrap_or_default();
            let authored_albedo = authored_textures.get("diffuseMap")
                .or_else(|| authored_textures.get("g_albedoMap")).cloned();
            result.push(MfmInfo {
                stem: stem.to_string(),
                full_path,
                authored_albedo,
                authored_textures,
            });
        }
    }

    result
}

/// Build a `TextureSet` from MFM infos: base albedo + all camo schemes.
pub fn build_texture_set(mfm_infos: &[MfmInfo], vfs: &VfsPath) -> TextureSet {
    let mut base = HashMap::new();

    let mut seen_paths = HashSet::new();
    let mut unique_infos: Vec<&MfmInfo> = Vec::new();
    for info in mfm_infos {
        if seen_paths.insert(info.full_path.clone()) {
            unique_infos.push(info);
        }
    }

    // Load base albedo textures.
    for info in &unique_infos {
        if info.authored_albedo.is_none()
            && info.authored_textures.contains_key("g_normalMap")
            && (info.stem.ends_with("_alpha_normal") || info.stem.ends_with("_alpha_normal_skinned"))
        {
            // No colour texture exists for this deferred normal-only pass.
            // The material exporter reports its portability limitation.
            continue;
        }
        // Indexed materials need shader baking before their tiny fallback
        // diffuse mip; ordinary/decal materials use authored references first.
        let indexed_png = texture::bake_indexed_ship_bound_png(vfs, &info.full_path, &info.authored_textures)
            .or_else(|| texture::bake_indexed_ship_albedo_png(vfs, &info.full_path));
        if indexed_png.is_none() && info.authored_textures.contains_key("materialIdMap") {
            eprintln!("  Warning: indexed material bake failed for {}; using diffuse fallback, appearance needs review", info.full_path);
        }
        let png = indexed_png
            .or_else(|| info.authored_albedo.as_deref().and_then(|path| texture::load_authored_texture_png(vfs, path)))
            .or_else(|| texture::load_base_albedo_png(vfs, &info.full_path));
        if let Some(png_bytes) = png {
            base.insert(info.full_path.clone(), png_bytes.clone());
            base.entry(info.stem.clone()).or_insert(png_bytes);
        } else {
            eprintln!("  Warning: failed to decode base texture for {}", info.stem);
        }
    }

    // Discover camo schemes.
    let stems: Vec<String> = unique_infos.iter().map(|i| i.stem.clone()).collect();
    let schemes = texture::discover_texture_schemes(vfs, &stems);

    let mut camo_schemes = Vec::new();
    for scheme in &schemes {
        let mut scheme_textures = HashMap::new();
        for info in &unique_infos {
            if let Some((_base_name, dds_bytes)) =
                texture::load_texture_bytes(vfs, &info.stem, scheme)
            {
                match texture::dds_to_png(&dds_bytes) {
                    Ok(png_bytes) => {
                        scheme_textures.insert(info.stem.clone(), png_bytes);
                    }
                    Err(e) => {
                        eprintln!(
                            "  Warning: failed to decode camo texture {}_{}: {e}",
                            info.stem, scheme
                        );
                    }
                }
            }
        }
        if !scheme_textures.is_empty() {
            camo_schemes.push((scheme.clone(), scheme_textures));
        }
    }

    TextureSet {
        base,
        base_uv_transforms: HashMap::new(),
        camo_schemes,
        tiled_uv_transforms: HashMap::new(),
    }
}

fn promote_camouflage_to_base(
    texture_set: &mut TextureSet,
    mfm_infos: &[MfmInfo],
    selection: &str,
) -> Result<(), Report> {
    let scheme_index = texture_set
        .camo_schemes
        .iter()
        .position(|(name, _)| name.eq_ignore_ascii_case(selection))
        .ok_or_else(|| {
            let available = texture_set
                .camo_schemes
                .iter()
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            rootcause::report!(
                "Camouflage scheme '{selection}' was not found. Available schemes: {available}"
            )
        })?;

    let scheme_textures = texture_set.camo_schemes[scheme_index].1.clone();
    let mut composite_cache: HashMap<(String, [u32; 4]), Vec<u8>> = HashMap::new();
    let mut applied = 0usize;
    for info in mfm_infos {
        let Some(png) = scheme_textures.get(&info.stem) else {
            continue;
        };
        let uv_xform = texture_set
            .tiled_uv_transforms
            .get(&(scheme_index, info.stem.clone()))
            .copied();
        let mut baked_composite = false;
        let promoted_png = if let Some(transform) = uv_xform {
            let base_png = texture_set
                .base
                .get(&info.full_path)
                .or_else(|| texture_set.base.get(&info.stem));
            if let Some(base_png) = base_png {
                let cache_key = (info.full_path.clone(), transform.map(f32::to_bits));
                if let Some(cached) = composite_cache.get(&cache_key) {
                    baked_composite = true;
                    cached.clone()
                } else {
                    match texture::composite_tiled_camo_png(base_png, png, transform) {
                        Ok(composite) => {
                            composite_cache.insert(cache_key, composite.clone());
                            baked_composite = true;
                            composite
                        }
                        Err(error) => {
                            eprintln!(
                                "  Warning: failed to composite camouflage for {}: {error}",
                                info.stem
                            );
                            png.clone()
                        }
                    }
                }
            } else {
                eprintln!(
                    "  Warning: base albedo missing for camouflage material {}",
                    info.stem
                );
                png.clone()
            }
        } else {
            png.clone()
        };

        texture_set
            .base
            .insert(info.full_path.clone(), promoted_png.clone());
        texture_set.base.insert(info.stem.clone(), promoted_png);
        if baked_composite {
            // The UV transform has already been baked into the material-sized
            // texture, so applying it again would shrink/offset the result.
            texture_set.base_uv_transforms.remove(&info.full_path);
            texture_set.base_uv_transforms.remove(&info.stem);
        } else if let Some(transform) = uv_xform {
            texture_set
                .base_uv_transforms
                .insert(info.full_path.clone(), transform);
            texture_set
                .base_uv_transforms
                .insert(info.stem.clone(), transform);
        }
        applied += 1;
    }

    if applied == 0 {
        bail!("Camouflage scheme '{selection}' did not match any ship materials.");
    }

    eprintln!("  Selected camouflage: {selection} ({applied} materials)");
    texture_set.camo_schemes.clear();
    texture_set.tiled_uv_transforms.clear();
    Ok(())
}

/// Resolve a compound hardpoint (e.g. `HP_AGM_3_HP_AGA_1`) by finding the
/// longest hull HP name that prefixes the mount's HP name, then looking up
/// the child HP in the parent turret's visual node tree.
///
/// Returns `(parent_hull_transform, Some(child_turret_transform))` on success.
fn resolve_compound_hp(
    hp_name: &str,
    hp_transforms: &HashMap<String, [f32; 16]>,
    hp_to_model_path: &HashMap<&str, &str>,
    turret_model_index: &HashMap<String, usize>,
    turret_models: &[OwnedSubModel],
    strings: &assets_bin::StringsSection<'_>,
) -> Option<([f32; 16], Option<[f32; 16]>)> {
    // Find the longest hull HP name that is a proper prefix of hp_name with
    // a '_' separator. This avoids partial matches like HP_AG matching HP_AGM_3.
    let mut best_parent: Option<(&str, &[f32; 16])> = None;
    for (hull_hp, xform) in hp_transforms {
        if hp_name.len() > hull_hp.len()
            && hp_name.starts_with(hull_hp.as_str())
            && hp_name.as_bytes()[hull_hp.len()] == b'_'
            && best_parent.is_none_or(|(bp, _)| hull_hp.len() > bp.len())
        {
            best_parent = Some((hull_hp.as_str(), xform));
        }
    }
    let (parent_hp, parent_xform) = best_parent?;

    // Extract child HP name: everything after "parent_"
    let child_hp = &hp_name[parent_hp.len() + 1..];

    // Find the parent turret model via the hp_to_model_path mapping.
    let &parent_model_path = hp_to_model_path.get(parent_hp)?;
    let &parent_turret_idx = turret_model_index.get(parent_model_path)?;
    let parent_turret = &turret_models[parent_turret_idx];

    // Look up the child HP transform in the parent turret's visual.
    let child_xform = parent_turret
        .visual
        .find_hardpoint_transform(child_hp, strings)?;

    Some((*parent_xform, Some(child_xform)))
}

/// Build a [`BarrelPitch`] config for per-vertex barrel rotation.
///
/// Finds the `Rotate_X` pivot point in turret-local space, builds a pitch
/// rotation matrix around it, and identifies which blend bone indices are
/// barrel bones (descendants of `Rotate_X` in the skeleton hierarchy).
fn build_barrel_pitch(
    visual: &VisualPrototype,
    strings: &assets_bin::StringsSection<'_>,
    min_pitch_deg: f32,
) -> Option<super::gltf_export::BarrelPitch> {
    let rotate_x_idx = visual.find_node_index_by_name("Rotate_X", strings)?;

    // Get the Rotate_X world (composed) transform to find pivot position.
    let rotate_x_world = visual.find_hardpoint_transform("Rotate_X", strings)?;
    let pivot = [rotate_x_world[12], rotate_x_world[13], rotate_x_world[14]];

    // Build pitch rotation matrix: T(-pivot) * Rx(-pitch) * T(pivot)
    let pitch_rad = -min_pitch_deg.to_radians();
    let (sin_p, cos_p) = pitch_rad.sin_cos();

    // Rx rotation (column-major):
    //   1     0      0
    //   0   cos_p  -sin_p
    //   0   sin_p   cos_p
    let pitch_matrix = [
        // col 0
        1.0,
        0.0,
        0.0,
        0.0,
        // col 1
        0.0,
        cos_p,
        sin_p,
        0.0,
        // col 2
        0.0,
        -sin_p,
        cos_p,
        0.0,
        // col 3: T(-pivot) * Rx * T(pivot) translation
        0.0,
        pivot[1] - cos_p * pivot[1] + sin_p * pivot[2],
        pivot[2] + sin_p * pivot[1] - cos_p * pivot[2],
        1.0,
    ];

    // Identify barrel bone indices from the first render set's blend bone list.
    // Barrel bones = those that ARE `Rotate_X` or descendants of it.
    let first_rs = visual.render_sets.first()?;
    let mut barrel_bone_indices = Vec::new();
    for (blend_idx, &name_id) in first_rs.node_name_ids.iter().enumerate() {
        // Resolve name_id → node index in skeleton
        let node_idx = visual
            .nodes
            .name_map_name_ids
            .iter()
            .position(|&nid| nid == name_id)
            .map(|i| visual.nodes.name_map_node_ids[i]);
        if let Some(ni) = node_idx
            && (ni == rotate_x_idx || visual.is_descendant_of(ni, rotate_x_idx))
        {
            barrel_bone_indices.push(blend_idx as u8);
        }
    }

    if barrel_bone_indices.is_empty() {
        return None;
    }

    // Conjugate the pitch matrix for Z-negated coordinate space (left→right-handed).
    let pitch_matrix = super::gltf_export::negate_z_transform(pitch_matrix);

    Some(super::gltf_export::BarrelPitch {
        pitch_matrix,
        barrel_bone_indices,
    })
}

fn correct_upper_citadel_labels(mesh: &mut InteractiveArmorMesh,
    cit: &[&geometry::SplashBox], cas: &[&geometry::SplashBox]) {
    if cit.is_empty() || cas.is_empty() { return; }
    let cit_top = cit.iter().map(|b|b.max[1]).fold(f32::NEG_INFINITY,f32::max);
    // The collision and splash surfaces can differ slightly at their borders.
    const MARGIN: f32 = 0.1;
    let mut corrected=0;
    for (i, info) in mesh.triangle_info.iter_mut().enumerate() {
        if info.zone != "Citadel" || info.material_name != "InclinCit" {continue;}
        let tri: Vec<_> = mesh.indices[i*3..i*3+3].iter().map(|n|mesh.positions[*n as usize]).collect();
        if tri.iter().any(|p|p[1] <= cit_top + MARGIN) {continue;}
        let center: [f32;3] = std::array::from_fn(|j|tri.iter().map(|p|p[j]).sum::<f32>()/3.0);
        let center=[center[0],center[1],-center[2]]; // splash uses the game axis
        if cas.iter().any(|b|(0..3).all(|j|center[j]>=b.min[j]-MARGIN && center[j]<=b.max[j]+MARGIN)) {
            info.zone="Casemate".to_string(); corrected+=1;
        }
    }
    if corrected>0 {eprintln!("Armor zone compatibility: {corrected} upper InclinCit faces classified as Casemate using authored hit boxes");}
}

#[cfg(test)]
mod armor_zone_compat_tests {
    use super::*;
    #[test]
    fn upper_deck_is_casemate_but_inner_citadel_and_thickness_are_preserved() {
        let tri = geometry::ArmorTriangle {vertices:[[0.2,0.36,1.0],[0.3,0.36,1.0],[0.2,0.39,1.1]],
            normals:[[0.0,1.0,0.0];3],material_id:114,layer_index:1};
        let model=geometry::ArmorModel{name:"test".into(),triangles:vec![tri,geometry::ArmorTriangle{vertices:[[0.2,-0.2,1.0];3],..tri}]};
        let mut mesh=InteractiveArmorMesh::from_armor_model(&model,None,None);
        for info in &mut mesh.triangle_info {info.thickness_mm=30.0;info.layers=vec![30.0];}
        let cit=geometry::SplashBox{name:"cit".into(),min:[-0.5,-0.4,0.0],max:[0.5,0.04,2.0]};
        let cas=geometry::SplashBox{name:"cas".into(),min:[-0.8,0.04,0.0],max:[0.8,0.36,2.0]};
        correct_upper_citadel_labels(&mut mesh,&[&cit],&[&cas]);
        assert_eq!(mesh.triangle_info[0].zone,"Casemate");
        assert_eq!(mesh.triangle_info[1].zone,"Citadel");
        assert_eq!(mesh.triangle_info[0].material_name,"InclinCit");
        assert_eq!(mesh.triangle_info[0].thickness_mm,30.0);
        assert_eq!(mesh.triangle_info[0].layers,vec![30.0]);
        let mut unsupported=InteractiveArmorMesh::from_armor_model(&model,None,None);
        correct_upper_citadel_labels(&mut unsupported,&[],&[&cas]);
        assert_eq!(unsupported.triangle_info[0].zone,"Citadel");
        correct_upper_citadel_labels(&mut unsupported,&[&cit],&[]);
        assert_eq!(unsupported.triangle_info[0].zone,"Citadel");
    }
}

fn mat4_mul_col_major(a: &[f32; 16], b: &[f32; 16]) -> [f32; 16] {
    let mut out = [0.0f32; 16];
    for col in 0..4 {
        for row in 0..4 {
            out[col * 4 + row] = (0..4).map(|k| a[k * 4 + row] * b[col * 4 + k]).sum();
        }
    }
    out
}

/// Extract the rotation part of a column-major 4x4 matrix and return its
/// inverse (transpose) as a full 4x4 matrix with zero translation.
///
/// Valid for rigid-body transforms (orthonormal rotation + translation).
/// The inverse of the rotation part is simply its transpose.
fn mat4_rotation_inverse(m: &[f32; 16]) -> [f32; 16] {
    // Column-major layout:
    //   col0 = m[0..4], col1 = m[4..8], col2 = m[8..12], col3 = m[12..16]
    // 3x3 rotation at (row, col) -> m[col*4 + row]
    // Transpose: swap (row, col) -> (col, row)
    [
        m[0], m[4], m[8], 0.0, // col 0 = original row 0
        m[1], m[5], m[9], 0.0, // col 1 = original row 1
        m[2], m[6], m[10], 0.0, // col 2 = original row 2
        0.0, 0.0, 0.0, 1.0, // no translation
    ]
}

/// Map a mount's species to a display group name.
fn mount_group(species: Option<crate::game_params::types::MountSpecies>) -> &'static str {
    match species {
        Some(s) => s.display_group(),
        None => "Other",
    }
}

// ---------------------------------------------------------------------------
// Convenience function
// ---------------------------------------------------------------------------

/// One-shot: load game data, resolve ship, export GLB.
///
/// For multiple ships, use [`ShipAssets`] directly to amortize the ~18s
/// GameParams parsing cost.
pub fn export_ship_glb(
    vfs: &VfsPath,
    name: &str,
    options: &ShipExportOptions,
    writer: &mut impl Write,
) -> Result<ShipInfo, Report> {
    let assets = ShipAssets::load(vfs)?;
    let ctx = assets.load_ship(name, options)?;
    let info = ctx.info().clone();
    ctx.export_glb(writer)?;
    Ok(info)
}
