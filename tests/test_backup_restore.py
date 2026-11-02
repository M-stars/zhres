"""备份 / 还原测试（含 A7：备份损坏 → restore 拒绝）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fixture_pe import build_pe, make_string_block
from zhres.cli import main
from zhres.errors import WritebackError
from zhres.project import Project
from zhres.restore import restore
from zhres.writeback import make_backup

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


def _make_project(tmp_path, exe):
    rc = main(["extract", str(exe), "-o",
               str(tmp_path / "p.zhres-project.json")])
    assert rc == 0
    project = Project.load(tmp_path / "p.zhres-project.json")
    return project


def _translate_all(project):
    for e in project.entries:
        if e.status == "untranslated":
            e.translation = "译" + e.source
            e.status = "human"


class TestBackup:
    def test_make_backup_and_record(self, tmp_path):
        exe = tmp_path / "app.exe"
        exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
        project = _make_project(tmp_path, exe)
        backup, sha = make_backup(exe, project)
        assert backup.is_file()
        assert backup.name == "app.exe.zhres-orig.bak"
        assert sha == project.target["backup_sha256"]
        assert str(backup) == project.target["backup_path"]

    def test_second_backup_gets_timestamp(self, tmp_path):
        exe = tmp_path / "app.exe"
        exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
        project = _make_project(tmp_path, exe)
        make_backup(exe, project)
        backup2, _ = make_backup(exe, project)
        assert backup2.name != "app.exe.zhres-orig.bak"
        assert "zhres-orig-" in backup2.name

    def test_corrupt_backup_rejected(self, tmp_path):
        """A7：备份损坏（改 1 字节）→ restore 拒绝且不破坏现状。"""
        exe = tmp_path / "app.exe"
        original = build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))])
        exe.write_bytes(original)
        # 用默认工程路径（restore 依据 <exe>.zhres-project.json 核对备份哈希）
        project_path = tmp_path / "app.exe.zhres-project.json"
        assert main(["extract", str(exe), "-o", str(project_path)]) == 0
        project = Project.load(project_path)
        _translate_all(project)
        project.save(project_path)

        # 一次真实回写（writeback 内部创建唯一备份并记录其 SHA-256）
        assert main(["writeback", str(project_path), "--exe", str(exe)]) == 0
        modified = exe.read_bytes()
        assert modified != original

        # 篡改 writeback 记录的那个备份 1 字节
        project = Project.load(project_path)
        backup = Path(project.target["backup_path"])
        data = bytearray(backup.read_bytes())
        data[-1] ^= 0xFF
        backup.write_bytes(bytes(data))

        with pytest.raises(WritebackError, match="损坏"):
            restore(exe, backup, yes=True)
        # 当前文件未被破坏
        assert exe.read_bytes() == modified


class TestRestoreCLI:
    def test_restore_roundtrip(self, tmp_path):
        exe = tmp_path / "app.exe"
        original = build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))])
        exe.write_bytes(original)
        project_path = tmp_path / "p.zhres-project.json"
        assert main(["extract", str(exe), "-o", str(project_path)]) == 0
        project = Project.load(project_path)
        _translate_all(project)
        project.save(project_path)
        assert main(["writeback", str(project_path),
                     "--exe", str(exe)]) == 0
        assert exe.read_bytes() != original

        # 还原（--yes 跳过确认）
        rc = main(["restore", str(exe), "--yes"])
        assert rc == 0
        assert exe.read_bytes() == original

        # before-restore 副本存在
        before = list(tmp_path.glob("app.exe.zhres-before-restore-*.bak"))
        assert before, "还原前的当前文件应有副本"

    def test_restore_no_backup(self, tmp_path):
        exe = tmp_path / "none.exe"
        exe.write_bytes(original := build_pe(
            [(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
        assert main(["restore", str(exe), "--yes"]) == 2

    def test_restore_ambiguous_backups(self, tmp_path):
        exe = tmp_path / "app.exe"
        exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
        (tmp_path / "app.exe.zhres-orig-20260101-000000.bak").write_bytes(
            build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
        (tmp_path / "app.exe.zhres-orig-20260102-000000.bak").write_bytes(
            build_pe([(6, 1, 1033, make_string_block(["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
        assert main(["restore", str(exe), "--yes"]) == 2
