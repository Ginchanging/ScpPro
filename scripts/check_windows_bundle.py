"""Smoke check a frozen executable and its Windows installer."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from scppro import __version__
from scppro.storage import AppState, ProfileStore


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / f"dist/ScpPro-{__version__}-Windows-11-x64-Setup.exe"


def launch(executable: Path, work: Path) -> None:
    local = work / "files"
    local.mkdir(exist_ok=True)
    config = work / "config"
    store = ProfileStore(config)
    store.save(AppState(local_path=str(local)))
    before = store.path.stat().st_mtime_ns
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_QPA_PLATFORM", "SCPPRO_DATA_DIR", "SCPPRO_SMOKE_SECONDS"):
        environment.pop(name, None)
    environment.update(SCPPRO_DATA_DIR=str(config), SCPPRO_SMOKE_SECONDS="2")
    completed = subprocess.run([str(executable)], cwd=work, env=environment, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout or f"启动失败：{completed.returncode}")
    assert store.path.stat().st_mtime_ns > before, "应用未正常关闭并保存配置"
    assert Path(store.load().local_path).resolve() == local.resolve()


def main() -> None:
    frozen = ROOT / "dist/ScpPro/ScpPro.exe"
    assert frozen.is_file(), f"请先构建：{frozen}"
    assert INSTALLER.is_file(), f"请先构建：{INSTALLER}"
    with tempfile.TemporaryDirectory(prefix="ScpPro-Windows-check-") as directory:
        work = Path(directory)
        launch(frozen, work)
        installed = work / "installed"
        install = subprocess.run([str(INSTALLER), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={installed}"], timeout=120)
        assert install.returncode == 0, f"安装失败：{install.returncode}"
        try:
            launch(installed / "ScpPro.exe", work)
        finally:
            uninstaller = installed / "unins000.exe"
            assert uninstaller.is_file(), "找不到卸载程序"
            uninstall = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
            assert uninstall.returncode == 0, f"卸载失败：{uninstall.returncode}"
    print("Windows executable, installer and uninstaller checks passed.")


if __name__ == "__main__":
    main()
