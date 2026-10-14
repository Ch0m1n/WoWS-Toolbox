pub mod convert;
pub mod keys;
pub mod provider;
pub mod translations;
pub mod types;

use std::io::Read;

/// Read GameParams from either the regular PC client name or Korabli's
/// Python-2-specific archive name.
pub fn read_game_params_bytes(vfs: &vfs::VfsPath) -> Result<Vec<u8>, crate::error::GameDataError> {
    if let Ok(path) = std::env::var("WOWS_GAME_PARAMS_OVERRIDE") {
        return Ok(std::fs::read(path)?);
    }
    let candidates = ["content/GameParams.data", "content/GameParams_py2.data"];
    let mut last_error = None;

    for candidate in candidates {
        let path = match vfs.join(candidate) {
            Ok(path) => path,
            Err(error) => {
                last_error = Some(error);
                continue;
            }
        };
        match path.open_file() {
            Ok(mut file) => {
                let mut bytes = Vec::new();
                file.read_to_end(&mut bytes)?;
                return Ok(bytes);
            }
            Err(error) => last_error = Some(error),
        }
    }

    Err(last_error
        .expect("at least one GameParams candidate was attempted")
        .into())
}
