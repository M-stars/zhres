"""packerdetect —— 加壳 / 保护特征检测。

解析 PE 头与节表（纯 struct，只读），按节名黑名单识别常见壳。
命中即认为该文件可能无法安全处理：extract 默认拒绝执行，
``--force`` 可绕过（逐条警告）。本工具不提供、不讨论任何绕过手段。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .errors import PeError

# 节名黑名单（大小写不敏感），覆盖常见壳/保护器
SECTION_BLACKLIST = {
    "upx0", "upx1",
    ".aspack", ".adata",
    ".themida",
    ".vmp0", ".vmp1",
    ".enigma1", ".enigma2",
    ".mpress1", ".mpress2",
    ".nsp0", ".nsp1",
    ".petite",
    ".pec1",
    ".mew",
}

_MACHINE_NAMES = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "ARM64",
    0x01C4: "ARM",
}


@dataclass
class PackerScanResult:
    """节表扫描结果。"""

    machine: str = ""
    sections: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)  # 黑名单命中的节名

    @property
    def packed(self) -> bool:
        return bool(self.hits)


def _align8(n: int) -> int:
    return (n + 7) & ~7


def read_pe_headers(path: str | Path) -> tuple[str, list[str]]:
    """读取 PE 头，返回 (machine 名称, 节名列表)。仅读取，不校验完整性。"""
    p = Path(path)
    with p.open("rb") as f:
        dos = f.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            raise PeError(f"不是有效的 PE 文件（缺少 MZ 签名）：{p}")
        e_lfanew = struct.unpack_from("<I", dos, 0x3C)[0]
        if e_lfanew <= 0 or e_lfanew > 0x1000:
            raise PeError(f"PE 头偏移异常（e_lfanew={e_lfanew}）：{p}")
        f.seek(e_lfanew)
        sig = f.read(4)
        if sig != b"PE\x00\x00":
            raise PeError(f"不是有效的 PE 文件（缺少 PE 签名）：{p}")
        coff = f.read(20)
        if len(coff) < 20:
            raise PeError(f"PE COFF 头不完整：{p}")
        machine, num_sections, _ts, _sym, _nsym, _opt_size, _chars = \
            struct.unpack("<HHIIIHH", coff)
        # 跳过 OptionalHeader（大小已含在 COFF 头之后），直接读节表
        opt_size = struct.unpack_from("<H", coff, 16)[0]
        f.seek(e_lfanew + 4 + 20 + opt_size)
        raw = f.read(40 * num_sections)
        if len(raw) < 40 * num_sections:
            raise PeError(f"节表不完整：{p}")

    machine_name = _MACHINE_NAMES.get(machine, f"0x{machine:04X}")
    sections: list[str] = []
    for i in range(num_sections):
        name_bytes = raw[i * 40: i * 40 + 8]
        sections.append(name_bytes.split(b"\x00", 1)[0].decode("latin-1"))
    return machine_name, sections


def scan(path: str | Path) -> PackerScanResult:
    """扫描目标文件的壳特征。"""
    machine, sections = read_pe_headers(path)
    hits = [s for s in sections if s.lower() in SECTION_BLACKLIST]
    return PackerScanResult(machine=machine, sections=sections, hits=hits)


# ---------------------------------------------------------------------------
# v2：熵分析 / TLS 回调 / overlay —— 分级而非一刀切
# ---------------------------------------------------------------------------

def _section_entropy(data: bytes, rp: int, rs: int) -> float:
    """节原始数据的香农熵（0~8）。"""
    import math
    chunk = data[rp:rp + min(rs, 1 << 20)]
    if not chunk:
        return 0.0
    freq = [0] * 256
    for b in chunk:
        freq[b] += 1
    total = len(chunk)
    ent = 0.0
    for f in freq:
        if f:
            p = f / total
            ent -= p * math.log2(p)
    return ent


def read_pe_headers_ex(path: str | Path) -> dict:
    """扩展头信息：machine/sections 之外返回 entropy、TLS、overlay、
    数据目录摘要（供分级判断）。"""
    p = Path(path)
    data = p.read_bytes()
    machine_name, sections = _read_sections_raw(data)
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt_size = struct.unpack_from("<H", data, e + 20)[0]
    opt = e + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)
    tls_rva, tls_size = struct.unpack_from("<II", data, dd + 9 * 8)
    cert_off, cert_size = struct.unpack_from("<II", data, dd + 4 * 8)

    sec_table = opt + opt_size
    ent = []
    last_raw_end = 0
    for i in range(len(sections)):
        o = sec_table + i * 40
        name_b, vs, va, rs, rp = struct.unpack_from("<8sIIII", data, o)
        chars = struct.unpack_from("<I", data, o + 36)[0]
        nm = name_b.split(b"\x00", 1)[0].decode("latin-1")
        ent_entropy = _section_entropy(data, rp, rs)             if (chars & 0x20000000) and rs >= 512 else None  # 可执行节
        ent.append({"name": nm, "vsize": vs, "rawsize": rs,
                    "entropy": ent_entropy})
        last_raw_end = max(last_raw_end, rp + rs)

    overlay = len(data) - last_raw_end if len(data) > last_raw_end else 0
    if cert_size and cert_off + cert_size <= len(data) + 8:
        overlay = max(0, overlay - _align8(cert_off + cert_size - last_raw_end)
                      if cert_off >= last_raw_end else overlay)

    return {
        "machine": machine_name,
        "sections": sections,
        "section_details": ent,
        "tls_present": tls_rva > 0 and tls_size > 0,
        "overlay_bytes": overlay,
        "authenticode": cert_size > 0,
    }


def _align8(n: int) -> int:
    return (n + 7) & ~7


def _read_sections_raw(data: bytes) -> tuple[str, list[str]]:
    if data[:2] != b"MZ" or len(data) < 64:
        raise PeError(f"不是有效的 PE 文件（缺少 MZ 签名）")
    e = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e:e + 4] != b"PE\x00\x00":
        raise PeError("不是有效的 PE 文件（缺少 PE 签名）")
    nsec = struct.unpack_from("<H", data, e + 6)[0]
    opt_size = struct.unpack_from("<H", data, e + 20)[0]
    machine = struct.unpack_from("<H", data, e + 4)[0]
    sec = e + 24 + opt_size
    names = []
    for i in range(nsec):
        nb = data[sec + i * 40: sec + i * 40 + 8]
        names.append(nb.split(b"\x00", 1)[0].decode("latin-1"))
    return _MACHINE_NAMES.get(machine, f"0x{machine:04X}"), names


def classify(path: str | Path) -> dict:
    """综合分级：clean（干净）/ suspicious（可疑，警告）/ packed（加壳，
    默认拒绝）。entropy 阈值 7.4 只看可执行节，避免误报。"""
    info = read_pe_headers_ex(path)
    hits = [s for s in info["sections"] if s.lower() in SECTION_BLACKLIST]
    notes: list[str] = []
    level = "clean"
    if hits:
        level = "packed"
        notes.append(f"节名命中壳特征：{', '.join(hits)}")
    high = [d["name"] for d in info["section_details"]
            if d["entropy"] is not None and d["entropy"] >= 7.4]
    if high and level == "clean":
        level = "packed"
        notes.append(f"可执行节熵值异常（{', '.join(high)}），"
                     "内容可能被压缩/加密")
    if info["tls_present"]:
        if level == "clean":
            level = "suspicious"
        notes.append("存在 TLS 回调（常见于完整性自校验/反调试保护），"
                     "修改后程序可能拒绝运行")
    if info["overlay_bytes"] > 1024:
        notes.append(f"文件尾部有 {info['overlay_bytes']} 字节 overlay 数据"
                     "（安装器/保护器常见），修改不影响该区域")
    info["level"] = level
    info["notes"] = notes
    info["hits"] = hits
    return info
