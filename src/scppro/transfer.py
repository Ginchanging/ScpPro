from __future__ import annotations

import errno
import os
import posixpath
import stat
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .models import (
    ConflictAction, ConflictDecision, Direction, EntryKind, FailedEntry,
    PlannedEntry, TransferJob, TransferProgress, TransferResult,
)
from .sftp import ConnectionLost, Session, TransferCancelled, entry_kind, error_text, valid_component


class UnsafeOverwrite(Exception):
    pass


class TransferEngine:
    CHUNK_SIZE = 256 * 1024

    def __init__(
        self, session: Session, job: TransferJob,
        progress: Callable[[TransferProgress], None],
        conflict: Callable[[PlannedEntry, int], ConflictDecision],
        cancellation: threading.Event | None = None,
    ):
        self.session = session
        self.job = job
        self.progress = progress
        self.conflict = conflict
        self.cancellation = cancellation or threading.Event()
        self.result = TransferResult(job.id)
        self.sftp = None
        self._channel_lock = threading.Lock()
        self._batch_choice: ConflictDecision | None = None
        self._total = 0
        self._completed = 0
        self._current = 0
        self._wire_bytes = 0
        self._total_files = 0
        self._started = time.monotonic()
        self._last_emit = 0.0
        self._current_name = ""
        self._local_root: Path | None = None
        self._remote_root = ""

    def cancel(self) -> None:
        self.cancellation.set()
        # Closing this worker's channel wakes a blocked read without disrupting browsing.
        with self._channel_lock:
            sftp = self.sftp
        if sftp:
            try:
                sftp.get_channel().close()
            except Exception:
                pass

    def _check_cancelled(self) -> None:
        if self.cancellation.is_set():
            raise TransferCancelled()

    def _emit(self, stage: str = "传输中", force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 0.08:
            return
        self._last_emit = now
        speed = self._wire_bytes / max(now - self._started, 0.001)
        self.progress(TransferProgress(
            self.job.id, stage, self._current_name,
            min(self._completed + self._current, self._total), self._total,
            speed, self.result.completed_files, self._total_files,
        ))

    def _on_bytes(self, current: int, _total: int) -> None:
        self._check_cancelled()
        first_chunk = self._current == 0
        self._wire_bytes += max(0, current - self._current)
        self._current = current
        self._emit(force=first_chunk)

    def _lstat_target(self, path: str):
        try:
            return self.sftp.lstat(path) if self.job.direction == Direction.UPLOAD else Path(path).lstat()
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise

    def _scan(self) -> list[PlannedEntry]:
        self._emit("正在扫描", force=True)
        if self.job.direction == Direction.UPLOAD:
            self._remote_root = self.sftp.normalize(self.job.destination)
            if entry_kind(self.sftp.lstat(self._remote_root).st_mode) != EntryKind.DIRECTORY:
                raise NotADirectoryError("远程目标必须是目录。")
        else:
            self._local_root = Path(self.job.destination).expanduser().resolve(strict=True)
            if not self._local_root.is_dir():
                raise NotADirectoryError("本机目标必须是目录。")
        if self.job.retry_entries:
            # Retrying retains the exact destinations and only repeats unfinished entries.
            entries = []
            for entry in self.job.retry_entries:
                self._check_cancelled()
                self._validate_destination(entry.destination)
                attributes = Path(entry.source).lstat() if self.job.direction == Direction.UPLOAD else self.sftp.lstat(entry.source)
                if entry_kind(attributes.st_mode) != entry.kind:
                    raise ValueError(f"重试源的文件类型已改变：{entry.source}")
                entries.append(replace(entry, size=(attributes.st_size or 0) if entry.kind == EntryKind.FILE else 0, modified=attributes.st_mtime or 0))
            return entries
        entries: list[PlannedEntry] = []
        seen: set[str] = set()
        sources = sorted(set(self.job.sources), key=lambda item: (len(item), item))
        for source in sources:
            self._check_cancelled()
            if self.job.direction == Direction.UPLOAD:
                path = Path(os.path.abspath(os.path.expanduser(source)))
                name = path.name
                if not valid_component(name):
                    raise ValueError("不能直接传输文件系统根目录。")
                target = posixpath.join(self._remote_root, name)
                self._scan_local(path, target, entries, seen)
            else:
                name = posixpath.basename(source.rstrip("/"))
                if not valid_component(name):
                    raise ValueError("不能直接传输远程文件系统根目录。")
                self._scan_remote(source, str(self._local_root / name), entries, seen)
        return entries

    def _scan_local(self, source: Path, target: str, entries: list[PlannedEntry], seen: set[str]) -> None:
        self._check_cancelled()
        if str(source) in seen:
            return
        seen.add(str(source))
        attributes = source.lstat()
        kind = entry_kind(attributes.st_mode)
        if kind not in (EntryKind.FILE, EntryKind.DIRECTORY):
            self.result.skipped.append(f"{source}（符号链接或特殊文件）")
            return
        entries.append(PlannedEntry(str(source), target, kind, attributes.st_size if kind == EntryKind.FILE else 0, attributes.st_mtime))
        if kind == EntryKind.DIRECTORY:
            for child in sorted(source.iterdir(), key=lambda item: item.name):
                self._scan_local(child, posixpath.join(target, child.name), entries, seen)

    def _scan_remote(self, source: str, target: str, entries: list[PlannedEntry], seen: set[str]) -> None:
        self._check_cancelled()
        if source in seen:
            return
        seen.add(source)
        attributes = self.sftp.lstat(source)
        kind = entry_kind(attributes.st_mode)
        if kind not in (EntryKind.FILE, EntryKind.DIRECTORY):
            self.result.skipped.append(f"{source}（符号链接或特殊文件）")
            return
        entries.append(PlannedEntry(source, target, kind, (attributes.st_size or 0) if kind == EntryKind.FILE else 0, attributes.st_mtime or 0))
        if kind == EntryKind.DIRECTORY:
            for child in sorted(self.sftp.listdir(source)):
                if not valid_component(child):
                    self.result.warnings.append(f"忽略无效文件名：{child!r}")
                    continue
                self._scan_remote(posixpath.join(source, child), str(Path(target) / child), entries, seen)

    def _validate_destination(self, target: str) -> None:
        if self.job.direction == Direction.UPLOAD:
            normalized = posixpath.normpath(target)
            if normalized == self._remote_root or not normalized.startswith(self._remote_root.rstrip("/") + "/"):
                raise ValueError("目标路径超出所选目录。")
        else:
            normalized = Path(os.path.abspath(target))
            if normalized == self._local_root or not normalized.is_relative_to(self._local_root):
                raise ValueError("目标路径超出所选目录。")

    def _make_directory(self, target: str) -> None:
        self._check_cancelled()
        self._validate_destination(target)
        attributes = self._lstat_target(target)
        if attributes is not None:
            if entry_kind(attributes.st_mode) != EntryKind.DIRECTORY:
                raise NotADirectoryError(f"同名目标不是普通目录：{target}")
            return
        try:
            if self.job.direction == Direction.UPLOAD:
                self.sftp.mkdir(target)
            else:
                Path(target).mkdir()
        except OSError:
            attributes = self._lstat_target(target)
            if attributes is None or entry_kind(attributes.st_mode) != EntryKind.DIRECTORY:
                raise

    def _ensure_parent(self, target: str) -> None:
        self._validate_destination(target)
        if self.job.direction == Direction.UPLOAD:
            parent = posixpath.dirname(target)
            relative = posixpath.relpath(parent, self._remote_root)
            current = self._remote_root
            if relative != ".":
                for component in relative.split("/"):
                    current = posixpath.join(current, component)
                    self._make_directory(current)
        else:
            parent = Path(target).parent
            current = self._local_root
            for component in parent.relative_to(self._local_root).parts:
                current = current / component
                self._make_directory(str(current))

    def _choose_destination(self, entry: PlannedEntry, target: str | None = None) -> tuple[str | None, object]:
        target = target or entry.destination
        self._validate_destination(target)
        attributes = self._lstat_target(target)
        if attributes is None:
            return target, None
        decision = self._batch_choice or self.conflict(entry, attributes.st_size or 0)
        self._check_cancelled()
        if decision.apply_to_batch and decision.action != ConflictAction.CANCEL:
            self._batch_choice = decision
        if decision.action == ConflictAction.CANCEL:
            raise TransferCancelled()
        if decision.action == ConflictAction.SKIP:
            return None, None
        if decision.action == ConflictAction.KEEP_BOTH:
            parent = posixpath.dirname(target) if self.job.direction == Direction.UPLOAD else str(Path(target).parent)
            name = posixpath.basename(target)
            stem, extension = os.path.splitext(name)
            number = 1
            while True:
                self._check_cancelled()
                candidate = posixpath.join(parent, f"{stem} ({number}){extension}")
                if self._lstat_target(candidate) is None:
                    return candidate, None
                number += 1
        if entry_kind(attributes.st_mode) != EntryKind.FILE:
            raise UnsafeOverwrite(f"不能覆盖目录、符号链接或特殊文件：{target}；请选择跳过或保留两份。")
        return target, attributes

    @staticmethod
    def _signature(attributes) -> tuple | None:
        if attributes is None:
            return None
        return (attributes.st_mode, attributes.st_size, attributes.st_mtime, getattr(attributes, "st_ino", None))

    def _commit(self, temporary: str, target: str, original, entry: PlannedEntry) -> bool:
        self._check_cancelled()
        self._ensure_parent(target)
        current = self._lstat_target(target)
        if self._signature(current) != self._signature(original):
            target, current = self._choose_destination(entry, target)
            if target is None:
                return False
        self._check_cancelled()
        if self.job.direction == Direction.UPLOAD:
            if current is None:
                self.sftp.rename(temporary, target)
            else:
                try:
                    self.sftp.posix_rename(temporary, target)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EPERM, errno.ENOENT):
                        raise
                    raise UnsafeOverwrite("无法安全覆盖远程文件，原文件已保留。服务器需支持 posix_rename；也可重试并选择保留两份。") from exc
        else:
            if current is None:
                # Atomic exclusive creation: a file created concurrently is never overwritten.
                os.link(temporary, target)
                Path(temporary).unlink()
            else:
                os.replace(temporary, target)
        return True

    def _cleanup(self, temporary: str) -> None:
        if self.job.direction == Direction.DOWNLOAD:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError as exc:
                self.result.warnings.append(f"未能清理临时文件 {temporary}：{error_text(exc)}")
            return
        try:
            self.sftp.remove(temporary)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return
            self._cleanup_using_new_channel(temporary)
        except Exception:
            self._cleanup_using_new_channel(temporary)

    def _cleanup_using_new_channel(self, temporary: str) -> None:
        try:
            with self.session.open_sftp() as cleanup:
                cleanup.get_channel().settimeout(5)
                cleanup.remove(temporary)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                self.result.warnings.append(f"未能清理远程临时文件：{temporary}")
        except Exception:
            self.result.warnings.append(f"连接中断，远程临时文件可能仍存在：{temporary}")

    def _copy_file(self, entry: PlannedEntry) -> bool:
        self._ensure_parent(entry.destination)
        target, original = self._choose_destination(entry)
        if target is None:
            return False
        temporary_name = f".scppro-{uuid.uuid4().hex}.part"
        if self.job.direction == Direction.UPLOAD:
            temporary = posixpath.join(posixpath.dirname(target), temporary_name)
        else:
            temporary = str(Path(target).parent / temporary_name)
        temporary_created = False
        try:
            self._check_cancelled()
            if self.job.direction == Direction.UPLOAD:
                descriptor = os.open(entry.source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("源文件不再是普通文件。")
                    if before.st_size != entry.size or before.st_mtime != entry.modified:
                        raise ValueError("源文件在扫描后已改变，请重新创建任务。")
                    with self.sftp.open(temporary, "wx") as destination:
                        temporary_created = True
                        destination.set_pipelined(True)
                        while chunk := source.read(self.CHUNK_SIZE):
                            self._check_cancelled()
                            destination.write(chunk)
                            self._on_bytes(self._current + len(chunk), entry.size)
                    after = os.fstat(source.fileno())
                    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or self._current != entry.size:
                        raise ValueError("源文件在传输期间发生变化，未替换目标文件。")
                if self.sftp.stat(temporary).st_size != entry.size:
                    raise IOError("上传大小校验失败，未替换目标文件。")
                try:
                    self.sftp.utime(temporary, (entry.modified, entry.modified))
                except OSError:
                    pass
            else:
                before = self.sftp.lstat(entry.source)
                if entry_kind(before.st_mode) != EntryKind.FILE or before.st_size != entry.size or (before.st_mtime or 0) != entry.modified:
                    raise ValueError("远程源文件在扫描后已改变，请重新创建任务。")
                descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                temporary_created = True
                with os.fdopen(descriptor, "wb") as destination:
                    # Paramiko's prefetch daemon cannot be cancelled reliably. Stream
                    # synchronously so channel closure stops reads without orphan threads.
                    written = self.sftp.getfo(entry.source, destination, callback=self._on_bytes, prefetch=False)
                    destination.flush()
                    os.fsync(destination.fileno())
                after = self.sftp.lstat(entry.source)
                if written != entry.size or before.st_size != after.st_size or before.st_mtime != after.st_mtime:
                    raise IOError("下载大小校验失败或远程文件已改变，未替换目标文件。")
                os.utime(temporary, (entry.modified, entry.modified))
            committed = self._commit(temporary, target, original, entry)
            if committed:
                temporary_created = False
            return committed
        finally:
            if temporary_created:
                self._cleanup(temporary)

    def _replace_channel(self) -> None:
        with self._channel_lock:
            old, self.sftp = self.sftp, None
        if old:
            try:
                old.close()
            except Exception:
                pass
        self._check_cancelled()
        fresh = self.session.open_sftp()
        with self._channel_lock:
            self.sftp = fresh

    def run(self) -> TransferResult:
        entries: list[PlannedEntry] = []
        try:
            if self.session.profile.connection_key != self.job.profile.connection_key:
                raise ConnectionLost("此任务绑定的服务器与当前连接不一致，请连接原服务器或重新创建任务。")
            self._replace_channel()
            entries = self._scan()
            self._total = sum(entry.size for entry in entries if entry.kind == EntryKind.FILE)
            self._total_files = sum(entry.kind == EntryKind.FILE for entry in entries)
            self._started = time.monotonic()
            self._emit(force=True)
            for index, entry in enumerate(entries):
                self._current = 0
                self._current_name = entry.source
                try:
                    self._check_cancelled()
                    self._ensure_parent(entry.destination)
                    if entry.kind == EntryKind.DIRECTORY:
                        self._make_directory(entry.destination)
                    elif self._copy_file(entry):
                        self.result.completed_files += 1
                        self._completed += entry.size
                    else:
                        self.result.skipped.append(entry.source)
                        self._total -= entry.size
                except Exception as exc:
                    if self.cancellation.is_set() or isinstance(exc, TransferCancelled):
                        self.result.cancelled = True
                        self.result.failed.extend(FailedEntry(item, "已取消，尚未完成") for item in entries[index:])
                        break
                    self.result.failed.append(FailedEntry(entry, error_text(exc)))
                    self._total -= entry.size
                    if not self.session.is_connected or isinstance(exc, ConnectionLost):
                        self.result.connection_lost = True
                        self.result.failed.extend(FailedEntry(item, "连接中断，尚未完成") for item in entries[index + 1:])
                        break
                    try:
                        self._replace_channel()
                    except Exception as channel_error:
                        if self.cancellation.is_set():
                            self.result.cancelled = True
                        else:
                            self.result.connection_lost = True
                            self.result.error = error_text(channel_error)
                        self.result.failed.extend(FailedEntry(item, "尚未完成") for item in entries[index + 1:])
                        break
                self._current = 0
                self._emit(force=True)
        except Exception as exc:
            if self.cancellation.is_set() or isinstance(exc, TransferCancelled):
                self.result.cancelled = True
            else:
                self.result.error = error_text(exc)
                self.result.connection_lost = not self.session.is_connected
        finally:
            self.result.transferred_bytes = self._wire_bytes
            self._current = 0
            self._emit(self.result.status, force=True)
            with self._channel_lock:
                sftp, self.sftp = self.sftp, None
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
        return self.result
