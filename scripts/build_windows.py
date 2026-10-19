"""Build the Windows executable and per-user installer."""
from __future__ import annotations

import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from scppro import __version__


ROOT = Path(__file__).resolve().parents[1]


def compiler() -> Path:
    override = os.environ.get("INNO_SETUP_COMPILER")
    candidates = [Path(override)] if override else []
    command = shutil.which("ISCC.exe")
    if command:
        candidates.append(Path(command))
    for folder in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if folder:
            candidates.extend(Path(folder).glob("Inno Setup */ISCC.exe"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend((Path(local_app_data) / "Programs").glob("Inno Setup */ISCC.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("找不到 Inno Setup 的 ISCC.exe；请安装 Inno Setup 6，或设置 INNO_SETUP_COMPILER。")


def make_icon() -> Path:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    source = ROOT / "src/scppro/resources/icon.svg"
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise SystemExit(f"应用图标无法读取：{source}")
    output = ROOT / "build/ScpPro.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise SystemExit("无法生成 Windows ICO 图标。")
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    with output.open("wb") as stream:
        stream.write(struct.pack("<HHH", 0, 1, len(images)))
        for size, data in images:
            stream.write(struct.pack("<BBBBHHII", size if size < 256 else 0,
                                     size if size < 256 else 0, 0, 0, 1, 32,
                                     len(data), offset))
            offset += len(data)
        for _, data in images:
            stream.write(data)
    return output


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("请在 Windows x64 上构建安装包。")
    if __version__ != "1.1.0":
        raise SystemExit("请同步更新安装脚本中的版本号。")
    make_icon()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "ScpPro-Windows.spec")], cwd=ROOT, check=True)
    subprocess.run([str(compiler()), str(ROOT / "installer/ScpPro.iss")], cwd=ROOT / "installer", check=True)
    print(ROOT / f"dist/ScpPro-{__version__}-Windows-11-x64-Setup.exe")


if __name__ == "__main__":
    main()
