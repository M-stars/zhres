"""adapters.gamedata_win —— GameMaker 数据文件（data.win/game.unx 等）适配器。

GameMaker Studio 系（Undertale、Deltarune、无数 Indie 游戏）的全部
文本放在 FORM 容器的 **STRG 块**：字符串池 + 全游戏共享的绝对偏移
引用。布局（小端）：

- ``FORM`` + u32 总长，随后若干块：``[4CC][u32 长度][载荷]``
- STRG：u32 数量 + 数量×u32 指针（指向字符串条目）+ 0x80 对齐填充
- 字符串条目：``[u32 字节长][UTF-8 内容][0x00]``；其他块里的引用
  指向**内容**（= 长度字段地址 + 4，UndertaleModTool ReadUndertaleString
  的 ``addr - 4`` 语义）

回写策略：**原位缩短替换**。新译文 UTF-8 字节数 ≤ 原长时：内容
原位重写 + 补 NUL 清零残余 + 更新长度字段——所有外部引用与 STRG
指针表不动，字节安全。译文比原文长时**拒绝写入**（扩长会使后续
字符串整体位移、打断全文件引用；完整重写需要全部块的指针语义，
超出静态汉化的安全边界）。英文→中文通常变短，命中率良好。

限制（如实告知）：仅支持未压缩的 FORM 布局（GMS1/GMS2 标准发行），
带 YYC/加密壳的文件按解析失败原样保留；条目 max_len 记录原文
UTF-8 字节数上限。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry

STRG_ALIGN = 0x80


@dataclass
class GMChunk:
    name: str
    offset: int          # 载荷起始
    size: int


@dataclass
class GMString:
    index: int
    content: str
    length: int          # 原 UTF-8 字节数
    length_off: int      # 长度字段文件偏移
    content_off: int     # 内容起始文件偏移


@dataclass
class DatFile:
    data: bytes
    chunks: list[GMChunk] = field(default_factory=list)
    strings: list[GMString] | None = None
    strg_count_off: int = 0


def parse_dat(data: bytes, *, name=None) -> DatFile:
    """解析 FORM 容器与 STRG 字符串池。非 GameMaker 布局抛 ParseError。"""
    if len(data) < 12 or data[:4] != b"FORM":
        raise ParseError("缺少 FORM 魔数（不是 GameMaker 数据文件）",
                         name=name)
    (form_size,) = struct.unpack_from("<I", data, 4)
    # FORM 的 size 字段 = 魔数+长度字段之后的内容长度
    if form_size < 4 or 8 + form_size > len(data):
        raise ParseError(f"FORM 总长异常：{form_size}", name=name)
    df = DatFile(data=data)
    p = 8
    end = 8 + form_size
    while p + 8 <= end:
        cname = data[p:p + 4]
        (csize,) = struct.unpack_from("<I", data, p + 4)
        p += 8
        if p + csize > end:
            raise ParseError(f"块 {cname!r} 越界", name=name, offset=p)
        df.chunks.append(GMChunk(cname.decode("latin-1"), p, csize))
        p += csize
    names = {c.name for c in df.chunks}
    if "GEN8" not in names:
        raise ParseError("缺少 GEN8 块（不是 GameMaker 数据文件）", name=name)
    strg = next((c for c in df.chunks if c.name == "STRG"), None)
    if strg is None:
        raise ParseError("缺少 STRG 块（无字符串池）", name=name)

    q = strg.offset
    (count,) = struct.unpack_from("<I", data, q)
    q += 4
    if count > 10_000_000 or q + count * 4 > strg.offset + strg.size:
        raise ParseError(f"STRG 数量异常：{count}", name=name)
    df.strg_count_off = strg.offset
    strings: list[GMString] = []
    for i in range(count):
        (ptr,) = struct.unpack_from("<I", data, q)
        q += 4
        if ptr < 4 or ptr + 4 > len(data):
            raise ParseError(f"字符串指针越界：{ptr:#x}", name=name)
        length_off = ptr
        (length,) = struct.unpack_from("<I", data, length_off)
        content_off = length_off + 4
        if length > len(data) - content_off or \
                content_off + length >= len(data) or \
                data[content_off + length] != 0:
            raise ParseError(
                f"字符串条目损坏（长度 {length}）", name=name,
                offset=length_off)
        content = data[content_off:content_off + length].decode(
            "utf-8", "replace")
        strings.append(GMString(i, content, length, length_off, content_off))
    df.strings = strings
    return df


class GameMakerAdapter:
    """GameMaker data.win 适配器（独立文件模式）。"""

    resource_types = ()

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        try:
            return parse_dat(data, name=filename).strings is not None
        except ParseError:
            return False

    def extract(self, name: str, data: bytes) -> list[Entry]:
        df = parse_dat(data, name=name)
        entries: list[Entry] = []
        for s in df.strings or []:
            if s.length == 0:
                continue
            reason = auto_skip(s.content)
            context = {"kind": "gm_string",
                       "location": f"STRG[{s.index}]",
                       "gm_index": s.index}
            e = make_entry(f"DATWIN/{name}/{s.index}", "gamemaker", 0,
                           name, 0, context, s.content, reason)
            e.max_len = s.length     # 原位缩短回写的字节上限
            entries.append(e)
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        df = parse_dat(original)
        out = bytearray(original)
        changed = False
        for e in entries:
            if e.status not in WRITABLE_STATUSES or e.translation is None:
                continue
            try:
                idx = int(str(e.id).rsplit("/", 1)[-1])
            except ValueError as exc:
                raise ParseError(f"非法 GameMaker 条目 id：{e.id}") from exc
            if idx >= len(df.strings):
                raise ParseError(f"字符串序号越界：{idx}")
            s = df.strings[idx]
            new_raw = e.translation.encode("utf-8")
            old_raw = original[s.content_off:s.content_off + s.length]
            if new_raw == old_raw:
                continue
            if len(new_raw) > s.length:
                raise ParseError(
                    f"{e.id}：译文 UTF-8 {len(new_raw)} 字节超过原文 "
                    f"{s.length} 字节——GameMaker 原位回写不支持变长"
                    "（请缩短译文）")
            # 原位重写：新内容 + NUL 终止 + 清零残余；引用指针不动
            out[s.content_off:s.content_off + s.length] = \
                new_raw + b"\x00" * (s.length - len(new_raw))
            struct.pack_into("<I", out, s.length_off, len(new_raw))
            changed = True
        if not changed:
            return original
        return bytes(out)
