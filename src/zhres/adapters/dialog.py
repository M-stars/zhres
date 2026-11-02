"""adapters.dialog —— RT_DIALOG(5)：DLGTEMPLATE 与 DLGTEMPLATEEX 两种格式。

格式依据 MS 文档（DLGTEMPLATE / DLGTEMPLATEEX / DLGITEMTEMPLATE /
DLGITEMTEMPLATEEX）+ Wine `dlls/user32/dialog.c`（DIALOG_ParseTemplate32）
交叉核对，并经 windres 真实编译产物 round-trip 验证。详见
docs/formats/DIALOG.md。

关键实测结论：
- 标准 DLGTEMPLATE 的字体块**只有** WORD pointsize + typeface
  （MS 文档与 Wine 一致；weight/italic/charset 是 EX 才有）。
- EX 模板 pointsize == 0x7FFF 表示"使用消息框字体"，此时字体块仅含
  该 WORD，后续字段不存在（Wine 同语义）。
- 控件按 DWORD 对齐（相对资源起始）；menu/class/title/typeface 数组
  按 WORD 对齐。
- EX 控件 id 是 DWORD、extraCount 是 WORD；非 EX 控件 id 是 WORD。
"""

from __future__ import annotations

import struct

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, entry_position, make_entry

RT_DIALOG = 5
RT_MENU = 4

DS_SETFONT = 0x40

# 预定义控件类序数 → 名称
CLASS_ORDINALS = {
    0x0080: "Button", 0x0081: "Edit", 0x0082: "Static",
    0x0083: "ListBox", 0x0084: "ScrollBar", 0x0085: "ComboBox",
}


def _read_utf16z(data: bytes, offset: int, *, name, lang) -> tuple[str, int]:
    chars: list[str] = []
    while True:
        if offset + 2 > len(data):
            raise ParseError(
                "对话框字符串未找到 NUL 结尾", type_id=RT_DIALOG, name=name,
                lang=lang, offset=offset)
        (w,) = struct.unpack_from("<H", data, offset)
        offset += 2
        if w == 0:
            return "".join(chars), offset
        chars.append(chr(w))


class SzOrOrd:
    """sz_Or_Ord 数组：0=无 / 0xFFFF+序数 / 字符串。原始值保留。"""

    __slots__ = ("kind", "ordinal", "text")

    def __init__(self, kind: str, ordinal: int = 0, text: str = "") -> None:
        self.kind = kind          # "none" | "ordinal" | "string"
        self.ordinal = ordinal
        self.text = text

    @classmethod
    def parse(cls, data: bytes, offset: int, *, name, lang) -> tuple["SzOrOrd", int]:
        (w,) = struct.unpack_from("<H", data, offset)
        if w == 0:
            return cls("none"), offset + 2
        if w == 0xFFFF:
            (w2,) = struct.unpack_from("<H", data, offset + 2)
            return cls("ordinal", ordinal=w2), offset + 4
        text, offset = _read_utf16z(data, offset, name=name, lang=lang)
        return cls("string", text=text), offset

    def dump(self, out: bytearray) -> None:
        if self.kind == "none":
            out += struct.pack("<H", 0)
        elif self.kind == "ordinal":
            out += struct.pack("<HH", 0xFFFF, self.ordinal)
        else:
            out += self.text.encode("utf-16-le") + b"\x00\x00"


class DialogItem:
    """一个控件。所有原始字段保留；title 仅在非序数时提取。"""

    __slots__ = ("ex", "style", "ex_style", "x", "y", "cx", "cy",
                 "item_id", "help_id", "window_class", "title", "extra")

    def __init__(self, *, ex: bool, style: int, ex_style: int,
                 x: int, y: int, cx: int, cy: int, item_id: int,
                 help_id: int, window_class: SzOrOrd, title: SzOrOrd,
                 extra: bytes) -> None:
        self.ex = ex
        self.style = style
        self.ex_style = ex_style
        self.x, self.y, self.cx, self.cy = x, y, cx, cy
        self.item_id = item_id
        self.help_id = help_id          # 仅 EX
        self.window_class = window_class
        self.title = title
        self.extra = extra

    @property
    def class_name(self) -> str:
        """控件类名（序数转友好名，字符串原样，未知原样）。"""
        if self.window_class.kind == "ordinal":
            return CLASS_ORDINALS.get(self.window_class.ordinal,
                                      f"0x{self.window_class.ordinal:04X}")
        return self.window_class.text


class DialogData:
    """一个对话框资源的完整模型。"""

    def __init__(self, *, ex: bool, style: int, ex_style: int, cdit: int,
                 x: int, y: int, cx: int, cy: int, help_id: int,
                 menu: SzOrOrd, window_class: SzOrOrd, title: str,
                 font: dict | None, items: list[DialogItem]) -> None:
        self.ex = ex
        self.style = style
        self.ex_style = ex_style
        self.cdit = cdit
        self.x, self.y, self.cx, self.cy = x, y, cx, cy
        self.help_id = help_id          # 仅 EX
        self.menu = menu
        self.window_class = window_class
        self.title = title
        self.font = font                # {"pointsize","weight","italic","charset","typeface"} 或 None
        self.items = items

    @property
    def has_font(self) -> bool:
        return bool(self.style & DS_SETFONT) and self.font is not None


def parse_dialog(data: bytes, *, name=None, lang=None) -> DialogData:
    if len(data) < 18:
        raise ParseError("对话框资源过短", type_id=RT_DIALOG, name=name,
                         lang=lang)
    # EX 判定：dlgVer 后紧接 signature=0xFFFF
    (signature,) = struct.unpack_from("<H", data, 2)
    is_ex = signature == 0xFFFF
    pos = 0
    if is_ex:
        dlg_ver, sig, help_id, ex_style, style, cdit = \
            struct.unpack_from("<HHIIIH", data, pos)
        if dlg_ver != 1:
            raise ParseError(
                f"未知的对话框 EX 模板版本 {dlg_ver}", type_id=RT_DIALOG,
                name=name, lang=lang)
        pos = 18
    else:
        style, ex_style, cdit = struct.unpack_from("<IIH", data, pos)
        help_id = 0
        pos = 10
    x, y, cx, cy = struct.unpack_from("<hhhh", data, pos)
    pos += 8
    menu, pos = SzOrOrd.parse(data, pos, name=name, lang=lang)
    window_class, pos = SzOrOrd.parse(data, pos, name=name, lang=lang)
    title, pos = _read_utf16z(data, pos, name=name, lang=lang)

    font: dict | None = None
    if style & DS_SETFONT:
        if pos + 2 > len(data):
            raise ParseError("字体块越界", type_id=RT_DIALOG, name=name,
                             lang=lang, offset=pos)
        (pointsize,) = struct.unpack_from("<H", data, pos)
        pos += 2
        if is_ex and pointsize == 0x7FFF:
            # 消息框字体：字体块仅含该 WORD
            font = {"pointsize": pointsize, "message_font": True}
        else:
            if is_ex:
                if pos + 4 > len(data):
                    raise ParseError(
                        "字体块越界", type_id=RT_DIALOG, name=name, lang=lang,
                        offset=pos)
                weight, italic, charset = struct.unpack_from("<HBB", data, pos)
                pos += 4
            else:
                weight = italic = charset = 0
            typeface, pos = _read_utf16z(data, pos, name=name, lang=lang)
            font = {"pointsize": pointsize, "weight": weight,
                    "italic": italic, "charset": charset, "typeface": typeface}

    items: list[DialogItem] = []
    for index in range(cdit):
        pos = (pos + 3) & ~3  # 控件 DWORD 对齐（相对资源起始）
        if is_ex:
            if pos + 24 > len(data):
                raise ParseError(
                    f"控件 {index} 头越界", type_id=RT_DIALOG, name=name,
                    lang=lang, offset=pos)
            i_help, i_ex_style, i_style = struct.unpack_from("<III", data, pos)
            x_, y_, cx_, cy_, i_id = struct.unpack_from("<hhhhI", data, pos + 12)
            pos += 24
        else:
            if pos + 18 > len(data):
                raise ParseError(
                    f"控件 {index} 头越界", type_id=RT_DIALOG, name=name,
                    lang=lang, offset=pos)
            i_style, i_ex_style = struct.unpack_from("<II", data, pos)
            x_, y_, cx_, cy_, i_id = struct.unpack_from("<hhhhH", data, pos + 8)
            pos += 18
            i_help = 0
        wclass, pos = SzOrOrd.parse(data, pos, name=name, lang=lang)
        ititle, pos = SzOrOrd.parse(data, pos, name=name, lang=lang)
        if pos + 2 > len(data):
            raise ParseError(
                f"控件 {index} extraCount 越界", type_id=RT_DIALOG,
                name=name, lang=lang, offset=pos)
        (extra_count,) = struct.unpack_from("<H", data, pos)
        pos += 2
        if pos + extra_count > len(data):
            raise ParseError(
                f"控件 {index} 创建数据越界", type_id=RT_DIALOG, name=name,
                lang=lang, offset=pos)
        extra = bytes(data[pos:pos + extra_count])
        pos += extra_count
        items.append(DialogItem(
            ex=is_ex, style=i_style, ex_style=i_ex_style, x=x_, y=y_,
            cx=cx_, cy=cy_, item_id=i_id, help_id=i_help, window_class=wclass,
            title=ititle, extra=extra))
    return DialogData(ex=is_ex, style=style, ex_style=ex_style, cdit=cdit,
                      x=x, y=y, cx=cx, cy=cy, help_id=help_id, menu=menu,
                      window_class=window_class, title=title, font=font,
                      items=items)


def serialize_dialog(dlg: DialogData) -> bytes:
    out = bytearray()
    if dlg.ex:
        out += struct.pack("<HHIIIH", 1, 0xFFFF, dlg.help_id, dlg.ex_style,
                           dlg.style, dlg.cdit)
    else:
        out += struct.pack("<IIH", dlg.style, dlg.ex_style, dlg.cdit)
    out += struct.pack("<hhhh", dlg.x, dlg.y, dlg.cx, dlg.cy)
    dlg.menu.dump(out)
    dlg.window_class.dump(out)
    out += dlg.title.encode("utf-16-le") + b"\x00\x00"
    if dlg.style & DS_SETFONT and dlg.font is not None:
        out += struct.pack("<H", dlg.font["pointsize"])
        if not dlg.font.get("message_font"):
            if dlg.ex:
                out += struct.pack("<HBB", dlg.font["weight"],
                                   dlg.font["italic"], dlg.font["charset"])
            out += dlg.font["typeface"].encode("utf-16-le") + b"\x00\x00"
    for item in dlg.items:
        while len(out) % 4:
            out += b"\x00"
        if dlg.ex:
            out += struct.pack("<III", item.help_id, item.ex_style, item.style)
            out += struct.pack("<hhhhI", item.x, item.y, item.cx, item.cy,
                               item.item_id)
        else:
            out += struct.pack("<II", item.style, item.ex_style)
            out += struct.pack("<hhhhH", item.x, item.y, item.cx, item.cy,
                               item.item_id)
        item.window_class.dump(out)
        item.title.dump(out)
        out += struct.pack("<H", len(item.extra))
        out += item.extra
    return bytes(out)


class DialogAdapter:
    """RT_DIALOG 适配器（两种格式统一处理）。"""

    resource_types = (5,)

    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool:
        # 适配器按类型 5 注册；EX 由 signature=0xFFFF 识别，其余按标准
        # 模板尝试解析（失败按铁律 d 原样保留并警告）。
        return type_id == 5 and len(data) >= 18

    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]:
        dlg = parse_dialog(data, name=name, lang=lang)
        entries: list[Entry] = []
        # 对话框标题
        if dlg.title:
            reason = auto_skip(dlg.title)
            if reason is None:
                entries.append(make_entry(
                    f"RT_DIALOG/{name}/{lang}/title", "dialog", type_id,
                    name, lang,
                    {"kind": "dialog_caption", "location": str(name)}, dlg.title,
                    None))
        # 控件文本
        for index, item in enumerate(dlg.items):
            if item.title.kind != "string" or item.title.text == "":
                continue  # 序数资源（图标等）或空文本
            reason = auto_skip(item.title.text)
            context = {
                "kind": "dialog_control_text",
                "location": f"{name} / id {item.item_id}",
                "dialog_title": dlg.title,
                "control_class": item.class_name,
            }
            entries.append(make_entry(
                f"RT_DIALOG/{name}/{lang}/{index}", "dialog", type_id,
                name, lang, context, item.title.text, reason))
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        dlg = parse_dialog(original)
        changed = False
        by_key: dict[object, Entry] = {}
        for e in entries:
            if e.status not in WRITABLE_STATUSES or e.translation is None:
                continue
            by_key[entry_position(e.id)] = e
        title_entry = by_key.get("title")
        if title_entry is not None and dlg.title != title_entry.translation:
            dlg.title = title_entry.translation
            changed = True
        for index, item in enumerate(dlg.items):
            entry = by_key.get(str(index))
            if entry is not None and item.title.kind == "string" and \
                    item.title.text != entry.translation:
                item.title.text = entry.translation
                changed = True
        if not changed:
            return original
        return serialize_dialog(dlg)


def patch_dialog_font(dlg: DialogData, typeface: str, charset: int) -> bool:
    """把对话框字体替换为中文可用字体（writeback 默认开启）。

    仅对已有字体块的对话框生效；返回是否发生修改。
    """
    if not dlg.has_font or dlg.font is None or dlg.font.get("message_font"):
        return False
    changed = False
    if dlg.font.get("typeface") != typeface:
        dlg.font["typeface"] = typeface
        changed = True
    if dlg.ex and dlg.font.get("charset") != charset:
        dlg.font["charset"] = charset
        changed = True
    return changed
