"""RT_MENU 适配器测试：两种模板的 round-trip、提取、回写。"""

from __future__ import annotations

import ctypes
import struct
import sys

import pytest

from fixture_pe import MF_SEPARATOR, make_menuex, make_normal_menu
from zhres.adapters.menu import MenuAdapter, parse_menu, serialize_menu


class TestNormalTemplate:
    def test_roundtrip(self):
        menu = make_normal_menu([
            ("popup", 0, "&File", [
                ("item", 0, 100, "&New"),
                ("item", 0, 101, "&Open"),
                ("item", MF_SEPARATOR, 0, ""),
                ("item", 0, 102, "E&xit"),
            ]),
            ("popup", 0, "&Help", [("item", 0, 103, "&About DemoApp")]),
        ])
        assert serialize_menu(parse_menu(menu)) == menu

    def test_structure(self):
        menu = make_normal_menu([
            ("popup", 0, "&File", [("item", 0, 100, "&New")]),
        ])
        data = parse_menu(menu)
        assert data.version == 0
        (file_item,) = data.items
        assert file_item.is_popup
        assert file_item.text == "&File"
        assert file_item.children[0].text == "&New"
        assert file_item.children[0].item_id == 100
        assert file_item.option & 0x80  # 本层最后一项自动 MF_END

    def test_header_extra_preserved(self):
        # 头部带 offset 保留字节
        base = make_normal_menu([("item", 0, 1, "X")])
        with_extra = struct.pack("<HH", 0, 4) + b"\xde\xad\xbe\xef" + base[4:]
        data = parse_menu(with_extra)
        assert data.header_extra == b"\xde\xad\xbe\xef"
        assert serialize_menu(data) == with_extra

    def test_separator_detection(self):
        menu = make_normal_menu([
            ("item", MF_SEPARATOR, 0, ""),
            ("item", 0, 1, "X"),
        ])
        items = parse_menu(menu).items
        assert items[0].is_separator
        assert not items[1].is_separator

    def test_loadmenuindirect_accepts(self):
        """手工构造的字节必须被真实系统解析器接受（Windows 专属）。"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.LoadMenuIndirectW.restype = ctypes.c_void_p
        user32.LoadMenuIndirectW.argtypes = [ctypes.c_wchar_p]
        user32.GetMenuItemCount.restype = ctypes.c_int
        user32.GetMenuItemCount.argtypes = [ctypes.c_void_p]
        user32.DestroyMenu.restype = ctypes.c_int
        user32.DestroyMenu.argtypes = [ctypes.c_void_p]
        menu = make_normal_menu([
            ("popup", 0, "&File", [("item", 0, 100, "&New")]),
        ])
        buf = ctypes.create_unicode_buffer(len(menu) // 2 + 2)
        ctypes.memmove(buf, menu, len(menu))
        hmenu = user32.LoadMenuIndirectW(buf)
        assert hmenu, "LoadMenuIndirectW 拒绝了普通模板字节"
        assert user32.GetMenuItemCount(hmenu) == 1
        user32.DestroyMenu(hmenu)


class TestMenuEx:
    def test_roundtrip(self):
        menu = make_menuex([
            ("popup", 0, 0, 10, "&File", 77, [
                ("item", 0, 0, 100, "&New"),
                ("item", 0, 0, 101, "E&xit"),
            ]),
            ("popup", 0, 0, 11, "&Help", 0, [("item", 0, 0, 102, "&About")]),
        ])
        assert serialize_menu(parse_menu(menu)) == menu

    def test_structure(self):
        menu = make_menuex([
            ("popup", 0, 0, 10, "&File", 77, [("item", 0, 0, 100, "&New")]),
        ])
        data = parse_menu(menu)
        assert data.version == 1
        item = data.items[0]
        assert item.ex["flags"] & 0x01  # popup
        assert item.ex["helpid"] == 77
        assert item.children[0].ex["flags"] & 0x80  # 层末

    def test_truncated(self):
        with pytest.raises(Exception):
            parse_menu(b"\x01\x00\x00\x00\x00\x01")


class TestExtractApply:
    def setup_method(self):
        self.adapter = MenuAdapter()
        self.menu = make_normal_menu([
            ("popup", 0, "&File", [
                ("item", 0, 100, "&New"),
                ("item", MF_SEPARATOR, 0, ""),
                ("item", 0, 101, "E&xit"),
            ]),
            ("popup", 0, "&Help", [("item", 0, 102, "&About DemoApp")]),
        ])

    def test_extract_kinds_and_ids(self):
        entries = self.adapter.extract(4, 101, 1033, self.menu)
        # 先序：0=&File(popup 标题) 1=&New 2=(分隔符，无条目) 3=E&xit
        #       4=&Help 5=&About DemoApp
        by_pos = {e.id.rsplit("/", 1)[-1]: e for e in entries}
        assert by_pos["0"].context["kind"] == "menu_title"
        assert by_pos["0"].source == "&File"
        assert by_pos["1"].context["kind"] == "menu_item"
        assert by_pos["3"].source == "E&xit"
        assert "2" not in by_pos, "分隔符不应产生条目"
        assert by_pos["5"].source == "&About DemoApp"

    def test_no_change_returns_original(self):
        entries = self.adapter.extract(4, 101, 1033, self.menu)
        assert self.adapter.apply(entries, self.menu) is self.menu

    def test_apply_translation(self):
        entries = self.adapter.extract(4, 101, 1033, self.menu)
        for e in entries:
            if e.source == "&New":
                e.translation = "新建(&N)"
                e.status = "human"
        result = self.adapter.apply(entries, self.menu)
        # 重新解析：仅 &New 变化，其余逐字节保持
        again = parse_menu(result)
        texts = []

        def collect(items):
            for it in items:
                texts.append(it.text)
                collect(it.children)

        collect(again.items)
        assert "新建(&N)" in texts
        assert "&File" in texts and "E&xit" in texts
        # 结构不变（分隔符仍在）
        assert any(it.option & MF_SEPARATOR for it in again.items[0].children)

    def test_menuex_apply(self):
        menuex = make_menuex([
            ("popup", 0, 0, 10, "&File", 77, [("item", 0, 0, 100, "&New")]),
        ])
        entries = self.adapter.extract(4, 102, 1033, menuex)
        assert entries, "MENUEX 提取应有条目"
        for e in entries:
            if e.source == "&New":
                e.translation = "新建(&N)"
                e.status = "machine"
        result = self.adapter.apply(entries, menuex)
        texts = []

        def collect(items):
            for it in items:
                texts.append(it.text)
                collect(it.children)

        collect(parse_menu(result).items)
        assert "新建(&N)" in texts
        assert "&File" in texts

    def test_amp_single_char_kept_in_menu(self):
        # menu 中 "&X" 形式的快捷键定义保留
        menu = make_normal_menu([("item", 0, 1, "&X")])
        entries = self.adapter.extract(4, 1, 1033, menu)
        assert entries[0].status == "untranslated"
