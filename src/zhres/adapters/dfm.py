"""adapters.dfm —— Delphi DFM（RT_RCDATA 中以 ``TPF0`` 开头的资源）。

binary 结构（经本机真实 Delphi/Lazarus/Inno 程序的 DFM 逐字节验证，
详见 docs/formats/DFM.md）：

- 签名 ``TPF0``；对象 = 类名 + 对象名（BYTE 长度 + ANSI 字节）
- 属性列表：``BYTE 名称长度``（0=结束）+ 名称 + ``BYTE 值类型`` + 值
- 子对象：``0x00`` 结束；普通子对象**无前缀**；``inherited/inline/带
  序号`` 的子对象有 ``$F0|flags`` 前缀（bit1=ffInherited、bit2=
  ffChildPos、bit4=ffInline），ffChildPos 时跟一个 ReadInteger
- 值类型（TypInfo.TValueType）：vaList=1（项=类型字节+值，0 结束）、
  vaInt8=2（有符号）、vaInt16=3、vaInt32=4、vaExtended=5、vaString=6、
  vaIdent=7、vaFalse=8、vaTrue=9、vaBinary=10、vaSet=11、vaLString=12、
  vaNil=13、vaCollection=14、vaSingle=15、vaCurrency=16、vaDate=17、
  vaWString=18、vaInt64=19、vaUTF8String=20、vaDouble=21
- TStrings 属性以带点名称（``Lines.Strings``）+ vaList 存储
- vaCollection 项：可选 vaInt8/16/32 顺序号 + 可选 vaList(1) 标记 +
  属性列表（0 结束）；两种标记的有无按原文件保留

Entry id 的 position 段为属性路径（如 ``Form1/Lines/3``、
``Form1/Button1/Caption``），满足任务书 §4.2 与跨 extract 稳定性。
"""

from __future__ import annotations

import struct

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry

RT_RCDATA = 10
TPF0 = b"TPF0"

VA_LIST = 1
VA_INT8 = 2
VA_INT16 = 3
VA_INT32 = 4
VA_EXTENDED = 5
VA_STRING = 6
VA_IDENT = 7
VA_FALSE = 8
VA_TRUE = 9
VA_BINARY = 10
VA_SET = 11
VA_LSTRING = 12
VA_NIL = 13
VA_COLLECTION = 14
VA_SINGLE = 15
VA_CURRENCY = 16
VA_DATE = 17
VA_WSTRING = 18
VA_INT64 = 19
VA_UTF8STRING = 20
VA_DOUBLE = 21

# 单值可翻译属性（白名单）；字符串列表属性为 .Strings 结尾的带点名称
TEXT_PROPERTIES = {"Caption", "Text", "Hint", "SimpleText", "Title"}


def _read_shortstr(data: bytes, pos: int, *, name, lang) -> tuple[bytes, int]:
    if pos >= len(data):
        raise ParseError("DFM 短串越界", type_id=RT_RCDATA, name=name,
                         lang=lang, offset=pos)
    n = data[pos]
    pos += 1
    if pos + n > len(data):
        raise ParseError("DFM 短串越界", type_id=RT_RCDATA, name=name,
                         lang=lang, offset=pos)
    return data[pos:pos + n], pos + n


def _read_dword_str(data: bytes, pos: int, *, name, lang) -> tuple[bytes, int]:
    if pos + 4 > len(data):
        raise ParseError("DFM 长串越界", type_id=RT_RCDATA, name=name,
                         lang=lang, offset=pos)
    (n,) = struct.unpack_from("<I", data, pos)
    pos += 4
    if pos + n > len(data):
        raise ParseError("DFM 长串越界", type_id=RT_RCDATA, name=name,
                         lang=lang, offset=pos)
    return data[pos:pos + n], pos + n


_INT_SIZES = {VA_INT8: 1, VA_INT16: 2, VA_INT32: 4}


class DfmValue:
    """属性值。

    kind: raw（非文本/容器，raw 逐字节保留）| text（可翻译文本）|
    list（vaList，children 为项）| collection（vaCollection）|
    collection_item（collection 内一项，raw = 原始项体不含终止 0）
    """

    __slots__ = ("vtype", "raw", "kind", "children")

    def __init__(self, vtype: int, raw: bytes = b"", kind: str = "raw",
                 children: list | None = None) -> None:
        self.vtype = vtype
        self.raw = raw
        self.kind = kind
        self.children = children


class DfmObject:
    """一个对象（根或子）。"""

    __slots__ = ("cls_name", "obj_name", "props", "children", "prefix")

    def __init__(self, cls_name: bytes, obj_name: bytes) -> None:
        self.cls_name = cls_name
        self.obj_name = obj_name
        self.props: list[tuple[str, DfmValue]] = []
        self.children: list[DfmObject] = []
        # 原始前缀形态：None=无前缀；(前缀字节, 顺序值原始字节或 None)
        self.prefix: tuple[int, bytes | None] | None = None

    def name(self) -> str:
        return (self.obj_name or self.cls_name).decode("latin-1", "replace")


class _Parser:
    def __init__(self, data: bytes, *, name, lang) -> None:
        self.data = data
        self.pos = 0
        self.name = name
        self.lang = lang

    def _err(self, msg: str, offset: int | None = None) -> ParseError:
        return ParseError(msg, type_id=RT_RCDATA, name=self.name,
                          lang=self.lang,
                          offset=self.pos if offset is None else offset)

    def parse(self) -> DfmObject:
        if self.data[:4] != TPF0:
            raise self._err("缺少 TPF0 签名")
        self.pos = 4
        root = self._read_object()
        if self.pos != len(self.data):
            raise self._err(f"DFM 有 {len(self.data) - self.pos} 字节残留")
        return root

    def _read_object(self) -> DfmObject:
        cls, self.pos = _read_shortstr(self.data, self.pos, name=self.name,
                                       lang=self.lang)
        obj, self.pos = _read_shortstr(self.data, self.pos, name=self.name,
                                       lang=self.lang)
        node = DfmObject(cls, obj)
        while True:  # 属性列表
            if self.pos >= len(self.data):
                raise self._err("属性列表未正常结束")
            n = self.data[self.pos]
            if n == 0:
                self.pos += 1
                break
            pname, self.pos = _read_shortstr(self.data, self.pos,
                                             name=self.name, lang=self.lang)
            if self.pos >= len(self.data):
                raise self._err("属性值类型越界")
            vtype = self.data[self.pos]
            self.pos += 1
            value = self._read_value(vtype)
            node.props.append((pname.decode("latin-1"), value))
        while True:  # 子对象列表
            if self.pos >= len(self.data):
                raise self._err("子对象列表未正常结束")
            b = self.data[self.pos]
            if b == 0:
                self.pos += 1
                break
            prefix: tuple[int, bytes | None] | None = None
            if b & 0xF0 == 0xF0:
                self.pos += 1
                order_raw = None
                if b & 0x02:  # ffChildPos：顺序整数的原始字节整体保留
                    start = self.pos
                    self._read_int_value()
                    order_raw = bytes(self.data[start:self.pos])
                prefix = (b, order_raw)
            child = self._read_object()
            child.prefix = prefix
            node.children.append(child)
        return node

    def _read_int_value(self) -> int:
        """读取 ReadInteger 形式的整数（类型字节 + 载荷），推进 self.pos。"""
        if self.pos >= len(self.data):
            raise self._err("DFM 整数越界")
        t = self.data[self.pos]
        self.pos += 1
        if t not in _INT_SIZES:
            raise self._err(f"DFM 非法整数类型 {t}")
        size = _INT_SIZES[t]
        if self.pos + size > len(self.data):
            raise self._err("DFM 整数载荷越界")
        fmt = {1: "<b", 2: "<h", 4: "<i"}[size]
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def _read_value(self, vtype: int) -> DfmValue:
        d, name, lang = self.data, self.name, self.lang
        if vtype == VA_LIST:
            items: list[DfmValue] = []
            while True:
                if self.pos >= len(d):
                    raise self._err("vaList 未正常结束")
                if d[self.pos] == 0:
                    self.pos += 1
                    break
                it = d[self.pos]
                self.pos += 1
                items.append(self._read_value(it))
            return DfmValue(VA_LIST, kind="list", children=items)
        if vtype in (VA_INT8, VA_INT16, VA_INT32):
            size = _INT_SIZES[vtype]
            if self.pos + size > len(d):
                raise self._err("DFM 整数越界")
            raw = d[self.pos:self.pos + size]
            self.pos += size
            return DfmValue(vtype, raw=raw)
        if vtype == VA_EXTENDED:
            if self.pos + 10 > len(d):
                raise self._err("DFM extended 越界")
            raw = d[self.pos:self.pos + 10]
            self.pos += 10
            return DfmValue(vtype, raw=raw)
        if vtype == VA_STRING:
            s, self.pos = _read_shortstr(d, self.pos, name=name, lang=lang)
            return DfmValue(vtype, raw=s, kind="text")
        if vtype == VA_IDENT:
            s, self.pos = _read_shortstr(d, self.pos, name=name, lang=lang)
            return DfmValue(vtype, raw=s)
        if vtype in (VA_FALSE, VA_TRUE, VA_NIL):
            return DfmValue(vtype)
        if vtype == VA_BINARY:
            s, self.pos = _read_dword_str(d, self.pos, name=name, lang=lang)
            return DfmValue(vtype, raw=s)
        if vtype == VA_SET:
            start = self.pos
            while True:
                s, self.pos = _read_shortstr(d, self.pos, name=name, lang=lang)
                if not s:
                    break  # 空串项：集合终止
            raw = d[start:self.pos]
            return DfmValue(vtype, raw=raw)
        if vtype == VA_LSTRING:
            s, self.pos = _read_dword_str(d, self.pos, name=name, lang=lang)
            return DfmValue(vtype, raw=s, kind="text")
        if vtype == VA_COLLECTION:
            items: list[DfmValue] = []
            while True:
                if self.pos >= len(d):
                    raise self._err("vaCollection 未正常结束")
                marker = d[self.pos]
                if marker == 0:
                    self.pos += 1
                    break
                order_raw = b""
                if marker in _INT_SIZES:
                    order_start = self.pos
                    self.pos += 1
                    self.pos += _INT_SIZES[marker]
                    order_raw = d[order_start:self.pos]
                    if self.pos >= len(d):
                        raise self._err("vaCollection 项越界")
                    marker = d[self.pos]
                list_flag = b""
                if marker == VA_LIST:
                    list_flag = b"\x01"
                    self.pos += 1
                body_start = self.pos
                self._read_prop_list()
                body = d[body_start:self.pos - 1]  # 不含终止 0
                items.append(DfmValue(
                    VA_COLLECTION, kind="collection_item",
                    raw=order_raw + list_flag + body))
            return DfmValue(VA_COLLECTION, kind="collection", children=items)
        if vtype == VA_SINGLE:
            if self.pos + 4 > len(d):
                raise self._err("DFM single 越界")
            raw = d[self.pos:self.pos + 4]
            self.pos += 4
            return DfmValue(vtype, raw=raw)
        if vtype in (VA_CURRENCY, VA_DATE, VA_INT64, VA_DOUBLE):
            if self.pos + 8 > len(d):
                raise self._err("DFM 8 字节值越界")
            raw = d[self.pos:self.pos + 8]
            self.pos += 8
            return DfmValue(vtype, raw=raw)
        if vtype in (VA_WSTRING, VA_UTF8STRING):
            s, self.pos = _read_dword_str(d, self.pos, name=name, lang=lang)
            return DfmValue(vtype, raw=s, kind="text")
        raise self._err(f"未知 DFM 值类型 {vtype}")

    def _read_prop_list(self) -> None:
        """读取一段属性列表（到 0 终止），值仅做结构跳过。"""
        while True:
            if self.pos >= len(self.data):
                raise self._err("属性列表未正常结束")
            n = self.data[self.pos]
            if n == 0:
                self.pos += 1
                return
            _, self.pos = _read_shortstr(self.data, self.pos, name=self.name,
                                         lang=self.lang)
            if self.pos >= len(self.data):
                raise self._err("属性值类型越界")
            vt = self.data[self.pos]
            self.pos += 1
            self._read_value(vt)


def parse_dfm(data: bytes, *, name=None, lang=None) -> DfmObject:
    """解析 binary DFM。"""
    return _Parser(data, name=name, lang=lang).parse()


def serialize_dfm(obj: DfmObject) -> bytes:
    """序列化回 binary。未修改的部分逐字节保持。"""
    out = bytearray(TPF0)
    _dump_object(out, obj)
    return bytes(out)


def _dump_object(out: bytearray, obj: DfmObject) -> None:
    out.append(len(obj.cls_name))
    out += obj.cls_name
    out.append(len(obj.obj_name))
    out += obj.obj_name
    for pname, value in obj.props:
        pb = pname.encode("latin-1")
        out.append(len(pb))
        out += pb
        out.append(value.vtype)
        _dump_value(out, value)
    out.append(0)
    for child in obj.children:
        if child.prefix is not None:
            prefix, order_raw = child.prefix
            out.append(prefix)
            if order_raw:
                out += order_raw
        _dump_object(out, child)
    out.append(0)


def _dump_value(out: bytearray, value: DfmValue) -> None:
    if value.kind == "list":
        for item in value.children:
            out.append(item.vtype)
            _dump_value(out, item)
        out.append(0)
    elif value.kind == "collection":
        for item in value.children:
            out += item.raw
            out.append(0)
        out.append(0)
    elif value.vtype in (VA_FALSE, VA_TRUE, VA_NIL):
        pass
    elif value.vtype in (VA_STRING, VA_IDENT):
        out.append(len(value.raw))
        out += value.raw
    elif value.vtype in (VA_LSTRING, VA_BINARY, VA_WSTRING, VA_UTF8STRING):
        out += struct.pack("<I", len(value.raw))
        out += value.raw
    else:
        out += value.raw


def decode_ansi(raw: bytes) -> str | None:
    """ANSI 字节按 ACP（简中环境即 GBK）解码；失败返回 None。"""
    try:
        return raw.decode("gbk")
    except (UnicodeDecodeError, LookupError):
        return None


def encode_ansi(text: str) -> bytes | None:
    try:
        return text.encode("gbk")
    except (UnicodeEncodeError, LookupError):
        return None


def decode_value_text(value: DfmValue) -> str | None:
    """按值类型解码文本；不可解码返回 None。"""
    if value.vtype == VA_WSTRING:
        return value.raw.decode("utf-16-le", "replace")
    if value.vtype == VA_UTF8STRING:
        return value.raw.decode("utf-8", "replace")
    if value.vtype in (VA_STRING, VA_LSTRING):
        return decode_ansi(value.raw)
    return None


def encode_value_text(value: DfmValue, text: str) -> bool:
    """把译文按原值类型编码；失败返回 False。"""
    try:
        if value.vtype == VA_WSTRING:
            value.raw = text.encode("utf-16-le")
        elif value.vtype == VA_UTF8STRING:
            value.raw = text.encode("utf-8")
        elif value.vtype in (VA_STRING, VA_LSTRING):
            encoded = encode_ansi(text)
            if encoded is None:
                return False
            value.raw = encoded
        else:
            return False
    except UnicodeEncodeError:
        return False
    return True


class DfmAdapter:
    """RCDATA 中 TPF0 DFM 的适配器。"""

    resource_types = (10,)

    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool:
        return type_id == RT_RCDATA and data[:4] == TPF0

    # -- 提取 ---------------------------------------------------------------

    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]:
        root = parse_dfm(data, name=name, lang=lang)
        entries: list[Entry] = []

        def add_entry(position: str, location: str, source: str,
                      prop: str, force_skip: str | None = None) -> None:
            context = {
                "kind": "dfm_property",
                "location": location,
                "dfm_property": prop,
            }
            reason = force_skip if force_skip else auto_skip(source)
            entries.append(make_entry(
                f"RT_RCDATA/{name}/{lang}/{position}", "dfm", type_id,
                name, lang, context, source, reason))

        def walk(obj: DfmObject, path: str) -> None:
            p = f"{path}/{obj.name()}" if path else obj.name()
            for pname, value in obj.props:
                if pname in TEXT_PROPERTIES and value.kind == "text":
                    text = decode_value_text(value)
                    if text is None:
                        # ANSI 解码失败：记录 skipped（任务书 §5.4）
                        add_entry(f"{p}/{pname}", f"{p} / {pname}", "",
                                  pname, "ANSI(GBK) 解码失败")
                    else:
                        add_entry(f"{p}/{pname}", f"{p} / {pname}", text,
                                  pname)
                elif pname.endswith(".Strings") and value.kind == "list":
                    base = pname[:-len(".Strings")]
                    for i, item in enumerate(value.children):
                        if item.kind != "text":
                            continue
                        text = decode_value_text(item)
                        if text is None:
                            add_entry(f"{p}/{base}/{i}",
                                      f"{p} / {base}/{i}", "", base,
                                      "ANSI(GBK) 解码失败")
                        else:
                            add_entry(f"{p}/{base}/{i}",
                                      f"{p} / {base}/{i}", text, base)
                elif pname in ("Lines", "Items") and value.kind == "list":
                    # 少数写入方用不带 .Strings 的裸列表名
                    for i, item in enumerate(value.children):
                        if item.kind != "text":
                            continue
                        text = decode_value_text(item)
                        if text is None:
                            add_entry(f"{p}/{pname}/{i}",
                                      f"{p} / {pname}/{i}", "", pname,
                                      "ANSI(GBK) 解码失败")
                        else:
                            add_entry(f"{p}/{pname}/{i}",
                                      f"{p} / {pname}/{i}", text, pname)
            for child in obj.children:
                walk(child, p)

        walk(root, "")
        return entries

    # -- 回写 ---------------------------------------------------------------

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        root = parse_dfm(original)
        by_pos: dict[str, Entry] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                by_pos[self._position_of(e.id)] = e

        changed = False

        def patch(obj: DfmObject, path: str) -> None:
            nonlocal changed
            p = f"{path}/{obj.name()}" if path else obj.name()
            for pname, value in obj.props:
                if pname in TEXT_PROPERTIES and value.kind == "text":
                    if decode_value_text(value) is None:
                        continue  # 与 extract 计数口径一致：不可解码不计
                    entry = by_pos.get(f"{p}/{pname}")
                    if entry is not None and encode_value_text(
                            value, entry.translation):
                        changed = True
                elif pname.endswith(".Strings") and value.kind == "list":
                    base = pname[:-len(".Strings")]
                    for i, item in enumerate(value.children):
                        if item.kind == "text" and \
                                decode_value_text(item) is not None:
                            entry = by_pos.get(f"{p}/{base}/{i}")
                            if entry is not None and encode_value_text(
                                    item, entry.translation):
                                changed = True
                elif pname in ("Lines", "Items") and value.kind == "list":
                    for i, item in enumerate(value.children):
                        if item.kind == "text" and \
                                decode_value_text(item) is not None:
                            entry = by_pos.get(f"{p}/{pname}/{i}")
                            if entry is not None and encode_value_text(
                                    item, entry.translation):
                                changed = True
            for child in obj.children:
                patch(child, p)

        patch(root, "")
        if not changed:
            return original
        return serialize_dfm(root)

    @staticmethod
    def _position_of(entry_id: str) -> str:
        """DFM 的 position 段含斜杠（属性路径），取第三段之后的全部。"""
        parts = entry_id.split("/")
        if len(parts) < 5:
            raise ParseError(f"非法 DFM 条目 id：{entry_id}",
                             type_id=RT_RCDATA)
        return "/".join(parts[3:])
