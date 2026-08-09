# WoWS Toolbox

![WoWS Toolbox](Installer/assets/WoWS-Toolbox.png)

WoWS Toolbox is an unofficial Windows desktop application for selecting, extracting, assembling, and inspecting ship models from locally installed World of Warships-family clients.

It provides a graphical workflow for users who do not want to work from a command line. The application exports editable OBJ/MTL models, keeps ship parts as separate objects where the source data allows it, and includes an offline 3D and armor viewer.

> [!IMPORTANT]
> WoWS Toolbox is not endorsed by, sponsored by, or affiliated with Wargaming Group Limited, Lesta Games, their licensors, or their affiliates. Game assets remain the property of their respective owners. See [Legal and asset rights](#legal-and-asset-rights).

## Supported game installations

- World of Warships: Legends on Steam
- World of Warships for PC
- Korabli

The selected game directory is treated as read-only. WoWS Toolbox writes caches and exported models outside the game installation.

## Main features

- Search ships by in-game name, nation, class, tier, or internal code
- Queue multiple ships for unattended extraction
- Export hull, superstructure, main battery, secondary battery, anti-aircraft weapons, and other resolved parts
- Preserve editable OBJ object groups instead of merging the entire ship into one mesh
- Export OBJ, MTL, and local base-color texture files without requiring Blender
- Inspect models in the built-in offline WebView2/Three.js viewer
- Select, hide, isolate, move, rotate, and undo part edits in the viewer
- Rotate weapon mounts around their extracted entity origin and adjust supported gun barrels
- Display exact armor thickness metadata when the selected client exposes it
- Show or hide the grid, background, waterline, wireframe, and armor overlays
- Compare two extracted models
- Use Korean or English throughout the main UI, ship picker, viewer, and runtime logs
- Run on Windows PowerShell 5.1 or PowerShell 7; PowerShell 7 is preferred when both are installed

English is the default application language for a new installation. It can be changed under **Settings > Interface language**.

## Download and install

1. Open the repository's [Releases](../../releases) page.
2. Download the latest `WoWS-Toolbox-Setup-*.exe`.
3. Check the published SHA-256 value before running it.
4. Choose the installer language and optional shortcuts.
5. Select a supported game directory when WoWS Toolbox starts.
6. Refresh the ship catalog, add one or more ships to the queue, run the readiness check, and start extraction.

The installer upgrades an older WoWS Toolbox installation in place. User settings, caches, and exported models are stored separately and are preserved.

The application launcher and installer are currently unsigned. Windows SmartScreen or reputation-based antivirus products may warn about new builds. Release notes should always include a SHA-256 digest.

## Requirements

- 64-bit Windows 10 version 1809 or later
- Windows PowerShell 5.1 or PowerShell 7
- Microsoft Edge WebView2 Runtime for the integrated viewer
- A locally installed, supported game client
- Free disk space for extracted models and textures

The release package includes a private CPython 3.10 runtime and Pillow. Blender and a system Python installation are not required. The installer carries Microsoft's signed WebView2 Evergreen bootstrapper and runs it only when WebView2 is missing.

PC and Korabli extraction can require a compatible Oodle runtime from software the user is entitled to use. WoWS Toolbox does not redistribute proprietary Oodle libraries.

## Quality and format notes

- Legends exports use the verified LOD0 render set and original-size base-color texture policy.
- PC and Korabli honor the requested LOD only when that LOD exists. The extractor reports an error instead of silently substituting a different LOD.
- OBJ/MTL output carries base-color textures. It is not a complete export of every proprietary in-game shader channel.
- Some internal, animated, streamed, or unsupported model formats may not resolve.
- Armor data availability and precision depend on the selected client and build.
- Viewer edits are temporary inspection edits; they are not written back to the source OBJ.

## Repository layout

- `Backend/` — catalog, queue, extraction, native OBJ/GLB conversion, and armor sidecars
- `BlenderExtractor/` — Legends geometry parsing and supporting format code
- `FullAssembly/` — selected-ship resource resolution and part assembly
- `GUI/` — PowerShell/WPF application and localization
- `Viewer/` — offline WebView2/Three.js model and armor viewer
- `Launcher/` — console-free Windows launcher source
- `Installer/` — Inno Setup definition, legal notices, and installer assets
- `Runtime/` — bundled runtime files used by release builds
- `examples/` — sanitized request examples

## Build and test

PowerShell 7 is required for the release scripts.

```powershell
pwsh -NoLogo -NoProfile -File .\Launcher\Build-Launcher.ps1
pwsh -NoLogo -NoProfile -File .\Update-SourceManifest.ps1
pwsh -NoLogo -NoProfile -File .\Run-SelfTests.ps1
pwsh -NoLogo -NoProfile -File .\Build-Release.ps1 -Version 5.0.30 -CreateZip
pwsh -NoLogo -NoProfile -File .\Installer\Build-Installer.ps1
```

Building the installer also requires Inno Setup 6. The build scripts refuse to overwrite an existing release target, validate the launcher version, verify the Microsoft signature and SHA-256 of the WebView2 bootstrapper, and create a release manifest.

## Reporting bugs

Please use the GitHub bug report form and remove personal paths, account names, tokens, and unrelated game files from logs before attaching them. Do not upload extracted models, textures, package files, or other copyrighted game assets.

Security-sensitive reports should follow [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions must not include extracted game assets, proprietary runtime libraries, credentials, or code copied from an incompatible or unlicensed source.

## Legal and asset rights

WoWS Toolbox is an unofficial community project. Its MIT license applies only to WoWS Toolbox code and does not grant rights to use, redistribute, or sell extracted game assets.

Users are responsible for following the applicable game EULA, platform terms, and local law. Do not redistribute or sell extracted assets without permission from the rights holder. See [LEGAL_NOTICE.txt](LEGAL_NOTICE.txt) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

WoWS Toolbox code is released under the [MIT License](LICENSE). Third-party components remain under their respective licenses.
