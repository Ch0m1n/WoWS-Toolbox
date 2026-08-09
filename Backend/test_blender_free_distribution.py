from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "GUI" / "WoWSToolboxGUI.ps1"
EXTRACT = ROOT / "Backend" / "extract_ship.py"
INSTALLER = ROOT / "Installer" / "WoWS-Toolbox.iss"
LAUNCHER = ROOT / "Launcher" / "WoWSToolboxLauncher.cs"
WEBVIEW_BOOTSTRAPPER = (
    ROOT / "Installer" / "dependencies" / "MicrosoftEdgeWebview2Setup.exe"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_gui_test_build_never_passes_a_blender_path() -> None:
    text = _read(GUI)
    assert "'--blender'" not in text
    assert text.count('Tag="obj"') == 1
    assert 'Tag="glb"' not in text
    assert 'Tag="obj_glb"' not in text
    assert 'Tag="obj_glb_fbx"' not in text
    assert "function Test-BlenderRequired" in text
    function = text.split("function Test-BlenderRequired", 1)[1].split(
        "function ", 1
    )[0]
    assert "return $false" in function


def test_pc_and_korabli_use_native_glb_obj_exporter() -> None:
    text = _read(EXTRACT)
    function = text.split("def extract_pc_family", 1)[1].split("\ndef ", 1)[0]
    assert "native_glb_export.py" in function
    assert '"blender_seconds": 0.0' in function
    assert 'parser.add_argument("--blender"' not in text
    assert "blender_export_v5.py" not in function
    assert "args.blender" not in function


def test_legends_native_scene_validation_disables_blender() -> None:
    assembler = _read(
        ROOT
        / "FullAssembly"
        / "SelectedShip"
        / "BlenderSceneAssembler"
        / "native_obj_assembler.py"
    )
    assert '"blender_used": False' in assembler


def test_exe_launcher_is_hidden_and_supports_both_powershells() -> None:
    text = _read(LAUNCHER)
    assert 'CreateNoWindow = true' in text
    assert 'ProcessWindowStyle.Hidden' in text
    assert '"PowerShell", "7", "pwsh.exe"' in text
    assert 'WindowsPowerShell' in text
    assert '--check' in text


def test_installer_checks_powershell_and_webview2() -> None:
    text = _read(INSTALLER)
    assert "WindowsPowerShell\\v1.0\\powershell.exe" in text
    assert "Programs\\PowerShell\\7\\pwsh.exe" in text
    assert "NeedsWebView2" in text
    assert "VerifyWebView2Install" in text
    assert 'Parameters: "/silent /install"' in text
    assert "F3017226-FE2A-4295-8BDF-00C3A9A7E4C5" in text
    assert 'Name: "startmenuicon"' in text
    assert 'Name: "desktopicon"' in text
    assert r'Filename: "{app}\WoWS Toolbox.exe"' in text
    assert 'Tasks: startmenuicon' in text
    assert 'Tasks: desktopicon' in text
    assert r'Filename: "{app}\WoWS-Toolbox-GUI.cmd"' not in text


def test_webview2_bootstrapper_is_pinned() -> None:
    assert WEBVIEW_BOOTSTRAPPER.is_file()
    digest = hashlib.sha256(WEBVIEW_BOOTSTRAPPER.read_bytes()).hexdigest().upper()
    assert digest == (
        "8C4A80540B6BBCBEF30A4E8C7D1AC504"
        "B6FC09DB922B4ACDFD85C9D5F6F1050E"
    )

