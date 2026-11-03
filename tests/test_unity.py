"""Unity .assets（TextAsset）适配器测试：真实 Tuanjie/团结引擎文件。"""

from __future__ import annotations

import os
import shutil

import pytest

from zhres.adapters.unity import UnityAssetsAdapter, is_unity_assets  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
REAL = os.path.join(FIXTURES, "real_unity_resources.assets")


def _real_file() -> str | None:
    if os.path.isfile(REAL):
        return REAL
    # 开发机直达路径（未固化 fixture 时）
    p = r"D:/Tuanjie Devops/TjDevops_Data/resources.assets"
    return p if os.path.isfile(p) else None


def test_parse_real_tuanjie():
    src = _real_file()
    if not src:
        pytest.skip("无 Unity 测试文件")
    data = open(src, "rb").read()
    assert is_unity_assets(data)
    adapter = UnityAssetsAdapter()
    entries = adapter.extract("resources.assets", data)
    assert len(entries) == 2
    by_src = {e.source: e for e in entries}
    assert '{"androidStore":"GooglePlay"}' in by_src
    assert all(e.resource_type == "unity" for e in entries)
    assert all(e.id.startswith("UNITY/") for e in entries)


def test_roundtrip_no_change(tmp_path):
    src = _real_file()
    if not src:
        pytest.skip("无 Unity 测试文件")
    data = open(src, "rb").read()
    adapter = UnityAssetsAdapter()
    entries = adapter.extract("resources.assets", data)
    assert adapter.apply(entries, data) is data


def test_writeback_and_reparse(tmp_path):
    src = _real_file()
    if not src:
        pytest.skip("无 Unity 测试文件")
    data = open(src, "rb").read()
    adapter = UnityAssetsAdapter()
    entries = adapter.extract("resources.assets", data)
    target_entry = next(e for e in entries
                        if e.source == '{"androidStore":"GooglePlay"}')
    target_entry.translation = '{"androidStore":"中文商店"}'
    target_entry.status = "human"
    new_file = adapter.apply(entries, data)
    assert new_file != data

    # 重新提取：译文成为该条目的当前内容
    again = {e.id: e for e in adapter.extract("resources.assets", new_file)}
    assert again[target_entry.id].source == '{"androidStore":"中文商店"}'
    # 另一条不受影响
    other = next(e for e in entries if e.id != target_entry.id)
    assert again[other.id].source == other.source


def test_not_unity(tmp_path):
    assert not is_unity_assets(b"\x00" * 64)
    assert not is_unity_assets(b"MZ\x00\x00" + b"\x00" * 60)
