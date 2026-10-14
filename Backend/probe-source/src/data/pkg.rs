use std::{
    collections::HashMap,
    fs::File,
    io::{self, Cursor, Write},
    path::{Path, PathBuf},
    sync::RwLock,
};

use flate2::read::{DeflateDecoder, ZlibDecoder};
use memmap2::MmapOptions;
use thiserror::Error;

use crate::data::idx::FileInfo;

fn compression_codec(compression_info: u64) -> u32 {
    if compression_info >> 32 == 0 {
        0
    } else {
        compression_info as u32
    }
}

#[cfg(windows)]
fn decompress_oodle_container(data: &[u8]) -> io::Result<Vec<u8>> {
    use std::ffi::{c_char, c_void};
    use std::os::windows::ffi::OsStrExt;

    type HModule = *mut c_void;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn LoadLibraryW(file_name: *const u16) -> HModule;
        fn GetProcAddress(module: HModule, proc_name: *const c_char) -> *mut c_void;
    }
    type OodleDecompress = unsafe extern "C" fn(
        *const c_void,
        i64,
        *mut c_void,
        i64,
        i32,
        i32,
        i32,
        *mut c_void,
        i64,
        *mut c_void,
        *mut c_void,
        *mut c_void,
        i64,
        i32,
    ) -> i64;

    let malformed = |detail: &str| io::Error::new(io::ErrorKind::InvalidData, detail.to_string());
    if data.len() < 56 {
        return Err(malformed("Oodle container header is too short"));
    }
    let read_u32 = |offset: usize| -> io::Result<u32> {
        let bytes = data
            .get(offset..offset + 4)
            .ok_or_else(|| malformed("Oodle u32 extends past input"))?;
        Ok(u32::from_le_bytes(
            bytes.try_into().expect("four-byte slice"),
        ))
    };
    let read_u64 = |offset: usize| -> io::Result<u64> {
        let bytes = data
            .get(offset..offset + 8)
            .ok_or_else(|| malformed("Oodle u64 extends past input"))?;
        Ok(u64::from_le_bytes(
            bytes.try_into().expect("eight-byte slice"),
        ))
    };

    let header_size =
        usize::try_from(read_u64(0)?).map_err(|_| malformed("Oodle header size overflow"))? + 8;
    let version = read_u32(8)?;
    let container_type = read_u32(12)?;
    let raw_total =
        usize::try_from(read_u64(16)?).map_err(|_| malformed("Oodle output size overflow"))?;
    let packed_total =
        usize::try_from(read_u64(24)?).map_err(|_| malformed("Oodle packed size overflow"))?;
    let chunk_count =
        usize::try_from(read_u32(32)?).map_err(|_| malformed("Oodle chunk count overflow"))?;
    let chunk_raw_size =
        usize::try_from(read_u32(36)?).map_err(|_| malformed("Oodle chunk size overflow"))?;
    if !(8..=13).contains(&version) || container_type != 1 {
        return Err(malformed("Unsupported Oodle container version or type"));
    }
    if header_size < 56 + chunk_count * 4 || header_size > data.len() {
        return Err(malformed("Invalid Oodle chunk table"));
    }

    let mut chunk_sizes = Vec::with_capacity(chunk_count);
    let mut packed_sum = 0usize;
    for index in 0..chunk_count {
        let size = usize::try_from(read_u32(56 + index * 4)?)
            .map_err(|_| malformed("Oodle chunk size overflow"))?;
        packed_sum = packed_sum
            .checked_add(size)
            .ok_or_else(|| malformed("Oodle packed size overflow"))?;
        chunk_sizes.push(size);
    }
    if packed_sum != packed_total || header_size + packed_total > data.len() {
        return Err(malformed("Oodle chunk sizes do not match the container"));
    }

    let dll_path = std::env::var_os("WOWS_OODLE_DLL").ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            "WOWS_OODLE_DLL is required for this package",
        )
    })?;
    let mut dll_wide: Vec<u16> = std::ffi::OsStr::new(&dll_path).encode_wide().collect();
    dll_wide.push(0);
    let module = unsafe { LoadLibraryW(dll_wide.as_ptr()) };
    if module.is_null() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "Could not load Oodle runtime: {}",
                dll_path.to_string_lossy()
            ),
        ));
    }
    let symbol =
        unsafe { GetProcAddress(module, b"OodleLZ_Decompress\0".as_ptr().cast::<c_char>()) };
    if symbol.is_null() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "OodleLZ_Decompress was not found in the selected runtime",
        ));
    }
    let decompress: OodleDecompress = unsafe { std::mem::transmute(symbol) };

    let mut result = Vec::with_capacity(raw_total);
    let mut cursor = header_size;
    let mut remaining = raw_total;
    for (index, packed_size) in chunk_sizes.into_iter().enumerate() {
        let raw_size = chunk_raw_size.min(remaining);
        let packed = &data[cursor..cursor + packed_size];
        let mut decoded = vec![0u8; raw_size];
        let written = unsafe {
            decompress(
                packed.as_ptr().cast::<c_void>(),
                packed_size as i64,
                decoded.as_mut_ptr().cast::<c_void>(),
                raw_size as i64,
                1,
                0,
                0,
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                0,
                0,
            )
        };
        if written != raw_size as i64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "Oodle chunk {}/{} decoded {} of {} bytes",
                    index + 1,
                    chunk_count,
                    written,
                    raw_size
                ),
            ));
        }
        result.extend_from_slice(&decoded);
        cursor += packed_size;
        remaining -= raw_size;
    }
    if remaining != 0 || result.len() != raw_total {
        return Err(malformed("Oodle output size mismatch"));
    }
    Ok(result)
}

#[cfg(not(windows))]
fn decompress_oodle_container(_data: &[u8]) -> io::Result<Vec<u8>> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "Oodle package decompression is currently supported on Windows only",
    ))
}

pub(crate) fn decode_package_entry(data: &[u8], compression_info: u64) -> io::Result<Vec<u8>> {
    match compression_codec(compression_info) {
        0 => Ok(data.to_vec()),
        5 => {
            let mut decoded = Vec::new();
            let mut deflate = DeflateDecoder::new(data);
            if std::io::Read::read_to_end(&mut deflate, &mut decoded).is_ok() {
                return Ok(decoded);
            }
            decoded.clear();
            let mut zlib = ZlibDecoder::new(data);
            std::io::Read::read_to_end(&mut zlib, &mut decoded)?;
            Ok(decoded)
        }
        6 => decompress_oodle_container(data),
        codec => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "unsupported package compression codec {}; compression_info={}",
                codec, compression_info
            ),
        )),
    }
}
/// `PkgFileLoader` is responsible for automatically loading and maintaining pkg files
/// in-memory to ensure that a file is only loaded once, and can be conveniently
/// loaded on-demand.
#[derive(Debug)]
pub struct PkgFileLoader {
    pkgs_dir: PathBuf,
    pkgs: RwLock<HashMap<PathBuf, (File, memmap2::Mmap)>>,
}

#[derive(Debug, Error)]
pub enum PkgError {
    #[error("PKG file {0} not found")]
    PkgNotFound(PathBuf),
    #[error("I/O error")]
    IoError(#[from] io::Error),
}

impl PkgFileLoader {
    /// Construct a new `PkgFileLoader` using `.pkg` files in the given directory
    pub fn new<P: AsRef<Path>>(pkgs_dir: P) -> Self {
        PkgFileLoader {
            pkgs_dir: pkgs_dir.as_ref().into(),
            pkgs: Default::default(),
        }
    }

    /// Ensures that the package with the given name is loaded.
    fn ensure_pkg_loaded<P: AsRef<Path>>(&self, pkg: P) -> Result<(), PkgError> {
        let pkg = pkg.as_ref().to_owned();
        let pkg_loaded = { self.pkgs.read().unwrap().contains_key(&pkg) };
        if !pkg_loaded {
            let pkg_path = self.pkgs_dir.join(&pkg);
            if !pkg_path.exists() {
                return Err(PkgError::PkgNotFound(pkg));
            }

            let pkg_file = File::open(pkg_path).expect("Input file does not exist");
            let mmap = unsafe { MmapOptions::new().map(&pkg_file)? };

            self.pkgs
                .write()
                .unwrap()
                .insert(pkg.clone(), (pkg_file, mmap));
        }

        Ok(())
    }

    /// Read some [`FileInfo`] out of the given `pkg`. Internally, this constructs
    /// a decompression decoder that seeks to the offset specified by the file info
    /// and copies the decompressed data to the given writer.
    pub fn read<P: AsRef<Path>, W: Write>(
        &self,
        pkg: P,
        file_info: &FileInfo,
        out_data: &mut W,
    ) -> Result<(), PkgError> {
        let pkg = pkg.as_ref();
        self.ensure_pkg_loaded(pkg)?;
        let pkgs = self.pkgs.read().unwrap();
        let mmap = &pkgs.get(pkg).unwrap().1;

        let start_offset = file_info.offset as usize;
        let end_offset = start_offset + (file_info.size as usize);

        let packed = &mmap[start_offset..end_offset];
        match compression_codec(file_info.compression_info) {
            0 => {
                let mut cursor = Cursor::new(packed);
                std::io::copy(&mut cursor, out_data)?;
            }
            5 => {
                let cursor = Cursor::new(packed);
                let mut decoder = DeflateDecoder::new(cursor);
                std::io::copy(&mut decoder, out_data)?;
            }
            6 => {
                let decoded = decompress_oodle_container(packed)?;
                out_data.write_all(&decoded)?;
            }
            codec => {
                return Err(PkgError::IoError(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "unsupported package compression codec {}; compression_info={}",
                        codec, file_info.compression_info
                    ),
                )));
            }
        }

        Ok(())
    }
}
