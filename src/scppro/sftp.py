from __future__ import annotations

import base64
import hashlib
import socket
import stat
import threading
from pathlib import Path
from typing import Callable

import paramiko

from .models import AuthMethod, Credentials, EntryKind, FileEntry, ServerProfile
from .storage import ProfileStore


class TransferCancelled(Exception):
    pass


class ConnectionLost(Exception):
    pass


class HostKeyRejected(Exception):
    pass


def fingerprint(key: paramiko.PKey) -> str:
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    return "SHA256:" + digest


def error_text(error: BaseException, secret: str = "") -> str:
    if isinstance(error, paramiko.BadHostKeyException):
        message = (
            "服务器主机密钥已变更，连接已停止。\n"
            f"已保存：{fingerprint(error.expected_key)}\n"
            f"收到：{fingerprint(error.key)}"
        )
    elif isinstance(error, paramiko.AuthenticationException):
        message = "SSH 认证失败，请检查用户名、密码、私钥或服务器的登录策略。"
    elif isinstance(error, paramiko.PasswordRequiredException):
        message = "此私钥已加密，请输入私钥口令。"
    elif isinstance(error, paramiko.ssh_exception.NoValidConnectionsError):
        message = "无法连接服务器，请检查 IP、端口、网络和 SSH 服务。"
    elif isinstance(error, (socket.timeout, TimeoutError)):
        message = "操作超时，请检查网络连接后重试。"
    elif isinstance(error, PermissionError):
        message = "没有访问或写入此目录的权限。"
    elif isinstance(error, TransferCancelled):
        message = "传输已取消。"
    else:
        message = str(error).strip() or type(error).__name__
    return message.replace(secret, "••••••") if secret else message


def entry_kind(mode: int | None) -> EntryKind:
    if mode is None:
        return EntryKind.SPECIAL
    if stat.S_ISLNK(mode):
        return EntryKind.SYMLINK
    if stat.S_ISDIR(mode):
        return EntryKind.DIRECTORY
    if stat.S_ISREG(mode):
        return EntryKind.FILE
    return EntryKind.SPECIAL


def valid_component(name: str) -> bool:
    return bool(name) and name not in (".", "..") and "/" not in name and "\x00" not in name


def list_local(path: str) -> tuple[str, list[FileEntry]]:
    directory = Path(path).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise NotADirectoryError("请选择一个目录。")
    entries = []
    for child in directory.iterdir():
        try:
            attributes = child.lstat()
        except FileNotFoundError:
            continue
        entries.append(FileEntry(child.name, str(child), entry_kind(attributes.st_mode), attributes.st_size, attributes.st_mtime))
    return str(directory), entries


class ConfirmHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, store: ProfileStore, confirm: Callable[[str, str, str], bool], cancellation: threading.Event):
        self.store = store
        self.confirm = confirm
        self.cancellation = cancellation

    def missing_host_key(self, client, hostname, key):
        if self.cancellation.is_set():
            raise TransferCancelled()
        if not self.confirm(hostname, key.get_name(), fingerprint(key)):
            raise HostKeyRejected("未信任此服务器，连接已取消。")
        if self.cancellation.is_set():
            raise TransferCancelled()
        client.get_host_keys().add(hostname, key.get_name(), key)
        self.store.save_host_keys(client.get_host_keys())


class Session:
    """One SSH transport, with a separate SFTP channel for each worker."""

    def __init__(self, profile: ServerProfile, store: ProfileStore):
        self.profile = profile
        self.store = store
        self._client: paramiko.SSHClient | None = None
        self._lock = threading.Lock()
        self._closed = False

    def connect(self, credentials: Credentials, confirm_host_key: Callable[[str, str, str], bool], cancellation: threading.Event) -> None:
        self.profile.validate()
        client = paramiko.SSHClient()
        with self._lock:
            if self._closed:
                raise TransferCancelled()
            self._client = client
        try:
            if self.store.known_hosts_path.exists():
                client.load_host_keys(str(self.store.known_hosts_path))
            client.set_missing_host_key_policy(ConfirmHostKeyPolicy(self.store, confirm_host_key, cancellation))
            kwargs = {
                "hostname": self.profile.host, "port": self.profile.port,
                "username": self.profile.username, "timeout": 15,
                "banner_timeout": 15, "auth_timeout": 20, "channel_timeout": 15,
                "allow_agent": False, "look_for_keys": False,
            }
            if self.profile.auth_method == AuthMethod.PASSWORD:
                kwargs["password"] = credentials.secret
            else:
                key_file = Path(self.profile.key_path).expanduser()
                if not key_file.is_file():
                    raise FileNotFoundError("找不到 SSH 私钥文件。")
                kwargs["key_filename"] = str(key_file)
                kwargs["passphrase"] = credentials.secret or None
            if cancellation.is_set():
                raise TransferCancelled()
            client.connect(**kwargs)
            if cancellation.is_set():
                raise TransferCancelled()
            transport = client.get_transport()
            if transport is None:
                raise ConnectionLost("SSH 连接未建立。")
            transport.set_keepalive(30)
            # Confirm SFTP availability before reporting a usable connection.
            with self.open_sftp() as sftp:
                sftp.normalize(".")
        except BaseException:
            self.close()
            raise

    @property
    def is_connected(self) -> bool:
        with self._lock:
            client = self._client
        transport = client.get_transport() if client else None
        return bool(transport and transport.is_active() and transport.is_authenticated())

    def open_sftp(self) -> paramiko.SFTPClient:
        with self._lock:
            client = self._client
        if not client or not self.is_connected:
            raise ConnectionLost("服务器连接已断开，请重新连接后重试。")
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(30)
        return sftp

    def list_directory(self, path: str) -> tuple[str, list[FileEntry]]:
        import posixpath

        with self.open_sftp() as sftp:
            canonical = sftp.normalize(path or ".")
            entries = []
            for attributes in sftp.listdir_attr(canonical):
                name = attributes.filename
                if not valid_component(name):
                    continue
                entries.append(FileEntry(
                    name, posixpath.join(canonical, name), entry_kind(attributes.st_mode),
                    attributes.st_size or 0, attributes.st_mtime or 0,
                ))
            return canonical, entries

    def close(self) -> None:
        with self._lock:
            self._closed = True
            client, self._client = self._client, None
        if client:
            client.close()
