# zhres 1.0.0 交付报告

> 日期：2026-11-03　平台：Windows 11 x64 (10.0.26200)　Python 3.12.10

## 验收清单（任务书 §14 逐项）

| 项 | 结果 | 证据 |
|---|---|---|
| A1 干净 venv 安装 | ✓ | `py -3.12 -m venv` 全新环境 `pip install -e .` 成功；`zhres --help` 输出完整中文帮助（8 个子命令）；`zhres.__version__` = 1.0.0 |
| A2 pytest 全绿 | ✓ | **114 passed, 1 skipped**；唯一 skip 为 `live` 标记的真实端点烟雾测试（需 `ZHRES_API_KEY`+`ZHRES_LIVE` 环境变量，默认无 key，符合任务书 §10.6） |
| A3 四类资源 round-trip 字节级 | ✓ | 合成 fixture：RT_STRING / MENU（普通+MENUEX）/ DIALOG（标准+EX）/ DFM 全部 `serialize(parse(x)) == x`；真实样本：**MENUEX 195 个系统资源 100%**（shell32/user32/webcheck 等）、**DFM 19 个真实样本 100%**（Inno=Delphi 系 + Cheat Engine=Lazarus 系，已提交 tests/fixtures/）、对话框经 windres 编译产物验证；demo.exe 资源随全流程测试覆盖 |
| A4 合成 PE 离线全流程 | ✓ | `test_full_offline_pipeline`：extract → export → import → writeback → verify → restore，SHA-256 循环一致 |
| A5 demo 端到端 | ✓ | `scripts/demo.sh` 全绿（43 条提取 → 43 条导入 → 5 资源回写 → 字体补丁 → verify 通过 → restore 还原）。**中文界面证据**：a) UIA 实测运行中进程——原始版状态栏文本 `Ready` → 翻译版 `就绪`；b) 真实 `LoadMenuW` 枚举翻译版 exe 菜单树：文件(&F)/新建(&N)/打开(&O)/保存(&S)/另存为(&A)/退出(&X)/编辑(&E)/撤销(&U)/剪切(&T)/复制(&C)/粘贴(&P)/全选(&A)/帮助(&H)/关于 DemoApp(&A)，快捷键字母全部保留；c) 翻译版 exe 正常启动运行（重写 PE 有效性） |
| A6 回写失败路径 | ✓ | `test_writeback_failure_keeps_target_intact`：构造引用不存在资源的坏条目 → writeback 拒绝（退出码 2）→ 目标文件 SHA-256 逐字节一致；ResourceUpdater 失败路径统一 `EndUpdateResourceW(TRUE)` 丢弃 |
| A7 备份损坏 → restore 拒绝 | ✓ | `test_corrupt_backup_rejected`：篡改备份 1 字节 → restore 抛"备份文件已损坏"，当前文件未被破坏 |
| A8 `--dry-run` 无网络无写盘 | ✓ | `test_translate_dry_run_no_write`（工程文件字节不变 + 输出 dry-run 报告）；`test_dry_run_no_network`（urlopen 被替换为立即爆炸，未触发） |
| A9 壳样本拒绝 | ✓ | `test_extract_packed_rejected`：手工构造含 UPX1 节名的 PE → extract 退出码 2 并列出命中节名；`--force` 可继续 |
| A10 校验器分级 | ✓ | `test_validate.py`：占位符丢失/多余（error）、快捷键 `&` 丢失（error）、术语缺失（warning）、快捷键冲突（warning）、空译文（error）——error 阻断写入（translate/import 均验证），warning 汇总打印 |
| A11 文档齐全 + 命令可执行 | ✓ | README 5 分钟上手 7 条命令在干净目录逐条真实运行成功（抽查还发现并修正了工程文件名不一致的文档 bug）；FAQ 6 问；法律说明在 README 与 extract 首次运行输出 |
| A12 法律提示 | ✓ | 新环境首次 `zhres extract` 输出法律与道德提示（标记文件 `~/.zhres/.legal-notice-shown`）；README 顶部与文末双处说明 |

## 测试汇总

- **115 项**：114 通过 + 1 跳过（live 烟雾测试，无 API key）
- 测试面：单测（字符串表/菜单/对话框/DFM/TM/术语/校验/LLM mock）+ round-trip 基石 + 合成 PE 集成（真实 Windows API）+ 失败路径专项
- 无 sleep 依赖真实网络的测试；LLM 重试时钟注入（`_sleep` monkeypatch）

## 未完成项与原因

无。任务书 §9 兜底条款未触发（MinGW-w64 经 winget 成功安装，demo 本机构建并实机验证）。

## 已知限制

1. 对话框无字体块时不新增字体（仅替换已有字体），老程序中文可能回落宋体
2. 不修改控件布局/宽度（超长译文仅警告）；RT_VERSION / RT_ACCELERATORS / 位图图标不处理（v2 候选）
3. restore 的备份损坏核对依赖默认工程路径（`-o` 自定义时跳过核对，还原后仍校验 SHA-256）
4. Windows 专属；Python ≥3.10（3.10 无 tomllib 时配置/术语表回退 JSON）
5. 控件 DWORD 对齐以资源数据起始为基准（资源数据在节内非 4 字节对齐的罕见文件可能有偏差）

## v2 建议

- RT_VERSION / RT_ACCELERATORS 适配器（结构简单，适合起步）
- .NET（.resources / 卫星 DLL）、Electron（asar/pak）、Qt（qm）适配器（思路见 docs/architecture.md）
- TM 相似度匹配（编辑距离/模糊命中）；占位符数量校验（当前仅集合）
- 控件布局微调（宽度自适应）；`python -m zhres` 之外的独立可执行发布（PyInstaller）
- CI（GitHub Actions，windows-latest 跑测试套件）

## 关键决策与踩坑记录

- 全部自主决策：`docs/decisions.md`（D001–D046）
- 格式踩坑实录：`docs/formats/`（资源目录偏移"节相对"基准、目录固定四层无中间层、MENUEX 与 Wine 的头字段差异、char_count=码元、DFM 子对象无 0xFF 前缀、TStrings=带点属性名+vaList、vaCollection 保守解析等）
