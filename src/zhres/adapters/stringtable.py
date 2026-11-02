"""adapters.stringtable —— RT_STRING(6) 字符串表。

每个 block 固定 16 条；每条 = ``WORD char_count + UTF-16LE 字符``（无 NUL
结尾）；空条目 char_count=0。resource_name 即 block 序号，block 内
position 0-15。长度无硬限制。

回写：整 block 重序列化（即使只改一条）；条目数与原 block 保持一致
（真实世界的 block 恒为 16；若遇到非常规 block，为满足字节级
round-trip，按原条目数重建，见 docs/decisions.md）。
"""

from __future__ import annotations

import struct

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry, entry_position

BLOCK_SIZE = 16


def parse_block(data: bytes, *, name=None, lang: int | None = None) -> list[str]:
    """解析一个字符串表 block，返回按位置排列的字符串列表。

    条目数以数据实际内容为准（标准为 16）；多余字节视为格式错误。
    """
    strings: list[str] = []
    offset = 0
    total = len(data)
    while offset < total:
        if offset + 2 > total:
            raise ParseError(
                f"字符串表 block 残留 {total - offset} 字节，不足一个 char_count",
                type_id=6, name=name, lang=lang, offset=offset)
        (char_count,) = struct.unpack_from("<H", data, offset)
        offset += 2
        end = offset + char_count * 2
        if end > total:
            raise ParseError(
                f"字符串表条目越界（char_count={char_count}，超出 {end - total} 字节）",
                type_id=6, name=name, lang=lang, offset=offset)
        raw = data[offset:end]
        offset = end
        strings.append(raw.decode("utf-16-le"))
    return strings


def serialize_block(strings: list[str]) -> bytes:
    """把字符串列表序列化为一个 block。

    char_count 以 UTF-16 **码元**为单位（非 Python 字符数——代理对
    字符如 emoji 占 2 个码元），与 Windows 语义一致。
    单条长度不能超过 65535 码元。
    """
    out = bytearray()
    for s in strings:
        encoded = s.encode("utf-16-le")
        units = len(encoded) // 2
        if units > 0xFFFF:
            raise ParseError(f"字符串过长（{units} 码元，上限 65535）")
        out += struct.pack("<H", units)
        out += encoded
    return bytes(out)


class StringTableAdapter:
    """RT_STRING 适配器。"""

    resource_types = (6,)

    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool:
        return type_id == 6 and len(data) % 2 == 0

    def entry_id(self, name, lang: int, position: int) -> str:
        return f"RT_STRING/{name}/{lang}/{position}"

    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]:
        strings = parse_block(data, name=name, lang=lang)
        entries: list[Entry] = []
        for pos, text in enumerate(strings):
            reason = auto_skip(text)
            if reason == "空字符串":
                # 结构性空槽位不产生条目（避免工程文件被空串淹没）
                continue
            context = {
                "kind": "string_table",
                "location": f"block {name} / pos {pos}",
            }
            entry = make_entry(
                self.entry_id(name, lang, pos), "string", type_id,
                name, lang, context, text, reason)
            entries.append(entry)
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        """把已翻译条目写回 block；未修改时原样返回。"""
        strings = parse_block(original)
        changed = False
        for e in entries:
            if e.status not in WRITABLE_STATUSES or e.translation is None:
                continue
            try:
                pos = int(entry_position(e.id))
            except ValueError as exc:
                raise ParseError(
                    f"非法字符串表条目 id：{e.id}", type_id=6) from exc
            if not 0 <= pos < BLOCK_SIZE:
                raise ParseError(
                    f"字符串表 position 越界：{pos}", type_id=6)
            while len(strings) <= pos:
                strings.append("")  # 缺失条目按空串补齐
            if strings[pos] != e.translation:
                strings[pos] = e.translation
                changed = True
        if not changed:
            return original
        return serialize_block(strings)
