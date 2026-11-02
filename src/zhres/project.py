"""project —— 翻译工程（``*.zhres-project.json``）的读写与数据模型。

Entry 数据类对应任务书 §4.2 的 schema；工程文件所有写盘均原子化
（临时文件 + os.replace）。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .errors import ProjectError

FORMAT_VERSION = 1

# Entry.status 的合法取值
STATUS_UNTRANSLATED = "untranslated"
STATUS_SKIPPED = "skipped"
STATUS_MACHINE = "machine"
STATUS_HUMAN = "human"
STATUS_APPROVED = "approved"

# 参与回写的状态（这些状态的 translation 会写入资源）
WRITABLE_STATUSES = {STATUS_MACHINE, STATUS_HUMAN, STATUS_APPROVED}


@dataclass
class Entry:
    """一条待翻译的界面文本。"""

    id: str                          # RT_<TYPE>/<resource_name>/<language>/<position>
    resource_type: str               # string | menu | dialog | dfm
    type_id: int                     # PE 资源类型序数（RT_STRING=6 等）
    resource_name: int | str         # 资源名（序数或字符串）
    language: int                    # 语言 ID（枚举原值）
    context: dict = field(default_factory=dict)
    source: str = ""
    translation: str | None = None
    status: str = STATUS_UNTRANSLATED
    skip_reason: str = ""
    max_len: int | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "resource_type": self.resource_type,
            "type_id": self.type_id,
            "resource_name": self.resource_name,
            "language": self.language,
            "context": self.context,
            "source": self.source,
            "translation": self.translation,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "max_len": self.max_len,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(
            id=d["id"],
            resource_type=d["resource_type"],
            type_id=d["type_id"],
            resource_name=d["resource_name"],
            language=d["language"],
            context=d.get("context") or {},
            source=d.get("source") or "",
            translation=d.get("translation"),
            status=d.get("status") or STATUS_UNTRANSLATED,
            skip_reason=d.get("skip_reason") or "",
            max_len=d.get("max_len"),
            notes=d.get("notes") or "",
        )


@dataclass
class Project:
    """翻译工程。"""

    format_version: int = FORMAT_VERSION
    tool_version: str = __version__
    created_at: str = ""
    target: dict = field(default_factory=dict)   # path/sha256/size/machine/backup_path/backup_sha256/font_patches
    languages: dict = field(default_factory=lambda: {"source": "en", "target": "zh-CN"})
    resources_seen: dict = field(default_factory=dict)
    entries: list[Entry] = field(default_factory=list)

    # -- 便捷访问 -----------------------------------------------------------

    def entry_by_id(self, entry_id: str) -> Entry | None:
        for e in self.entries:
            if e.id == entry_id:
                return e
        return None

    def stat_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entries:
            counts[e.status] = counts.get(e.status, 0) + 1
        return counts

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "tool_version": self.tool_version,
            "created_at": self.created_at,
            "target": self.target,
            "languages": self.languages,
            "resources_seen": self.resources_seen,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        if d.get("format_version") != FORMAT_VERSION:
            raise ProjectError(
                f"不支持的工程文件版本：{d.get('format_version')!r}（期望 {FORMAT_VERSION}）")
        entries = [Entry.from_dict(x) for x in d.get("entries", [])]
        return cls(
            format_version=d["format_version"],
            tool_version=d.get("tool_version", ""),
            created_at=d.get("created_at", ""),
            target=d.get("target") or {},
            languages=d.get("languages") or {"source": "en", "target": "zh-CN"},
            resources_seen=d.get("resources_seen") or {},
            entries=entries,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        p = Path(path)
        if not p.is_file():
            raise ProjectError(f"工程文件不存在：{p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"读取工程文件失败（{p}）：{exc}") from exc
        try:
            return cls.from_dict(data)
        except (KeyError, TypeError) as exc:
            raise ProjectError(f"工程文件格式错误（{p}）：{exc}") from exc

    def save(self, path: str | Path) -> None:
        """原子写工程 JSON（临时文件 + os.replace）。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, p)
        except OSError as exc:
            # 清理残留临时文件（replace 已成功时不存在，忽略）
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise ProjectError(f"写入工程文件失败（{p}）：{exc}") from exc


def new_project(target_path: str | Path, sha256: str, size: int,
                machine: str) -> Project:
    """为一次新的 extract 创建工程对象。"""
    return Project(
        created_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        target={
            "path": str(Path(target_path).resolve()),
            "sha256": sha256,
            "size": size,
            "machine": machine,
            "backup_path": None,
            "backup_sha256": None,
        },
    )
