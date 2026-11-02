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
