"""adapters.base —— 适配器协议与公共逻辑。

所有资源内容体适配器实现同一协议（任务书 §3.2）：

- ``resource_types``：关心的 RT_* 序号元组
- ``can_parse``：判断本适配器能否解析该资源体
- ``extract``：把资源体解析为 Entry 列表
- ``apply``：把已翻译的 Entry 应用回原始资源体，返回替换后的字节；
  **未修改时必须原样返回**（字节级一致）
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from ..errors import ParseError
from ..project import Entry

# CJK 统一表意文字范围（含扩展 A）
_CJK_RANGES = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF),
)


def has_alpha_or_cjk(text: str) -> bool:
    """是否含有字母或 CJK 字符。"""
    for ch in text:
        cp = ord(ch)
        if ch.isalpha():
            return True
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def auto_skip(text: str, *, is_menu: bool = False) -> str | None:
    """自动跳过规则（任务书 §4.2）。返回 skip_reason，None 表示保留。

    规则：空串、纯空白、纯数字/标点、单个字符（快捷键定义除外——
    menu 中单个 ``&`` 后字符的项保留）、无字母且无 CJK 的串、
    ``&`` 开头且长度≤2（menu 中的快捷键项除外）。
    """
    if text == "":
        return "空字符串"
    stripped = text.strip()
    if stripped == "":
        return "纯空白"
    if stripped.isdigit():
        return "纯数字"
    # 纯数字/标点：无字母无 CJK
    if not has_alpha_or_cjk(stripped):
        return "纯数字或标点"
    # 单个字符（快捷键定义除外）
    if len(stripped) == 1 and not (is_menu and stripped == "&"):
        if is_menu and text.startswith("&") and len(text) == 2:
            return None  # "&X" 形式的快捷键定义，保留
        return "单个字符"
    # & 开头且长度≤2（menu 中的快捷键定义保留）
    if text.startswith("&") and len(text.strip()) <= 2:
        if not is_menu:
            return "快捷键占位串"
        return None
    return None


@runtime_checkable
class ResourceAdapter(Protocol):
    """资源适配器协议（任务书 §3.2）。"""

    resource_types: tuple[int, ...]

    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool: ...

    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]: ...

    def apply(self, entries: list[Entry], original: bytes) -> bytes: ...


class AdapterRegistry:
    """按 resource_types 分发的适配器注册表。

    无法解析的资源一律原样保留并记录警告，绝不因不认识的资源失败。
    """

    def __init__(self) -> None:
        self._adapters: dict[int, list] = {}

    def register(self, adapter) -> None:
        for rt in adapter.resource_types:
            self._adapters.setdefault(rt, []).append(adapter)

    def for_type(self, type_id: int) -> list:
        return list(self._adapters.get(type_id, []))

    def find(self, type_id: int, name, lang: int, data: bytes):
        """找到第一个 can_parse 的适配器；没有则返回 None。"""
        for adapter in self._adapters.get(type_id, []):
            try:
                if adapter.can_parse(type_id, name, lang, data):
                    return adapter
            except Exception as exc:  # can_parse 本身出错按"不认识"处理
                raise ParseError(
                    f"适配器 can_parse 异常：{exc}",
                    type_id=type_id, name=name, lang=lang) from exc
        return None


def build_default_registry() -> AdapterRegistry:
    """注册 v1 全部内置适配器。"""
    from .dialog import DialogAdapter
    from .dfm import DfmAdapter
    from .menu import MenuAdapter
    from .stringtable import StringTableAdapter

    reg = AdapterRegistry()
    reg.register(StringTableAdapter())
    reg.register(MenuAdapter())
    reg.register(DialogAdapter())
    reg.register(DfmAdapter())
    return reg


def make_entry(entry_id: str, resource_type: str, type_id: int, name,
               lang: int, context: dict, source: str,
               skip_reason: str | None) -> Entry:
    """按 skip 结果构造 Entry。"""
    if skip_reason is not None:
        return Entry(
            id=entry_id, resource_type=resource_type, type_id=type_id,
            resource_name=name, language=lang, context=context,
            source=source, status="skipped", skip_reason=skip_reason,
        )
    return Entry(
        id=entry_id, resource_type=resource_type, type_id=type_id,
        resource_name=name, language=lang, context=context,
        source=source, status="untranslated",
    )


def entry_position(entry_id: str) -> str:
    """取 Entry id 的 position 部分（最后一段）。"""
    return entry_id.rsplit("/", 1)[-1]
