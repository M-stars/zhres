"""tm —— 翻译记忆（SQLite，WAL 模式，事务化）。

``source_norm`` = 去首尾空白、连续空白折叠为单空格、统一弯引号为直引号。
查询顺序：(source_norm, context_kind) 精确 → 仅 source_norm。
人工来源永远覆盖机器来源。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .errors import ZhresError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
  source_norm TEXT NOT NULL,
  context_kind TEXT NOT NULL DEFAULT '',
  translation TEXT NOT NULL,
  origin TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_norm, context_kind)
);
"""

_WS_RE = re.compile(r"\s+")
_CURLY = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
          "\u3010": "[", "\u3011": "]"}


def normalize(source: str) -> str:
    """归一化源文：去首尾空白、折叠空白、弯引号转直引号。"""
    text = source.strip()
    text = _WS_RE.sub(" ", text)
    for curly, straight in _CURLY.items():
        text = text.replace(curly, straight)
    return text


class TranslationMemory:
    """SQLite 翻译记忆。"""

    def __init__(self, db_path: str | Path) -> None:
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(p))
        except sqlite3.Error as exc:
            raise ZhresError(f"无法打开翻译记忆数据库（{p}）：{exc}") from exc
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # -- 写入 ---------------------------------------------------------------

    def upsert(self, source: str, translation: str, origin: str,
               context_kind: str = "") -> None:
        """写入/更新一条译文。human 覆盖 machine；machine 不覆盖 human。"""
        norm = normalize(source)
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with self._conn:
            row = self._conn.execute(
                "SELECT origin FROM translations WHERE source_norm=? AND context_kind=?",
                (norm, context_kind)).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO translations (source_norm, context_kind, translation,"
                    " origin, hits, updated_at) VALUES (?,?,?,?,0,?)",
                    (norm, context_kind, translation, origin, now))
            elif row[0] == "machine" and origin == "human":
                self._conn.execute(
                    "UPDATE translations SET translation=?, origin=?, updated_at=?"
                    " WHERE source_norm=? AND context_kind=?",
                    (translation, origin, now, norm, context_kind))
            elif row[0] == origin:
                self._conn.execute(
                    "UPDATE translations SET translation=?, updated_at=?"
                    " WHERE source_norm=? AND context_kind=?",
                    (translation, now, norm, context_kind))
            # row[0]=human 且 origin=machine：忽略

    # -- 查询 ---------------------------------------------------------------

    def lookup(self, source: str, context_kind: str = "") -> tuple[str, str] | None:
        """查询译文，返回 (translation, origin) 或 None。

        先 (source_norm, context_kind) 精确，再仅 source_norm
        （同文多语境时优先 human，其次 hits 高者）。命中记一次 hits。
        """
        norm = normalize(source)
        row = self._conn.execute(
            "SELECT translation, origin, context_kind FROM translations"
            " WHERE source_norm=? AND context_kind=?",
            (norm, context_kind)).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT translation, origin, context_kind FROM translations"
                " WHERE source_norm=?"
                " ORDER BY CASE origin WHEN 'human' THEN 0 ELSE 1 END,"
                " hits DESC, updated_at DESC LIMIT 1",
                (norm,)).fetchone()
        if row is None:
            return None
        translation, origin, matched_kind = row
        with self._conn:
            self._conn.execute(
                "UPDATE translations SET hits = hits + 1"
                " WHERE source_norm=? AND context_kind=?",
                (norm, matched_kind))
        return translation, origin

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM translations").fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TranslationMemory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
