# WoWS Toolbox 5.0.72

## Folder picker compatibility hotfix

- Fixed repeated UI processing errors on Windows PowerShell/.NET Framework
  versions whose folder picker does not expose `UseDescriptionForTitle`.
- Folder selection now uses the enhanced title only when the runtime supports
  it and otherwise falls back to the standard dialog safely.
- The installer continues to detect Microsoft Edge WebView2 during both new
  installations and upgrades and installs it only when it is missing.
- Microsoft-signed Visual C++ runtime DLLs required by the native exporters are
  now deployed app-locally, so users do not need a separate VC++ runtime setup.
- The dedicated armor exporter now includes unified-root armor geometry used by
  Ships 2.0 hulls, verified against Songun and Znamya extraction fixtures.

Model and texture extraction behavior is unchanged from 5.0.71.