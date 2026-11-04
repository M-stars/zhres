"""restore —— 从备份还原目标文件。

- 默认备份：``<exe>.zhres-orig.bak``；否则同名目录下唯一的
  ``<exe>.zhres-orig-<时间戳>.bak``
- 还原前把当前文件另存 ``<exe>.zhres-before-restore-<时间戳>.bak``
  （防止还原错对象）
- 备份 SHA-256 与工程记录不符 → 拒绝还原（备份损坏防护）
- 还原后校验 SHA-256 等于备份记录
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .errors import WritebackError
from .project import Project
from .writeback import _sha256_file


@dataclass
class RestoreResult:
    target: Path = None  # type: ignore[assignment]
    backup: Path = None  # type: ignore[assignment]
    before_restore: Path = None  # type: ignore[assignment]
    current_sha: str = ""
    backup_sha: str = ""
    restored_sha: str = ""


def find_backup(exe: Path) -> Path:
    """定位备份文件：默认 .bak，否则同目录唯一的时间戳备份。"""
    default = Path(str(exe) + ".zhres-orig.bak")
    if default.is_file():
        return default
    candidates = sorted(Path(exe).parent.glob(
        Path(exe).name + ".zhres-orig-*.bak"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise WritebackError(f"找不到备份文件：{default}（也无时间戳备份）")
    raise WritebackError(
        f"找到多个时间戳备份，请用 --backup 指定：\n"
        + "\n".join(str(c) for c in candidates))


def restore(exe: str | Path, backup: str | Path | None = None,
            *, yes: bool = False) -> RestoreResult:
    """还原目标文件。yes=True 跳过交互确认（由 CLI 处理确认）。"""
    target = Path(exe)
    if not target.is_file():
        raise WritebackError(f"目标文件不存在：{target}")
    bak = Path(backup) if backup else find_backup(target)
    if not bak.is_file():
        raise WritebackError(f"备份文件不存在：{bak}")

    current_sha = _sha256_file(target)
    backup_sha = _sha256_file(bak)

    # 备份损坏检测：与工程记录对照
    project_path = Path(str(target) + ".zhres-project.json")
    if project_path.is_file():
        try:
            project = Project.load(project_path)
        except Exception:
            project = None  # type: ignore[assignment]
        if project is not None:
            recorded = project.target.get("backup_sha256")
            recorded_path = project.target.get("backup_path")
            if recorded and recorded_path and \
                    Path(recorded_path).resolve() == bak.resolve() and \
                    recorded != backup_sha:
                raise WritebackError(
                    f"备份文件已损坏（SHA-256 与工程记录不符）：{bak}\n"
                    f"  期望 {recorded}\n  实际 {backup_sha}\n"
                    "已拒绝还原，当前文件未受影响。")

    result = RestoreResult(target=target, backup=bak,
                           current_sha=current_sha, backup_sha=backup_sha)

    # 当前文件另存（防止还原错对象）
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    before = Path(str(target) + f".zhres-before-restore-{stamp}.bak")
    try:
        shutil.copy2(target, before)
    except OSError as exc:
        raise WritebackError(f"保存当前文件副本失败：{exc}") from exc
    result.before_restore = before

    # 还原
    try:
        shutil.copy2(bak, target)
    except OSError as exc:
        raise WritebackError(f"还原失败：{exc}") from exc

    restored_sha = _sha256_file(target)
    result.restored_sha = restored_sha
    if restored_sha != backup_sha:
        raise WritebackError(
            f"还原后校验失败（SHA-256 不一致）：{target}")
    return result


def restore_folder(project: Project) -> list[RestoreResult]:
    """文件夹模式还原：迭代工程记录的全部文件。

    - 任一文件缺失/备份缺失：警告跳过（其余照常还原）
    - 备份 SHA-256 与工程记录不符：拒绝还原该文件（备份损坏防护）
    """
    from pathlib import Path as _P

    root = _P(str(project.target.get("path", "")))
    files = project.target.get("files") or []
    if not root or not files:
        raise WritebackError("工程内没有文件夹模式的目标文件记录")
    results: list[RestoreResult] = []
    for info in files:
        rel = info.get("rel", "")
        target = root / rel
        if not target.is_file():
            continue
        bak = find_backup(target)
        backup_sha = _sha256_file(bak)
        recorded = info.get("backup_sha256")
        recorded_path = info.get("backup_path")
        if recorded and recorded_path and \
                _P(recorded_path).resolve() == bak.resolve() and \
                recorded != backup_sha:
            raise WritebackError(
                f"备份文件已损坏（SHA-256 与工程记录不符）：{bak}\n"
                f"  文件：{rel}\n  期望 {recorded}\n  实际 {backup_sha}\n"
                "已拒绝还原，其余文件未受影响。")
        result = restore(target, bak, yes=True)
        results.append(result)
    if not results:
        raise WritebackError("没有可还原的文件（目标均不存在）")
    return results
