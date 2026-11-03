# Qt 翻译文件（.qm）格式笔记

> 实现依据：Qt6 官方源码（src/corelib/kernel/qtranslator.cpp）+ 本机
> PySide6 真实 .qm 样本逐字节验证（assistant_de.qm，207 条消息）。

## 文件结构

```
16 字节魔数: 3c b8 64 18 ca ef 9c 95 cd 21 1c bf 60 a1 bd dd
块* := [BYTE tag][DWORD BE 长度][内容]
```

块 tag（Qt6）：

| tag | 含义 |
|---|---|
| 0xa7 | Language（语言名，UTF-8，如 "de_DE"） |
| 0x2f | Contexts（上下文名表） |
| 0x42 | Hashes（ELF hash 表） |
| 0x69 | **Messages**（消息块） |
| 0x88 | NumerusRules（复数规则字节码） |
| 0x96 | Dependencies（依赖 .qm，QDataStream 字符串） |

（Qt5 的 Messages=0x3f 与 Qt6 不同——两代格式 tag 有差异，
解析按 tag 分派即可兼容。）

## Messages 块

```
消息 := 记录* Tag_End(1 字节，无长度)
记录 := [BYTE tag][DWORD BE 长度][载荷]
```

记录 tag（Qt6）：

| tag | 含义 | 编码 |
|---|---|---|
| 2 | SourceText16 | UTF-16BE（旧格式） |
| 3 | **Translation** | **UTF-16BE**（长度为字节数，必须偶数） |
| 4 | Context16 | UTF-16BE（旧格式） |
| 5 | Obsolete1 | （4 字节占位） |
| 6 | SourceText | UTF-8 |
| 7 | Context | UTF-8 |
| 8 | Comment | UTF-8 |
| 9 | Obsolete2 | （同 5） |

**关键点**：

- 译文是 **UTF-16 大端**（`qFromBigEndian<char16_t>`）——与小端直觉相反
- Tag_End 只有 1 字节，无长度字段
- 源文记录（SourceText/SourceText16）在翻译后**保持不变**——
  Hash 块的 ELF hash 与 Contexts 表都基于源文，动源文会破坏查找
- 一条消息可有**多条 Translation 记录**（复数形式，按 numerus 索引取）

## 提取 / 回写约定

- Entry id：`QM/<文件名>/<消息序号>`；context/comment 进 context
- 回写只替换 Translation 记录载荷（可变长，整文件重写——QM 是
  独立文件，无 PE 长度约束），其余块与记录逐字节保留
- 已有译文的条目带出为 human 状态（便于改译）；无译文条目为
  untranslated
- verify 语义：QM 的源文记录不随翻译变化，对比的是**译文记录**
  （context.verify_field = "translation"）

## 限制

- **RCC 内嵌 .qm**（qrc 编译进 exe）：需要定位 .rdata 中的 RCC blob
  并处理 zlib 压缩与树偏移——v2 后续；本版支持独立 .qm 文件
- NumerusRules 原样保留（复数规则与语言绑定，不因翻译变化）
