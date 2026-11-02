"""glossary —— 术语表（TOML）与保护记号。

```toml
[terms]
"File" = "文件"
"Exit" = "退出"
[protect]
patterns = ["%s", "%d", "%1$s", "\\\\n", "&"]
```

应用方式（§4.4）：翻译前把术语与占位符替换为保护记号（``⟦T0⟧``），
翻译后回填并校验无遗漏。术语强制映射：源文（归一化）精确命中
``[terms]`` 键时，译文强制为对应译名。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .errors import ZhresError

PROTECT_OPEN = "⟦"
PROTECT_CLOSE = "⟧"


class Glossary:
    """术语表：术语映射 + 占位符保护。"""

    def __init__(self, terms: dict[str, str] | None = None,
                 protect_patterns: list[str] | None = None) -> None:
        self.terms: dict[str, str] = dict(terms or {})
        self.protect_patterns: list[str] = list(protect_patterns or [])
        # 术语按长度降序（长词优先匹配）
        self._term_res: list[tuple[re.Pattern, str]] = [
            (re.compile(re.escape(term), re.IGNORECASE), translation)
            for term, translation in sorted(self.terms.items(),
                                            key=lambda kv: -len(kv[0]))
        ]
        self._protect_res = [re.compile(re.escape(p))
                             for p in self.protect_patterns]

    # -- 加载 ---------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        p = Path(path)
        if not p.is_file():
            raise ZhresError(f"术语表不存在：{p}")
        try:
            if p.suffix.lower() in (".toml",):
                if sys.version_info < (3, 11):
                    raise ZhresError(
                        "当前 Python 无 tomllib，请使用 JSON 术语表")
                import tomllib
                data = tomllib.loads(p.read_text(encoding="utf-8"))
            elif p.suffix.lower() == ".json":
                import json
                data = json.loads(p.read_text(encoding="utf-8"))
            else:
                raise ZhresError(f"不支持的术语表类型（{p.suffix}）：{p}")
        except ZhresError:
            raise
        except Exception as exc:
            raise ZhresError(f"解析术语表失败（{p}）：{exc}") from exc
        terms = data.get("terms") or {}
        protect = data.get("protect") or {}
        patterns = protect.get("patterns") or []
        if not isinstance(terms, dict) or not isinstance(patterns, list):
            raise ZhresError(f"术语表格式错误（[terms] 应为表、"
                             f"[protect].patterns 应为数组）：{p}")
        return cls(terms={str(k): str(v) for k, v in terms.items()},
                   protect_patterns=[str(x) for x in patterns])

    # -- 查询 ---------------------------------------------------------------

    def lookup_term(self, source: str) -> str | None:
        """源文精确命中术语键（忽略大小写）时返回强制译名。"""
        if source in self.terms:
            return self.terms[source]
        low = source.lower()
        for term, translation in self.terms.items():
            if term.lower() == low:
                return translation
        return None

    def missing_terms(self, source: str, translation: str) -> list[str]:
        """校验：源文含术语词而译文不含对应译名，返回缺失术语列表。"""
        missing = []
        for pattern, expected in self._term_res:
            if pattern.search(source) and expected not in translation:
                missing.append(f"{pattern.pattern}→{expected}")
        return missing

    # -- 保护 / 回填 --------------------------------------------------------

    def protect(self, text: str) -> tuple[str, dict[str, str]]:
        """把术语与占位符替换为 ⟦T<N>⟧ 记号。

        返回 (受保护文本, {记号: 原文})。
        """
        store: dict[str, str] = {}

        def _replace(match: re.Match) -> str:
            token = match.group(0)
            key = PROTECT_OPEN + str(len(store)) + PROTECT_CLOSE
            store[key] = token
            return key

        out = text
        for pattern in self._protect_res:
            out = pattern.sub(_replace, out)
        for pattern, _translation in self._term_res:
            out = pattern.sub(_replace, out)
        return out, store

    def restore(self, text: str, store: dict[str, str]) -> tuple[str, list[str]]:
        """回填记号。返回 (回填后文本, 未回填的记号列表)。"""
        lost = []
        for key, original in store.items():
            if key in text:
                text = text.replace(key, original)
            else:
                lost.append(key)
        return text, lost


def default_glossary() -> Glossary:
    """内置默认占位符保护（无术语）。"""
    return Glossary(protect_patterns=[
        "%1$s", "%2$s", "%3$s", "%s", "%d", "%u", "%x", "%c", "%f",
        "%1$d", "%2$d", "%3$d",
        "{0}", "{1}", "{2}", "{name}", "{filename}", "{path}",
        "\\n", "\\t", "\\r",
    ])
