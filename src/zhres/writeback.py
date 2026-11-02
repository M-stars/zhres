"""writeback —— 备份、完整性校验、UpdateResource 回写循环、字体补丁。

安全铁律（任务书 §8）：
- 原文件不可变：extract 只读；writeback 任一步失败都必须让目标文件
  保持原样（EndUpdateResourceW(TRUE) 丢弃）。
- 备份强制（--no-backup 需再显式 --yes-i-know）；备份后立即校验
  SHA-256，不一致视为失败终止。
- 成功后自动运行 verify。
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .adapters.base import build_default_registry
from .adapters.dialog import parse_dialog, patch_dialog_font, serialize_dialog
from .errors import WritebackError, ZhresError
from .pe import list_resources, load_target_pe
from .project import WRITABLE_STATUSES, Project
from .verify import verify_project
from .winapi import ResourceUpdater


@dataclass
class WritebackResult:
    changed_resources: int = 0
    font_patches: list[dict] = field(default_factory=list)
    length_warnings: list[str] = field(default_factory=list)
    verify_ok: bool = False
    verify_problems: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_backup(exe: Path, project: Project) -> tuple[Path, str]:
    """复制原文件为备份并校验 SHA-256。返回 (备份路径, sha256)。"""
    base = Path(str(exe) + ".zhres-orig.bak")
    if base.exists():
        base = Path(str(exe) + ".zhres-orig-"
                    + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    try:
        shutil.copy2(exe, base)
    except OSError as exc:
        raise WritebackError(f"创建备份失败（{exe} → {base}）：{exc}") from exc
    src_sha = _sha256_file(exe)
    bak_sha = _sha256_file(base)
    if src_sha != bak_sha:
        # 备份损坏视为失败终止（不继续回写）
        try:
            base.unlink()
        except OSError:
            pass
        raise WritebackError(
            f"备份校验失败（SHA-256 不一致）：{base}")
    project.target["backup_path"] = str(base)
    project.target["backup_sha256"] = bak_sha
    return base, bak_sha


def writeback(project: Project, exe: str | Path | None = None, *,
              project_path: str | Path | None = None,
              font: str = "Microsoft YaHei UI",
              font_charset: int = 134,
              no_font: bool = False,
              force: bool = False) -> WritebackResult:
    """把工程内已翻译条目写回目标文件。

    返回 WritebackResult；任何失败抛 WritebackError 且目标文件保持原样。
    调用方（CLI）负责 --no-backup / --yes-i-know 的参数校验与备份触发；
    备份本身也由调用方先行调用 make_backup()。
    project_path：工程 JSON 保存路径（默认 <exe>.zhres-project.json）。
    """
    target = Path(exe) if exe else Path(project.target["path"])
    if not target.is_file():
        raise WritebackError(f"目标文件不存在：{target}")

    # 目标文件完整性核对
    current_sha = _sha256_file(target)
    if current_sha != project.target.get("sha256") and not force:
        raise WritebackError(
            f"目标文件与工程记录不一致（SHA-256 不符）：{target}\n"
            "文件可能已被修改或替换。如确认要继续，请使用 --force。")

    result = WritebackResult()
    registry = build_default_registry()

    # 分组：三元组 → 该资源内的条目
    groups: dict[tuple, list] = {}
    for entry in project.entries:
        if entry.status not in WRITABLE_STATUSES or \
                entry.translation is None:
            continue
        key = (entry.type_id, entry.resource_name, entry.language)
        groups.setdefault(key, []).append(entry)
    if not groups:
        raise WritebackError("工程内没有可写回的已翻译条目")

    # 控件文本变长警告（§5.3：中文长度 > 源文长度 × 1.5）
    for entry in project.entries:
        if entry.status in WRITABLE_STATUSES and entry.translation and \
                entry.context.get("kind") == "dialog_control_text":
            if len(entry.translation) > len(entry.source) * 1.5:
                result.length_warnings.append(
                    f"{entry.id}: 译文长度 {len(entry.translation)} 超过"
                    f"源文长度 {len(entry.source)} × 1.5，可能超出控件宽度")

    updater = ResourceUpdater(target)
    try:
        with load_target_pe(target) as lib:
            existing = {r.key for r in list_resources(lib)}
            for (type_id, name, lang), entries in groups.items():
                if (type_id, name, lang) not in existing:
                    raise WritebackError(
                        f"目标文件缺少资源（type={type_id}, name={name!r}, "
                        f"lang={lang}）——文件与工程不匹配")
                original = lib.read(type_id, name, lang)
                adapter = registry.find(type_id, name, lang, original)
                if adapter is None:
                    continue
                new_bytes = adapter.apply(entries, original)
                # 字体补丁（仅对话框）
                if not no_font and type_id == 5:
                    dlg = parse_dialog(new_bytes)
                    if patch_dialog_font(dlg, font, font_charset):
                        patched = serialize_dialog(dlg)
                        result.font_patches.append({
                            "resource": f"RT_DIALOG/{name}/{lang}",
                            "to": font,
                            "charset": font_charset,
                        })
                        new_bytes = patched
                if new_bytes != original:
                    updater.update(type_id, name, lang, new_bytes)
                    result.changed_resources += 1
        updater.commit()
    except ZhresError:
        updater.discard()   # 任何失败：丢弃全部修改，目标文件保持原样
        raise
    except Exception as exc:
        updater.discard()
        raise WritebackError(f"回写失败（目标文件保持原样）：{exc}") from exc

    # 记录字体补丁清单进工程 JSON（§5.3 target.font_patches）
    if result.font_patches:
        project.target["font_patches"] = result.font_patches
    save_path = Path(project_path) if project_path else \
        Path(str(target) + ".zhres-project.json")
    project.save(save_path)

    # 自动 verify
    vr = verify_project(project, target)
    result.verify_ok = vr.ok
    result.verify_problems = vr.problems
    return result
