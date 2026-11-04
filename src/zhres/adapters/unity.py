"""adapters.unity —— Unity 序列化文件（.assets）TextAsset 适配器。

覆盖范围（v2）：
- **SerializedFile v15–21**（Unity 2017–2021，全小端头）：解析对象表，
  提取 **TextAsset**（class_id 49）对象——游戏常把本地化 JSON/CSV、
  剧情文本放在 TextAsset 里
- **SerializedFile v22**（Unity 2022+/团结引擎，大端头 + 小端体）：同上
- 回写：新数据**追加到文件尾**（4 字节对齐），就地修补对象表的
  byte_start/byte_size 字段（v22 另需更新头部 fileSize）——无需移动
  其他对象，老数据成为死区（与 .NET 手术同思路）
- **限制**（如实告知）：
  - enableTypeTree=1 的文件（编辑器构建）：类型树跳过需要完整
    typetree 解析，暂不支持（明确报错）
  - MonoBehaviour 内嵌字符串（如 TextMeshPro 的 m_text）需要类型树
    或 IL2CPP 信息，暂不支持
  - fileVersion < 15 或 23–25：明确报错；UnityFS 包裹文件见 unityfs.py

格式依据：UnityPy（社区标准实现）SerializedFile/ObjectReader 逐字段
对照 + docs/formats/UNITY.md 记录；v22 侧另有实测 Tuanjie
（团结引擎）2022.3.17 真实文件 + UnityPy 交叉验证。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..errors import ParseError
from ..project import Entry, WRITABLE_STATUSES
from .base import auto_skip, make_entry

UNITY_MIN_VERSION = 15
UNITY_V22_VERSION = 22
_CLASS_ID_TEXT_ASSET = 49


@dataclass
class UnityObject:
    path_id: int
    type_id: int
    class_id: int
    byte_start: int              # 绝对文件偏移（v15-21 直接绝对；v22 表值+data_offset）
    byte_size: int
    entry_off: int               # byte_start 字段在文件内的偏移（就地修补用）
    entry_size_off: int = 0      # byte_size 字段在文件内的偏移
    # TextAsset 解析结果（仅 class_id==49）
    asset_name: str | None = None
    script: str | None = None
    script_off: int = 0          # 脚本字节在文件内的偏移
    script_len: int = 0


@dataclass
class UnityFile:
    data: bytes
    version: int = 0
    raw_endian_le: bool = True
    metadata_size: int = 0
    file_size_field: int = 0     # 头部 fileSize 字段的文件偏移（v22 专用）
    file_size_value: int = 0
    data_offset: int = 0
    objects: list[UnityObject] = field(default_factory=list)

    def text_assets(self) -> list[UnityObject]:
        return [o for o in self.objects if o.asset_name is not None]


def _parse_text_asset(obj: UnityObject, data: bytes) -> None:
    """TextAsset 对象布局：[i32 名长][utf8 名][align4][i32 脚本长][utf8 脚本]。"""
    if not (0 <= obj.byte_start <= len(data)
            and obj.byte_start + obj.byte_size <= len(data)):
        return
    o = obj.byte_start
    (name_len,) = struct.unpack_from("<i", data, o)
    o += 4
    if not (0 <= name_len <= obj.byte_size):
        return
    obj.asset_name = data[o:o + name_len].decode("utf-8", "replace")
    o += name_len
    o = (o + 3) & ~3
    (script_len,) = struct.unpack_from("<i", data, o)
    o += 4
    if 0 <= script_len <= obj.byte_size:
        obj.script = data[o:o + script_len].decode("utf-8", "replace")
        obj.script_off = o
        obj.script_len = script_len


def _parse_v15_21(data: bytes, version: int, *, name=None) -> UnityFile:
    """v15–21：全小端。头 4×u32（metadataSize/fileSize/version/dataOffset）
    + u8 endian + 3 保留字节，随后 strz unityVersion 等。"""
    if len(data) < 0x20:
        raise ParseError("文件过短，不是 Unity .assets", name=name)
    uf = UnityFile(data=data, version=version)
    uf.metadata_size = struct.unpack_from("<I", data, 0)[0]
    uf.file_size_value = struct.unpack_from("<I", data, 4)[0]
    uf.data_offset = struct.unpack_from("<I", data, 12)[0]
    if data[16] != 0:
        raise ParseError("大端体的 v15-21 文件暂不支持", name=name)

    p = 0x14                      # u8 endian + 3 保留之后
    end = data.index(b"\x00", p)
    p = end + 1                   # unityVersion 字符串（含 NUL）
    if version >= 8:
        p += 4                    # targetPlatform
    enable_tree = data[p]
    p += 1
    (type_count,) = struct.unpack_from("<i", data, p)
    p += 4
    if type_count < 0 or type_count > 100_000:
        raise ParseError(f"类型表数量异常：{type_count}", name=name)
    if enable_tree:
        raise ParseError(
            "该文件带类型树（enableTypeTree=1，常见于编辑器构建或部分游戏），"
            "暂不支持解析——可尝试寻找发布版（stripped）资源文件",
            name=name)
    class_ids: list[int] = []
    for _ in range(type_count):
        (class_id,) = struct.unpack_from("<i", data, p)
        p += 4
        if version >= 16:
            p += 1                # isStrippedType u8
        if version >= 17:
            p += 2                # scriptTypeIndex i16
        # script_id（Hash128）：v<16 且 class_id<0，或 v>=16 且 class_id==114
        if (version < 16 and class_id < 0) or \
                (version >= 16 and class_id == 114):
            p += 16
        p += 16                   # oldTypeHash
        class_ids.append(class_id)

    (obj_count,) = struct.unpack_from("<i", data, p)
    p += 4
    if obj_count < 0 or obj_count > 1_000_000:
        raise ParseError(f"对象表数量异常：{obj_count}", name=name)
    for _ in range(obj_count):
        if p % 4:
            p = (p + 3) & ~3
        (path_id,) = struct.unpack_from("<q", data, p)
        p += 8
        (byte_start,) = struct.unpack_from("<I", data, p)
        entry_off = p             # byte_start 字段位置（就地修补用）
        p += 4
        (byte_size,) = struct.unpack_from("<I", data, p)
        p += 4
        (type_id,) = struct.unpack_from("<i", data, p)
        p += 4
        if 11 <= version < 17:
            p += 2                # scriptTypeIndex i16（对象表内）
        if version in (15, 16):
            p += 1                # isStripped u8（对象表内）
        obj = UnityObject(
            path_id=path_id,
            type_id=type_id,
            class_id=class_ids[type_id] if 0 <= type_id < len(class_ids) else -1,
            byte_start=byte_start + uf.data_offset,
            byte_size=byte_size,
            entry_off=entry_off,
            entry_size_off=entry_off + 4,
        )
        if obj.class_id == _CLASS_ID_TEXT_ASSET:
            _parse_text_asset(obj, data)
        uf.objects.append(obj)
    return uf


def _parse_v22(data: bytes, *, name=None) -> UnityFile:
    """v22（Unity 2022+/团结引擎）：大端头 + 小端体。"""
    uf = UnityFile(data=data, version=UNITY_V22_VERSION)
    uf.raw_endian_le = struct.unpack_from(">B", data, 0x10)[0] == 0
    if not uf.raw_endian_le:
        raise ParseError("大端体的 v22 文件暂不支持", name=name)
    uf.metadata_size = struct.unpack_from(">I", data, 0x14)[0]
    uf.file_size_field = 0x18
    uf.file_size_value = struct.unpack_from(">q", data, 0x18)[0]
    uf.data_offset = struct.unpack_from(">q", data, 0x20)[0]

    # 小端体：@0x30 strz revision → i32 targetPlatform → u8 enableTypeTree
    # → i32 typeCount → [类型表] → i32 objectCount → [对象表]
    p = 0x30
    end = data.index(b"\x00", p)
    p = end + 1                      # revision 字符串（含 NUL）
    p += 4                           # targetPlatform（值不影响文本提取）
    enable_tree = data[p]
    p += 1
    (type_count,) = struct.unpack_from("<i", data, p)
    p += 4
    if enable_tree:
        raise ParseError(
            "该文件带类型树（enableTypeTree=1，常见于编辑器构建或部分游戏），"
            "暂不支持解析——可尝试寻找发布版（stripped）资源文件",
            name=name)
    class_ids: list[int] = []
    for _ in range(type_count):
        (class_id,) = struct.unpack_from("<i", data, p)
        p += 4
        stripped = data[p]
        p += 1
        (script_idx,) = struct.unpack_from("<h", data, p)
        p += 2
        if class_id == 114:
            p += 16                  # script_id (Hash128)
        p += 16                      # old_type_hash
        class_ids.append(class_id)

    (obj_count,) = struct.unpack_from("<i", data, p)
    p += 4
    if obj_count < 0 or obj_count > 1_000_000:
        raise ParseError(f"对象表数量异常：{obj_count}", name=name)
    for _ in range(obj_count):
        if p % 4:
            p = (p + 3) & ~3
        (path_id,) = struct.unpack_from("<q", data, p)
        p += 8
        (rel_start,) = struct.unpack_from("<q", data, p)
        p += 8
        (byte_size,) = struct.unpack_from("<I", data, p)
        p += 4
        (type_id,) = struct.unpack_from("<i", data, p)
        p += 4
        obj = UnityObject(
            path_id=path_id,
            type_id=type_id,
            class_id=class_ids[type_id] if 0 <= type_id < len(class_ids) else -1,
            byte_start=uf.data_offset + rel_start,
            byte_size=byte_size,
            entry_off=p - 16,        # byte_start 字段位置（path_id 后）
            entry_size_off=p - 8,
        )
        if obj.class_id == _CLASS_ID_TEXT_ASSET:
            _parse_text_asset(obj, data)
        uf.objects.append(obj)
    return uf


def parse_unity(data: bytes, *, name=None) -> UnityFile:
    """解析 SerializedFile（v15–21 小端 / v22 大端头）。"""
    if len(data) < 0x20:
        raise ParseError("文件过短，不是 Unity .assets", name=name)
    # v22 的版本号在偏移 8 处为大端 i32；v15-21 为小端 u32
    if struct.unpack_from(">i", data, 8)[0] == UNITY_V22_VERSION:
        if data[:8] != b"\x00" * 8:
            raise ParseError("v22 头部校验失败（非标准 Unity 文件）", name=name)
        if len(data) < 0x48:
            raise ParseError("文件过短，不是 Unity .assets", name=name)
        return _parse_v22(data, name=name)
    version = struct.unpack_from("<I", data, 8)[0]
    if UNITY_MIN_VERSION <= version < UNITY_V22_VERSION:
        return _parse_v15_21(data, version, name=name)
    raise ParseError(
        f"不支持的 SerializedFile 版本：{version}"
        f"（支持 {UNITY_MIN_VERSION}–{UNITY_V22_VERSION}）", name=name)


class UnityAssetsAdapter:
    """Unity .assets（TextAsset）适配器（独立文件模式）。"""

    resource_types = ()   # 非 PE 资源树

    def can_parse_file(self, data: bytes) -> bool:
        try:
            parse_unity(data)
            return True
        except ParseError:
            return False

    def extract(self, name, data: bytes) -> list[Entry]:
        uf = parse_unity(data, name=name)
        entries: list[Entry] = []
        for obj in uf.text_assets():
            text = obj.script or ""
            reason = auto_skip(text)
            context = {"kind": "unity_text_asset",
                       "location": f"pathID {obj.path_id}",
                       "unity_name": obj.asset_name or ""}
            e = make_entry(f"UNITY/{obj.path_id}/{obj.asset_name}",
                           "unity", 0, obj.path_id, 0, context, text, reason)
            entries.append(e)
        return entries

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        uf = parse_unity(original)
        by_pid: dict[int, Entry] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                try:
                    pid = int(str(e.id).split("/")[1])
                except (ValueError, IndexError) as exc:
                    raise ParseError(f"非法 Unity 条目 id：{e.id}") from exc
                by_pid[pid] = e
        out = bytearray(original)
        changed = False
        for obj in uf.text_assets():
            entry = by_pid.get(obj.path_id)
            if entry is None:
                continue
            new_script = entry.translation.encode("utf-8")
            old_script = original[obj.script_off:
                                  obj.script_off + obj.script_len]
            if new_script == old_script:
                continue
            # 追加完整对象数据（脚本长度字段之前的前缀 + 新长度 + 新脚本
            # + 4 对齐尾）到文件尾，就地修补对象表字段；原尾部字节是
            # 对齐填充，按新长度重算
            prefix = original[obj.byte_start:obj.script_off - 4]
            while len(out) % 4:
                out += b"\x00"
            new_start = len(out)
            out += prefix
            out += struct.pack("<i", len(new_script))
            out += new_script
            while len(out) % 4:
                out += b"\x00"
            if uf.version == UNITY_V22_VERSION:
                # v22：byte_start 为相对 data_offset 的 i64
                struct.pack_into("<q", out, obj.entry_off,
                                 new_start - uf.data_offset)
            else:
                # v15-21：byte_start 为绝对偏移 u32（>4GB 的 .assets 不存在）
                struct.pack_into("<I", out, obj.entry_off, new_start)
            struct.pack_into("<I", out, obj.entry_size_off,
                             len(out) - new_start)
            changed = True
        if not changed:
            return original
        if uf.version == UNITY_V22_VERSION:
            # 头部 fileSize 字段更新（大端 i64 @0x18）
            struct.pack_into(">q", out, uf.file_size_field, len(out))
        elif uf.file_size_value == len(original):
            # v15-21 头部 fileSize u32（引擎一般不校验，保持一致性）
            struct.pack_into("<I", out, 4, len(out))
        return bytes(out)


def is_unity_assets(data: bytes) -> bool:
    try:
        parse_unity(data)
        return True
    except ParseError:
        return False
