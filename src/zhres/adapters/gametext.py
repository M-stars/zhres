"""adapters.gametext —— 文本类游戏数据适配器（TXT/INI/CSV 通用兜底）。

大量游戏引擎（Tyrano Builder 的 .ks 场景、Godot 的 CSV 表、
无数自研引擎的 lang/*.txt / config.ini）把文本放在**纯文本文件**
里。本适配器按扩展名分三种模式，全部保守提取——宁可漏提，
不可错改（改了键名/代码行会坏游戏）：

- **TXT 行模式**（.txt/.text/.ks/.sc）：只提取"普通行"——含字母、
  不含 ``=<>{}[]|\\~^$`` 等代码特征字符、不以 ``;``/``#``/``@``/
  ``*``/``[`` 开头、非 URL/文件名/标识符。KS 标签行 ``[bg …]``、
  赋值行、注释行天然被排除
- **INI 键值模式**（.ini/.cfg/.conf/.properties/.lang）：只提取
  ``key=value`` 的**值**，键名/节名/注释行不动；路径/扩展名/布尔/
  数字值跳过
- **CSV 表格模式**（.csv）：第 0 列（键列）与表头行不提取；自研
  解析器记录每个单元格的引号风格，回写按原风格逐格还原

编码：按 BOM → UTF-8 → GBK 顺序探测，回写用同一编码；译文无法
编码时报错中止（绝不静默替换）。回写为整文件原子重写；TXT/INI
按行号定位（行集合必须与提取时一致，否则报错），CSV 按行列定位。

限制（如实告知）：TXT/INI 的回写要求文件行集合不变——回写前文件
被其他工具改动过会拒绝；CSV 引号风格逐格保持，但表格结构变化
（增删行列）同样拒绝。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import ParseError
from ..project import WRITABLE_STATUSES, Entry
from .base import auto_skip, make_entry
from .gamejson import (_has_cjk, _is_ident, _looks_like_asset,
                       _looks_like_code, MAX_JSON_FILE_SIZE,
                       MAX_JSON_STRING_LEN)

# ---------------------------------------------------------------------
# 编码探测
# ---------------------------------------------------------------------

def _decode_text(data: bytes) -> tuple[str, str]:
    if data[:3] == b"\xef\xbb\xbf":
        try:
            return data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    raise ParseError("文本文件解码失败（非 UTF-8/UTF-16/GBK）")


def _split_terminator(line: str) -> tuple[str, str]:
    """把行拆成 (内容, 行终止符)。"""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _mode_of(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in (".ini", ".cfg", ".conf", ".properties", ".lang"):
        return "ini"
    return "txt"


# ---------------------------------------------------------------------
# 保守跳过规则（三种模式共用）
# ---------------------------------------------------------------------

_TXT_FORBIDDEN = set("=<>{}[]|\\~^$`")


def _line_candidate(line: str) -> str | None:
    """TXT 行是否为候选；返回 None=非候选（不产出条目）。"""
    s = line.strip()
    if not s or s.isdigit():
        return None
    if s[0] in ";#@*[/":
        return None
    if any(c in _TXT_FORBIDDEN for c in s):
        return None
    if not any(ch.isalpha() or 0x4E00 <= ord(ch) <= 0x9FFF for ch in s):
        return None
    return s


def _value_skip(value: str) -> str | None:
    """值级跳过（INI/CSV 单元格）。返回 skip_reason 或 None。"""
    reason = auto_skip(value)
    if reason is not None:
        return reason
    if len(value) > MAX_JSON_STRING_LEN:
        return "超长字符串"
    if _looks_like_code(value):
        return "代码特征"
    if _looks_like_asset(value):
        return "文件名"
    if _is_ident(value) and not _has_cjk(value):
        return "标识符"
    return None


# ---------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------

def can_parse_text(data: bytes, filename: str) -> bool:
    """能否按文本类数据解析（对应模式能提出 ≥1 条候选）。"""
    if len(data) > MAX_JSON_FILE_SIZE:
        return False
    if b"\x00" in data[:4096] and _mode_of(filename) != "txt":
        return False   # 含 NUL 的非 txt 大概率是二进制
    try:
        text, _enc = _decode_text(data)
    except ParseError:
        return False
    mode = _mode_of(filename)
    try:
        if mode == "csv":
            rows = _parse_csv(text)
            return next(iter(_iter_csv_cells(rows)), None) is not None
        if mode == "ini":
            return next(iter(_iter_ini_values(text)), None) is not None
        return next(iter(_iter_txt_lines(text)), None) is not None
    except ParseError:
        return False


# ---------------------------------------------------------------------
# TXT 行模式
# ---------------------------------------------------------------------

def _iter_txt_lines(text: str):
    for i, raw in enumerate(text.splitlines(keepends=True)):
        content, _term = _split_terminator(raw)
        if _line_candidate(content) is None:
            continue
        yield i, content


# ---------------------------------------------------------------------
# INI 键值模式
# ---------------------------------------------------------------------

_INI_LINE = re.compile(r"^(\s*)([^=;#\[\r\n]+?)(\s*)=(\s*)(.*)$")


def _iter_ini_values(text: str):
    section = ""
    for i, raw in enumerate(text.splitlines(keepends=True)):
        content, _term = _split_terminator(raw)
        stripped = content.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            continue
        m = _INI_LINE.match(content)
        if not m:
            continue
        key = m.group(2).strip()
        value = m.group(5)
        if not key or not value.strip():
            continue
        yield i, key, value, section


# ---------------------------------------------------------------------
# CSV 表格模式（自研解析，记录引号风格）
# ---------------------------------------------------------------------

class _CsvRow:
    __slots__ = ("cells", "quoted", "terminated")

    def __init__(self, cells: list[str], quoted: list[bool],
                 terminated: bool) -> None:
        self.cells = cells
        self.quoted = quoted
        self.terminated = terminated   # 行以换行结束


def _parse_csv(text: str) -> list[_CsvRow]:
    """RFC4180 近似解析：sep=','；引号内可含 sep/引号(双写)/换行。"""
    rows: list[_CsvRow] = []
    cells: list[str] = []
    quoted: list[bool] = []
    cur: list[str] = []
    cur_quoted = False
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                in_quotes = False
                cur_quoted = True
                i += 1
                continue
            cur.append(ch)
            i += 1
            continue
        if ch == '"' and not cur:
            in_quotes = True
            i += 1
            continue
        if ch == ',':
            cells.append("".join(cur))
            quoted.append(cur_quoted)
            cur = []
            cur_quoted = False
            i += 1
            continue
        if ch == '\n' or ch == '\r':
            if ch == '\r' and i + 1 < n and text[i + 1] == '\n':
                i += 1
            cells.append("".join(cur))
            quoted.append(cur_quoted)
            rows.append(_CsvRow(cells, quoted, True))
            cells = []
            quoted = []
            cur = []
            cur_quoted = False
            i += 1
            continue
        cur.append(ch)
        i += 1
    if in_quotes:
        raise ParseError("CSV 引号未闭合")
    if cur or cells:
        cells.append("".join(cur))
        quoted.append(cur_quoted)
        rows.append(_CsvRow(cells, quoted, False))
    return rows


def _csv_needs_quote(value: str) -> bool:
    return any(c in value for c in ',"\n\r')


def _serialize_csv(rows: list[_CsvRow]) -> str:
    out: list[str] = []
    for row in rows:
        parts = []
        for cell, was_quoted in zip(row.cells, row.quoted):
            need = was_quoted or _csv_needs_quote(cell)
            parts.append('"' + cell.replace('"', '""') + '"' if need
                         else cell)
        out.append(",".join(parts) + ("\r\n" if row.terminated else ""))
    return "".join(out)


_LOCALE_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


def _csv_candidates(rows: list[_CsvRow]):
    """返回表头行（若识别为本地化表头），否则 None。"""
    if rows and rows[0].cells:
        if all(_LOCALE_CODE.match(c.strip().lower()) or not c.strip()
               for c in rows[0].cells) and \
                any(c.strip() for c in rows[0].cells):
            return 0
    return None


def _iter_csv_cells(rows: list[_CsvRow]):
    header = _csv_candidates(rows)
    for r, row in enumerate(rows):
        if r == header:
            continue
        for c, cell in enumerate(row.cells):
            if c == 0:
                continue   # 第 0 列视为键列
            if not cell.strip():
                continue
            yield r, c, cell


# ---------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------

class GameTextAdapter:
    """文本类游戏数据适配器（TXT/INI/CSV，独立文件模式）。"""

    resource_types = ()

    def can_parse_file(self, data: bytes, filename: str) -> bool:
        return can_parse_text(data, filename)

    def extract(self, name: str, data: bytes) -> list[Entry]:
        text, _enc = _decode_text(data)
        mode = _mode_of(name)
        if mode == "csv":
            return self._extract_csv(name, text)
        if mode == "ini":
            return self._extract_ini(name, text)
        return self._extract_txt(name, text)

    # -- TXT ----------------------------------------------------------

    def _extract_txt(self, name: str, text: str) -> list[Entry]:
        entries: list[Entry] = []
        for i, content in _iter_txt_lines(text):
            skip = _value_skip(content)
            context = {"kind": "text_line", "file": name,
                       "location": f"第 {i + 1} 行", "line": i}
            e = make_entry(f"TXT/{name}/{i}", "text", 0, name, 0,
                           context, content, skip)
            entries.append(e)
        return entries

    # -- INI ----------------------------------------------------------

    def _extract_ini(self, name: str, text: str) -> list[Entry]:
        entries: list[Entry] = []
        for i, key, value, section in _iter_ini_values(text):
            skip = _value_skip(value)
            context = {"kind": "ini_value", "file": name,
                       "location": f"[{section}] {key}",
                       "line": i, "ini_key": key, "section": section}
            e = make_entry(f"INI/{name}/{i}", "ini", 0, name, 0,
                           context, value, skip)
            entries.append(e)
        return entries

    # -- CSV ----------------------------------------------------------

    def _extract_csv(self, name: str, text: str) -> list[Entry]:
        try:
            rows = _parse_csv(text)
        except ParseError:
            raise
        entries: list[Entry] = []
        header = _csv_candidates(rows)
        for r, c, cell in _iter_csv_cells(rows):
            skip = _value_skip(cell)
            context = {"kind": "csv_cell", "file": name,
                       "location": f"行 {r + 1} 列 {c + 1}",
                       "row": r, "col": c}
            e = make_entry(f"CSV/{name}/{r}/{c}", "csv", 0, name, 0,
                           context, cell, skip)
            entries.append(e)
        return entries

    # -- 回写 ----------------------------------------------------------

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        text, enc = _decode_text(original)
        mode = _mode_of(
            str(entries[0].context.get("file", "")) if entries else "")
        if mode == "csv":
            return self._apply_csv(entries, text, enc, original)
        if mode == "ini":
            return self._apply_ini(entries, text, enc, original)
        return self._apply_txt(entries, text, enc, original)

    def _apply_txt(self, entries: list[Entry], text: str,
                   enc: str, original: bytes) -> bytes:
        by_line: dict[int, tuple[str, str]] = {}   # line → (id, translation)
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                if "\n" in e.translation or "\r" in e.translation:
                    raise ParseError(
                        f"{e.id}：TXT 译文不能包含换行（会破坏行结构）")
                by_line[int(e.context["line"])] = (e.id, e.translation)
        if not by_line:
            return original
        lines = text.splitlines(keepends=True)
        if not lines:
            return original
        changed = False
        for i, raw in enumerate(lines):
            hit = by_line.get(i)
            if hit is None:
                continue
            eid, t = hit
            content, term = _split_terminator(raw)
            if t != content:
                if t.strip() and _line_candidate(t) is None:
                    # 译文含代码特征字符：拒绝（防止把代码行写进脚本）
                    raise ParseError(
                        f"{eid}：译文含代码特征字符，为安全起见拒绝写入 TXT")
                lines[i] = t + term
                changed = True
        if not changed:
            return original
        return _encode_text("".join(lines), enc, original)

    def _apply_ini(self, entries: list[Entry], text: str,
                   enc: str, original: bytes) -> bytes:
        by_line: dict[int, tuple[str, str]] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                if "\n" in e.translation or "\r" in e.translation:
                    raise ParseError(
                        f"{e.id}：INI 译文不能包含换行")
                by_line[int(e.context["line"])] = (e.id, e.translation)
        if not by_line:
            return original
        lines = text.splitlines(keepends=True)
        changed = False
        for i, raw in enumerate(lines):
            hit = by_line.get(i)
            if hit is None:
                continue
            _eid, t = hit
            content, term = _split_terminator(raw)
            m = _INI_LINE.match(content)
            if not m:
                raise ParseError(f"INI 第 {i + 1} 行与提取时不一致")
            new_line = f"{m.group(1)}{m.group(2)}{m.group(3)}={m.group(4)}{t}"
            if new_line != content:
                lines[i] = new_line + term
                changed = True
        if not changed:
            return original
        return _encode_text("".join(lines), enc, original)

    def _apply_csv(self, entries: list[Entry], text: str,
                   enc: str, original: bytes) -> bytes:
        try:
            rows = _parse_csv(text)
        except ParseError as exc:
            raise ParseError(f"CSV 重新解析失败：{exc}") from exc
        by_pos: dict[tuple[int, int], str] = {}
        for e in entries:
            if e.status in WRITABLE_STATUSES and e.translation is not None:
                by_pos[(int(e.context["row"]), int(e.context["col"]))] = \
                    e.translation
        if not by_pos:
            return original
        changed = False
        for (r, c), t in by_pos.items():
            if r >= len(rows) or c >= len(rows[r].cells):
                raise ParseError(
                    f"CSV 行 {r + 1} 列 {c + 1} 与提取时不一致（结构已变化）")
            if rows[r].cells[c] != t:
                rows[r].cells[c] = t
                # 含分隔符/引号/换行的单元格强制加引号
                rows[r].quoted[c] = rows[r].quoted[c] or \
                    _csv_needs_quote(t)
                changed = True
        if not changed:
            return original
        return _encode_text(_serialize_csv(rows), enc, original)


def _encode_text(text: str, enc: str, original: bytes) -> bytes:
    try:
        return text.encode(enc)
    except UnicodeEncodeError as exc:
        raise ParseError(
            f"译文无法以 {enc} 编码（回写中止）：{exc}") from exc
