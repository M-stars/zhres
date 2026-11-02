"""翻译记忆与术语表测试。"""

from __future__ import annotations

import json

import pytest

from zhres.errors import ZhresError
from zhres.glossary import Glossary
from zhres.tm import TranslationMemory, normalize


class TestNormalize:
    def test_whitespace_and_quotes(self):
        assert normalize("  Hello   World  ") == "Hello World"
        assert normalize("\u201cCurly\u201d \u2018Quotes\u2019") == '"Curly" \'Quotes\''
        assert normalize("tab\there") == "tab here"


class TestTM:
    def test_exact_and_normalized_hit(self, tmp_path):
        with TranslationMemory(tmp_path / "tm.db") as tm:
            tm.upsert('File not found.', '找不到文件。', "human")
            # 精确
            assert tm.lookup("File not found.", "") == ('找不到文件。', "human")
            # 归一化命中（空白差异）
            assert tm.lookup("  File  not  found. ", "") == ('找不到文件。', "human")
            # 弯引号 → 直引号
            tm.upsert('The "File" menu', '“文件”菜单', "human")
            assert tm.lookup("The \u201cFile\u201d menu", "") == ('“文件”菜单', "human")

    def test_context_priority(self, tmp_path):
        with TranslationMemory(tmp_path / "tm.db") as tm:
            tm.upsert("Open", "打开", "human", context_kind="menu_item")
            tm.upsert("Open", "开启", "machine", context_kind="dialog_caption")
            # 同语境精确优先
            assert tm.lookup("Open", "menu_item") == ("打开", "human")
            assert tm.lookup("Open", "dialog_caption") == ("开启", "machine")
            # 未知语境 → 回退 source_norm（human 优先）
            assert tm.lookup("Open", "other") == ("打开", "human")

    def test_human_overwrites_machine(self, tmp_path):
        with TranslationMemory(tmp_path / "tm.db") as tm:
            tm.upsert("Save", "存储", "machine")
            tm.upsert("Save", "保存", "human")
            assert tm.lookup("Save", "") == ("保存", "human")
            # machine 不能覆盖 human
            tm.upsert("Save", "储存", "machine")
            assert tm.lookup("Save", "") == ("保存", "human")

    def test_hits_increment(self, tmp_path):
        with TranslationMemory(tmp_path / "tm.db") as tm:
            tm.upsert("Exit", "退出", "human")
            tm.lookup("Exit", "")
            tm.lookup("Exit", "")
            hits = tm._conn.execute(
                "SELECT hits FROM translations WHERE source_norm='Exit'"
            ).fetchone()[0]
            assert hits == 2

    def test_persistence(self, tmp_path):
        with TranslationMemory(tmp_path / "tm.db") as tm:
            tm.upsert("Ready", "就绪", "human")
        with TranslationMemory(tmp_path / "tm.db") as tm:
            assert tm.lookup("Ready", "") == ("就绪", "human")
            assert tm.count() == 1


class TestGlossary:
    def test_load_toml(self, tmp_path):
        p = tmp_path / "glossary.toml"
        p.write_text(
            '[terms]\n"File" = "文件"\n"Exit" = "退出"\n'
            '[protect]\npatterns = ["%s", "\\\\n"]\n', encoding="utf-8")
        g = Glossary.load(p)
        assert g.terms["File"] == "文件"
        assert "%s" in g.protect_patterns

    def test_load_json(self, tmp_path):
        p = tmp_path / "glossary.json"
        p.write_text(json.dumps(
            {"terms": {"File": "文件"}, "protect": {"patterns": ["%d"]}},
            ensure_ascii=False), encoding="utf-8")
        g = Glossary.load(p)
        assert g.lookup_term("file") == "文件"  # 忽略大小写

    def test_lookup_term_exact(self):
        g = Glossary(terms={"File": "文件"})
        assert g.lookup_term("File") == "文件"
        assert g.lookup_term("file") == "文件"
        assert g.lookup_term("File not found") is None  # 仅精确键

    def test_missing_terms(self):
        g = Glossary(terms={"File": "文件"})
        assert g.missing_terms("Open File", "打开档案") == ["File→文件"]
        assert g.missing_terms("Open File", "打开文件") == []

    def test_protect_restore(self):
        g = Glossary(terms={"File": "文件"},
                     protect_patterns=["%s", "\\n"])
        protected, store = g.protect("File %s:\\n%s")
        assert "%s" not in protected and "\\n" not in protected
        assert "File" not in protected
        restored, lost = g.restore(protected, store)
        assert restored == "File %s:\\n%s"
        assert lost == []

    def test_restore_lost_token(self):
        g = Glossary(protect_patterns=["%d"])
        protected, store = g.protect("count %d")
        # 模拟译文把保护记号弄丢
        lost_filled = protected.replace("⟦0⟧", "XX")
        _, lost = g.restore(lost_filled, store)
        assert lost, "被破坏的记号应报 lost"

    def test_missing_file(self, tmp_path):
        with pytest.raises(ZhresError):
            Glossary.load(tmp_path / "nope.toml")
