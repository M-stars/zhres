# PE 资源目录（.rsrc）构建笔记

> 本文是 `tests/fixture_pe.py` 构建合成 PE 时实测沉淀的格式笔记。
> 关键结论全部经 Windows 10/11 x64 加载器（`LoadLibraryExW` +
> `EnumResource*W` / `FindResourceExW`）实证，并与
> `C:\Windows\System32\notepad.exe` 的真实目录逐字节对照。

## 目录层级

```
根目录（root） ──条目: 类型
  └─ 类型目录（per-type） ──条目: 资源名
      └─ 名字目录（per-name） ──条目: 语言 ID
          └─ 语言目录（per-lang，叶） ──条目: 数据项
              └─ 数据项（DATA ENTRY, 16 字节）
```

固定四层目录 + 数据项，**没有中间层**。
（首版实现曾误加一层"类型→名字"的中间目录，加载器把中间目录的
条目名当资源名返回，出现 `EnumResourceNames(6) → [6]` 的诡异现象。）

## 目录结构

```
目录头 16 字节:
  DWORD Characteristics; DWORD TimeDateStamp;
  WORD  MajorVersion;    WORD MinorVersion;
  WORD  NumberOfNamedEntries; WORD NumberOfIdEntries;
条目（每条 8 字节）: DWORD 名称字段; DWORD 偏移字段
```

## 偏移编码（实测裁定，容易搞错的部分）

| 字段 | 编码 |
|---|---|
| 条目名称字段，高位 0x80000000 置位 | 其余 31 位 = 名字字符串的**节相对偏移** |
| 条目名称字段，高位为 0 | 资源类型/名字的序数 ID |
| 条目偏移字段，高位 0x80000000 置位 | 其余 31 位 = **子目录的节相对偏移** |
| 条目偏移字段，高位为 0 | **数据项的节相对偏移** |
| 数据项内 DataRVA | **绝对 RVA**（相对映像基址） |

**所有目录内偏移一律相对"资源节起始"（节 VirtualAddress），不是相对
映像基址；唯一例外是数据项里的 DataRVA 是绝对 RVA。**

notepad.exe 实证：`.rsrc` 节 VA=0x3A000，根目录首条目名字字段
0x80000688 → 名字串在节内偏移 0x688；数据项偏移 raw=0x668 → 数据项在
节内偏移 0x668；该数据项 DataRVA=0x57D18（绝对 RVA，落在节 VA +
0x1DD18 处）。

## 其他实测结论

- **根目录必须位于资源目录区间内**（建议直接放在节数据开头）。NT
  加载器以数据目录 [RVA, RVA+Size) 为有效范围，子目录偏移越界时报
  `ERROR_BAD_EXE_FORMAT (193)`，与真实格式错误难以区分。
- 加载失败报错不精确：偏移越界 → 193；找不到类型 → 1813
  （ERROR_RESOURCE_TYPE_NOT_FOUND）；重复加载同一路径会命中加载器
  模块缓存（同进程内改文件再加载返回旧映射，**测试每个变体必须用
  不同文件名或不同进程**）。
- 最小 PE 头：DOS 头（e_lfanew=0x40）+ `PE\0\0` + COFF（20 字节，
  SizeOfOptionalHeader=240）+ PE32+ OptionalHeader（112 + 16×8 数据
  目录）+ 1 个节头。`LoadLibraryExW` 接受 CheckSum=0、EntryPoint=0、
  无重定位的极简头；`LOAD_LIBRARY_AS_DATAFILE|LOAD_LIBRARY_AS_IMAGE_RESOURCE`
  (0x22) 加载时不执行任何代码。
- 节对齐：SectionAlignment=0x1000、FileAlignment=0x200、
  SizeOfHeaders=0x200、PointerToRawData=0x200；
  `SizeOfImage = SectionRVA + align_up(max(VirtualSize, SizeOfRawData), SectionAlignment)`。

## 参考

- MS Learn: PE Format → .rsrc section（Resource Directory 一节）
- 实测基准：`notepad.exe`（Windows 11 x64）
