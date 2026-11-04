"""adapters.gamefiles —— 游戏数据文件格式注册表（独立文件 + 文件夹模式）。

游戏引擎的文本载体绝大多数不是 PE 资源树，而是**数据文件**：
RPG Maker MV/MZ 的 data/*.json、GameMaker 的 data.win、Unity 的
.assets/.bundle、RGSS 档案、各种 TXT/INI/CSV 本地化文件……

本模块提供统一的注册表与检测入口：

- 每个 GameFileFormat 实现 QtAdapter/UnityAssetsAdapter 同款协议：
  ``can_parse_file(data, filename)`` / ``extract(name, data)`` /
  ``apply(entries, original) -> bytes``（未修改时必须原样返回）
- ``detect_game_file``：按"廉价兴趣判断 → 完整解析验证"两级检测，
  避免对二进制文件跑 JSON 解析之类的浪费
- ``scan_game_folder``：文件夹模式——扫描目录下所有可识别的
  游戏数据文件（一个工程对应一个游戏目录，一次提取/翻译/回写）

回写安全模型与 QM/Unity 独立文件模式一致：整文件原子重写
（临时文件 + os.replace），或数据格式内部原位修补；未识别内容
一律原样保留。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import ParseError
from ..project import Entry

# 单文件大于该值时不尝试按文本类格式解析（JSON/TXT/INI/CSV）
MAX_TEXT_FILE_SIZE = 64 * 1024 * 1024

# 文件夹扫描时跳过的目录（媒体/依赖/存档：无文本或不应汉化）
FOLDER_SKIP_DIRS = {
    "windows", "windowsapps", "system32", "syswow64", "winsxs",
    "$recycle.bin", "appdata", "node_modules", ".git", "__macosx",
    "audio", "img", "movies", "font", "fonts", "save", "saves",
    "backup", "cache", ".cache",
}

# 明确跳过的文件名（依赖清单/构建产物：翻译它们只会破坏构建）
_SKIP_FILENAMES = {
    "package.json", "package-lock.json", "yarn.lock", "tsconfig.json",
    "project.json",
}


class GameFileFormat:
    """游戏数据文件格式基类。子类需实现三个方法并给出 name/display。"""

    name: str = ""        # 工程内 target["kind"] / 文件级 fmt 标识
    display: str = ""     # 面向用户的中文名

    def interested(self, head: bytes, filename: str) -> bool:
        """廉价兴趣判断（魔数/扩展名），不解析内容。"""
        raise NotImplementedError

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        """完整验证：能否按本格式解析。"""
        raise NotImplementedError

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        raise NotImplementedError

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        raise NotImplementedError


class _QtFileFormat(GameFileFormat):
    """QM 翻译文件（Qt）——复用现有 QtAdapter。"""

    name = "qm"
    display = "Qt 翻译文件"

    def interested(self, head: bytes, filename: str) -> bool:
        from .qt import QM_MAGIC
        return head[:16] == QM_MAGIC

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        return self.interested(data[:16], filename)

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .qt import QtAdapter
        return QtAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .qt import QtAdapter
        return QtAdapter().apply(entries, original)


class _UnityFileFormat(GameFileFormat):
    """Unity SerializedFile（.assets）——复用现有 UnityAssetsAdapter。"""

    name = "unity"
    display = "Unity 资源文件"

    def interested(self, head: bytes, filename: str) -> bool:
        # v22：前 8 字节为零；v15-21：偏移 8 处小端版本号
        if head[:4] == b"\x00\x00\x00\x00":
            return True
        if len(head) >= 12:
            version = int.from_bytes(head[8:12], "little")
            if 15 <= version <= 30:
                return True
        return False

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        from .unity import is_unity_assets
        return is_unity_assets(data)

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .unity import UnityAssetsAdapter
        return UnityAssetsAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .unity import UnityAssetsAdapter
        return UnityAssetsAdapter().apply(entries, original)


class _UnityFSFileFormat(GameFileFormat):
    """UnityFS/UnityWeb/UnityRaw 包裹文件（.bundle/.assets bundle）。"""

    name = "unityfs"
    display = "Unity 包裹文件"

    def interested(self, head: bytes, filename: str) -> bool:
        return head[:8] in (b"UnityFS\x00", b"UnityWeb\x00", b"UnityRaw\x00")

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        from .unityfs import parse_bundle
        try:
            parse_bundle(data)
            return True
        except ParseError:
            return False

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .unityfs import UnityBundleAdapter
        return UnityBundleAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .unityfs import UnityBundleAdapter
        return UnityBundleAdapter().apply(entries, original)


class _GameMakerFileFormat(GameFileFormat):
    """GameMaker data.win / game.unx / audiogroup*.dat（FORM/STRG）。"""

    name = "gamemaker"
    display = "GameMaker 数据文件"

    def interested(self, head: bytes, filename: str) -> bool:
        return head[:4] == b"FORM"

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        from .gamedata_win import parse_dat
        try:
            return parse_dat(data, name=filename).strings is not None
        except ParseError:
            return False

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .gamedata_win import GameMakerAdapter
        return GameMakerAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .gamedata_win import GameMakerAdapter
        return GameMakerAdapter().apply(entries, original)


class _RGSSFileFormat(GameFileFormat):
    """RPG Maker XP/VX/VX Ace（RGSSAD/RGSS3A 档案与散装 Marshal 数据）。"""

    name = "rgss"
    display = "RPG Maker RGSS 数据"

    def interested(self, head: bytes, filename: str) -> bool:
        from .rgss import is_rgss_data
        return is_rgss_data(head, filename)

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        from .rgss import is_rgss_data
        return is_rgss_data(data, filename)

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .rgss import RGSSAdapter
        return RGSSAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .rgss import RGSSAdapter
        return RGSSAdapter().apply(entries, original)


class _JSONFileFormat(GameFileFormat):
    """JSON 本地化数据（RPG Maker MV/MZ data/*.json、通用 JSON）。"""

    name = "json"
    display = "JSON 数据文件"

    def interested(self, head: bytes, filename: str) -> bool:
        if Path(filename).suffix.lower() not in (".json",):
            return False
        base = Path(filename).name.lower()
        return base not in _SKIP_FILENAMES

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        from .gamejson import can_parse_json
        return can_parse_json(data, filename)

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .gamejson import GameJsonAdapter
        return GameJsonAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .gamejson import GameJsonAdapter
        return GameJsonAdapter().apply(entries, original)


class _TextFileFormat(GameFileFormat):
    """TXT / INI / CSV 等文本类本地化文件（通用兜底）。"""

    name = "text"
    display = "文本类数据文件"

    def interested(self, head: bytes, filename: str) -> bool:
        suffix = Path(filename).suffix.lower()
        return suffix in (".txt", ".ini", ".cfg", ".conf", ".csv",
                          ".properties", ".lang", ".text", ".ks", ".sc")

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        from .gametext import can_parse_text
        return can_parse_text(data, filename)

    def extract(self, filename: str, data: bytes) -> list[Entry]:
        from .gametext import GameTextAdapter
        return GameTextAdapter().extract(filename, data)

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        from .gametext import GameTextAdapter
        return GameTextAdapter().apply(entries, original)


# 注册顺序即检测顺序：魔数格式在前（精确），文本类在后（宽松）
_FORMATS: list[GameFileFormat] = [
    _QtFileFormat(),
    _UnityFileFormat(),
    _UnityFSFileFormat(),
    _GameMakerFileFormat(),
    _RGSSFileFormat(),
    _JSONFileFormat(),
    _TextFileFormat(),
]


def formats() -> list[GameFileFormat]:
    return list(_FORMATS)


def format_by_name(name: str) -> GameFileFormat | None:
    for fmt in _FORMATS:
        if fmt.name == name:
            return fmt
    return None


def detect_game_file(data: bytes, filename: str) -> GameFileFormat | None:
    """检测数据文件格式：先廉价兴趣判断，再完整解析验证。

    无法识别 → None（调用方按"不认识，原样保留"处理）。
    """
    head = data[:64]
    for fmt in _FORMATS:
        try:
            if not fmt.interested(head, filename):
                continue
            if fmt.can_parse_file(data, filename):
                return fmt
        except Exception:
            # 单个格式检测异常不影响其他格式
            continue
    return None


def scan_game_folder(root: str | Path, max_depth: int = 5,
                     max_files: int = 2000, progress=None) -> list[tuple[str, GameFileFormat]]:
    """扫描文件夹下所有可识别的游戏数据文件。

    返回 [(相对路径, 格式), ...]，相对路径以 POSIX 风格（/）分隔，
    稳定排序（保证多次提取条目顺序一致）。
    """
    base = Path(root)
    base_depth = len(base.parts)
    found: list[tuple[str, GameFileFormat]] = []

    def walk(dirpath: Path, depth: int) -> None:
        if depth > max_depth or len(found) >= max_files:
            return
        try:
            entries = sorted(os.scandir(dirpath), key=lambda e: e.name.lower())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_file():
                    if entry.stat().st_size > MAX_TEXT_FILE_SIZE:
                        continue
                    if entry.name.lower() in _SKIP_FILENAMES:
                        continue
                    # 跳过 zhres 自己的产物（备份/工程/临时文件）
                    if ".zhres-" in entry.name.lower():
                        continue
                    rel = entry.path[len(str(base)) + 1:]
                    rel = rel.replace("\\", "/")
                    try:
                        data = Path(entry.path).read_bytes()
                    except OSError:
                        continue
                    fmt = detect_game_file(data, entry.name)
                    if fmt is not None:
                        if progress:
                            progress(rel, fmt)
                        found.append((rel, fmt))
                elif entry.is_dir():
                    if entry.name.lower() in FOLDER_SKIP_DIRS:
                        continue
                    walk(Path(entry.path), depth + 1)
            except OSError:
                continue

    walk(base, 0)
    found.sort(key=lambda x: x[0].lower())
    return found
