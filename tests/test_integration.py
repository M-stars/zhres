"""集成测试：合成 PE 全流程（离线）。

A4 验收项：extract → import CSV → writeback → verify → restore，
SHA-256 循环校验。
"""

from __future__ import annotations

import json
import sys

import pytest

from zhres.cli import main

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


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


# ---------------------------------------------------------------------------
# A4：离线全流程（extract → export → import → writeback → verify → restore）
# ---------------------------------------------------------------------------

def _csv_content(project_path):
    project = json.loads(project_path.read_text(encoding="utf-8"))
    rows = ["id,source,translation,status,notes"]
    for e in project["entries"]:
        if e["status"] == "untranslated" and e["source"].isascii():
            # 简单人工译文：加"译"前缀（ASCII 源文）
            rows.append('"%s","%s","%s","untranslated",""' % (
                e["id"], e["source"].replace('"', '""'),
                "[中]" + e["source"].replace('"', '""')))
    return "\r\n".join(rows) + "\r\n"


def test_full_offline_pipeline(tmp_path, fixture_pe_file, clean_home):
    exe = fixture_pe_file
    project_path = tmp_path / "p.zhres-project.json"
    original_sha = exe.read_bytes()

    # 1. extract
    assert main(["extract", str(exe), "-o", str(project_path)]) == 0

    # 2. export → 人工填 CSV → import（离线主路径）
    csv_path = tmp_path / "translations.csv"
    assert main(["export", str(project_path), "--csv", str(csv_path)]) == 0
    csv_path.write_text(_csv_content(project_path), encoding="utf-8-sig")
    rc = main(["import", str(project_path), str(csv_path),
               "--tm", str(tmp_path / "tm.db")])
    assert rc == 0

    project = json.loads(project_path.read_text(encoding="utf-8"))
    human = [e for e in project["entries"] if e["status"] == "human"]
    assert human, "CSV 导入应产生 human 条目"

    # 3. writeback（默认备份 + 字体补丁 + 自动 verify）
    assert main(["writeback", str(project_path)]) == 0

    # 回写后 exe 已变化
    modified = exe.read_bytes()
    assert modified != original_sha

    # 4. verify 显式复核
    assert main(["verify", str(project_path)]) == 0

    # 工程记录了备份与字体补丁
    project = json.loads(project_path.read_text(encoding="utf-8"))
    assert project["target"]["backup_path"]
    assert project["target"]["backup_sha256"]

    # 5. restore：SHA-256 循环校验（回到原始字节）
    assert main(["restore", str(exe), "--yes"]) == 0
    assert exe.read_bytes() == original_sha


def test_writeback_failure_keeps_target_intact(tmp_path, make_pe_file,
                                               clean_home):
    """A6：构造坏资源使某三元组失败 → 目标文件保持原样。

    构造方式：工程中引用一个目标文件里不存在的资源三元组
    （writeback 的资源存在性检查先于 UpdateResource，属快速失败路径）。
    UpdateResource 层失败路径由 ResourceUpdater.discard 保证。
    """
    exe = make_pe_file([(6, 1, 1033, __import__("fixture_pe")
                         .make_string_block(["Open", "Save", "Close"] + [""] * 13))])
    project_path = tmp_path / "p.zhres-project.json"
    assert main(["extract", str(exe), "-o", str(project_path)]) == 0
    before = exe.read_bytes()

    data = json.loads(project_path.read_text(encoding="utf-8"))
    # 篡改工程：加一条指向不存在资源的已翻译条目
    data["entries"].append({
        "id": "RT_STRING/999/1033/0", "resource_type": "string",
        "type_id": 6, "resource_name": 999, "language": 1033,
        "context": {"kind": "string_table"}, "source": "X",
        "translation": "Y", "status": "human", "skip_reason": "",
        "max_len": None, "notes": ""})
    project_path.write_text(json.dumps(data, ensure_ascii=False),
                            encoding="utf-8")

    rc = main(["writeback", str(project_path)])
    assert rc == 2
    assert exe.read_bytes() == before, "失败路径目标文件必须保持原样"


def test_translate_with_tm_and_glossary(tmp_path, fixture_pe_file, clean_home):
    project_path = tmp_path / "p.zhres-project.json"
    tm_path = tmp_path / "tm.db"
    assert main(["extract", str(fixture_pe_file), "-o", str(project_path)]) == 0

    # 预填 TM：把 "File" → "文件"（TM 路径）
    from zhres.tm import TranslationMemory
    with TranslationMemory(tm_path) as tm:
        tm.upsert("File", "文件", "human")
        tm.upsert("Exit", "退出", "human")

    # 术语表：Open → 打开（术语强制映射路径）
    gl = tmp_path / "glossary.toml"
    gl.write_text('[terms]\n"Select All" = "全选"\n', encoding="utf-8")

    assert main(["translate", str(project_path), "--tm", str(tm_path),
                 "--glossary", str(gl)]) == 0

    project = json.loads(project_path.read_text(encoding="utf-8"))
    by_source = {e["source"]: e for e in project["entries"]}
    assert by_source["File"]["translation"] == "文件"
    assert by_source["File"]["status"] == "machine"
    assert by_source["Exit"]["translation"] == "退出"
    assert by_source["Select All"]["translation"] == "全选"
    # 无命中条目保持 untranslated
    assert by_source["&New"]["status"] == "untranslated"


def test_translate_dry_run_no_write(tmp_path, fixture_pe_file, clean_home, capsys):
    project_path = tmp_path / "p.zhres-project.json"
    assert main(["extract", str(fixture_pe_file), "-o", str(project_path)]) == 0
    before = project_path.read_bytes()
    rc = main(["translate", str(project_path), "--dry-run"])
    assert rc == 0
    assert project_path.read_bytes() == before, "dry-run 不得写工程文件"
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "翻译记忆可命中" in out
