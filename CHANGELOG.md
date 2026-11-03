# 更新日志

## 1.0.0（2026-11-03）

首个正式版本。

### 新增

- **提取**（`zhres extract`）：RT_STRING 字符串表、RT_MENU 菜单
  （普通模板 + MENUEX）、RT_DIALOG 对话框（DLGTEMPLATE +
  DLGTEMPLATEEX）、Delphi DFM（RT_RCDATA 中 TPF0 签名）；
  加壳/保护特征检测（节名黑名单，命中默认拒绝）；自动跳过规则
  （纯数字/标点等，记录原因）；壳样本与大 RCDATA 的内存安全处理
  （先读 4 字节签名）。
- **翻译工作流**：翻译工程 JSON（跨 extract 稳定 id）；人工 CSV
  导入/导出（UTF-8 with BOM，Excel 直开）；SQLite 翻译记忆
  （WAL，human 永远覆盖 machine）；术语表（TOML，强制映射 +
  占位符保护记号）；可选 LLM 批量翻译（OpenAI 兼容端点，指数退避，
  同文去重，断点续跑，`--dry-run` 零网络）。
- **校验**（translate/import 时执行）：占位符集合一致（error）、
  快捷键 `&X` 保留（error）、术语缺失（warning）、快捷键字母冲突
  （warning）；error 阻断该条并写入 notes。
- **回写**（`zhres writeback`）：先核对目标 SHA-256；强制备份
  （`--no-backup` 需 `--yes-i-know`）；逐三元组 `UpdateResourceW`，
  任一失败立即丢弃修改（文件保持原样）；对话框字体补丁为
  Microsoft YaHei UI（charset 134，记录清单进工程）；超长译文
  警告；成功后自动 `verify`。
- **复核 / 还原**：`verify` 重新提取对照（译文一致 + 资源集合
  一致）；`restore` 一键还原（备份损坏拒绝，还原前另存当前文件，
  SHA-256 循环校验）。
- **报告**（`zhres report`）：状态统计、翻译覆盖率、跳过原因分布、
  TM 条数、字体补丁清单。
- **图形界面**（`zhres-gui` / `zhres gui`）：tkinter 实现，零新增依赖——
  选择 exe → 提取 → 条目表格内双击直译（含占位符/快捷键实时校验）→
  自动翻译 → 回写 → 校验 → 还原；耗时操作后台线程化，日志实时滚动。
- **本机软件扫描（傻瓜式入口）**：GUI 内一键扫描已安装程序（注册表
  卸载表 + 开始菜单快捷方式解析 + 指定文件夹深度扫描），双击即自动
  提取；翻译记忆默认启用（~/.zhres/tm.db）；权限不足/程序占用给中文
  提示。
- **独立 exe 发布**：PyInstaller 打包 `zhres-gui.exe`（图形版）与
  `zhres.exe`（命令行版）单文件可执行，免 Python 环境；构建入口
  `packaging/entry_*.py`，`pip install -e .[build]` 提供构建工具。
- **Demo**：`demo/DemoApp` 纯 Win32 英文演示程序（43 条界面文本，
  双构建脚本）与 `scripts/demo.sh` 一键端到端演示。
- **文档**：中文 README（5 分钟上手 + FAQ + 法律说明）、四份格式
  笔记（含实测踩坑）、架构与 v2 扩展指南、决策记录。

### .NET / Qt / 壳检测 v2（本轮追加）

- **.NET WinForms 适配器**：CLR 托管资源解析（COR20 头 → 托管资源
  blob → .resources 键值），String 值可翻译；PE 手术回写（节内余量
  原位 → 扩展尾节 → 追加 .zres 节，三级策略）；强名/Authenticode
  警告；回写结果用官方 ResourceReader 交叉验证（csc 生成样本）；
  WPF BAML 识别到即原样保留
- **Qt .qm 适配器**（独立文件模式）：Qt6 块/记录格式（Language/
  Contexts/Hashes/Messages/NumerusRules），Translation 记录替换
  （UTF-16BE），PySide6 真实样本 2/2 字节还原
- **壳检测 v2**：可执行节熵值分析（≥7.4 判加密/压缩）、TLS 回调
  （自校验/反调试警告）、overlay 识别；分级 clean/suspicious/packed
- verify 语义扩展：QM 等源文不变格式按译文记录对比
- 文档：docs/formats/DOTNET.md、docs/formats/QM.md

### 质量

- 115 项自动化测试（pytest，全离线）：四类资源字节级 round-trip
  为基石——MENUEX 经 195 个真实系统资源 100% 还原，DFM 经 19 个
  真实 Delphi/Lazarus 样本 100% 还原；合成 PE fixture 全流程
  （extract→import→writeback→verify→restore，SHA-256 循环一致）；
  失败路径（回写回滚、备份损坏拒绝还原）专项覆盖。
- 运行时零第三方依赖（仅 Python 标准库，3.10/3.11/3.12）。

### 已知限制

见 README「已知限制」一节（无字体块对话框不新增字体、不修改布局、
restore 备份核对依赖默认工程路径等）。
