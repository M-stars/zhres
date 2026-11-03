"""本机软件扫描（software.py）测试。"""

from __future__ import annotations

import os
import sys

import pytest
from zhres.software import (  # noqa: E402
    InstalledApp, scan_all, scan_folder, scan_registry, scan_start_menu)

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="注册表/开始菜单仅 Windows")


def test_scan_registry_nonempty():
    apps = scan_registry()
    assert apps, "本机注册表应至少发现一个已安装程序"
    for a in apps:
        assert a.name and a.exe_path
        assert a.exe_path.lower().endswith(".exe")
        assert os.path.isfile(a.exe_path), a.exe_path
        assert a.source == "registry"


def test_scan_start_menu_returns():
    apps = scan_start_menu()
    assert isinstance(apps, list)
    for a in apps:
        assert os.path.isfile(a.exe_path)


def test_scan_all_dedupes():
    apps = scan_all()
    paths = [os.path.normcase(a.exe_path) for a in apps]
    assert len(paths) == len(set(paths)), "同路径程序应去重"
    assert len(apps) >= len(scan_registry())
    names = [a.name for a in apps]
    assert names == sorted(names, key=str.lower)


def test_scan_folder(tmp_path):
    # 伪造“程序”：MZ 头 + 足够大小
    exe = tmp_path / "MyGame.exe"
    exe.write_bytes(b"MZ" + b"\x00" * (200 * 1024))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notexe.txt").write_text("x")
    found = scan_folder(tmp_path, max_depth=2)
    assert len(found) == 1
    assert found[0].name == "MyGame"
    assert found[0].source == "folder"


def test_scan_folder_skips_system_dirs(tmp_path):
    (tmp_path / "AppData").mkdir()
    (tmp_path / "AppData" / "Skipped.exe").write_bytes(b"MZ" + b"\x00" * 200000)
    (tmp_path / "Keep.exe").write_bytes(b"MZ" + b"\x00" * 200000)
    found = scan_folder(tmp_path, max_depth=3)
    assert [f.name for f in found] == ["Keep"]


def test_installed_app_size():
    app = InstalledApp(name="x", exe_path=sys.executable)
    assert app.size > 0
