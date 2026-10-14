//! DDS texture loading and conversion for glTF export.

use std::io::Cursor;

use image_dds::image::ExtendedColorType;
use image_dds::image::ImageEncoder;
use image_dds::image::codecs::png::PngEncoder;
use rayon::prelude::*;
use rootcause::Report;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum TextureError {
    #[error("failed to parse DDS: {0}")]
    DdsParse(String),
    #[error("failed to decode DDS image: {0}")]
    DdsDecode(String),
    #[error("failed to decode PNG image: {0}")]
    PngDecode(String),
    #[error("failed to encode PNG: {0}")]
    PngEncode(String),
}

/// Decode DDS bytes to PNG bytes (RGBA8), optionally downsampling to a max size.
///
/// If `max_size` is `Some(n)`, the image is downsampled using box filtering so
/// that neither dimension exceeds `n`. This is a simple but effective way to
/// reduce texture memory for map-scale visualization.
pub fn dds_to_png_resized(
    dds_bytes: &[u8],
    max_size: Option<u32>,
) -> Result<Vec<u8>, Report<TextureError>> {
    let decoded;
    let dds_bytes = if bc7prep_block_count(dds_bytes).is_some() {
        decoded = super::bc7prep::decode_dds(dds_bytes)
            .map_err(|e| Report::new(TextureError::DdsDecode(e)))?;
        decoded.as_slice()
    } else { dds_bytes };
    let dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(dds_bytes))
        .map_err(|e| Report::new(TextureError::DdsParse(e.to_string())))?;

    let rgba_image = image_dds::image_from_dds(&dds, 0)
        .map_err(|e| Report::new(TextureError::DdsDecode(e.to_string())))?;

    let (w, h) = (rgba_image.width(), rgba_image.height());

    // Downsample if needed.
    let (out_w, out_h, pixels) = if let Some(max) = max_size
        && (w > max || h > max)
    {
        let scale = (max as f32 / w as f32).min(max as f32 / h as f32);
        let nw = ((w as f32 * scale) as u32).max(1);
        let nh = ((h as f32 * scale) as u32).max(1);
        let src = rgba_image.as_raw();
        let mut dst = vec![0u8; (nw * nh * 4) as usize];
        // Box filter: average source pixels that map to each destination pixel.
        for dy in 0..nh {
            let sy0 = (dy as f64 * h as f64 / nh as f64) as u32;
            let sy1 = (((dy + 1) as f64 * h as f64 / nh as f64) as u32).min(h);
            for dx in 0..nw {
                let sx0 = (dx as f64 * w as f64 / nw as f64) as u32;
                let sx1 = (((dx + 1) as f64 * w as f64 / nw as f64) as u32).min(w);
                let mut r = 0u32;
                let mut g = 0u32;
                let mut b = 0u32;
                let mut a = 0u32;
                let mut count = 0u32;
                for sy in sy0..sy1 {
                    for sx in sx0..sx1 {
                        let i = (sy * w + sx) as usize * 4;
                        r += src[i] as u32;
                        g += src[i + 1] as u32;
                        b += src[i + 2] as u32;
                        a += src[i + 3] as u32;
                        count += 1;
                    }
                }
                if count > 0 {
                    let di = (dy * nw + dx) as usize * 4;
                    dst[di] = (r / count) as u8;
                    dst[di + 1] = (g / count) as u8;
                    dst[di + 2] = (b / count) as u8;
                    dst[di + 3] = (a / count) as u8;
                }
            }
        }
        (nw, nh, dst)
    } else {
        (w, h, rgba_image.into_raw())
    };

    let mut png_buf = Vec::new();
    PngEncoder::new(&mut png_buf)
        .write_image(&pixels, out_w, out_h, ExtendedColorType::Rgba8)
        .map_err(|e| Report::new(TextureError::PngEncode(e.to_string())))?;

    Ok(png_buf)
}

/// Decode DDS bytes to PNG bytes (RGBA8).
pub fn dds_to_png(dds_bytes: &[u8]) -> Result<Vec<u8>, Report<TextureError>> {
    let dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(dds_bytes))
        .map_err(|e| Report::new(TextureError::DdsParse(e.to_string())))?;

    let rgba_image = image_dds::image_from_dds(&dds, 0)
        .map_err(|e| Report::new(TextureError::DdsDecode(e.to_string())))?;

    let mut png_buf = Vec::new();
    PngEncoder::new(&mut png_buf)
        .write_image(
            rgba_image.as_raw(),
            rgba_image.width(),
            rgba_image.height(),
            ExtendedColorType::Rgba8,
        )
        .map_err(|e| Report::new(TextureError::PngEncode(e.to_string())))?;

    Ok(png_buf)
}

const INDEXED_MATERIAL_COUNT: usize = 196;
const INDEXED_ARRAY_COUNT: usize = 6;

/// Texture property IDs in a MaterialPrototype. Offsets are relative to the
/// record start; the low nibble of a property descriptor is its value type.
pub fn material_texture_ids(data: &[u8]) -> Option<Vec<(u32, u64)>> {
    let count = u32::from_le_bytes(data.get(0..4)?.try_into().ok()?) as usize;
    if count > 4096 { return None; }
    let names = resolve_record_relptr(data, 0x18)?;
    let descriptors = resolve_record_relptr(data, 0x20)?;
    let textures = resolve_record_relptr(data, 0x50)?;
    let mut result = Vec::new();
    for i in 0..count {
        let d = descriptors.checked_add(i * 2)?;
        let descriptor = u16::from_le_bytes(data.get(d..d + 2)?.try_into().ok()?);
        if descriptor & 15 != 4 { continue; }
        let n = names.checked_add(i * 4)?;
        let name = u32::from_le_bytes(data.get(n..n + 4)?.try_into().ok()?);
        let t = textures.checked_add((descriptor >> 4) as usize * 8)?;
        let path = u64::from_le_bytes(data.get(t..t + 8)?.try_into().ok()?);
        result.push((name, path));
    }
    Some(result)
}

pub fn load_authored_texture_png(vfs: &vfs::VfsPath, path: &str) -> Option<Vec<u8>> {
    let paths = authored_texture_candidates(path);
    let bytes = load_texture_candidate(vfs, &paths)?;
    dds_to_png(&bytes).ok()
}

fn authored_texture_candidates(path: &str) -> Vec<String> {
    let Some(stem) = path.strip_suffix(".dds") else {
        return vec![path.to_string()];
    };
    // Streamed clients can provide intermediate mips separately. A rejected
    // dd0 must not hide an intact dd1/dd2 behind the much smaller DDS tail.
    // Every candidate still passes the same standalone decode validation.
    ["dd0", "dd1", "dd2", "dds"]
        .into_iter()
        .map(|extension| format!("{stem}.{extension}"))
        .collect()
}

fn read_i64_at(data: &[u8], offset: usize) -> Option<i64> {
    Some(i64::from_le_bytes(
        data.get(offset..offset + 8)?.try_into().ok()?,
    ))
}

fn resolve_record_relptr(data: &[u8], field_offset: usize) -> Option<usize> {
    // PrototypeDatabase pointers in the extracted record are already absolute
    // offsets within that record. Treating them as field-relative shifts every
    // indexed-material array by 0x68 bytes and mixes tint data into tile IDs.
    let target = read_i64_at(data, field_offset)?;
    (target >= 0 && target as usize <= data.len()).then_some(target as usize)
}

fn read_indexed_material_arrays(data: &[u8]) -> Option<Vec<Vec<[f32; 4]>>> {
    if u32::from_le_bytes(data.get(0..4)?.try_into().ok()?) < 15 {
        return None;
    }
    let base = resolve_record_relptr(data, 0x68)?;
    let byte_count = INDEXED_ARRAY_COUNT * INDEXED_MATERIAL_COUNT * 16;
    let bytes = data.get(base..base.checked_add(byte_count)?)?;
    let mut arrays = vec![vec![[0.0; 4]; INDEXED_MATERIAL_COUNT]; INDEXED_ARRAY_COUNT];
    for (array_index, array) in arrays.iter_mut().enumerate() {
        for (material_index, value) in array.iter_mut().enumerate() {
            let start = (array_index * INDEXED_MATERIAL_COUNT + material_index) * 16;
            for (component, slot) in value.iter_mut().enumerate() {
                let offset = start + component * 4;
                *slot = f32::from_le_bytes(bytes[offset..offset + 4].try_into().ok()?);
            }
        }
    }
    Some(arrays)
}

fn load_texture_candidate(
    vfs: &vfs::VfsPath,
    paths: &[String],
) -> Option<Vec<u8>> {
    for path in paths {
        let Some(data) = load_dds_from_vfs(vfs, path) else {
            continue;
        };
        if let Some(normalized) = normalize_streamed_dds(data) {
            return Some(normalized);
        }
        // Incomplete streamed chunks are not assumed to be sparse atlases.
        // Try the next intact mip instead of inventing missing pixel blocks.
    }
    None
}

fn sample_wrapped(image: &image_dds::image::RgbaImage, u: f32, v: f32) -> [u8; 4] {
    // Colour/coverage textures use the renderer's linear repeat sampler.
    // Material IDs are deliberately read directly in the bake loop below:
    // interpolating IDs would select unrelated material recipes at boundaries.
    let x = u.rem_euclid(1.0) * image.width() as f32 - 0.5;
    let y = (1.0 - v.rem_euclid(1.0)) * image.height() as f32 - 0.5;
    let x0 = x.floor() as i64;
    let y0 = y.floor() as i64;
    let tx = x - x.floor();
    let ty = y - y.floor();
    let at = |dx: i64, dy: i64| image.get_pixel(
        (x0 + dx).rem_euclid(image.width() as i64) as u32,
        (y0 + dy).rem_euclid(image.height() as i64) as u32,
    ).0;
    let (a,b,c,d) = (at(0,0),at(1,0),at(0,1),at(1,1));
    std::array::from_fn(|i| {
        let top = a[i] as f32 * (1.0-tx) + b[i] as f32 * tx;
        let bottom = c[i] as f32 * (1.0-tx) + d[i] as f32 * tx;
        (top * (1.0-ty) + bottom * ty).round().clamp(0.0,255.0) as u8
    })
}

/// Reproduce the colour stage of Korabli's `ship_material_indexed.fx`.
///
/// Ships 2.0 hull textures are not conventional albedos. `_matid` selects one
/// of 196 material recipes; each recipe selects and transforms a layer from a
/// shared 30-layer tile array, then replaces the tile's authored key colour
/// with the ship-specific tint. Baking that lookup into an ordinary PNG makes
/// the result portable to OBJ, Blender, and the integrated viewer.
pub fn bake_indexed_ship_albedo_png(vfs: &vfs::VfsPath, mfm_full_path: &str) -> Option<Vec<u8>> {
    let dir = mfm_full_path.rsplit_once('/')?.0;
    let filename = mfm_full_path.rsplit_once('/')?.1;
    let stem = filename.strip_suffix(".mfm")?;
    let texture_dir = dir
        .rsplit_once('/')
        .map(|(parent, _)| format!("{parent}/textures"))?;

    let mfm_data = load_dds_from_vfs(vfs, mfm_full_path)?;
    let matid_paths = [
        format!("{texture_dir}/{stem}_matid.dd0"),
        format!("{texture_dir}/{stem}_matid.dds"),
        format!("{dir}/{stem}_matid.dd0"),
        format!("{dir}/{stem}_matid.dds"),
    ];
    let matid_bytes = load_texture_candidate(vfs, &matid_paths)?;
    let tile_bytes = load_texture_candidate(
        vfs,
        &["content/gameplay/common/textures/CIT000_1k_ship_tiles_a.dds".to_string()],
    )?;
    let art_bytes = load_texture_candidate(
        vfs,
        &[
            format!("{texture_dir}/{stem}_art.dd0"),
            format!("{texture_dir}/{stem}_art.dd1"),
            format!("{texture_dir}/{stem}_art.dd2"),
            format!("{texture_dir}/{stem}_art.dds"),
            format!("{dir}/{stem}_art.dd0"),
            format!("{dir}/{stem}_art.dds"),
        ],
    );
    let ao_bytes = load_texture_candidate(
        vfs,
        &[
            format!("{texture_dir}/{stem}_ao.dds"),
            format!("{texture_dir}/{stem}_ao.dd0"),
            format!("{dir}/{stem}_ao.dds"),
            format!("{dir}/{stem}_ao.dd0"),
        ],
    );
    let mg_bytes = load_texture_candidate(
        vfs,
        &["content/gameplay/common/textures/CIT000_1k_ship_tiles_mg.dds".to_string()],
    );
    let noise_bytes = load_texture_candidate(
        vfs,
        &["content/gameplay/common/textures/CIC001_splice_rgb_noise_a.dds".to_string()],
    );
    bake_indexed_ship_albedo_with_art_from_parts(
        &mfm_data,
        &matid_bytes,
        &tile_bytes,
        art_bytes.as_deref(),
        ao_bytes.as_deref(),
        mg_bytes.as_deref(),
        noise_bytes.as_deref(),
    )
}

/// Resolve every shader input from its authored binding, including atlases
/// shared across nations and instance materials with a different file stem.
pub fn bake_indexed_ship_bound_png(
    vfs: &vfs::VfsPath,
    mfm_path: &str,
    bindings: &std::collections::HashMap<String, String>,
) -> Option<Vec<u8>> {
    let load = |name: &str, high: bool| {
        let path = bindings.get(name)?;
        let mut paths = authored_texture_candidates(path);
        if !high && paths.len() > 1 {
            paths.rotate_right(1);
        }
        load_texture_candidate(vfs, &paths)
    };
    let mfm = load_dds_from_vfs(vfs, mfm_path)?;
    let matid = load("materialIdMap", true)?;
    let albedo = load("albedoArray", true)?;
    let art = load("artMap", true);
    let ao = load("ambientOcclusionMap", true);
    let mg = load("MGArray", true);
    let noise = load("rgbNoiseMap", true);
    bake_indexed_ship_albedo_with_art_from_parts(&mfm, &matid, &albedo,
        art.as_deref(), ao.as_deref(), mg.as_deref(), noise.as_deref())
}

/// Bake an indexed material from its three independent binary inputs.
/// Exposed separately so format regressions can be tested without mounting the
/// complete game archive.
pub fn bake_indexed_ship_albedo_from_parts(
    mfm_data: &[u8],
    matid_bytes: &[u8],
    tile_bytes: &[u8],
) -> Option<Vec<u8>> {
    bake_indexed_ship_albedo_with_art_from_parts(
        mfm_data,
        matid_bytes,
        tile_bytes,
        None,
        None,
        None,
        None,
    )
}

/// Offline material verification entry point. Inputs retain their authored
/// DDS channels; this does not enable experimental streamed-texture decoding.
pub fn bake_indexed_ship_albedo_with_art_from_parts(
    mfm_data: &[u8],
    matid_bytes: &[u8],
    tile_bytes: &[u8],
    art_bytes: Option<&[u8]>,
    ao_bytes: Option<&[u8]>,
    mg_bytes: Option<&[u8]>,
    noise_bytes: Option<&[u8]>,
) -> Option<Vec<u8>> {
    let arrays = read_indexed_material_arrays(mfm_data)?;
    let albedo_tint = &arrays[0];
    let remove_tint = &arrays[1];
    let art_strength = &arrays[2];
    let offset_scale = &arrays[3];
    let rotation = &arrays[4];
    let tile_index = &arrays[5];

    let matid_dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(matid_bytes)).ok()?;
    let matid = image_dds::image_from_dds(&matid_dds, 0).ok()?;
    let tile_dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(tile_bytes)).ok()?;
    let tile_surface = image_dds::SurfaceRgba8::decode_dds(&tile_dds).ok()?;
    let layer_count = tile_surface.layers;
    if layer_count == 0 {
        return None;
    }
    let mut tiles = Vec::with_capacity(layer_count as usize);
    for layer in 0..layer_count {
        tiles.push(tile_surface.get_image(layer, 0, 0)?);
    }
    let decode_image = |bytes: &[u8]| {
        let dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(bytes)).ok()?;
        image_dds::image_from_dds(&dds, 0).ok()
    };
    let art = art_bytes.and_then(decode_image);
    let ao = ao_bytes.and_then(decode_image);
    let noise = noise_bytes.and_then(decode_image);
    let mg_tiles = mg_bytes.and_then(|bytes| {
        let dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(bytes)).ok()?;
        let surface = image_dds::SurfaceRgba8::decode_dds(&dds).ok()?;
        (0..surface.layers)
            .map(|layer| surface.get_image(layer, 0, 0))
            .collect::<Option<Vec<_>>>()
    });

    let (width, height) = matid.dimensions();
    let row_bytes = width as usize * 4;
    let mut pixels = vec![0u8; row_bytes * height as usize];
    const GAMMA: f32 = 2.2;
    pixels
        .par_chunks_mut(row_bytes)
        .enumerate()
        .for_each(|(y, row)| {
            for x in 0..width as usize {
                let material_id = (matid.get_pixel(x as u32, y as u32)[0] as usize)
                    .min(INDEXED_MATERIAL_COUNT - 1);
                let transform = offset_scale[material_id];
                let angle = rotation[material_id][0];
                let layer = (tile_index[material_id][0].round() as usize).min(tiles.len() - 1);
                let u = (x as f32 + 0.5) / width as f32;
                let v = 1.0 - (y as f32 + 0.5) / height as f32;
                let tu = u * transform[2] + transform[0];
                let tv = v * transform[3] + transform[1];
                let (sin_angle, cos_angle) = angle.sin_cos();
                let tile_u = tu * cos_angle + tv * sin_angle;
                let tile_v = -tu * sin_angle + tv * cos_angle;
                let sample = sample_wrapped(&tiles[layer], tile_u, tile_v);

                let tint = albedo_tint[material_id];
                let remove = remove_tint[material_id];
                let threshold = tint[3];
                let distance = ((sample[0] as f32 / 255.0 - remove[0]).abs()
                    + (sample[1] as f32 / 255.0 - remove[1]).abs()
                    + (sample[2] as f32 / 255.0 - remove[2]).abs()
                    + 0.001)
                    / threshold.max(0.0001);
                let blend = distance.clamp(0.0, 1.0);
                let offset = x * 4;
                let mut base_linear = [0.0f32; 3];
                for channel in 0..3 {
                    let source = sample[channel] as f32 / 255.0;
                    let source_linear = source.powf(GAMMA);
                    let tint_linear = tint[channel].clamp(0.0, 1.0).powf(GAMMA);
                    base_linear[channel] = tint_linear + (source_linear - tint_linear) * blend;
                }

                // The per-ship `_art` map completes the indexed material. Its
                // alpha, the selected MG layer, recipe strength, AO and splice
                // noise form the shader mask that blends authored colour over
                // the tiled base. Without this stage only the conspicuous
                // brown/grey material-ID blocks remain visible.
                if let (Some(art), Some(mg_tiles)) = (art.as_ref(), mg_tiles.as_ref()) {
                    let art_sample = sample_wrapped(art, u, v);
                    // Shader tileIdx: X=albedo, Y=normal, Z=MG. The art
                    // gate samples MG blue, not the normal array's layer.
                    let mg_layer = (tile_index[material_id][2].round() as usize)
                        .min(mg_tiles.len().saturating_sub(1));
                    let mg_sample = sample_wrapped(&mg_tiles[mg_layer], tile_u, tile_v);
                    let strength = art_strength[material_id];
                    let art_gate = (art_sample[3] as f32 / 255.0 * strength[0]).clamp(0.0, 1.0)
                        * (mg_sample[2] as f32 / 255.0);
                    let noise_value = noise
                        .as_ref()
                        .map(|map| sample_wrapped(map, u * 52.0, v * 52.0)[0] as f32 / 255.0)
                        .unwrap_or(1.0);
                    let edge = (art_gate * 6.666668 - 5.333334).clamp(0.0, 1.0);
                    let threshold = strength[1] * (0.5 - edge * (11.0 - strength[1]));
                    let denominator = strength[1] - threshold;
                    let art_mask = if denominator.abs() > 0.0001 {
                        ((noise_value.max(0.0) * 10.0 - threshold) / denominator).clamp(0.0, 1.0)
                            * art_gate
                    } else {
                        art_gate
                    };
                    let ao_amount = ao
                        .as_ref()
                        .map(|map| sample_wrapped(map, u, v)[1] as f32 / 255.0)
                        .unwrap_or(0.0);
                    for channel in 0..3 {
                        let art_linear =
                            (art_sample[channel] as f32 / 255.0).powf(GAMMA) * (1.0 - ao_amount);
                        base_linear[channel] += (art_linear - base_linear[channel]) * art_mask;
                    }
                }
                for channel in 0..3 {
                    row[offset + channel] = (linear_to_srgb(base_linear[channel]) * 255.0)
                        .round()
                        .clamp(0.0, 255.0) as u8;
                }
                row[offset + 3] = sample[3];
            }
        });
    let output = image_dds::image::RgbaImage::from_raw(width, height, pixels)?;
    let mut png = Vec::new();
    PngEncoder::new(&mut png)
        .write_image(output.as_raw(), width, height, ExtendedColorType::Rgba8)
        .ok()?;
    Some(png)
}
/// Load a portable PNG for either a conventional or indexed ship material.
pub fn load_base_albedo_png(vfs: &vfs::VfsPath, mfm_full_path: &str) -> Option<Vec<u8>> {
    bake_indexed_ship_albedo_png(vfs, mfm_full_path).or_else(|| {
        load_base_albedo_bytes(vfs, mfm_full_path).and_then(|dds| dds_to_png(&dds).ok())
    })
}
/// Bake a tiled camouflage tile texture with color scheme replacement.
///
/// The tile texture is a color-indexed mask where R/G/B/Black zones correspond
/// to color1/color2/color3/color0 from the color scheme. This function replaces
/// each zone with the appropriate color and returns the result as PNG.
pub fn bake_tiled_camo_png(
    tile_dds_bytes: &[u8],
    colors: &[[f32; 4]; 4],
) -> Result<Vec<u8>, Report<TextureError>> {
    let dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(tile_dds_bytes))
        .map_err(|e| Report::new(TextureError::DdsParse(e.to_string())))?;

    let mut rgba_image = image_dds::image_from_dds(&dds, 0)
        .map_err(|e| Report::new(TextureError::DdsDecode(e.to_string())))?;

    for pixel in rgba_image.pixels_mut() {
        let [r, g, b, _a] = pixel.0;
        // Determine zone by dominant channel. DXT1 compression may blend
        // edge pixels, but dominant-channel detection handles this well.
        let color = if r > g && r > b && r > 30 {
            &colors[1] // Red zone → color1
        } else if g > r && g > b && g > 30 {
            &colors[2] // Green zone → color2
        } else if b > r && b > g && b > 30 {
            &colors[3] // Blue zone → color3
        } else {
            &colors[0] // Black/dark zone → color0
        };
        // Convert linear float [0,1] to sRGB [0,255]
        pixel.0 = [
            (linear_to_srgb(color[0]) * 255.0) as u8,
            (linear_to_srgb(color[1]) * 255.0) as u8,
            (linear_to_srgb(color[2]) * 255.0) as u8,
            (color[3].clamp(0.0, 1.0) * 255.0) as u8,
        ];
    }

    let mut png_buf = Vec::new();
    PngEncoder::new(&mut png_buf)
        .write_image(
            rgba_image.as_raw(),
            rgba_image.width(),
            rgba_image.height(),
            ExtendedColorType::Rgba8,
        )
        .map_err(|e| Report::new(TextureError::PngEncode(e.to_string())))?;

    Ok(png_buf)
}

/// Composite a colorized tiled camouflage over a material's original albedo.
///
/// World of Warships tiled camouflage textures are paint masks, not complete
/// material albedos. Replacing a 4x4 solid-color tile with a 4096x4096 hull
/// texture discards every weld, rust streak, decal, and panel detail. This
/// helper samples the tile using the game's UV transform and applies it as a
/// neutral-128 multiply over the original albedo, preserving those details.
pub fn composite_tiled_camo_png(
    base_png_bytes: &[u8],
    camo_png_bytes: &[u8],
    uv_transform: [f32; 4],
) -> Result<Vec<u8>, Report<TextureError>> {
    let mut base = image_dds::image::load_from_memory(base_png_bytes)
        .map_err(|e| Report::new(TextureError::PngDecode(e.to_string())))?
        .to_rgba8();
    let camo = image_dds::image::load_from_memory(camo_png_bytes)
        .map_err(|e| Report::new(TextureError::PngDecode(e.to_string())))?
        .to_rgba8();
    let (base_w, base_h) = base.dimensions();
    let (camo_w, camo_h) = camo.dimensions();
    if base_w == 0 || base_h == 0 || camo_w == 0 || camo_h == 0 {
        return Err(Report::new(TextureError::PngDecode(
            "camouflage composite received an empty image".to_string(),
        )));
    }

    let [scale_x, scale_y, offset_x, offset_y] = uv_transform;
    for y in 0..base_h {
        for x in 0..base_w {
            // glTF texture coordinates use a bottom-left V origin while PNG
            // rows are stored top-down. Convert to UV space for the transform,
            // then back to a PNG row for sampling.
            let u = (x as f32 + 0.5) / base_w as f32;
            let v = 1.0 - (y as f32 + 0.5) / base_h as f32;
            let camo_u = u * scale_x + offset_x;
            let camo_v = v * scale_y + offset_y;
            // The game samples camouflage masks with a linear repeat sampler.
            // Nearest-neighbour expansion made low-resolution authored masks
            // (Bismarck '41 uses a 512 px mask over a 4096 px hull) visibly
            // stair-step when baked into a portable PNG.
            let paint = sample_wrapped(&camo, camo_u, camo_v);
            let pixel = base.get_pixel_mut(x, y);
            let source = pixel.0;
            let coverage = paint[3] as f32 / 255.0;
            for channel in 0..3 {
                // 128 is the neutral paint value: it leaves the original
                // channel unchanged. Darker/lighter camouflage colors tint the
                // surface while retaining the source texture's local detail.
                let painted =
                    ((source[channel] as u16 * paint[channel] as u16 + 64) / 128).min(255) as f32;
                pixel.0[channel] = (source[channel] as f32
                    + (painted - source[channel] as f32) * coverage)
                    .round()
                    .clamp(0.0, 255.0) as u8;
            }
            // The source material controls cutouts. Camouflage alpha is paint
            // coverage only and must not punch new holes into the model.
            pixel.0[3] = source[3];
        }
    }

    let mut png_buf = Vec::new();
    PngEncoder::new(&mut png_buf)
        .write_image(
            base.as_raw(),
            base.width(),
            base.height(),
            ExtendedColorType::Rgba8,
        )
        .map_err(|e| Report::new(TextureError::PngEncode(e.to_string())))?;
    Ok(png_buf)
}

/// Convert a linear-space color component to sRGB.
fn linear_to_srgb(c: f32) -> f32 {
    let c = c.clamp(0.0, 1.0);
    if c <= 0.0031308 {
        c * 12.92
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

const TEXTURE_BASE: &str = "content/gameplay/common/camouflage/textures";

/// Load raw DDS bytes from an absolute VFS path.
pub fn load_dds_from_vfs(vfs: &vfs::VfsPath, path: &str) -> Option<Vec<u8>> {
    let mut data = Vec::new();
    let mut file = vfs.join(path).ok()?.open_file().ok()?;
    std::io::Read::read_to_end(&mut file, &mut data).ok()?;
    if data.is_empty() { None } else { Some(data) }
}

/// Recognize the BC7Prep container observed in Korabli's streamed BC7 DDS.
/// This is identification only, NOT an inverse-transform implementation.
/// The 48-byte header stores magic, flags and ten mode counts. Modes 0..8
/// have 16 bytes/block, mode 9 stores solid RGBA in four bytes/block, followed
/// by a packed four-bit mode stream. Check all sizes before classifying it.
pub fn bc7prep_block_count(data: &[u8]) -> Option<u64> {
    let u32_at = |offset: usize| -> Option<u32> {
        Some(u32::from_le_bytes(data.get(offset..offset + 4)?.try_into().ok()?))
    };
    if data.get(..4)? != b"DDS " || u32_at(4)? != 124
        || data.get(84..88)? != b"DX10"
        || !matches!(u32_at(128)?, 98 | 99)
        || u32_at(148)? != 0x7bc {
        return None;
    }
    let mut counts = [0u64; 10];
    for (mode, count) in counts.iter_mut().enumerate() {
        *count = u32_at(156 + mode * 4)? as u64;
    }
    let blocks: u64 = counts.iter().sum();
    let width = u32_at(16)? as u64;
    let height = u32_at(12)? as u64;
    // Only the single-top-mip, single-layer 2D layout verified locally.
    if width == 0 || height == 0 || u32_at(28)? > 1
        || u32_at(132)? != 3 || u32_at(136)? != 0 || u32_at(140)? != 1
        || blocks != width.div_ceil(4) * height.div_ceil(4) {
        return None;
    }
    let payload = counts[..9].iter().sum::<u64>() * 16
        + counts[9] * 4 + blocks.div_ceil(2);
    (data.len() as u64 == 196 + payload).then_some(blocks)
}

/// Normalize a streamed DDS chunk into a standalone top-mip image.
///
/// A few archives omit a very small alignment tail while keeping the complete
/// dimensions in the DDS header. Only repair that bounded alignment case.
/// BC7Prep needs an inverse transform, not padding. Other substantially short
/// data is also rejected, so callers can try another standalone mip.
fn normalize_streamed_dds(mut data: Vec<u8>) -> Option<Vec<u8>> {
    if bc7prep_block_count(&data).is_some() {
        match super::bc7prep::decode_dds(&data) {
            Ok(decoded) => return Some(decoded),
            Err(reason) => {
        static WARNED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        if !WARNED.swap(true, std::sync::atomic::Ordering::Relaxed) {
            eprintln!("Texture compatibility: unsupported BC7Prep layout ({reason}); trying standalone mip fallbacks. High-resolution detail may be reduced.");
        }
        return None;
            }
        }
    }
    let dds = image_dds::ddsfile::Dds::read(&mut Cursor::new(&data)).ok()?;
    if image_dds::image_from_dds(&dds, 0).is_ok() {
        return Some(data);
    }

    let header_size = data.len().checked_sub(dds.data.len())?;
    let main_size = dds.get_main_texture_size()? as usize;
    let expected_size = header_size.checked_add(main_size)?;
    if data.len() >= expected_size {
        return None;
    }

    let missing = expected_size - data.len();
    const MAX_ALIGNMENT_TAIL: usize = 64 * 1024;
    if main_size == 0 || missing > MAX_ALIGNMENT_TAIL || missing > main_size / 128 {
        return None;
    }

    data.resize(expected_size, 0);
    let normalized = image_dds::ddsfile::Dds::read(&mut Cursor::new(&data)).ok()?;
    image_dds::image_from_dds(&normalized, 0).ok()?;
    Some(data)
}

#[cfg(test)]
mod tests {
    fn prep_fixture(solid: bool) -> Vec<u8> {
        let mut bytes = vec![0u8; 196 + if solid { 5 } else { 17 }];
        bytes[..4].copy_from_slice(b"DDS ");
        bytes[84..88].copy_from_slice(b"DX10");
        for (offset, value) in [(4, 124u32), (12, 4), (16, 4), (128, 98),
            (132, 3), (140, 1), (148, 0x7bc), (156 + if solid { 36 } else { 0 }, 1)] {
            bytes[offset..offset+4].copy_from_slice(&value.to_le_bytes());
        }
        bytes
    }

    #[test]
    fn bc7prep_recognizes_both_short_and_larger_than_raw_payloads() {
        for solid in [false, true] {
            let bytes = prep_fixture(solid);
            assert_eq!(super::bc7prep_block_count(&bytes), Some(1));
            // A prep payload larger than a raw BC7 block can otherwise pass
            // an ordinary DDS decoder and silently produce invalid pixels.
            assert!(super::normalize_streamed_dds(bytes).is_none());
        }
    }

    #[test]
    fn bc7prep_identification_checks_dimensions_lengths_and_format() {
        let bytes = prep_fixture(false);
        for length in [0, 147, 195, bytes.len()-1] {
            assert_eq!(super::bc7prep_block_count(&bytes[..length]), None);
        }
        for (offset, value) in [(16, 8u32), (128, 71), (140, 2), (148, 0), (156, 2)] {
            let mut invalid = bytes.clone();
            invalid[offset..offset+4].copy_from_slice(&value.to_le_bytes());
            assert_eq!(super::bc7prep_block_count(&invalid), None);
        }
    }

    #[test]
    fn authored_texture_candidates_preserve_intermediate_mips() {
        assert_eq!(super::authored_texture_candidates("textures/hull_art.dds"),
            ["textures/hull_art.dd0", "textures/hull_art.dd1",
             "textures/hull_art.dd2", "textures/hull_art.dds"]);
        assert_eq!(super::authored_texture_candidates("textures/hull_art.dd1"),
            ["textures/hull_art.dd1"]);
    }

    #[test]
    fn repeat_colour_sampler_interpolates_and_wraps() {
        let image = image_dds::image::RgbaImage::from_raw(2, 1,
            vec![0,0,0,255, 200,100,50,255]).unwrap();
        assert_eq!(super::sample_wrapped(&image, 0.25, 0.5), [0,0,0,255]);
        assert_eq!(super::sample_wrapped(&image, 0.5, 0.5), [100,50,25,255]);
        assert_eq!(super::sample_wrapped(&image, 0.0, 0.5), [100,50,25,255]);
        assert_eq!(super::sample_wrapped(&image, -0.25, 0.5), [200,100,50,255]);
    }
    use super::*;

    #[test]
    fn material_texture_references_use_typed_slots() {
        let mut record = vec![0u8; 240];
        record[0..4].copy_from_slice(&3u32.to_le_bytes());
        record[0x18..0x20].copy_from_slice(&136i64.to_le_bytes());
        record[0x20..0x28].copy_from_slice(&148i64.to_le_bytes());
        record[0x50..0x58].copy_from_slice(&176i64.to_le_bytes());
        for (i, name) in [11u32, 22, 33].into_iter().enumerate() {
            record[136+i*4..140+i*4].copy_from_slice(&name.to_le_bytes());
        }
        // Slot 1 texture, scalar (not texture), slot 0 texture.
        for (i, desc) in [0x14u16, 0x01, 0x04].into_iter().enumerate() {
            record[148+i*2..150+i*2].copy_from_slice(&desc.to_le_bytes());
        }
        record[176..184].copy_from_slice(&100u64.to_le_bytes());
        record[184..192].copy_from_slice(&200u64.to_le_bytes());
        assert_eq!(material_texture_ids(&record), Some(vec![(11,200),(33,100)]));
        assert!(material_texture_ids(&record[..160]).is_none());
    }

    fn rgba_png(width: u32, height: u32, pixels: &[u8]) -> Vec<u8> {
        let mut png = Vec::new();
        PngEncoder::new(&mut png)
            .write_image(pixels, width, height, ExtendedColorType::Rgba8)
            .expect("test PNG encoding should succeed");
        png
    }

    #[test]
    fn camouflage_composite_linearly_filters_a_smaller_mask() {
        let base = rgba_png(4, 1, &[
            128, 128, 128, 255,
            128, 128, 128, 255,
            128, 128, 128, 255,
            128, 128, 128, 255,
        ]);
        let camo = rgba_png(2, 1, &[0, 0, 0, 255, 255, 255, 255, 255]);
        let result = composite_tiled_camo_png(&base, &camo, [1.0, 1.0, 0.0, 0.0])
            .expect("camouflage composite should succeed");
        let decoded = image_dds::image::load_from_memory(&result)
            .expect("result should be a valid PNG")
            .to_rgba8();

        assert_eq!(decoded.get_pixel(0, 0).0, [64, 64, 64, 255]);
        assert_eq!(decoded.get_pixel(1, 0).0, [64, 64, 64, 255]);
        assert_eq!(decoded.get_pixel(2, 0).0, [191, 191, 191, 255]);
        assert_eq!(decoded.get_pixel(3, 0).0, [191, 191, 191, 255]);
    }

    #[test]
    fn tiled_camouflage_preserves_base_detail_and_alpha() {
        let base = rgba_png(2, 1, &[128, 64, 255, 200, 32, 200, 96, 77]);
        let camo = rgba_png(1, 1, &[64, 128, 32, 128]);
        let result = composite_tiled_camo_png(&base, &camo, [1.0, 1.0, 0.0, 0.0])
            .expect("camouflage composite should succeed");
        let decoded = image_dds::image::load_from_memory(&result)
            .expect("result should be a valid PNG")
            .to_rgba8();

        assert_eq!(decoded.dimensions(), (2, 1));
        assert_eq!(decoded.get_pixel(0, 0).0, [96, 64, 159, 200]);
        assert_eq!(decoded.get_pixel(1, 0).0, [24, 200, 60, 77]);
    }
}
/// MFM name suffixes that don't appear in texture filenames.
///
/// E.g. MFM `AGM034_16in50_Mk7_skinned.mfm` → texture `AGM034_16in50_Mk7_camo_01.dds`.
const MFM_STRIP_SUFFIXES: &[&str] = &["_skinned", "_wire", "_dead", "_blaze", "_alpha"];

/// Derive texture base names from an MFM stem.
///
/// Returns the original stem first, then the stem with known MFM-only suffixes
/// stripped (e.g. `_skinned`). This allows matching both hull-style stems
/// (where `JSB039_Yamato_1945_Hull` IS the texture name) and turret-style stems
/// (where `AGM034_16in50_Mk7_skinned` maps to `AGM034_16in50_Mk7`).
pub fn texture_base_names(mfm_stem: &str) -> Vec<String> {
    let mut names = vec![mfm_stem.to_string()];
    for suffix in MFM_STRIP_SUFFIXES {
        if let Some(stripped) = mfm_stem.strip_suffix(suffix)
            && !names.contains(&stripped.to_string())
        {
            names.push(stripped.to_string());
        }
    }
    names
}

/// Texture channel suffixes that indicate a multi-channel camo scheme.
///
/// When a scheme is discovered as e.g. `GW_a`, the `_a` suffix means it's the albedo
/// channel of scheme `GW`. The `_mg` and `_mgn` suffixes are metallic/gloss channels.
/// These are stripped during discovery to group channels into a single scheme.
const TEXTURE_CHANNEL_SUFFIXES: &[&str] = &["_a", "_mg", "_mgn"];

/// Load the albedo texture for a given MFM stem and camo scheme from the VFS.
///
/// Given an MFM leaf like `JSB039_Yamato_1945_Hull` and scheme like `GW`,
/// tries multiple naming conventions in order:
/// 1. `{stem}_{scheme}_a.dd0/dds` — explicit albedo channel (e.g. `Hull_GW_a.dds`)
/// 2. `{stem}_{scheme}.dd0/dds` — direct replacement (e.g. `Hull_camo_01.dds`)
///
/// Also tries with known MFM suffixes stripped (e.g. `_skinned`) to handle
/// turret models where the texture name differs from the MFM name.
///
/// Returns `(base_name, dds_bytes)` if found, or `None`.
pub fn load_texture_bytes(
    vfs: &vfs::VfsPath,
    mfm_stem: &str,
    scheme: &str,
) -> Option<(String, Vec<u8>)> {
    for base in texture_base_names(mfm_stem) {
        // Try explicit albedo channel first ({base}_{scheme}_a), then direct ({base}_{scheme}).
        let candidates = [
            format!("{TEXTURE_BASE}/{base}_{scheme}_a.dd0"),
            format!("{TEXTURE_BASE}/{base}_{scheme}_a.dds"),
            format!("{TEXTURE_BASE}/{base}_{scheme}.dd0"),
            format!("{TEXTURE_BASE}/{base}_{scheme}.dds"),
        ];

        for path in &candidates {
            if let Ok(vfs_path) = vfs.join(path)
                && let Ok(mut file) = vfs_path.open_file()
            {
                let mut data = Vec::new();
                if std::io::Read::read_to_end(&mut file, &mut data).is_ok()
                    && let Some(data) = normalize_streamed_dds(data)
                {
                    return Some((base, data));
                }
            }
        }
    }

    None
}

/// Load the base albedo texture for a hull mesh from the VFS.
///
/// The base albedo is the "default" ship appearance — gray/weathered paint without
/// any camouflage applied. Textures live in a `textures/` sibling directory next to
/// the ship folder, e.g.:
/// `content/gameplay/japan/ship/battleship/textures/JSB039_Yamato_1945_Hull_a.dd0`
///
/// Uses the resolved `_a` colour mip for indexed Ships 2.0 materials and the
/// high-resolution `_art`/legacy `_a` families for ordinary materials.
/// Falls back to the MFM's own directory.
///
/// `mfm_full_path` is the full VFS path to the MFM file (e.g. ending in `.mfm`).
/// Returns DDS bytes if found.
pub fn load_base_albedo_bytes(vfs: &vfs::VfsPath, mfm_full_path: &str) -> Option<Vec<u8>> {
    let dir = mfm_full_path.rsplit_once('/')?.0;
    let mfm_filename = mfm_full_path.rsplit_once('/')?.1;
    let stem = mfm_filename.strip_suffix(".mfm")?;

    // The textures/ sibling directory: go up from the ship dir to the species dir,
    // then into textures/. E.g. .../cruiser/JSC010_Mogami_1944/ -> .../cruiser/textures/
    let tex_sibling_dir = dir
        .rsplit_once('/')
        .map(|(parent, _)| format!("{parent}/textures"));

    // Search directories: textures/ sibling, MFM's dir, and TILED/ subdirectory
    // (underwater TILEDLAND materials store textures in a TILED/ subdirectory).
    let tiled_subdir = format!("{dir}/TILED");

    for base in texture_base_names(stem) {
        // Ships 2.0 uses `_art` as an indexed shader input rather than display
        // colour. Feeding it directly to ordinary PBR viewers produces the
        // black/gold/channel-mask appearance. Presence of `_matid` identifies
        // that layout; use the game's resolved `_a` mip as the colour-correct
        // fallback until the indexed runtime shader can be reproduced exactly.
        let indexed_material = [
            tex_sibling_dir.as_deref(),
            Some(dir),
            Some(tiled_subdir.as_str()),
        ]
        .into_iter()
        .flatten()
        .any(|candidate_dir| {
            ["dd0", "dds"].into_iter().any(|extension| {
                let path = format!("{candidate_dir}/{base}_matid.{extension}");
                vfs.join(&path)
                    .ok()
                    .and_then(|item| item.exists().ok())
                    .unwrap_or(false)
            })
        });
        let albedo_suffixes = if indexed_material {
            ["_a", "_art", "_od"]
        } else {
            ["_art", "_a", "_od"]
        };
        // Build candidate paths: prefer dd0 (high-res) over dds (low-res mip tail).
        let mut candidates = Vec::new();
        for suffix in &albedo_suffixes {
            if let Some(tex_dir) = &tex_sibling_dir {
                candidates.push(format!("{tex_dir}/{base}{suffix}.dd0"));
                candidates.push(format!("{tex_dir}/{base}{suffix}.dds"));
            }
            candidates.push(format!("{dir}/{base}{suffix}.dd0"));
            candidates.push(format!("{dir}/{base}{suffix}.dds"));
            candidates.push(format!("{tiled_subdir}/{base}{suffix}.dd0"));
            candidates.push(format!("{tiled_subdir}/{base}{suffix}.dds"));
        }

        for path in &candidates {
            if let Ok(vfs_path) = vfs.join(path)
                && let Ok(mut file) = vfs_path.open_file()
            {
                let mut data = Vec::new();
                if std::io::Read::read_to_end(&mut file, &mut data).is_ok()
                    && let Some(data) = normalize_streamed_dds(data)
                {
                    return Some(data);
                }
            }
        }
    }

    None
}

/// Strip texture channel suffixes (`_a`, `_mg`, `_mgn`) from a raw scheme name.
///
/// E.g. `GW_a` → `GW`, `camo_01` → `camo_01` (no channel suffix).
fn strip_channel_suffix(scheme: &str) -> &str {
    for suffix in TEXTURE_CHANNEL_SUFFIXES {
        if let Some(stripped) = scheme.strip_suffix(suffix)
            && !stripped.is_empty()
        {
            return stripped;
        }
    }
    scheme
}

/// Discover available texture schemes for a set of MFM stems by scanning the VFS.
///
/// Multi-channel schemes (e.g. `GW_a` + `GW_mg`) are grouped into a single scheme
/// name (`GW`). Returns sorted, deduplicated scheme names.
pub fn discover_texture_schemes(vfs: &vfs::VfsPath, mfm_stems: &[String]) -> Vec<String> {
    let mut schemes = std::collections::BTreeSet::new();

    let Ok(tex_dir) = vfs.join(TEXTURE_BASE) else {
        return Vec::new();
    };
    let Ok(entries) = tex_dir.read_dir() else {
        return Vec::new();
    };

    // Collect filenames ending in .dds (base mip level — avoids counting .dd0/.dd1/.dd2 dupes).
    let dds_names: Vec<String> = entries
        .filter_map(|entry| {
            let name = entry.filename();
            if name.ends_with(".dds") {
                Some(name)
            } else {
                None
            }
        })
        .collect();

    for stem in mfm_stems {
        for base in texture_base_names(stem) {
            let prefix = format!("{base}_");
            for name in &dds_names {
                if let Some(rest) = name.strip_prefix(&prefix)
                    && let Some(raw_scheme) = rest.strip_suffix(".dds")
                    && !raw_scheme.is_empty()
                {
                    let scheme = strip_channel_suffix(raw_scheme);
                    schemes.insert(scheme.to_string());
                }
            }
        }
    }

    schemes.into_iter().collect()
}
