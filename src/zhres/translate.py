"""translate —— 翻译编排：合并优先级 TM → 术语强制映射 → LLM → 人工。

优先级（任务书 §7.1）：
``approved`` > ``human`` > ``tm(human)`` > ``tm(machine)`` > ``llm`` > 未翻译。
任何写入必须先过校验；error 阻断该条（保持原状态，原因记入 notes）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import validate as validate_mod
from .errors import ZhresError
from .glossary import Glossary
from .project import STATUS_APPROVED, STATUS_HUMAN, STATUS_SKIPPED, Entry, Project
from .tm import TranslationMemory


@dataclass
class TranslateResult:
    tm_hits: int = 0
    glossary_hits: int = 0
    llm_hits: int = 0
    untouched: int = 0
    failed: list[str] = field(default_factory=list)   # "id: 原因"
    warnings: list[str] = field(default_factory=list)


def _validate_and_store(entry: Entry, translation: str, status: str,
                        glossary: Glossary | None,
                        result: TranslateResult) -> bool:
    """校验并写入一条译文。error 时保持原状态并记录原因。"""
    errors, warnings = validate_mod.validate_pair(
        entry.source, translation,
        context_kind=entry.context.get("kind", ""),
        glossary=glossary)
    for w in warnings:
        result.warnings.append(f"{entry.id}: {w}")
    if errors:
        entry.notes = "校验失败: " + "；".join(errors)
        result.failed.append(f"{entry.id}: {'；'.join(errors)}")
        return False
    entry.translation = translation
    entry.status = status
    entry.notes = ""
    return True


def translate_project(project: Project, *,
                      tm: TranslationMemory | None = None,
                      glossary: Glossary | None = None,
                      llm=None,
                      batch_size: int = 20,
                      dry_run: bool = False) -> TranslateResult:
    """按合并优先级填充译文。

    llm: 可调用 ``fill(entries: list[Entry]) -> dict[id, translation]``，
    由 llm.py 提供（Phase 4）；None 表示离线模式。
    dry_run: 只打印将发生的动作，不修改工程。
    """
    if glossary is None:
        glossary = Glossary()
    result = TranslateResult()

    todo: list[Entry] = []
    for entry in project.entries:
        if entry.status == STATUS_SKIPPED:
            continue
        if entry.status in (STATUS_APPROVED, STATUS_HUMAN) and \
                entry.translation is not None:
            result.untouched += 1
            continue
        if entry.source:
            todo.append(entry)

    # 1) 翻译记忆（精确 → 归一化）
    if tm is not None:
        for entry in todo:
            if entry.translation is not None:
                continue
            hit = tm.lookup(entry.source, entry.context.get("kind", ""))
            if hit is None:
                continue
            translation, _origin = hit
            if dry_run:
                result.tm_hits += 1
                continue
            if _validate_and_store(entry, translation, "machine",
                                   glossary, result):
                result.tm_hits += 1

    # 2) 术语强制映射（源文精确命中术语键）
    for entry in todo:
        if entry.translation is not None:
            continue
        forced = glossary.lookup_term(entry.source)
        if forced is None:
            continue
        if dry_run:
            result.glossary_hits += 1
            continue
        if _validate_and_store(entry, forced, "machine", glossary, result):
            result.glossary_hits += 1

    # 3) LLM 批量（仅提供 llm 时）
    if llm is not None:
        pending = [e for e in todo if e.translation is None]
        batches = _make_batches(pending, batch_size)
        for batch in batches:
            if dry_run:
                # 批内同文去重：相同 source 只计一次请求
                result.llm_hits += len({e.source for e in batch})
                continue
            try:
                answers = llm(batch)
            except ZhresError as exc:
                for entry in batch:
                    entry.notes = f"LLM 失败: {exc}"
                    result.failed.append(f"{entry.id}: LLM 失败 ({exc})")
                continue
            for entry in batch:
                translation = answers.get(entry.id)
                if translation is None:
                    continue
                if _validate_and_store(entry, translation, "machine",
                                       glossary, result):
                    result.llm_hits += 1
    return result


def _make_batches(entries: list[Entry], batch_size: int) -> list[list[Entry]]:
    """同 context_kind 的相邻条目组成一批；相同 source 只送一次由
    调用方（llm.py）去重，此处仅分批。"""
    batches: list[list[Entry]] = []
    current: list[Entry] = []
    current_kind = None
    for entry in entries:
        kind = entry.context.get("kind", "")
        if current and (kind != current_kind or len(current) >= batch_size):
            batches.append(current)
            current = []
        current.append(entry)
        current_kind = kind
    if current:
        batches.append(current)
    return batches


def build_llm_request_sample(batch: list[Entry], model: str) -> str:
    """构造一条 LLM 请求样例（--dry-run 展示用，不产生网络请求）。"""
    items = [{"id": e.id, "source": e.source,
              "context": e.context.get("kind", "")} for e in batch[:5]]
    return json.dumps({
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "<内置系统提示词，见 llm.py>"},
            {"role": "user", "content": json.dumps(
                {"items": items}, ensure_ascii=False)},
        ],
    }, ensure_ascii=False, indent=2)
