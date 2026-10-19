from __future__ import annotations

import json
import posixpath
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..models import EntryKind, FileEntry, format_size

MIME_TYPE = "application/x-scppro-files"
KIND_LABELS = {EntryKind.DIRECTORY: "文件夹", EntryKind.FILE: "文件", EntryKind.SYMLINK: "符号链接", EntryKind.SPECIAL: "特殊文件"}


class FileItem(QTableWidgetItem):
    def __init__(self, text: str, entry: FileEntry, column: int):
        super().__init__(text)
        self.entry = entry
        self.column = column
        self.setData(Qt.ItemDataRole.UserRole, entry)
        self.setToolTip(entry.path)

    def __lt__(self, other):
        if not isinstance(other, FileItem):
            return super().__lt__(other)
        if self.entry.kind == EntryKind.DIRECTORY and other.entry.kind != EntryKind.DIRECTORY:
            return True
        if self.entry.kind != EntryKind.DIRECTORY and other.entry.kind == EntryKind.DIRECTORY:
            return False
        if self.column == 1:
            return self.entry.size < other.entry.size
        if self.column == 3:
            return self.entry.modified < other.entry.modified
        return self.text().casefold() < other.text().casefold()


class FileTable(QTableWidget):
    files_dropped = Signal(object, str)

    def __init__(self, side: str, parent=None):
        super().__init__(0, 4, parent)
        self.side = side
        self.directory = ""
        self.profile_id = ""
        self.drop_enabled = side == "local"
        self.setHorizontalHeaderLabels(["名称", "大小", "类型", "修改时间"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(1, 82)
        self.setColumnWidth(2, 78)
        self.setColumnWidth(3, 142)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def selected_entries(self) -> list[FileEntry]:
        return [self.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in self.selectionModel().selectedRows()]

    def startDrag(self, _actions):
        entries = self.selected_entries()
        if not entries or (self.side == "remote" and not self.profile_id):
            return
        mime = QMimeData()
        mime.setData(MIME_TYPE, json.dumps({
            "side": self.side, "profile_id": self.profile_id,
            "paths": [entry.path for entry in entries],
        }).encode("utf-8"))
        if self.side == "local":
            mime.setUrls([QUrl.fromLocalFile(entry.path) for entry in entries])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _drop_payload(self, mime) -> dict | None:
        if not self.drop_enabled or not self.directory:
            return None
        if mime.hasFormat(MIME_TYPE):
            try:
                data = json.loads(bytes(mime.data(MIME_TYPE)))
                if data.get("side") not in ("local", "remote") or data["side"] == self.side:
                    return None
                if not isinstance(data.get("paths"), list) or not data["paths"] or not all(isinstance(path, str) for path in data["paths"]):
                    return None
                return data
            except (ValueError, TypeError, KeyError):
                return None
        if self.side == "remote" and mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                return {"side": "local", "paths": paths, "profile_id": ""}
        return None

    def dragEnterEvent(self, event):
        if self._drop_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drop_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        payload = self._drop_payload(event.mimeData())
        if payload is None:
            event.ignore()
            return
        target = self.directory
        item = self.itemAt(event.position().toPoint())
        if item:
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry.kind == EntryKind.DIRECTORY:
                target = entry.path
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.files_dropped.emit(payload, target)


class FilePane(QFrame):
    navigate = Signal(str)
    selection_changed = Signal()
    files_dropped = Signal(object, str)

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.side = side
        self.directory = ""
        self.entries: list[FileEntry] = []
        self.show_hidden = False
        self.setObjectName("filePane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title = QLabel("本机文件" if side == "local" else "服务器文件")
        self.title.setObjectName("paneTitle")
        self.subtitle = QLabel("LOCAL" if side == "local" else "SFTP")
        self.subtitle.setObjectName("badge")
        heading.addWidget(self.title)
        heading.addStretch()
        heading.addWidget(self.subtitle)
        layout.addLayout(heading)
        navigation = QHBoxLayout()
        navigation.setSpacing(6)
        self.up_button = QPushButton("↑")
        self.up_button.setToolTip("返回上级目录")
        self.up_button.setFixedWidth(30)
        self.home_button = QPushButton("⌂")
        self.home_button.setToolTip("用户主目录")
        self.home_button.setFixedWidth(30)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("输入本机路径" if side == "local" else "连接后输入远程路径")
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setToolTip("刷新目录")
        self.refresh_button.setFixedWidth(30)
        for widget in (self.up_button, self.home_button, self.path_edit, self.refresh_button):
            navigation.addWidget(widget)
        layout.addLayout(navigation)
        self.table = FileTable(side)
        layout.addWidget(self.table, 1)
        self.footer = QLabel("正在读取本机目录…" if side == "local" else "选择或添加服务器，然后点击连接。")
        self.footer.setObjectName("muted")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)
        self.path_edit.returnPressed.connect(lambda: self.navigate.emit(self.path_edit.text().strip()))
        self.up_button.clicked.connect(self._go_up)
        self.home_button.clicked.connect(lambda: self.navigate.emit(str(Path.home()) if self.side == "local" else "."))
        self.refresh_button.clicked.connect(lambda: self.navigate.emit(self.directory))
        self.table.itemDoubleClicked.connect(self._open_entry)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.files_dropped.connect(self.files_dropped)
        if side == "remote":
            self.set_connected(False)

    def _go_up(self):
        if self.directory:
            path = str(Path(self.directory).parent) if self.side == "local" else posixpath.dirname(self.directory.rstrip("/")) or "/"
            self.navigate.emit(path)

    def _open_entry(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry.kind == EntryKind.DIRECTORY:
            self.navigate.emit(entry.path)

    def _selection_changed(self):
        count = len(self.table.selected_entries())
        visible = self.table.rowCount()
        self.footer.setText(f"{visible} 项" + (f" · 已选 {count} 项" if count else ""))
        self.selection_changed.emit()

    def set_connected(self, connected: bool, profile_id: str = "") -> None:
        self.table.profile_id = profile_id if connected else ""
        self.table.drop_enabled = connected if self.side == "remote" else True
        for widget in (self.up_button, self.home_button, self.path_edit, self.refresh_button):
            widget.setEnabled(connected or self.side == "local")
        if not connected and self.side == "remote":
            self.directory = ""
            self.table.directory = ""
            self.entries = []
            self.table.setRowCount(0)
            self.path_edit.clear()
            self.footer.setText("选择或添加服务器，然后点击连接。")
            self.subtitle.setText("未连接")

    def set_loading(self):
        self.footer.setText("正在读取目录…")

    def set_error(self, message: str):
        self.path_edit.setText(self.directory)
        self.footer.setText(message)
        self.footer.setToolTip(message)

    def set_entries(self, directory: str, entries: list[FileEntry]):
        self.directory = directory
        self.table.directory = directory
        self.path_edit.setText(directory)
        self.entries = entries
        self.render_entries()

    def render_entries(self):
        selected = {entry.path for entry in self.table.selected_entries()}
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        visible = [entry for entry in self.entries if self.show_hidden or not entry.name.startswith(".")]
        self.table.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            modified = datetime.fromtimestamp(entry.modified).strftime("%Y-%m-%d %H:%M") if entry.modified else "—"
            values = (entry.name, format_size(entry.size) if entry.kind == EntryKind.FILE else "—", KIND_LABELS[entry.kind], modified)
            for column, text in enumerate(values):
                item = FileItem(text, entry, column)
                if column == 0:
                    icon = QStyle.StandardPixmap.SP_DirIcon if entry.kind == EntryKind.DIRECTORY else QStyle.StandardPixmap.SP_FileIcon
                    item.setIcon(self.style().standardIcon(icon))
                if column == 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(sort_column, sort_order)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).entry.path in selected:
                self.table.selectionModel().select(self.table.model().index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.footer.setToolTip("")
        self._selection_changed()
