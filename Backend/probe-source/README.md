# Material inspector source snapshot

This is the complete modified `landaire/wowsunpack` library used to build the
material inspector, based on commit `53f7c6040780e6fede941fbf4579cf785fedb718`.
It includes local modifications, not an unmodified upstream checkout.
License: MIT; see the toolbox root LICENSE and THIRD_PARTY_NOTICES.md.
No game assets, Oodle DLL, Cargo build cache or Git history is included.

From the toolbox directory:

```powershell
cargo test --locked --manifest-path Backend/probe-source/Cargo.toml --example ship_material_probe
cargo build --locked --release --manifest-path Backend/probe-source/Cargo.toml --example ship_material_probe
```

Add `--offline` when the locked Cargo dependencies are already cached.
The binary is `Backend/probe-source/target/release/examples/ship_material_probe.exe`.
Copy it to `Backend/ship_material_probe.exe`. Compiler differences can change
binary hashes; behavioral tests and the locked source are the reproducibility
contract. The canonical helper source is also supplied at
`Backend/ship_material_probe.rs`; keep the example identical when modifying it.
Only the verified material checksum is classified; unknown layouts stay unknown.
