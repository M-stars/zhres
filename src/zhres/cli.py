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
    resource_refs: list[list] = []

    with load_target_pe(exe) as lib:
        refs = list_resources(lib)
        for ref in refs:
            tname = type_display_name(ref.type_id)
            resource_refs.append([ref.type_id if isinstance(ref.type_id, int)
                                  else str(ref.type_id),
                                  ref.name if isinstance(ref.name, int)
                                  else str(ref.name),
                                  ref.lang])
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
    project.target["resource_refs"] = resource_refs
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
# translate / export / import
# ---------------------------------------------------------------------------

def _load_project(path: str) -> Project:
    return Project.load(path)


def cmd_translate(args, config: dict) -> int:
    from .glossary import Glossary
    from .tm import TranslationMemory
    from .translate import build_llm_request_sample, translate_project

    project = _load_project(args.project)
    glossary = Glossary.load(args.glossary) if args.glossary else None
    tm = TranslationMemory(args.tm) if args.tm else None

    llm = None
    if args.llm:
        from .llm import make_llm_filler
        llm = make_llm_filler(config, dry_run=args.dry_run)

    try:
        result = translate_project(
            project, tm=tm, glossary=glossary, llm=llm,
            batch_size=config.get("llm", {}).get("batch_size", 20),
            dry_run=args.dry_run)
    finally:
        if tm is not None:
            tm.close()

    if args.dry_run:
        print("—— dry-run：以下为将发生的动作（未写任何文件）——")
        print(f"翻译记忆可命中：{result.tm_hits}")
        print(f"术语强制映射：{result.glossary_hits}")
        if args.llm:
            print(f"LLM 请求批次数（去重后条目）：{result.llm_hits}")
            sample_batch = [e for e in project.entries
                            if e.status == "untranslated"][:5]
            if sample_batch:
                print("LLM 请求样例：")
                print(build_llm_request_sample(
                    sample_batch, config.get("llm", {}).get("model", "")))
        return EXIT_OK

    project.save(args.project)
    print(f"翻译记忆命中：{result.tm_hits}")
    print(f"术语强制映射：{result.glossary_hits}")
    print(f"LLM 翻译：{result.llm_hits}")
    print(f"已有译文（不动）：{result.untouched}")
    for w in result.warnings[:20]:
        print(f"警告：{w}", file=sys.stderr)
    if len(result.warnings) > 20:
        print(f"警告：……另有 {len(result.warnings) - 20} 条", file=sys.stderr)
    for f in result.failed[:20]:
        print(f"校验失败：{f}", file=sys.stderr)
    if len(result.failed) > 20:
        print(f"校验失败：……另有 {len(result.failed) - 20} 条", file=sys.stderr)
    counts = project.stat_counts()
    print(f"状态统计：{counts}")
    print(f"工程已保存：{args.project}")
    return EXIT_OK


def cmd_export(args, config: dict) -> int:
    from .csvio import export_csv
    project = _load_project(args.project)
    out = export_csv(project, args.csv)
    print(f"已导出 {len(project.entries)} 条 → {out}（UTF-8 with BOM，"
          "Excel 可直接打开）")
    return EXIT_OK


def cmd_import(args, config: dict) -> int:
    from .csvio import import_csv
    from .glossary import Glossary
    from .tm import TranslationMemory

    project = _load_project(args.project)
    glossary = Glossary.load(args.glossary) if args.glossary else None
    tm = TranslationMemory(args.tm) if args.tm else None
    try:
        result = import_csv(project, args.file, tm=tm, glossary=glossary)
    finally:
        if tm is not None:
            tm.close()
    project.save(args.project)
    print(f"导入 {result.imported} 条（status=human，已写入翻译记忆）")
    if result.skipped_unknown:
        print(f"跳过未知 id：{len(result.skipped_unknown)} 条"
              f"（如 {result.skipped_unknown[:3]}）", file=sys.stderr)
    for w in result.warnings[:20]:
        print(f"警告：{w}", file=sys.stderr)
    for f in result.failed[:20]:
        print(f"校验失败：{f}", file=sys.stderr)
    project.save(args.project)
    print(f"工程已保存：{args.project}")
    return EXIT_OK if not result.failed else EXIT_VALIDATE


# ---------------------------------------------------------------------------
# writeback / verify / restore / report
# ---------------------------------------------------------------------------

def cmd_writeback(args, config: dict) -> int:
    from .writeback import make_backup, writeback

    project = _load_project(args.project)
    if args.no_backup and not args.yes_i_know:
        print("错误：--no-backup 必须与 --yes-i-know 同时使用"
              "（关闭备份非常危险）。", file=sys.stderr)
        return EXIT_USAGE

    wb_config = config.get("writeback", {})
    exe = args.exe or project.target.get("path")

    # 完整性核对
    if not args.force and exe and Path(exe).is_file():
        current = file_sha256(exe)
        if current != project.target.get("sha256"):
            print(f"错误：目标文件与工程记录不一致（SHA-256 不符）：{exe}\n"
                  "文件可能已被修改或替换。如确认要继续，请使用 --force。",
                  file=sys.stderr)
            return EXIT_TARGET

    if not args.no_backup:
        from pathlib import Path as _P
        backup, bak_sha = make_backup(_P(exe), project)
        print(f"已备份：{backup}（SHA-256 校验通过）")
        project.save(args.project)
    else:
        print("警告：--no-backup 已指定，不创建备份。", file=sys.stderr)

    try:
        result = writeback(
            project, exe, project_path=args.project,
            font=args.font or wb_config.get("font", "Microsoft YaHei UI"),
            font_charset=wb_config.get("font_charset", 134),
            no_font=args.no_font, force=args.force)
    except ZhresError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_TARGET

    print(f"回写完成：更新 {result.changed_resources} 个资源")
    for fp in result.font_patches:
        print(f"字体补丁：{fp['resource']} → {fp['to']}")
    for w in result.length_warnings:
        print(f"警告：{w}", file=sys.stderr)
    if result.verify_ok:
        print("自动校验（verify）：通过")
        return EXIT_OK
    print("自动校验（verify）：失败！", file=sys.stderr)
    for p in result.verify_problems:
        print(f"  {p}", file=sys.stderr)
    return EXIT_VALIDATE


def cmd_verify(args, config: dict) -> int:
    from .verify import verify_project
    project = _load_project(args.project)
    exe = args.exe or project.target.get("path")
    result = verify_project(project, exe)
    if result.ok:
        print(f"校验通过（对照 {result.checked} 条，资源集合一致）")
        return EXIT_OK
    print(f"校验失败（{len(result.problems)} 处差异）：", file=sys.stderr)
    for p in result.problems[:30]:
        print(f"  {p}", file=sys.stderr)
    if len(result.problems) > 30:
        print(f"  ……另有 {len(result.problems) - 30} 处", file=sys.stderr)
    return EXIT_VALIDATE


def cmd_restore(args, config: dict) -> int:
    from .restore import find_backup, restore
    exe = Path(args.exe)
    backup = args.backup or find_backup(exe)
    print(f"目标：{exe}")
    print(f"备份：{backup}")
    print(f"当前文件 SHA-256：{file_sha256(exe)}")
    print(f"备份文件 SHA-256：{file_sha256(backup)}")
    if not args.yes:
        try:
            answer = input("确认还原？(y/N) ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            print("已取消。")
            return EXIT_OK
    result = restore(exe, backup, yes=True)
    print(f"当前文件已另存：{result.before_restore}")
    print(f"还原完成，SHA-256 校验通过（{result.restored_sha}）")
    return EXIT_OK


def cmd_report(args, config: dict) -> int:
    project = _load_project(args.project)
    entries = project.entries
    print(f"工程：{args.project}")
    print(f"目标：{project.target.get('path')}")
    print(f"语言：{project.languages.get('source')} → "
          f"{project.languages.get('target')}")
    stats: dict[str, dict[str, int]] = {}
    for e in entries:
        row = stats.setdefault(e.resource_type, {})
        row[e.status] = row.get(e.status, 0) + 1
    print(f"{'类型':<10}{'总数':>6}{'待翻译':>8}{'机器':>6}{'人工':>6}"
          f"{'已批准':>8}{'跳过':>6}")
    total = {"untranslated": 0, "machine": 0, "human": 0, "approved": 0,
             "skipped": 0}
    for rt, row in sorted(stats.items()):
        n = sum(row.values())
        print(f"{rt:<10}{n:>6}{row.get('untranslated', 0):>8}"
              f"{row.get('machine', 0):>6}{row.get('human', 0):>6}"
              f"{row.get('approved', 0):>8}{row.get('skipped', 0):>6}")
        for k in total:
            total[k] += row.get(k, 0)
    n = len(entries)
    tr = total["machine"] + total["human"] + total["approved"]
    translatable = n - total["skipped"]
    cover = (tr / translatable * 100) if translatable else 100.0
    print(f"{'合计':<10}{n:>6}{total['untranslated']:>8}{total['machine']:>6}"
          f"{total['human']:>6}{total['approved']:>8}{total['skipped']:>6}")
    print(f"翻译覆盖率：{cover:.1f}%（{tr}/{translatable}，不含跳过项）")

    skip_reasons: dict[str, int] = {}
    for e in entries:
        if e.status == "skipped" and e.skip_reason:
            skip_reasons[e.skip_reason] = skip_reasons.get(e.skip_reason, 0) + 1
    if skip_reasons:
        print("跳过原因分布：")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")

    tm_path = args.tm
    if tm_path and Path(tm_path).is_file():
        from .tm import TranslationMemory
        with TranslationMemory(tm_path) as tm:
            print(f"翻译记忆：{tm.count()} 条（{tm_path}）")
    if project.target.get("font_patches"):
        print(f"字体补丁：{len(project.target['font_patches'])} 处")
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

    p = sub.add_parser("extract", help="提取 PE 中的界面资源到翻译工程")
    p.add_argument("exe", help="目标 exe/dll 路径")
    p.add_argument("-o", "--output", help="输出工程文件（默认 <exe>.zhres-project.json）")
    p.add_argument("--force", action="store_true",
                   help="检测到壳/保护特征时仍继续（不推荐）")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("translate", help="按优先级填充译文（TM/术语/LLM）")
    p.add_argument("project", help="工程文件路径")
    p.add_argument("--llm", action="store_true", help="启用 LLM 批量翻译（默认离线）")
    p.add_argument("--glossary", help="术语表（TOML/JSON）")
    p.add_argument("--tm", help="翻译记忆数据库（SQLite）")
    p.add_argument("--batch-size", type=int, help="LLM 批大小（覆盖配置）")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印将发生的动作与请求样例，不写文件、不联网")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("export", help="导出 CSV（UTF-8 with BOM，Excel 直开）")
    p.add_argument("project", help="工程文件路径")
    p.add_argument("--csv", required=True, help="输出 CSV 路径")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="把人工翻译的 CSV 合并回工程")
    p.add_argument("project", help="工程文件路径")
    p.add_argument("file", help="CSV 文件路径")
    p.add_argument("--glossary", help="术语表（用于导入校验）")
    p.add_argument("--tm", help="翻译记忆数据库（导入译文写入 TM）")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("writeback", help="把已翻译条目写回目标文件（自动备份+校验）")
    p.add_argument("project", help="工程文件路径")
    p.add_argument("--exe", help="目标文件路径（默认用工程内记录）")
    p.add_argument("--no-backup", action="store_true",
                   help="不创建备份（必须同时给 --yes-i-know）")
    p.add_argument("--yes-i-know", action="store_true",
                   help="确认关闭备份的风险")
    p.add_argument("--no-font", action="store_true", help="不做对话框字体补丁")
    p.add_argument("--font", help="字体补丁目标字体（默认 Microsoft YaHei UI）")
    p.add_argument("--force", action="store_true",
                   help="目标文件 SHA-256 不符时仍继续（不推荐）")
    p.set_defaults(func=cmd_writeback)

    p = sub.add_parser("verify", help="复核目标文件与工程一致")
    p.add_argument("project", help="工程文件路径")
    p.add_argument("--exe", help="目标文件路径（默认用工程内记录）")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="从备份还原目标文件")
    p.add_argument("exe", help="目标 exe/dll 路径")
    p.add_argument("--backup", help="备份文件（默认自动定位）")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("report", help="工程状态统计报告")
    p.add_argument("project", help="工程文件路径")
    p.add_argument("--tm", help="翻译记忆数据库（显示条数）")
    p.set_defaults(func=cmd_report)

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
        if getattr(args, "batch_size", None):
            config.setdefault("llm", {})["batch_size"] = args.batch_size
        return args.func(args, config)
    except ZhresError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_TARGET
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
