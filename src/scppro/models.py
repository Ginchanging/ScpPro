from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class AuthMethod(StrEnum):
    PASSWORD = "password"
    KEY = "key"


@dataclass(frozen=True)
class ServerProfile:
    name: str
    host: str
    username: str
    port: int = 22
    auth_method: AuthMethod = AuthMethod.PASSWORD
    key_path: str = ""
    remember_secret: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("请填写服务器名称。")
        try:
            ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise ValueError("请填写有效的 IPv4 或 IPv6 地址。") from exc
        if not self.username.strip():
            raise ValueError("请填写 SSH 用户名。")
        if not 1 <= self.port <= 65535:
            raise ValueError("端口应在 1–65535 之间。")
        if self.auth_method == AuthMethod.KEY and not self.key_path:
            raise ValueError("请选择 SSH 私钥文件。")
        if not self.id or any(char in self.id for char in "/\\\x00"):
            raise ValueError("服务器配置标识无效。")

    @property
    def endpoint(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.username}@{host}:{self.port}"

    @property
    def connection_key(self) -> tuple:
        return (self.id, self.host, self.port, self.username, self.auth_method, self.key_path)


@dataclass(frozen=True)
class Credentials:
    secret: str = ""


class EntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SPECIAL = "special"


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    kind: EntryKind
    size: int = 0
    modified: float = 0


class Direction(StrEnum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


class ConflictAction(StrEnum):
    OVERWRITE = "overwrite"
    SKIP = "skip"
    KEEP_BOTH = "keep_both"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ConflictDecision:
    action: ConflictAction
    apply_to_batch: bool = False


@dataclass(frozen=True)
class PlannedEntry:
    source: str
    destination: str
    kind: EntryKind
    size: int = 0
    modified: float = 0


@dataclass(frozen=True)
class FailedEntry:
    entry: PlannedEntry
    error: str


@dataclass(frozen=True)
class TransferJob:
    profile: ServerProfile
    direction: Direction
    sources: tuple[str, ...]
    destination: str
    retry_entries: tuple[PlannedEntry, ...] = ()
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def label(self) -> str:
        if self.retry_entries:
            name = Path(self.retry_entries[0].source).name
            count = len(self.retry_entries)
        else:
            name = Path(self.sources[0]).name if self.sources else "传输任务"
            count = len(self.sources)
        return name if count == 1 else f"{name} 等 {count} 项"


@dataclass(frozen=True)
class TransferProgress:
    job_id: str
    stage: str
    filename: str = ""
    completed_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0
    completed_files: int = 0
    total_files: int = 0


@dataclass
class TransferResult:
    job_id: str
    completed_files: int = 0
    skipped: list[str] = field(default_factory=list)
    failed: list[FailedEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False
    transferred_bytes: int = 0
    error: str = ""
    connection_lost: bool = False

    @property
    def status(self) -> str:
        if self.cancelled:
            return "已取消"
        if self.failed or self.error:
            return "部分失败" if self.completed_files else "失败"
        if self.skipped or self.warnings:
            return "完成（含跳过项）" if self.completed_files else "已跳过"
        return "已完成"


def format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"
