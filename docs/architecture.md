# 架构与适配器扩展指南

## 总体架构

```
cli.py ──► packerdetect.py（壳检测，读节表）
       ──► pe.py + winapi.py（系统 API：只读枚举 / UpdateResource 回写）
       ──► adapters/（资源内容体解析与序列化）
       │     ├─ base.py          协议 + 注册表 + 自动跳过规则
       │     ├─ stringtable.py   RT_STRING
       │     ├─ menu.py          RT_MENU（普通 + MENUEX）
       │     ├─ dialog.py        RT_DIALOG（标准 + EX）+ 字体补丁
       │     └─ dfm.py           RT_RCDATA 中 TPF0（Delphi DFM）
       ──► project.py（翻译工程 JSON）
       ──► translate.py（合并编排）→ tm.py / glossary.py / llm.py
       ──► validate.py（占位符/快捷键/术语校验）
       ──► writeback.py / verify.py / restore.py（安全回写与还原）
```

关键设计约束：

1. **资源目录层面一律走系统 API**（kernel32），自建 PE 资源目录是已知
   高危错误源。适配器只处理"资源内容体"（一块字节）。
2. **内容体字节级 round-trip**：`serialize(parse(x)) == x` 是每个
   适配器的硬性测试要求——未修改的属性/字段必须逐字节保持。
3. **不认识的资源原样保留**：注册表按 `resource_types` 分发，
   `find()` 返回 None 时跳过并计数，绝不因陌生格式失败或改写。
4. 解析错误带上下文（type/name/lang + 字节偏移）抛 `ParseError`；
   单个资源解析失败 → 整资源跳过 + 警告，不产生半截条目。

## 适配器协议

```python
class ResourceAdapter(Protocol):
    resource_types: tuple[int, ...]          # 关心的 RT_* 序号

    def can_parse(self, type_id: int, name, lang: int, data: bytes) -> bool:
        """同一类型可能有多种内容体格式时用于甄别（如 RCDATA 的 TPF0）。"""

    def extract(self, type_id: int, name, lang: int, data: bytes) -> list[Entry]:
        """解析内容体 → Entry 列表（含 skipped 条目，含 skip_reason）。"""

    def apply(self, entries: list[Entry], original: bytes) -> bytes:
        """把已翻译条目写回内容体；未修改时必须原样返回（字节一致）。"""
```

Entry 约定：

- `id = "RT_<TYPE>/<resource_name>/<language>/<position>"`，
  position 段对 menu/字符串表是序号、对 DFM 是属性路径
  （`Form1/Lines/3`）；同一 exe 两次 extract 的 id 集合必须一致
  （verify 依赖）。
- `context.kind` 是翻译语境（`menu_item` / `dialog_caption` /
  `string_table` / `dfm_property`…），TM 按 (source, kind) 匹配。
- skipped 条目也要产出（带 `skip_reason`），让用户看到"为什么没翻"。

新适配器实现步骤：

1. 在 `adapters/` 新建模块，实现协议三个方法；
2. 在 `base.py::build_default_registry()` 注册；
3. 在 `tests/fixture_pe.py` 增加该格式的合成构造器（进合成 PE）；
4. round-trip 测试（fixture + 能找到的真实样本）+ 修改类测试；
5. 在 `docs/formats/` 落一份格式笔记（含实测踩坑）。

## v2 扩展思路（v1 未实现，仅方向）

### .NET 程序集（.resources / .resx / 嵌入资源流）

.NET 的 UI 文本不在 Win32 资源里，而在 CLR 元数据流的托管资源中：
`RT_RCDATA` 里可能嵌 `.resources`（二进制 RuntimeResource 格式），
或卫星 DLL（`<name>.zh-CN.dll`）内含主 `.resources`。适配器思路：
RCDATA 内容体前 4 字节判别 `0xB5,0x79,0xAE,0xB2`（.resources 魔数），
解析 ResourceManager 二进制格式（7 位变长整数、RuntimeResource 头、
按类型名分派的记录流）；用户类型（WinForms Form 的
`$this.Icon` 等）需按序列化类名分派。resx（XML）出现在源码侧，
编译后不存在于 exe，一般无需处理。难点：反序列化需要部分类型系统
知识；WinForms 设计器资源通常只含图标/图片，文本多在
`Form.resources` 的字符串对里，是可摘出的子集。建议先支持纯
字符串记录（ResourceTypeCode.String / StringIO），跳过对象记录
原样保留。卫星 DLL 是独立 PE，主流程可直接复用（extract 卫星 DLL）。

### Electron 应用（asar / pak）

Electron 的界面文本多数在 JS 代码与 JSON 里，不属于 PE 资源；可做
的是：a) `app.asar`（自描述归档：8 字节 pickle 头 + JSON 目录 +
文件内容），适配器思路：按目录树定位 `.js/.json/.html` 文本块，
对 i18n 库的 JSON 词典（如 `zh-CN.json` 缺失时按 `en.json` 生成）
做提取回写，回写保持 asar 目录偏移一致性（asar 支持追加式修改，
或整包重打包并修复头部长度）；b) Chromium 的 `.pak`（版本 5/6：
资源 ID + 偏移/长度表），`locale.pak` 内是 `IDS_*` 消息字符串，
可按 ID 提取。注意 asar 有完整性校验（Electron 可开启
`EnableEmbeddedAsarIntegrityValidation`），回写需提示该风险。
这类适配器的 resource_types 建议占用 RT_RCDATA（内容体在 RCDATA
中的场景较少，更多是"旁路文件处理"——v2 需先给 CLI 增加文件级
入口，而不只是 PE 资源入口）。

### Qt 应用（.qm / .ts / 嵌入 QRC）

Qt 程序的翻译载体是 `.qm`（编译后消息文件，常作为独立文件或嵌入
QRC 资源）。`.qm` 格式：`magic "qmchucknorris"`（16 字节）后跟
tagged 数据块：contexts（Contexts 块 → 消息组：源文 hash、SourceText
字节串、Translation 字节串、状态）+ 可选 translators 注释块。
适配器思路：QRC 优先——`RT_RCDATA` 中以 `qres:` 开头的压缩数据块
（Zlib，RFC1950），解压后是 XML（RCC 目录），定位 `.qm` 成员后按
QM 块结构改写 Translation 字符串（变长编码，回写需重建块长度与
偏移表）。QT_TR_NOOP 源文在 .ts（XML）里，但发布程序通常只有 .qm。
注意：Qt 程序加载 .qm 时有 `^e` 魔数与可选的间隔树索引，改写译文
后应保持消息顺序与 hash 一致（hash 由源文决定，不变）；若存在
`QTranslator` 完整性自检（极少），原样保留策略兜底。独立 `.qm`
文件场景同样需要 v2 的"旁路文件"入口。

### 游戏引擎数据文件（v2 已实现，见 docs/formats/）

游戏引擎的文本载体是**数据文件**而非 PE 资源。v2 已通过
adapters/gamefiles.py 的统一注册表落地（单文件 + 文件夹双模式）：
RPG Maker MV/MZ（JSON）、XP/VX/VX Ace（RGSSAD + Ruby Marshal）、
GameMaker（data.win STRG）、Unity（.assets v15–22、UnityFS 包裹）、
通用 JSON/TXT/INI/CSV。尚未覆盖：Kirikiri .xp3（加密过滤器可配置）、
Wolf RPG（密钥在 exe 内需启发式提取）、Ren'Py（自定义 pickle）、
SRPG Studio（.dts XOR）、Godot .translation（RSRC 二进制资源）——
接入方式与 QM/Unity 相同：实现 can_parse_file/extract/apply 三方法
并注册进 gamefiles._FORMATS。

### 其他候选

- **RT_VERSION / RT_ACCELERATORS**：纯展示性信息（版本串）与快捷键表，
  结构简单，适合作为下一个练手适配器。
- **WPF/BAML**：`.g.resources` 里的 BAML（二进制 XAML），文本以
  UTF-16 字面量嵌在记录流中，可定位字符串表段做受控替换，但控件
  布局由 XAML 决定，风险中等。
- **Inno Setup / NSIS 安装包**：脚本驱动的文本表，各自有成熟社区
  工具，v2 可按同样协议包一层。
