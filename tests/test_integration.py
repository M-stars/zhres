"""集成测试：合成 PE 全流程（离线）。

Phase 1 覆盖 extract 阶段；Phase 3 补全 import → writeback → verify → restore。
"""

from __future__ import annotations

import json
import sys

import pytest

from zhres.cli import main

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    """隔离用户主目录（法律提示标记等不污染真实 home）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    # Path.home() 在 Windows 上读 USERPROFILE
    return home


def test_extract_fixture_offline(tmp_path, fixture_pe_file, clean_home, capsys):
    out = tmp_path / "fixture.zhres-project.json"
    rc = main(["extract", str(fixture_pe_file), "-o", str(out)])
    assert rc == 0
    assert out.is_file()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format_version"] == 1
    assert data["target"]["machine"] == "x64"
    assert len(data["target"]["sha256"]) == 64

    entries = {e["id"]: e for e in data["entries"]}
    # block 1：16 条中非空且非跳过的英文条目
    assert "RT_STRING/1/1033/0" in entries          # File
    assert "RT_STRING/1/1033/1" in entries          # &New
    assert "RT_STRING/1/1033/13" in entries         # Hello, 世界!
    assert "RT_STRING/1/1033/14" in entries         # Curly “quotes” and emoji 🎉
    # 空槽位无条目
    assert "RT_STRING/1/1033/5" not in entries
    assert "RT_STRING/1/1033/12" not in entries
    # RCDATA 非 DFM 跳过（2 个：序数 900 + 字符串名 TEXTDATA）、RT_VERSION 跳过
    assert data["resources_seen"]["RT_RCDATA_skipped"] == 2
    assert data["resources_seen"]["RT_VERSION_skipped"] == 1
    assert "RT_STRING" in data["resources_seen"]
    # block2 的跳过规则样本：纯数字/空白/&占位/无字母，均记录 skip_reason
    skipped = [e for e in data["entries"] if e["status"] == "skipped"]
    assert skipped, "应有自动跳过条目并记录原因"
    assert all(e["skip_reason"] for e in skipped)
    assert entries["RT_STRING/2/1033/1"]["skip_reason"] == "纯数字"
    assert entries["RT_STRING/2/1033/4"]["skip_reason"] == "纯数字或标点"
    assert entries["RT_STRING/2/1033/3"]["skip_reason"] == "快捷键占位串"
    assert entries["RT_STRING/2/1033/10"]["status"] == "untranslated"  # OK 保留

    # 跨 extract 稳定：同一文件再提取一次，id 集合一致
    out2 = tmp_path / "again.json"
    rc2 = main(["extract", str(fixture_pe_file), "-o", str(out2)])
    assert rc2 == 0
    data2 = json.loads(out2.read_text(encoding="utf-8"))
    assert {e["id"] for e in data2["entries"]} == set(entries)


def test_extract_packed_rejected(tmp_path, monkeypatch):
    from fixture_pe import build_fixture_pe
    data = bytearray(build_fixture_pe())
    data[0x148:0x150] = b"UPX1\x00\x00\x00\x00"
    p = tmp_path / "packed.exe"
    p.write_bytes(bytes(data))
    rc = main(["extract", str(p), "-o", str(tmp_path / "x.json")])
    assert rc == 2
    assert not (tmp_path / "x.json").exists()

    # --force 继续
    rc2 = main(["extract", str(p), "-o", str(tmp_path / "y.json"), "--force"])
    assert rc2 == 0
    assert (tmp_path / "y.json").exists()


def test_extract_empty_resources(tmp_path, clean_home):
    # 只有 RCDATA 的 PE：资源存在但无可翻译条目 → 退出码 0
    from fixture_pe import build_pe
    p = tmp_path / "empty.exe"
    p.write_bytes(build_pe([(10, 1, 1033, b"no strings here")]))
    out = tmp_path / "empty.zhres-project.json"
    rc = main(["extract", str(p), "-o", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["entries"] == []
    assert data["resources_seen"]["RT_RCDATA_skipped"] == 1
