# Security policy

## Supported versions

Security fixes are provided for the latest published WoWS Toolbox release. Older releases should be upgraded before a report is reproduced.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting or Security Advisory feature for the repository. Do not publish an exploitable report in a public issue.

Include:

- the affected WoWS Toolbox version;
- Windows and PowerShell versions;
- the affected game source;
- a minimal description of the impact;
- safe reproduction steps;
- sanitized logs or diagnostics.

Do not attach credentials, account identifiers, personal paths, proprietary game packages, extracted models, textures, or Oodle libraries.

## Scope

Security-sensitive areas include:

- command or argument injection;
- writing outside the selected output, cache, or application-owned state directories;
- modifying the selected game installation;
- unsafe archive or package path handling;
- unintended network access;
- loading remote content in the offline viewer;
- installer or updater behavior that deletes user data;
- disclosure of local paths or sensitive diagnostic content.

## Release integrity

Official release notes should publish SHA-256 digests for the installer and portable ZIP. The current launcher and installer are not code-signed, so a digest verifies file identity but does not replace Authenticode publisher verification.

The integrated viewer is designed to use local application resources. The installer may contact Microsoft only through the signed WebView2 Evergreen bootstrapper when the WebView2 Runtime is missing.
