"""Check the frozen app both without the source environment and via LaunchServices."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from scppro.storage import AppState, ProfileStore

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist/ScpPro.app"


def check(native: bool):
    with tempfile.TemporaryDirectory(prefix="ScpPro-bundle-check-") as temporary:
        base = Path(temporary)
        local = base / "files"
        local.mkdir()
        config = base / "config"
        store = ProfileStore(config)
        store.save(AppState(local_path=str(local)))
        previous_modified = store.path.stat().st_mtime_ns
        environment = os.environ.copy()
        for name in ("PYTHONPATH", "PYTHONHOME", "QT_QPA_PLATFORM", "SCPPRO_DATA_DIR", "SCPPRO_SMOKE_SECONDS"):
            environment.pop(name, None)
        if native:
            stdout = base / "stdout.txt"
            stderr = base / "stderr.txt"
            command = [
                "open", "-W", "-n", str(APP),
                "--env", f"SCPPRO_DATA_DIR={config}",
                "--env", "SCPPRO_SMOKE_SECONDS=2",
                "--stdout", str(stdout), "--stderr", str(stderr),
            ]
        else:
            environment.update(QT_QPA_PLATFORM="offscreen", SCPPRO_DATA_DIR=str(config), SCPPRO_SMOKE_SECONDS="2")
            command = [str(APP / "Contents/MacOS/ScpPro")]
        result = subprocess.run(command, cwd=base, env=environment, capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout or f"Launch exited {result.returncode}")
        if native and stderr.exists() and "Traceback" in stderr.read_text():
            raise RuntimeError(stderr.read_text())
        assert store.path.stat().st_mtime_ns > previous_modified, "Application did not reach a normal close"
        assert Path(store.load().local_path).resolve() == local.resolve()
        print(("LaunchServices / native macOS" if native else "Frozen executable / offscreen") + ": passed")


def main():
    if not APP.is_dir():
        raise SystemExit("请先执行 scripts/build.py。")
    check(False)
    check(True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    print("Application code signature: passed")


if __name__ == "__main__":
    main()
