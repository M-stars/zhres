"""pe —— 基于 Windows API 的 PE 资源枚举与读取。

只负责把 (type, name, lang) 三元组枚举出来并读取原始字节；
资源内容体的解析在 adapters/ 中完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import PeError
from .winapi import PeResourceLibrary

# 常见标准资源类型序数
RT_MENU = 4
RT_DIALOG = 5
RT_STRING = 6
RT_RCDATA = 10
RT_VERSION = 16

RT_NAMES: dict[int, str] = {
    1: "RT_CURSOR", 2: "RT_BITMAP", 3: "RT_ICON", 4: "RT_MENU",
    5: "RT_DIALOG", 6: "RT_STRING", 7: "RT_FONTDIR", 8: "RT_FONT",
    9: "RT_ACCELERATORS", 10: "RT_RCDATA", 11: "RT_MESSAGETABLE",
    12: "RT_GROUP_CURSOR", 14: "RT_GROUP_ICON", 16: "RT_VERSION",
    23: "RT_MANIFEST", 24: "RT_DLGINCLUDE",
}


def type_display_name(type_id: int | str) -> str:
    """资源类型的展示名：标准类型给 RT_XXX，否则原样。"""
    if isinstance(type_id, int) and type_id in RT_NAMES:
        return RT_NAMES[type_id]
    return str(type_id)


@dataclass(frozen=True)
class ResourceRef:
    """一个资源三元组。name 为序数 int 或字符串名；lang 保持枚举原值。"""

    type_id: int | str
    name: int | str
    lang: int

    @property
    def key(self) -> tuple[int | str, int | str, int]:
        return (self.type_id, self.name, self.lang)


def list_resources(lib: PeResourceLibrary) -> list[ResourceRef]:
    """枚举 PE 内全部资源三元组（按 type/name/lang 稳定排序）。"""

    def sort_key(ref: ResourceRef):
        t = (0, ref.type_id) if isinstance(ref.type_id, int) else (1, str(ref.type_id))
        n = (0, ref.name) if isinstance(ref.name, int) else (1, str(ref.name))
        return (t, n, ref.lang)

    refs: list[ResourceRef] = []
    for type_id in lib.enum_types():
        for name in lib.enum_names(type_id):
            for lang in lib.enum_languages(type_id, name):
                refs.append(ResourceRef(type_id, name, lang))
    refs.sort(key=sort_key)
    return refs


def load_target_pe(path: str | Path) -> PeResourceLibrary:
    """以只读数据文件方式打开目标 PE，失败给出中文错误。"""
    p = Path(path)
    if not p.is_file():
        raise PeError(f"目标文件不存在：{p}")
    return PeResourceLibrary(p)
