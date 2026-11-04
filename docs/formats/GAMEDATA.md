# GameMaker 数据文件（data.win）格式笔记 —— STRG 字符串池

> 实现依据：UndertaleModTool（UndertaleModLib）源码对照
> （UndertaleChunks.cs 的 STRG 块、UndertaleIO.cs 的
> ReadUndertaleString）；合成样本 round-trip 测试。

## 文件结构（小端）

```
"FORM" + u32 总长（不含 8 字节头）
块* := [4CC][u32 长度][载荷]        GEN8/OPTN/…/STRG/TXTR/AUDO…
```

STRG 块：

```
u32  字符串数量
u32* 数量 × 绝对文件偏移（指向每个字符串条目的"长度字段"）
[0x80 对齐的零填充]
```

字符串条目（位置由指针表给出）：

```
u32  字节长
byte[length]  UTF-8 内容
0x00 终止符
```

**关键语义**（UndertaleModTool ReadUndertaleString）：其他块里的
引用指向**内容**（= 长度字段地址 + 4，读取时 `addr - 4` 定位对象）。
全游戏共享一个字符串池（相同文本合并为同一对象）。

## 回写：原位缩短替换

新译文 UTF-8 字节数 ≤ 原长时：

1. 内容**原位**重写（地址不变 → 所有引用、STRG 指针表全部有效）
2. 补 NUL 终止 + 清零残余旧字节
3. 就地更新长度字段（u32）

译文超长 → **拒绝写入**（扩长会使后续字符串整体位移、打断全文件
绝对引用；完整重写需要全部块的指针语义模型，超出安全边界）。
条目 `max_len` 记录原文 UTF-8 字节预算（英文→中文短句通常够用，
超长对话框句富余更多）。

## 限制

- 仅未压缩 FORM 布局（GMS1/GMS2 标准发行）；带壳/加密文件按解析
  失败原样保留
- `audiogroup*.dat` 无 STRG → 不识别（本来也无文本）
- 与 UnityFS 不同，GameMaker 无官方容器重排方案，故采用保守策略
