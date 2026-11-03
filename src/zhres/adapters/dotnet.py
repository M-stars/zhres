"""adapters.dotnet —— .NET 程序集托管资源适配器（WinForms 等）。

格式依据 ECMA-335（COR20 头 / 托管资源 blob）与 dotnet/runtime 官方
ResourceReader/ResourceWriter 源码，并经 csc 生成样本 + 系统真实
程序集逐字节验证（docs/formats/DOTNET.md）：

- DataDirectory[14] → COR20 头（72 字节），其中 Resources RVA/Size
  指向托管资源 blob；blob = ``[DWORD size][data][8 字节对齐]`` 序列，
  名字在 ManifestResource 元数据表（本版用条目序号做 id）
- 条目内容为 ``.resources``（ResourceWriter 二进制，魔数 0xBEEFCACE）：
  头（magic/version/skip/两个 7-bit 长度 UTF-8 类型名）+ userState
  （version/numResources/numTypes/类型表/【8 字节对齐】hash 表/
  namePos 表/dataSectionOffset/名字区/数据区）
- 名字区每项：``[7bit 字节长][UTF-16LE 名][int32 dataPos]``；
  数据区按 dataPos 寻址：``[7bit typeCode][值]``；String 值 =
  ``[7bit 字节长][UTF-8]``；ByteArray/Stream 前缀 int32 长度；
  用户类型（typeCode ≥ 0x40）无长度前缀——按 dataPos 与相邻记录
  边界切片原样保留
- WPF ``.g.resources``（BAML）：键名以 .baml 结尾的识别为 WPF，
  原样保留并警告（BAML 修改风险高，暂不支持）

回写：重建 .resources（String 值可变长）→ 重建 blob → PE 手术：
新 blob ≤ 原 blob 时原位写（余量清零 + 改 COR20 Resources Size）；
更大时追加新节（.zres）并改指（NumberOfSections/SizeOfImage/RVA）。
强名签名 / Authenticode 程序集修改后验签失败——警告但不绕过。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ParseError, ZhresError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry

RESOURCES_MAGIC = 0xBEEFCACE

RC_STRING = 1
RC_BYTEARRAY = 0x20
RC_STREAM = 0x21
RC_USER_START = 0x40

_PRIMITIVE_SIZES = {
    2: 1, 3: 2, 4: 1, 5: 1, 6: 2, 7: 2, 8: 4, 9: 4,
    10: 8, 11: 8, 12: 4, 13: 8, 14: 16, 15: 8, 16: 8,
}


def _read7(data: bytes, off: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if off >= len(data):
            raise ParseError("7-bit 编码越界")
        b = data[off]
        off += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, off
        shift += 7
        if shift > 35:
            raise ParseError("7-bit 编码过长")


def _write7(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


# ---------------------------------------------------------------------------
# PE / CLR
# ---------------------------------------------------------------------------

class PeView:
    """PE 只读视图。"""

    def __init__(self, data: bytes) -> None:
        if data[:2] != b"MZ":
            raise ParseError("缺少 MZ 签名")
        e = struct.unpack_from("<I", data, 0x3C)[0]
        if data[e:e + 4] != b"PE\x00\x00":
            raise ParseError("缺少 PE 签名")
        self.e = e
        self.data = data
        self.nsec = struct.unpack_from("<H", data, e + 6)[0]
        self.opt_size = struct.unpack_from("<H", data, e + 20)[0]
        self.opt = e + 24
        magic = struct.unpack_from("<H", data, self.opt)[0]
        if magic not in (0x10B, 0x20B):
            raise ParseError(f"未知的 OptionalHeader 魔数 {magic:#x}")
        self.pe32plus = magic == 0x20B
        self.dd = self.opt + (112 if self.pe32plus else 96)
        self.sec_table = self.opt + self.opt_size

    def dir(self, index: int) -> tuple[int, int]:
        return struct.unpack_from("<II", self.data, self.dd + index * 8)

    def rva2off(self, rva: int) -> int | None:
        for i in range(self.nsec):
            o = self.sec_table + i * 40
            _n, vs, va, rs, rp = struct.unpack_from("<8sIIII", self.data, o)
            if va <= rva < va + max(vs, rs):
                return rp + (rva - va)
        return None

    def sections(self) -> list[tuple[str, int, int, int, int]]:
        out = []
        for i in range(self.nsec):
            o = self.sec_table + i * 40
            name, vs, va, rs, rp = struct.unpack_from("<8sIIII", self.data, o)
            out.append((name.rstrip(b"\x00").decode("latin-1"), vs, va, rs, rp))
        return out


def read_clr_resources(data: bytes) -> tuple[int, int, int, int] | None:
    """读 COR20 头。返回 (coff, res_size, flags, res_rva)；非 .NET → None。"""
    view = PeView(data)
    rva, size = view.dir(14)
    if not rva or size < 72:
        return None
    coff = view.rva2off(rva)
    if coff is None:
        return None
    cb, _maj, _mi, _md_rva, _md_size, flags, _ep, res_rva, res_size = \
        struct.unpack_from("<IHHIIIIII", data, coff)
    if cb < 72:
        return None
    return coff, res_size, flags, res_rva


# ---------------------------------------------------------------------------
# 托管资源 blob
# ---------------------------------------------------------------------------

@dataclass
class DotnetEntry:
    """托管资源 blob 内一个条目。"""

    index: int
    data: bytes
    is_resources: bool
    resources: "ResourcesFile | None" = None
    baml_keys: list[str] = field(default_factory=list)


def parse_managed_blob(blob: bytes, *, name=None) -> list[DotnetEntry]:
    entries: list[DotnetEntry] = []
    pos = 0
    index = 0
    while pos + 4 <= len(blob):
        (size,) = struct.unpack_from("<I", blob, pos)
        if size == 0 or pos + 4 + size > len(blob):
            break
        data = blob[pos + 4:pos + 4 + size]
        entry = DotnetEntry(index=index, data=data,
                            is_resources=data[:4] == struct.pack("<I", RESOURCES_MAGIC))
        if entry.is_resources:
            try:
                entry.resources = parse_resources(data)
            except ParseError:
                pass
        entries.append(entry)
        index += 1
        pos += 4 + size
        pos = (pos + 7) & ~7
    if not entries:
        raise ParseError("托管资源 blob 为空或无法解析", name=name)
    return entries


def rebuild_managed_blob(entries: list[DotnetEntry]) -> bytes:
    out = bytearray()
    for entry in entries:
        out += struct.pack("<I", len(entry.data))
        out += entry.data
        while len(out) % 8:
            out += b"\x00"
    # 末尾对齐不算入任何条目
    return bytes(out)


# ---------------------------------------------------------------------------
# .resources（ResourceWriter 格式）
# ---------------------------------------------------------------------------

@dataclass
class ResourceRecord:
    name: str
    data_pos: int
    type_code: int
    raw: bytes = b""           # 完整记录字节（7bit typeCode + 载荷）
    value: str | None = None   # 仅 String

    @property
    def is_string(self) -> bool:
        return self.type_code == RC_STRING


@dataclass
class ResourcesFile:
    content: bytes
    version: int = 1
    num_resources: int = 0
    layout: dict = field(default_factory=dict)   # 解析位置记录
    records: list[ResourceRecord] = field(default_factory=list)
    baml: bool = False


def parse_resources(content: bytes, *, name=None) -> ResourcesFile:
    if struct.unpack_from("<I", content, 0)[0] != RESOURCES_MAGIC:
        raise ParseError("缺少 .resources 魔数 0xBEEFCACE", name=name)
    rf = ResourcesFile(content=content)
    header_version = struct.unpack_from("<i", content, 4)[0]
    skip = struct.unpack_from("<i", content, 8)[0]
    p = 12
    if header_version > 1:
        p = 12 + skip
    else:
        for _ in range(2):
            l, p = _read7(content, p)
            p += l
    rf.version = struct.unpack_from("<i", content, p)[0]
    num_res = struct.unpack_from("<i", content, p + 4)[0]
    num_types = struct.unpack_from("<i", content, p + 8)[0]
    q = p + 12
    for _ in range(num_types):
        l, q = _read7(content, q)
        q += l
    q = (q + 7) & ~7                       # hash 表 8 字节对齐
    if q + num_res * 8 + 4 > len(content):
        raise ParseError(".resources 名称表越界", name=name)
    hashes_off = q
    namepos_off = q + 4 * num_res
    data_sec_off_field = namepos_off + 4 * num_res
    data_sec = struct.unpack_from("<i", content, data_sec_off_field)[0]
    names_start = data_sec_off_field + 4
    if data_sec < names_start or data_sec > len(content):
        raise ParseError(".resources 数据区偏移异常", name=name)
    rf.layout = {"head_end": p, "types_off": p + 12, "types_end": q,
                 "hashes_off": hashes_off, "namepos_off": namepos_off,
                 "data_sec_field": data_sec_off_field,
                 "names_start": names_start, "data_sec": data_sec,
                 "num_res": num_res, "num_types": num_types}

    # 名字区顺序遍历
    records: list[ResourceRecord] = []
    m = names_start
    for _ in range(num_res):
        ln, m2 = _read7(content, m)
        nm = content[m2:m2 + ln].decode("utf-16-le", "replace")
        m2 += ln
        (dp,) = struct.unpack_from("<i", content, m2)
        m2 += 4
        base = data_sec + dp
        type_code, off = _read7(content, base)
        if type_code == RC_STRING:
            ln2, off2 = _read7(content, off)
            value = content[off2:off2 + ln2].decode("utf-8", "replace")
            records.append(ResourceRecord(
                name=nm, data_pos=dp, type_code=type_code,
                raw=content[base:off2 + ln2], value=value))
        elif type_code in (RC_BYTEARRAY, RC_STREAM):
            (ln2,) = struct.unpack_from("<i", content, off)
            end = off + 4 + ln2
            records.append(ResourceRecord(
                name=nm, data_pos=dp, type_code=type_code,
                raw=content[base:end]))
        elif type_code in _PRIMITIVE_SIZES:
            end = off + _PRIMITIVE_SIZES[type_code]
            records.append(ResourceRecord(
                name=nm, data_pos=dp, type_code=type_code,
                raw=content[base:end]))
        else:
            # 用户类型等：长度未知，此处仅记录（重建走 dataPos 切片）
            records.append(ResourceRecord(
                name=nm, data_pos=dp, type_code=type_code, raw=b""))
        m = m2
    rf.records = records

    # WPF 检测：键名以 .baml 结尾
    rf.baml = any(r.name.lower().endswith(".baml") for r in records)
    return rf


def rebuild_resources(rf: ResourcesFile,
                      new_strings: dict[str, str]) -> bytes:
    """重建 .resources：替换 String 值（可变长），其余记录原样搬运。

    用户类型记录通过 dataPos 排序切片获得完整字节；切片失败时抛
    ParseError（调用方放弃重建，原文件保持）。
    """
    content = rf.content
    lay = rf.layout
    data_sec = lay["data_sec"]

    # 1) 每个记录的原始完整字节：先读出 String/已知类型；用户类型用
    #    dataPos 排序切片推边界（记录在数据区内按发射序连续存放）。
    ordered = sorted(rf.records, key=lambda r: r.data_pos)
    bounds = [data_sec + r.data_pos for r in ordered] + [len(content)]
    raw_by_pos: dict[int, bytes] = {}
    for i, r in enumerate(ordered):
        start = bounds[i]
        stop = bounds[i + 1]
        if start >= stop:
            raise ParseError("数据区切片异常（dataPos 非递增）")
        if r.type_code == RC_STRING:
            encoded = (new_strings.get(r.name, r.value or "")).encode("utf-8")
            raw_by_pos[r.data_pos] = (_write7(RC_STRING)
                                      + _write7(len(encoded)) + encoded)
        else:
            raw_by_pos[r.data_pos] = content[start:stop]

    # 2) 重建数据区与名字区（按名字序发射，重算 dataPos）
    new_data = bytearray()
    new_pos: list[int] = []
    for r in rf.records:
        new_pos.append(len(new_data))
        new_data += raw_by_pos[r.data_pos]

    new_names = bytearray()
    for i, r in enumerate(rf.records):
        nm = r.name.encode("utf-16-le")
        new_names += _write7(len(nm))
        new_names += nm
        new_names += struct.pack("<i", new_pos[i])

    # 3) 组装：头+类型表+hash 表+namePos 表（原样，到 dataSectionOffset
    #    字段之前）→ 新 dataSectionOffset → 名字区 → 数据区
    cut = lay["data_sec_field"]            # dataSectionOffset 字段本身
    out = bytearray(content[:cut])
    new_data_sec = cut + 4 + len(new_names)
    out += struct.pack("<i", new_data_sec)
    out += new_names
    out += new_data
    return bytes(out)


# ---------------------------------------------------------------------------
# blob 级 apply / extract
# ---------------------------------------------------------------------------

def extract_entries(entries: list[DotnetEntry], *, name) -> list[Entry]:
    """把托管资源条目转成工程条目（id: DOTNET/<entryIdx>/<key>）。"""
    out: list[Entry] = []
    for entry in entries:
        if not entry.resources or entry.resources.baml:
            continue
        for r in entry.resources.records:
            if not r.is_string:
                continue
            reason = auto_skip(r.value or "")
            context = {"kind": "dotnet_string",
                       "location": f"entry#{entry.index} / {r.name}"}
            e = make_entry(f"DOTNET/{entry.index}/{r.name}", "dotnet",
                           0, entry.index, 0, context, r.value or "", reason)
            out.append(e)
    return out


def apply_entries(entries: list[Entry], original_blob: bytes) -> bytes:
    """把工程译文写回托管资源 blob。返回新 blob；无变化时返回原 blob。"""
    try:
        parsed = parse_managed_blob(original_blob)
    except ParseError:
        return original_blob
    strings_by_entry: dict[int, dict[str, str]] = {}
    touched = False
    for e in entries:
        if e.status not in WRITABLE_STATUSES or e.translation is None:
            continue
        try:
            _, idx_s, key = e.id.split("/", 2)
            idx = int(idx_s)
        except (ValueError, IndexError) as exc:
            raise ParseError(f"非法 .NET 条目 id：{e.id}") from exc
        strings_by_entry.setdefault(idx, {})[key] = e.translation
        touched = True
    if not touched:
        return original_blob
    changed = False
    for entry in parsed:
        if not entry.resources:
            continue
        new_map = strings_by_entry.get(entry.index)
        if not new_map:
            continue
        try:
            entry.data = rebuild_resources(entry.resources, new_map)
            changed = True
        except ParseError:
            continue   # 切片失败的条目放弃重建（原样保留）
    if not changed:
        return original_blob
    return rebuild_managed_blob(parsed)


# ---------------------------------------------------------------------------
# PE 手术
# ---------------------------------------------------------------------------

def apply_surgery(path: str | Path, new_blob: bytes,
                  warnings: list[str]) -> None:
    """把重建后的托管资源 blob 写回 PE（原位或追加新节）。"""
    p = Path(path)
    data = bytearray(p.read_bytes())
    view = PeView(bytes(data))
    info = read_clr_resources(bytes(data))
    if info is None:
        raise ParseError("目标不是 .NET 程序集")
    _coff, old_size, flags, old_rva = info
    if flags & 0x08 or view.dir(5)[1] > 0:
        warnings.append("该程序集带强名签名（StrongName），修改后签名验证"
                        "将失败；如运行时报强名错误，需自行重签或忽略验证。")
    if view.dir(4)[1] > 0:
        warnings.append("该文件带 Authenticode 数字签名，修改后签名失效。")

    off = view.rva2off(old_rva)
    if off is None:
        raise ParseError("托管资源 RVA 无法定位")
    # 优先：blob 原地扩展——新 blob 放进原节原始数据范围（旧 blob 起点
    # 到节 raw 末尾的余量）内时，不动节表，只改 Resources Size 字段。
    sec_raw_end = None
    for _n, _vs, va, rs, rp in view.sections():
        if rp <= off < rp + rs and rs > 0:
            sec_raw_end = rp + rs
            break
    clr_rva, _sz = view.dir(14)
    rva_field, size_field = _cor20_res_fields(view, clr_rva)
    if sec_raw_end is not None and off + len(new_blob) <= sec_raw_end:
        data[off:off + len(new_blob)] = new_blob
        struct.pack_into("<I", data, size_field, len(new_blob))
        _zero_checksum(data, view)
        p.write_bytes(bytes(data))
        return

    if len(new_blob) <= old_size:
        # 原位写 + 清零余量（罕见：blob 跨节等）
        data[off:off + old_size] = new_blob + b"\x00" * (old_size - len(new_blob))
        struct.pack_into("<I", data, size_field, len(new_blob))
        _zero_checksum(data, view)
        p.write_bytes(bytes(data))
        return

    # 变大且本节放不下：扩展最后一个节 / 追加新节
    _surgery_new_section(data, view, new_blob, warnings)
    _zero_checksum(data, view)
    p.write_bytes(bytes(data))


def _cor20_res_fields(view: PeView, clr_rva: int) -> tuple[int, int]:
    """COR20 头内 (Resources RVA 字段, Resources Size 字段) 的文件偏移。

    clr_rva 是 COR20 头本身的 RVA（DataDirectory[14]）。COR20 布局：
    cb(4) 版本(4) 元数据(8) flags(4) ep(4) | Resources RVA(4) Size(4)
    → RVA 字段 = coff+24，Size 字段 = coff+28。
    """
    coff = view.rva2off(clr_rva)
    return coff + 24, coff + 28


def _zero_checksum(data: bytearray, view: PeView) -> None:
    struct.pack_into("<I", data, view.opt + 64, 0)


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def _surgery_new_section(data: bytearray, view: PeView, new_blob: bytes,
                         warnings: list[str]) -> None:
    """blob 变大时的回写：优先扩展最后一个节（尾部无 overlay 时安全），
    否则尝试追加新节头（头部有余量时），都不行则拒绝。"""
    fa = max(struct.unpack_from("<I", data, view.opt + 36)[0], 512)
    sa = max(struct.unpack_from("<I", data, view.opt + 32)[0], 4096)
    sections = view.sections()
    last_name, last_vs, last_va, last_rs, last_rp = sections[-1]

    file_len = len(data)
    last_raw_end = last_rp + last_rs
    no_overlay = file_len <= _align(last_raw_end, fa) or file_len == last_raw_end

    if no_overlay and last_name not in ("", ".bss"):
        # 扩展最后一个节：把 blob 追加到其原始数据之后
        blob_off = _align(last_raw_end, fa)
        blob_rva = last_va + (blob_off - last_rp)
        new_rs = blob_off - last_rp + _align(len(new_blob), fa)
        sec_o = view.sec_table + (len(sections) - 1) * 40
        struct.pack_into("<I", data, sec_o + 8, new_rs)          # SizeOfRawData
        struct.pack_into("<I", data, sec_o + 4,
                         max(last_vs, blob_rva - last_va + len(new_blob)))
        size_of_image = struct.unpack_from("<I", data, view.opt + 56)[0]
        new_soi = _align(last_va + new_rs, sa)
        if new_soi > size_of_image:
            struct.pack_into("<I", data, view.opt + 56, new_soi)
        info = read_clr_resources(bytes(data))
        if info is None:
            raise ParseError("COR20 头丢失")
        _coff, _old, _fl, old_rva = info
        clr_rva, _sz = view.dir(14)
        rva_field, size_field = _cor20_res_fields(view, clr_rva)
        struct.pack_into("<I", data, rva_field, blob_rva)
        struct.pack_into("<I", data, size_field, len(new_blob))
        if len(data) < blob_off:
            data += b"\x00" * (blob_off - len(data))
        data += new_blob
        # 文件必须覆盖声明的新 SizeOfRawData（映射不越界）
        raw_end = last_rp + new_rs
        if len(data) < raw_end:
            data += b"\x00" * (raw_end - len(data))
        warnings.append(f"托管资源变大：已扩展节 {last_name}（RVA {blob_rva:#x}），"
                        "建议处理后完整测试目标程序。")
        return

    # 回退：头部有余量时追加新节头
    headers_size = struct.unpack_from("<I", data, view.opt + 60)[0]
    if view.sec_table + (view.nsec + 1) * 40 <= headers_size:
        max_end = max(va + vs for _n, vs, va, _rs, _rp in sections)
        last_end = max(rp + rs for _n, _vs, _va, rs, rp in sections)
        new_va = _align(max_end, sa)
        new_raw_off = _align(last_end, fa)
        new_raw_size = _align(len(new_blob), fa)
        header = struct.pack("<8sIIIIIIHHI", b".zres",
                             _align(len(new_blob), fa), new_va, new_raw_size,
                             new_raw_off, 0, 0, 0, 0, 0x40000040)
        pos = view.sec_table + view.nsec * 40
        data[pos:pos] = header
        struct.pack_into("<H", data, view.e + 6, view.nsec + 1)
        size_of_image = struct.unpack_from("<I", data, view.opt + 56)[0]
        new_soi = _align(new_va + _align(len(new_blob), sa), sa)
        if new_soi > size_of_image:
            struct.pack_into("<I", data, view.opt + 56, new_soi)
        info = read_clr_resources(bytes(data))
        if info is None:
            raise ParseError("COR20 头丢失")
        clr_rva, _sz = view.dir(14)
        rva_field, size_field = _cor20_res_fields(view, clr_rva)
        struct.pack_into("<I", data, rva_field, new_va)
        struct.pack_into("<I", data, size_field, len(new_blob))
        if len(data) < new_raw_off:
            data += b"\x00" * (new_raw_off - len(data))
        data += new_blob
        if len(data) < new_raw_off + new_raw_size:
            data += b"\x00" * (new_raw_off + new_raw_size - len(data))
        warnings.append(f"托管资源变大：已追加 PE 节 .zres（RVA {new_va:#x}），"
                        "建议处理后完整测试目标程序。")
        return

    raise ZhresError(
        "托管资源变大且 PE 无扩展空间（无空闲节头/尾部有 overlay），"
        "无法回写（文件保持原样）。")
