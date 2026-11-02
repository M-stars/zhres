"""validate —— 译文校验（占位符 / 快捷键 / 术语 / 状态一致性）。

error 阻断该条写入；warning 汇总打印不阻断。
翻译与 CSV 导入时都要跑。
"""

from __future__ import annotations

import re

# 占位符模式：printf 风格（含 %1$s）、花括号风格、常见转义
_PLACEHOLDER_RE = re.compile(
    r"%\d+\$[-+ #0]*\d*(?:\.\d+)?[a-zA-Z]"   # %1$s
    r"|%(?:[-+ #0]*\d*(?:\.\d+)?)?[a-zA-Z%]"  # %s %d %%
    r"|\{[^{}\s]*\}"                          # {0} {name}
    r"|\\[ntr]"                               # \n \t \r
)


def extract_placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def _accelerators(text: str) -> list[str]:
    """提取快捷键字母：紧跟 & 的字符（&& 视为字面量跳过）。"""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "&":
            if i + 1 < len(text) and text[i + 1] == "&":
                i += 2
                continue
            if i + 1 < len(text):
                out.append(text[i + 1].upper())
                i += 2
                continue
        i += 1
    return out


def validate_pair(source: str, translation: str, *,
                  context_kind: str = "",
                  glossary=None) -> tuple[list[str], list[str]]:
    """校验单条译文。返回 (errors, warnings)。

    规则（任务书 §7.4）：
    1. 占位符集合一致（顺序可不严格，集合必须相等）→ error
    2. 快捷键一致：源文含 &X → 译文必须含 &（菜单惯例输出 中文(&X)）；
       同 dialog/menu 内译文快捷键字母冲突 → warning（由调用方汇总）
    3. 术语：源文含术语词而译文不含译名 → warning
    4. 译文为空但状态非 untranslated/skipped → error（调用方处理）
    """
    errors: list[str] = []
    warnings: list[str] = []

    src_ph = extract_placeholders(source)
    dst_ph = extract_placeholders(translation)
    if src_ph != dst_ph:
        lost = sorted(src_ph - dst_ph)
        extra = sorted(dst_ph - src_ph)
        if lost:
            errors.append(f"占位符丢失：{' '.join(lost)}")
        if extra:
            errors.append(f"占位符多余：{' '.join(extra)}")

    src_acc = _accelerators(source)
    dst_acc = _accelerators(translation)
    if src_acc and not dst_acc:
        errors.append(
            "快捷键丢失：源文含 &{}，译文应包含 &（惯例为 中文(&{})）".format(
                "/&".join(src_acc), src_acc[0]))

    if glossary is not None:
        for term in glossary.missing_terms(source, translation):
            warnings.append(f"术语缺失：{term}")

    if translation.strip() == "":
        errors.append("译文为空")
    return errors, warnings


def accel_conflicts(entries: list[tuple[str, list[str]]]) -> list[str]:
    """检查同一 dialog/menu 内译文快捷键字母冲突。

    entries: (分组键, 该组内各译文的快捷键字母列表)。返回冲突描述。
    """
    conflicts = []
    seen: dict[str, dict[str, list[str]]] = {}
    for group, accs in entries:
        by_letter = seen.setdefault(group, {})
        for acc in accs:
            by_letter.setdefault(acc, []).append(group)
    for group, by_letter in seen.items():
        for letter, owners in by_letter.items():
            if len(owners) > 1:
                conflicts.append(
                    f"{group}: 快捷键 &{letter} 被 {len(owners)} 处使用")
    return conflicts
