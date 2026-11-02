"""config —— zhres.toml / zhres.json 配置加载与默认值。

查找顺序：``--config`` > ``./zhres.toml`` > ``~/.zhres/zhres.toml``。
Python 3.10（无 tomllib）时回退接受 ``zhres.json``，字段一致。
用户配置只覆盖出现的字段，其余取默认值（深层合并）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .errors import ZhresError

DEFAULT_CONFIG: dict = {
    "languages": {"source": "en", "target": "zh-CN"},
    "writeback": {
        "font": "Microsoft YaHei UI",
        "font_charset": 134,   # GB2312_CHARSET
        "backup": True,
    },
    "llm": {
        "enabled": False,      # 默认关闭，核心流程完全离线
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "ZHRES_API_KEY",
        "batch_size": 20,
        "temperature": 0.2,
        "timeout_seconds": 60,
        "max_retries": 3,
    },
}

_CANDIDATES = ("zhres.toml", "zhres.json")


def _deep_merge(base: dict, override: dict) -> dict:
    """用 override 深层覆盖 base（返回新 dict）。"""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_toml(path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
        with path.open("rb") as f:
            return tomllib.load(f)
    raise ZhresError(
        f"当前 Python（{sys.version_info.major}.{sys.version_info.minor}）无 tomllib，"
        f"请改用等价的 JSON 配置：{path.with_suffix('.json')}")


def _load_json(path: Path) -> dict:
    with path.open("rb") as f:
        return json.loads(f.read().decode("utf-8"))


def find_config(explicit: str | None) -> Path | None:
    """按优先级定位配置文件；找不到返回 None（全部用默认值）。"""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise ZhresError(f"指定的配置文件不存在：{p}")
        return p
    for candidate in _CANDIDATES:
        p = Path.cwd() / candidate
        if p.is_file():
            return p
    home = Path.home() / ".zhres"
    for candidate in _CANDIDATES:
        p = home / candidate
        if p.is_file():
            return p
    return None


def load_config(explicit: str | None = None) -> dict:
    """加载配置并合并默认值。"""
    path = find_config(explicit)
    if path is None:
        return dict(DEFAULT_CONFIG)
    try:
        if path.suffix.lower() == ".json":
            user = _load_json(path)
        elif path.suffix.lower() == ".toml":
            user = _load_toml(path)
        else:
            raise ZhresError(f"不支持的配置文件类型（{path.suffix}）：{path}")
    except ZhresError:
        raise
    except Exception as exc:
        raise ZhresError(f"解析配置文件失败（{path}）：{exc}") from exc
    if not isinstance(user, dict):
        raise ZhresError(f"配置文件顶层必须是表/对象：{path}")
    return _deep_merge(DEFAULT_CONFIG, user)
