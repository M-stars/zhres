# RT_MENU 菜单资源格式笔记

> 实现依据：MS 文档 + Wine `dlls/user32/menu.c` 交叉核对；
> **最终以字节级 round-trip 裁决**——普通模板经 windres 编译产物验证，
> MENUEX 经 **195 个本机系统 DLL/EXE 的真实 MENUEX 资源**全量验证
> （shell32.dll / user32.dll / webcheck.dll 等，逐字节还原 195/195），
> 并通过真实资源加载器（`LoadMenuW`）加载验证。

## 通用头部

```
WORD wVersion;   // 0=普通模板, 1=MENUEX
WORD wOffset;    // 头扩展字节数，随后跳过
BYTE  extra[wOffset];
```

## 普通模板（version 0）

项序列（无长度前缀，靠 MF_END 终止）：

```
WORD mtOption;
[WORD mtID;]          // 仅非 popup 项
WCHAR szText[] + NUL; // UTF-16LE
```

- `MF_POPUP(0x10)`：该项弹出子菜单，**无 mtID**，子项序列紧随其后
- `MF_END(0x80)`：本层最后一项
- `MF_SEPARATOR(0x800)`：分隔符（windres 实测：SEPARATOR 编码为
  `option=0x0000, id=0, 空文本`，**没有** 0x800 位！）

注意：模板里的 option 值与运行时 MF_* 同值，但 MF_END(0x80) 与
MF_HILITE 同值，解析时不能清除它（round-trip 需要原样保留）。

## MENUEX（version 1）—— 与 Wine 的两处差异

项结构：

```
DWORD dwType;     // MFT_*（原样保留）
DWORD dwState;    // MFS_*
DWORD dwId;
WORD  wFlags;     // bit0(0x01)=popup；bit7(0x80)=本层最后一项
WCHAR szText[] + NUL;
[DWORD dwHelpId;] // 仅 wFlags&0x01（popup 项）时存在
// 项与项之间：文本结束后按 DWORD 对齐（相对资源起始）
```

**踩坑记录**：

1. **Wine 的 `LoadMenuIndirectW` 会多读一个头部 `dwHelpId`**（读完
   version/offset/扩展后又读 4 字节）。真实 RC 输出**没有**这个字段——
   头部 `wOffset` 扩展字节里已经包含 dwHelpId（实测样本 offset=4 恰为
   一个零 dwHelpId）。按 Wine 实现会把整个项流错位 4 字节。
   （ReactOS 同源代码同病。）
2. 实测中曾把项头写成 `<IIHH`（12 字节，id 被拆成两个半字），
   id 高半字被当作 flags 导致 flags 恒 0——正确格式是
   `<IIIH`（14 字节）。此类错误在 bounds check 全部合法的情况下
   只能靠 round-trip 暴露。

## 提取 / 回写约定

- 先序遍历编号（含分隔符）作为 Entry 的 position
- popup 标题 → `menu_title`；叶子 → `menu_item`；分隔符不产生条目
- menu 上下文的自动跳过规则：`&X`（单个 & 后字符）**保留**（快捷键定义）
- 回写只改文本，option/id/helpid/嵌套结构逐字节保持；
  模板版本保持不变（0→0，1→1）
