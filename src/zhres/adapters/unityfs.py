"""adapters.unityfs —— Unity 包裹文件（UnityFS .bundle/.assets bundle）适配器。

游戏把 .assets 装进 UnityFS 包裹（LZ4/LZMA 压缩块）。本适配器：

- 解析 UnityFS 头（全大端）：签名/格式版本/引擎版本/size/块信息
- 解压全部数据块：**LZ4/LZ4HC 用纯 Python 解压器**（零第三方依赖；
  项目铁律不允许引入 lz4 包），LZMA 用标准库 FORMAT_RAW（Unity 的
  5 字节裸 LZMA1 头）
- 提取：按节点（DirectoryInfo）逐个解析内部 SerializedFile
  （v15–22，复用 unity.py），提取 TextAsset
- 回写：**整体重建**——修改后的节点内容重新按 128 KiB 未压缩块
  分块，块信息不压缩紧跟头部。未压缩块是合法布局，读取方按块
  信息逐块拼接，与压缩与否无关
- **明确拒绝**（报错，不猜测）：
  - UnityCN 加密包裹（国产手游常见，UsesAssetBundleEncryption）
  - UnityWeb/UnityRaw 老签名与 UnityArchive（布局不同，无收益）
  - 块信息无法解压 / 节点不是 SerializedFile 时该节点原样保留

格式依据：UnityPy BundleFile.py/CompressionHelper.py 逐字段对照
（docs/formats/UNITYFS.md）。
"""

from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass, field

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry
from .unity import UnityFile, parse_unity

# 块信息 flags（新旧引擎一致）
AF_BLOCKS_AND_DIR_COMBINED = 0x40
AF_BLOCKS_INFO_AT_END = 0x80
AF_OLD_WEB_COMPAT = 0x100
# 新老引擎对 0x200 的解释不同（老=加密，新=padding）；0x400/0x1000 仅新引擎
AF_ENCRYPTION = 0x1400            # 覆盖新引擎 0x400/0x1000
AF_PADDING_AT_START = 0x200
AF_ENCRYPTION_OLD = 0x200

BLOCK_CHUNK = 0x20000             # 重建时分块大小（128 KiB）


# ---------------------------------------------------------------------------
# 纯 Python LZ4 块解压（LZ4 block format，LZ4HC 输出同构）
# ---------------------------------------------------------------------------

def decompress_lz4_block(data: bytes, uncompressed_size: int) -> bytes:
    """LZ4 块格式解压。实现依据 LZ4 Frame/Block 官方格式规范。"""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        token = data[i]
        i += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                if i >= n:
                    raise ParseError("LZ4 字面量长度越界")
                b = data[i]
                i += 1
                lit_len += b
                if b != 255:
                    break
        if i + lit_len > n:
            raise ParseError("LZ4 字面量越界")
        out += data[i:i + lit_len]
        i += lit_len
        if len(out) >= uncompressed_size:
            break
        if i + 2 > n:
            raise ParseError("LZ4 匹配偏移越界")
        (offset,) = struct.unpack_from("<H", data, i)
        i += 2
        if offset == 0 or offset > len(out):
            raise ParseError("LZ4 匹配偏移非法")
        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                if i >= n:
                    raise ParseError("LZ4 匹配长度越界")
                b = data[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        start = len(out) - offset
        for j in range(match_len):
            out.append(out[start + j])
    if len(out) != uncompressed_size:
        raise ParseError(
            f"LZ4 解压长度不符：{len(out)} != {uncompressed_size}")
    return bytes(out)


def _decompress_unity_lzma(data: bytes) -> bytes:
    """Unity 裸 LZMA1（5 字节 props+dict_size 头，无 size 字段）。"""
    if len(data) < 5:
        raise ParseError("LZMA 数据过短")
    props, dict_size = struct.unpack("<BI", data[:5])
    remainder, lc = divmod(props, 9)
    pb, lp = divmod(remainder, 5)
    dec = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                  "lc": lc, "lp": lp, "pb": pb}],
    )
    return dec.decompress(data[5:])


# ---------------------------------------------------------------------------
# 包裹解析
# ---------------------------------------------------------------------------

@dataclass
class BundleBlock:
    uncomp_size: int
    comp_size: int
    flags: int


@dataclass
class BundleNode:
    offset: int
    size: int
    flags: int
    path: str


@dataclass
class Bundle:
    data: bytes
    signature: str
    version: int
    version_player: str
    version_engine: str
    hash: bytes = b""
    blocks: list[BundleBlock] = field(default_factory=list)
    nodes: list[BundleNode] = field(default_factory=list)
    buffer: bytes = b""            # 全部块解压后的数据

    def uses_block_alignment(self) -> bool:
        """写回时头部后是否 16 字节对齐（与读取方规则一致）。"""
        if self.version >= 7:
            return True
        m = self.version_engine.split(".")
        try:
            if m and int(m[0]) == 2019:
                minor = int(m[1]) if len(m) > 1 else 0
                patch = int("".join(ch for ch in (m[2] if len(m) > 2 else "0")
                                    if ch.isdigit()) or 0)
                return (2019, 4, 15) <= (2019, minor, patch)
        except (ValueError, IndexError):
            pass
        return False


def parse_bundle(data: bytes, *, name=None) -> Bundle:
    """解析 UnityFS 包裹（签名 UnityFS）。"""
    try:
        signature, p = read_strz_pos(data, 0)
    except ValueError as exc:
        raise ParseError("不是 Unity 包裹文件", name=name) from exc
    if signature != "UnityFS":
        raise ParseError(
            f"不支持的包裹签名：{signature}（仅支持 UnityFS；"
            "UnityWeb/UnityRaw 老格式暂不支持）", name=name)
    if len(data) < 0x40:
        raise ParseError("包裹文件过短", name=name)
    (version,) = struct.unpack_from(">I", data, p)
    p += 4
    version_player, p = read_strz_pos(data, p)
    version_engine, p = read_strz_pos(data, p)
    bundle = Bundle(data=data, signature=signature, version=version,
                    version_player=version_player,
                    version_engine=version_engine)
    size, comp_size, uncomp_size, flags = struct.unpack_from(">qIII", data, p)
    p += 20
    engine_year = 0
    try:
        engine_year = int(version_engine.split(".")[0])
    except (ValueError, IndexError):
        engine_year = 0
    enc_flags = AF_ENCRYPTION_OLD if engine_year < 2020 else AF_ENCRYPTION
    if flags & enc_flags or flags & AF_OLD_WEB_COMPAT:
        raise ParseError(
            "该包裹带加密/兼容标志（UnityCN 加密常见于国产手游），暂不支持",
            name=name)

    # 读取块信息（0x80=在文件尾；压缩类型 = flags & 0x3F）
    if flags & AF_BLOCKS_INFO_AT_END:
        info_bytes = data[len(data) - comp_size:]
    else:
        info_bytes = data[p:p + comp_size]
    comp = flags & 0x3F
    if comp == 1:
        info_bytes = _decompress_unity_lzma(info_bytes)
    elif comp in (2, 3):
        info_bytes = decompress_lz4_block(info_bytes, uncomp_size)
    elif comp != 0:
        raise ParseError(f"未知块信息压缩类型：{comp}", name=name)
    if len(info_bytes) < 16 + 4:
        raise ParseError("块信息过短", name=name)
    bundle.hash = info_bytes[:16]
    (block_count,) = struct.unpack_from(">i", info_bytes, 16)
    q = 20
    if block_count < 0 or block_count > 1_000_000:
        raise ParseError(f"块数量异常：{block_count}", name=name)
    for _ in range(block_count):
        us, cs, bf = struct.unpack_from(">IIH", info_bytes, q)
        q += 10
        bundle.blocks.append(BundleBlock(us, cs, bf))

    (node_count,) = struct.unpack_from(">i", info_bytes, q)
    q += 4
    if node_count < 0 or node_count > 1_000_000:
        raise ParseError(f"节点数量异常：{node_count}", name=name)
    for _ in range(node_count):
        off, sz, nf = struct.unpack_from(">qqI", info_bytes, q)
        q += 20
        path, q = read_strz_pos(info_bytes, q)
        bundle.nodes.append(BundleNode(off, sz, nf, path))

    # 数据块起始：块信息在头部之后时紧跟块信息；在文件尾时紧跟头部
    if flags & AF_BLOCKS_INFO_AT_END:
        data_start = p
    else:
        data_start = p + comp_size
    if flags & AF_PADDING_AT_START and engine_year >= 2020:
        data_start = (data_start + 15) & ~15

    parts: list[bytes] = []
    pos = data_start
    for blk in bundle.blocks:
        raw = data[pos:pos + blk.comp_size]
        if len(raw) < blk.comp_size:
            raise ParseError("数据块越界", name=name)
        c = blk.flags & 0x3F
        if c == 0:
            parts.append(raw)
        elif c == 1:
            parts.append(_decompress_unity_lzma(raw))
        elif c in (2, 3):
            parts.append(decompress_lz4_block(raw, blk.uncomp_size))
        else:
            raise ParseError(f"未知数据块压缩类型：{c}", name=name)
        pos += blk.comp_size
    bundle.buffer = b"".join(parts)
    return bundle


def read_strz_pos(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


class UnityBundleAdapter:
    """UnityFS 包裹适配器（独立文件模式）。"""

    resource_types = ()

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        try:
            parse_bundle(data)
            return True
        except ParseError:
            return False

    def extract(self, name: str, data: bytes) -> list[Entry]:
        bundle = parse_bundle(data, name=name)
        entries: list[Entry] = []
        for node in bundle.nodes:
            content = bundle.buffer[node.offset:node.offset + node.size]
            try:
                uf = parse_unity(content, name=node.path)
            except ParseError:
                continue   # 非 SerializedFile 节点（如纯资源）原样保留
            for obj in uf.text_assets():
                text = obj.script or ""
                reason = auto_skip(text)
                context = {"kind": "unity_text_asset",
                           "location": f"{node.path} pathID {obj.path_id}",
                           "unity_name": obj.asset_name or "",
                           "node": node.path}
                e = make_entry(
                    f"UNITYFS/{node.path}/{obj.path_id}/{obj.asset_name}",
                    "unityfs", 0, node.path, 0, context, text, reason)
                entries.append(e)
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        bundle = parse_bundle(original)
        from ..project import WRITABLE_STATUSES as W
        by_node: dict[str, dict[int, str]] = {}
        for e in entries:
            if e.status in W and e.translation is not None:
                node = e.context.get("node")
                if not node:
                    raise ParseError(f"条目缺少 node 上下文：{e.id}")
                try:
                    pid = int(str(e.id).split("/")[2])
                except (ValueError, IndexError) as exc:
                    raise ParseError(f"非法 UnityFS 条目 id：{e.id}") from exc
                by_node.setdefault(node, {})[pid] = e.translation
        if not by_node:
            return original

        # 逐节点改写：原内容 + 文件尾追加对象（与 .assets 同思路）
        new_contents: list[bytes] = []
        changed = False
        for node in bundle.nodes:
            content = bytearray(
                bundle.buffer[node.offset:node.offset + node.size])
            pids = by_node.get(node.path)
            if pids:
                try:
                    uf = parse_unity(bytes(content), name=node.path)
                except ParseError as exc:
                    raise ParseError(
                        f"节点 {node.path} 回写时无法解析：{exc}") from exc
                content, node_changed = _patch_node(uf, content, pids)
                changed = changed or node_changed
            new_contents.append(bytes(content))
        if not changed:
            return original
        return _rebuild(bundle, new_contents)


def _patch_node(uf: UnityFile, content: bytearray,
                pids: dict[int, str]) -> tuple[bytearray, bool]:
    """在节点内容内追加 TextAsset 新数据并就地修补对象表。

    返回 (内容, 是否有改动)。"""
    original_len = len(content)
    changed = False
    for obj in uf.text_assets():
        t = pids.get(obj.path_id)
        if t is None:
            continue
        new_script = t.encode("utf-8")
        old_script = bytes(content[obj.script_off:
                                   obj.script_off + obj.script_len])
        if new_script == old_script:
            continue
        prefix = bytes(content[obj.byte_start:obj.script_off - 4])
        while len(content) % 4:
            content += b"\x00"
        new_start = len(content)
        content += prefix
        content += struct.pack("<i", len(new_script))
        content += new_script
        while len(content) % 4:
            content += b"\x00"
        if uf.version == 22:
            struct.pack_into("<q", content, obj.entry_off,
                             new_start - uf.data_offset)
        else:
            struct.pack_into("<I", content, obj.entry_off, new_start)
        struct.pack_into("<I", content, obj.entry_size_off,
                         len(content) - new_start)
        changed = True
    if not changed:
        return content, False
    # SerializedFile 头部 fileSize 同步（v22 大端 i64 @0x18；v15-21 u32 @4）
    if uf.version == 22:
        struct.pack_into(">q", content, 0x18, len(content))
    elif uf.file_size_value == original_len:
        struct.pack_into("<I", content, 4, len(content))
    return content, True


def _rebuild(bundle: Bundle, node_contents: list[bytes]) -> bytes:
    """按未压缩块整体重建 UnityFS（块信息不压缩、紧跟头部）。"""
    # 节点重排：按原顺序连续存放
    nodes: list[BundleNode] = []
    offset = 0
    for node, content in zip(bundle.nodes, node_contents):
        nodes.append(BundleNode(offset, len(content), 0, node.path))
        offset += len(content)
    data = b"".join(node_contents)

    # 数据分块（全部未压缩）
    blocks: list[BundleBlock] = []
    for i in range(0, len(data), BLOCK_CHUNK):
        chunk = data[i:i + BLOCK_CHUNK]
        blocks.append(BundleBlock(len(chunk), len(chunk), 0))

    # 块信息（大端）：hash + 块表 + 节点表
    info = bytearray(bundle.hash)
    info += struct.pack(">i", len(blocks))
    for blk in blocks:
        info += struct.pack(">IIH", blk.uncomp_size, blk.comp_size,
                            blk.flags)
    info += struct.pack(">i", len(nodes))
    for node in nodes:
        info += struct.pack(">qqI", node.offset, node.size, node.flags)
        info += node.path.encode("utf-8") + b"\x00"
    info_bytes = bytes(info)

    out = bytearray()
    out += bundle.signature.encode("utf-8") + b"\x00"
    out += struct.pack(">I", bundle.version)
    out += bundle.version_player.encode("utf-8") + b"\x00"
    out += bundle.version_engine.encode("utf-8") + b"\x00"
    header_size = len(out) + 20     # size(8) + comp(4) + uncomp(4) + flags(4)
    flags = AF_BLOCKS_AND_DIR_COMBINED    # 0x40：块信息+目录合并、不压缩
    out += struct.pack(">q", 0)     # size 占位，最后回填
    out += struct.pack(">III", len(info_bytes), len(info_bytes), flags)
    if bundle.uses_block_alignment():
        while len(out) % 16:
            out.append(0)
    out += info_bytes
    out += data
    struct.pack_into(">q", out, header_size - 20, len(out))
    return bytes(out)
