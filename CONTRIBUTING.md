# Contributing to WoWS Toolbox

Thank you for helping improve WoWS Toolbox.

## Before opening a change

1. Search existing issues and pull requests.
2. Keep each change focused on one problem.
3. Test against files from a game installation you are entitled to use.
4. Never commit extracted models, textures, game packages, proprietary Oodle libraries, credentials, or personal diagnostic data.
5. Confirm that copied or adapted code has a compatible license and update `THIRD_PARTY_NOTICES.md` when needed.

## Development environment

- 64-bit Windows 10 or later
- PowerShell 7 for build and release scripts
- Windows PowerShell 5.1 for compatibility testing
- Inno Setup 6 only when building the installer

The release build includes its own Python runtime. Blender is not required.

## Run the test suite

```powershell
pwsh -NoLogo -NoProfile -File .\Update-SourceManifest.ps1
pwsh -NoLogo -NoProfile -File .\Run-SelfTests.ps1
```

The suite checks both PowerShell 5.1 and PowerShell 7 GUI startup, Python regressions, viewer behavior, package structure, dependency notices, and file hashes. A live WebView2 controller check may be reported as an environmental skip on non-interactive or restricted Windows sessions.

## Pull requests

A pull request should include:

- a concise description of the problem and the chosen solution;
- the affected game source: Legends, World of Warships PC, Korabli, or all;
- test results;
- screenshots for visible UI changes;
- sanitized logs for extraction fixes;
- documentation and notice updates when behavior or dependencies change.

Do not attach copyrighted game assets. If a fix requires a private sample, describe its relevant metadata and reproduce the problem with the smallest legally shareable fixture possible.

## Coding expectations

- Prefer reversible, explicit file operations.
- Treat selected game directories as read-only.
- Fail clearly when a requested LOD or resource is unavailable; do not silently substitute different data.
- Keep Korean and English user-facing text synchronized.
- Preserve Windows PowerShell 5.1 compatibility in the GUI path.
- Add a regression test for every fixed bug when practical.
- Keep release output independent from shared caches.

## Reporting vulnerabilities

Do not open a public issue for a vulnerability that could expose local files, execute arbitrary commands, or bypass path validation. Follow [SECURITY.md](SECURITY.md) instead.
