//! Independently inferred Korabli BC7Prep decoder. Unsupported layouts fail closed.



const WEIGHTS: [u32; 16] = [0,4,9,13,17,21,26,30,34,38,43,47,51,55,60,64];

fn solid(c: &[u8], transformed: bool) -> [u8; 4] {
    if transformed {
        [c[0].wrapping_add(c[1]), c[0], c[0].wrapping_add(c[2]), c[3]]
    } else { [c[0], c[1], c[2], c[3]] }
}

fn mode6(endpoints: &[u8], indices: &[u8], transformed: bool) -> [[u8; 4]; 16] {
    let mut ends = [[0u32; 4]; 2];
    for i in 0..2 {
        let c = &endpoints[i*4..i*4+4];
        let p = (c[3] >> 7) as u32;
        let rgb = if transformed {
            [c[0].wrapping_add(c[1]), c[0], c[0].wrapping_add(c[2])]
        } else { [c[0], c[1], c[2]] };
        for j in 0..3 { ends[i][j] = ((rgb[j] & 127) as u32)*2+p; }
        ends[i][3] = ((c[3] & 127) as u32)*2+p;
    }
    let mut out = [[0u8; 4]; 16];
    for (pixel, value) in out.iter_mut().enumerate() {
        let index = ((indices[pixel/2] >> ((pixel%2)*4)) & 15) as usize;
        let w = WEIGHTS[index];
        for channel in 0..4 {
            value[channel] = (((64-w)*ends[0][channel]+w*ends[1][channel]+32)/64) as u8;
        }
    }
    out
}

// Unverified mode-5 hypothesis. Explicit opt-in only: mip-average scores
// cannot yet distinguish all index/endpoint variants reliably.
fn mode5_candidate(endpoints: &[u8], indices: &[u8], transformed: bool) -> [[u8;4];16] {
    let a = solid(&endpoints[..4], transformed);
    let b = solid(&endpoints[4..8], transformed);
    let colour = u32::from_le_bytes(indices[..4].try_into().unwrap());
    let alpha = u32::from_le_bytes(indices[4..8].try_into().unwrap());
    let weights = [0u32,21,43,64];
    std::array::from_fn(|pixel| std::array::from_fn(|channel| {
        let bits = if channel == 3 {alpha} else {colour};
        let w = weights[((bits >> (pixel*2)) & 3) as usize];
        (((64-w)*a[channel] as u32+w*b[channel] as u32+32)/64) as u8
    }))
}

pub fn decode(data: &[u8], mode5_enabled: bool) -> Result<(u32, u32, Vec<u8>, Vec<u8>, [u64; 10]), String> {
    decode_options(data, mode5_enabled, false)
}

// Independently inferred mode-4 routing candidate. Requires explicit opt-in.
fn mode4_candidate(endpoints: &[u8], indices: &[u8]) -> Result<[[u8;4];16], String> {
    if endpoints.len()!=6 || indices.len()!=10 { return Err("mode4 input length".into()); }
    let p=endpoints.iter().enumerate().fold(0u64, |v,(i,b)| v|((*b as u64)<<(8*i)));
    if p>>43 != 0 { return Err("unverified mode4 endpoint bits".into()); }
    let ix=indices.iter().enumerate().fold(0u128, |v,(i,b)| v|((*b as u128)<<(8*i)));
    let tag=(ix&3) as usize;
    let separate=if tag==0 {3} else {tag-1};
    let mut ends=[[0u32;4];2];
    for k in 0..2 {
        let g=((p>>(2+k*5))&31) as u32;
        let dr=((p>>(12+k*5))&31) as u32;
        let db=((p>>(22+k*5))&31) as u32;
        let raw=[(g+dr)&31,g,(g+db)&31,((p>>(32+k*5))&31) as u32];
        for ch in 0..4 { ends[k][ch]=(raw[ch]<<3)|(raw[ch]>>2); }
        let six=(raw[separate]<<1)|((p>>k)&1) as u32;
        ends[k][separate]=(six<<2)|(six>>4);
    }
    let w2=[0u32,21,43,64];
    let w3=[0u32,9,18,27,37,46,55,64];
    Ok(std::array::from_fn(|j| {
        let two=if j==0 {(ix>>2)&1} else {(ix>>(1+2*j))&3} as usize;
        let three=if j==0 {(ix>>33)&3} else {(ix>>(32+3*j))&7} as usize;
        std::array::from_fn(|ch| {
            let use_three=(p&(1<<42)!=0) != (ch==separate);
            let w=if use_three {w3[three]} else {w2[two]};
            (((64-w)*ends[0][ch]+w*ends[1][ch]+32)/64) as u8
        })
    }))
}

pub fn decode_options(data: &[u8], mode5_enabled: bool, mode4_enabled: bool) -> Result<(u32, u32, Vec<u8>, Vec<u8>, [u64; 10]), String> {
    decode_extended(data, mode5_enabled, mode4_enabled, false)
}

fn mode5_routed(endpoints: &[u8], indices: &[u8]) -> [[u8;4];16] {
    let ix=u64::from_le_bytes(indices.try_into().unwrap());
    let tag=(ix&3) as usize;let separate=if tag==0 {3} else {tag-1};
    let mut ends=[[0u32;4];2];
    for k in 0..2 {
        let raw=solid(&endpoints[k*4..k*4+4],true);
        for ch in 0..4 {
            let v=(raw[ch]>>1) as u32;
            ends[k][ch]=if ch==separate {raw[ch] as u32} else {(v<<1)|(v>>6)};
        }
    }
    let weights=[0u32,21,43,64];
    std::array::from_fn(|j|std::array::from_fn(|ch| {
        let independent=ch==separate;
        let (pos,mask)=if j==0 {(if independent {33} else {2},1)} else {(if independent {32+2*j} else {1+2*j},3)};
        let w=weights[((ix>>pos)&mask) as usize];
        (((64-w)*ends[0][ch]+w*ends[1][ch]+32)/64) as u8
    }))
}

// Reconstruct a standard BC7 mode-7 block; image_dds supplies the existing
// standard BC7 decoder and partition tables (no proprietary prep decoder).
fn mode7_candidate(endpoints: &[u8], indices: &[u8]) -> Result<[[u8;4];16],String> {
    if endpoints.len()!=12 || indices.len()!=4 {return Err("mode7 length".into());}
    let tail=u32::from_le_bytes(endpoints[8..12].try_into().unwrap());
    if tail&255!=0 {return Err("unverified mode7 padding".into());}
    let ix=u32::from_le_bytes(indices.try_into().unwrap());
    let partition=(tail>>28)|((ix&3)<<4);
    let mut packed=128u128|((partition as u128)<<8);
    let mut pos=14;
    let mut raw=[[0u32;4];4];let mut pbits=[0u32;4];
    for k in 0..4 {
        let v=u16::from_le_bytes(endpoints[k*2..k*2+2].try_into().unwrap()) as u32;
        let g=(v>>1)&31;let dr=(v>>6)&31;let db=(v>>11)&31;
        raw[k]=[(g+dr)&31,g,(g+db)&31,(tail>>(8+5*k))&31];pbits[k]=v&1;
    }
    for ch in 0..4 {for k in 0..4 {packed|=(raw[k][ch] as u128)<<pos;pos+=5;}}
    for p in pbits {packed|=(p as u128)<<pos;pos+=1;}
    packed|=((ix>>2) as u128)<<pos;
    standard_bc7(packed.to_le_bytes())
}

fn standard_bc7(bytes: [u8;16]) -> Result<[[u8;4];16],String> {
    let surface=image_dds::Surface {width:4,height:4,depth:1,layers:1,mipmaps:1,image_format:image_dds::ImageFormat::BC7RgbaUnorm,data:&bytes[..]};
    let rgba=surface.decode_rgba8().map_err(|e|format!("standard BC7 decode: {e:?}"))?;
    Ok(std::array::from_fn(|j|rgba.data[j*4..j*4+4].try_into().unwrap()))
}

pub fn decode_extended(data: &[u8], mode5_enabled: bool, mode4_enabled: bool, routed: bool) -> Result<(u32, u32, Vec<u8>, Vec<u8>, [u64; 10]), String> {
    decode_all(data,mode5_enabled,mode4_enabled,routed,false)
}

fn rare_candidate(mode: usize, d: &[u8]) -> Result<[[u8;4];16],String> {
    if d.len()!=16 {return Err("rare block length".into());}
    if mode==8 {return standard_bc7(d.try_into().unwrap());}
    let mut raw=[[0u32;3];6];let mut pbits=[0u32;6];
    let (partition,indices,nends,colour_bits,index_bits)=match mode {
        0 => {
            let ep=d[..10].iter().enumerate().fold(0u128,|v,(i,b)|v|((*b as u128)<<(8*i)));
            let ix=d[10..].iter().enumerate().fold(0u64,|v,(i,b)|v|((*b as u64)<<(8*i)));
            if ep&(1<<76)!=0 {return Err("unverified mode0 padding".into());}
            let ps=((ep>>77)&7) as u64|((ix&7)<<3);
            for k in 0..6 {
                let g=((ep>>(4*k))&15) as u32;let dr=((ep>>(24+4*k))&15) as u32;let db=((ep>>(48+4*k))&15) as u32;
                raw[k]=[(g+dr)&15,g,(g+db)&15];pbits[k]=((ps>>k)&1) as u32;
            }
            (((ep>>72)&15) as u32,ix>>3,6,4,45)
        },
        1 => {
            let tail=u16::from_le_bytes(d[8..10].try_into().unwrap()) as u32;
            if tail>>14!=0 {return Err("unverified mode1 padding".into());}
            let ix=d[10..].iter().enumerate().fold(0u64,|v,(i,b)|v|((*b as u64)<<(8*i)));
            for k in 0..4 {
                let v=u16::from_le_bytes(d[k*2..k*2+2].try_into().unwrap()) as u32;
                let g=v&63;let dr=(((v>>6)&31)<<1)|((tail>>(k*2))&1);let db=(((v>>11)&31)<<1)|((tail>>(k*2+1))&1);
                raw[k]=[(g+dr)&63,g,(g+db)&63];
            }
            pbits[0]=(ix&1) as u32;pbits[1]=((ix>>1)&1) as u32;
            ((tail>>8)&63,ix>>2,4,6,46)
        },
        2 => {
            let ix=u32::from_le_bytes(d[12..].try_into().unwrap());
            if ix&7!=0 {return Err("unverified mode2 padding".into());}
            let mut part=0;
            for k in 0..6 {
                let v=u16::from_le_bytes(d[k*2..k*2+2].try_into().unwrap()) as u32;
                let g=(v>>1)&31;let dr=(v>>6)&31;let db=(v>>11)&31;
                raw[k]=[(g+dr)&31,g,(g+db)&31];part|=(v&1)<<k;
            }
            (part,(ix>>3) as u64,6,5,29)
        },
        3 => {
            if d[8..12].iter().any(|v|v&128!=0) {return Err("unverified mode3 padding".into());}
            let ix=u32::from_le_bytes(d[12..].try_into().unwrap());let mut part=0;
            for k in 0..6 {part|=((d[k]>>7) as u32)<<k;}
            for k in 0..4 {
                let c=&d[k*3..k*3+3];raw[k]=[(c[0] as u32+c[1] as u32)&127,(c[0]&127) as u32,(c[0] as u32+c[2] as u32)&127];
                pbits[k]=if k<2 {(d[6+k]>>7) as u32} else {(ix>>(k-2))&1};
            }
            (part,(ix>>2) as u64,4,7,30)
        },
        _=>return Err("not a rare mode".into()),
    };
    let mut packed=1u128<<mode;let mut pos=mode+1;
    packed|=(partition as u128)<<pos;pos+=if mode==0 {4} else {6};
    for ch in 0..3 {for k in 0..nends {packed|=(raw[k][ch] as u128)<<pos;pos+=colour_bits;}}
    let np=if mode==2 {0} else if mode==1 {2} else {nends};
    for k in 0..np {packed|=(pbits[k] as u128)<<pos;pos+=1;}
    if pos+index_bits!=128 {return Err("rare bit layout".into());}
    packed|=(indices as u128)<<pos;
    standard_bc7(packed.to_le_bytes())
}

pub fn decode_all(data: &[u8], mode5_enabled: bool, mode4_enabled: bool, routed: bool, rare: bool) -> Result<(u32, u32, Vec<u8>, Vec<u8>, [u64; 10]), String> {
    let blocks = crate::export::texture::bc7prep_block_count(data)
        .ok_or("not a validated single-mip BC7Prep DDS")? as usize;
    let read = |p| u32::from_le_bytes(data[p..p+4].try_into().unwrap());
    let (width, height, flags) = (read(16), read(12), read(152));
    // Only observed flags: per-mode stream splitting and colour decorrelation.
    if flags & !0x103ff != 0 || flags & (1 << 9) != 0 {
        return Err(format!("unverified flags {flags:#x}"));
    }
    let counts: [usize; 10] = std::array::from_fn(|i| read(156+i*4) as usize);
    if routed && (flags&0x10000==0 || (counts[5]>0 && flags&(1<<5)==0) || (counts[7]>0 && flags&(1<<7)==0)) {
        return Err("routed candidates require observed split/decorrelated layout".into());
    }
    if mode4_enabled && counts[4]>0 && (flags&0x10000==0 || flags&(1<<4)==0) {
        return Err("mode4 candidate requires observed split/decorrelated layout".into());
    }
    let mut starts = [0usize; 10];
    let mut cursor = 196;
    for i in 0..10 {
        starts[i] = cursor;
        cursor += counts[i] * if i==9 {4} else {16};
    }
    let order = &data[cursor..];
    let mut histogram = [0usize; 10];
    for block in 0..blocks {
        let m = ((order[block/2] >> ((block%2)*4)) & 15) as usize;
        if m >= 10 { return Err("invalid mode index".into()); }
        histogram[m] += 1;
    }
    if histogram != counts { return Err("mode stream disagrees with header counts".into()); }
    let pixels = (width as usize).checked_mul(height as usize).ok_or("dimensions overflow")?;
    if pixels > 8192*8192 { return Err("experimental decoder size limit exceeded".into()); }
    let mut rgba = vec![0u8; pixels*4];
    let mut coverage = vec![0u8; pixels];
    let mut seen = [0usize; 10];
    let mut decoded = [0u64; 10];
    let transformed = flags & 0x10000 != 0;
    let bw = width.div_ceil(4) as usize;
    for block in 0..blocks {
        let m = ((order[block/2] >> ((block%2)*4)) & 15) as usize;
        let i = seen[m];
        seen[m] += 1;
        let values = match m {
            0..=3 | 8 if rare => {
                let stride=if m<2 {8} else if m<4 {12} else {16};
                let mut bytes=[0u8;16];
                if flags&(1<<m)!=0 {
                    let a=starts[m]+i*stride;let b=starts[m]+counts[m]*stride+i*(16-stride);
                    bytes[..stride].copy_from_slice(&data[a..a+stride]);bytes[stride..].copy_from_slice(&data[b..b+16-stride]);
                } else {bytes.copy_from_slice(&data[starts[m]+i*16..starts[m]+(i+1)*16]);}
                rare_candidate(m,&bytes)?
            },
            7 if routed => {
                let a=starts[7]+i*12;let b=starts[7]+counts[7]*12+i*4;
                mode7_candidate(&data[a..a+12],&data[b..b+4])?
            },
            4 if mode4_enabled => {
                let a=starts[4]+i*6;
                let b=starts[4]+counts[4]*6+i*10;
                mode4_candidate(&data[a..a+6], &data[b..b+10])?
            },
            9 => [solid(&data[starts[9]+i*4..starts[9]+i*4+4], transformed); 16],
            5 if mode5_enabled => {
                let (a,b) = if flags & (1<<5) != 0 {
                    (starts[5]+i*8, starts[5]+counts[5]*8+i*8)
                } else { (starts[5]+i*16, starts[5]+i*16+8) };
                if routed {mode5_routed(&data[a..a+8],&data[b..b+8])} else {mode5_candidate(&data[a..a+8], &data[b..b+8], transformed)}
            },
            6 => {
                let (a,b) = if flags & (1<<6) != 0 {
                    (starts[6]+i*8, starts[6]+counts[6]*8+i*8)
                } else { (starts[6]+i*16, starts[6]+i*16+8) };
                mode6(&data[a..a+8], &data[b..b+8], transformed)
            },
            _ => continue,
        };
        decoded[m] += 1;
        for (p, colour) in values.iter().enumerate() {
            let (x,y) = ((block%bw)*4+p%4, (block/bw)*4+p/4);
            if x < width as usize && y < height as usize {
                let dest = y*width as usize+x;
                rgba[dest*4..dest*4+4].copy_from_slice(colour);
                // 128 is a lower-confidence mode-5 hypothesis, not the same
                // status as the original mode-6/9 experimental reconstruction.
                coverage[dest] = if m<=3 || m==8 {64} else if m == 7 {224} else if m == 4 {192} else if m == 5 {128} else {255};
            }
        }
    }
    Ok((width, height, rgba, coverage, decoded))
}

pub fn decode_dds(data: &[u8]) -> Result<Vec<u8>, String> {
    crate::export::texture::bc7prep_block_count(data).ok_or("invalid BC7Prep container")?;
    if u32::from_le_bytes(data[188..192].try_into().unwrap()) != 0 {
        return Err("mode8 is not validated for release".into());
    }
    let (width,height,rgba,coverage,counts)=decode_all(data,true,true,true,true)?;
    if coverage.contains(&0) || counts.iter().sum::<u64>() != width.div_ceil(4) as u64*height.div_ceil(4) as u64 {
        return Err("incomplete BC7Prep reconstruction".into());
    }
    let surface=image_dds::Surface{width,height,depth:1,layers:1,mipmaps:1,
        image_format:if u32::from_le_bytes(data[128..132].try_into().unwrap())==99 {
            image_dds::ImageFormat::Rgba8UnormSrgb
        } else {image_dds::ImageFormat::Rgba8Unorm}, data:rgba.as_slice()};
    let dds=surface.to_dds().map_err(|e|format!("{e:?}"))?;
    let mut out=std::io::Cursor::new(Vec::new());
    dds.write(&mut out).map_err(|e|format!("{e:?}"))?;
    Ok(out.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn release_dds_preserves_pixels_dimensions_and_srgb() {
        for format in [98u32,99] {
            let mut input=solid_dds();input[128..132].copy_from_slice(&format.to_le_bytes());
            let bytes=decode_dds(&input).unwrap();
            let dds=image_dds::ddsfile::Dds::read(&mut std::io::Cursor::new(&bytes)).unwrap();
            let image=image_dds::image_from_dds(&dds,0).unwrap();
            assert_eq!(image.dimensions(),(8,4));
            assert_eq!(image.as_raw(),&decode(&input,false).unwrap().2);
            assert_eq!(u32::from_le_bytes(bytes[128..132].try_into().unwrap()),if format==99 {29} else {28});
        }
    }
    #[test]
    fn release_rejects_unknown_mode8_and_truncated_containers() {
        let mut input=solid_dds();
        for len in 0..input.len() {assert!(decode_dds(&input[..len]).is_err());}
        input.resize(229,0);input[192..196].fill(0);input[188..192].copy_from_slice(&2u32.to_le_bytes());input[228]=0x88;
        assert!(decode_dds(&input).unwrap_err().contains("mode8"));
    }
    #[test]
    fn rare_modes_constant_endpoints_all_partitions() {
        for mode in 0..4 {
            for part in 0..if mode==0 {16} else {64} {
                let mut d=[0u8;16];
                let expected=match mode {
                    0=>{
                        let ep=(0..6).fold(0u128,|v,k|v|(8u128<<(4*k)))|((part as u128)<<72);
                        d[..10].copy_from_slice(&ep.to_le_bytes()[..10]);132
                    },
                    1=>{
                        for k in 0..4 {d[k*2..k*2+2].copy_from_slice(&32u16.to_le_bytes());}
                        d[8..10].copy_from_slice(&((part as u16)<<8).to_le_bytes());129
                    },
                    2=>{
                        for k in 0..6 {d[k*2..k*2+2].copy_from_slice(&(32u16|(((part>>k)&1) as u16)).to_le_bytes());}132
                    },
                    _=>{
                        for k in 0..4 {d[k*3]=64;}
                        for k in 0..6 {d[k]|=(((part>>k)&1) as u8)<<7;}128
                    }
                };
                assert_eq!(rare_candidate(mode,&d).unwrap(),[[expected,expected,expected,255];16],"mode {mode} partition {part}");
            }
        }
    }
    #[test]
    fn rare_modes_reject_unobserved_padding() {
        for (mode,byte,bit) in [(0,9,4),(1,9,6),(2,12,0),(3,8,7)] {
            let mut d=[0u8;16];d[byte]=1<<bit;
            assert!(rare_candidate(mode,&d).is_err());
        }
        assert!(rare_candidate(0,&[0;15]).is_err());
    }
    #[test]
    fn rare_mode2_varying_endpoints_indices_and_modular_differences() {
        for part in 0..64u32 {
            let mut d=[0u8;16];let mut ends=[[0u32;3];6];
            for k in 0..6 {
                ends[k]=[(3+k as u32*7)&31,(29+k as u32*3)&31,(k as u32*11)&31];
                let [r,g,b]=ends[k];
                let v=((part>>k)&1)|(g<<1)|(((r+32-g)&31)<<6)|(((b+32-g)&31)<<11);
                d[k*2..k*2+2].copy_from_slice(&(v as u16).to_le_bytes());
            }
            let ix=0x195a36cdu32 ^ (part*7919);
            d[12..].copy_from_slice(&(ix<<3).to_le_bytes());
            let mut raw=4u128|((part as u128)<<3);let mut pos=9;
            for c in 0..3 {for ep in ends {raw|=(ep[c] as u128)<<pos;pos+=5;}}
            assert_eq!(pos,99);raw|=(ix as u128)<<pos;
            assert_eq!(rare_candidate(2,&d).unwrap(),standard_bc7(raw.to_le_bytes()).unwrap());
        }
    }
    #[test]
    fn rare_mode0_varying_endpoints_pbits_and_anchors() {
        for part in 0..16u32 {
            let mut ep= (part as u128)<<72;let mut ends=[[0u32;3];6];
            let ps=(part*5+11)&63;
            for k in 0..6 {
                ends[k]=[(3+k as u32*7)&15,(13+k as u32*3)&15,(k as u32*11)&15];
                let [r,g,b]=ends[k];ep|=(g as u128)<<(k*4);ep|=(((r+16-g)&15) as u128)<<(24+k*4);ep|=(((b+16-g)&15) as u128)<<(48+k*4);
            }
            ep|=((ps&7) as u128)<<77;let ix=0x195a36cdb79u64^(part as u64*7919);
            let mut d=[0u8;16];d[..10].copy_from_slice(&ep.to_le_bytes()[..10]);d[10..].copy_from_slice(&((ix<<3)|((ps>>3) as u64)).to_le_bytes()[..6]);
            let mut raw=1u128|((part as u128)<<1);let mut pos=5;
            for c in 0..3 {for end in ends {raw|=(end[c] as u128)<<pos;pos+=4;}}
            assert_eq!(pos,77);raw|=(ps as u128)<<77;raw|=(ix as u128)<<83;
            assert_eq!(rare_candidate(0,&d).unwrap(),standard_bc7(raw.to_le_bytes()).unwrap());
        }
    }
    #[test]
    fn rare_mode1_varying_endpoints_shared_pbits_and_anchors() {
        for part in 0..64u32 {
            let mut d=[0u8;16];let mut ends=[[0u32;3];4];let mut tail=part<<8;
            for k in 0..4 {
                ends[k]=[(3+k as u32*17)&63,(61+k as u32*13)&63,(k as u32*31)&63];
                let [r,g,b]=ends[k];let dr=(r+64-g)&63;let db=(b+64-g)&63;
                let word=g|((dr>>1)<<6)|((db>>1)<<11);d[k*2..k*2+2].copy_from_slice(&(word as u16).to_le_bytes());
                tail|=(dr&1)<<(2*k);tail|=(db&1)<<(2*k+1);
            }
            d[8..10].copy_from_slice(&(tail as u16).to_le_bytes());let ps=(part^1)&3;let ix=0x295a36cdb79u64^(part as u64*7919);
            d[10..].copy_from_slice(&((ix<<2)|(ps as u64)).to_le_bytes()[..6]);
            let mut raw=2u128|((part as u128)<<2);let mut pos=8;
            for c in 0..3 {for end in ends {raw|=(end[c] as u128)<<pos;pos+=6;}}
            assert_eq!(pos,80);raw|=(ps as u128)<<80;raw|=(ix as u128)<<82;
            assert_eq!(rare_candidate(1,&d).unwrap(),standard_bc7(raw.to_le_bytes()).unwrap());
        }
    }
    #[test]
    fn rare_mode3_varying_endpoints_metadata_and_anchors() {
        for part in 0..64u32 {
            let mut d=[0u8;16];let mut ends=[[0u32;3];4];let ps=(part^9)&15;
            for k in 0..4 {
                ends[k]=[(5+k as u32*37)&127,(119+k as u32*17)&127,(31+k as u32*53)&127];
                let [r,g,b]=ends[k];d[k*3]=g as u8;d[k*3+1]=((r+128-g)&127) as u8;d[k*3+2]=((b+128-g)&127) as u8;
            }
            for k in 0..6 {d[k]|=(((part>>k)&1) as u8)<<7;}
            d[6]|=((ps&1) as u8)<<7;d[7]|=(((ps>>1)&1) as u8)<<7;
            let ix=0x295a36cdu32^(part*7919);d[12..].copy_from_slice(&((ix<<2)|(ps>>2)).to_le_bytes());
            let mut raw=8u128|((part as u128)<<4);let mut pos=10;
            for c in 0..3 {for ep in ends {raw|=(ep[c] as u128)<<pos;pos+=7;}}
            assert_eq!(pos,94);raw|=(ps as u128)<<94;raw|=(ix as u128)<<98;
            assert_eq!(rare_candidate(3,&d).unwrap(),standard_bc7(raw.to_le_bytes()).unwrap());
        }
    }
    #[test]
    fn mode7_all_partitions_constant_endpoints_and_padding() {
        for part in 0..64u32 {
            let mut ep=[0u8;12];
            for k in 0..4 {ep[k*2..k*2+2].copy_from_slice(&17u16.to_le_bytes());}
            let tail=(part&15)<<28|(8<<8)|(8<<13)|(8<<18)|(8<<23);
            ep[8..].copy_from_slice(&tail.to_le_bytes());
            assert_eq!(mode7_candidate(&ep,&(part>>4).to_le_bytes()).unwrap(),[[69;4];16]);
        }
        assert!(mode7_candidate(&[0;11],&[0;4]).is_err());
        let mut ep=[0;12];ep[8]=1;
        assert!(mode7_candidate(&ep,&[0;4]).is_err());
    }
    #[test]
    fn mode5_routed_all_tags_and_precision() {
        for tag in 0..4u64 {
            let ends=[0,0,0,0,254,0,0,254];
            let ix=tag|(3<<3); // shared stream selects endpoint 1 at pixel 1
            let values=mode5_routed(&ends,&ix.to_le_bytes());
            let mut expected=[255;4];expected[if tag==0 {3} else {tag as usize-1}]=0;
            assert_eq!(values[1],expected);
        }
    }
    #[test]
    fn mode4_channel_precision_and_anchor() {
        let p=3u64|(8<<2)|(8<<7)|(8<<32)|(8<<37);
        for tag in 0..4 {
            let mut ix=[0u8;10]; ix[0]=tag;
            let mut expected=[66u8;4];
            expected[if tag==0 {3} else {tag as usize-1}]=69;
            assert_eq!(mode4_candidate(&p.to_le_bytes()[..6],&ix).unwrap(),[expected;16]);
        }
        let p=2u64|(31<<7)|(31<<37);
        let mut ix=[0u8;10];ix[0]=4;
        assert_eq!(mode4_candidate(&p.to_le_bytes()[..6],&ix).unwrap()[0],[84,84,84,0]);
    }
    #[test]
    fn mode4_rejects_unobserved_fields_and_lengths() {
        assert!(mode4_candidate(&[0;5],&[0;10]).is_err());
        assert!(mode4_candidate(&[0;6],&[0;9]).is_err());
        assert!(mode4_candidate(&(1u64<<43).to_le_bytes()[..6],&[0;10]).is_err());
    }
    #[test]
    fn mode4_split_layout_requires_opt_in() {
        let mut d=solid_dds();d.resize(229,0);
        d[192..196].copy_from_slice(&0u32.to_le_bytes());
        d[172..176].copy_from_slice(&2u32.to_le_bytes());
        d[152..156].copy_from_slice(&0x10010u32.to_le_bytes());
        d[196..228].fill(0);d[228]=0x44;
        let p=3u64|(8<<2)|(8<<7)|(8<<32)|(8<<37);
        d[196..202].copy_from_slice(&p.to_le_bytes()[..6]);
        d[202..208].copy_from_slice(&p.to_le_bytes()[..6]);
        d[208]=1;d[218]=3;
        let (_,_,_,off,counts)=decode(&d,false).unwrap();
        assert_eq!(counts[4],0);assert!(off.iter().all(|&v| v==0));
        let (_,_,rgba,on,counts)=decode_options(&d,false,true).unwrap();
        assert_eq!(counts[4],2);assert!(on.iter().all(|&v| v==192));
        assert_eq!(&rgba[..4],&[69,66,66,66]);
        assert_eq!(&rgba[16..20],&[66,66,69,66]);
        d[152..156].copy_from_slice(&0x10000u32.to_le_bytes());
        assert!(decode_options(&d,false,true).is_err());
    }
    fn solid_dds() -> Vec<u8> {
        let mut d = vec![0;205];
        d[..4].copy_from_slice(b"DDS ");
        d[84..88].copy_from_slice(b"DX10");
        for (p,v) in [(4,124u32),(12,4),(16,8),(128,98),(132,3),
            (140,1),(148,0x7bc),(152,0x10000),(192,2)] {
            d[p..p+4].copy_from_slice(&v.to_le_bytes());
        }
        d[196..204].copy_from_slice(&[195,243,229,1,27,0,0,255]);
        d[204] = 0x99;
        d
    }
    #[test]
    fn full_layout_places_blocks_and_tracks_coverage() {
        let (w,h,rgba,coverage,counts) = decode(&solid_dds(), false).unwrap();
        assert_eq!((w,h),(8,4));
        assert_eq!(&rgba[..4], &[182,195,168,1]);
        assert_eq!(&rgba[16..20], &[27,27,27,255]);
        assert!(coverage.iter().all(|&c| c==255));
        assert_eq!(counts[9],2);
    }
    #[test]
    fn invalid_mode_order_and_unverified_flags_are_rejected() {
        let mut d = solid_dds();
        d[204] = 0x66;
        assert!(decode(&d, false).unwrap_err().contains("counts"));
        d[204] = 0xaa;
        assert!(decode(&d, false).unwrap_err().contains("mode index"));
        d[204] = 0x99;
        d[155] = 128;
        assert!(decode(&d, false).unwrap_err().contains("flags"));
    }
    #[test]
    fn mode5_candidate_keeps_colour_and_alpha_indices_separate() {
        let values = mode5_candidate(&[0,0,0,255,255,255,255,0], &[255,255,255,255,0,0,0,0], false);
        assert!(values.iter().all(|p| *p == [255;4]));
    }
    #[test]
    fn mode5_candidate_requires_opt_in_and_marks_lower_confidence() {
        let mut d = solid_dds();
        d.resize(196+32+1,0);
        d[192..196].copy_from_slice(&0u32.to_le_bytes());
        d[176..180].copy_from_slice(&2u32.to_le_bytes());
        d[228] = 0x55;
        let (_,_,_,off,counts) = decode(&d,false).unwrap();
        assert_eq!(counts[5],0);
        assert!(off.iter().all(|&p| p==0));
        let (_,_,_,on,counts) = decode(&d,true).unwrap();
        assert_eq!(counts[5],2);
        assert!(on.iter().all(|&p| p==128));
    }
    #[test]
    fn solid_colour_uses_modular_green_differences() {
        assert_eq!(solid(&[195,243,229,1], true), [182,195,168,1]);
        assert_eq!(solid(&[1,2,3,4], false), [1,2,3,4]);
    }
    #[test]
    fn mode6_endpoints_pbits_and_index_order() {
        let ends = [0,0,0,0, 127,127,127,255];
        let result = mode6(&ends, &[0xf0;8], false);
        assert_eq!(result[0], [0;4]);
        assert_eq!(result[1], [255;4]);
        assert_eq!(result[14], [0;4]);
        assert_eq!(result[15], [255;4]);
    }
    #[test]
    fn mode6_inverts_seven_bit_colour_transform() {
        let ends = [27,4,112,127, 52,2,97,255];
        let result = mode6(&ends, &[0xf0;8], true);
        assert_eq!(result[0], [62,54,22,254]);
        assert_eq!(result[1], [109,105,43,255]);
    }
}

