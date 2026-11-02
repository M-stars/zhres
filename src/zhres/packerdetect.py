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
