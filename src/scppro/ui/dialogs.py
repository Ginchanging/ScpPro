from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from ..models import AuthMethod, ConflictAction, ConflictDecision, Credentials, PlannedEntry, ServerProfile, TransferResult, format_size


class ProfileDialog(QDialog):
    def __init__(self, profile: ServerProfile | None = None, parent=None):
        super().__init__(parent)
        self.original = profile
        self.profile: ServerProfile | None = None
        self.credentials: Credentials | None = None
        self.setWindowTitle("编辑服务器" if profile else "添加服务器")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        title = QLabel("服务器连接")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("填写服务器的 SSH 登录信息，即可通过 SFTP 传输文件。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        form = QFormLayout()
        form.setSpacing(12)
        self.name_edit = QLineEdit(profile.name if profile else "")
        self.name_edit.setPlaceholderText("例如：工作服务器")
        self.host_edit = QLineEdit(profile.host if profile else "")
        self.host_edit.setPlaceholderText("例如：192.168.1.10")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(profile.port if profile else 22)
        self.username_edit = QLineEdit(profile.username if profile else "")
        self.username_edit.setPlaceholderText("SSH 用户名")
        self.auth_combo = QComboBox()
        self.auth_combo.addItem("密码登录", AuthMethod.PASSWORD)
        self.auth_combo.addItem("SSH 私钥", AuthMethod.KEY)
        self.key_edit = QLineEdit(profile.key_path if profile else "")
        self.key_edit.setPlaceholderText("~/.ssh/id_ed25519")
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_edit)
        key_button = QPushButton("选择…")
        key_button.clicked.connect(self._choose_key)
        key_layout.addWidget(key_button)
        self.key_row = key_row
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_label = QLabel("密码")
        self.remember_check = QCheckBox("保存密码到 macOS 钥匙串")
        self.remember_check.setChecked(profile.remember_secret if profile else False)
        self.saved_hint = QLabel("留空保留已保存的凭据；也可在连接时输入。")
        self.saved_hint.setObjectName("muted")
        self.saved_hint.setWordWrap(True)
        for label, widget in (("名称", self.name_edit), ("IP 地址", self.host_edit), ("SSH 端口", self.port_edit), ("用户名", self.username_edit), ("认证方式", self.auth_combo), ("私钥文件", key_row)):
            form.addRow(label, widget)
        self.key_label = form.labelForField(key_row)
        form.addRow(self.secret_label, self.secret_edit)
        form.addRow("", self.remember_check)
        form.addRow("", self.saved_hint)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存服务器")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.auth_combo.currentIndexChanged.connect(self._auth_changed)
        if profile and profile.auth_method == AuthMethod.KEY:
            self.auth_combo.setCurrentIndex(1)
        self._auth_changed()

    def _auth_changed(self):
        is_key = self.auth_combo.currentData() == AuthMethod.KEY
        self.key_row.setVisible(is_key)
        self.key_label.setVisible(is_key)
        self.secret_label.setText("私钥口令" if is_key else "密码")
        self.secret_edit.setPlaceholderText("未加密私钥可留空" if is_key else "连接密码")
        self.remember_check.setText("保存私钥口令到 macOS 钥匙串" if is_key else "保存密码到 macOS 钥匙串")

    def _choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 SSH 私钥", str(Path.home() / ".ssh"))
        if path:
            self.key_edit.setText(path)

    def _save(self):
        fields = dict(
            name=self.name_edit.text().strip(), host=self.host_edit.text().strip(),
            username=self.username_edit.text().strip(), port=self.port_edit.value(),
            auth_method=self.auth_combo.currentData(), key_path=self.key_edit.text().strip(),
            remember_secret=self.remember_check.isChecked(),
        )
        profile = replace(self.original, **fields) if self.original else ServerProfile(**fields)
        try:
            profile.validate()
            if profile.auth_method == AuthMethod.KEY and not Path(profile.key_path).expanduser().is_file():
                raise ValueError("找不到所选私钥文件。")
        except ValueError as exc:
            QMessageBox.warning(self, "请检查连接信息", str(exc))
            return
        self.profile = profile
        if self.secret_edit.text():
            self.credentials = Credentials(self.secret_edit.text())
        self.secret_edit.clear()
        self.accept()


class ConflictDialog(QDialog):
    def __init__(self, entry: PlannedEntry, existing_size: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("目标已存在")
        self.setMinimumWidth(470)
        self.decision = ConflictDecision(ConflictAction.CANCEL)
        layout = QVBoxLayout(self)
        heading = QLabel(f"已存在同名文件：{Path(entry.destination).name}")
        heading.setObjectName("dialogTitle")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        path = QLabel(entry.destination)
        path.setTextFormat(Qt.TextFormat.PlainText)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setWordWrap(True)
        path.setObjectName("muted")
        layout.addWidget(path)
        layout.addWidget(QLabel(f"已有文件：{format_size(existing_size)}　　传入文件：{format_size(entry.size)}"))
        self.apply_check = QCheckBox("将选择应用于当前批次的所有同名文件")
        layout.addWidget(self.apply_check)
        buttons = QHBoxLayout()
        for text, action in (("取消批次", ConflictAction.CANCEL), ("跳过", ConflictAction.SKIP), ("保留两份", ConflictAction.KEEP_BOTH), ("覆盖", ConflictAction.OVERWRITE)):
            button = QPushButton(text)
            if action == ConflictAction.KEEP_BOTH:
                button.setDefault(True)
                button.setObjectName("primary")
            button.clicked.connect(lambda _checked=False, choice=action: self._choose(choice))
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _choose(self, action):
        self.decision = ConflictDecision(action, self.apply_check.isChecked())
        self.accept()


def show_result(result: TransferResult, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle("传输结果")
    dialog.resize(680, 430)
    layout = QVBoxLayout(dialog)
    lines = [f"状态：{result.status}", f"已完成文件：{result.completed_files}", f"实际传输：{format_size(result.transferred_bytes)}"]
    if result.error:
        lines += ["", "错误", result.error]
    if result.failed:
        lines += ["", "失败或尚未完成"]
        for failed in result.failed:
            lines.append(f"{failed.entry.source}\n  → {failed.entry.destination}\n  {failed.error}")
    if result.skipped:
        lines += ["", "跳过项", *result.skipped]
    if result.warnings:
        lines += ["", "提示", *result.warnings]
    text = QPlainTextEdit("\n".join(lines))
    text.setReadOnly(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
