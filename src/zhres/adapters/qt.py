"""adapters.qt —— Qt 翻译文件（.qm）适配器。

格式依据 Qt6 官方源码（src/corelib/kernel/qtranslator.cpp）+ 本机
PySide6 真实 .qm 样本逐字节验证（docs/formats/QM.md）：

- 文件：16 字节魔数 + 若干块（``[BYTE tag][DWORD BE 长度][内容]``）：
  Language=0xa7、Contexts=0x2f、Hashes=0x42、Messages=0x69、
  NumerusRules=0x88、Dependencies=0x96；块序任意，未知块原样保留
- 消息块：消息序列，每条 = 记录序列 + ``Tag_End(1)``（无长度）；
  记录 = ``[BYTE tag][DWORD BE 长度][载荷]``：
  Translation=3（UTF-16**BE**，长度为字节数）、SourceText=6、
  Context=7、Comment=8（UTF-8）；旧格式 SourceText16=2/Context16=4、
  Obsolete1=5/Obsolete2=9 同构，一律原样保留
- 源文不变（Hashes/Contexts 块的查找键），只改 Translation 载荷

QM 是独立文件（非 PE 资源）：extract/writeback/verify 走"文件模式"，
整文件原子重写，无长度约束。
"""

from __future__ import annotations

import struct

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry

QM_MAGIC = bytes.fromhex("3cb86418caef9c95cd211cbf60a1bddd")

BLOCK_LANGUAGE = 0xA7
BLOCK_CONTEXTS = 0x2F
BLOCK_HASHES = 0x42
BLOCK_MESSAGES = 0x69
BLOCK_NUMERUS = 0x88
BLOCK_DEPENDENCIES = 0x96

TAG_END = 1
TAG_SOURCETEXT16 = 2
TAG_TRANSLATION = 3
TAG_CONTEXT16 = 4
TAG_OBSOLETE1 = 5
TAG_SOURCETEXT = 6
TAG_CONTEXT = 7
TAG_COMMENT = 8
TAG_OBSOLETE2 = 9

_TRANSLATION_TAGS = (TAG_TRANSLATION,)

# 已有译文条目的导出状态（复用 human 语义：非机器、可改）
STATUS_EXISTING = "human"


class QmMessage:
    """一条消息：原始记录序列 + 解码出的源文/译文。"""

    __slots__ = ("records", "source", "context", "comment", "translations")

    def __init__(self, records: list[tuple[int, bytes | None]]) -> None:
        self.records = records          # (tag, payload)；不含 Tag_End
        self.source = self._first_text(TAG_SOURCETEXT) or \
            self._first_text16(TAG_SOURCETEXT16)
        self.context = self._first_text(TAG_CONTEXT) or \
            self._first_text16(TAG_CONTEXT16)
        self.comment = self._first_text(TAG_COMMENT)
        self.translations = [
            p.decode("utf-16-be", "replace")
            for t, p in records if t in _TRANSLATION_TAGS and p is not None
        ]

    def _first_text(self, tag: int) -> str | None:
        for t, p in self.records:
            if t == tag and p is not None:
                return p.decode("utf-8", "replace").rstrip("\x00")
        return None

    def _first_text16(self, tag: int) -> str | None:
        for t, p in self.records:
            if t == tag and p is not None:
                return p.decode("utf-16-be", "replace").rstrip("\x00")
        return None

    def set_translations(self, values: list[str]) -> None:
        """替换全部 Translation 记录（数量可与原来不同）。"""
        out: list[tuple[int, bytes | None]] = []
        replaced = False
        for t, p in self.records:
            if t in _TRANSLATION_TAGS:
                if not replaced and values:
                    out.append((TAG_TRANSLATION,
                                values[0].encode("utf-16-be")))
                    replaced = True
                # 多余的旧译文记录丢弃（复数形式场景以首条为准）
                continue
            out.append((t, p))
        if not replaced and values:
            out.append((TAG_TRANSLATION, values[0].encode("utf-16-be")))
        self.records = out


class QmFile:
    """一个 .qm 文件：块列表 + 消息。"""

    def __init__(self) -> None:
        self.blocks: list[tuple[int, bytes]] = []   # 原样保留（消息块除外）
        self.messages: list[QmMessage] = []
        self.language: str | None = None


def parse_qm(data: bytes, *, name=None) -> QmFile:
    if data[:16] != QM_MAGIC:
        raise ParseError("缺少 QM 魔数（不是 .qm 翻译文件）", name=name)
    qm = QmFile()
    off = 16
    end = len(data)
    while off + 5 <= end:
        tag = data[off]
        (blen,) = struct.unpack_from(">I", data, off + 1)
        off += 5
        if tag == 0 or blen == 0:
            break
        if off + blen > end:
            raise ParseError(f"QM 块越界（tag={tag:#x}）", name=name,
                             offset=off)
        content = data[off:off + blen]
        off += blen
        if tag == BLOCK_LANGUAGE:
            qm.language = content.decode("utf-8", "replace").rstrip("\x00")
            qm.blocks.append((tag, content))
        elif tag == BLOCK_MESSAGES:
            qm.blocks.append((tag, b""))   # 占位，序列化时重建
            _parse_messages(qm, content, name=name)
        else:
            qm.blocks.append((tag, content))
    return qm


def _parse_messages(qm: QmFile, content: bytes, *, name=None) -> None:
    m = 0
    end = len(content)
    while m < end:
        records: list[tuple[int, bytes | None]] = []
        while m < end:
            tag = content[m]
            m += 1
            if tag == TAG_END:
                break
            if m + 4 > end:
                raise ParseError("QM 记录长度越界", name=name, offset=m)
            (ln,) = struct.unpack_from(">I", content, m)
            m += 4
            if m + ln > end:
                raise ParseError("QM 记录载荷越界", name=name, offset=m)
            records.append((tag, content[m:m + ln]))
            m += ln
        qm.messages.append(QmMessage(records))


def serialize_qm(qm: QmFile) -> bytes:
    out = bytearray(QM_MAGIC)
    for tag, content in qm.blocks:
        if tag == BLOCK_MESSAGES:
            msg = bytearray()
            for message in qm.messages:
                for t, p in message.records:
                    msg += struct.pack("B", t)
                    if t != TAG_END and p is not None:
                        msg += struct.pack(">I", len(p))
                        msg += p
                msg += struct.pack("B", TAG_END)
            out += struct.pack("B", BLOCK_MESSAGES)
            out += struct.pack(">I", len(msg))
            out += msg
        else:
            out += struct.pack("B", tag)
            out += struct.pack(">I", len(content))
            out += content
    return bytes(out)


def iter_source_texts(qm: QmFile):
    for i, message in enumerate(qm.messages):
        if message.source is not None:
            yield i, message


class QtAdapter:
    """QM 文件适配器（独立文件模式，type_id 约定为 0）。"""

    resource_types = ()   # 不参与 PE 资源分发

    def can_parse_file(self, data: bytes) -> bool:
        return data[:16] == QM_MAGIC

    def extract(self, name, data: bytes) -> list[Entry]:
        qm = parse_qm(data, name=name)
        entries: list[Entry] = []
        for i, message in enumerate(qm.messages):
            if message.source is None:
                continue
            reason = auto_skip(message.source)
            context = {"kind": "qt_message",
                       "location": f"{message.context or '-'} / "
                                   f"{message.source[:40]}",
                       "qt_context": message.context or "",
                       "qt_comment": message.comment or "",
                       # QM 的 source 记录不随翻译变化，verify 应对比译文
                       "verify_field": "translation"}
            e = make_entry(f"QM/{name}/{i}", "qt", 0, name, 0, context,
                           message.source, reason)
            target = message.translations[0] if message.translations else None
            if target is not None and e.status != "skipped":
                # 已有译文带出（human，便于改译）
                e.translation = target
                e.status = STATUS_EXISTING
            entries.append(e)
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        qm = parse_qm(original)
        by_index: dict[int, Entry] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                try:
                    by_index[int(str(e.id).rsplit("/", 1)[-1])] = e
                except ValueError as exc:
                    raise ParseError(f"非法 QM 条目 id：{e.id}") from exc
        changed = False
        for i, message in enumerate(qm.messages):
            entry = by_index.get(i)
            if entry is None:
                continue
            current = message.translations[0] if message.translations else ""
            if current != entry.translation:
                message.set_translations([entry.translation])
                changed = True
        if not changed:
            return original
        return serialize_qm(qm)


def is_qm_file(data: bytes) -> bool:
    return data[:16] == QM_MAGIC
