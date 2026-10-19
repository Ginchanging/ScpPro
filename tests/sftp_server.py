"""Isolated loopback SSH/SFTP server used for real protocol integration tests."""
from __future__ import annotations

import errno
import os
import posixpath
import socket
import stat
import threading
import time
from pathlib import Path

import paramiko


class SSHServer(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if username == "tester" and password == "test-password" else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL if username == "tester" and key.asbytes() == self.owner.user_key.asbytes() else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_channel_request(self, kind, channel_id):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class FileHandle(paramiko.SFTPHandle):
    def __init__(self, flags, path, owner):
        super().__init__(flags)
        self.path = path
        self.owner = owner

    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno() if hasattr(self, "readfile") else self.writefile.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def read(self, offset, length):
        if self.owner.delay:
            time.sleep(self.owner.delay)
        return super().read(offset, length)

    def write(self, offset, data):
        if self.owner.delay:
            time.sleep(self.owner.delay)
        return super().write(offset, data)


class Filesystem(paramiko.SFTPServerInterface):
    def __init__(self, server, owner):
        super().__init__(server)
        self.owner = owner

    def canonicalize(self, path):
        return posixpath.normpath("/" + path.lstrip("/"))

    def _path(self, path):
        normalized = self.canonicalize(path)
        target = self.owner.root / normalized.lstrip("/")
        if not target.parent.resolve().is_relative_to(self.owner.root.resolve()) and target != self.owner.root:
            raise PermissionError(errno.EACCES, "Outside test root")
        return target

    def _denied(self, path):
        normalized = self.canonicalize(path)
        return any(normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/") for prefix in self.owner.denied)

    def list_folder(self, path):
        try:
            if self._denied(path):
                return paramiko.SFTP_PERMISSION_DENIED
            entries = []
            for item in self._path(path).iterdir():
                attributes = paramiko.SFTPAttributes.from_stat(item.lstat())
                attributes.filename = item.name
                entries.append(attributes)
            return entries
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        try:
            if self._denied(path):
                return paramiko.SFTP_PERMISSION_DENIED
            return paramiko.SFTPAttributes.from_stat(self._path(path).stat())
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def lstat(self, path):
        try:
            if self._denied(path):
                return paramiko.SFTP_PERMISSION_DENIED
            return paramiko.SFTPAttributes.from_stat(self._path(path).lstat())
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def open(self, path, flags, attr):
        try:
            if self._denied(path):
                return paramiko.SFTP_PERMISSION_DENIED
            target = self._path(path)
            descriptor = os.open(target, flags, attr.st_mode or 0o666)
            mode = "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb"
            stream = os.fdopen(descriptor, mode, buffering=0)
            handle = FileHandle(flags, target, self.owner)
            if flags & (os.O_WRONLY | os.O_RDWR):
                handle.writefile = stream
            if not flags & os.O_WRONLY:
                handle.readfile = stream
            return handle
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def mkdir(self, path, attr):
        try:
            if self._denied(path):
                return paramiko.SFTP_PERMISSION_DENIED
            self._path(path).mkdir(mode=attr.st_mode or 0o755)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def remove(self, path):
        try:
            if self._denied(path):
                return paramiko.SFTP_PERMISSION_DENIED
            self._path(path).unlink()
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def rename(self, oldpath, newpath):
        try:
            if self._path(newpath).exists():
                return paramiko.SFTP_FAILURE
            self._path(oldpath).rename(self._path(newpath))
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def posix_rename(self, oldpath, newpath):
        if not self.owner.posix_rename:
            return paramiko.SFTP_OP_UNSUPPORTED
        try:
            if self._denied(newpath):
                return paramiko.SFTP_PERMISSION_DENIED
            os.replace(self._path(oldpath), self._path(newpath))
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def chattr(self, path, attr):
        try:
            paramiko.SFTPServer.set_file_attr(str(self._path(path)), attr)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


class LocalSFTPServer:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.host_key = paramiko.RSAKey.generate(2048)
        self.user_key = paramiko.RSAKey.generate(2048)
        self.posix_rename = True
        self.denied: set[str] = set()
        self.delay = 0
        self.stopping = threading.Event()
        self.transports: list[paramiko.Transport] = []
        self.threads: list[threading.Thread] = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.accept_thread = threading.Thread(target=self._accept, daemon=True)
        self.accept_thread.start()

    def _accept(self):
        while not self.stopping.is_set():
            try:
                client, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(target=self._serve, args=(client,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _serve(self, client):
        transport = paramiko.Transport(client)
        self.transports.append(transport)
        transport.add_server_key(self.host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Filesystem, owner=self)
        try:
            transport.start_server(server=SSHServer(self))
            while transport.is_active() and not self.stopping.wait(0.05):
                pass
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def disconnect_clients(self):
        for transport in list(self.transports):
            transport.close()

    def close(self):
        self.stopping.set()
        self.listener.close()
        self.disconnect_clients()
        self.accept_thread.join(timeout=2)
        for thread in self.threads:
            thread.join(timeout=2)
