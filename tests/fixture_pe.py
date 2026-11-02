"""fixture_pe —— 纯 Python 合成最小合法 PE（测试专用）。

构造 DOS 头 + PE 头 + 单节 ``.rsrc`` 的最小 PE，资源目录树手工构建，
保证 ``LoadLibraryExW(LOAD_LIBRARY_AS_DATAFILE|LOAD_LIBRARY_AS_IMAGE_RESOURCE)``
接受并可正常枚举 / 读取 / 回写。头字段按 PE 规范填齐。

布局约定：
- 文件偏移 0x200 之前是头（SizeOfHeaders = 0x200）
- 节数据从文件偏移 0x200 开始，对应 RVA 0x1000（节 VirtualAddress）
- 即：RVA = 0x1000 + (文件偏移 - 0x200)
"""

from __future__ import annotations

import struct

SECTION_RVA = 0x1000
FILE_ALIGN = 0x200
SECTION_ALIGN = 0x1000
HEADER_SIZE = 0x200


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def _dir(entries: list[bytes]) -> bytes:
    """构建一个资源目录。entries 为已编码的 (name, offset) DWORD 对字节。"""
    named = sum(1 for e in entries[::2] if struct.unpack("<I", e)[0] & 0x80000000)
    header = struct.pack(
        "<IIHHHH", 0, 0, 0, 0, named, len(entries) // 2 - named)
    return header + b"".join(entries)


class _SectionBuilder:
    """资源节布局器：按写入顺序记录偏移。"""

    def __init__(self) -> None:
        self.buf = bytearray()

    def place(self, data: bytes, align: int = 4) -> int:
        while len(self.buf) % align:
            self.buf.append(0)
        off = len(self.buf)
        self.buf += data
        return off


def build_pe(resources: list[tuple[int, int | str, int, bytes]]) -> bytes:
    """构建包含给定资源三元组的最小 PE。

    resources: (type_id, name, lang, data) 列表；name 可为序数或字符串。
    """
    # 组织成树 type -> name -> lang -> data
    tree: dict[int | str, dict[int | str, dict[int, bytes]]] = {}
    for type_id, name, lang, data in resources:
        tree.setdefault(type_id, {}).setdefault(name, {})[lang] = data

    sec = _SectionBuilder()
    de_off: dict[tuple, int] = {}

    # 根目录必须位于资源目录区间起点（加载器按数据目录 [RVA, RVA+Size)
    # 校验所有子目录偏移），先占位、最后回填。
    root_off = sec.place(b"\x00" * (16 + 8 * len(tree)), 4)

    # 1. 数据 blob + 数据项
    for t, names in tree.items():
        for n, langs in names.items():
            for l, data in langs.items():
                blob = sec.place(data, 4)
                de = struct.pack("<IIII", SECTION_RVA + blob, len(data), 0, 0)  # DataRVA：绝对 RVA
                de_off[(t, n, l)] = sec.place(de, 4)

    # 2. 名称字符串（字符串命名的资源/类型）
    ns_off: dict[str, int] = {}

    def name_string(s: str) -> int:
        if s not in ns_off:
            ns_off[s] = sec.place(
                struct.pack("<H", len(s)) + s.encode("utf-16-le"), 2)
        return ns_off[s]

    for t in tree:
        if isinstance(t, str):
            name_string(t)
        for n in tree[t]:
            if isinstance(n, str):
                name_string(n)

    # 3. 语言叶目录（type,name 级）
    lang_dir_off: dict[tuple, int] = {}
    for t, names in tree.items():
        for n, langs in names.items():
            entries: list[bytes] = []
            for l in sorted(langs):
                entries.append(struct.pack("<I", l))
                entries.append(struct.pack("<I", de_off[(t, n, l)]))  # 数据项偏移：节相对
            lang_dir_off[(t, n)] = sec.place(_dir(entries), 4)

    # 4. 类型目录（type 级，条目为资源名）：字符串名在前（按字典序），序数名在后（升序）
    #    目录层级固定为 根 → 类型 → 名字 → 语言 → 数据项，没有中间层。
    name_dir_off: dict[int | str, int] = {}
    for t, names in tree.items():
        entries = []
        for n in sorted(x for x in names if isinstance(x, str)):
            entries.append(struct.pack("<I", 0x80000000 | name_string(n)))
            entries.append(struct.pack("<I", 0x80000000 | lang_dir_off[(t, n)]))
        for n in sorted(x for x in names if isinstance(x, int)):
            entries.append(struct.pack("<I", n))
            entries.append(struct.pack("<I", 0x80000000 | lang_dir_off[(t, n)]))
        name_dir_off[t] = sec.place(_dir(entries), 4)

    # 5. 根目录（回填到占位处）
    entries = []
    for t in sorted((x for x in tree if isinstance(x, str))):
        entries.append(struct.pack("<I", 0x80000000 | name_string(t)))
        entries.append(struct.pack("<I", 0x80000000 | name_dir_off[t]))
    for t in sorted((x for x in tree if isinstance(x, int))):
        entries.append(struct.pack("<I", t))
        entries.append(struct.pack("<I", 0x80000000 | name_dir_off[t]))
    root = _dir(entries)
    assert len(root) == 16 + 8 * len(tree)
    sec.buf[root_off:root_off + len(root)] = root

    # ---- PE 头 ----
    virtual_size = len(sec.buf)
    raw_size = _align(virtual_size, FILE_ALIGN)
    size_of_image = SECTION_RVA + _align(max(virtual_size, raw_size), SECTION_ALIGN)

    dos = bytearray(HEADER_SIZE)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    dos[0x40:0x44] = b"PE\x00\x00"

    coff = struct.pack(
        "<HHIIIHH",
        0x8664,            # Machine: x64
        1,                 # NumberOfSections
        0,                 # TimeDateStamp
        0, 0,              # symbol table
        240,               # SizeOfOptionalHeader (PE32+)
        0x0022,            # Characteristics: EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE
    )

    # 数据目录：仅资源目录（index 2）
    data_dirs = bytearray()
    for i in range(16):
        if i == 2:
            data_dirs += struct.pack("<II", SECTION_RVA + root_off,
                                     virtual_size - root_off)
        else:
            data_dirs += struct.pack("<II", 0, 0)

    opt = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x020B,            # Magic: PE32+
        14, 0,             # linker version
        raw_size,          # SizeOfCode（占位，不影响资源加载）
        raw_size,          # SizeOfInitializedData
        0,                 # SizeOfUninitializedData
        0,                 # AddressOfEntryPoint（不执行代码）
        0x1000,            # BaseOfCode
        0x140000000,       # ImageBase
        SECTION_ALIGN,     # SectionAlignment
        FILE_ALIGN,        # FileAlignment
        6, 0,              # OS version
        0, 0,              # image version
        6, 0,              # subsystem version
        0,                 # Win32VersionValue
        size_of_image,     # SizeOfImage
        HEADER_SIZE,       # SizeOfHeaders
        0,                 # CheckSum
        2,                 # Subsystem: GUI
        0,                 # DllCharacteristics
        0x100000, 0x1000,  # stack reserve/commit
        0x100000, 0x1000,  # heap reserve/commit
        0,                 # LoaderFlags
        16,                # NumberOfRvaAndSizes
    ) + bytes(data_dirs)
    assert len(opt) == 240, f"OptionalHeader 长度 {len(opt)} != 240"

    section = struct.pack(
        "<8sIIIIIIHHI",
        b".rsrc",
        virtual_size,      # VirtualSize
        SECTION_RVA,       # VirtualAddress
        raw_size,          # SizeOfRawData
        HEADER_SIZE,       # PointerToRawData
        0, 0, 0, 0,        # relocations / line numbers
        0x40000040,        # Characteristics: INITIALIZED_DATA | READ
    )
    assert len(section) == 40

    # 拼接：DOS 头(0x40) + PE 签名 + COFF + Optional + Section，补齐到 0x200
    body = b"PE\x00\x00" + coff + opt + section
    assert 0x40 + len(body) <= HEADER_SIZE, (len(body), HEADER_SIZE)
    headers = (bytes(dos[:0x40]) + body).ljust(HEADER_SIZE, b"\x00")

    return headers + bytes(sec.buf).ljust(raw_size, b"\x00")


# ---------------------------------------------------------------------------
# 各资源内容体构造器（随 Phase 推进补充）
# ---------------------------------------------------------------------------

def make_string_block(strings: list[str]) -> bytes:
    """构造一个 RT_STRING block（必须恰 16 条）。

    char_count 以 UTF-16 码元为单位（与 Windows 一致，代理对算 2）。
    """
    if len(strings) != 16:
        raise ValueError(f"字符串表 block 必须为 16 条，收到 {len(strings)}")
    out = bytearray()
    for s in strings:
        encoded = s.encode("utf-16-le")
        out += struct.pack("<H", len(encoded) // 2)
        out += encoded
    return bytes(out)


# ---------------------------------------------------------------------------
# MENU（普通模板 + MENUEX）
# ---------------------------------------------------------------------------

MF_END = 0x80
MF_POPUP = 0x10
MF_SEPARATOR = 0x800


def make_normal_menu(items: list) -> bytes:
    """构造普通模板菜单（version 0）。

    items 元素：("item", option, id, text) 或 ("popup", option, text, children)
    option 中的 MF_END 由本函数自动标注（每层最后一项）。
    """
    out = bytearray(struct.pack("<HH", 0, 0))

    def dump(seq: list, is_root: bool) -> None:
        for index, spec in enumerate(seq):
            last = index == len(seq) - 1
            if spec[0] == "popup":
                _, option, text, children = spec
                option = (option & ~MF_END) | (MF_END if last else 0) | MF_POPUP
                out.extend(struct.pack("<H", option))
                out.extend(text.encode("utf-16-le") + b"\x00\x00")
                dump(children, False)
            else:
                _, option, item_id, text = spec
                option = (option & ~MF_END) | (MF_END if last else 0)
                out.extend(struct.pack("<H", option))
                out.extend(struct.pack("<H", item_id))
                out.extend(text.encode("utf-16-le") + b"\x00\x00")

    dump(items, True)
    return bytes(out)


def make_menuex(items: list) -> bytes:
    """构造 MENUEX 菜单（version 1）。

    items 元素：("item", type, state, id, text) 或
                ("popup", type, state, id, text, helpid, children)
    每层最后一项自动置 flags 的 0x80 位。
    """
    out = bytearray(struct.pack("<HH", 1, 0))

    def dump(seq: list) -> None:
        for index, spec in enumerate(seq):
            last = index == len(seq) - 1
            if spec[0] == "popup":
                _, ex_type, state, ex_id, text, helpid, children = spec
                flags = 0x01 | (0x80 if last else 0)  # popup + end
                out.extend(struct.pack("<IIIH", ex_type, state, ex_id, flags))
                while len(out) % 2:
                    out.extend(b"\x00")
                out.extend(text.encode("utf-16-le") + b"\x00\x00")
                while len(out) % 4:
                    out.extend(b"\x00")
                out.extend(struct.pack("<I", helpid))
                dump(children)
            else:
                _, ex_type, state, ex_id, text = spec
                flags = 0x80 if last else 0
                out.extend(struct.pack("<IIIH", ex_type, state, ex_id, flags))
                while len(out) % 2:
                    out.extend(b"\x00")
                out.extend(text.encode("utf-16-le") + b"\x00\x00")
                while len(out) % 4:
                    out.extend(b"\x00")

    dump(items)
    return bytes(out)


# ---------------------------------------------------------------------------
# DIALOG（DLGTEMPLATE + DLGTEMPLATEEX）
# ---------------------------------------------------------------------------

DS_SETFONT = 0x40


def _sz_or_ord(out: bytearray, value) -> None:
    """value: None | int（序数）| str。"""
    if value is None:
        out.extend(struct.pack("<H", 0))
    elif isinstance(value, int):
        out.extend(struct.pack("<HH", 0xFFFF, value))
    else:
        out += value.encode("utf-16-le") + b"\x00\x00"


def _align4(offset: int) -> int:
    return (offset + 3) & ~3


def make_dialog_standard(*, style: int, ex_style: int = 0,
                         x: int = 0, y: int = 0, cx: int = 100, cy: int = 50,
                         menu=None, wclass=None, title: str = "",
                         font: tuple[int, str] | None = None,
                         items: list[dict] | None = None) -> bytes:
    """构造标准 DLGTEMPLATE。

    items 元素 dict: style, ex_style, x, y, cx, cy, id, wclass, title,
    extra(bytes, 可省)。
    """
    # 控件按“资源起始”DWORD 对齐：头 18 字节计入
    body = bytearray(struct.pack("<IIHhhhh", style, ex_style, len(items or []),
                                 x, y, cx, cy))
    _sz_or_ord(body, menu)
    _sz_or_ord(body, wclass)
    body += title.encode("utf-16-le") + b"\x00\x00"
    if style & DS_SETFONT and font:
        pointsize, typeface = font
        body += struct.pack("<H", pointsize)
        body += typeface.encode("utf-16-le") + b"\x00\x00"
    for spec in items or []:
        while len(body) % 4:
            body += b"\x00"
        body += struct.pack("<IIhhhhH", spec["style"], spec.get("ex_style", 0),
                            spec["x"], spec["y"], spec["cx"], spec["cy"],
                            spec["id"])
        _sz_or_ord(body, spec["wclass"])
        _sz_or_ord(body, spec.get("title"))
        extra = spec.get("extra", b"")
        body += struct.pack("<H", len(extra))
        body += extra
    return bytes(body)


def make_dialog_ex(*, help_id: int = 0, style: int, ex_style: int = 0,
                   x: int = 0, y: int = 0, cx: int = 100, cy: int = 50,
                   menu=None, wclass=None, title: str = "",
                   font: tuple[int, int, int, int, str] | None = None,
                   items: list[dict] | None = None) -> bytes:
    """构造 DLGTEMPLATEEX。

    font: (pointsize, weight, italic, charset, typeface)；
    items 元素同 make_dialog_standard，另支持 help_id。
    """
    # 控件按“资源起始”DWORD 对齐：头 26 字节计入
    body = bytearray(struct.pack("<HHIIIHhhhh", 1, 0xFFFF, help_id, ex_style,
                                 style, len(items or []), x, y, cx, cy))
    _sz_or_ord(body, menu)
    _sz_or_ord(body, wclass)
    body += title.encode("utf-16-le") + b"\x00\x00"
    if style & DS_SETFONT and font:
        pointsize, weight, italic, charset, typeface = font
        body += struct.pack("<HHBB", pointsize, weight, italic, charset)
        body += typeface.encode("utf-16-le") + b"\x00\x00"
    for spec in items or []:
        while len(body) % 4:
            body += b"\x00"
        body += struct.pack("<IIIhhhhI", spec.get("help_id", 0),
                            spec.get("ex_style", 0), spec["style"],
                            spec["x"], spec["y"], spec["cx"], spec["cy"],
                            spec["id"])
        _sz_or_ord(body, spec["wclass"])
        _sz_or_ord(body, spec.get("title"))
        extra = spec.get("extra", b"")
        body += struct.pack("<H", len(extra))
        body += extra
    return bytes(body)


# ---------------------------------------------------------------------------
# Delphi DFM（binary）
# ---------------------------------------------------------------------------

VA_LIST = 1
VA_INT8 = 2
VA_INT16 = 3
VA_INT32 = 4
VA_STRING = 6
VA_IDENT = 7
VA_FALSE = 8
VA_TRUE = 9
VA_BINARY = 10
VA_SET = 11
VA_COLLECTION = 14
VA_WSTRING = 18
VA_UTF8STRING = 20


def _shortstr(s: bytes) -> bytes:
    if len(s) > 255:
        raise ValueError("短串过长")
    return bytes([len(s)]) + s


def _dword_str(s: bytes) -> bytes:
    return struct.pack("<I", len(s)) + s


class DfmBuilder:
    """构造 binary DFM（TPF0）。支持全部常用值类型。"""

    def __init__(self, cls_name: str, obj_name: str) -> None:
        self.cls = cls_name.encode("latin-1")
        self.name = obj_name.encode("latin-1")
        self.props: list[bytes] = []
        self.children: list[bytes] = []
        self.child_prefixes: list[bytes] = []

    def prop_raw(self, pname: str, vtype: int, payload: bytes) -> "DfmBuilder":
        self.props.append(_shortstr(pname.encode("latin-1")) +
                          bytes([vtype]) + payload)
        return self

    def prop_int(self, pname: str, value: int) -> "DfmBuilder":
        if -128 <= value <= 127:
            return self.prop_raw(pname, VA_INT8, struct.pack("<b", value))
        if -32768 <= value <= 32767:
            return self.prop_raw(pname, VA_INT16, struct.pack("<h", value))
        return self.prop_raw(pname, VA_INT32, struct.pack("<i", value))

    def prop_bool(self, pname: str, value: bool) -> "DfmBuilder":
        return self.prop_raw(pname, VA_TRUE if value else VA_FALSE, b"")

    def prop_ident(self, pname: str, ident: str) -> "DfmBuilder":
        return self.prop_raw(pname, VA_IDENT, _shortstr(ident.encode("latin-1")))

    def prop_string(self, pname: str, text: str) -> "DfmBuilder":
        return self.prop_raw(pname, VA_STRING,
                             _shortstr(text.encode("gbk")))

    def prop_wstring(self, pname: str, text: str) -> "DfmBuilder":
        return self.prop_raw(pname, VA_WSTRING,
                             _dword_str(text.encode("utf-16-le")))

    def prop_utf8(self, pname: str, text: str) -> "DfmBuilder":
        return self.prop_raw(pname, VA_UTF8STRING,
                             _dword_str(text.encode("utf-8")))

    def prop_set(self, pname: str, idents: list[str]) -> "DfmBuilder":
        payload = b"".join(_shortstr(i.encode("latin-1")) for i in idents)
        payload += b"\x00"
        return self.prop_raw(pname, VA_SET, payload)

    def prop_binary(self, pname: str, blob: bytes) -> "DfmBuilder":
        return self.prop_raw(pname, VA_BINARY, _dword_str(blob))

    def prop_string_list(self, dotted_name: str, lines: list[str]) -> "DfmBuilder":
        """Lines.Strings 形式的字符串列表（vaList，项为 vaString）。"""
        payload = b""
        for line in lines:
            payload += bytes([VA_STRING]) + _shortstr(line.encode("gbk"))
        payload += b"\x00"
        return self.prop_raw(dotted_name, VA_LIST, payload)

    def prop_collection(self, pname: str,
                        items: list[list[tuple[str, int, bytes]]],
                        with_list_flag: bool = True) -> "DfmBuilder":
        """vaCollection。items 每项为 (name, vtype, payload) 属性列表。"""
        payload = b""
        for props in items:
            body = b""
            for pname2, vtype, val in props:
                body += _shortstr(pname2.encode("latin-1")) + \
                    bytes([vtype]) + val
            if with_list_flag:
                payload += b"\x01" + body + b"\x00"
            else:
                payload += body + b"\x00"
        payload += b"\x00"
        return self.prop_raw(pname, VA_COLLECTION, payload)

    def child(self, builder: "DfmBuilder", prefix: bytes = b"") -> "DfmBuilder":
        """添加子对象。prefix: b""（普通）/ b"\xf1" 等 $F0 系前缀。"""
        self.child_prefixes.append(prefix)
        self.children.append(builder._body())
        return self

    def _body(self) -> bytes:
        """对象体（不含 TPF0 签名，供嵌套复用）。"""
        out = bytearray()
        out += _shortstr(self.cls)
        out += _shortstr(self.name)
        for p in self.props:
            out += p
        out += b"\x00"
        for prefix, child in zip(self.child_prefixes, self.children):
            out += prefix
            out += child
        out += b"\x00"
        return bytes(out)

    def __bytes__(self) -> bytes:
        return b"TPF0" + self._body()


def default_fixture_resources() -> list[tuple[int, int | str, int, bytes]]:
    """合成 PE 的默认资源集合：四类资源 + 占位跳过项。"""
    block1 = make_string_block([
        "File", "&New", "&Open", "&Save", "Exit", "", "Edit", "&Undo",
        "Cu&t", "&Copy", "&Paste", "Select All", "", "Hello, 世界!",
        "Curly “quotes” and emoji 🎉", "Tab\there",
    ])
    block2 = make_string_block([
        "", "42", "  ", "&X", "...", "©2024", "€$£", "1.5",
        "-", "", "OK", "", "", "", "", "",
    ])
    menu = make_normal_menu([
        ("popup", 0, "&File", [
            ("item", 0, 100, "&New"),
            ("item", 0, 101, "&Open"),
            ("item", MF_SEPARATOR, 0, ""),
            ("item", 0, 102, "E&xit"),
        ]),
        ("popup", 0, "&Help", [
            ("item", 0, 103, "&About DemoApp"),
        ]),
    ])
    menuex = make_menuex([
        ("popup", 0, 0, 10, "&File", 77, [
            ("item", 0, 0, 100, "&New"),
            ("item", 0, 0, 101, "E&xit"),
        ]),
        ("popup", 0, 0, 11, "&Help", 0, [
            ("item", 0, 0, 102, "&About"),
        ]),
    ])
    dlg_std = make_dialog_standard(
        style=DS_SETFONT | 0x800C0000, title="About DemoApp",
        font=(8, "MS Shell Dlg"),
        items=[
            {"style": 0x50020000, "x": 32, "y": 8, "cx": 120, "cy": 10,
             "id": 1002, "wclass": 0x82, "title": "Demo Application, "
             "Version 1.0"},
            {"style": 0x50010001, "x": 56, "y": 56, "cx": 48, "cy": 14,
             "id": 1, "wclass": 0x80, "title": "OK"},
            {"style": 0x50000003, "x": 8, "y": 8, "cx": 20, "cy": 20,
             "id": 1001, "wclass": "STATIC", "title": None},
        ])
    dlg_ex = make_dialog_ex(
        style=DS_SETFONT | 0x800C0000, title="Settings",
        font=(8, 400, 0, 0, "MS Shell Dlg"),
        items=[
            {"style": 0x50020000, "x": 8, "y": 8, "cx": 60, "cy": 10,
             "id": 2001, "wclass": 0x82, "title": "User &name:",
             "help_id": 7},
            {"style": 0x50810080, "x": 70, "y": 6, "cx": 100, "cy": 12,
             "id": 2002, "wclass": "EDIT", "title": None},
        ])
    form = DfmBuilder("TForm1", "Form1")
    form.prop_int("Left", 191).prop_int("Top", 139)
    form.prop_string("Caption", "Hello")
    form.prop_set("BorderIcons", ["biSystemMenu", "biMinimize"])
    form.prop_bool("Scaled", False)
    form.prop_binary("Icon.Data", b"\x00\x01\x02\x03binary-icon")
    form.prop_string_list("Lines.Strings", ["alpha", "beta", "gamma"])
    edit = DfmBuilder("TEdit", "Edit1")
    edit.prop_string("Text", "Type here").prop_int("TabOrder", 0)
    form.child(edit)
    lbl = DfmBuilder("TLabel", "Label1")
    lbl.prop_string("Caption", "User &name:").prop_wstring("Hint", "提示")
    form.child(lbl)
    dfm = bytes(form)
    return [
        (6, 1, 1033, block1),
        (6, 2, 1033, block2),
        (4, 101, 1033, menu),
        (4, 102, 1033, menuex),
        (5, 103, 1033, dlg_std),
        (5, 104, 1033, dlg_ex),
        (10, "TForm1", 1033, dfm),
        (16, 1, 1033, b"VS_VERSION_INFO-fixture-placeholder" * 4),
        (10, 900, 1033, b"\x00\x01\x02not-a-dfm" * 8),
        (10, "TEXTDATA", 1033, b"plain rcdata, string resource name"),
    ]


def default_fixture_resources() -> list[tuple[int, int | str, int, bytes]]:
    """Phase 1 默认合成资源：2 个字符串表 + 占位 RT_VERSION + 2 个 RCDATA。"""
    block1 = make_string_block([
        "File", "&New", "&Open", "&Save", "Exit", "", "Edit", "&Undo",
        "Cu&t", "&Copy", "&Paste", "Select All", "", "Hello, 世界!",
        "Curly “quotes” and emoji 🎉", "Tab\there",
    ])
    block2 = make_string_block([
        "", "42", "  ", "&X", "...", "©2024", "€$£", "1.5",
        "-", "", "OK", "", "", "", "", "",
    ])
    return [
        (6, 1, 1033, block1),
        (6, 2, 1033, block2),
        (16, 1, 1033, b"VS_VERSION_INFO-fixture-placeholder" * 4),
        (10, 900, 1033, b"\x00\x01\x02not-a-dfm" * 8),
        (10, "TEXTDATA", 1033, b"plain rcdata, string resource name"),
    ]


def build_fixture_pe() -> bytes:
    """构建 Phase 1 默认合成 PE。"""
    return build_pe(default_fixture_resources())
