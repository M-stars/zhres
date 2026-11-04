# Unity 包裹文件（UnityFS .bundle）格式笔记

> 实现依据：UnityPy BundleFile.py / CompressionHelper.py /
> enums/BundleFile.py（ArchiveFlags）逐字段对照；合成样本
> （LZ4 存储块 / LZMA 块信息 / v6、v7 头对齐）round-trip 测试。

## 头（全大端）

```
strz  "UnityFS"          （UnityWeb/UnityRaw 老签名暂不支持）
u32   格式版本            （3–7）
strz  version_player
strz  version_engine
i64   size（包裹总长）
u32   blocks_info 压缩长
u32   blocks_info 解压长
u32   flags
```

flags（引擎 <2020 与 ≥2020 对 0x200 解释不同，须按引擎版本裁定）：

| 位 | 含义 |
|---|---|
| 0x3F | 块信息压缩类型（0 无 / 1 LZMA / 2 LZ4 / 3 LZ4HC） |
| 0x40 | 块信息与目录合并（单一 blob） |
| 0x80 | 块信息在文件尾（否则紧跟头部） |
| 0x100 | 老版 Web 插件兼容（→ 拒绝） |
| 0x200 | 新引擎：数据前 16 对齐；老引擎：**UnityCN 加密**（→ 拒绝） |
| 0x400/0x1000 | 新引擎 UnityCN 加密（→ 拒绝） |

格式版本 ≥ 7（或引擎 ≥ 2019.4.15 的 v6）在头部之后 **16 字节对齐**
再读块信息——读写两侧规则必须一致。

## 块信息（解压后，大端）

```
16 字节 hash
i32 块数
[每块：u32 解压长 u32 压缩长 u16 flags]     flags&0x3F = 压缩类型
i32 节点数
[每节点：i64 偏移 i64 长度 u32 flags strz 路径]
```

节点（如 `CAB-xxxx`）指向全部数据块**解压后**拼接缓冲的位置，
每个节点是一个 SerializedFile（复用 unity.py 解析 v15–22）。

## LZ4 / LZMA

- **LZ4/LZ4HC 块**：纯 Python 解压器（零第三方依赖；LZ4 块格式 =
  字面量/匹配序列，LZ4HC 输出同构）。只解压——重建用未压缩块
- **LZMA**：Unity 的 5 字节裸 LZMA1 头（props+dict_size，无 size），
  标准库 `lzma.FORMAT_RAW + FILTER_LZMA1` 解压

## 回写：整体重建为未压缩块

1. 解压全部块 → 缓冲；逐节点解析 SerializedFile
2. 节点内改写 TextAsset（与 .assets 相同的追加+就地修补）
3. 全部节点内容依原顺序重排 → 按 128 KiB 分块，**全部未压缩**
   （flags=0 是合法布局，读取方按块表逐块拼接）
4. 块信息不压缩（flags=0x40 合并）紧跟头部；size 回填
5. 节点内 SerializedFile 头部 fileSize 同步（v22 大端 i64 @0x18；
   v15–21 u32 @4）

## 明确拒绝（报错不猜测）

- UnityCN 加密包裹（国产手游常见）
- UnityWeb / UnityRaw / UnityArchive 签名
- 未知的压缩类型 / 无法解压的块信息
