from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME_ROOTS = ("Backend", "BlenderExtractor", "FullAssembly")


def runtime_python_files():
    for root_name in RUNTIME_ROOTS:
        for path in (ROOT / root_name).rglob("*.py"):
            if path.name.startswith("test_"):
                continue
            yield path


def test_runtime_avoids_python39_string_removers_and_python310_strict_zip():
    issues = []
    for path in runtime_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"removeprefix", "removesuffix"}:
                    issues.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "zip" and any(
                    keyword.arg == "strict" for keyword in node.keywords
                ):
                    issues.append(f"{path.relative_to(ROOT)}:{node.lineno}:zip(strict=)")
    assert not issues, "Python 3.8-incompatible runtime calls: " + ", ".join(issues)

def test_bundled_python_runtime_has_pillow():
    import json
    import subprocess

    python = ROOT / "Runtime" / "Python" / "python.exe"
    assert python.is_file()
    run = subprocess.run(
        [
            str(python),
            "-B",
            "-c",
            (
                "import json, sys, PIL; "
                "print(json.dumps({'python': list(sys.version_info[:3]), "
                "'pillow': PIL.__version__}))"
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["python"] >= [3, 10, 0]
    assert payload["pillow"] == "12.3.0"


def test_gui_prefers_the_bundled_python_runtime():
    gui = (ROOT / "GUI" / "WoWSToolboxGUI.ps1").read_text(encoding="utf-8-sig")
    assert "Runtime\\Python\\python.exe" in gui
    assert "Test-Path -LiteralPath $script:BundledPythonCommand" in gui