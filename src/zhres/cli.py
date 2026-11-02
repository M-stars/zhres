"""cli —— zhres 命令行入口。

退出码：0 成功、1 用法/参数错误、2 目标文件处理失败、3 翻译校验失败。
所有面向用户的输出为中文。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from . import __version__
from .adapters.base import build_default_registry
from .config import load_config
from .errors import ParseError, ZhresError
from .pe import RT_RCDATA, list_resources, load_target_pe, type_display_name
from .packerdetect import scan as packer_scan
from .project import Entry, Project, new_project

logger = logging.getLogger("zhres")

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_TARGET = 2
EXIT_VALIDATE = 3

LEGAL_NOTICE = (
    "法律与道德提示：zhres 仅用于对您合法拥有的软件做自用汉化。\n"
    "  修改后的文件不得再分发；本工具不用于、也不得用于规避版权保护或技术保护措施；\n"
    "  请勿处理来路不明或破解版的程序。本提示仅显示一次（--help 可再次查看）。"
)


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_legal_notice() -> None:
    """首次运行 extract 时输出法律提示（A12）。"""
    marker = Path.home() / ".zhres" / ".legal-notice-shown"
    if marker.exists():
        return
    print(LEGAL_NOTICE)
    print()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("shown", encoding="utf-8")
    except OSError:
        pass  # 提示已输出，标记失败只影响下次重复显示


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

def cmd_extract(args, config: dict) -> int:
    exe = Path(args.exe)
    ensure_legal_notice()

    # 壳检测：命中默认拒绝执行
    scan = packer_scan(exe)
    if scan.packed:
        for hit in scan.hits:
            print(f"警告：检测到疑似加壳/保护节：{hit}", file=sys.stderr)
        if not args.force:
            print(
                f"已拒绝处理：{exe} 存在壳/保护特征（{', '.join(scan.hits)}），\n"
                "该文件可能无法安全处理（内容被压缩/加密或带完整性自校验）。\n"
                "如确认要继续，请使用 --force（后果自负）。",
                file=sys.stderr)
            return EXIT_TARGET
        print("警告：--force 已指定，继续处理（结果不可保证）。", file=sys.stderr)

    project = new_project(exe, file_sha256(exe), exe.stat().st_size, scan.machine)
    registry = build_default_registry()
    seen: dict[str, int] = {}
    entries: list[Entry] = []
    warnings: list[str] = []

    with load_target_pe(exe) as lib:
        refs = list_resources(lib)
        for ref in refs:
            tname = type_display_name(ref.type_id)
            # RCDATA 先读 4 字节签名，避免大资源整体入内存
            if ref.type_id == RT_RCDATA:
                head = lib.read_head(ref.type_id, ref.name, ref.lang, 4)
                if head != b"TPF0":
                    seen["RT_RCDATA_skipped"] = seen.get("RT_RCDATA_skipped", 0) + 1
                    logger.debug("跳过非 DFM 的 RCDATA：%r (lang=%d)", ref.name, ref.lang)
                    continue
                tname = "RT_RCDATA_DFM"
            data = lib.read(ref.type_id, ref.name, ref.lang)
            adapter = registry.find(ref.type_id, ref.name, ref.lang, data)
            if adapter is None:
                seen[f"{tname}_skipped"] = seen.get(f"{tname}_skipped", 0) + 1
                logger.debug("无适配器，跳过资源：%s %r (lang=%d, %d 字节)",
                             tname, ref.name, ref.lang, len(data))
                continue
            try:
                es = adapter.extract(ref.type_id, ref.name, ref.lang, data)
            except ParseError as exc:
                # 解析失败：该资源整体原样保留，不产生条目，警告列出
                msg = f"解析失败（{tname} name={ref.name!r} lang={ref.lang}）：{exc}"
                warnings.append(msg)
                logger.warning(msg)
                seen[f"{tname}_failed"] = seen.get(f"{tname}_failed", 0) + 1
                continue
            seen[tname] = seen.get(tname, 0) + 1
            logger.debug("解析 %s %r lang=%d：%d 字节 → %d 条",
                         tname, ref.name, ref.lang, len(data), len(es))
            entries.extend(es)

    seen_keys = [e.id for e in entries]
    if len(seen_keys) != len(set(seen_keys)):
        raise ZhresError("内部错误：条目 id 重复，请报告此问题")

    project.resources_seen = seen
    project.entries = entries

    # 统计表
    stats: dict[str, dict[str, int]] = {}
    for e in entries:
        row = stats.setdefault(e.resource_type, {"entries": 0, "translatable": 0, "skipped": 0})
        row["entries"] += 1
        if e.status == "skipped":
            row["skipped"] += 1
        else:
            row["translatable"] += 1
    print(f"目标：{exe}")
    print(f"SHA-256：{project.target['sha256']}")
    if not entries and not seen:
        print("未发现可翻译资源（资源目录为空）。")
    else:
        print(f"{'类型':<10}{'条目':>6}{'待翻译':>8}{'跳过':>6}")
        for rt, row in sorted(stats.items()):
            print(f"{rt:<10}{row['entries']:>6}{row['translatable']:>8}{row['skipped']:>6}")
        total = sum(r["entries"] for r in stats.values())
        trans = sum(r["translatable"] for r in stats.values())
        skip = sum(r["skipped"] for r in stats.values())
        print(f"{'合计':<10}{total:>6}{trans:>8}{skip:>6}")
        print(f"资源概览：{project.resources_seen}")

    out = Path(args.output) if args.output else Path(str(exe) + ".zhres-project.json")
    project.save(out)
    print(f"工程已写入：{out}")
    for w in warnings:
        print(f"警告：{w}", file=sys.stderr)
    return EXIT_OK


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

class _ArgumentParser(argparse.ArgumentParser):
    """用法错误退出码改为 1（任务书规定）。"""

    def error(self, message):  # noqa: D401
        self.print_usage(sys.stderr)
        print(f"{self.prog}: 错误：{message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="zhres",
        description="Windows 静态资源汉化工具：提取 → 翻译 → 回写（仅限自用汉化）",
    )
    parser.add_argument("--version", action="version",
                        version=f"zhres {__version__}")
    parser.add_argument("--verbose", action="store_true", help="输出每个资源的解析细节")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    parser.add_argument("--config", help="配置文件路径（默认查找 ./zhres.toml → ~/.zhres/zhres.toml）")

    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    p_extract = sub.add_parser("extract", help="提取 PE 中的界面资源到翻译工程")
    p_extract.add_argument("exe", help="目标 exe/dll 路径")
    p_extract.add_argument("-o", "--output", help="输出工程文件（默认 <exe>.zhres-project.json）")
    p_extract.add_argument("--force", action="store_true",
                           help="检测到壳/保护特征时仍继续（不推荐）")
    p_extract.set_defaults(func=cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.INFO),
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config = load_config(args.config)
        return args.func(args, config)
    except ZhresError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_TARGET
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
