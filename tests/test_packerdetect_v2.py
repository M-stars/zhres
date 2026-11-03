"""packerdetect v2 测试：分级检测（clean / suspicious / packed）。"""

from __future__ import annotations

import sys
import struct

import pytest

from fixture_pe import build_fixture_pe, build_pe, make_string_block
from zhres.packerdetect import classify, scan

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


def test_clean_fixture(tmp_path):
    exe = tmp_path / "clean.exe"
    exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(["Open"] * 16))]))
    r = classify(exe)
    assert r["level"] == "clean"
    assert r["machine"] == "x64"


def test_upx_section_packed(tmp_path):
    """节名黑名单命中 → packed（拒绝）。"""
    data = bytearray(build_fixture_pe())
    data[0x148:0x150] = b"UPX0\x00\x00\x00\x00"
    p = tmp_path / "packed.exe"
    p.write_bytes(bytes(data))
    r = classify(p)
    assert r["level"] == "packed"
    assert "UPX0" in r["hits"]


def test_entropy_packed(tmp_path):
    """可执行节高熵（加密/压缩特征）→ packed。"""
    import os
    data = bytearray(build_pe([(6, 1, 1033, make_string_block(["A"] * 16))]))
    # 唯一节改为可执行 + 填满高熵伪随机字节（加密/压缩特征）
    struct.pack_into("<I", data, 0x148 + 36, 0x60000040)  # +CODE|EXEC
    data[0x200:0x800] = os.urandom(0x600)
    p = tmp_path / "highent.exe"
    p.write_bytes(bytes(data))
    r = classify(p)
    assert r["level"] == "packed", r["notes"]


def test_tls_suspicious(tmp_path):
    """TLS 目录存在 → suspicious（自校验/反调试警告，不拒绝）。"""
    data = bytearray(build_pe([(6, 1, 1033, make_string_block(["Open"] * 16))]))
    e = 0x40
    opt = e + 24
    dd = opt + 112  # PE32+
    struct.pack_into("<II", data, dd + 9 * 8, 0x2000, 0x40)  # TLS 目录指向 .text
    p = tmp_path / "tls.exe"
    p.write_bytes(bytes(data))
    r = classify(p)
    assert r["tls_present"]
    assert any("TLS" in n for n in r["notes"])


def test_overlay_detected(tmp_path):
    exe = tmp_path / "ovl.exe"
    exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(["A"] * 16))])
                    + b"OVERLAY" * 256)
    r = classify(exe)
    assert r["overlay_bytes"] > 0


def test_scan_legacy_still_works(tmp_path):
    exe = tmp_path / "c.exe"
    exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(["A"] * 16))]))
    assert not scan(exe).packed
