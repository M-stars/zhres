# RT_DIALOG 对话框资源格式笔记

> 实现依据：MS 文档（DLGTEMPLATE / DLGTEMPLATEEX / DLGITEMTEMPLATE /
> DLGITEMTEMPLATEEX）+ Wine `dlls/user32/dialog.c` 交叉核对；
> round-trip 经 windres 编译产物（`DIALOG` 与 `DIALOGEX` 各一）逐字节验证。

## 判定 EX

偏移 2 处的 WORD == 0xFFFF 即 DLGTEMPLATEEX（signature）；
标准 DLGTEMPLATE 的该位置是 cdit 的高 16 位，无此判别价值。

## DLGTEMPLATE（标准）

```
DWORD style; DWORD dwExtendedStyle; WORD cdit;
short x, y, cx, cy;                     // 共 18 字节
sz_Or_Ord menu;                          // 0x0000 / 0xFFFF+ord / 字符串+NUL
sz_Or_Ord windowClass;
WCHAR title[] + NUL;
[WORD pointsize; WCHAR typeface[] + NUL] // 仅 style & DS_SETFONT(0x40)
--- 控件（每个按 DWORD 对齐，相对资源起始）---
DWORD style; DWORD dwExtendedStyle;
short x, y, cx, cy; WORD id;
sz_Or_Ord windowClass;
sz_Or_Ord title;                         // 0xFFFF+ord = 图标等序数资源
WORD extraCount; BYTE extra[extraCount];
```

**关键实测结论（MS 文档与 Wine 一致，与坊间传言相反）**：
标准模板的字体块**只有 pointsize + typeface 两个字段**——
weight/italic/charset 是 EX 模板才有的。按 5 字段解析标准模板会把
typeface 后面的控件流错位。

（`DS_SHELLFONT` 在标准模板中不受支持——MS 文档明示。）

## DLGTEMPLATEEX

```
WORD dlgVer(=1); WORD signature(=0xFFFF);
DWORD helpID; DWORD exStyle; DWORD style; WORD cdit;   // 共 18 字节
short x, y, cx, cy;                                     // 至 26
sz_Or_Ord menu; sz_Or_Ord windowClass; WCHAR title[] + NUL;
[WORD pointsize; WORD weight; BYTE italic; BYTE charset;
 WCHAR typeface[] + NUL]      // 仅 style & (DS_SETFONT|DS_SHELLFONT)
--- 控件（DWORD 对齐）---
DWORD helpID; DWORD exStyle; DWORD style;
short x, y, cx, cy; DWORD id;          // 注意 id 是 DWORD！
sz_Or_Ord windowClass; sz_Or_Ord title;
WORD extraCount; BYTE extra[extraCount];
```

- pointsize == 0x7FFF 表示"使用消息框字体"（Wine 同语义）：
  此时字体块仅含该 WORD，weight/italic/charset/typeface 不存在。
- 控件 id 在 EX 中是 **DWORD**（标准模板是 WORD）——照抄标准模板的
  `<hhhhH` 会在 EX 上错位。

## 预定义控件类序数

0x80=Button, 0x81=Edit, 0x82=Static, 0x83=ListBox, 0x84=ScrollBar,
0x85=ComboBox。

## 提取 / 回写 / 字体补丁

- 对话框 title → `dialog_caption`（Entry id 的 position 段为 `title`）
- 控件 title（非 0xFFFF 序数、非空）→ `dialog_control_text`，
  position 段为控件序号（0 起，按 cdit 顺序）
- 回写只改文本；菜单/类数组、样式、坐标、extra 创建数据逐字节保持
- 字体补丁（writeback 默认开启）：typeface → Microsoft YaHei UI；
  EX 模板另把 charset → 134 (GB2312_CHARSET)；pointsize/weight/italic
  保持。标准模板无 charset 字段，只换 typeface。
  没有字体块的对话框 v1 **不新增**（已知限制）。
