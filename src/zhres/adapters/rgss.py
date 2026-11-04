"""adapters.rgss —— RPG Maker XP/VX/VX Ace（RGSS）适配器。

覆盖两类载体：
- **RGSSAD 档案**（``Game.rgssad`` = v1 / XP+VX；``Game.rgss3a`` = v3 /
  VX Ace）：自定义 XOR 流加密的包档案，内部是 Ruby Marshal 数据
  （``Data/*.rxdata``/``.rvdata``/``.rvdata2``）
- **散装 Marshal 数据**（编辑器工程或解包后的 ``.rxdata`` 等）

Ruby Marshal 4.8 按 ruby/ruby 官方 marshal.c 逐字段实现
（w_long/r_long 的 5 偏移变长编码、对象/符号 0 基链接表、``I``
ivar 包装写在内容之前、u UserDef 原样载荷），**未修改内容序列化
后字节级一致**。RGSS 解密算法与 arc_unpacker 实现对照并用其官方
测试档案验证（v1/v3 均通过）。

编码裁定：
- ``.rvdata2``（VX Ace，Ruby 1.9）：UTF-8；二进制串（如 zlib 压缩的
  脚本数据）UTF-8 严格解码失败即跳过
- ``.rxdata``/``.rvdata``（XP/VX，Ruby 1.8）：无编码标记，按 GBK 解
  （简中汉化惯例），失败跳过

回写：改写 Marshal 字符串后**整体重新序列化 + 档案重加密**——
加密算法完全确定，未修改条目逐字节复现原密文；``Scripts.*``（Ruby
代码，翻译必坏）整体跳过。

限制（如实告知）：XP/VX 的日文数据按 GBK 解码可能出现乱码条目
（噪音，可忽略）；TYPE_EXTENDED（单例类）等罕见标签明确报错。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry
from .gamejson import _is_ident, _looks_like_asset, _looks_like_code

MARSHAL_MAGIC = b"\x04\x08"
RGSSAD_V1_MAGIC = b"RGSSAD\x00\x01"
RGSSAD_V3_MAGIC = b"RGSSAD\x00\x03"
RGSSAD_V1_KEY = 0xDEADCAFE

# 跳过的档案条目（Ruby 脚本：翻译必坏）
_SKIP_ENTRIES = ("scripts",)

# zlib 魔数（XP/VX 的 Scripts 条目内嵌 zlib 数据的二次保险）
_ZLIB_HEADS = (0x01, 0x5E, 0x9C, 0xDA, 0x20, 0x7D, 0xBB, 0xF9)

# RM 事件脚本指令里的 Ruby 代码特征
_RM_CODE_TOKENS = ("$game", "$data", "$scene", "$rgss", "Audio.",
                   "Input.", "Graphics.", "def ", "elsif", "unless ")


def _advance_key(key: int) -> int:
    return (key * 7 + 3) & 0xFFFFFFFF


def is_rgss_data(head: bytes, filename: str) -> bool:
    """廉价兴趣判断：RGSSAD 魔数，或 Marshal 魔数 + RM 数据扩展名。"""
    if head[:8] in (RGSSAD_V1_MAGIC, RGSSAD_V3_MAGIC):
        return True
    if head[:2] == MARSHAL_MAGIC:
        lower = filename.lower()
        return lower.endswith((".rxdata", ".rvdata", ".rvdata2"))
    return False


# ---------------------------------------------------------------------------
# Ruby Marshal 4.8 节点
# ---------------------------------------------------------------------------

@dataclass
class MString:
    raw: bytes
    ivars: list | None = None      # 'I' 包装的 ivar 对（编码标记等）


@dataclass
class MSymbol:
    name: bytes                    # 原始字节


@dataclass
class MSymLink:
    index: int


@dataclass
class MLink:
    index: int


@dataclass
class MFixnum:
    value: int


@dataclass
class MBignum:
    raw: bytes                     # 符号字节之后的原始载荷


@dataclass
class MFloat:
    raw: bytes                     # 长度前缀字符串原始字节（保持格式）


@dataclass
class MRegexp:
    src: bytes
    opts: int
    ivars: list | None = None


@dataclass
class MArray:
    items: list = field(default_factory=list)
    ivars: list | None = None


@dataclass
class MHash:
    pairs: list = field(default_factory=list)   # [(k, v)]
    default: object = None         # '}' 才有
    ivars: list | None = None


@dataclass
class MObject:
    cls: object                    # 符号节点
    ivars: list = field(default_factory=list)   # 自身 @ 变量
    wrapper_ivars: list | None = None           # 'I' 包装（扩展模块等）


@dataclass
class MStruct:
    cls: object
    members: list = field(default_factory=list)


@dataclass
class MUserDef:
    cls: object
    raw: bytes = b""               # _dump 载荷原样
    ivars: list | None = None


@dataclass
class MUserMarshal:
    cls: object
    value: object = None


@dataclass
class MUClass:
    cls: object                    # 'C'：String/Array/Hash 子类
    inner: object = None


@dataclass
class MClass:
    name: bytes


@dataclass
class MModule:
    name: bytes


MNil = "nil"
MTrue = "true"
MFalse = "false"


class _Reader:
    def __init__(self, data: bytes, name=None) -> None:
        self.data = data
        self.pos = 0
        self.name = name
        self.symbols: list[object] = []   # 0 基符号表
        self.objects: list[object] = []   # 0 基对象表

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise ParseError("Marshal 数据提前结束", name=self.name)
        b = self.data[self.pos]
        self.pos += 1
        return b

    def nbytes(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ParseError("Marshal 数据越界", name=self.name)
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def long(self) -> int:
        """ruby marshal.c r_long 的精确复刻（首字节按有符号 char）。"""
        c = self.byte()
        if c >= 128:
            c -= 256
        if c == 0:
            return 0
        if c > 0:
            if 4 < c < 128:
                return c - 5
            x = 0
            for i in range(c):
                x |= self.byte() << (8 * i)
            return x
        if -129 < c < -4:
            return c + 5
        c = -c
        x = -1
        for i in range(c):
            x &= ~(0xFF << (8 * i))
            x |= self.byte() << (8 * i)
        return x

    def sym(self):
        b = self.byte()
        if b == 0x3A:                      # ':' 新符号
            n = self.long()
            node = MSymbol(self.nbytes(n))
            self.symbols.append(node)
            return node
        if b == 0x3B:                      # ';' 符号链接
            idx = self.long()
            if idx >= len(self.symbols):
                raise ParseError(f"符号链接越界：{idx}", name=self.name)
            return MSymLink(idx)
        raise ParseError(f"非法符号标记 {b:#x}", name=self.name,
                         offset=self.pos - 1)

    def _read_ivar_pairs(self) -> list:
        count = self.long()
        if count < 0 or count > 1_000_000:
            raise ParseError(f"ivar 数量异常：{count}", name=self.name)
        pairs = []
        for _ in range(count):
            s = self.sym()
            v = self.object()
            pairs.append((s, v))
        return pairs

    def _string_payload(self) -> bytes:
        n = self.long()
        if n < 0 or n > len(self.data) - self.pos:
            raise ParseError(f"字符串长度异常：{n}", name=self.name)
        return self.nbytes(n)

    def object(self):
        b = self.byte()
        if b == 0x30:                      # '0' nil
            return MNil
        if b == 0x54:                      # 'T'
            return MTrue
        if b == 0x46:                      # 'F'
            return MFalse
        if b == 0x69:                      # 'i' fixnum
            return MFixnum(self.long())
        if b == 0x6C:                      # 'l' bignum
            sign = self.byte()
            count = self.long()
            node = MBignum(bytes([sign]) + self.nbytes(count * 2))
            self.objects.append(node)
            return node
        if b == 0x66:                      # 'f' float
            node = MFloat(self._string_payload())
            self.objects.append(node)
            return node
        if b == 0x22:                      # '"' string
            node = MString(self._string_payload())
            self.objects.append(node)
            return node
        if b == 0x49:                      # 'I' ivar 包装（写在内容之前）
            inner = self.object()
            pairs = self._read_ivar_pairs()
            if isinstance(inner, (MString, MArray, MHash, MRegexp,
                                  MObject, MUserDef)):
                if getattr(inner, "ivars", None) is None and \
                        not isinstance(inner, MObject):
                    inner.ivars = pairs
                else:
                    inner.wrapper_ivars = pairs
                return inner
            if isinstance(inner, (MFixnum, MBignum, MFloat, MLink)):
                return inner
            raise ParseError("I 包装了不支持的对象类型", name=self.name)
        if b == 0x5B:                      # '[' array
            node = MArray()
            self.objects.append(node)
            n = self.long()
            if n < 0 or n > 100_000_000:
                raise ParseError(f"数组长度异常：{n}", name=self.name)
            node.items = [self.object() for _ in range(n)]
            return node
        if b in (0x7B, 0x7D):              # '{' hash / '}' 带默认值
            node = MHash()
            self.objects.append(node)
            n = self.long()
            if n < 0 or n > 100_000_000:
                raise ParseError(f"哈希长度异常：{n}", name=self.name)
            for _ in range(n):
                k = self.object()
                v = self.object()
                node.pairs.append((k, v))
            if b == 0x7D:
                node.default = self.object()
            return node
        if b == 0x6F:                      # 'o' object
            cls = self.sym()
            node = MObject(cls)
            self.objects.append(node)
            n = self.long()
            if n < 0 or n > 100_000_000:
                raise ParseError(f"对象变量数异常：{n}", name=self.name)
            for _ in range(n):
                s = self.sym()
                v = self.object()
                node.ivars.append((s, v))
            return node
        if b == 0x53:                      # 'S' struct
            cls = self.sym()
            node = MStruct(cls)
            self.objects.append(node)
            n = self.long()
            for _ in range(n):
                s = self.sym()
                v = self.object()
                node.members.append((s, v))
            return node
        if b == 0x75:                      # 'u' userdef（Table/Color/Tone）
            cls = self.sym()
            node = MUserDef(cls, self._string_payload())
            self.objects.append(node)
            return node
        if b == 0x55:                      # 'U' usermarshal
            cls = self.sym()
            node = MUserMarshal(cls)
            self.objects.append(node)
            node.value = self.object()
            return node
        if b == 0x43:                      # 'C' uclass
            cls = self.sym()
            return MUClass(cls, self.object())
        if b == 0x2F:                      # '/' regexp
            src = self._string_payload()
            node = MRegexp(src, self.byte())
            self.objects.append(node)
            return node
        if b == 0x63:                      # 'c' class
            node = MClass(self._string_payload())
            self.objects.append(node)
            return node
        if b == 0x6D:                      # 'm' module
            node = MModule(self._string_payload())
            self.objects.append(node)
            return node
        if b == 0x40:                      # '@' 对象链接
            idx = self.long()
            if idx >= len(self.objects):
                raise ParseError(f"对象链接越界：{idx}", name=self.name)
            return MLink(idx)
        if b in (0x3A, 0x3B):              # 顶层符号/符号链接
            self.pos -= 1
            return self.sym()
        if b == 0x65:                      # 'e' extended
            raise ParseError("TYPE_EXTENDED（单例类）暂不支持",
                             name=self.name)
        raise ParseError(f"未知 Marshal 类型标记 {b:#x}", name=self.name,
                         offset=self.pos - 1)


def _write_long(out: bytearray, x: int) -> None:
    """ruby_marshal_write_long 的精确复刻。"""
    if x == 0:
        out.append(0)
        return
    if 0 < x < 123:
        out.append(x + 5)
        return
    if -124 < x < 0:
        out.append((x - 5) & 0xFF)
        return
    buf = bytearray()
    v = x
    header = 0
    for i in range(1, 6):
        buf.append(v & 0xFF)
        v >>= 8
        if v == 0:
            header = i
            break
        if v == -1:
            header = -i
            break
    out.append(header & 0xFF)
    out += buf


class _Writer:
    def __init__(self) -> None:
        self.out = bytearray()
        self.symbols: dict[int, int] = {}   # id(node) → 序号
        self.objects: dict[int, int] = {}

    # -- 符号 -----------------------------------------------------------

    def symbol(self, node) -> None:
        idx = self.symbols.get(id(node))
        if idx is not None:
            self.out.append(0x3B)
            _write_long(self.out, idx)
            return
        self.symbols[id(node)] = len(self.symbols)
        self.out.append(0x3A)
        _write_long(self.out, len(node.name))
        self.out += node.name

    # -- 对象 -----------------------------------------------------------

    def _register(self, node) -> bool:
        """已注册则发链接并返回 True；否则登记。"""
        idx = self.objects.get(id(node))
        if idx is not None:
            self.out.append(0x40)
            _write_long(self.out, idx)
            return True
        self.objects[id(node)] = len(self.objects)
        return False

    def _emit_wrapper_start(self, ivars) -> bool:
        has = ivars is not None
        if has:
            self.out.append(0x49)
        return has

    def _emit_wrapper_end(self, ivars, has: bool) -> None:
        if not has:
            return
        _write_long(self.out, len(ivars))
        for s, v in ivars:
            self.symbol(s)
            self.object(v)

    def _string_bytes(self, raw: bytes) -> None:
        _write_long(self.out, len(raw))
        self.out += raw

    def object(self, node) -> None:
        if node is MNil:
            self.out.append(0x30)
            return
        if node is MTrue:
            self.out.append(0x54)
            return
        if node is MFalse:
            self.out.append(0x46)
            return
        if isinstance(node, MFixnum):
            self.out.append(0x69)
            _write_long(self.out, node.value)
            return
        if isinstance(node, MSymbol):
            self.symbol(node)
            return
        if isinstance(node, MLink):
            self.out.append(0x40)
            _write_long(self.out, node.index)
            return
        if isinstance(node, MSymLink):
            self.out.append(0x3B)
            _write_long(self.out, node.index)
            return
        if isinstance(node, MBignum):
            if self._register(node):
                return
            self.out.append(0x6C)
            self.out += node.raw
            return
        if isinstance(node, MFloat):
            if self._register(node):
                return
            self.out.append(0x66)
            self._string_bytes(node.raw)
            return
        if isinstance(node, MString):
            if self._register(node):
                return
            has = self._emit_wrapper_start(node.ivars)
            self.out.append(0x22)
            self._string_bytes(node.raw)
            self._emit_wrapper_end(node.ivars, has)
            return
        if isinstance(node, MRegexp):
            if self._register(node):
                return
            has = self._emit_wrapper_start(node.ivars)
            self.out.append(0x2F)
            self._string_bytes(node.src)
            self.out.append(node.opts)
            self._emit_wrapper_end(node.ivars, has)
            return
        if isinstance(node, MArray):
            if self._register(node):
                return
            has = self._emit_wrapper_start(node.ivars)
            self.out.append(0x5B)
            _write_long(self.out, len(node.items))
            for item in node.items:
                self.object(item)
            self._emit_wrapper_end(node.ivars, has)
            return
        if isinstance(node, MHash):
            if self._register(node):
                return
            has = self._emit_wrapper_start(node.ivars)
            self.out.append(0x7D if node.default is not None else 0x7B)
            _write_long(self.out, len(node.pairs))
            for k, v in node.pairs:
                self.object(k)
                self.object(v)
            if node.default is not None:
                self.object(node.default)
            self._emit_wrapper_end(node.ivars, has)
            return
        if isinstance(node, MObject):
            if self._register(node):
                return
            has = self._emit_wrapper_start(node.wrapper_ivars)
            self.out.append(0x6F)
            self.symbol(node.cls)
            _write_long(self.out, len(node.ivars))
            for s, v in node.ivars:
                self.symbol(s)
                self.object(v)
            self._emit_wrapper_end(node.wrapper_ivars, has)
            return
        if isinstance(node, MStruct):
            if self._register(node):
                return
            self.out.append(0x53)
            self.symbol(node.cls)
            _write_long(self.out, len(node.members))
            for s, v in node.members:
                self.symbol(s)
                self.object(v)
            return
        if isinstance(node, MUserDef):
            if self._register(node):
                return
            self.out.append(0x75)
            self.symbol(node.cls)
            self._string_bytes(node.raw)
            has = self._emit_wrapper_start(node.ivars)
            self._emit_wrapper_end(node.ivars, has)
            return
        if isinstance(node, MUserMarshal):
            if self._register(node):
                return
            self.out.append(0x55)
            self.symbol(node.cls)
            self.object(node.value)
            return
        if isinstance(node, MUClass):
            self.out.append(0x43)
            self.symbol(node.cls)
            self.object(node.inner)
            return
        if isinstance(node, MClass):
            if self._register(node):
                return
            self.out.append(0x63)
            self._string_bytes(node.name)
            return
        if isinstance(node, MModule):
            if self._register(node):
                return
            self.out.append(0x6D)
            self._string_bytes(node.name)
            return
        raise ParseError(f"无法序列化的 Marshal 节点：{type(node).__name__}")


def parse_marshal(data: bytes, *, name=None):
    """解析 Marshal 4.8 数据（须以 \\x04\\x08 开头，且不允许尾随数据）。"""
    if data[:2] != MARSHAL_MAGIC:
        raise ParseError("缺少 Marshal 魔数", name=name)
    r = _Reader(data, name=name)
    major = r.byte()
    minor = r.byte()
    if major != 4 or minor != 8:
        raise ParseError(f"不支持的 Marshal 版本 {major}.{minor}",
                         name=name)
    node = r.object()
    if r.pos != len(data):
        raise ParseError(
            f"Marshal 数据后存在 {len(data) - r.pos} 字节尾随内容",
            name=name)
    return node


def serialize_marshal(node) -> bytes:
    w = _Writer()
    w.object(node)
    return bytes(b"\x04\x08" + w.out)


# ---------------------------------------------------------------------------
# RGSSAD 档案
# ---------------------------------------------------------------------------

@dataclass
class RGSSADItem:
    name: str                     # 展示用
    name_raw: bytes               # 原始字节（回写原样保留）
    size: int
    offset: int
    key: int                      # 数据解密密钥快照
    data: bytes = b""             # 解密后的内容


def _decrypt_stream(data: bytes, offset: int, size: int, key: int) -> bytes:
    out = bytearray()
    pos = offset
    remaining = size
    while remaining > 0:
        chunk = data[pos:pos + 4]
        chunk += b"\x00" * (4 - len(chunk))
        (c,) = struct.unpack("<I", chunk)
        c ^= key
        out += struct.pack("<I", c)
        key = _advance_key(key)
        pos += 4
        remaining -= 4
    return bytes(out[:size])


def _encrypt_stream(content: bytes, key: int) -> bytes:
    pad = content + b"\x00" * ((4 - len(content) % 4) % 4)
    out = bytearray()
    for i in range(0, len(pad), 4):
        (c,) = struct.unpack("<I", pad[i:i + 4])
        out += struct.pack("<I", c ^ key)
        key = _advance_key(key)
    return bytes(out[:len(content)])


def _decode_name(raw: bytes) -> str:
    for enc in ("utf-8", "cp932", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_rgssad(data: bytes, *, name=None) -> list[RGSSADItem]:
    """解析 RGSSAD v1/v3 档案，返回全部条目（已解密）。"""
    if data[:8] == RGSSAD_V1_MAGIC:
        items = []
        key = RGSSAD_V1_KEY
        p = 8
        while p + 4 <= len(data):
            (name_len,) = struct.unpack_from("<I", data, p)
            name_len ^= key
            key = _advance_key(key)
            p += 4
            if name_len == 0 or name_len > len(data) - p:
                raise ParseError(f"条目名长度异常：{name_len}", name=name)
            name_raw = bytearray(data[p:p + name_len])
            p += name_len
            for i in range(name_len):
                name_raw[i] ^= key & 0xFF
                key = _advance_key(key)
            (size,) = struct.unpack_from("<I", data, p)
            size ^= key
            key = _advance_key(key)
            p += 4
            if size > len(data) - p:
                raise ParseError(f"条目数据越界：{size}", name=name)
            item = RGSSADItem(_decode_name(bytes(name_raw)),
                              bytes(name_raw), size, p, key)
            item.data = _decrypt_stream(data, p, size, key)
            p += size
            items.append(item)
        return items
    if data[:8] == RGSSAD_V3_MAGIC:
        items = []
        (seed,) = struct.unpack_from("<I", data, 8)
        key = (seed * 9 + 3) & 0xFFFFFFFF
        p = 12
        while p + 4 <= len(data):
            (off,) = struct.unpack_from("<I", data, p)
            off ^= key
            p += 4
            if off == 0:
                break
            (size,) = struct.unpack_from("<I", data, p)
            size ^= key
            p += 4
            (ekey,) = struct.unpack_from("<I", data, p)
            ekey ^= key
            p += 4
            (name_len,) = struct.unpack_from("<I", data, p)
            name_len ^= key
            p += 4
            if name_len > len(data) - p:
                raise ParseError(f"条目名长度异常：{name_len}", name=name)
            name_raw = bytearray(data[p:p + name_len])
            p += name_len
            for i in range(name_len):
                name_raw[i] ^= (key >> ((i % 4) * 8)) & 0xFF
            if size > len(data) - off:
                raise ParseError(f"条目数据越界：{size}", name=name)
            item = RGSSADItem(_decode_name(bytes(name_raw)),
                              bytes(name_raw), size, off, ekey)
            item.data = _decrypt_stream(data, off, size, ekey)
            items.append(item)
        return items
    raise ParseError("缺少 RGSSAD 魔数", name=name)


def rebuild_rgssad(items: list[RGSSADItem], version: int,
                   seed: int = 0) -> bytes:
    """重建档案（条目顺序保持）。未修改条目逐字节复现原密文。"""
    out = bytearray()
    if version == 1:
        out += RGSSAD_V1_MAGIC
        key = RGSSAD_V1_KEY
        for item in items:
            name = item.name_raw
            out += struct.pack("<I", len(name) ^ key)
            key = _advance_key(key)
            for b in name:
                out.append(b ^ (key & 0xFF))
                key = _advance_key(key)
            data = item.data
            out += struct.pack("<I", len(data) ^ key)
            key = _advance_key(key)
            out += _encrypt_stream(data, key)
        return bytes(out)
    out += RGSSAD_V3_MAGIC
    out += struct.pack("<I", seed)
    key = (seed * 9 + 3) & 0xFFFFFFFF
    # 头区：每条目 4×u32 + 名字；结束标记；随后数据块依序排列
    headers = bytearray()
    offset = 12 + 4
    for item in items:
        offset += 16 + len(item.name_raw)
    for item in items:
        data = item.data
        headers += struct.pack("<I", offset ^ key)
        headers += struct.pack("<I", len(data) ^ key)
        headers += struct.pack("<I", item.key ^ key)
        headers += struct.pack("<I", len(item.name_raw) ^ key)
        name_enc = bytearray(item.name_raw)
        for i in range(len(item.name_raw)):
            name_enc[i] ^= (key >> ((i % 4) * 8)) & 0xFF
        headers += name_enc
        offset += len(data)
    headers += struct.pack("<I", 0 ^ key)
    out += headers
    for item in items:
        out += _encrypt_stream(item.data, item.key)
    return bytes(out)


# ---------------------------------------------------------------------------
# 文本提取
# ---------------------------------------------------------------------------

def _decode_text(raw: bytes, ext: str) -> str | None:
    """按数据文件类型解码字符串；非文本（二进制）返回 None。"""
    if raw == b"":
        return None
    if raw[0] == 0x78 and len(raw) > 1 and raw[1] in _ZLIB_HEADS:
        return None                    # zlib 脚本数据
    if ext in (".rxdata", ".rvdata"):
        for enc in ("gbk", "utf-8"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return None
    try:
        return raw.decode("utf-8")     # rvdata2：仅 UTF-8 文本
    except UnicodeDecodeError:
        return None


def _encode_text(text: str, ext: str) -> bytes:
    if ext in (".rxdata", ".rvdata"):
        return text.encode("gbk")
    return text.encode("utf-8")


def _skip_reason(text: str) -> str | None:
    reason = auto_skip(text)
    if reason is not None:
        return reason
    if len(text) > 4000:
        return "超长字符串"
    if _looks_like_code(text) or any(t in text for t in _RM_CODE_TOKENS):
        return "代码特征"
    if _looks_like_asset(text):
        return "文件名"
    if _is_ident(text):
        return "标识符"
    return None


def _sym_display(node) -> str:
    if isinstance(node, MSymbol):
        return node.name.decode("utf-8", "replace")
    if isinstance(node, MSymLink):
        return f"sym#{node.index}"
    if isinstance(node, MFixnum):
        return str(node.value)
    if isinstance(node, MString):
        return node.raw.decode("utf-8", "replace")[:20]
    return "?"


def _iter_strings(node, path: str, ext: str, out: list) -> None:
    """深度优先收集文本字符串（顺序稳定）。"""
    if isinstance(node, MString):
        text = _decode_text(node.raw, ext)
        if text is not None:
            out.append((path, node, text))
        return
    if isinstance(node, MArray):
        for i, item in enumerate(node.items):
            _iter_strings(item, f"{path}[{i}]", ext, out)
        return
    if isinstance(node, MHash):
        for k, v in node.pairs:
            _iter_strings(v, f"{path}{{{_sym_display(k)}}}", ext, out)
        return
    if isinstance(node, MObject):
        for s, v in node.ivars:
            sym = _sym_display(s)
            _iter_strings(v, f"{path}/{sym}", ext, out)
        return
    if isinstance(node, MStruct):
        for s, v in node.members:
            _iter_strings(v, f"{path}/{_sym_display(s)}", ext, out)
        return
    if isinstance(node, MUClass):
        _iter_strings(node.inner, path, ext, out)
        return


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------

class RGSSAdapter:
    """RPG Maker XP/VX/VX Ace 适配器（档案或散装 Marshal 数据）。"""

    resource_types = ()

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        try:
            return len(self._collect(data, filename)) > 0
        except ParseError:
            return False

    # -- 内部 ------------------------------------------------------------

    def _collect(self, data: bytes, filename: str) -> list:
        """返回 [(version, item_or_None, ext, strings)]。"""
        results = []
        if data[:8] in (RGSSAD_V1_MAGIC, RGSSAD_V3_MAGIC):
            version = 1 if data[7] == 1 else 3
            for item in parse_rgssad(data, name=filename):
                low = item.name.lower()
                if any(k in low for k in _SKIP_ENTRIES):
                    continue
                if not item.data.startswith(MARSHAL_MAGIC):
                    continue
                ext = "." + low.rsplit(".", 1)[-1] if "." in low else ""
                try:
                    root = parse_marshal(item.data, name=item.name)
                except ParseError:
                    continue
                strings: list = []
                _iter_strings(root, _base_name(item.name), ext, strings)
                if strings:
                    results.append((version, item, ext, strings))
            return results
        if data[:2] == MARSHAL_MAGIC:
            lower = filename.lower()
            if any(k in lower for k in _SKIP_ENTRIES):
                return []
            ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
            root = parse_marshal(data, name=filename)
            strings: list = []
            _iter_strings(root, _base_name(filename), ext, strings)
            return [(0, None, ext, strings)]
        raise ParseError("不是 RGSS 数据（缺少档案或 Marshal 魔数）",
                         name=filename)

    # -- 提取 ------------------------------------------------------------

    def extract(self, name: str, data: bytes) -> list[Entry]:
        entries: list[Entry] = []
        for _version, item, ext, strings in self._collect(data, name):
            entry_name = item.name if item is not None else name
            for mpos, (path, node, text) in enumerate(strings):
                reason = _skip_reason(text)
                context = {"kind": "rgss_string",
                           "file": name,
                           "entry": entry_name,
                           "location": path,
                           "mpos": mpos,
                           "encoding": "gbk" if ext in
                                       (".rxdata", ".rvdata") else "utf-8"}
                e = make_entry(f"RGSS/{name}/{entry_name}/{mpos}", "rgss",
                               0, entry_name, 0, context, text, reason)
                entries.append(e)
        return entries

    # -- 回写 ------------------------------------------------------------

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        by_key: dict[tuple, str] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                entry_name = e.context.get("entry")
                mpos = e.context.get("mpos")
                if entry_name is None or mpos is None:
                    raise ParseError(f"条目缺少 RGSS 上下文：{e.id}")
                by_key[(entry_name, int(mpos))] = e.translation
        if not by_key:
            return original

        if original[:8] in (RGSSAD_V1_MAGIC, RGSSAD_V3_MAGIC):
            version = 1 if original[7] == 1 else 3
            items = parse_rgssad(original)
            changed = False
            for item in items:
                if not any(en == item.name for en, _ in by_key):
                    continue
                if not item.data.startswith(MARSHAL_MAGIC):
                    continue
                ext = "." + item.name.lower().rsplit(".", 1)[-1] \
                    if "." in item.name else ".rvdata2"
                root = parse_marshal(item.data, name=item.name)
                strings: list = []
                _iter_strings(root, _base_name(item.name), ext, strings)
                file_changed = False
                for (en, mpos), t in by_key.items():
                    if en != item.name:
                        continue
                    if mpos >= len(strings):
                        raise ParseError(
                            f"{item.name} 字符串序号越界：{mpos}")
                    _path, node, text = strings[mpos]
                    if t == text:
                        continue
                    try:
                        node.raw = _encode_text(t, ext)
                    except UnicodeEncodeError as exc:
                        raise ParseError(
                            f"译文无法以 {ext} 编码写入：{exc}") from exc
                    file_changed = True
                if file_changed:
                    item.data = serialize_marshal(root)
                    changed = True
            if not changed:
                return original
            if version == 1:
                return rebuild_rgssad(items, 1)
            seed = struct.unpack_from("<I", original, 8)[0]
            return rebuild_rgssad(items, 3, seed=seed)

        # 散装 Marshal 数据
        entry_name = str(entries[0].context.get("entry", ""))
        lower = entry_name.lower()
        ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ".rvdata2"
        root = parse_marshal(original)
        strings: list = []
        _iter_strings(root, _base_name(entry_name), ext, strings)
        changed = False
        for (_en, mpos), t in by_key.items():
            if mpos >= len(strings):
                raise ParseError(f"字符串序号越界：{mpos}")
            _path, node, text = strings[mpos]
            if t == text:
                continue
            try:
                node.raw = _encode_text(t, ext)
            except UnicodeEncodeError as exc:
                raise ParseError(
                    f"译文无法以 {ext} 编码写入：{exc}") from exc
            changed = True
        if not changed:
            return original
        return serialize_marshal(root)


def _base_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]
