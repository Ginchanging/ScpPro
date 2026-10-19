from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "scripts/launch.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "src/scppro/resources/icon.svg"), "scppro/resources")],
    hiddenimports=["keyring.backends.macOS", "PySide6.QtSvg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="ScpPro", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, argv_emulation=False,
    target_arch="arm64", codesign_identity=None, entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ScpPro")
app = BUNDLE(
    collection, name="ScpPro.app", icon=str(root / "build/ScpPro.icns"),
    bundle_identifier="com.scppro.desktop",
    info_plist={
        "CFBundleShortVersionString": "1.1.0",
        "CFBundleVersion": "1.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "NSDocumentsFolderUsageDescription": "用于浏览、上传和下载您选择的文稿文件。",
        "NSDownloadsFolderUsageDescription": "用于上传及保存您选择下载的文件。",
        "NSDesktopFolderUsageDescription": "用于浏览、上传和下载您选择的桌面文件。",
        "NSNetworkVolumesUsageDescription": "用于传输您选择的网络磁盘文件。",
        "NSRemovableVolumesUsageDescription": "用于传输您选择的外接磁盘文件。",
    },
)
