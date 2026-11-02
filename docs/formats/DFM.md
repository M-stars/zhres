# Delphi DFM（binary）格式笔记

> 实现依据：任务书 §5.4 规格 + 本机真实程序样本逐字节验证。
> 样本来源：Inno Setup 卸载程序（Delphi 系写入方，unins000.exe 的
> RT_RCDATA）与 Cheat Engine（Lazarus/FreePascal 系写入方）。
> round-trip：真实样本 19/19 逐字节还原（提交于 tests/fixtures/）。

## 总体结构

```
"TPF0"
Object := 类名(BYTE len + ANSI) 对象名(BYTE len + ANSI)
          属性列表 子对象列表

属性列表 := { BYTE 名称长度; 名称; BYTE 值类型; 值 }* 0x00
子对象列表 := { [前缀] 子对象 }* 0x00
```

## 子对象前缀（重要，与常见描述不同）

- **普通子对象没有 0xFF 前缀**——类名长度字节直接开始
  （实测 Inno 与 Lazarus 两种写入方皆如此；`TReader.ReadPrefix`
  的判定是 `字节 & 0xF0 == 0xF0` 才视为前缀，普通类名长度不可能命中）
- `inherited`/`inline`/带序号的子对象有 `$F0 | flags` 前缀字节：
  bit0=ffInherited、bit1=ffChildPos、bit2=ffInline；
  ffChildPos 时跟一个 ReadInteger（vaInt8/16/32 + 载荷）
- 前缀字节 + 顺序值原始字节整体保留，round-trip 逐字节还原

## 值类型（TypInfo.TValueType）

| 值 | 名称 | 载荷 |
|---|---|---|
| 1 | vaList | { BYTE 类型 + 值 }* 0x00 |
| 2/3/4 | vaInt8/16/32 | 1/2/4 字节，**有符号**（Font.Height=-11 实测） |
| 5 | vaExtended | 10 字节（原样保留，不做浮点重编码） |
| 6 | vaString | BYTE len + ANSI 字节 |
| 7 | vaIdent | BYTE len + ANSI |
| 8/9/13 | vaFalse/vaTrue/vaNil | 无 |
| 10 | vaBinary | DWORD 字节数 + 原始字节 |
| 11 | vaSet | { BYTE len + 字节 }* 空串(0x00) 终止 |
| 12 | vaLString | DWORD 字节数 + 字节 |
| 14 | vaCollection | 见下 |
| 15/16/17 | vaSingle/vaCurrency/vaDate | 4/8/8 字节 |
| 18 | vaWString | DWORD 字节数 + UTF-16LE |
| 19 | vaInt64 | 8 字节 |
| 20 | vaUTF8String | DWORD 字节数 + UTF-8 |
| 21 | vaDouble | 8 字节 |

## vaCollection（含糊区的实测裁定）

```
vaCollection:
  { [顺序号: vaInt8/16/32 + 载荷]     ← 可选
    [0x01]                            ← TWriter.WriteListBegin 的标记，可选
    属性列表(0x00 终止) }*
  0x00
```

Delphi 标准写入方每项带 0x01 标记；实现按"逐字节保留原始形态"
处理（顺序号、0x01 标记有无都原样还原），不假设其一。

## TStrings 类属性（Lines / Items）

**以带点属性名 + vaList 存储**（实测 TWizardForm 样本）：

```
"Lines.Strings"  ← 名称含点号，作为一个属性名
01 (vaList)
  06 "alpha"     ← 每项 = 类型字节 + vaString
  06 "beta"
  00
```

## 提取 / 回写约定

- 白名单属性：`Caption` `Text` `Hint` `SimpleText` `Title`；
  字符串列表：`*.Strings`（如 `Lines.Strings`）与裸名 `Lines`/`Items`
- Entry id 的 position 段 = 属性路径：`Form1/Lines/3`、
  `Form1/Panel1/InnerLabel/Caption`（任务书 §4.2）
- ANSI（vaString/vaLString）：按 GBK 解码读取、按 GBK 编码回写；
  解码失败 → 该条 skipped（记录原因），编码失败 → 回写时跳过该条
- 未修改的属性（含 vaBinary/vaCollection/vaExtended 等）逐字节保持
