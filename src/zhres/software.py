"""software —— 本机已安装软件扫描（傻瓜式入口）。

三个来源：
1. 注册表卸载表（HKLM/HKCU × 64/32 位视图）——覆盖绝大多数安装软件
2. 开始菜单快捷方式（.lnk 经 PowerShell WScript.Shell 解析）——
   覆盖注册表缺失的绿色版/游戏
3. 指定文件夹深度扫描——覆盖便携版游戏

只做发现与基础校验（MZ 头），不做深度兼容性判断（交给 extract）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import ZhresError


@dataclass
class InstalledApp:
    name: str
    exe_path: str
    publisher: str = ""
    source: str = "registry"   # registry | startmenu | folder

    @property
    def size(self) -> int:
        try:
            return os.path.getsize(self.exe_path)
        except OSError:
            return 0


def _is_exe(path: str) -> bool:
    try:
        if not os.path.isfile(path) or not path.lower().endswith(".exe"):
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def _display_icon_exe(raw: str) -> str | None:
    """DisplayIcon 形如 'C:\\...\\foo.exe,0'；取 exe 部分。"""
    if not raw:
        return None
    icon = raw
    if icon.lower().rstrip().endswith((".exe", ".dll")) and "," in icon:
        icon = icon.rsplit(",", 1)[0]
    icon = icon.strip().strip('"')
    if icon.lower().endswith(".exe") and os.path.isfile(icon):
        return icon
    return None


def _exe_from_install_location(loc: str, *preferred_names: str) -> str | None:
    """从安装目录找主程序：优先与名称匹配的 exe，其次目录下最大的 exe。"""
    if not loc or not os.path.isdir(loc):
        return None
    exes: list[str] = []
    try:
        for name in os.listdir(loc):
            full = os.path.join(loc, name)
            if name.lower().endswith(".exe") and _is_exe(full):
                exes.append(full)
    except OSError:
        return None
    if not exes:
        return None
    lowered = [n.lower() for n in preferred_names if n]
    for exe in exes:
        stem = os.path.splitext(os.path.basename(exe))[0].lower()
        if stem in lowered:
            return exe
    # 目录下唯一 exe 直接采用；多个则取最大（通常是主程序）
    if len(exes) == 1:
        return exes[0]
    return max(exes, key=lambda p: os.path.getsize(p))


def scan_registry() -> list[InstalledApp]:
    """注册表卸载表扫描。"""
    import winreg

    results: list[InstalledApp] = []
    seen_keys: set[str] = set()
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, path in roots:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                if sub_name in seen_keys:
                    continue
                seen_keys.add(sub_name)
                try:
                    sub = winreg.OpenKey(key, sub_name)
                except OSError:
                    continue
                with sub:
                    def qstr(name: str) -> str:
                        try:
                            v, _ = winreg.QueryValueEx(sub, name)
                            return v if isinstance(v, str) else ""
                        except OSError:
                            return ""

                    display = qstr("DisplayName")
                    if not display or qstr("SystemComponent") == "1":
                        continue
                    # 过滤卸载器/安装器/运行库等非主程序
                    _low = (display + " " + sub_name).lower()
                    if any(k in _low for k in (
                            "unins", "installer", "setup ", "redistributable",
                            "crash handler", "runtime", "vcredist")):
                        continue
                    exe = _display_icon_exe(qstr("DisplayIcon"))
                    if not exe:
                        loc = qstr("InstallLocation")
                        exe = _exe_from_install_location(
                            loc, display, sub_name)
                    if not exe or not _is_exe(exe):
                        continue
                    results.append(InstalledApp(
                        name=display, exe_path=exe,
                        publisher=qstr("Publisher"), source="registry"))
    return results


_PS_STARTMENU = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$sh = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('CommonPrograms'),
          [Environment]::GetFolderPath('Programs'))
foreach ($d in $dirs) {
  if (-not (Test-Path $d)) { continue }
  Get-ChildItem -LiteralPath $d -Recurse -Filter *.lnk -ErrorAction SilentlyContinue |
    ForEach-Object {
      try {
        $t = $sh.CreateShortcut($_.FullName).TargetPath
        if ($t -and $t.ToLower().EndsWith('.exe')) {
          Write-Output ($_.BaseName + "`t" + $t)
        }
      } catch {}
    }
}
"""


def scan_start_menu() -> list[InstalledApp]:
    """开始菜单快捷方式扫描（PowerShell 一次性解析全部 .lnk）。"""
    if sys.platform != "win32":
        return []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_STARTMENU],
            capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = r.stdout.decode("utf-8", "replace")
    results: list[InstalledApp] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "\t" not in line:
            continue
        name, target = line.split("\t", 1)
        name, target = name.strip(), target.strip()
        base = os.path.basename(target).lower()
        if any(k in base for k in ("unins", "installer", "crashhandler")):
            continue
        if not name or not _is_exe(target) or target.lower() in seen:
            continue
        seen.add(target.lower())
        results.append(InstalledApp(name=name, exe_path=target,
                                    source="startmenu"))
    return results


_SKIP_DIRS = {
    "windows", "windowsapps", "system32", "syswow64", "winSxS",
    "$recycle.bin", "appdata", "node_modules", ".git",
}


def scan_folder(root: str | Path, max_depth: int = 4,
                max_results: int = 500,
                progress=None) -> list[InstalledApp]:
    """指定文件夹深度扫描（便携版/游戏）。"""
    results: list[InstalledApp] = []
    base = Path(root)
    base_depth = len(base.parts)

    def walk(dirpath: Path, depth: int) -> None:
        if depth > max_depth or len(results) >= max_results:
            return
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            return
        for entry in entries:
            if entry.is_file() and entry.name.lower().endswith(".exe"):
                full = entry.path
                if _is_exe(full) and os.path.getsize(full) > 64 * 1024:
                    if progress:
                        progress(full)
                    results.append(InstalledApp(
                        name=entry.name[:-4], exe_path=full,
                        source="folder"))
            elif entry.is_dir():
                if entry.name.lower() in _SKIP_DIRS:
                    continue
                walk(Path(entry.path), depth + 1)

    walk(base, 0)
    return results


def scan_all() -> list[InstalledApp]:
    """注册表 + 开始菜单合并（按名称排序，同路径去重）。"""
    combined: dict[str, InstalledApp] = {}
    for app in scan_registry() + scan_start_menu():
        key = os.path.normcase(app.exe_path)
        if key not in combined:
            combined[key] = app
    return sorted(combined.values(), key=lambda a: a.name.lower())
