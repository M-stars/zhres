# RT_STRING 字符串表格式笔记

> 实现依据：MS 文档 + 实测；round-trip 经合成 fixture（含 Unicode 边缘
> 字符）与真实程序字符串表验证。这是四类资源中最简单、也最少坑的一类。

## 结构

一个 RT_STRING 资源（block）固定容纳 **16 条**字符串，对应字符串 ID
`16*N .. 16*N+15`（N = block 的资源名序数，即 ID 16..31 在 block 1、
32..47 在 block 2……）。

```
block := entry × 16
entry := WORD char_count; WCHAR chars[char_count];   // UTF-16LE，无 NUL 结尾
```

- 空条目 = `WORD 0`（没有任何字符数据）
- **char_count 以 UTF-16 码元为单位**，不是字符数——emoji 等
  astral 字符（如 🎉 U+1F389）占 1 个字符但 2 个码元。实现时用
  `len(s.encode('utf-16-le')) // 2`，用 `len(s)` 会让解析错位
  （实测踩坑：含 emoji 的 block 无法 round-trip）。
- **没有 NUL 结尾符**——长度完全由 char_count 决定
- 长度无硬限制（char_count 是 WORD，单条上限 65535 码元）

## 提取 / 回写约定

- resource_name 即 block 序号；Entry id 的 position 段为 block 内
  序号 0-15：`RT_STRING/<block>/<lang>/<pos>`
- 回写：整 block 重序列化（即使只改一条）；条目数与原 block 保持
  一致（真实世界恒为 16；若遇到条目数 <16 的病态 block，按原样
  重建以满足字节级 round-trip，需要时补空串）
- 空槽位不产生 Entry（避免工程文件被空串淹没）；其余自动跳过规则
  （纯数字/标点、单字符等）见 `adapters/base.py::auto_skip`
