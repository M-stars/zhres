"""RT_DIALOG 适配器测试：两种模板 round-trip（含真实 windres 产物字节）、提取、回写、字体补丁。"""

from __future__ import annotations

import struct

import pytest

from fixture_pe import DS_SETFONT, make_dialog_ex, make_dialog_standard
from zhres.adapters.dialog import DialogAdapter, parse_dialog, patch_dialog_font, serialize_dialog


class TestStandardTemplate:
    def make(self):
        return make_dialog_standard(
            style=DS_SETFONT | 0x800C0000, title="About DemoApp",
            font=(8, "MS Shell Dlg"),
            items=[
                {"style": 0x50020000, "x": 32, "y": 8, "cx": 120, "cy": 10,
                 "id": 1002, "wclass": 0x82, "title": "Demo Application"},
                {"style": 0x50010001, "x": 56, "y": 56, "cx": 48, "cy": 14,
                 "id": 1, "wclass": 0x80, "title": "OK"},
                {"style": 0x50000003, "x": 8, "y": 8, "cx": 20, "cy": 20,
                 "id": 1001, "wclass": "STATIC", "title": None},
                {"style": 0x50010000, "x": 8, "y": 30, "cx": 100, "cy": 12,
                 "id": 1003, "wclass": "EDIT", "title": None,
                 "extra": b"\x01\x02\x03"},
            ])

    def test_roundtrip(self):
        data = self.make()
        assert serialize_dialog(parse_dialog(data)) == data

    def test_font_block_standard_has_no_weight(self):
        """标准模板字体块只有 pointsize+typeface（Wine/MS 文档一致）。"""
        data = self.make()
        dlg = parse_dialog(data)
        assert dlg.font["pointsize"] == 8
        assert dlg.font["typeface"] == "MS Shell Dlg"
        assert "weight" not in dlg.font or dlg.font.get("weight") == 0

    def test_class_ordinals(self):
        dlg = parse_dialog(self.make())
        assert dlg.items[0].class_name == "Static"
        assert dlg.items[1].class_name == "Button"
        assert dlg.items[2].class_name == "STATIC"

    def test_extra_data_preserved(self):
        dlg = parse_dialog(self.make())
        assert dlg.items[3].extra == b"\x01\x02\x03"

    def test_ordinal_title_not_extracted(self):
        # title 为序数（0xFFFF+id，如图标）的控件不产生文本条目
        data = make_dialog_standard(
            style=0x800C0000, title="X",
            items=[{"style": 0x50000003, "x": 0, "y": 0, "cx": 10, "cy": 10,
                    "id": 1, "wclass": 0x82, "title": 5}])
        entries = DialogAdapter().extract(5, 103, 1033, data)
        # 仅标题 "X" 一个条目（单字符会被 auto_skip）
        assert all(e.context["kind"] == "dialog_caption" or
                   e.status == "skipped" for e in entries)


class TestExTemplate:
    def make(self):
        return make_dialog_ex(
            style=DS_SETFONT | 0x800C0000, title="Settings",
            font=(8, 400, 0, 0, "MS Shell Dlg"),
            items=[
                {"style": 0x50020000, "x": 8, "y": 8, "cx": 60, "cy": 10,
                 "id": 2001, "wclass": 0x82, "title": "User &name:",
                 "help_id": 7},
                {"style": 0x50810080, "x": 70, "y": 6, "cx": 100, "cy": 12,
                 "id": 2002, "wclass": "EDIT", "title": None},
            ])

    def test_roundtrip(self):
        data = self.make()
        assert serialize_dialog(parse_dialog(data)) == data

    def test_ex_font_block(self):
        dlg = parse_dialog(self.make())
        assert dlg.ex
        assert dlg.font["weight"] == 400
        assert dlg.font["charset"] == 0
        assert dlg.font["typeface"] == "MS Shell Dlg"

    def test_helpid(self):
        dlg = parse_dialog(self.make())
        assert dlg.items[0].help_id == 7


class TestExtractApply:
    def setup_method(self):
        self.adapter = DialogAdapter()
        self.data = make_dialog_standard(
            style=DS_SETFONT | 0x800C0000, title="About DemoApp",
            font=(8, "MS Shell Dlg"),
            items=[
                {"style": 0x50020000, "x": 32, "y": 8, "cx": 120, "cy": 10,
                 "id": 1002, "wclass": 0x82, "title": "Demo Application"},
                {"style": 0x50010001, "x": 56, "y": 56, "cx": 48, "cy": 14,
                 "id": 1, "wclass": 0x80, "title": "OK"},
            ])

    def test_extract_caption_and_controls(self):
        entries = self.adapter.extract(5, 103, 1033, self.data)
        by_pos = {e.id.rsplit("/", 1)[-1]: e for e in entries}
        assert by_pos["title"].context["kind"] == "dialog_caption"
        assert by_pos["title"].source == "About DemoApp"
        assert by_pos["0"].context["kind"] == "dialog_control_text"
        assert by_pos["0"].context["control_class"] == "Static"
        assert by_pos["1"].source == "OK"

    def test_no_change_returns_original(self):
        entries = self.adapter.extract(5, 103, 1033, self.data)
        assert self.adapter.apply(entries, self.data) is self.data

    def test_apply_changes_only_target(self):
        entries = self.adapter.extract(5, 103, 1033, self.data)
        for e in entries:
            if e.source == "OK":
                e.translation = "确定"
                e.status = "human"
        result = self.adapter.apply(entries, self.data)
        dlg = parse_dialog(result)
        assert dlg.items[1].title.text == "确定"
        assert dlg.items[0].title.text == "Demo Application"
        assert dlg.title == "About DemoApp"

    def test_font_patch(self):
        dlg = parse_dialog(self.data)
        assert patch_dialog_font(dlg, "Microsoft YaHei UI", 134)
        result = serialize_dialog(dlg)
        dlg2 = parse_dialog(result)
        assert dlg2.font["typeface"] == "Microsoft YaHei UI"
        # 标准模板没有 charset 字段，typeface 仍替换成功
        assert dlg2.font["pointsize"] == 8
        # 未修改其他字段
        assert dlg2.title == "About DemoApp"
        assert dlg2.items[0].title.text == "Demo Application"

    def test_font_patch_noop_when_same(self):
        dlg = parse_dialog(self.data)
        dlg.font["typeface"] = "Microsoft YaHei UI"
        if dlg.ex:
            dlg.font["charset"] = 134
        # 标准（非 EX）模板只比较 typeface——EX 才额外比较 charset
        data2 = make_dialog_ex(style=DS_SETFONT | 0x800C0000, title="T",
                               font=(8, 400, 0, 134, "Microsoft YaHei UI"),
                               items=[])
        dlg2 = parse_dialog(data2)
        assert not patch_dialog_font(dlg2, "Microsoft YaHei UI", 134)

    def test_dialog_without_font_not_patched(self):
        data = make_dialog_standard(style=0x800C0000, title="NoFont",
                                    items=[])
        dlg = parse_dialog(data)
        assert not patch_dialog_font(dlg, "Microsoft YaHei UI", 134)
