//! Read-only catalog probe. Arguments: decoded assets.bin, JSON model-path array.
//! Produces model-path keyed JSON; never classifies by ship code or filename prefix.
use std::{collections::{BTreeMap, HashMap, HashSet}, env, fs};
use wowsunpack::{models::{assets_bin::{parse_assets_bin, PrototypeDatabase}, model, visual}, export::texture::material_texture_ids};

fn normalize(path: &str) -> String {
    path.replace('\\', "/").trim_start_matches('/').to_ascii_lowercase()
}

fn resolve_visual(
    db: &PrototypeDatabase<'_>, ids: &HashMap<u64, usize>, paths: &HashMap<String, u64>,
    id: u64, depth: usize,
) -> Result<visual::VisualPrototype, String> {
    if depth > 4 { return Err("cyclic model reference".into()); }
    let loc = db.decode_r2p_value(db.lookup_r2p(id).ok_or("missing prototype")?)
        .map_err(|e| format!("{e:?}"))?;
    let blob = &db.databases[loc.blob_index];
    match blob.prototype_magic {
        0xA957_6F28 => {
            let split = blob.prototype_checksum == model::MODEL_V2_CHECKSUM;
            let data = db.get_prototype_data(loc, if split { model::MODEL_V2_ITEM_SIZE } else { model::MODEL_ITEM_SIZE })
                .map_err(|e| format!("{e:?}"))?;
            let vis_id = if split { model::parse_model_v2_visual_id(data).map_err(|e| format!("{e:?}"))? }
                else { model::parse_model(data).map_err(|e| format!("{e:?}"))?.visual_resource_id };
            resolve_visual(db, ids, paths, vis_id, depth + 1)
        }
        0x480D_C57B if blob.prototype_checksum == 0xA012_5AEA => {
            let data = db.get_prototype_data(loc, visual::VISUAL_V2_ITEM_SIZE).map_err(|e| format!("{e:?}"))?;
            let path = db.reconstruct_path(*ids.get(&id).ok_or("missing visual path")?, ids);
            let nid = *paths.get(&normalize(&format!("@{path}"))).ok_or("missing visual nodes")?;
            let nl = db.decode_r2p_value(db.lookup_r2p(nid).ok_or("missing nodes prototype")?).map_err(|e| format!("{e:?}"))?;
            if db.databases[nl.blob_index].prototype_magic != 0xD9BB_9F4A { return Err("unsupported nodes layout".into()); }
            let nodes = db.get_prototype_data(nl, visual::VISUAL_V2_NODES_ITEM_SIZE).map_err(|e| format!("{e:?}"))?;
            visual::parse_visual_v2(data, nodes).map_err(|e| format!("{e:?}"))
        }
        0x480D_C57B => {
            let data = db.get_prototype_data(loc, visual::VISUAL_ITEM_SIZE).map_err(|e| format!("{e:?}"))?;
            visual::parse_visual(data).map_err(|e| format!("{e:?}"))
        }
        _ => Err("unsupported model/visual layout".into()),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().collect();
    if args.len() != 3 { return Err("usage: ship_material_probe <assets.bin> <models.json>".into()); }
    let bytes = fs::read(&args[1])?;
    let db = parse_assets_bin(&bytes).map_err(|e| format!("{e:?}"))?;
    let ids = db.build_self_id_index();
    let mut paths = HashMap::new();
    let mut directories: HashMap<String, Vec<u64>> = HashMap::new();
    for (i, entry) in db.paths_storage.iter().enumerate() {
        let path = normalize(&db.reconstruct_path(i, &ids));
        paths.insert(path.clone(), entry.self_id);
        if !path.starts_with('@') && path.ends_with(".visual") && !path.contains("/lods/") {
            if let Some((dir, _)) = path.rsplit_once('/') {
                directories.entry(dir.to_owned()).or_default().push(entry.self_id);
            }
        }
    }
    let models: Vec<String> = serde_json::from_slice(&fs::read(&args[2])?)?;
    let mut output = BTreeMap::new();
    for original in models {
        let path = normalize(&original);
        if output.contains_key(&original) { continue; }
        let mut errors = Vec::new();
        let mut material_ids = HashSet::new();
        let mut checked_visuals = 0;
        // Follow the selected model's actual reference. The exporter additionally
        // uses drawable sibling hull visuals for node-only roots in split builds.
        match paths.get(&path).ok_or("model not found").and_then(|id| resolve_visual(&db, &ids, &paths, *id, 0).map_err(|_| "model reference could not be read")) {
            Ok(root) => {
                if !root.render_sets.is_empty() {
                    checked_visuals += 1;
                    material_ids.extend(root.render_sets.iter().map(|rs| rs.material_mfm_path_id).filter(|id| *id != 0));
                } else if let Some((dir, _)) = path.rsplit_once('/') {
                    for id in directories.get(dir).into_iter().flatten() {
                        match resolve_visual(&db, &ids, &paths, *id, 0) {
                            Ok(vp) if !vp.lods.is_empty() && !vp.render_sets.is_empty() => {
                                checked_visuals += 1;
                                material_ids.extend(vp.render_sets.iter().map(|rs| rs.material_mfm_path_id).filter(|id| *id != 0));
                            }
                            Ok(_) => (),
                            Err(e) => errors.push(e),
                        }
                    }
                }
            }
            Err(e) => errors.push(e.to_owned()),
        }
        let mut indexed = Vec::new();
        let mut materials_checked = 0;
        for id in &material_ids {
            let bindings = (|| -> Result<HashMap<String, String>, String> {
                let loc = db.decode_r2p_value(db.lookup_r2p(*id).ok_or("missing material")?).map_err(|e| format!("{e:?}"))?;
                let blob = &db.databases[loc.blob_index];
                if blob.prototype_magic != 0x5069_C471 { return Err("unsupported material prototype".into()); }
                let stride = if blob.prototype_checksum == 0x755D_585C { 0x88 } else { 0x78 };
                let data = db.get_prototype_data(loc, stride).map_err(|e| format!("{e:?}"))?;
                let values = material_texture_ids(data).ok_or("material inputs could not be read")?;
                let mut result = HashMap::new();
                for (name, target) in values {
                    let name = db.strings.get_string_by_id(name).ok_or("missing input name")?;
                    let idx = ids.get(&target).ok_or("missing texture path")?;
                    result.insert(name.to_owned(), db.reconstruct_path(*idx, &ids));
                }
                Ok(result)
            })();
            match bindings {
                Ok(b) => {
                    materials_checked += 1;
                    if ["materialIdMap", "albedoArray", "MGArray", "artMap"].iter().all(|key| b.get(*key).is_some_and(|value| !value.is_empty())) {
                        indexed.push(db.reconstruct_path(*ids.get(id).ok_or("missing material path")?, &ids));
                    }
                }
                Err(e) => errors.push(e),
            }
        }
        indexed.sort();
        errors.sort(); errors.dedup();
        let status = if !indexed.is_empty() { "indexed" } else if errors.is_empty() && materials_checked > 0 { "legacy" } else { "unknown" };
        output.insert(original, serde_json::json!({
            "status": status, "materials_checked": materials_checked,
            "visuals_checked": checked_visuals, "indexed_materials": indexed,
            "errors": errors.into_iter().take(3).collect::<Vec<_>>(),
        }));
    }
    println!("{}", serde_json::to_string(&serde_json::json!({"schema": "ship-material-probe/v1", "models": output}))?);
    Ok(())
}
