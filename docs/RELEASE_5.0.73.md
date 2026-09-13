# WoWS Toolbox 5.0.73

## Camouflage and path compatibility fixes

- Fixed ship-specific permanent camouflage masks leaking from the hull onto
  superstructures, guns, directors, and other parts not listed in the game's
  camouflage XML.
- Corrected the original/alternate permanent-camouflage color labels to match
  the client-visible palette meaning.
- Reads settings, catalogs, queue files, recent ships, and batch manifests as
  UTF-8 under both Windows PowerShell 5.1 and PowerShell 7, including Korean
  and space-containing paths.
- Revalidates saved game folders at startup. Missing old paths are replaced by
  a newly detected installation or cleared when no valid installation exists.
- The uninstall wizard now removes Toolbox-owned settings, caches, WebView2
  data, update downloads, diagnostics, and the default in-app output folder.
  Custom output folders outside the application directory are not removed.
