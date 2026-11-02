"""conftest —— pytest 共享 fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试可以 import src/zhres 与 tests/fixture_pe
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_pe import build_fixture_pe, build_pe  # noqa: E402


@pytest.fixture(scope="session")
def fixture_pe_bytes() -> bytes:
    """默认合成 PE（含全部目标资源类型）。"""
    return build_fixture_pe()


@pytest.fixture
def fixture_pe_file(tmp_path: Path, fixture_pe_bytes: bytes) -> Path:
    """落盘的合成 PE（每个测试独立副本，避免互相污染）。"""
    p = tmp_path / "fixture.exe"
    p.write_bytes(fixture_pe_bytes)
    return p


@pytest.fixture
def make_pe_file(tmp_path: Path):
    """构建自定义资源集合的合成 PE 并落盘。"""
    written: list[Path] = []

    def _make(resources, name: str = "custom.exe") -> Path:
        p = tmp_path / name
        p.write_bytes(build_pe(resources))
        written.append(p)
        return p

    yield _make


@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    """隔离用户主目录（法律提示标记等不污染真实 home）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    # Path.home() 在 Windows 上读 USERPROFILE
    return home
