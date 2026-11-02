"""zhres 统一异常层级。"""

from __future__ import annotations


class ZhresError(Exception):
    """zhres 所有自定义异常的基类。"""


class PeError(ZhresError):
    """PE 文件读取 / 资源枚举 / 资源回写失败。"""


class ParseError(ZhresError):
    """资源内容体（字符串表/菜单/对话框/DFM）解析失败。

    携带资源上下文（type/name/lang）与字节偏移，便于定位。
    """

    def __init__(self, message: str, *, type_id: int | None = None,
                 name: object = None, lang: int | None = None,
                 offset: int | None = None) -> None:
        parts = [message]
        if type_id is not None:
            parts.append(f"type={type_id}")
        if name is not None:
            parts.append(f"name={name!r}")
        if lang is not None:
            parts.append(f"lang={lang}")
        if offset is not None:
            parts.append(f"offset={offset}")
        super().__init__("; ".join(parts))
        self.type_id = type_id
        self.name = name
        self.lang = lang
        self.offset = offset


class ProjectError(ZhresError):
    """翻译工程文件（JSON/CSV）读写或格式错误。"""


class ValidationError(ZhresError):
    """译文校验失败（error 级，阻断写入）。"""


class WritebackError(ZhresError):
    """回写 / 备份 / 还原过程中的失败。"""


class LlmError(ZhresError):
    """LLM 网络调用失败。"""
