"""RT_RCDATA Delphi DFM 适配器测试：round-trip（自建样本 + 真实程序提取样本）、提取、回写。"""

from __future__ import annotations

import glob
import os
import struct

from fixture_pe import DfmBuilder
from zhres.adapters.dfm import DfmAdapter, parse_dfm, serialize_dfm

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def make_complex_dfm() -> bytes:
    """覆盖全部主要值类型与结构的 DFM 样本。"""
    form = DfmBuilder("TForm1", "Form1")
    form.prop_int("Left", 191).prop_int("Top", -11)   # 负数走 int8/int16
    form.prop_int("ClientWidth", 50000)               # 需要 int32
    form.prop_string("Caption", "Hello")
    form.prop_wstring("Hint", "提示文字")
    form.prop_utf8("CustomHint", "UTF8 文本")
    form.prop_ident("BorderStyle", "bsDialog")
    form.prop_set("BorderIcons", ["biSystemMenu", "biMinimize"])
    form.prop_bool("Scaled", False).prop_bool("Visible", True)
    form.prop_binary("Icon.Data", b"\x00\x01\x02\x03\x04binary")
    form.prop_string_list("Lines.Strings", ["alpha", "beta", "中文行"])
    form.prop_string_list("Items.Strings", ["one", "two"])
    form.prop_collection("ActionBar", [
        [("Caption", 6, b"\x05Check"), ("Checked", 9, b"")],
        [("Caption", 18, struct.pack("<I", 4) + "提交".encode("utf-16-le"))],
    ])
    edit = DfmBuilder("TEdit", "Edit1")
    edit.prop_string("Text", "Type here").prop_int("TabOrder", 0)
    form.child(edit)
    panel = DfmBuilder("TPanel", "Panel1")
    panel.prop_string("Caption", "Panel").prop_int("TabOrder", 1)
    label = DfmBuilder("TLabel", "InnerLabel")
    label.prop_string("Caption", "Inner")
    panel.child(label)          # 二级嵌套
    form.child(panel)
    # 带前缀的子对象（inline：$F0|ffInline=$F4，带序号：$F2 + int）
    btn = DfmBuilder("TButton", "Button1")
    btn.prop_string("Caption", "OK")
    form.child(btn, prefix=b"\xf4")
    btn2 = DfmBuilder("TButton", "Button2")
    btn2.prop_string("Caption", "Cancel")
    form.child(btn2, prefix=b"\xf2" + bytes([4]) + b"\x07\x00\x00\x00")
    return bytes(form)


class TestRoundtrip:
    def test_selfbuilt_complex(self):
        data = make_complex_dfm()
        assert serialize_dfm(parse_dfm(data)) == data

    def test_real_world_dfms(self):
        """真实程序（Inno=Delphi 系 / Cheat Engine=Lazarus 系）提取的
        DFM 全量字节还原。样本位于 tests/fixtures/（随仓库分发）。"""
        files = sorted(glob.glob(os.path.join(FIXTURES, "real_*.bin")))
        dfms = [f for f in files if "menuex" not in os.path.basename(f)]
        assert dfms, "真实 DFM 样本缺失"
        for fn in dfms:
            data = open(fn, "rb").read()
            assert serialize_dfm(parse_dfm(data)) == data, fn

    def test_real_menuex_roundtrip(self):
        from zhres.adapters.menu import parse_menu, serialize_menu
        files = sorted(glob.glob(os.path.join(FIXTURES, "real_menuex_*.bin")))
        assert files, "真实 MENUEX 样本缺失"
        for fn in files:
            data = open(fn, "rb").read()
            assert serialize_menu(parse_menu(data)) == data, fn


class TestExtract:
    def setup_method(self):
        self.adapter = DfmAdapter()

    def test_extract_paths_and_lists(self):
        data = make_complex_dfm()
        entries = self.adapter.extract(10, "TForm1", 1033, data)
        ids = [e.id for e in entries]
        # 属性路径 id（任务书 §4.2）
        assert "RT_RCDATA/TForm1/1033/Form1/Caption" in ids
        assert "RT_RCDATA/TForm1/1033/Form1/Lines/0" in ids
        assert "RT_RCDATA/TForm1/1033/Form1/Lines/2" in ids
        assert "RT_RCDATA/TForm1/1033/Form1/Items/1" in ids
        assert "RT_RCDATA/TForm1/1033/Form1/Edit1/Text" in ids
        assert "RT_RCDATA/TForm1/1033/Form1/Panel1/InnerLabel/Caption" in ids
        assert "RT_RCDATA/TForm1/1033/Form1/Button2/Caption" in ids

    def test_ansi_gbk_decoding(self):
        data = make_complex_dfm()
        entries = self.adapter.extract(10, "TForm1", 1033, data)
        caption = next(e for e in entries if e.id.endswith("Form1/Caption"))
        assert caption.source == "Hello"
        hint = next(e for e in entries if e.id.endswith("Form1/Hint"))
        assert hint.source == "提示文字"

    def test_undecodable_ansi_skipped(self):
        # vaString 内容为无效 GBK 字节 → 该条 skipped（不进翻译队列）
        # 手工构造：TPF0 + 类名/对象名 + Caption(vaString, 非法 GBK) + 结束
        data = (b"TPF0" + b"\x02TF" + b"\x02F1"
                + b"\x07Caption" + bytes([6]) + b"\x03\x81\x40\x81"
                + b"\x00" + b"\x00")
        entries = self.adapter.extract(10, "TF", 1033, data)
        assert len(entries) == 1
        assert entries[0].id.endswith("F1/Caption")
        assert entries[0].status == "skipped"


class TestApply:
    def setup_method(self):
        self.adapter = DfmAdapter()

    def test_no_change_returns_original(self):
        data = make_complex_dfm()
        entries = self.adapter.extract(10, "TForm1", 1033, data)
        assert self.adapter.apply(entries, data) is data

    def test_apply_wstring_and_list(self):
        data = make_complex_dfm()
        entries = self.adapter.extract(10, "TForm1", 1033, data)
        for e in entries:
            if e.id.endswith("Form1/Caption"):
                e.translation = "你好"
                e.status = "human"
            if e.id.endswith("Form1/Lines/0"):
                e.translation = "甲"
                e.status = "machine"
            if e.id.endswith("Form1/Edit1/Text"):
                e.translation = "在此输入"
                e.status = "approved"
        result = self.adapter.apply(entries, data)
        again = self.adapter.extract(10, "TForm1", 1033, result)
        sources = {e.id: e.source for e in again}
        assert sources["RT_RCDATA/TForm1/1033/Form1/Caption"] == "你好"
        assert sources["RT_RCDATA/TForm1/1033/Form1/Lines/0"] == "甲"
        assert sources["RT_RCDATA/TForm1/1033/Form1/Edit1/Text"] == "在此输入"
        assert sources["RT_RCDATA/TForm1/1033/Form1/Lines/1"] == "beta"
        # 其他属性字节保持：Button1.Caption 仍为 OK
        assert sources["RT_RCDATA/TForm1/1033/Form1/Button1/Caption"] == "OK"

    def test_gbk_unencodable_translation_skipped(self):
        # vaString 译文含 GBK 无法编码的字符 → 不写入
        data = make_complex_dfm()
        entries = self.adapter.extract(10, "TForm1", 1033, data)
        for e in entries:
            if e.id.endswith("Form1/Caption"):
                e.translation = "𝄞"  # 音符，GBK 无
                e.status = "human"
        result = self.adapter.apply(entries, data)
        again = self.adapter.extract(10, "TForm1", 1033, result)
        caption = next(e for e in again if e.id.endswith("Form1/Caption"))
        assert caption.source == "Hello"  # 保持原文
