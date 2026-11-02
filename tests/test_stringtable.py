"""RT_STRING 适配器单测：解析、序列化、round-trip、条目提取、回写。"""

from __future__ import annotations

import struct

import pytest

from fixture_pe import make_string_block
from zhres.adapters.stringtable import StringTableAdapter, parse_block, serialize_block
from zhres.errors import ParseError


class TestParseSerialize:
    def test_roundtrip_simple(self):
        block = make_string_block(["File", "&New", "", "Open"] + [""] * 12)
        assert serialize_block(parse_block(block)) == block

    def test_roundtrip_full_16(self):
        strings = [f"Item {i}" for i in range(16)]
        block = make_string_block(strings)
        assert parse_block(block) == strings
        assert serialize_block(parse_block(block)) == block

    def test_roundtrip_unicode_edge(self):
        strings = [
            "Hello, 世界!",            # CJK
            "Curly “quotes”",           # 弯引号
            "emoji 🎉",                 # 非 BMP 字符
            "Tab\there",                # 控制字符
            "\u00e9\u00e8",             # 拉丁扩展
            "a" * 5000,                 # 长串
        ] + [""] * 10
        block = make_string_block(strings)
        assert serialize_block(parse_block(block)) == block

    def test_all_empty(self):
        block = make_string_block([""] * 16)
        assert parse_block(block) == [""] * 16
        assert serialize_block(parse_block(block)) == block

    def test_parse_trailing_garbage(self):
        block = make_string_block(["A"] * 16) + b"\x01"
        with pytest.raises(ParseError):
            parse_block(block)

    def test_parse_truncated_entry(self):
        data = struct.pack("<H", 5) + "AB".encode("utf-16-le")  # 越界
        with pytest.raises(ParseError):
            parse_block(data)

    def test_serialize_too_long(self):
        with pytest.raises(ParseError):
            serialize_block(["x" * 70000])


class TestExtract:
    def setup_method(self):
        self.adapter = StringTableAdapter()

    def _extract(self, strings):
        block = make_string_block(strings)
        return self.adapter.extract(6, 1, 1033, block)

    def test_entry_ids_and_order(self):
        entries = self._extract(["File", "", "&New"] + [""] * 13)
        assert [e.id for e in entries] == ["RT_STRING/1/1033/0", "RT_STRING/1/1033/2"]
        assert entries[0].source == "File"
        assert entries[1].source == "&New"
        assert all(e.resource_type == "string" for e in entries)
        assert all(e.type_id == 6 for e in entries)
        assert all(e.language == 1033 for e in entries)

    def test_empty_slots_no_entry(self):
        entries = self._extract([""] * 16)
        assert entries == []

    def test_auto_skip_recorded(self):
        entries = self._extract(
            ["12345", "   ", "OK", "©2024", "Cancel"] + [""] * 11)
        by_id = {e.id.rsplit("/", 1)[-1]: e for e in entries}
        assert by_id["0"].status == "skipped"
        assert by_id["0"].skip_reason == "纯数字"
        assert by_id["1"].status == "skipped"
        assert by_id["3"].status == "skipped"   # ©2024 无字母无 CJK（© 不是字母）
        assert by_id["2"].status == "untranslated"
        assert by_id["4"].status == "untranslated"

    def test_unicode_status(self):
        entries = self._extract(["文件", "Save"] + [""] * 14)
        # 中文字符串本身也会被提取（状态 untranslated，由用户决定是否翻）
        assert entries[0].source == "文件"
        assert entries[0].status == "untranslated"


class TestApply:
    def setup_method(self):
        self.adapter = StringTableAdapter()

    def _block(self):
        return make_string_block(
            ["File", "Open", "Save", "Exit"] + [""] * 12)

    def _entries(self):
        return self.adapter.extract(6, 1, 1033, self._block())

    def test_no_change_returns_original(self):
        original = self._block()
        assert self.adapter.apply(self._entries(), original) is original

    def test_single_change(self):
        entries = self._entries()
        entries[0].translation = "文件"
        entries[0].status = "human"
        result = self.adapter.apply(entries, self._block())
        strings = parse_block(result)
        assert strings[0] == "文件"
        assert strings[1] == "Open"
        assert len(strings) == 16  # block 条目数保持

    def test_other_bytes_untouched(self):
        original = self._block()
        entries = self._entries()
        entries[2].translation = "保存"
        entries[2].status = "machine"
        result = self.adapter.apply(entries, original)
        # 未修改条目逐字节保持：除第 2 条外，前缀与后缀一致
        strings = parse_block(result)
        original_strings = parse_block(original)
        for i in (0, 1, 3):
            assert strings[i] == original_strings[i]
        assert strings[2] == "保存"

    def test_untranslated_not_written(self):
        entries = self._entries()
        entries[0].translation = None
        entries[0].status = "untranslated"
        original = self._block()
        assert self.adapter.apply(entries, original) is original

    def test_skipped_not_written(self):
        block = make_string_block(["123", "Open"] + [""] * 14)
        entries = self.adapter.extract(6, 1, 1033, block)
        assert entries[0].status == "skipped"
        entries[0].translation = "一二三"  # 即使误给了译文也不应写入
        result = self.adapter.apply(entries, block)
        assert parse_block(result)[0] == "123"

    def test_apply_empty_block(self):
        block = make_string_block([""] * 16)
        assert self.adapter.apply([], block) is block
