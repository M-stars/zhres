# 游戏数据文件（JSON）格式笔记 —— RPG Maker MV/MZ 与通用 JSON

> 实现依据：RPG Maker MV/MZ 数据文件公开结构 + 通用 JSON 语义；
> 合成样本 round-trip 测试（tests/test_gamefiles.py）。

## 覆盖范围

- **RPG Maker MV/MZ**：全部文本在 `www/data/*.json`（或 `data/*.json`）：
  `MapXXX.json` 事件指令（401 对话、102 选项、101 头像+名字）、
  `Actors.json`/`Items.json` 等数据库（name/nickname/description/profile/note）
- **通用 JSON 本地化**：`lang/zh-Hans.json`、`i18n/*.json` 等键值表

## 提取

递归遍历**字符串值**（键名不提取），条目 id/context.pointer =
RFC 6901 JSON Pointer（`~0`/`~1` 转义，键含 `/` 也无歧义）。

跳过规则（宁可漏提，不可错改——改坏脚本串/资源名会让游戏无法运行）：

| 规则 | 例子 |
|---|---|
| 键名黑名单 | `src`/`path`/`file`/`url`/`icon`/`bgm`/`characterName`… |
| 媒体兄弟键 | 对象含 `volume`/`pitch`/`pan` → 其 `name` 是音频素材 |
| RM 事件指令 | `code` ∈ {108,408,355,655,356,657}（注释/脚本/插件命令） |
| MZ 插件命令 | `code`=357 的 `parameters[0..1]`（插件名/命令名） |
| 代码特征 | `function`/`=>`/`&&`/`return `…；`foo(){`；URL |
| 资产扩展名结尾 | `.png`/`.ogg`/`.js`/`.ttf`… |
| 标识符 | 全大写下划线（`SW_ATK_01`） |
| `<notetag>` | 无 CJK 的 `<...>` 串（如 `<Sideview Actor: 3>`） |
| 通用 | auto_skip（纯数字/标点/单字符）+ 超 2000 字符 |

## 回写

整文件重写，**按 context.pointer 定位**（不解析 id，文件夹模式
相对路径含 `/` 也安全）：

- 键序保持（json 解析天然有序）；缩进按原文探测（紧凑 →
  `separators=(",", ":")`；缩进 → 首层宽度）；尾部换行保持
- 编码按 BOM 探测：UTF-8/BOM UTF-8/GBK，回写用同一编码
- 回写前 `json.loads` 复检：新文件必须可解析且与内存对象一致
- 无改动时 apply 原样返回（字节级一致）

**限制**：原文含重复键或非常规数字字面量（`1.50`、`1E-6`）时重写
后字节可能微差——游戏按 JSON 语义读取，不影响运行。
