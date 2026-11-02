"""winapi / pe 层测试：对合成 PE 用真实 Windows API 枚举、读取（仅 Windows）。"""

from __future__ import annotations

import sys

import pytest

from fixture_pe import default_fixture_resources
from zhres.errors import PeError
from zhres.packerdetect import scan
from zhres.pe import list_resources, load_target_pe

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


class TestEnumeration:
    def test_enumerate_all_resources(self, fixture_pe_file):
        with load_target_pe(fixture_pe_file) as lib:
            refs = list_resources(lib)
        keys = [(r.type_id, r.name, r.lang) for r in refs]
        assert (6, 1, 1033) in keys
        assert (6, 2, 1033) in keys
        assert (16, 1, 1033) in keys
        assert (10, 900, 1033) in keys
        assert (10, "TEXTDATA", 1033) in keys  # 字符串资源名
        assert len(keys) == 5

    def test_read_string_block_roundtrip(self, fixture_pe_file):
        with load_target_pe(fixture_pe_file) as lib:
            data = lib.read(6, 1, 1033)
        assert data == default_fixture_resources()[0][3]  # 字节级一致

    def test_read_head_partial(self, fixture_pe_file):
        with load_target_pe(fixture_pe_file) as lib:
            head = lib.read_head(10, 900, 1033, 4)
        assert head == b"\x00\x01\x02n"

    def test_missing_file(self, tmp_path):
        with pytest.raises(PeError):
            load_target_pe(tmp_path / "nope.exe")

    def test_not_a_pe(self, tmp_path):
        p = tmp_path / "text.txt"
        p.write_bytes(b"hello world, definitely not a PE")
        with pytest.raises(PeError):
            load_target_pe(p)


class TestPackerDetect:
    def test_clean_fixture(self, fixture_pe_file):
        result = scan(fixture_pe_file)
        assert not result.packed
        assert result.machine == "x64"
        assert result.sections == [".rsrc"]

    def test_upx_section_rejected(self, tmp_path):
        # 手工构造含 UPX0 节名的 PE（A9 验收项）
        from fixture_pe import build_fixture_pe
        data = bytearray(build_fixture_pe())
        # 节表位于 0x40 + 4(签名) + 20(COFF) + 240(Optional) = 0x148；节名占前 8 字节
        data[0x148:0x150] = b"UPX0\x00\x00\x00\x00"
        p = tmp_path / "packed.exe"
        p.write_bytes(bytes(data))
        result = scan(p)
        assert result.packed
        assert "UPX0" in result.hits

    def test_machine_x86(self, tmp_path):
        from fixture_pe import build_pe, make_string_block
        pe = build_pe([(6, 1, 1033, make_string_block(["A"] * 16))])
        # 直接改 COFF 的 Machine 字段为 0x014C
        data = bytearray(pe)
        struct_off = 0x40 + 4
        data[struct_off:struct_off + 2] = (0x014C).to_bytes(2, "little")
        p = tmp_path / "x86.exe"
        p.write_bytes(bytes(data))
        assert scan(p).machine == "x86"
