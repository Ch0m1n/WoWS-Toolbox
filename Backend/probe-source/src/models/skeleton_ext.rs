//! Verified legacy SkeletonExtender layout: 0x20-byte record, parallel name,
//! parent-name and column-major local transform arrays. Only its known checksum
//! is accepted by callers; these nodes supplement, rather than replace, visuals.
pub const ITEM_SIZE: usize = 0x20;
pub const MAGIC: u32 = 0x1AE0_23FF;
pub const CHECKSUM: u32 = 0x5B23_D7B9;

#[derive(Debug, Clone)]
pub struct ExtensionNode {
    pub name_id: u32,
    pub parent_name_id: u32,
    pub transform: [f32; 16],
}

pub fn parse_extension(data: &[u8]) -> Result<Vec<ExtensionNode>, &'static str> {
    if data.len() < ITEM_SIZE { return Err("short skeleton extension record"); }
    let count = u16::from_le_bytes(data[2..4].try_into().unwrap()) as usize;
    if count == 0 { return Ok(Vec::new()); }
    let array = |field: usize, stride: usize| -> Result<&[u8], &'static str> {
        let pointer = i64::from_le_bytes(data[field..field+8].try_into().unwrap());
        let start = usize::try_from(pointer).map_err(|_| "negative skeleton extension pointer")?;
        if start < ITEM_SIZE { return Err("skeleton extension array overlaps header"); }
        let end = start.checked_add(count.checked_mul(stride).ok_or("array size overflow")?).ok_or("array offset overflow")?;
        data.get(start..end).ok_or("skeleton extension array out of bounds")
    };
    let names = array(8, 4)?;
    let parents = array(16, 4)?;
    let matrices = array(24, 64)?;
    let mut nodes = Vec::with_capacity(count);
    for i in 0..count {
        let mut transform = [0.0; 16];
        for (j, value) in transform.iter_mut().enumerate() {
            let offset = i * 64 + j * 4;
            *value = f32::from_le_bytes(matrices[offset..offset+4].try_into().unwrap());
        }
        if !transform.iter().all(|value| value.is_finite()) { return Err("non-finite skeleton transform"); }
        if [3,7,11].iter().any(|&j| transform[j].abs() > 0.0001) || (transform[15]-1.0).abs() > 0.0001 {
            return Err("non-affine skeleton transform");
        }
        nodes.push(ExtensionNode {
            name_id:u32::from_le_bytes(names[i*4..i*4+4].try_into().unwrap()),
            parent_name_id:u32::from_le_bytes(parents[i*4..i*4+4].try_into().unwrap()),
            transform,
        });
    }
    Ok(nodes)
}

/// Compose extension ancestry with a parent that already exists in the visual.
/// Cycles and unresolved parents are rejected, never placed at an invented origin.
pub fn resolve_world_transform<F>(node: usize, nodes: &[ExtensionNode], visual: F) -> Option<[f32;16]>
where F: Fn(u32) -> Option<[f32;16]> {
    let mut current = node;
    let mut visited = std::collections::HashSet::new();
    let mut result = nodes.get(current)?.transform;
    loop {
        if !visited.insert(current) { return None; }
        let parent = nodes.get(current)?.parent_name_id;
        if let Some(matrix) = visual(parent) { return Some(multiply(&matrix, &result)); }
        current = nodes.iter().position(|candidate| candidate.name_id == parent)?;
        result = multiply(&nodes[current].transform, &result);
    }
}

fn multiply(a: &[f32;16], b: &[f32;16]) -> [f32;16] {
    let mut result = [0.0;16];
    for col in 0..4 { for row in 0..4 {
        for k in 0..4 { result[col*4+row] += a[k*4+row]*b[col*4+k]; }
    }}
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn translation(x:f32,y:f32,z:f32) -> [f32;16] {
        [1.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,0.0,1.0,0.0,x,y,z,1.0]
    }
    fn fixture() -> Vec<u8> {
        let mut data = vec![0u8; ITEM_SIZE+4+4+64];
        data[2..4].copy_from_slice(&1u16.to_le_bytes());
        for (offset,pointer) in [(8,32i64),(16,36),(24,40)] { data[offset..offset+8].copy_from_slice(&pointer.to_le_bytes()); }
        data[32..36].copy_from_slice(&101u32.to_le_bytes());
        data[36..40].copy_from_slice(&201u32.to_le_bytes());
        for (i,value) in translation(1.0,2.0,3.0).iter().enumerate() { data[40+i*4..44+i*4].copy_from_slice(&value.to_le_bytes()); }
        data
    }
    #[test] fn reads_verified_parallel_arrays_and_preserves_translation() {
        let nodes=parse_extension(&fixture()).unwrap();
        assert_eq!((nodes[0].name_id,nodes[0].parent_name_id),(101,201));
        assert_eq!(nodes[0].transform,translation(1.0,2.0,3.0));
    }
    #[test] fn rejects_truncation_bad_pointers_and_invalid_matrices() {
        for size in [0,31,40,103] { assert!(parse_extension(&fixture()[..size]).is_err()); }
        let mut data=fixture();data[8..16].copy_from_slice(&(-1i64).to_le_bytes());assert!(parse_extension(&data).is_err());
        let mut data=fixture();data[40..44].copy_from_slice(&f32::NAN.to_le_bytes());assert!(parse_extension(&data).is_err());
        let mut data=fixture();data[100..104].copy_from_slice(&0.0f32.to_le_bytes());assert!(parse_extension(&data).is_err());
    }
    #[test] fn composes_extension_parent_and_visual_parent_without_double_translation() {
        let nodes=[ExtensionNode{name_id:1,parent_name_id:10,transform:translation(2.0,0.0,0.0)}, ExtensionNode{name_id:2,parent_name_id:1,transform:translation(0.0,3.0,0.0)}];
        assert_eq!(resolve_world_transform(1,&nodes,|id| (id==10).then(||translation(0.0,0.0,4.0))),Some(translation(2.0,3.0,4.0)));
        assert_eq!(resolve_world_transform(1,&nodes,|_|None),None);
    }
    #[test] fn rejects_cyclic_extension_ancestry() {
        let nodes=[ExtensionNode{name_id:1,parent_name_id:2,transform:translation(0.0,0.0,0.0)},ExtensionNode{name_id:2,parent_name_id:1,transform:translation(0.0,0.0,0.0)}];
        assert_eq!(resolve_world_transform(0,&nodes,|_|None),None);
    }
}
