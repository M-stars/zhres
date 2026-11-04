"""adapters.gamejson —— JSON 游戏数据适配器（RPG Maker MV/MZ 与通用 JSON）。

覆盖范围：
- **RPG Maker MV/MZ**：文本全部在 ``data/*.json``（事件指令 401 对话、
  102 选项、数据库名称/描述……），是 RPG/视觉小说类游戏最常见的
  文本载体
- **通用 JSON 本地化文件**：``zh-Hans.json``、``lang/*.json`` 等

提取策略：递归遍历所有**字符串值**（不提取键名），条目 id 与
context.pointer = RFC 6901 JSON Pointer（``~0``/``~1`` 转义，天然无歧义）。
跳过策略（宁可漏提，不可错改——脚本串/资源名改了会坏游戏）：

- 键名黑名单：``src``/``path``/``file``/``url``/``icon``/``bgm``…
  （资源引用）；``characterName``/``faceName`` 等资产文件名键
- 兄弟键推断：含 ``volume``/``pitch``/``pan`` 的对象是音频引用，
  其 ``name`` 是素材文件名，跳过
- RPG Maker 事件指令：``code`` 为 355/655/356/657（脚本/插件命令）、
  108/408（注释）的 parameters 整体跳过；357（MZ 插件命令）只跳过
  前两个参数（插件名/命令名）
- 字符串级启发：JS 代码特征、URL、文件扩展名结尾、全大写下划线
  标识符、``<notetag>``、超长串（>2000 字符）

回写：整文件重写，**按 context.pointer 定位赋值**（不解析 id，
文件夹模式相对路径含 ``/`` 也无歧义）。键序保持（json 解析天然
有序）；缩进风格按原文探测（紧凑/缩进）；尾部换行保持；回写前
``json.loads`` 复检（新文件必须可解析且与内存对象一致）。

限制（如实告知）：原文含重复键或非常规数字字面量（``1.50``、
``1E-6``）时，重写后与原文件字节可能有差异——游戏引擎按 JSON
语义读取，不影响运行；无改动时 apply 原样返回（字节级一致）。
"""

from __future__ import annotations

import json
import re

from ..errors import ParseError
from ..project import WRITABLE_STATUSES, Entry
from .base import auto_skip, make_entry

# 键名黑名单（小写比较）：值为资源引用/文件名/代码，翻译会破坏游戏
_KEY_BLOCKLIST = {
    "src", "path", "file", "filename", "filepath", "url", "link",
    "icon", "image", "picture", "animation", "bgm", "bgs", "me", "se",
    "font", "background", "condition", "extension", "folder", "dir",
    "sprite", "spritefile", "charactername", "battlername", "facename",
    "parallaxname", "boatname", "shipname", "airshipname", "title1",
    "title2", "script", "code", "id", "key", "symbol", "locale",
    "language", "langcode", "ciphertext", "note2",
}

# 音频/媒体对象的兄弟键特征：这些键出现过 → 对象内的字符串是素材名
_MEDIA_SIBLING_KEYS = {"volume", "pitch", "pan", "bitrate", "channels"}

# RPG Maker MV/MZ：这些事件指令的 parameters 是脚本/插件命令/注释
_RM_CODE_SKIP = {108, 408, 355, 655, 356, 657}

_ASSET_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".ogg", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".opus",
    ".mp4", ".webm", ".ogv", ".mkv", ".avi",
    ".js", ".json", ".csv", ".txt", ".xml", ".html", ".css",
    ".ttf", ".otf", ".woff", ".woff2", ".fnt",
    ".exe", ".dll", ".so", ".pak", ".zip", ".rar",
    ".ico", ".cur", ".ani", ".lnk",
)

_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")
_IDENT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_NOTETAG_RE = re.compile(r"^<[^<>]*>$")
# JS 代码特征：出现即视为脚本串
_CODE_TOKENS = (
    "function", "=>", "&&", "||", "==", "!=", "++", "--", "/*", "*/",
    "return ", "if(", "if (", "for(", "for (", "while(", "while (",
    "this.", "new ", "typeof", "import ", "export ", "class ",
)
_CODE_PAIR = re.compile(r"\([^()]*\)\s*[{;=]")   # foo(...) { / ; / =

MAX_JSON_STRING_LEN = 2000
MAX_JSON_FILE_SIZE = 64 * 1024 * 1024


def _decode(data: bytes) -> tuple[str, str]:
    """解码 JSON 文本。返回 (text, encoding)——编码名用于按原样回写。"""
    if data[:3] == b"\xef\xbb\xbf":
        try:
            return data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if b"\x00" in data[:8192]:
        # 含 NUL：可能是无 BOM 的 UTF-16
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return data.decode(enc), enc
            except UnicodeDecodeError:
                continue
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    raise ParseError("JSON 文件解码失败（非 UTF-8/GBK）")


def _looks_like_code(s: str) -> bool:
    if any(tok in s for tok in _CODE_TOKENS):
        return True
    if _URL_RE.search(s):
        return True
    if _CODE_PAIR.search(s):
        return True
    return False


def _looks_like_asset(s: str) -> bool:
    low = s.lower()
    return any(low.endswith(ext) for ext in _ASSET_EXT)


def _is_ident(s: str) -> bool:
    """全大写下划线标识符（SWITCH_NAME 之类，多为插件参数键）。"""
    return bool(_IDENT_RE.match(s)) and "_" in s


def _has_cjk(s: str) -> bool:
    return any(0x4E00 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) <= 0x4DBF
               for c in s)


def _skip_string(key: str, value: str, rm_code: int | None,
                 siblings: set[str]) -> str | None:
    """返回 skip_reason，None 表示保留。"""
    reason = auto_skip(value)
    if reason is not None:
        return reason
    if len(value) > MAX_JSON_STRING_LEN:
        return "超长字符串"
    if key.lower() in _KEY_BLOCKLIST:
        return "资源/代码键"
    if siblings & _MEDIA_SIBLING_KEYS:
        return "媒体素材引用"
    if rm_code in _RM_CODE_SKIP:
        return "脚本/注释指令"
    if _looks_like_code(value):
        return "代码特征"
    if _looks_like_asset(value):
        return "文件名"
    if _is_ident(value):
        return "标识符"
    if _NOTETAG_RE.match(value.strip()) and not _has_cjk(value):
        return "记号标签"
    return None


def _rm_code_of(obj: dict) -> int | None:
    """RPG Maker 事件指令识别：dict 含整数 code 键。"""
    code = obj.get("code")
    if isinstance(code, int):
        return code
    return None


def _ptr_escape(token: str) -> str:
    """RFC 6901 JSON Pointer 转义：~ → ~0，/ → ~1。"""
    return token.replace("~", "~0").replace("/", "~1")


def _pointer(path: list[str]) -> str:
    return "/".join(_ptr_escape(p) for p in path)


def can_parse_json(data: bytes, filename: str) -> bool:
    """能否按 JSON 数据文件解析（解析成功且能提出 ≥1 条字符串）。"""
    if len(data) > MAX_JSON_FILE_SIZE:
        return False
    try:
        text, _enc = _decode(data)
        root = json.loads(text)
    except (ParseError, ValueError):
        return False
    return _has_candidate(root)


def _has_candidate(root, depth: int = 0) -> bool:
    """粗略判断是否含有可提取的字符串值（限制深度防栈溢出）。"""
    if depth > 24:
        return False
    if isinstance(root, str):
        return bool(root.strip())
    if isinstance(root, list):
        return any(_has_candidate(x, depth + 1) for x in root[:64])
    if isinstance(root, dict):
        return any(_has_candidate(v, depth + 1) for v in root.values())
    return False


class GameJsonAdapter:
    """JSON 游戏数据适配器（独立文件模式，type_id 约定为 0）。"""

    resource_types = ()

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        return can_parse_json(data, filename)

    # -- 提取 ---------------------------------------------------------------

    def extract(self, name: str, data: bytes) -> list[Entry]:
        text, _enc = _decode(data)
        try:
            root = json.loads(text)
        except ValueError as exc:
            raise ParseError(f"JSON 解析失败：{exc}", name=name) from exc
        entries: list[Entry] = []
        self._walk(root, name, [], entries)
        return entries

    def _walk(self, node, name: str, path: list[str],
              out: list[Entry], skip_indices: set[int] | None = None,
              rm_code: int | None = None) -> None:
        if isinstance(node, dict):
            rm_code = _rm_code_of(node) or rm_code
            keys = list(node.keys())
            for k in keys:
                v = node[k]
                child_path = path + [_ptr_escape(str(k))]
                # MZ 插件命令 357：parameters[0..1] 是插件名/命令名，跳过
                child_skip = {0, 1} if rm_code == 357 and k == "parameters" \
                    and isinstance(v, list) else None
                if isinstance(v, (dict, list)):
                    self._walk(v, name, child_path, out, child_skip, rm_code)
                elif isinstance(v, str):
                    skip = _skip_string(str(k), v, rm_code, set(keys))
                    self._emit(v, name, child_path, out, skip)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                child_path = path + [str(i)]
                if isinstance(item, (dict, list)):
                    if skip_indices and i in skip_indices and \
                            isinstance(item, list):
                        # 357 parameters[0..1]（插件名/命令名）：不产出条目
                        continue
                    self._walk(item, name, child_path, out, None, rm_code)
                elif isinstance(item, str):
                    if skip_indices and i in skip_indices:
                        continue
                    skip = _skip_string(path[-1] if path else "", item,
                                        rm_code, set())
                    self._emit(item, name, child_path, out, skip)

    def _emit(self, value: str, name: str, path: list[str],
              out: list[Entry], skip: str | None) -> None:
        ptr = _pointer(path)
        display = "$." + ".".join(
            ("'" + p.replace("~1", "/").replace("~0", "~") + "'")
            if ("/" in p or " " in p) else
            p.replace("~1", "/").replace("~0", "~")
            for p in path)
        context = {
            "kind": "game_json",
            "file": name,
            "pointer": ptr,
            "location": display,
            "json_key": path[-1].replace("~1", "/").replace("~0", "~")
            if path else "",
        }
        e = make_entry(f"JSON/{name}/{ptr}", "json", 0, name, 0,
                       context, value, skip)
        out.append(e)

    # -- 回写 ---------------------------------------------------------------

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        text, enc = _decode(original)
        try:
            root = json.loads(text)
        except ValueError as exc:
            raise ParseError(f"JSON 解析失败：{exc}") from exc

        by_pointer: dict[str, str] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                ptr = e.context.get("pointer")
                if not ptr:
                    raise ParseError(f"条目缺少 pointer 上下文：{e.id}")
                by_pointer[ptr] = e.translation
        if not by_pointer:
            return original

        changed = self._assign(root, [], by_pointer)
        if not changed:
            return original

        out_text = _dump_json(root, text)
        # 回写复检：新文件必须可解析且与内存对象一致
        try:
            if json.loads(out_text) != root:
                raise ParseError("JSON 回写复检失败（round-trip 不一致）")
        except ValueError as exc:
            raise ParseError(f"JSON 回写复检失败：{exc}") from exc
        try:
            return out_text.encode(enc)
        except UnicodeEncodeError as exc:
            raise ParseError(
                f"译文无法以 {enc} 编码（JSON 回写中止）：{exc}") from exc

    def _assign(self, node, path: list[str],
                by_pointer: dict[str, str]) -> bool:
        """按 pointer 就地赋值。返回是否有改动。"""
        changed = False
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    changed = self._assign(v, path + [_ptr_escape(str(k))],
                                           by_pointer) or changed
                elif isinstance(v, str):
                    t = by_pointer.get(_pointer(path + [_ptr_escape(str(k))]))
                    if t is not None and t != v:
                        node[k] = t
                        changed = True
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, (dict, list)):
                    changed = self._assign(item, path + [str(i)],
                                           by_pointer) or changed
                elif isinstance(item, str):
                    t = by_pointer.get(_pointer(path + [str(i)]))
                    if t is not None and t != item:
                        node[i] = t
                        changed = True
        return changed


def _dump_json(root, original_text: str) -> str:
    """按原文件风格序列化（缩进/紧凑/尾换行）。"""
    trailing_nl = original_text.endswith("\n")
    if "\n" in original_text.strip():
        # 探测首层缩进宽度（默认 2 空格）
        m = re.search(r"\n(\s+)\S", original_text)
        indent = min(len(m.group(1).replace("\t", "    ")), 8) if m else 2
        out = json.dumps(root, ensure_ascii=False, indent=indent)
    else:
        # 紧凑风格：无 `, `/`: ` 空格视为 minified
        sample = original_text[:2000]
        if ": " not in sample and ", " not in sample:
            out = json.dumps(root, ensure_ascii=False,
                             separators=(",", ":"))
        else:
            out = json.dumps(root, ensure_ascii=False)
    if trailing_nl and not out.endswith("\n"):
        out += "\n"
    return out
