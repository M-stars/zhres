# zhres

**Windows 静态资源汉化工具**：对 exe/dll 中的界面资源（字符串表、菜单、对话框、Delphi DFM）执行 **提取 → 翻译 → 回写**，让没有官方中文的 Windows 软件持久显示简体中文——无需运行时注入，改前自动备份，可一键还原。

> ⚖️ **法律与道德说明（请务必阅读）**
>
> - zhres 仅用于对**您合法拥有**的软件做**自用汉化**；
> - 修改后的文件**不得再分发**（不得上传、传播、出售修改版）；
> - 本工具**不用于、也不得用于**规避版权保护或技术保护措施；
> - 请勿处理来路不明的程序或破解版程序（它们可能带壳或自校验，见 FAQ）；
> - 汉化成果属于对原软件的修改，其权利义务遵循原软件的最终用户许可协议。

## 原理

Windows PE 文件的界面文本存放在资源段（.rsrc）中。zhres 用系统 API 只读加载文件、解析资源内容体、把译文写回——不修改任何代码逻辑，程序功能与原版完全一致。

```
                       ┌─────────────────────────────────────────┐
                       │                demo.exe                 │
                       └────────────┬─────────────────▲──────────┘
              只读枚举（系统 API）   │                 │ 逐三元组回写
              LoadLibraryExW        │                 │ UpdateResourceW
                       ┌────────────▼─────────────────┴──────────┐
                       │              zhres extract              │
                       │  RT_STRING / RT_MENU / RT_DIALOG / DFM  │
                       └────────────┬────────────────────────────┘
                                    ▼
                        demo.exe.zhres-project.json（翻译工程）
                                    │
              ┌─────────────────────┼──────────────────────┐
              ▼                     ▼                      ▼
        人工翻译 CSV        翻译记忆 / 术语表          可选：LLM 批量翻译
       （Excel 可直开）       （自动命中）              （默认关闭，完全离线可用）
              └─────────────────────┼──────────────────────┘
                                    ▼
                        zhres writeback（自动备份 + 完整性校验）
                                    │
                        备份 demo.exe.zhres-orig.bak
                                    │
              zhres verify（复核）──▶ 界面持久变中文
              zhres restore（一键还原原版，SHA-256 校验）
```

支持的资源类型：字符串表（RT_STRING）、菜单（普通模板与 MENUEX）、对话框（DLGTEMPLATE 与 DLGTEMPLATEEX，含字体补丁）、Delphi DFM（RT_RCDATA 中 TPF0 签名）、.NET 托管资源（.resources，WinForms 字符串）、Qt 翻译文件（独立 .qm）、Unity TextAsset（SerializedFile v22）。其他资源一律原样保留；加壳/保护特征检测（节名 + 熵值 + TLS 回调分级）命中时默认拒绝处理。

## 安装

要求：Windows 10/11、Python 3.10/3.11/3.12。**零第三方运行时依赖**（`pip install zhres` 不会拉入 requests/pywin32 等）。

```bat
git clone https://github.com/M-stars/zhres.git
cd zhres
pip install -e .
zhres --help
```

### 图形界面（傻瓜式）

不想用命令行？两种方式启动图形界面：

```bat
pip install -e .            # 之后：
zhres-gui                   # 或 zhres gui
```

**傻瓜式三步走**：

1. 点 **「扫描本机软件」** —— 自动列出电脑里安装的程序
   （注册表 + 开始菜单双来源，支持搜索；绿色版/游戏点
   「扫描指定文件夹」）；双击任一程序**自动完成提取**
2. 在表格里**双击译文列直接翻译**（或点「自动翻译」用翻译记忆/
   术语表——翻译记忆默认 `~/.zhres/tm.db`，自动创建、越用越准）
3. 点 **「回写」** —— 自动备份原文件 → 写入 → 字体补丁 → 自动校验；
   出问题随时「还原原版」

其余一切自动：耗时操作后台执行、权限不足/程序占用给中文提示、
翻译记忆自动积累。也支持 .qm（Qt）与 .assets（Unity TextAsset）
等独立文件——直接浏览选择即可。

进阶：也可以导出/导入 CSV（Excel 直开）走人工翻译，或接 LLM 自动
翻译（需配置 API key，默认关闭）。

### 免 Python 环境的单文件 exe

无需安装 Python，直接构建独立可执行文件（一次性构建，产物约 13 MB/个）：

```bat
pip install -e .[build]     # 安装 PyInstaller（仅构建期需要，运行时仍零依赖）
build\venv_gui\Scripts\pyinstaller --noconfirm --onefile --windowed --name zhres-gui --icon demo\DemoApp\app.ico packaging\entry_gui.py
build\venv_gui\Scripts\pyinstaller --noconfirm --onefile --console  --name zhres     --icon demo\DemoApp\app.ico packaging\entry_cli.py
:: 产物在 dist\zhres-gui.exe（图形版）与 dist\zhres.exe（命令行版）
```

## 5 分钟上手（以自带 demo 为例）

```bat
:: 0. 构建/获取演示程序（需要 MinGW-w64：windres + gcc）
sh demo\DemoApp\build_mingw.sh

:: 1. 提取界面资源 → 生成翻译工程
zhres extract demo\DemoApp\DemoApp.exe
::  输出：43 条（menu 14 + dialog 5 + string 24），工程已写入
::       demo\DemoApp\DemoApp.exe.zhres-project.json

:: 2a. 离线路径（推荐）：导出 CSV → 填译文 → 导回
zhres export demo\DemoApp\DemoApp.exe.zhres-project.json --csv trans.csv
::    用 Excel/WPS 打开 trans.csv（UTF-8 with BOM，不乱码），
::    在 translation 列填写中文，保存；也可直接使用仓库自带的
::    examples\demo-translations.csv（demo 全量人工译文）。
zhres import demo\DemoApp\DemoApp.exe.zhres-project.json examples\demo-translations.csv --tm tm.db
::    导入 43 条（status=human，已写入翻译记忆）

:: 2b. 自动路径：翻译记忆 + 术语表（离线）
zhres translate demo\DemoApp\DemoApp.exe.zhres-project.json --tm tm.db --glossary examples\glossary.toml.example

:: 2c. 在线路径（可选）：LLM 批量翻译（需配置 zhres.toml 与 API key）
zhres translate demo\DemoApp\DemoApp.exe.zhres-project.json --llm

:: 3. 回写：自动备份 → 写入 → 字体补丁 → 自动校验
zhres writeback demo\DemoApp\DemoApp.exe.zhres-project.json
::    已备份：demo\DemoApp\DemoApp.exe.zhres-orig.bak（SHA-256 校验通过）
::    回写完成：更新 N 个资源；字体补丁：RT_DIALOG/100/1033 → Microsoft YaHei UI
::    自动校验（verify）：通过

:: 4. 运行 demo\DemoApp\DemoApp.exe —— 界面已持久显示中文
::    （菜单：文件/新建/打开/…；About 对话框与状态栏全部中文）

:: 5. 一键还原原版（SHA-256 循环校验）
zhres restore demo\DemoApp\DemoApp.exe --yes
```

也可以一键跑完整演示：`sh scripts/demo.sh`。

### 人工翻译工作流详解（离线主路径）

```bat
:: 1) 导出全部条目为 CSV（UTF-8 with BOM，Excel 双击打开不乱码）
zhres export 我的软件.zhres-project.json --csv 工作表.csv

:: 2) 在 Excel 中逐行填写 translation 列。各列说明：
::    id          —— 不要改！回写的唯一索引（资源类型/资源名/语言/位置）
::    source      —— 原文（只读参考，改了无效）
::    translation —— ★ 你要填的列。留空 = 不翻译（条目保持未翻译；
::                     对已翻译条目填空并把 status 改为 untranslated 可撤销翻译）
::    status      —— 可回改：untranslated（未翻译）/ machine（机器）/ 
::                     human（人工）/ approved（已批准）。
::                     导入时译文非空自动置为 human；approved 的条目
::                     后续 translate 不会覆盖。
::    notes       —— 备注列（校验失败原因也会写回这里）

:: 3) 导回工程（自动校验：占位符/快捷键/术语；error 阻断该条并写明原因）
zhres import 我的软件.zhres-project.json 工作表.csv --tm tm.db

:: 4) 回写并校验
zhres writeback 我的软件.zhres-project.json
```

校验规则：源文与译文的**占位符集合**（`%s` `%d` `%1$s` `{0}` `\n`…）必须一致（error）；源文带 `&X` 快捷键时译文必须保留 `&`（惯例写法 `中文(&X)`，error）；源文含术语而译文不含译名（warning）；同一对话框/菜单内快捷键字母冲突（warning）。

## 命令参考

| 命令 | 参数 | 说明 |
|---|---|---|
| `zhres extract <exe>` | `-o <工程.json>`（默认 `<exe>.zhres-project.json`）`--force` | 只读提取。先做壳检测（命中拒绝，`--force` 继续）。输出统计表 |
| `zhres translate <工程>` | `--llm` `--glossary <f>` `--tm <db>` `--batch-size N` `--dry-run` | 合并优先级：approved > human > TM > 术语强制映射 > LLM > 不动。`--dry-run` 打印动作与请求样例，不写文件不联网 |
| `zhres export <工程>` | `--csv <out.csv>` | 导出 `id,source,translation,status,notes`（UTF-8 with BOM） |
| `zhres import <工程> <csv>` | `--glossary <f>` `--tm <db>` | id 匹配合并；译文非空置 human；写入 TM；未知 id 警告跳过 |
| `zhres writeback <工程>` | `--exe <p>` `--no-backup --yes-i-know` `--no-font` `--font <名>` `--force` | 先核对目标 SHA-256；备份（`--no-backup` 需再给 `--yes-i-know`）；逐三元组回写；任一失败立即回滚且文件保持原样；成功后自动 verify |
| `zhres verify <工程>` | `--exe <p>` | 重新提取目标文件与工程对照：已翻译条目内容一致、资源集合一致 |
| `zhres restore <exe>` | `--backup <f>` `--yes` | 显示当前/备份 SHA-256；还原前把当前文件另存 `*.zhres-before-restore-*.bak`；备份与工程记录不符（损坏）→ 拒绝 |
| `zhres report <工程>` | `--tm <db>` | 状态统计、翻译覆盖率、跳过原因分布、TM 条数、字体补丁数 |

全局参数：`--verbose`（每个资源的解析细节）`--quiet` `--config <path>`。

**退出码**：`0` 成功；`1` 用法/参数错误；`2` 目标文件处理失败（不存在/加壳/哈希不符/回写失败等）；`3` 翻译校验失败（verify 不通过、导入存在 error 条目）。

## 配置参考（zhres.toml）

查找顺序：`--config` > `./zhres.toml` > `~/.zhres/zhres.toml`。Python 3.10（无 tomllib）时可用等价的 `zhres.json`。完整示例见 `examples/zhres.toml.example`。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `languages.source` | `"en"` | 源语言（记录用） |
| `languages.target` | `"zh-CN"` | 目标语言（记录用） |
| `writeback.font` | `"Microsoft YaHei UI"` | 对话框字体补丁目标字体 |
| `writeback.font_charset` | `134` | 字体 charset（134 = GB2312_CHARSET，仅 EX 模板可存） |
| `writeback.backup` | `true` | 是否备份（命令行 `--no-backup` 可覆盖，需 `--yes-i-know`） |
| `llm.enabled` | `false` | LLM 总开关；默认关闭，核心流程完全离线 |
| `llm.base_url` | `"https://api.openai.com/v1"` | OpenAI 兼容端点 |
| `llm.model` | `"gpt-4o-mini"` | 模型名 |
| `llm.api_key_env` | `"ZHRES_API_KEY"` | 读取 API key 的环境变量名 |
| `llm.batch_size` | `20` | 每批条目数（同 context_kind 相邻成批，同文去重） |
| `llm.temperature` | `0.2` | 采样温度 |
| `llm.timeout_seconds` | `60` | 单请求超时 |
| `llm.max_retries` | `3` | 网络错误/5xx 指数退避次数（2s/4s/8s） |

## 翻译记忆与术语表

- **翻译记忆**（`--tm tm.db`）：SQLite 库，human 译文永远覆盖 machine；源文归一化后匹配（忽略首尾/连续空白与弯直引号差异）。每次 `import` 的人工译文自动入库，越用越准。
- **术语表**（`--glossary glossary.toml`）：源文精确命中 `[terms]` 键时**强制**使用对应译名；源文含术语词而译文缺译名时给 warning。送 LLM 前，术语与占位符会被替换为保护记号 `⟦T0⟧`，翻译后回填并校验无遗漏。示例见 `examples/glossary.toml.example`。

## FAQ

**Q：杀毒软件对修改后的 exe 报毒怎么办？**
资源回写改变了文件哈希与部分字节布局，杀软可能启发式误报。处理方式：向杀软提交白名单/误报申诉；对照 SHA-256 确认文件仅经过 zhres 处理（extract 输出与工程文件里都记录了哈希链）。zhres 不加壳、不注入、不修改代码段，误报不构成安全性问题，但**请只对自己合法拥有的软件操作**，并保留原始备份。

**Q：带完整性自校验的程序为什么不行？**
部分程序启动时会校验自身文件哈希/某些段的内容，回写资源会改变文件，导致程序拒绝启动或报"文件已损坏"。zhres 无法也不打算绕过这类保护——这不是本工具的目标。典型的还有带壳程序（UPX/Themida/VMP 等）：壳通常压缩/加密了资源节，zhres 会在 extract 时**检测壳特征并默认拒绝处理**（节名黑名单：UPX0/UPX1/.aspack/.themida/.vmp0/.vmp1/.enigma1/.enigma2/.mpress/.nsp/.petite/.pec1/.mew）。`--force` 可以强行继续，但结果不可保证（资源可能读出来是密文，或程序启动即自毁）。

**Q：控件文本变长了，显示不下怎么办？**
中文通常比英文短，但个别译文可能更长。writeback 会对"译文长度 > 源文长度 × 1.5"的对话框控件文本打印警告（不自动改布局——修改控件坐标超出静态汉化的边界）。应对：a) 换更简洁的译法；b) 用 ResHacker 类工具手动调整控件宽度（本工具不代劳）；c) 接受截断（多数控件不截断，只是换行）。

**Q：ANSI / 非 Unicode 程序的限制？**
Delphi/VC 旧程序的字符串多为 ANSI，按系统代码页（简中环境 = GBK/CP936）解码。若原文含 GBK 无法表示的字符，该条目会被标记 skipped（原因：ANSI(GBK) 解码失败）；译文含 GBK 无法表示的字符（如生僻 Unicode 符号）时回写会跳过该条、保持原文。Unicode 资源（RT_STRING、MENUEX 文本、vaWString/vaUTF8String）无此限制。

**Q：如何翻译 Delphi 程序？**
直接支持。zhres 识别 RT_RCDATA 中以 `TPF0` 开头的 DFM 资源（Delphi 窗体），提取 `Caption` `Text` `Hint` `SimpleText` `Title` 属性与 `Lines.Strings` / `Items.Strings` 字符串列表（id 形如 `Form1/Lines/3`、`Form1/Button1/Caption`）。回写按原值类型编码（vaString 走 GBK，vaWString 走 UTF-16，vaUTF8String 走 UTF-8），未涉及的属性逐字节保持——含 vaBinary/vaCollection 的复杂窗体也已通过字节级 round-trip 验证。Lazarus/FreePascal 程序同格式兼容。

**Q：哪些程序类型不支持？**
WPF 的 BAML、Electron（asar/pak）、Java、Unity 的 MonoBehaviour 内嵌字符串与 UnityFS 包裹文件、Unreal 等暂不支持，遇到时按不认识的资源原样保留并跳过，不会损坏文件。扩展点见 `docs/architecture.md`。

**Q：回写失败会弄坏文件吗？**
不会。回写通过 `BeginUpdateResourceW/UpdateResourceW/EndUpdateResourceW` 系统 API 完成：任一三元组失败立即 `EndUpdateResourceW(TRUE)` 丢弃全部修改，目标文件保持原样（有自动化测试覆盖此路径）；此外回写前强制备份，失败后可 `restore`。

**Q：为什么有的条目被跳过（skipped）？**
纯数字/标点（如 "1.5"、"©2024"）、纯空白、单个字符、`&` 开头且 ≤2 字符的快捷键占位串——这些无需翻译。每条 skipped 都记录原因，`zhres report` 可看分布。menu 上下文中的 `&X` 单字符快捷键定义会保留。

## 技术要点

- **零第三方依赖**：argparse / ctypes / sqlite3 / csv / struct / tomllib（3.11+）全标准库
- **系统 API 读写**：`LoadLibraryExW(LOAD_LIBRARY_AS_DATAFILE|IMAGE_RESOURCE)` 只读枚举，`UpdateResourceW` 逐三元组回写——资源目录重建、节扩展等高危细节全部交给 Windows
- **格式层字节级 round-trip**：`serialize(parse(x)) == x` 是质量基石。真实样本验证：MENUEX 195 个系统资源 100% 还原；Delphi/Lazarus DFM 19 个样本 100% 还原；手工构造的字节可被真实 `LoadMenuW` 加载
- **测试**：`pip install -e .[dev] && pytest -q`（合成 PE fixture 全流程离线测试，见 `tests/`）
- **格式笔记**：`docs/formats/`（RESOURCEDIR / MENU / DIALOG / DFM，含实测踩坑记录）

## 已知限制

- 对话框没有字体块时不新增字体（只替换已有字体），部分老程序中文可能显示为宋体
- 不修改控件布局/宽度（仅警告超长译文）；不改 RT_VERSION/快捷键表/位图图标（v2 候选）
- 自定义二进制资源格式（非四类目标）原样保留，不解析
- restore 的备份损坏核对依赖默认路径的工程文件（`<exe>.zhres-project.json`），用 `-o` 自定义工程名时该核对跳过（还原后仍校验 SHA-256 一致）
- Windows 专属（ctypes + Win32 API），不支持 Linux/macOS

## .NET / Qt / Unity 程序适配（v2 新增）

- **.NET WinForms**：`zhres extract app.exe` 直接解析 CLR 托管资源
  （.resources），提取 `Control.Text` 等字符串键；回写通过 PE 手术
  （原位或扩节）+ COR20 头修补。回写结果用官方 ResourceReader 交叉
  验证。强名签名 / Authenticode 程序集修改后验签会失败（有警告）。
  WPF 的 BAML 资源识别到即原样保留（暂不支持修改）。
- **Qt**：对独立 `.qm` 翻译文件（Qt 程序 `translations/` 目录常见）
  直接 extract/import/writeback/verify（整文件原子重写，可变长）。
  已用 PySide6 真实样本验证。qrc 内嵌 .qm 暂不支持。
- **Unity**：对 SerializedFile v22（Unity 2022+/团结引擎）的
  `.assets` 文件提取/回写 **TextAsset**（本地化 JSON/CSV、剧情文本
  常见载体）；追加式对象重写，UnityPy 交叉验证。类型树文件与
  UnityFS 包裹暂不支持。
- **壳/保护检测 v2**：节名黑名单之外，新增可执行节熵值分析（加密/
  压缩特征 → 拒绝）、TLS 回调检测（自校验/反调试警告）、overlay
  识别。分级：clean / suspicious（警告）/ packed（默认拒绝）。

## v2 路线（候选）

RT_VERSION / RT_ACCELERATORS 翻译、WPF BAML、Electron（asar/pak）、
qrc 内嵌 .qm、ManifestResource 名字解析、控件布局微调、TM 相似度匹配（编辑距离）。

## 许可与法律

本项目代码采用 MIT 许可。**工具的输出不受此许可授权**：使用本工具产生的修改版程序文件仅供使用者本人使用，不得再分发；使用者需自行确保符合原软件的许可协议与当地法律。本工具不用于规避版权保护或任何技术保护措施。
