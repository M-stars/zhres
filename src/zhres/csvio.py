"""csvio —— 人工翻译 CSV 导入/导出。

- 导出：``id,source,translation,status,notes``，UTF-8 **with BOM**
  （Excel 直接打开不乱码）；已翻译条目也导出（status 列可回改）
- 导入：id 匹配；译文非空则写入并置 status=human；id 不存在 → 警告跳过；
  写入前过校验（error 阻断该条）；同时写入 TM（origin=human）
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from . import validate as validate_mod
from .errors import ProjectError, ZhresError
from .glossary import Glossary
from .project import STATUS_HUMAN, Entry, Project
from .tm import TranslationMemory

COLUMNS = ["id", "source", "translation", "status", "notes"]


@dataclass
class ImportResult:
    imported: int = 0
    skipped_unknown: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def export_csv(project: Project, out_path: str | Path) -> Path:
    """导出工程条目为 CSV（UTF-8 with BOM）。"""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for e in project.entries:
                writer.writerow([e.id, e.source, e.translation or "",
                                 e.status, e.notes])
    except OSError as exc:
        raise ProjectError(f"写入 CSV 失败（{p}）：{exc}") from exc
    return p


def import_csv(project: Project, csv_path: str | Path, *,
               tm: TranslationMemory | None = None,
               glossary: Glossary | None = None) -> ImportResult:
    """把 CSV 译文合并回工程。"""
    p = Path(csv_path)
    if not p.is_file():
        raise ProjectError(f"CSV 文件不存在：{p}")
    result = ImportResult()
    by_id = {e.id: e for e in project.entries}

    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or \
                    "id" not in reader.fieldnames or \
                    "translation" not in reader.fieldnames:
                raise ProjectError(
                    f"CSV 缺少必需列 id/translation（{p}）")
            for row in reader:
                entry_id = (row.get("id") or "").strip()
                translation = row.get("translation") or ""
                status = (row.get("status") or "").strip()
                entry = by_id.get(entry_id)
                if entry is None:
                    result.skipped_unknown.append(entry_id)
                    continue
                if translation.strip() == "":
                    # 空译文：允许把状态改回 untranslated（清空翻译）
                    if status == "untranslated" and \
                            entry.status == STATUS_HUMAN:
                        entry.translation = None
                        entry.status = "untranslated"
                    continue
                errors, warnings = validate_mod.validate_pair(
                    entry.source, translation,
                    context_kind=entry.context.get("kind", ""),
                    glossary=glossary)
                for w in warnings:
                    result.warnings.append(f"{entry.id}: {w}")
                if errors:
                    entry.notes = "校验失败: " + "；".join(errors)
                    result.failed.append(
                        f"{entry.id}: {'；'.join(errors)}")
                    continue
                entry.translation = translation
                entry.status = STATUS_HUMAN
                entry.notes = ""
                result.imported += 1
                if tm is not None:
                    tm.upsert(entry.source, translation, "human",
                              entry.context.get("kind", ""))
    except OSError as exc:
        raise ProjectError(f"读取 CSV 失败（{p}）：{exc}") from exc
    except csv.Error as exc:
        raise ProjectError(f"CSV 解析失败（{p}）：{exc}") from exc
    return result
