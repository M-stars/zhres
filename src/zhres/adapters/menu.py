"""adapters.menu —— RT_MENU(4)：普通模板（version 0）与 MENUEX（version 1）。

格式依据 MS 文档 + Wine `dlls/user32/menu.c`（MENU_ParseResource /
MENUEX_ParseResource）交叉核对，round-trip 测试为最终裁判。
详见 docs/formats/MENU.md。

普通模板（version 0）：
    头：WORD version=0; WORD offset;（offset 字节的保留数据跳过）
    项：WORD mtOption; [WORD mtID（仅非 popup）;] UTF-16 字符串 + NUL
    MF_POPUP(0x10) 表示该项弹出子菜单（无 mtID，子项紧随其后）；
    MF_END(0x80) 标记本层结束。

MENUEX（version 1）：
    头：WORD version=1; WORD offset;（offset 字节的头扩展原样跳过——
        经实测其内容是 dwHelpId 等，紧随其后**没有**额外的 dwHelpId 字段，
        Wine 的 LoadMenuIndirectW 在此处多读 4 字节，与 RC 实际输出不符）
    项：DWORD type; DWORD state; DWORD id; WORD flags;（文本按 WORD 对齐）
        UTF-16 字符串 + NUL;（后续字段按 DWORD 对齐）
        [DWORD helpId（仅 flags&0x01 即 popup 项）]
    flags&0x01 = popup；flags&0x80 = 本层最后一项。

以上 MENUEX 布局经 195 个本机系统 DLL/EXE 的真实 MENUEX 资源
（shell32.dll / user32.dll / webcheck.dll 等）字节级 round-trip 全量验证。
"""

from __future__ import annotations

import struct

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, entry_position, make_entry

MF_END = 0x80
MF_POPUP = 0x10
MF_SEPARATOR = 0x800

RT_MENU = 4


def _read_utf16z(data: bytes, offset: int, *, name, lang) -> tuple[str, int]:
    """读取 NUL 结尾的 UTF-16LE 字符串，返回 (文本, 新偏移)。"""
    chars: list[str] = []
    while True:
        if offset + 2 > len(data):
            raise ParseError(
                "菜单字符串未找到 NUL 结尾", type_id=RT_MENU, name=name,
                lang=lang, offset=offset)
        (w,) = struct.unpack_from("<H", data, offset)
        offset += 2
        if w == 0:
            return "".join(chars), offset
        chars.append(chr(w))


class MenuItem:
    """菜单项（含子项）。option/flags 等原始字段逐字节保留。"""

    __slots__ = ("option", "item_id", "text", "children", "ex")

    def __init__(self, *, option: int = 0, item_id: int | None = None,
                 text: str = "", children: list["MenuItem"] | None = None,
                 ex: dict | None = None) -> None:
        self.option = option          # 普通模板 mtOption（EX 存入 ex["flags"]）
        self.item_id = item_id        # 普通模板 mtID（popup 项为 None）
        self.text = text
        self.children = children or []
        # MENUEX 专用字段
        self.ex = ex                  # {"type","state","id","flags","helpid"}

    @property
    def is_popup(self) -> bool:
        if self.ex is not None:
            return bool(self.ex["flags"] & 0x01)
        return bool(self.option & MF_POPUP)

    @property
    def is_separator(self) -> bool:
        if self.ex is not None:
            return self.text == "" and not self.is_popup
        return bool(self.option & MF_SEPARATOR) or \
            (not self.is_popup and self.text == "")


class MenuData:
    """一个菜单资源的完整模型。"""

    def __init__(self, version: int, header_extra: bytes,
                 items: list[MenuItem]) -> None:
        self.version = version
        self.header_extra = header_extra      # 头部 offset 字节的保留数据
        self.items = items


def parse_menu(data: bytes, *, name=None, lang=None) -> MenuData:
    """解析普通模板或 MENUEX 菜单资源。"""
    if len(data) < 4:
        raise ParseError("菜单资源过短", type_id=RT_MENU, name=name, lang=lang)
    version, offset = struct.unpack_from("<HH", data, 0)
    if version not in (0, 1):
        raise ParseError(
            f"未知菜单模板版本 {version}", type_id=RT_MENU, name=name, lang=lang)
    pos = 4
    if offset:
        if pos + offset > len(data):
            raise ParseError(
                "菜单头保留数据越界", type_id=RT_MENU, name=name, lang=lang,
                offset=pos)
        header_extra = bytes(data[pos:pos + offset])
        pos += offset
    else:
        header_extra = b""

    if version == 0:
        items, pos = _parse_normal_items(data, pos, name=name, lang=lang)
        return MenuData(0, header_extra, items)
    items, pos = _parse_ex_items(data, pos, name=name, lang=lang)
    return MenuData(1, header_extra, items)


def _parse_normal_items(data: bytes, pos: int, *, name, lang,
                        depth: int = 0) -> tuple[list[MenuItem], int]:
    items: list[MenuItem] = []
    while True:
        if pos + 2 > len(data):
            raise ParseError(
                "菜单项 mtOption 越界（缺少 MF_END）", type_id=RT_MENU,
                name=name, lang=lang, offset=pos)
        (option,) = struct.unpack_from("<H", data, pos)
        pos += 2
        item_id = None
        if not option & MF_POPUP:
            if pos + 2 > len(data):
                raise ParseError(
                    "菜单项 mtID 越界", type_id=RT_MENU, name=name, lang=lang,
                    offset=pos)
            (item_id,) = struct.unpack_from("<H", data, pos)
            pos += 2
        text, pos = _read_utf16z(data, pos, name=name, lang=lang)
        children: list[MenuItem] = []
        if option & MF_POPUP:
            if depth > 32:
                raise ParseError(
                    "菜单嵌套过深（>32 层）", type_id=RT_MENU, name=name,
                    lang=lang, offset=pos)
            children, pos = _parse_normal_items(
                data, pos, name=name, lang=lang, depth=depth + 1)
        items.append(MenuItem(option=option, item_id=item_id, text=text,
                              children=children))
        if option & MF_END:
            return items, pos


def _parse_ex_items(data: bytes, pos: int, *, name, lang,
                    depth: int = 0) -> tuple[list[MenuItem], int]:
    items: list[MenuItem] = []
    while True:
        if pos + 14 > len(data):
            raise ParseError(
                "MENUEX 项头越界", type_id=RT_MENU, name=name, lang=lang,
                offset=pos)
        ex_type, ex_state, ex_id, flags = struct.unpack_from("<IIIH", data, pos)
        pos += 14
        # 文本按 WORD 对齐（相对资源起始，Wine 原式 ~(res-1)&1）
        pos += (~(pos - 1)) & 1
        text, pos = _read_utf16z(data, pos, name=name, lang=lang)
        # 后续字段按 DWORD 对齐
        pos = (pos + 3) & ~3
        help_id = None
        if flags & 0x01:  # popup：帮助 ID 紧跟其后
            if pos + 4 > len(data):
                raise ParseError(
                    "MENUEX popup 帮助 ID 越界", type_id=RT_MENU, name=name,
                    lang=lang, offset=pos)
            (help_id,) = struct.unpack_from("<I", data, pos)
            pos += 4
        children: list[MenuItem] = []
        if flags & 0x01:
            if depth > 32:
                raise ParseError(
                    "MENUEX 嵌套过深（>32 层）", type_id=RT_MENU, name=name,
                    lang=lang, offset=pos)
            children, pos = _parse_ex_items(
                data, pos, name=name, lang=lang, depth=depth + 1)
        items.append(MenuItem(
            text=text, children=children,
            ex={"type": ex_type, "state": ex_state, "id": ex_id,
                "flags": flags, "helpid": help_id}))
        if flags & MF_END:
            return items, pos


def serialize_menu(menu: MenuData) -> bytes:
    out = bytearray()
    out += struct.pack("<HH", menu.version, len(menu.header_extra))
    out += menu.header_extra
    if menu.version == 0:
        for item in menu.items:
            _serialize_normal_item(out, item)
    else:
        for item in menu.items:
            _serialize_ex_item(out, item)
    return bytes(out)


def _serialize_normal_item(out: bytearray, item: MenuItem) -> None:
    out += struct.pack("<H", item.option)
    if not item.option & MF_POPUP:
        assert item.item_id is not None
        out += struct.pack("<H", item.item_id)
    out += item.text.encode("utf-16-le") + b"\x00\x00"
    for child in item.children:
        _serialize_normal_item(out, child)


def _serialize_ex_item(out: bytearray, item: MenuItem) -> None:
    assert item.ex is not None
    out += struct.pack("<IIIH", item.ex["type"], item.ex["state"],
                       item.ex["id"], item.ex["flags"])
    while len(out) % 2:
        out += b"\x00"
    out += item.text.encode("utf-16-le") + b"\x00\x00"
    while len(out) % 4:
        out += b"\x00"
    if item.ex["flags"] & 0x01:
        assert item.ex["helpid"] is not None
        out += struct.pack("<I", item.ex["helpid"])
    for child in item.children:
        _serialize_ex_item(out, child)


class MenuAdapter:
    """RT_MENU 适配器（两种格式统一处理）。"""

    resource_types = (4,)

    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool:
        if type_id != 4 or len(data) < 4:
            return False
        version = struct.unpack_from("<H", data, 0)[0]
        return version in (0, 1)

    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]:
        menu = parse_menu(data, name=name, lang=lang)
        entries: list[Entry] = []
        counter = 0

        def walk(items: list[MenuItem], path: str) -> None:
            nonlocal counter
            for item in items:
                position = counter
                counter += 1
                if not item.is_separator:
                    kind = "menu_title" if item.is_popup else "menu_item"
                    reason = auto_skip(item.text, is_menu=True)
                    context = {"kind": kind, "location": f"{path}/{position}"}
                    entries.append(make_entry(
                        f"RT_MENU/{name}/{lang}/{position}", "menu",
                        type_id, name, lang, context, item.text, reason))
                walk(item.children, f"{path}/{position}")

        walk(menu.items, str(name))
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        menu = parse_menu(original)
        changed = False
        by_pos = {}
        for e in entries:
            if e.status not in WRITABLE_STATUSES or e.translation is None:
                continue
            by_pos[int(entry_position(e.id))] = e
        idx = 0

        def walk_and_patch(items: list[MenuItem]) -> None:
            nonlocal changed, idx
            for item in items:
                position = idx
                idx += 1
                entry = by_pos.get(position)
                if entry is not None and item.text != entry.translation:
                    item.text = entry.translation
                    changed = True
                walk_and_patch(item.children)

        walk_and_patch(menu.items)
        if not changed:
            return original
        return serialize_menu(menu)
