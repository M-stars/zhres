"""字节级 round-trip 测试（本工具的质量基石）。

对每个适配器：serialize(parse(x)) == x。Phase 1 覆盖 RT_STRING；
Phase 2 补 MENU / DIALOG / DFM。
"""

from __future__ import annotations

import pytest

from fixture_pe import default_fixture_resources
from zhres.adapters.base import build_default_registry
from zhres.adapters.stringtable import parse_block, serialize_block


def _resource_bytes(type_id, name, lang):
    for t, n, l, data in default_fixture_resources():
        if (t, n, l) == (type_id, name, lang):
            return data
    raise KeyError((type_id, name, lang))


class TestStringTableRoundtrip:
    @pytest.mark.parametrize("block_name", [1, 2])
    def test_fixture_roundtrip(self, block_name):
        data = _resource_bytes(6, block_name, 1033)
        assert serialize_block(parse_block(data)) == data

    def test_registry_extracts_fixture(self):
        registry = build_default_registry()
        data = _resource_bytes(6, 1, 1033)
        adapter = registry.find(6, 1, 1033, data)
        assert adapter is not None
        entries = adapter.extract(6, 1, 1033, data)
        assert entries
        # 未修改时 apply 必须原样返回
        assert adapter.apply(entries, data) is data

    def test_unknown_resource_kept(self):
        registry = build_default_registry()
        data = b"\x00\x01\x02not-a-dfm"
        assert registry.find(10, 900, 1033, data) is None
        assert registry.find(16, 1, 1033, b"version-info") is None
