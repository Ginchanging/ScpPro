from dataclasses import replace
import hashlib
import threading
from pathlib import Path

import pytest

from scppro.models import ConflictAction, ConflictDecision, Direction, EntryKind, TransferJob
from scppro.transfer import TransferEngine


def run(session, sources, destination="/", direction=Direction.UPLOAD, action=ConflictAction.OVERWRITE, progress=None, cancel=None, retry=()):
    job = TransferJob(session.profile, direction, tuple(map(str, sources)), str(destination), retry_entries=retry)
    engine = TransferEngine(session, job, progress or (lambda _p: None), lambda *_args: ConflictDecision(action), cancel)
    return engine.run()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_directory_round_trip(session, server, tmp_path):
    source = tmp_path / "本机资料 空格"
    (source / "嵌套目录" / "空文件夹").mkdir(parents=True)
    (source / "中文 file.txt").write_text("你好\n文件传输 🚀", encoding="utf-8")
    (source / "嵌套目录" / "empty.bin").write_bytes(b"")
    (source / ".hidden").write_bytes(b"hidden")
    (source / "binary.dat").write_bytes(bytes(range(256)) * 4096)
    uploaded = run(session, [source])
    assert not uploaded.error and not uploaded.failed, uploaded
    assert uploaded.completed_files == 4
    assert uploaded.transferred_bytes == sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    destination = tmp_path / "downloads"
    destination.mkdir()
    downloaded = run(session, ["/" + source.name], destination, Direction.DOWNLOAD)
    assert not downloaded.error and not downloaded.failed, downloaded
    assert downloaded.completed_files == 4
    for path in source.rglob("*"):
        counterpart = destination / source.name / path.relative_to(source)
        assert counterpart.exists()
        if path.is_file():
            assert digest(path) == digest(counterpart)
    assert not list(server.root.rglob(".scppro-*.part"))
    assert not list(destination.rglob(".scppro-*.part"))


@pytest.mark.parametrize("direction", [Direction.UPLOAD, Direction.DOWNLOAD])
@pytest.mark.parametrize("action", [ConflictAction.SKIP, ConflictAction.KEEP_BOTH, ConflictAction.OVERWRITE])
def test_conflict_actions(session, server, tmp_path, direction, action):
    local = tmp_path / "same.txt"
    remote = server.root / "same.txt"
    local.write_bytes(b"local contents")
    remote.write_bytes(b"remote original")
    result = run(session, [local] if direction == Direction.UPLOAD else ["/same.txt"], "/" if direction == Direction.UPLOAD else tmp_path, direction, action)
    assert not result.error and not result.failed, result
    target = remote if direction == Direction.UPLOAD else local
    original = b"remote original" if direction == Direction.UPLOAD else b"local contents"
    incoming = b"local contents" if direction == Direction.UPLOAD else b"remote original"
    if action == ConflictAction.SKIP:
        assert target.read_bytes() == original
        assert result.skipped
    elif action == ConflictAction.KEEP_BOTH:
        assert target.read_bytes() == original
        assert target.with_name("same (1).txt").read_bytes() == incoming
    else:
        assert target.read_bytes() == incoming


def test_apply_conflict_to_batch(session, server, tmp_path):
    files = []
    for name in ("one.txt", "two.txt"):
        source = tmp_path / name
        source.write_bytes(b"new")
        (server.root / name).write_bytes(b"old")
        files.append(str(source))
    calls = []
    job = TransferJob(session.profile, Direction.UPLOAD, tuple(files), "/")
    engine = TransferEngine(session, job, lambda _p: None, lambda *args: (calls.append(args) or ConflictDecision(ConflictAction.SKIP, True)))
    result = engine.run()
    assert len(calls) == 1
    assert len(result.skipped) == 2


def test_unsupported_atomic_overwrite_preserves_original(session, server, tmp_path):
    server.posix_rename = False
    source = tmp_path / "existing.txt"
    source.write_bytes(b"new contents")
    target = server.root / source.name
    target.write_bytes(b"preserve me")
    result = run(session, [source])
    assert len(result.failed) == 1
    assert "安全覆盖" in result.failed[0].error
    assert target.read_bytes() == b"preserve me"
    assert not list(server.root.glob(".scppro-*.part"))
    retry = run(session, [source], action=ConflictAction.KEEP_BOTH, retry=tuple(failed.entry for failed in result.failed))
    assert retry.completed_files == 1
    assert (server.root / "existing (1).txt").read_bytes() == b"new contents"


@pytest.mark.parametrize("direction", [Direction.UPLOAD, Direction.DOWNLOAD])
def test_cancellation_preserves_original_and_cleans_temp(session, server, tmp_path, direction):
    source = tmp_path / "big.bin"
    remote = server.root / "big.bin"
    payload = b"abcdefgh" * (1024 * 1024)
    if direction == Direction.UPLOAD:
        source.write_bytes(payload)
        remote.write_bytes(b"original")
        sources, destination = [source], "/"
        target = remote
    else:
        source.write_bytes(b"original")
        remote.write_bytes(payload)
        sources, destination = ["/big.bin"], tmp_path
        target = source
    cancellation = threading.Event()
    def progress(update):
        if update.completed_bytes:
            cancellation.set()
    result = run(session, sources, destination, direction, progress=progress, cancel=cancellation)
    assert result.cancelled
    assert target.read_bytes() == b"original"
    assert not list(server.root.glob(".scppro-*.part"))
    assert not list(tmp_path.glob(".scppro-*.part"))
    assert result.failed


def test_permission_failure_and_retry_only_failed_files(session, server, tmp_path):
    source = tmp_path / "folder"
    source.mkdir()
    (source / "good.txt").write_bytes(b"okay")
    (source / "bad.txt").write_bytes(b"will retry")
    server.denied.add("/folder/bad.txt")
    result = run(session, [source])
    assert result.completed_files == 1 and len(result.failed) == 1, result
    assert (server.root / "folder/good.txt").read_bytes() == b"okay"
    server.denied.clear()
    server.denied.add("/folder/good.txt")
    retried = run(session, [source], retry=tuple(failed.entry for failed in result.failed))
    assert retried.completed_files == 1 and not retried.failed, retried
    assert (server.root / "folder/bad.txt").read_bytes() == b"will retry"


def test_symlinks_skipped_and_destination_links_not_followed(session, server, tmp_path):
    source = tmp_path / "folder"
    source.mkdir()
    (source / "real.txt").write_bytes(b"file")
    (source / "link.txt").symlink_to(source / "real.txt")
    result = run(session, [source])
    assert result.completed_files == 1 and len(result.skipped) == 1
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "dest"
    destination.mkdir()
    (destination / "folder").symlink_to(outside, target_is_directory=True)
    result = run(session, ["/folder"], destination, Direction.DOWNLOAD)
    assert result.failed
    assert not list(outside.iterdir())


def test_connection_loss_retains_failed_and_remaining_entries(session, server, tmp_path):
    source = tmp_path / "big.bin"
    source.write_bytes(b"01234567" * (1024 * 1024))
    another = tmp_path / "later.txt"
    another.write_bytes(b"later")
    disconnected = False
    def progress(update):
        nonlocal disconnected
        if update.completed_bytes and not disconnected:
            disconnected = True
            server.disconnect_clients()
    result = run(session, [source, another], progress=progress)
    assert result.connection_lost
    assert len(result.failed) == 2
    assert not (server.root / "big.bin").exists()
    assert not (server.root / "later.txt").exists()


def test_jobs_cannot_use_another_server(session, tmp_path):
    source = tmp_path / "file.txt"
    source.write_bytes(b"test")
    wrong = replace(session.profile, port=session.profile.port + 1)
    job = TransferJob(wrong, Direction.UPLOAD, (str(source),), "/")
    result = TransferEngine(session, job, lambda _p: None, lambda *_args: ConflictDecision(ConflictAction.OVERWRITE)).run()
    assert result.error and "不一致" in result.error


@pytest.mark.parametrize("direction", [Direction.UPLOAD, Direction.DOWNLOAD])
def test_cancel_closes_only_transfer_channel(session, server, tmp_path, direction):
    source = tmp_path / "cancel.bin"
    payload = b"12345678" * (1024 * 1024)
    source.write_bytes(payload if direction == Direction.UPLOAD else b"original")
    (server.root / source.name).write_bytes(b"original" if direction == Direction.UPLOAD else payload)
    server.delay = 0.002
    started = threading.Event()
    job = TransferJob(session.profile, direction, (str(source) if direction == Direction.UPLOAD else "/cancel.bin",), "/" if direction == Direction.UPLOAD else str(tmp_path))
    engine = TransferEngine(session, job, lambda update: started.set() if update.completed_bytes else None, lambda *_args: ConflictDecision(ConflictAction.OVERWRITE))
    results = []
    thread = threading.Thread(target=lambda: results.append(engine.run()))
    thread.start()
    assert started.wait(5)
    engine.cancel()
    thread.join(10)
    assert not thread.is_alive()
    assert results[0].cancelled
    assert (server.root / source.name if direction == Direction.UPLOAD else source).read_bytes() == b"original"
    assert not list(server.root.glob(".scppro-*.part"))
    assert not list(tmp_path.glob(".scppro-*.part"))
    assert session.is_connected
    assert session.list_directory("/")[0] == "/"


def test_retry_refreshes_source_size(session, server, tmp_path):
    source = tmp_path / "changed.txt"
    source.write_bytes(b"first")
    server.denied.add("/changed.txt")
    result = run(session, [source])
    assert result.failed
    source.write_bytes(b"modified before retry")
    server.denied.clear()
    retry = run(session, [source], retry=tuple(failed.entry for failed in result.failed))
    assert retry.completed_files == 1 and not retry.failed
    assert (server.root / source.name).read_bytes() == source.read_bytes()


def test_target_created_during_upload_requires_conflict_decision(session, server, tmp_path):
    source = tmp_path / "concurrent.txt"
    source.write_bytes(b"new payload" * 65536)
    target = server.root / source.name
    created = False
    decisions = []
    def progress(update):
        nonlocal created
        if update.completed_bytes and not created:
            created = True
            target.write_bytes(b"created by another process")
    job = TransferJob(session.profile, Direction.UPLOAD, (str(source),), "/")
    engine = TransferEngine(session, job, progress, lambda *args: (decisions.append(args) or ConflictDecision(ConflictAction.SKIP)))
    result = engine.run()
    assert result.skipped and not result.failed
    assert len(decisions) == 1
    assert target.read_bytes() == b"created by another process"
    assert not list(server.root.glob(".scppro-*.part"))
