"""PyInstaller one-folder build for Windows x64."""
from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "scripts/launch.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "src/scppro/resources/icon.svg"), "scppro/resources")],
    hiddenimports=["keyring.backends.Windows", "PySide6.QtSvg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
# A development machine may have Anaconda on PATH. Its ICU DLLs are ABI
# incompatible with Qt's Windows-system ICU imports and must not be bundled.
a.binaries = [entry for entry in a.binaries if entry[0].lower() not in {"icuuc.dll", "icudt73.dll"}]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="ScpPro", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / "build/ScpPro.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ScpPro")
