from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSize, QThreadPool, QTimer, Qt, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPushButton, QSplitter, QStyle, QVBoxLayout, QWidget,
)

from .. import __version__
from ..models import (
    AuthMethod, ConflictAction, ConflictDecision, Credentials, Direction,
    ServerProfile, TransferJob, TransferResult,
)
from ..sftp import Session, TransferCancelled, error_text, list_local
from ..storage import AppState, ProfileStore, SecretStore, StorageError
from ..transfer import TransferEngine
from ..workers import DecisionBridge, DecisionRequest, Worker
from .dialogs import ConflictDialog, ProfileDialog, show_result
from .panes import FilePane
from .queue import QueuePane


@dataclass
class TaskRecord:
    job: TransferJob
    result: TransferResult | None = None


class MainWindow(QMainWindow):
    def __init__(self, store: ProfileStore | None = None, secret_store: SecretStore | None = None):
        super().__init__()
        self.store = store or ProfileStore()
        self.secret_store = secret_store or SecretStore()
        self.storage_error = ""
        try:
            self.state = self.store.load()
        except StorageError as exc:
            self.state = AppState()
            self.storage_error = str(exc)
        self.session: Session | None = None
        self.connecting_session: Session | None = None
        self.connect_cancel: threading.Event | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.bridge = DecisionBridge(self)
        self.bridge.requested.connect(self._handle_decision)
        self._workers: set[Worker] = set()
        self._secrets: dict[str, Credentials] = {}
        self._credential_barriers: dict[str, threading.Event] = {}
        self._connection_token = 0
        self._local_token = 0
        self._remote_token = 0
        self._pending: deque[str] = deque()
        self.tasks: dict[str, TaskRecord] = {}
        self._active_id: str | None = None
        self._engine: TransferEngine | None = None
        self._closing = False
        self._decision_dialog: QDialog | None = None
        self.setWindowTitle("ScpPro · 文件传输")
        self.setWindowIcon(QIcon(str(Path(__file__).parents[1] / "resources/icon.svg")))
        self.resize(1440, 900)
        self.setMinimumSize(1140, 680)
        self._build_ui()
        self._build_menus()
        self._refresh_profiles()
        self._update_actions()
        self._browse_local(self.state.local_path)
        self.monitor = QTimer(self)
        self.monitor.setInterval(1500)
        self.monitor.timeout.connect(self._monitor_connection)
        self.monitor.start()
        if self.storage_error:
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, "配置读取失败", self.storage_error))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(215)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 24, 18, 18)
        side_layout.setSpacing(12)
        brand = QLabel("ScpPro")
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        tagline = QLabel("文件自由往来")
        tagline.setObjectName("muted")
        side_layout.addWidget(tagline)
        side_layout.addSpacing(24)
        section = QLabel("我的服务器")
        section.setObjectName("paneTitle")
        side_layout.addWidget(section)
        self.profile_list = QListWidget()
        self.profile_list.setWordWrap(True)
        self.profile_list.currentItemChanged.connect(self._update_actions)
        self.profile_list.itemDoubleClicked.connect(lambda _item: self._connect_selected())
        self.profile_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.profile_list.customContextMenuRequested.connect(self._profile_menu)
        side_layout.addWidget(self.profile_list, 1)
        self.add_button = QPushButton("＋ 添加服务器")
        self.add_button.clicked.connect(lambda: self._edit_profile())
        side_layout.addWidget(self.add_button)
        management = QHBoxLayout()
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        self.edit_button.clicked.connect(lambda: self._edit_profile(self._selected_profile()))
        self.delete_button.clicked.connect(self._delete_selected)
        management.addWidget(self.edit_button)
        management.addWidget(self.delete_button)
        side_layout.addLayout(management)
        side_layout.addSpacing(12)
        security = QLabel("SSH 加密连接\n密码可保存到系统钥匙串")
        security.setObjectName("muted")
        security.setWordWrap(True)
        side_layout.addWidget(security)
        root.addWidget(sidebar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 22, 20, 14)
        content_layout.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        title = QLabel("文件传输")
        title.setObjectName("mainTitle")
        subtitle = QLabel("浏览两端文件，选择后上传、下载，或直接拖拽。")
        subtitle.setObjectName("muted")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.connection_label = QLabel("● 未连接")
        self.connection_label.setObjectName("connectionState")
        header.addWidget(self.connection_label)
        self.connect_button = QPushButton("连接服务器")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self._connect_selected)
        header.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.clicked.connect(self._disconnect)
        header.addWidget(self.disconnect_button)
        content_layout.addLayout(header)
        options = QHBoxLayout()
        self.show_hidden_check = QCheckBox("显示隐藏文件")
        self.show_hidden_check.setChecked(self.state.show_hidden)
        self.show_hidden_check.toggled.connect(self._hidden_changed)
        options.addWidget(self.show_hidden_check)
        options.addStretch()
        tip = QLabel("多选：⌘ 点击 · 文件夹：双击打开")
        tip.setObjectName("muted")
        options.addWidget(tip)
        content_layout.addLayout(options)
        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.setChildrenCollapsible(False)
        files = QWidget()
        files_layout = QHBoxLayout(files)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(10)
        self.local_pane = FilePane("local")
        self.remote_pane = FilePane("remote")
        self.local_pane.show_hidden = self.remote_pane.show_hidden = self.state.show_hidden
        self.local_pane.navigate.connect(self._browse_local)
        self.remote_pane.navigate.connect(self._browse_remote)
        self.local_pane.selection_changed.connect(self._update_actions)
        self.remote_pane.selection_changed.connect(self._update_actions)
        self.local_pane.files_dropped.connect(self._drop_to_local)
        self.remote_pane.files_dropped.connect(self._drop_to_remote)
        files_layout.addWidget(self.local_pane, 1)
        middle = QWidget()
        middle.setFixedWidth(84)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(10)
        middle_layout.addStretch()
        self.upload_button = QPushButton("上传 →")
        self.upload_button.setObjectName("primary")
        self.upload_button.clicked.connect(self._upload_selection)
        self.download_button = QPushButton("← 下载")
        self.download_button.clicked.connect(self._download_selection)
        self.choose_button = QPushButton("选择…")
        self.choose_button.setToolTip("选择本机文件或文件夹进行上传")
        choose_menu = QMenu(self.choose_button)
        choose_menu.addAction("选择文件上传…", self._choose_upload_files)
        choose_menu.addAction("选择文件夹上传…", self._choose_upload_folder)
        self.choose_button.setMenu(choose_menu)
        for button in (self.upload_button, self.download_button, self.choose_button):
            middle_layout.addWidget(button)
        middle_layout.addStretch()
        files_layout.addWidget(middle)
        files_layout.addWidget(self.remote_pane, 1)
        vertical.addWidget(files)
        self.queue = QueuePane()
        self.queue.cancel_requested.connect(self._cancel_task)
        self.queue.retry_requested.connect(self._retry_task)
        self.queue.details_requested.connect(self._show_task_details)
        self.queue.clear_requested.connect(self._clear_records)
        self.queue.cancel_all_requested.connect(self._cancel_all)
        vertical.addWidget(self.queue)
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 1)
        vertical.setSizes([520, 210])
        content_layout.addWidget(vertical, 1)
        root.addWidget(content, 1)
        self.statusBar().showMessage("添加服务器连接，开始上传或下载文件。")
        version = QLabel(f"ScpPro {__version__}  ·  SFTP")
        version.setObjectName("muted")
        self.statusBar().addPermanentWidget(version)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("文件")
        actions = [
            ("添加服务器…", "Ctrl+N", lambda: self._edit_profile()),
            ("连接服务器", "Ctrl+K", self._connect_selected),
            ("断开连接", "Ctrl+D", self._disconnect),
            ("选择文件上传…", "Ctrl+U", self._choose_upload_files),
            ("选择文件夹上传…", "Ctrl+Shift+U", self._choose_upload_folder),
            ("下载选中项", "Ctrl+Shift+D", self._download_selection),
        ]
        for text, shortcut, callback in actions:
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
        file_menu.addSeparator()
        quit_action = QAction("退出 ScpPro", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("帮助")
        about = QAction("关于 ScpPro", self)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(lambda: QMessageBox.about(self, "ScpPro", f"ScpPro {__version__}\n\n本机与服务器之间的 SFTP 文件传输工具。\n支持文件、文件夹、多服务器、取消及失败重试。"))
        help_menu.addAction(about)

    def _run_worker(self, worker: Worker, result=None, error=None):
        self._workers.add(worker)
        if result:
            worker.signals.result.connect(result)
        worker.signals.error.connect(error or self._notify_error)
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.pool.start(worker)

    def _notify(self, message: str):
        if not self._closing:
            self.statusBar().showMessage(message)

    @Slot(str)
    def _notify_error(self, message: str):
        self._notify(message.replace("\n", " · "))
        self.statusBar().setToolTip(message)

    def _save_state(self) -> bool:
        if self.storage_error:
            self._notify_error("配置文件不可写，请先修复原配置。")
            return False
        try:
            self.store.save(self.state)
            return True
        except StorageError as exc:
            self._notify_error(str(exc))
            return False

    def _selected_profile(self) -> ServerProfile | None:
        item = self.profile_list.currentItem()
        profile_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((profile for profile in self.state.profiles if profile.id == profile_id), None)

    def _refresh_profiles(self, selected_id: str | None = None):
        if selected_id is None:
            current = self._selected_profile()
            selected_id = current.id if current else None
        self.profile_list.clear()
        for profile in self.state.profiles:
            item = QListWidgetItem(f"{profile.name}\n{profile.host}")
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            item.setSizeHint(QSize(170, 60))
            item.setToolTip(profile.endpoint)
            item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon))
            self.profile_list.addItem(item)
            if profile.id == selected_id:
                self.profile_list.setCurrentItem(item)
        if self.profile_list.currentRow() < 0 and self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        self._update_actions()

    def _profile_menu(self, position):
        item = self.profile_list.itemAt(position)
        if item is None:
            return
        self.profile_list.setCurrentItem(item)
        menu = QMenu(self)
        connect = menu.addAction("连接", self._connect_selected)
        edit = menu.addAction("编辑…", lambda: self._edit_profile(self._selected_profile()))
        delete = menu.addAction("删除连接…", self._delete_selected)
        connect.setEnabled(self.connect_button.isEnabled())
        edit.setEnabled(self.edit_button.isEnabled())
        delete.setEnabled(self.delete_button.isEnabled())
        menu.exec(self.profile_list.mapToGlobal(position))

    def _busy(self) -> bool:
        return bool(self._active_id or self._pending or self.connecting_session)

    def _edit_profile(self, profile: ServerProfile | None = None):
        if self._busy() or self._closing:
            self._notify("请先等待传输完成，或取消全部任务后管理服务器。")
            return
        if self.storage_error:
            self._notify_error(self.storage_error)
            return
        dialog = ProfileDialog(profile, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.profile is None:
            return
        updated = dialog.profile
        old_profiles = list(self.state.profiles)
        self.state.profiles = [updated if item.id == updated.id else item for item in old_profiles]
        if not any(item.id == updated.id for item in old_profiles):
            self.state.profiles.append(updated)
        if not self._save_state():
            self.state.profiles = old_profiles
            return
        if profile and self.session and self.session.profile.id == profile.id:
            self._disconnect_now()
        if profile and (profile.connection_key != updated.connection_key or not updated.remember_secret):
            self._secrets.pop(profile.id, None)
            if profile.remember_secret:
                self._forget_credentials(profile)
        if dialog.credentials is not None:
            self._secrets[updated.id] = dialog.credentials
        self._refresh_profiles(updated.id)
        self._notify(f"已保存 {updated.name}；点击连接服务器即可使用。")

    def _forget_credentials(self, profile: ServerProfile):
        previous = self._credential_barriers.get(profile.id)
        barrier = threading.Event()
        self._credential_barriers[profile.id] = barrier

        def forget():
            try:
                if previous:
                    previous.wait()
                self.secret_store.delete(profile)
            finally:
                barrier.set()

        self._run_worker(Worker(forget), error=lambda message: self._notify_error("未能移除钥匙串凭据：" + message))

    def _delete_selected(self):
        profile = self._selected_profile()
        if not profile or self._busy():
            return
        answer = QMessageBox.question(self, "删除服务器连接", f"删除“{profile.name}”的连接配置和已保存凭据？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        old_profiles = list(self.state.profiles)
        self.state.profiles = [item for item in old_profiles if item.id != profile.id]
        if not self._save_state():
            self.state.profiles = old_profiles
            return
        if self.session and self.session.profile.id == profile.id:
            self._disconnect_now()
        self.state.remote_paths.pop(profile.id, None)
        self._secrets.pop(profile.id, None)
        self._forget_credentials(profile)
        self._refresh_profiles()

    def _connect_selected(self):
        profile = self._selected_profile()
        if profile:
            self.connect_profile(profile)
        else:
            self._edit_profile()

    def connect_profile(self, profile: ServerProfile, credentials: Credentials | None = None):
        if self._active_id or self._closing or self.connecting_session:
            return
        if self._pending and any(self.tasks[job_id].job.profile.connection_key != profile.connection_key for job_id in self._pending):
            self._notify("请先取消等待中的任务，再切换到其他服务器。")
            return
        if self.session and self.session.is_connected and self.session.profile.connection_key == profile.connection_key:
            self._start_next()
            return
        self._disconnect_now()
        self._connection_token += 1
        token = self._connection_token
        cancellation = threading.Event()
        session = Session(profile, self.store)
        self.connecting_session = session
        self.connect_cancel = cancellation
        cached = credentials if credentials is not None else self._secrets.get(profile.id)
        barrier = self._credential_barriers.get(profile.id)
        self.connection_label.setText("● 正在连接…")
        self._notify(f"正在连接 {profile.endpoint}…")
        self._update_actions()

        def connect():
            credential = cached
            warning = ""
            if barrier:
                while not barrier.wait(0.1):
                    if cancellation.is_set():
                        raise TransferCancelled()
            if credential is None and profile.remember_secret:
                try:
                    credential = self.secret_store.get(profile)
                except Exception as exc:
                    warning = "无法读取钥匙串凭据：" + error_text(exc)
            if credential is None:
                credential = self.bridge.ask("credentials", profile, None, cancellation)
                if credential is None:
                    raise TransferCancelled()
            try:
                session.connect(credential, lambda host, algorithm, key: self.bridge.ask("host_key", (host, algorithm, key), False, cancellation), cancellation)
                if profile.remember_secret:
                    try:
                        self.secret_store.set(profile, credential)
                    except Exception as exc:
                        warning = "连接成功，但未能保存钥匙串凭据：" + error_text(exc, credential.secret)
                return session, credential, warning
            except Exception as exc:
                session.close()
                raise RuntimeError(error_text(exc, credential.secret)) from None

        self._run_worker(Worker(connect), result=lambda value: self._connected(value, token), error=lambda message: self._connection_failed(message, token))

    def _connected(self, value, token: int):
        session, credential, warning = value
        if token != self._connection_token or self._closing:
            session.close()
            return
        self.connecting_session = None
        self.connect_cancel = None
        self.session = session
        self._secrets[session.profile.id] = credential
        self.remote_pane.set_connected(True, f"{session.profile.id}:{token}")
        self.remote_pane.subtitle.setText(session.profile.name)
        self.remote_pane.subtitle.setToolTip(session.profile.endpoint)
        self.connection_label.setText("● 已连接")
        self.connection_label.setToolTip(session.profile.endpoint)
        self._notify(warning or f"已连接 {session.profile.endpoint}")
        self._browse_remote(self.state.remote_paths.get(session.profile.id, "."))
        self._update_actions()
        self._start_next()

    def _connection_failed(self, message: str, token: int):
        if token != self._connection_token:
            return
        self.connecting_session = None
        self.connect_cancel = None
        self.connection_label.setText("● 连接失败")
        self._notify_error(message)
        self._update_actions()

    def _disconnect(self):
        if self._active_id or self._pending:
            answer = QMessageBox.question(self, "断开连接", "断开连接将取消正在运行及等待中的传输任务。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._cancel_all()
        self._disconnect_now()
        self._notify("已断开服务器连接。")

    def _disconnect_now(self):
        self._connection_token += 1
        self._remote_token += 1
        if self.connect_cancel:
            self.connect_cancel.set()
        if self.connecting_session:
            self.connecting_session.close()
        self.connecting_session = None
        self.connect_cancel = None
        if self.session:
            self.session.close()
        self.session = None
        self.bridge.cancel_all()
        self.remote_pane.set_connected(False)
        self.connection_label.setText("● 未连接")
        self.connection_label.setToolTip("")
        self._update_actions()

    def _monitor_connection(self):
        if self.session and not self.session.is_connected:
            if self.remote_pane.table.profile_id:
                self._remote_token += 1
                self.remote_pane.set_connected(False)
                self.connection_label.setText("● 连接已断开")
                self._notify_error("连接已断开；等待中的任务已暂停，重新连接后可继续。")
            self._update_actions()

    def _browse_local(self, path: str):
        if self._closing:
            return
        self._local_token += 1
        token = self._local_token
        self.local_pane.set_loading()

        def ready(value):
            if token == self._local_token and not self._closing:
                directory, entries = value
                self.local_pane.set_entries(directory, entries)
                self.state.local_path = directory
                self._update_actions()

        def failed(message):
            if token != self._local_token or self._closing:
                return
            self.local_pane.set_error(message)
            if not self.local_pane.directory and path != str(Path.home()):
                self._browse_local(str(Path.home()))

        self._run_worker(Worker(lambda: list_local(path)), ready, failed)

    def _browse_remote(self, path: str):
        session = self.session
        if not session or not session.is_connected or self._closing:
            return
        self._remote_token += 1
        token = self._remote_token
        self.remote_pane.set_loading()

        def ready(value):
            if token == self._remote_token and self.session is session and not self._closing:
                directory, entries = value
                self.remote_pane.set_entries(directory, entries)
                self.state.remote_paths[session.profile.id] = directory
                self._update_actions()

        def failed(message):
            if token != self._remote_token or self._closing:
                return
            self.remote_pane.set_error(message)
            self._notify_error(message)
            if not self.remote_pane.directory and path != "." and session.is_connected:
                self._browse_remote(".")
            self._monitor_connection()

        self._run_worker(Worker(lambda: session.list_directory(path)), ready, failed)

    def _hidden_changed(self, checked: bool):
        self.state.show_hidden = checked
        for pane in (self.local_pane, self.remote_pane):
            pane.show_hidden = checked
            pane.render_entries()

    def _update_actions(self, *_args):
        if not hasattr(self, "local_pane"):
            return
        profile = self._selected_profile()
        connected = bool(self.session and self.session.is_connected)
        busy = self._busy()
        same = bool(connected and profile and self.session.profile.connection_key == profile.connection_key)
        self.connect_button.setEnabled(not self._closing and not self._active_id and not self.connecting_session and not same)
        self.connect_button.setText("添加服务器" if not profile else "连接服务器")
        self.disconnect_button.setEnabled(bool(self.session or self.connecting_session))
        self.add_button.setEnabled(not busy and not self.storage_error)
        self.edit_button.setEnabled(bool(profile) and not busy and not self.storage_error)
        self.delete_button.setEnabled(bool(profile) and not busy and not self.storage_error)
        target_ready = connected and bool(self.remote_pane.directory)
        self.upload_button.setEnabled(target_ready and bool(self.local_pane.table.selected_entries()))
        self.download_button.setEnabled(target_ready and bool(self.local_pane.directory) and bool(self.remote_pane.table.selected_entries()))
        self.choose_button.setEnabled(target_ready)

    def _choose_upload_files(self):
        if not self.session or not self.session.is_connected or not self.remote_pane.directory:
            self._notify("请先连接服务器并打开远程目标目录。")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择上传文件", self.local_pane.directory)
        if paths:
            self.enqueue(Direction.UPLOAD, tuple(paths), self.remote_pane.directory)

    def _choose_upload_folder(self):
        if not self.session or not self.session.is_connected or not self.remote_pane.directory:
            self._notify("请先连接服务器并打开远程目标目录。")
            return
        path = QFileDialog.getExistingDirectory(self, "选择上传文件夹", self.local_pane.directory)
        if path:
            self.enqueue(Direction.UPLOAD, (path,), self.remote_pane.directory)

    def _upload_selection(self):
        entries = self.local_pane.table.selected_entries()
        if entries:
            self.enqueue(Direction.UPLOAD, tuple(entry.path for entry in entries), self.remote_pane.directory)

    def _download_selection(self):
        entries = self.remote_pane.table.selected_entries()
        if entries:
            self.enqueue(Direction.DOWNLOAD, tuple(entry.path for entry in entries), self.local_pane.directory)

    def _drop_to_remote(self, payload: dict, target: str):
        if payload.get("side") == "local":
            self.enqueue(Direction.UPLOAD, tuple(payload["paths"]), target)

    def _drop_to_local(self, payload: dict, target: str):
        if payload.get("side") == "remote" and payload.get("profile_id") == self.remote_pane.table.profile_id:
            self.enqueue(Direction.DOWNLOAD, tuple(payload["paths"]), target)
        else:
            self._notify("拖拽来源连接已改变，请从当前服务器重新选择文件。")

    def enqueue(self, direction: Direction, sources: tuple[str, ...], destination: str) -> str | None:
        if not self.session or not self.session.is_connected or not destination or not sources or self._closing:
            self._notify("请先连接服务器并选择源文件及目标目录。")
            return None
        job = TransferJob(self.session.profile, direction, sources, destination)
        self._add_job(job)
        return job.id

    def _add_job(self, job: TransferJob):
        self.tasks[job.id] = TaskRecord(job)
        self._pending.append(job.id)
        self.queue.add_job(job)
        self._update_actions()
        self._start_next()

    def _start_next(self):
        if self._active_id or not self._pending or self._closing:
            return
        session = self.session
        job_id = self._pending[0]
        record = self.tasks[job_id]
        if not session or not session.is_connected or session.profile.connection_key != record.job.profile.connection_key:
            self.queue.rows[job_id]["state"].setText("等待连接")
            return
        self._pending.popleft()
        self._active_id = job_id
        cancellation = threading.Event()
        worker = Worker(lambda: engine.run())
        engine = TransferEngine(
            session, record.job, worker.signals.progress.emit,
            lambda entry, size: self.bridge.ask("conflict", (entry, size), ConflictDecision(ConflictAction.CANCEL), cancellation),
            cancellation,
        )
        self._engine = engine
        worker.signals.progress.connect(self.queue.update_progress)
        self._run_worker(worker, self._transfer_finished, lambda message: self._transfer_finished(TransferResult(job_id, error=message, connection_lost=not session.is_connected)))
        self._update_actions()

    @Slot(object)
    def _transfer_finished(self, result: TransferResult):
        record = self.tasks.get(result.job_id)
        if record is None:
            return
        record.result = result
        self.queue.finish(result)
        if result.job_id == self._active_id:
            self._active_id = None
            self._engine = None
        self._notify(f"{record.job.label}：{result.status}，完成 {result.completed_files} 个文件。")
        if result.error:
            self._notify_error(result.error)
        if not self._closing:
            if record.job.direction == Direction.UPLOAD and self.remote_pane.directory:
                self._browse_remote(self.remote_pane.directory)
            elif record.job.direction == Direction.DOWNLOAD and self.local_pane.directory:
                self._browse_local(self.local_pane.directory)
            self._monitor_connection()
            self._update_actions()
            self._start_next()

    def _cancel_task(self, job_id: str):
        if job_id == self._active_id and self._engine:
            self.queue.cancelling(job_id)
            self._engine.cancel()
            self.bridge.cancel_all()
        elif job_id in self._pending:
            self._pending.remove(job_id)
            result = TransferResult(job_id, cancelled=True)
            self.tasks[job_id].result = result
            self.queue.finish(result)
            self._update_actions()

    def _cancel_all(self):
        for job_id in list(self._pending):
            self._cancel_task(job_id)
        if self._active_id:
            self._cancel_task(self._active_id)

    def _retry_task(self, job_id: str):
        record = self.tasks.get(job_id)
        if record is None or record.result is None or self._closing:
            return
        if not self.session or not self.session.is_connected or self.session.profile.connection_key != record.job.profile.connection_key:
            self._notify("请先连接此任务原先使用的服务器，再点击重试。")
            return
        entries = tuple(failed.entry for failed in record.result.failed) if record.result.failed else record.job.retry_entries
        retry = TransferJob(record.job.profile, record.job.direction, record.job.sources, record.job.destination, retry_entries=entries)
        self.queue.retried(job_id)
        self._add_job(retry)

    def _show_task_details(self, job_id: str):
        record = self.tasks.get(job_id)
        if record and record.result:
            show_result(record.result, self)

    def _clear_records(self):
        for job_id in self.queue.clear_finished():
            self.tasks.pop(job_id, None)

    @Slot(object)
    def _handle_decision(self, request: DecisionRequest):
        if self._closing or request.event.is_set():
            request.resolve(request.default)
            return
        dialog = None
        try:
            if request.kind == "host_key":
                host, algorithm, key = request.payload
                dialog = QMessageBox(self)
                dialog.setWindowTitle("确认服务器身份")
                dialog.setIcon(QMessageBox.Icon.Question)
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setText(f"首次连接 {host}\n\n主机密钥类型：{algorithm}\n指纹：{key}\n\n确认这是你的服务器后，才信任并继续连接。")
                trust = dialog.addButton("信任并连接", QMessageBox.ButtonRole.AcceptRole)
                cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                dialog.setDefaultButton(cancel)
                self._decision_dialog = dialog
                dialog.exec()
                request.resolve(dialog.clickedButton() == trust)
            elif request.kind == "credentials":
                profile = request.payload
                dialog = QInputDialog(self)
                dialog.setWindowTitle(f"登录 {profile.name}")
                label = "请输入 SSH 密码" if profile.auth_method == AuthMethod.PASSWORD else "请输入私钥口令（未加密私钥可留空）"
                dialog.setLabelText(profile.endpoint + "\n" + label)
                dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
                dialog.setOkButtonText("连接")
                dialog.setCancelButtonText("取消")
                self._decision_dialog = dialog
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
                request.resolve(Credentials(dialog.textValue()) if accepted else None)
                dialog.setTextValue("")
            elif request.kind == "conflict":
                entry, size = request.payload
                dialog = ConflictDialog(entry, size, self)
                self._decision_dialog = dialog
                dialog.exec()
                request.resolve(dialog.decision)
            else:
                request.resolve(request.default)
        finally:
            self._decision_dialog = None
            request.resolve(request.default)
            if dialog:
                dialog.deleteLater()

    def closeEvent(self, event):
        if not self._closing and (self._active_id or self._pending):
            answer = QMessageBox.question(self, "退出 ScpPro", "退出将取消尚未完成的传输任务。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if not self._closing:
            self._closing = True
            self.monitor.stop()
            self._cancel_all()
            self.bridge.cancel_all(close=True)
            if self._decision_dialog:
                self._decision_dialog.reject()
            self._disconnect_now()
            self._save_state()
        if self.pool.activeThreadCount():
            event.ignore()
            self.setWindowTitle("ScpPro · 正在结束连接…")
            QTimer.singleShot(100, self.close)
            return
        self._secrets.clear()
        event.accept()
