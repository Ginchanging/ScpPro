"""Render UI previews using temporary files and a real loopback SFTP connection."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from scppro.models import Credentials, Direction, ServerProfile
from scppro.storage import AppState, ProfileStore, SecretStore
from scppro.ui.dialogs import ProfileDialog
from scppro.ui.main_window import MainWindow
from scppro.ui.theme import apply_theme
from tests.conftest import MemoryKeyring
from tests.sftp_server import LocalSFTPServer


def spin(application, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition():
        application.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("UI preview did not complete")
        time.sleep(0.01)
    application.processEvents()


def main():
    application = QApplication([])
    application.setApplicationName("ScpPro Preview")
    application.styleHints().setColorScheme(Qt.ColorScheme.Light)
    apply_theme(application)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ScpPro-preview-") as temporary:
        base = Path(temporary)
        local = base / "本机文件"
        local.mkdir()
        folder = local / "项目文件"
        folder.mkdir()
        (folder / "说明文档.md").write_text("# ScpPro 文件传输示例\n", encoding="utf-8")
        (folder / "数据样例.csv").write_text("名称,数值\n文件,1024\n", encoding="utf-8")
        (local / "会议记录.txt").write_text("这是一份临时文件，用于验证真实上传下载。\n", encoding="utf-8")
        (local / "示例数据.bin").write_bytes(bytes(range(256)) * 4096)
        (local / "空目录").mkdir()
        server = LocalSFTPServer(base / "remote")
        window = None
        try:
            (server.root / "backups").mkdir()
            (server.root / "datasets").mkdir()
            (server.root / "操作指南.txt").write_text("ScpPro 上传和下载操作指南。\n", encoding="utf-8")
            profile = ServerProfile("本机测试服务器", "127.0.0.1", "tester", port=server.port)
            store = ProfileStore(base / "config")
            store.save(AppState([profile], local_path=str(local)))
            window = MainWindow(store, SecretStore(MemoryKeyring(), "ScpPro.Preview"))
            window.bridge.requested.disconnect(window._handle_decision)
            window.bridge.requested.connect(lambda request: request.resolve(True) if request.kind == "host_key" else request.resolve(request.default))
            window.show()
            window.connect_profile(profile, Credentials("test-password"))
            spin(application, lambda: bool(window.session and window.remote_pane.directory and window.local_pane.directory))
            window.enqueue(Direction.UPLOAD, (str(folder),), "/")
            window.enqueue(Direction.UPLOAD, (str(local / "示例数据.bin"),), "/")
            window.enqueue(Direction.DOWNLOAD, ("/操作指南.txt",), str(local))
            spin(application, lambda: len(window.tasks) == 3 and all(task.result is not None for task in window.tasks.values()))
            spin(application, lambda: not window.pool.activeThreadCount())
            assert all(not task.result.failed and not task.result.error for task in window.tasks.values())
            assert (local / "操作指南.txt").read_bytes() == (server.root / "操作指南.txt").read_bytes()
            for row in range(window.local_pane.table.rowCount()):
                if window.local_pane.table.item(row, 0).entry.name == "项目文件":
                    window.local_pane.table.selectRow(row)
                    break
            application.processEvents()
            window.grab().save(str(artifacts / "ScpPro-light.png"))
            dialog = ProfileDialog(profile, window)
            dialog.show()
            application.processEvents()
            dialog.grab().save(str(artifacts / "ScpPro-connection.png"))
            dialog.close()
            application.styleHints().setColorScheme(Qt.ColorScheme.Dark)
            apply_theme(application, dark=True)
            application.processEvents()
            window.grab().save(str(artifacts / "ScpPro-dark.png"))
            window.close()
            spin(application, lambda: not window.isVisible())
        finally:
            if window and window.session:
                window.session.close()
            server.close()
    print("已验证真实上传与下载，预览保存于 artifacts/。")


if __name__ == "__main__":
    main()
