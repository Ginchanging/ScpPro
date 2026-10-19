from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from scppro.models import Direction, ServerProfile, TransferJob
from scppro.transfer import TransferEngine
from scppro.windows_paths import case_collision, invalid_windows_name


@pytest.mark.parametrize("name", ["a:b.txt", "a\\b", "bad?name", "CON", "nul.txt", "LPT1.log", "COM¹", "trail.", "trail ", "line\nfeed"])
def test_invalid_windows_names(name):
    assert invalid_windows_name(name)


@pytest.mark.parametrize("name", ["report.txt", "项目资料", ".hidden", "file (1).txt"])
def test_valid_windows_names(name):
    assert invalid_windows_name(name) is None


def test_case_collision_with_existing_file(tmp_path):
    (tmp_path / "Report.txt").write_text("existing", encoding="utf-8")
    assert case_collision(tmp_path / "report.txt")
    assert not case_collision(tmp_path / "Report.txt")


@pytest.mark.skipif(os.name != "nt", reason="Windows download behavior")
def test_download_scan_skips_incompatible_remote_names(tmp_path):
    job = TransferJob(ServerProfile("Test", "127.0.0.1", "tester"), Direction.DOWNLOAD, ("/",), str(tmp_path))
    engine = TransferEngine(SimpleNamespace(), job, lambda _progress: None, lambda *_args: None)
    engine.sftp = SimpleNamespace(lstat=lambda _path: SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_size=4, st_mtime=1))
    entries = []
    seen = set()
    engine._scan_remote("/readme.txt", str(tmp_path / "readme.txt"), entries, seen)
    engine._scan_remote("/README.txt", str(tmp_path / "README.txt"), entries, seen)
    engine._scan_remote("/bad:name", str(tmp_path / "bad:name"), entries, seen)
    assert [item.source for item in entries] == ["/readme.txt"]
    assert len(engine.result.skipped) == 2
    assert "大小写冲突" in engine.result.skipped[0]
    assert "不允许" in engine.result.skipped[1]
