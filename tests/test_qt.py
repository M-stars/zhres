"""Qt .qm 适配器测试：真实 PySide6 样本 round-trip、翻译回写。"""

from __future__ import annotations

import glob
import os
import shutil

import pytest

from zhres.adapters.qt import QtAdapter, is_qm_file, parse_qm, serialize_qm

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _qm_samples() -> list[str]:
    """优先用 tests/fixtures 的样本；找不到时探测 PySide6 安装目录。"""
    files = sorted(glob.glob(os.path.join(FIXTURES, "*.qm")))
    if files:
        return files
    for pat in ("D:/EnglishScram/venv/Lib/site-packages/PySide6/translations/*.qm",):
        files = sorted(glob.glob(pat))[:3]
        if files:
            return files
    return []


def test_real_qm_roundtrip(tmp_path):
    samples = _qm_samples()
    if not samples:
        pytest.skip("本机无 .qm 样本")
    adapter = QtAdapter()
    for fn in samples[:3]:
        data = open(fn, "rb").read()
        assert is_qm_file(data)
        out = adapter.apply(adapter.extract(os.path.basename(fn), data), data)
        assert out == data, fn


def test_translation_writeback(tmp_path):
    samples = _qm_samples()
    if not samples:
        pytest.skip("本机无 .qm 样本")
    src = samples[0]
    data = open(src, "rb").read()
    adapter = QtAdapter()
    name = os.path.basename(src)
    entries = adapter.extract(name, data)
    translatable = [e for e in entries if e.status != "skipped"]
    assert translatable
    changed = translatable[0]
    changed.translation = "测试译文"
    changed.status = "human"
    out = adapter.apply(entries, data)
    assert out != data
    # 重新提取：该条为新译文，其他不变
    again = {e.id: e for e in adapter.extract(name, out)}
    assert again[changed.id].translation == "测试译文"
    others = [e for e in translatable[1:] if e.id in again]
    for e in others[:5]:
        assert again[e.id].translation == e.translation


def test_untouched_records_byte_exact(tmp_path):
    samples = _qm_samples()
    if not samples:
        pytest.skip("本机无 .qm 样本")
    src = samples[0]
    data = open(src, "rb").read()
    qm = parse_qm(data)
    # 非 Messages 块原样
    for tag, content in qm.blocks:
        if tag != 0x69:
            assert (tag, content) in [
                (t, c) for t, c in parse_qm(serialize_qm(qm)).blocks
            ]


def test_qm_extract_skips_and_ids(tmp_path):
    samples = _qm_samples()
    if not samples:
        pytest.skip("本机无 .qm 样本")
    src = samples[0]
    data = open(src, "rb").read()
    name = os.path.basename(src)
    entries = QtAdapter().extract(name, data)
    for e in entries:
        assert e.id.startswith(f"QM/{name}/")
        assert e.resource_type == "qt"


def test_fixture_copy_regression(tmp_path):
    """fixtures 里的样本回归（如有）。"""
    fixed = sorted(glob.glob(os.path.join(FIXTURES, "real_qm_*.bin")))
    if not fixed:
        pytest.skip("无固化的 QM 回归样本")
    adapter = QtAdapter()
    for fn in fixed:
        data = open(fn, "rb").read()
        assert serialize_qm(parse_qm(data)) == data, fn
