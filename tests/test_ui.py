from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl, Qt

from scppro.models import ConflictAction, ConflictDecision, Credentials, Direction, EntryKind, FileEntry
from scppro.storage import AppState
from scppro.ui.main_window import MainWindow
from scppro.ui.panes import FilePane


def test_local_browser_sort_hidden_and_selection(qtbot, tmp_path):
    pane = FilePane("local")
    qtbot.addWidget(pane)
    entries = [
        FileEntry("large.txt", str(tmp_path / "large.txt"), EntryKind.FILE, 10000),
        FileEntry("small.txt", str(tmp_path / "small.txt"), EntryKind.FILE, 2),
        FileEntry(".secret", str(tmp_path / ".secret"), EntryKind.FILE, 3),
        FileEntry("folder", str(tmp_path / "folder"), EntryKind.DIRECTORY),
    ]
    pane.set_entries(str(tmp_path), entries)
    assert pane.table.rowCount() == 3
    assert pane.table.item(0, 0).text() == "folder"
    pane.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert pane.table.item(1, 0).text() == "small.txt"
    pane.show_hidden = True
    pane.render_entries()
    assert pane.table.rowCount() == 4
    pane.table.selectRow(0)
    assert len(pane.table.selected_entries()) == 1
    selected = pane.table.selected_entries()[0].path
    pane.render_entries()
    assert [entry.path for entry in pane.table.selected_entries()] == [selected]


def test_external_file_drop_only_to_connected_remote(qtbot, tmp_path):
    pane = FilePane("remote")
    qtbot.addWidget(pane)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "file.txt"))])
    assert pane.table._drop_payload(mime) is None
    pane.set_connected(True, "connection-marker")
    pane.set_entries("/", [])
    payload = pane.table._drop_payload(mime)
    assert payload["paths"] == [str(tmp_path / "file.txt")]


def test_ui_connection_upload_download_and_retry(qtbot, store, secret_store, profile, server, tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    file = local / "中文.txt"
    file.write_text("UI end to end", encoding="utf-8")
    saved_profile = replace(profile, remember_secret=True)
    store.save(AppState([saved_profile], local_path=str(local)))
    window = MainWindow(store, secret_store)
    qtbot.addWidget(window)
    # Resolve dialogs programmatically; transport and all file I/O remain real.
    window.bridge.requested.disconnect(window._handle_decision)
    def resolve(request):
        if request.kind == "host_key":
            request.resolve(True)
        elif request.kind == "credentials":
            request.resolve(Credentials("test-password"))
        else:
            request.resolve(ConflictDecision(ConflictAction.OVERWRITE))
    window.bridge.requested.connect(resolve)
    window.show()
    window.connect_profile(saved_profile)
    qtbot.waitUntil(lambda: bool(window.session and window.session.is_connected and window.remote_pane.directory), timeout=10000)
    assert secret_store.get(saved_profile) == Credentials("test-password")
    qtbot.waitUntil(lambda: window.local_pane.table.rowCount() == 1)
    window.local_pane.table.selectRow(0)
    assert window.upload_button.isEnabled()
    window.upload_button.click()
    qtbot.waitUntil(lambda: len(window.tasks) == 1 and all(task.result is not None for task in window.tasks.values()), timeout=10000)
    assert (server.root / file.name).read_bytes() == file.read_bytes()
    downloaded_dir = tmp_path / "downloaded"
    downloaded_dir.mkdir()
    window._browse_local(str(downloaded_dir))
    qtbot.waitUntil(lambda: window.local_pane.directory == str(downloaded_dir))
    download_id = window.enqueue(Direction.DOWNLOAD, ("/" + file.name,), str(downloaded_dir))
    qtbot.waitUntil(lambda: window.tasks[download_id].result is not None, timeout=10000)
    assert (downloaded_dir / file.name).read_bytes() == file.read_bytes()
    server.denied.add("/中文.txt")
    failed_id = window.enqueue(Direction.UPLOAD, (str(file),), "/")
    qtbot.waitUntil(lambda: window.tasks[failed_id].result is not None, timeout=10000)
    assert window.tasks[failed_id].result.failed
    server.denied.clear()
    window._retry_task(failed_id)
    qtbot.waitUntil(lambda: len(window.tasks) == 4 and all(task.result is not None for task in window.tasks.values()), timeout=10000)
    assert list(window.tasks.values())[-1].result.completed_files == 1
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=10000)


def test_ui_stays_responsive_during_transfer(qtbot, store, secret_store, profile, server, tmp_path):
    from PySide6.QtCore import QTimer

    local = tmp_path / "responsive"
    local.mkdir()
    source = local / "large.bin"
    source.write_bytes(b"testdata" * (256 * 1024))
    store.save(AppState([profile], local_path=str(local)))
    window = MainWindow(store, secret_store)
    qtbot.addWidget(window)
    window.bridge.requested.disconnect(window._handle_decision)
    window.bridge.requested.connect(lambda request: request.resolve(True) if request.kind == "host_key" else request.resolve(request.default))
    window.show()
    window.connect_profile(profile, Credentials("test-password"))
    qtbot.waitUntil(lambda: bool(window.session and window.remote_pane.directory), timeout=10000)
    server.delay = 0.003
    ticks = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    job_id = window.enqueue(Direction.UPLOAD, (str(source),), "/")
    qtbot.waitUntil(lambda: window.tasks[job_id].result is not None, timeout=10000)
    timer.stop()
    assert len(ticks) >= 5
    assert window.tasks[job_id].result.completed_files == 1
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=10000)
