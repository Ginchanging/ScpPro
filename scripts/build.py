"""Build the self-contained Apple Silicon macOS application."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("请在 Apple 芯片的 Mac 上构建此应用。")
    root = Path(__file__).resolve().parents[1]
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    iconset = root / "build/ScpPro.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(str(root / "src/scppro/resources/icon.svg"))
    if not renderer.isValid():
        raise SystemExit("应用图标无法读取。")
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            suffix = "@2x" if scale == 2 else ""
            if not image.save(str(iconset / f"icon_{size}x{size}{suffix}.png")):
                raise SystemExit("无法生成应用图标。")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(root / "build/ScpPro.icns")], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(root / "ScpPro.spec")], cwd=root, check=True)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", "dist/ScpPro.app", "dist/ScpPro-macOS-arm64.zip"], cwd=root, check=True)
    print(f"构建完成：{root / 'dist/ScpPro.app'}")


if __name__ == "__main__":
    main()
