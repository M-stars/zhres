"""verify —— 回写后复核：重新 extract 目标文件并与工程对照。

- 已翻译条目必须存在且当前源文 == 工程译文
- 资源 (type, name, lang) 集合与工程记录一致
- 任一失败 → 退出码 3（由 CLI 处理）并列出差异
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .adapters.base import build_default_registry
from .adapters.dotnet import PeView
from .errors import ZhresError
from .pe import RT_RCDATA, list_resources, load_target_pe
from .project import STATUS_APPROVED, STATUS_HUMAN, STATUS_MACHINE, Project

_TRANSLATED = (STATUS_MACHINE, STATUS_HUMAN, STATUS_APPROVED)


@dataclass
class VerifyResult:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    checked: int = 0


def verify_project(project: Project, exe: str | Path | None = None,
                   *, require_translated: bool = True) -> VerifyResult:
    """重新提取目标文件并与工程对照。"""
    target = Path(exe) if exe else Path(project.target["path"])
    result = VerifyResult()
    registry = build_default_registry()

    fresh: dict[str, str] = {}          # id → 当前源文
    fresh_translations: dict[str, str | None] = {}  # id → 当前译文（QM 等）
    fresh_refs: set[tuple] = set()
    if project.target.get("kind") in ("qm", "unity"):
        # QM / Unity 独立文件模式：重新解析并对照
        kind = project.target["kind"]
        if kind == "unity":
            from .adapters.unity import UnityAssetsAdapter
            file_adapter = UnityAssetsAdapter()
        else:
            from .adapters.qt import QtAdapter
            file_adapter = QtAdapter()
        try:
            data = target.read_bytes()
            for entry in file_adapter.extract(target.name, data):
                fresh[entry.id] = entry.source
                fresh_translations[entry.id] = entry.translation
            fresh_refs.add((kind.upper(), target.name, 0))
        except ZhresError as exc:
            result.ok = False
            result.problems.append(f"无法解析目标文件：{exc}")
            return result
    else:
        try:
            with load_target_pe(target) as lib:
                refs = list_resources(lib)
                for ref in refs:
                    fresh_refs.add((ref.type_id if isinstance(ref.type_id, int)
                                    else str(ref.type_id),
                                    ref.name if isinstance(ref.name, int)
                                    else str(ref.name),
                                    ref.lang))
                    if ref.type_id == RT_RCDATA:
                        if lib.read_head(ref.type_id, ref.name, ref.lang, 4) \
                                != b"TPF0":
                            continue
                    data = lib.read(ref.type_id, ref.name, ref.lang)
                    adapter = registry.find(ref.type_id, ref.name, ref.lang,
                                            data)
                    if adapter is None:
                        continue
                    for entry in adapter.extract(ref.type_id, ref.name,
                                                 ref.lang, data):
                        fresh[entry.id] = entry.source
            # .NET 托管资源
            if any(e.resource_type == "dotnet" for e in project.entries):
                from .adapters.dotnet import (read_clr_resources,
                                              parse_managed_blob,
                                              extract_entries as dn_extract)
                raw = target.read_bytes()
                info = read_clr_resources(raw)
                if info is not None:
                    _coff, res_size, _flags, res_rva = info
                    v = PeView(raw)
                    roff = v.rva2off(res_rva)
                    if roff is not None:
                        parsed = parse_managed_blob(raw[roff:roff + res_size],
                                                    name=target.name)
                        for entry in dn_extract(parsed, name=target.name):
                            fresh[entry.id] = entry.source
        except ZhresError as exc:
            result.ok = False
            result.problems.append(f"无法提取目标文件：{exc}")

    # 资源集合一致性（与 extract 时记录的 resource_refs 对照）
    expected_refs = {tuple(r) for r in project.target.get("resource_refs", [])}
    if expected_refs and fresh_refs != expected_refs:
        missing = expected_refs - fresh_refs
        extra = fresh_refs - expected_refs
        if missing:
            result.ok = False
            result.problems.append(
                f"资源缺失（{len(missing)} 个）：{sorted(missing, key=repr)[:5]}")
        if extra:
            result.ok = False
            result.problems.append(
                f"资源新增（{len(extra)} 个）：{sorted(extra, key=repr)[:5]}")

    # 已翻译条目对照
    for entry in project.entries:
        if require_translated and entry.status not in _TRANSLATED:
            continue
        if entry.status in _TRANSLATED and entry.translation is None:
            continue
        result.checked += 1
        if entry.id not in fresh:
            result.ok = False
            result.problems.append(f"{entry.id}: 条目在目标文件中不存在")
            continue
        if entry.context.get("verify_field") == "translation" and \
                entry.status in _TRANSLATED:
            # QM 等格式：文件内的源文记录不变，译文记录承载翻译
            if fresh_translations.get(entry.id) != entry.translation:
                result.ok = False
                result.problems.append(
                    f"{entry.id}: 译文不符（期望 {entry.translation!r}，"
                    f"实际 {fresh_translations.get(entry.id)!r}）")
            continue
        expected = entry.translation if entry.status in _TRANSLATED \
            else entry.source
        if fresh[entry.id] != expected:
            result.ok = False
            result.problems.append(
                f"{entry.id}: 内容不符（期望 {expected!r}，"
                f"实际 {fresh[entry.id]!r}）")
    return result
