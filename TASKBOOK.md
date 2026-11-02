# 开发任务书：zhres —— Windows 静态资源汉化工具（路线 A 成品）

> 使用说明：本文件是一份自包含的完整开发提示词。把它整份交给一个编码 Agent（新会话），Agent 应一次性交付下述全部内容，完成即成品，无需向用户追问。
> **执行目录：`D:\zhres`**（该目录已 `git init` 并配置 GitHub 远程仓库），Phase 0 直接在其上提交，不要重新初始化。

---

## 0. 使命

开发一个生产可用的 Windows 命令行工具 **zhres**，实现"对 PE 文件（exe/dll）中的界面资源做 **提取 → 翻译 → 回写**"的完整静态汉化流水线。用户对一个英文界面的 Windows 桌面软件执行一次 zhres 流程后，该软件界面**持久变为中文**——不需要每次启动时注入、不改变软件功能、随时可一键还原为原版。

**"成品"的定义**：端到端可演示、测试全绿、文档完整、失败路径全部有兜底。不是原型，不是骨架。

---

## 1. 范围

### 1.1 v1 必须实现（全部）

| 模块 | 内容 |
|---|---|
| PE 资源提取 | RT_STRING(6) 字符串表、RT_MENU(4) 菜单（普通模板 + MENUEX 两种格式）、RT_DIALOG(5) 对话框（DLGTEMPLATE + DLGTEMPLATEEX 两种格式）、Delphi DFM（RT_RCDATA 中以 `TPF0` 签名开头的资源） |
| 翻译工作流 | 翻译工程 JSON、人工翻译 CSV 导入/导出、翻译记忆（SQLite）、术语表、可选 LLM 批量翻译（OpenAI 兼容接口） |
| 回写 | 通过系统 API 重建资源、对话框字体替换为中文可用字体、备份/还原/完整性校验 |
| 打包检测 | 识别常见壳/保护特征并明确警告 |

### 1.2 明确不做（遇到时警告而非崩溃，文档中说明原因）

- 游戏引擎资源（Unity/Unreal 等）、.NET 程序集、Electron/Qt 资源 —— 架构上预留适配器扩展点（见 §3.2），v1 不实现。
- 加壳 / DRM / 完整性自校验程序 —— 本工具**不提供、不讨论任何绕过手段**；检测到壳特征时警告"该文件可能无法安全处理"。
- RT_VERSION（VERSIONINFO）、RT_ACCELERATORS、位图/图标资源 —— v1 跳过，文档记录为 v2 候选。
- 工具只面向**用户合法拥有的软件的自用汉化**。README 中必须包含法律说明：修改后的文件不得再分发；本工具不用于规避版权保护。

### 1.3 核心用户故事（验收以这三条为准）

1. `zhres extract demo.exe` → 生成 `demo.zhres-project.json`，其中包含 demo 菜单、对话框、字符串表的全部英文条目及上下文。
2. `zhres translate`（离线模式，用人工导入或翻译记忆）或 `zhres translate --llm`（在线）→ 条目变为已翻译状态，占位符与快捷键校验通过。
3. `zhres writeback demo.exe` → 自动备份原文件 → 回写 → `zhres verify` 通过 → 启动 demo.exe **界面显示中文**；`zhres restore demo.exe` 还原后与原始文件 SHA-256 一致。

---

## 2. 技术决策（已锁定，不要重新讨论）

1. **语言与运行时**：Python ≥ 3.10，**仅标准库**（argparse、ctypes、json、sqlite3、csv、hashlib、struct、logging、pathlib、urllib.request、datetime、tomllib[3.11+]）。`pyproject.toml` 提供 `pip install -e .` 与 console_script 入口 `zhres`。测试用 pytest（唯一 dev 依赖）。
2. **PE 枚举与回写一律走系统 API（ctypes → kernel32），禁止手写 .rsrc 目录重建**：
   - 读取：`LoadLibraryExW(path, NULL, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE)`（0x2 | 0x20，不执行任何代码）→ `EnumResourceTypesW` / `EnumResourceNamesW` / `EnumResourceLanguagesW` → `FindResourceExW` / `LoadResource` / `LockResource` / `SizeofResource`。
   - 写入：`BeginUpdateResourceW` → `UpdateResourceW`（按 type/name/language 三元组逐个更新）→ `EndUpdateResourceW(hUpdate, FALSE)`。
   - 理由：系统 API 负责资源目录重建、对齐、节扩展等全部边界情况，自写 PE 资源目录重建是已知的高危错误源。
3. **资源内容体（字符串表项、菜单/对话框模板、DFM）用 `struct` 手工解析与序列化**——系统 API 不提供结构化访问，这是本工具的核心实现量。
4. **LLM 网络调用用 `urllib.request`**，OpenAI 兼容 `/chat/completions`。**核心工作流必须完全离线可用**（人工 CSV + 翻译记忆即可完成翻译），LLM 是可选增强。
5. **配置文件**：`zhres.toml`（Python ≥3.11 时用 tomllib）；若运行时 Python 为 3.10（无 tomllib），回退接受 `zhres.json`。两者字段一致。
6. 不调用任何外部二进制（ResourceHacker 等），全流程自包含。
7. 面向用户的输出、日志、文档、注释用中文；代码标识符用英文。

---

## 3. 仓库结构

```
zhres/
├── pyproject.toml
├── README.md                  # 中文，成品文档（见 §12）
├── CHANGELOG.md
├── src/zhres/
│   ├── __init__.py            # 版本号
│   ├── cli.py                 # argparse 子命令分发（§7）
│   ├── config.py              # zhres.toml/json 加载 + 默认值
│   ├── winapi.py              # 全部 ctypes 声明与封装（附录 C 的坑都在这里解决）
│   ├── pe.py                  # 资源枚举：type/name/lang → 原始字节
│   ├── packerdetect.py        # 壳特征检测（§6）
│   ├── adapters/
│   │   ├── __init__.py        # 适配器注册表
│   │   ├── base.py            # ResourceAdapter 协议
│   │   ├── stringtable.py     # RT_STRING
│   │   ├── menu.py            # RT_MENU 两种格式
│   │   ├── dialog.py          # RT_DIALOG 两种格式 + 字体补丁
│   │   └── dfm.py             # Delphi DFM（binary↔text）
│   ├── project.py             # 工程 JSON 读写、Entry 数据类、id 规则
│   ├── translate.py           # 翻译编排：术语→TM→LLM→人工 合并策略
│   ├── glossary.py
│   ├── tm.py                  # SQLite 翻译记忆
│   ├── llm.py                 # OpenAI 兼容客户端 + 批处理 + 重试
│   ├── validate.py            # 占位符/快捷键/术语一致性校验
│   ├── writeback.py           # 备份、sha256 校验、UpdateResource 循环、字体补丁
│   ├── restore.py
│   └── verify.py              # 回写后复核
├── tests/
│   ├── conftest.py
│   ├── fixture_pe.py          # 纯 Python 合成最小 PE（含全部目标资源类型），见 §11
│   ├── test_stringtable.py
│   ├── test_menu.py
│   ├── test_dialog.py
│   ├── test_dfm.py
│   ├── test_roundtrip.py      # 字节级 round-trip（本工具的质量基石）
│   ├── test_validate.py
│   ├── test_tm_glossary.py
│   ├── test_backup_restore.py
│   └── test_integration.py    # 合成 PE 全流程（离线）
├── demo/DemoApp/              # 英文 Win32 演示程序（§10）
├── examples/
│   ├── zhres.toml.example
│   ├── glossary.toml.example
│   └── demo-translations.csv  # demo 全量人工译文（离线演示用）
├── docs/
│   ├── formats/STRING.md      # 实现中沉淀的格式笔记（含偏移图）
│   ├── formats/MENU.md
│   ├── formats/DIALOG.md
│   ├── formats/DFM.md
│   ├── architecture.md        # 适配器扩展指南（v2 如何加 .NET/Electron/Qt）
│   └── decisions.md           # 所有自主决策的记录（§16）
└── scripts/demo.sh            # 端到端演示脚本
```

### 3.2 适配器协议（为 v2 预留，v1 只实现上述四个）

```python
class ResourceAdapter(Protocol):
    resource_types: tuple[int, ...]          # 关心的 RT_* 序号
    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool: ...
    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]: ...
    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        """返回替换后的资源体字节；未修改时必须原样返回"""
```

注册表按 `resource_types` 分发；无法解析的资源一律**原样保留**并记录警告，绝不因不认识的资源失败。

---

## 4. 数据格式（精确 schema）

### 4.1 工程 JSON（`<name>.zhres-project.json`）

```json
{
  "format_version": 1,
  "tool_version": "1.0.0",
  "created_at": "2026-01-01T12:00:00+08:00",
  "target": {
    "path": "C:/abs/path/demo.exe",
    "sha256": "…64 hex…",
    "size": 123456,
    "machine": "x64",
    "backup_path": "C:/abs/path/demo.exe.zhres-orig.bak",
    "backup_sha256": "…"
  },
  "languages": { "source": "en", "target": "zh-CN" },
  "resources_seen": {"RT_STRING": 12, "RT_MENU": 3, "RT_DIALOG": 2, "RT_RCDATA_DFM": 1, "RT_RCDATA_skipped": 4, "RT_VERSION_skipped": 1},
  "entries": [ /* Entry 数组，见 4.2 */ ]
}
```

### 4.2 Entry

```json
{
  "id": "RT_DIALOG/129/1033/2",
  "resource_type": "dialog",
  "type_id": 5,
  "resource_name": "129",
  "language": 1033,
  "context": {
    "kind": "menu_title|menu_item|dialog_caption|dialog_control_text|string_table|dfm_property",
    "location": "IDD_ABOUTBOX / IDC_STATIC_3",
    "dialog_title": "About DemoApp",
    "control_class": "Button",
    "dfm_property": "Caption"
  },
  "source": "E&xit",
  "translation": null,
  "status": "untranslated|skipped|machine|human|approved",
  "skip_reason": "",
  "max_len": null,
  "notes": ""
}
```

**id 规则**：`RT_<TYPE>/<resource_name>/<language>/<position>`。position 对字符串表是 block 内序号（0-15），对 menu 是先序遍历序号，对 dialog 是控件序号（caption 专条），对 DFM 是属性路径（如 `Form1/Lines/3`）。id 在同一工程内必须唯一且**跨 extract 稳定**（同一 exe 两次 extract 的 id 集合必须一致），verify 依赖这一点。

**自动 skipped 规则**（不进翻译队列）：空串、纯空白、纯数字/标点、单个字符（快捷键定义除外——menu 中单个 `&` 后字符的项保留）、无字母且无 CJK 的串、`&` 开头且长度≤2。每条记录 skip_reason。

### 4.3 翻译记忆 SQLite（`tm.db`）

```sql
CREATE TABLE translations (
  source_norm TEXT NOT NULL,
  context_kind TEXT NOT NULL DEFAULT '',
  translation TEXT NOT NULL,
  origin TEXT NOT NULL,          -- human|machine
  hits INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_norm, context_kind)
);
```

`source_norm` = 去首尾空白、连续空白折叠为单空格、统一弯引号为直引号后的源文。查询顺序：`(source_norm, context_kind)` 精确 → 仅 `source_norm`。人工来源永远覆盖机器来源。

### 4.4 术语表（TOML）

```toml
[terms]
"File" = "文件"
"Exit" = "退出"
[protect]
patterns = ["%s", "%d", "%1$s", "\\n", "&"]
```

应用方式：翻译前把术语与占位符替换为保护记号（`⟦T0⟧`），翻译后回填并校验无遗漏。

---

## 5. 资源格式实现规格

> **通用铁律（适用于 §5 全部小节）**：
> (a) 每个序列化器必须先通过 **round-trip 测试**：`serialize(parse(x)) == x`（字节级）在未修改任何文本时成立；demo exe 的真实资源与手工合成 fixture 都要做。
> (b) MS 文档对个别字段（尤其 MENUEX 与 DLGTEMPLATEEX 的标志位/字段宽度）描述分散，实现前先核对：MS Learn 关键词 `MENUITEMTEMPLATE`、`DLGTEMPLATE`、`DLGTEMPLATEEX`、`DLGITEMTEMPLATEEX`，辅以 Wine 源码 `dlls/user32/menu.c`（`LoadMenuIndirectW`）交叉验证。**理解是否正确最终由 round-trip 测试裁决**：若解析→重序列化无法字节还原，说明理解有误，回去重读，不许通过放宽比较来"让测试通过"。
> (c) 所有解析错误必须带上下文（资源 type/name/lang + 字节偏移）抛出，禁止裸 except。
> (d) 遇到解析失败的资源：该资源整体原样保留（round-trip 天然满足），所有条目标记 skipped，警告列出。

### 5.1 RT_STRING（字符串表）

- 每 block 固定 16 条；每条 = `WORD char_count + UTF-16LE 字符`（**无 NUL 结尾**）；空条目 char_count=0。
- resource_name 即 block 序号；同一 block 内 position 0-15。
- 回写：整 block 重序列化（即使只改一条）；**block 内条目数保持 16**，缺失条目按空串补齐。
- 长度无硬限制，条目可变长。

### 5.2 RT_MENU

- **普通模板（version 0）**：头 `WORD version=0; WORD offset;`（offset 之后可能有 helpid 字节串，按 offset 跳过），随后为项序列：`WORD mtOption; [WORD mtID（仅非 popup 项）;] UTF-16 字符串 + NUL`。模板选项位与运行时 MF_* 同值，其中 `MF_END(0x80)`、`MF_POPUP(0x10)`、`MF_SEPARATOR(0x800)` 是解析关键；popup 项无 mtID、其后紧跟其子项；`MF_END` 标记本层结束。
- **MENUEX（version 1）**：头 `WORD wVersion=1; WORD wOffset; DWORD dwHelpId;`，项结构 `DWORD type; DWORD state; DWORD id; WORD flags; UTF-16 字符串 + NUL; [DWORD helpId（flags&0x80 时）]`；嵌套结束以 flags&0x01 标记。**此格式实现前必须按 (b) 核对文档**；demo 用普通模板，MENUEX 由手工合成 fixture 覆盖并在 docs 中注明来源。
- 提取：popup 标题 → `menu_title`，叶子项 → `menu_item`，分隔符跳过。
- 回写：**必须保持模板格式版本与原文件一致**（普通→普通，EX→EX），未修改项逐字节保持。

### 5.3 RT_DIALOG

- **DLGTEMPLATE**：`DWORD style; DWORD exStyle; WORD cdit; WORD x,y,cx,cy;` + menu 数组（`WORD 0`=无 / `WORD 0xFFFF + WORD 序数` / UTF-16+NUL）+ class 数组（同规则）+ title（UTF-16+NUL）+ 若 `style & DS_SETFONT(0x40)`：`WORD pointsize; WORD weight; BYTE italic; BYTE charset; UTF-16 typeface+NUL`；随后每控件按 **DWORD 对齐**：`DWORD style; DWORD exStyle; WORD x,y,cx,cy; WORD id;` + class 数组 + title 数组（`0xFFFF+WORD` 表示序数资源如图标，非文本）+ `WORD extraCount + extraCount 字节`。
- **DLGTEMPLATEEX**：头 `WORD dlgVer=1; WORD signature=0xFFFF; DWORD helpID; DWORD exStyle; DWORD style; WORD cdit; WORD x,y,cx,cy;`，其余结构同上按 EX 规则；**字段宽度以文档核对 + round-trip 裁决**。
- 提取：对话框 title → `dialog_caption`（location 记 dialog name）；控件 title（非 `0xFFFF` 序数）→ `dialog_control_text`，记录 control class。class 数组本身不提取。
- **字体补丁**（回写默认开启，`--no-font` 关闭）：对已有 `DS_SETFONT` 的对话框，把 typeface 替换为 `Microsoft YaHei UI`、charset=134（GB2312_CHARSET），size/weight/italic 保持。对没有字体块的对话框 v1 **不新增**（记入已知限制）。同时记录一份"字体补丁清单"进工程 JSON（`target.font_patches`）。
- 控件文本变长可能超控件宽度：writeback 时对"中文长度 > 源文长度 × 1.5"的 `dialog_control_text` 打印警告，不自动改布局。

### 5.4 Delphi DFM

- 识别：RT_RCDATA 且前 4 字节为 `TPF0`；其余 RCDATA 一律跳过（可能高达数百 MB，禁止整体读入内存后再判断——先读前 4 字节）。
- binary 结构（实现前对照开源实现 dfm2txt 逻辑复核）：根对象 = 签名 `TPF0` + 类名（BYTE 长度 + ANSI 字节）+ 对象名（同格式）+ 属性列表 + 子对象列表。属性 = 名称长度字节（0=属性列表结束）+ 名称 + 值类型字节 + 值。值类型（TypInfo.TValueType）：`vaList=1, vaInt8=2, vaInt16=3, vaInt32=4, vaExtended=5, vaString=6, vaIdent=7, vaFalse=8, vaTrue=9, vaBinary=10, vaSet=11, vaLString=12, vaNil=13, vaCollection=14, vaSingle=15, vaCurrency=16, vaDate=17, vaWString=18, vaInt64=19, vaUTF8String=20, vaDouble=21`。子对象列表：每个子对象前有 `0xFF` 标记字节，列表以 `0x00` 结束。
- 提取白名单属性：`Caption`、`Text`、`Hint`、`SimpleText`、`Title`、字符串列表类的 `Items.Strings` / `Lines`（逐条，id 用 `路径/序号`）。嵌套对象属性（如 `EditLabel.Caption`）按路径进 id。
- v1 只处理 Unicode 字符串属性（vaWString/vaUTF8String）与 ANSI vaString（按 CP_ACP→尝试 GBK 解码读取、按 GBK 编码回写，转换失败则 skipped）。
- 回写：text→binary 转回；**未修改的属性字节必须逐字节保持**（round-trip 测试覆盖含 vaBinary/vaCollection 的复杂样本）。

### 5.5 packerdetect（壳检测）

- 读节表（解析 `e_lfanew` → PE 头 → 节表，纯 struct，只读）。
- 节名黑名单（大小写不敏感）：`UPX0 UPX1 .aspack .adata .themida .vmp0 .vmp1 .enigma1 .enigma2 .mpress1 .mpress2 .nsp0 .nsp1 .petite .pec1 .mew`。
- 命中 → extract 默认**拒绝执行**并解释原因；`--force` 可继续（警告逐条列出）。写进 README 的 FAQ。
- 另检测：资源目录为空 → 报"未发现可翻译资源"正常退出（退出码 0，entries 为空，报告说明）。

---

## 6. CLI 规格

全局参数：`--verbose` / `--quiet` / `--config <path>`。默认配置查找：`--config` > `./zhres.toml` > `~/.zhres/zhres.toml`。所有命令输出中文；退出码：`0` 成功、`1` 用法/参数错误、`2` 目标文件处理失败、`3` 翻译校验失败。

| 命令 | 参数 | 行为要点 |
|---|---|---|
| `zhres extract <exe>` | `-o <project.json>`（默认 `<exe>.zhres-project.json`）`--force` | 只读目标文件；先跑 packerdetect；生成工程 JSON；打印各类型条目数统计表 |
| `zhres translate <project>` | `--llm` `--glossary <f>` `--tm <db>` `--batch-size N` `--dry-run` | 合并顺序：已有 translation(approved/human) 不动 → TM 精确 → 术语强制映射 → LLM（仅 `--llm` 时）→ 保持 untranslated。`--dry-run` 打印将发生的动作与 LLM 请求样例（1 条），不写文件 |
| `zhres export <project>` | `--csv <out.csv>` | 导出 `id,source,translation,status,notes`，**UTF-8 with BOM**（Excel 直开），已翻译条目也导出（status 列可回改） |
| `zhres import <project> <file>` | `--csv` | 合并回工程：id 匹配；CSV 译文非空则写入并置 `status=human`；id 不存在→警告跳过；**写入 TM**（origin=human） |
| `zhres writeback <project>` | `--exe <path>`（默认工程内路径）`--no-backup` `--no-font` `--font <name>` | 先核对目标文件 SHA-256 与工程记录一致（不符→拒绝，`--force` 覆盖核对）；强制备份（`--no-backup` 需再显式 `--yes-i-know`）；逐三元组 `UpdateResourceW`；任一资源失败→立即 `EndUpdateResourceW(h, TRUE)` 回滚并报错；成功后自动运行 verify |
| `zhres verify <project>` | `--exe <path>` | 重新 extract 目标文件→按 id 对照工程：已翻译条目必须存在且译文相等；资源 (type,name,lang) 集合与工程一致；任一失败→退出码 3 并列出差异 |
| `zhres restore <exe>` | `--backup <f>`（默认同目录唯一 .bak）`--yes` | 显示当前/备份的 SHA-256 与时间戳；`--yes` 跳过确认；还原后校验 SHA-256 等于备份记录 |
| `zhres report <project>` | — | 状态统计：各类型条目数、翻译覆盖率、skipped 原因分布、TM 命中数、LLM 用量（如有） |

---

## 7. 翻译层规格

### 7.1 合并优先级

`approved` > `human` > `tm(human origin)` > `tm(machine origin)` > `llm` > 未翻译。任何写入都必须先通过 §7.4 校验，失败则该条保持原状态并记录原因（存入 `notes`，格式 `校验失败: …`）。

### 7.2 LLM 客户端

- 配置（zhres.toml）：

```toml
[llm]
enabled = false          # 默认 false，核心流程离线
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "ZHRES_API_KEY"
batch_size = 20
temperature = 0.2
timeout_seconds = 60
max_retries = 3
```

- 批处理：同 `context_kind` 的相邻条目组成一批（去重：相同 `source` 只送一次，结果回填所有同文条目）；请求/响应均为 JSON（`response_format: {"type":"json_object"}`，不支持该参数的端点容忍报错后降级为纯文本 JSON 解析）。
- 重试：指数退避（2s/4s/8s），仅对网络错误与 5xx；4xx 立即失败并输出响应体摘要。
- 断点续跑：每批成功即写回工程 JSON（原子写：临时文件 + `os.replace`），中断后重跑自动跳过已翻译条目。
- `--dry-run` 必须可用且不产生网络请求。

### 7.3 人工翻译工作流（离线主路径，必须一等公民）

`export --csv` → 人（或另一个 Agent）填 `translation` 列 → `import --csv`。README 用不少于 15 行的完整示例演示该流程，包括：如何在 Excel 中打开（BOM 说明）、哪些列可改、status 列含义。

### 7.4 校验规则（validate.py，翻译与导入时都要跑）

1. 占位符集合一致：`%s %d %1$s {0} {name} \n \t` 等正则提取后比较（顺序可不严格，集合必须相等）。
2. 快捷键一致：源文 `&X` → 译文必须含 `&` 且菜单场景惯例输出 `中文(&X)` 形式；同一 dialog/menu 内译文快捷键字母冲突 → warning（不阻断）。
3. 术语校验：源文含术语词而译文不含对应译名 → warning。
4. 译文为空但状态非 untranslated/skipped → 错误。
5. warning 与 error 分级：error 阻断该条写入；warning 汇总打印。

### 7.5 内置 LLM 系统提示词（原文随 llm.py 附带，见附录 A）

---

## 8. 回写、备份、安全

1. **原文件不可变性**：extract 阶段对目标文件只读；writeback 失败在任何一步都必须让目标文件保持原样（BeginUpdateResource 的句柄用 `EndUpdateResourceW(h, TRUE)` 丢弃）。
2. **备份**：writeback 前复制原文件为 `<name>.zhres-orig.bak`；已存在则命名 `<name>.zhres-orig-<YYYYmmdd-HHMMSS>.bak`；备份后立即校验备份 SHA-256 与源一致，不一致视为失败终止。备份路径与哈希写进工程 JSON。
3. **还原**：restore 覆盖当前文件前，将当前文件另存 `<name>.zhres-before-restore-<ts>.bak`（防止用户还原错对象）；还原后 SHA-256 校验。
4. **原子写**：工程 JSON、CSV、tm.db（WAL 模式）全部原子/事务化。
5. 所有写文件操作前检查目标可写与磁盘错误，错误信息中文且含路径。

---

## 9. Demo 演示程序（PoC 靶子）

- `demo/DemoApp/`：纯 Win32 C 程序（不依赖 MFC/ATL），英文界面，包含：
  - 主窗口 + 完整菜单树：File(New/Open/Save/Exit)、Edit(Undo/Cut/Copy/Paste/Select All)、Help(About)——**用普通 MENU 模板**；
  - About 对话框（.rc 中 DLGTEMPLATE，DS_SETFONT，含 Static 文本、Button、Icon）；
  - ≥1 个字符串表 block（≥10 条英文，状态栏/消息框文本）；
  - 全部英文界面文本 ≥ 40 条（菜单+对话框+字符串表合计）。
- 构建：`build_mingw.sh`（`windres app.rc appres.o && gcc main.c appres.o -mwindows -o DemoApp.exe`）与 `build_msvc.bat`（`rc + cl /W4`）。
- 源码与 .rc 中所有英文文本集中定义（.rc 的 STRINGTABLE + 菜单/对话框资源），便于对照翻译。
- **编译器探测**（执行阶段 Phase 0 做）：PATH 中依次找 `cl`、`gcc`；都没有时尝试 `winget install -e --id BrechtSanders.WinLibs.POSIX.UCRT --silent`（装不上不硬磕，走 §9 兜底条款）。
- **兜底条款**：若无任何可用编译器且 winget 失败——交付不阻塞：测试全部用合成 fixture（§11），README 写明 demo 的一键构建命令，最终报告的"未完成项"中明确记录"demo 未能本机构建"。**除该条款外，其余验收项不得打折。**

---

## 10. 测试要求

1. **round-trip 字节级测试是质量基石**：
   - 合成 fixture：`fixture_pe.py` 纯 Python 构造一个最小合法 PE（DOS 头 + PE 头 + 单节 `.rsrc`），内含：2 个字符串表 block（含空条目、含 Unicode 边缘字符）、普通 MENU（含 popup 嵌套与分隔符）、MENUEX 样本（手工字节）、DLGTEMPLATE 与 DLGTEMPLATEEX 各 1（含 DS_SETFONT）、DFM 样本（含 Caption/Hint/Lines/vaCollection/vaBinary 混合）。构造函数返回 bytes，落盘供 ctypes API 真实读写。
   - 每个 adapter 对 fixture 与（可用时的）demo exe 资源做 `serialize(parse(x)) == x`。
2. 修改类测试：改一条字符串 → 回写 → 重新解析 → 断言仅该条变化、block 仍 16 条；菜单改一条后其余项逐字节不变；DFM 同理。
3. 合成 PE 全流程集成测试（离线）：extract → import CSV → writeback → verify → restore（SHA-256 循环校验）。注意：合成 PE 需要让 `LoadLibraryExW(LOAD_LIBRARY_AS_DATAFILE)` 接受——头字段按规范填齐。
4. validate 规则逐条单测（含 `%s` 缺失、`&` 丢失、术语缺失）。
5. TM：并发无关，测精确/归一化命中、human 覆盖 machine。
6. LLM：`--dry-run` 与错误重试用本地 mock（monkeypatch urllib），**无 API key 时真实网络测试自动 skip**（pytest marker `live`）。
7. 备份/恢复：含"备份损坏"（改 1 字节）→ restore 拒绝。
8. `pytest -q` 必须全绿；测试里禁止 sleep 依赖真实网络。

---

## 11. 文档要求（成品的一部分，缺一不可）

1. **README.md（中文）**：项目简介与原理图（ASCII 流程图）；安装（`pip install -e .`）；**5 分钟上手**（以 demo 为例的完整命令序列与预期输出）；全部命令参考（含退出码）；配置参考（逐字段）；FAQ：杀软对修改后 exe 误报怎么办、自校验程序为何不行、控件文本变长怎么办、ANSI/非 Unicode 程序的限制、如何翻译 Delphi 程序；**法律与道德说明**：仅限自用、修改后的文件不得分发、不用于规避版权保护、不适用于破解版程序。
2. **docs/formats/*.md**：实现过程中沉淀的四份格式笔记，含字段偏移图表与参考链接（MS 文档/Wine），并把 round-trip 中踩过的坑记进去。
3. **docs/architecture.md**：适配器协议说明 + v2 扩展指南（.NET resx、Electron asar/pak、Qt ts/qm 各自的提取思路，每项 ≥10 行）。
4. **docs/decisions.md**：§16 要求的全部自主决策记录。
5. **CHANGELOG.md**：1.0.0 条目。

---

## 12. 非功能需求

- 性能：≤50MB 文件 extract+writeback 全程 <30s（LLM 不计）。
- 内存：禁止把 >100MB 的 RCDATA 整体读入（先读签名判断）。
- 兼容：Windows 10/11 x64 与 x86 PE；Python 3.10/3.11/3.12。
- 日志：默认简洁中文；`--verbose` 输出每个资源的解析细节（type/name/lang/size/条目数）。
- 无任何第三方运行时依赖（`pip install zhres` 不得拉入 requests/pywin32 等）。

---

## 13. 执行阶段（顺序执行，每阶段 DoD 未达不许进入下一阶段）

- **Phase 0 环境探测**：Python 版本、pytest 可用性、编译器（cl/gcc/winget）、网络连通性。输出环境报告；后续决策（如 demo 兜底条款）据此锁定。仓库已存在（见执行目录说明），直接在其上提交。
- **Phase 1 骨架 + 合成 PE fixture + winapi.py + RT_STRING 提取/回写**。DoD：`test_stringtable` + round-trip 全绿；`zhres extract` 对合成 PE 输出正确条目。
- **Phase 2 MENU + DIALOG + DFM 适配器**。DoD：四类资源 round-trip 全绿（含修改类测试）。
- **Phase 3 工程文件 / TM / 术语 / CSV 导入导出 / validate**。DoD：集成测试（离线全流程）绿。
- **Phase 4 LLM 客户端**（enabled 默认 false）。DoD：dry-run、mock 重试、真实 key 烟雾测试（有则做，无则记录跳过）。
- **Phase 5 Demo + 端到端**：构建 demo → `scripts/demo.sh` 跑通 extract→translate（离线，用 examples/demo-translations.csv）→writeback→verify→restore；有编译器则启动 exe 确认中文界面（描述看到的菜单/对话框内容作为证据）。DoD：脚本全绿或仅 §9 兜底条款生效。
- **Phase 6 文档收尾**：README、docs/*、CHANGELOG；`pip install -e .` 全新目录冒烟；按 §14 自检。

每阶段至少一次 git commit，信息格式 `phase(n): 摘要`。

---

## 14. 最终验收清单（交付前逐项自检，结果写入交付报告）

- [ ] A1 `pip install -e .` 在干净 venv 成功；`zhres --help` 正常
- [ ] A2 `pytest -q` 全绿，且无 skip 掩盖的失败（skip 项逐一说明原因）
- [ ] A3 四类资源 round-trip 字节级相等（fixture 必做；demo 有则加测）
- [ ] A4 合成 PE 离线全流程：extract→import→writeback→verify→restore，SHA-256 循环一致
- [ ] A5 demo 端到端：构建 → 离线翻译 → 回写 → verify 通过 → （有编译器时）界面中文证据（描述或截图）
- [ ] A6 writeback 失败路径：构造坏资源使某三元组失败 → 目标文件保持原样（SHA-256 一致）
- [ ] A7 备份损坏 → restore 拒绝且不破坏现状
- [ ] A8 `--dry-run` 全程无网络、无写文件
- [ ] A9 壳样本（手工构造含 UPX0 节名的 PE）→ extract 拒绝并说明
- [ ] A10 校验器：占位符丢失/`&` 丢失/术语缺失分别产生 error/warning 且阻断行为正确
- [ ] A11 文档齐全且命令示例可复制执行（抽查 README 中每条命令真实运行）
- [ ] A12 法律说明存在于 README 与 `zhres extract` 首次运行的提示输出中

**交付报告格式**：验收清单逐项结果（✓/✗+证据）｜测试汇总（通过/失败/skip 数）｜未完成项与原因｜已知限制｜v2 建议。

---

## 15. 工作纪律

1. **不要向用户提澄清问题**。本任务书未覆盖的细节，按"最保守、最可回滚"原则自行决定，并记入 `docs/decisions.md`（格式：决策/理由/影响）。
2. 测试红了不许进入下一阶段；**禁止为了让测试通过而弱化断言或放宽 round-trip 比较**。
3. 格式规格与 MS 文档冲突时，以文档为准并更新本任务的实现（在 docs/formats 中记录差异）；round-trip 测试是最终裁判。
4. 严禁：调用外部汉化/资源工具二进制；把任何网络请求放进默认路径；在异常分支吞掉错误继续写文件。
5. 代码风格：类型注解齐全；公开函数 docstring（中文）；模块级错误用自定义异常层级（`ZhresError` 基类）。

---

## 附录 A：内置 LLM 系统提示词（随 llm.py 分发，逐字使用）

```
你是资深的软件界面本地化专家，任务是把 Windows 软件界面文本从英文翻译成简体中文。

规则：
1. 遵守 Windows 中文软件惯例：File→文件、Edit→编辑、Exit→退出、About→关于、OK→确定、Cancel→取消、Help→帮助。
2. 带有 & 的英文（如 E&xit）表示快捷键：译文采用 "中文(&X)" 格式，保留原快捷键字母。
3. 严格保留所有占位符（%s、%d、%1$s、{0}、\n、\t），一个都不能丢。
4. 菜单项和按钮用词简洁（动词优先）；对话框标题、提示语可用完整句；错误消息礼貌、清晰。
5. 不确定的专有名词保留英文原文，不要硬译产品名。
6. 只输出 JSON，不要输出任何解释。输入是 {"items":[{"id":...,"source":...,"context":...}]}，
   输出 {"translations":[{"id":...,"translation":...}]}，id 一一对应。
```

## 附录 B：`zhres.toml.example`

```toml
[languages]
source = "en"
target = "zh-CN"

[writeback]
font = "Microsoft YaHei UI"
font_charset = 134
backup = true

[llm]
enabled = false
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "ZHRES_API_KEY"
batch_size = 20
temperature = 0.2
timeout_seconds = 60
max_retries = 3
```

## 附录 C：ctypes 注意点（winapi.py 必须处理）

1. `MAKEINTRESOURCE`：资源名是序数时，传参必须把整数 cast 成 `LPCWSTR`（`ctypes.cast(ctypes.c_void_p(n), LPCWSTR)`），直接给 `c_wchar_p` 传 int 会抛异常；同理**返回值**里收到的 `LPCWSTR` 要先判断是否为序数（`IsIntResource`：指针值高 16 位为 0 即视为序数）——Enum* 回调里 type/name 两种情况都要判。
2. 回调签名（x64）：`BOOL CALLBACK EnumResNameProc(HMODULE, LPWSTR type, LPWSTR name, WORD lang, LONG_PTR param)`——用 `WINEVENTPROC` 风格 `WINFUNCTYPE` 声明，保持对象引用防 GC。
3. `UpdateResourceW` 的 lpType/lpName 同样支持序数；`lpData` 用 `(ctypes.c_char * len).from_buffer_copy(data)`。
4. 语言 ID 必须使用**枚举时得到的原值**回写（含 0=中性语言），不得自行改写。
5. `LoadLibraryExW` 用完 `FreeLibrary`；`LoadResource` 句柄无需释放（系统托管），但 `LockResource` 返回的指针仅在模块存活期有效——先拷贝成 bytes 再用。
