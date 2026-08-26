# WoWS Blitz model-extraction setup guide

This guide explains how to prepare **World of Warships Blitz data that you
legitimately installed and downloaded in your own Android environment** for use
with WoWS Toolbox 5.0.61 or later.

WoWS Toolbox does not connect to or modify an emulator. It reads only a folder
that you copied to the PC. APKs, OBBs, AssetBundles, exported models, and
textures are game assets and must not be redistributed.

## 1. Requirements and scope

You need:

- WoWS Toolbox 5.0.61 or later;
- an Android device or emulator that can run WoWS Blitz;
- root access and ADB access to the app-private directory;
- local data downloaded by your own account after completing updates and
  reaching the port; and
- enough PC storage for the bundle tree and base OBB.

The Windows installation directory created by Google Play Games beta cannot be
selected directly as a Blitz data root. It does not expose the Android
app-private data in the layout required by the toolbox. The simplest currently
tested preparation route is a root-capable Android emulator with ADB access.
The root switch and ADB port vary by emulator and version.

The Android package ID used by WoWS Toolbox is:

```text
net.wargaming.wows.blitz
```

## 2. Let the game download its local data first

1. Install WoWS Blitz through an official store.
2. Update the game completely.
3. Sign in and reach the port.
4. Wait for background resource processing to finish.
5. If possible, visit the tech tree and ship-detail screens, then exit the game
   normally.

Blitz does not expose the entire server-side ship library as one fixed optional
download. `files/bundle` contains **only resources currently downloaded by that
installation**. WoWS Toolbox therefore distinguishes catalog records from ships
that have a local body bundle and can actually be extracted. One tested build
contained 964 catalog records and 756 locally backed ships, but the numbers can
change with game version and region.

Do not copy the data while the game is updating or writing resources. Finish
port loading, close the game, and then copy the files so that the bundle tree and
OBB represent the same point in time.

## 3. Connect ADB and verify root access

Enable root access and ADB in the emulator settings. Locate that emulator's ADB
executable, then set its path in PowerShell 7:

```powershell
$adb = 'C:\path\to\adb.exe'
& $adb devices
```

If the list is empty, use the address and port shown by the emulator manager.
Replace `PORT` below with the actual value for your installation:

```powershell
& $adb connect '127.0.0.1:PORT'
& $adb devices
```

If more than one device is listed, add `-s SERIAL` to later commands:

```powershell
& $adb -s '127.0.0.1:PORT' shell id
```

Check root access in this order:

```powershell
& $adb shell id
& $adb root
& $adb shell id
& $adb shell su -c id
```

`uid=0(root)` means that the app-private directory is readable. If `adb root` is
unsupported but `su -c id` returns root, use the shared-storage staging method
below. If you receive `su: not found` or `permission denied`, recheck the
emulator's root setting and make sure that you are using its matching ADB
executable.

## 4. Verify the source locations

Downloaded ship bundles normally reside at:

```text
/data/data/net.wargaming.wows.blitz/files/bundle/
```

Verify the body directory and count its files:

```powershell
& $adb shell su -c "ls -ld /data/data/net.wargaming.wows.blitz/files/bundle/prefab/ship/body"
& $adb shell su -c "find /data/data/net.wargaming.wows.blitz/files/bundle/prefab/ship/body -type f | wc -l"
```

The base OBB is normally under one of these paths:

```text
/sdcard/Android/obb/net.wargaming.wows.blitz/
/storage/emulated/0/Android/obb/net.wargaming.wows.blitz/
```

```powershell
& $adb shell "ls -l /sdcard/Android/obb/net.wargaming.wows.blitz/main.*.obb"
```

You should see `main.<version>.net.wargaming.wows.blitz.obb`. The OBB contains
shared guns, miscellaneous objects, animations, shaders, and other dependencies
referenced by ship body bundles. A body-only copy is not enough to assemble a
complete ship.

## 5. Create the prepared data root on the PC

The example below creates `WoWS-Blitz-Data` in Documents. You may use another
location.

```powershell
$blitzData = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WoWS-Blitz-Data'
New-Item -ItemType Directory -Path $blitzData -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $blitzData 'downloads') -Force | Out-Null
```

### Method A: root ADB can read the private directory directly

```powershell
& $adb pull '/data/data/net.wargaming.wows.blitz/files/bundle' $blitzData
Rename-Item -LiteralPath (Join-Path $blitzData 'bundle') -NewName 'full_bundle'

$obbRemote = ((& $adb shell "ls -1 /sdcard/Android/obb/net.wargaming.wows.blitz/main.*.obb") |
    Select-Object -First 1).Trim()
& $adb pull $obbRemote (Join-Path $blitzData 'downloads')
```

If `full_bundle` already exists, create a separate directory for the new game
build instead of merging two versions.

### Method B: direct `adb pull /data/data/...` is denied

Use `su` to copy the private bundle tree to a dedicated temporary directory in
Android shared storage, then pull that copy. This method needs additional free
emulator storage roughly equal to the bundle tree size.

```powershell
$stage = '/sdcard/Download/WoWSBlitzExport'
& $adb shell su -c "mkdir -p $stage"
& $adb shell su -c "cp -R /data/data/net.wargaming.wows.blitz/files/bundle $stage/"
& $adb pull "$stage/bundle" $blitzData
Rename-Item -LiteralPath (Join-Path $blitzData 'bundle') -NewName 'full_bundle'

$obbRemote = ((& $adb shell "ls -1 /sdcard/Android/obb/net.wargaming.wows.blitz/main.*.obb") |
    Select-Object -First 1).Trim()
& $adb pull $obbRemote (Join-Path $blitzData 'downloads')
```

Remove the staging directory only after verifying the PC copy. The command below
targets only the dedicated `WoWSBlitzExport` directory, not the game source:

```powershell
& $adb shell su -c "rm -rf /sdcard/Download/WoWSBlitzExport"
```

### Optional: DesignData

`DesignData` improves localized ship names, tiers, and catalog metadata. It is
not required for model extraction; without it, the toolbox builds a catalog from
internal resource names.

Search the current installation first:

```powershell
& $adb shell su -c "find /data/data/net.wargaming.wows.blitz -type f -name DesignData 2>/dev/null"
```

If a path is returned, copy that file to the prepared data root as `DesignData`:

```powershell
$designRemote = ((& $adb shell su -c "find /data/data/net.wargaming.wows.blitz -type f -name DesignData 2>/dev/null") |
    Select-Object -First 1).Trim()
if ($designRemote) {
    & $adb pull $designRemote (Join-Path $blitzData 'DesignData')
}
```

If no path is returned, skip this step. Do not mix DesignData from another game
version with the current bundle tree.

## 6. Validate the finished layout

The prepared root should look like this:

```text
WoWS-Blitz-Data/
├─ full_bundle/
│  ├─ BundlePackInfo.bytes
│  ├─ prefab/ship/body/*.ab
│  ├─ artist/
│  ├─ shippaint/
│  └─ nation-specific bundle directories
├─ downloads/
│  └─ main.<version>.net.wargaming.wows.blitz.obb
└─ DesignData                 optional
```

Run these checks in PowerShell:

```powershell
Test-Path (Join-Path $blitzData 'full_bundle\prefab\ship\body')
Get-ChildItem (Join-Path $blitzData 'full_bundle\prefab\ship\body') -Filter '*.ab' |
    Measure-Object
Get-ChildItem (Join-Path $blitzData 'downloads') -Filter 'main.*.obb' |
    Select-Object Name, Length
```

The first result must be `True`, the body count must be greater than zero, and
the OBB must have a nonzero size.

## 7. Extract with WoWS Toolbox

1. Open **Settings > Game installation folders** in WoWS Toolbox.
2. Set **World of Warships Blitz** to the `WoWS-Blitz-Data` root and save.
3. On **Ship Extraction**, select **World of Warships Blitz** as the source.
4. Click **Refresh catalog** to scan the current local bundle set.
5. Add a ship and, when present, select **Default paint**, **Paint 01**, or
   **Paint 02**.
6. Confirm the output directory and run **Readiness check**.
7. After it passes, click **Extract queued models**.

The output contains an editable OBJ, MTL, a `textures` directory, and an
extraction report. The first extraction may take longer because WoWS Toolbox
indexes CAB locations in the OBB. Resolved dependencies are cached under
`%LOCALAPPDATA%\WoWSToolbox\Cache\Blitz` for later exports.

## 8. Refresh data after a game update

Create a new prepared directory for each game build whenever possible:

1. Complete the game update and port loading.
2. Exit the game.
3. Copy the new `files/bundle` and new `main.*.obb` together.
4. Optionally copy DesignData from the same build.
5. Select the new root in WoWS Toolbox and refresh the catalog.

Mixing an old OBB with new body bundles can cause unresolved CAB dependencies,
missing mounts, or white materials. A versioned directory is safer than partly
overwriting an older prepared root.

## 9. Troubleshooting

### The Blitz path is missing or not recognized

The selected directory must directly contain either
`full_bundle\prefab\ship\body` or `bundle\prefab\ship\body`. If your result is
`full_bundle\bundle\prefab`, select the inner directory or rearrange it to match
the example layout.

### `main.*.obb` is missing

Place the OBB either in the selected root or its `downloads` directory. Keep its
original file name and use the file copied from the same game build as the body
bundles.

### The catalog is small or a ship is missing

WoWS Toolbox extracts actual body bundles in the local `files/bundle` cache, not
a server-side ship list. Finish resource loading in the game, visit the relevant
screens, and copy the bundle tree again. If no body bundle is downloaded, that
installation cannot currently provide that model.

### Ship names appear as internal codes

DesignData may be absent or may not match the current bundle version. Extraction
still works, but localized names and tier metadata are limited.

### Main, secondary, or anti-aircraft mounts are white

Install WoWS Toolbox 5.0.61 or later and **extract the ship again**. Existing
outputs are not repaired in place. Version 5.0.61 recursively resolves
second-level texture CABs referenced by weapon materials. If the problem remains
on the latest version, first check for a mismatched OBB and bundle tree or an
incomplete copy.

### A CAB or external reference cannot be resolved

- Recopy the OBB and `full_bundle` from the same game build.
- Make sure that the game did not update during the copy.
- Use **Settings > Cache > Clear cache**, then retry one ship.
- If it still fails, attach only a Diagnostic ZIP. Do not attach game assets.

### More than one ADB device is listed

Add `-s SERIAL` to every command:

```powershell
& $adb -s 'SERIAL' shell su -c id
```

## 10. Safety and asset rights

- Read and copy the original Android data; avoid modifying it.
- Use root only inside a dedicated data-preparation emulator.
- Never share account tokens, login data, or device identifiers.
- Do not upload APKs, OBBs, AssetBundles, OBJ/MTL files, or textures to GitHub
  issues or releases.
- Use exports only as permitted by the game EULA, platform terms, and local law.

If you need to report a problem, use **Settings > Diagnostic ZIP** in WoWS
Toolbox. It is designed to exclude personal paths and model assets.
