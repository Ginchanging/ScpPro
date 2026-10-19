from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..models import Direction, TransferJob, TransferProgress, TransferResult, format_size


class QueuePane(QFrame):
    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    details_requested = Signal(str)
    clear_requested = Signal()
    cancel_all_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("queuePane")
        self.rows: dict[str, dict] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        label = QLabel("传输队列")
        label.setObjectName("paneTitle")
        header.addWidget(label)
        self.summary = QLabel("等待添加传输任务")
        self.summary.setObjectName("muted")
        header.addWidget(self.summary)
        header.addStretch()
        cancel = QPushButton("取消全部")
        cancel.clicked.connect(self.cancel_all_requested)
        header.addWidget(cancel)
        clear = QPushButton("清除记录")
        clear.clicked.connect(self.clear_requested)
        header.addWidget(clear)
        layout.addLayout(header)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["文件 / 文件夹", "方向", "状态", "进度", "速度", "操作"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 4, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 56)
        self.table.setColumnWidth(2, 114)
        self.table.setColumnWidth(4, 95)
        self.table.setColumnWidth(5, 145)
        layout.addWidget(self.table)

    def add_job(self, job: TransferJob):
        row = self.table.rowCount()
        self.table.insertRow(row)
        name = QTableWidgetItem(job.label)
        name.setData(Qt.ItemDataRole.UserRole, job.id)
        name.setToolTip(f"服务器：{job.profile.endpoint}\n源：" + "\n".join(job.sources) + f"\n目标：{job.destination}")
        direction = QTableWidgetItem("↑ 上传" if job.direction == Direction.UPLOAD else "↓ 下载")
        state = QTableWidgetItem("等待中")
        speed = QTableWidgetItem("—")
        for column, item in ((0, name), (1, direction), (2, state), (4, speed)):
            self.table.setItem(row, column, item)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setFormat("等待中")
        progress.setMinimumWidth(145)
        progress.setFixedHeight(22)
        progress_holder = QWidget()
        progress_layout = QHBoxLayout(progress_holder)
        progress_layout.setContentsMargins(6, 12, 10, 12)
        progress_layout.addWidget(progress)
        self.table.setCellWidget(row, 3, progress_holder)
        operations = QWidget()
        operations_layout = QHBoxLayout(operations)
        operations_layout.setContentsMargins(2, 7, 2, 7)
        operations_layout.setSpacing(4)
        cancel = QPushButton("取消")
        retry = QPushButton("重试")
        details = QPushButton("详情")
        retry.hide()
        details.hide()
        cancel.clicked.connect(lambda _checked=False: self.cancel_requested.emit(job.id))
        retry.clicked.connect(lambda _checked=False: self.retry_requested.emit(job.id))
        details.clicked.connect(lambda _checked=False: self.details_requested.emit(job.id))
        for button in (cancel, retry, details):
            operations_layout.addWidget(button)
        self.table.setCellWidget(row, 5, operations)
        self.rows[job.id] = {"state": state, "speed": speed, "progress": progress, "cancel": cancel, "retry": retry, "details": details, "done": False}
        self.table.scrollToBottom()
        self._update_summary()

    def update_progress(self, progress: TransferProgress):
        row = self.rows.get(progress.job_id)
        if row is None:
            return
        row["state"].setText(progress.stage)
        row["state"].setToolTip(progress.filename)
        bar = row["progress"]
        if progress.stage == "正在扫描":
            bar.setRange(0, 0)
            bar.setFormat("扫描目录")
        else:
            bar.setRange(0, 100)
            percentage = int(progress.completed_bytes / progress.total_bytes * 100) if progress.total_bytes else 0
            bar.setValue(min(percentage, 100))
            bar.setFormat(f"{format_size(progress.completed_bytes)} / {format_size(progress.total_bytes)}")
        row["speed"].setText(format_size(progress.speed) + "/s" if progress.speed else "—")

    def finish(self, result: TransferResult):
        row = self.rows.get(result.job_id)
        if row is None:
            return
        row["state"].setText(result.status)
        row["state"].setToolTip(result.error or "\n".join(failed.error for failed in result.failed[:3]))
        row["progress"].setRange(0, 100)
        if not result.failed and not result.error and not result.cancelled:
            row["progress"].setValue(100)
        row["progress"].setFormat(f"{result.completed_files} 个文件 · {format_size(result.transferred_bytes)}")
        row["speed"].setText("—")
        row["cancel"].hide()
        row["details"].show()
        if result.failed or result.error or result.cancelled:
            row["retry"].show()
        row["done"] = True
        self._update_summary()

    def cancelling(self, job_id: str):
        row = self.rows.get(job_id)
        if row:
            row["state"].setText("正在取消")
            row["cancel"].setEnabled(False)

    def retried(self, job_id: str):
        row = self.rows.get(job_id)
        if row:
            row["retry"].setText("已重试")
            row["retry"].setEnabled(False)

    def clear_finished(self) -> list[str]:
        removed = []
        for row_index in range(self.table.rowCount() - 1, -1, -1):
            job_id = self.table.item(row_index, 0).data(Qt.ItemDataRole.UserRole)
            if self.rows[job_id]["done"]:
                self.table.removeRow(row_index)
                del self.rows[job_id]
                removed.append(job_id)
        self._update_summary()
        return removed

    def _update_summary(self):
        active = sum(not row["done"] for row in self.rows.values())
        complete = len(self.rows) - active
        self.summary.setText(f"{active} 个待处理 · {complete} 个已结束" if self.rows else "等待添加传输任务")
