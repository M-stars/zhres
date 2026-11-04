# Unity 序列化文件（.assets）格式笔记 —— TextAsset 适配

> 实现依据：本机真实 Tuanjie（团结引擎，Unity 中国版）2022.3.17 文件
> 逐字节实测 + UnityPy（社区标准实现，项目 venv 参照）交叉验证。

## 定位

Unity 的 UI 文本最常见载体是 **TextAsset**（本地化 JSON/CSV、剧情
文本）与 MonoBehaviour 内嵌字符串（如 TextMeshPro 的 m_text）。
本版适配 **TextAsset**（无需类型树即可解析），MonoBehaviour 需要
类型树或 IL2CPP 信息——暂不支持。

## SerializedFile v22 头（Unity 2022+/团结引擎，大端）

```
i64  （保留，实测为 0）
i32  version = 22
i32  version_deprecated = 0
u8   raw_endian（0 = 正文小端）
3 字节 reserved
u32  metadata_size
i64  file_size
i64  data_offset
i64  unknown
strz unity_revision（如 "2022.3.17f1c1"）
─── 之后正文为小端 ───
i32  target_platform
u8   enable_type_tree
i32  type_count
[类型表]
i32  object_count
[对象表]
...
```

（Unity 2022 把头部换成了大端 i64/i32 —— 与旧版小端 u32×4 完全
不同；这也是为什么很多老工具读不了 2022+ 文件。）

## 类型表（enable_type_tree = 0 时，发布构建常见）

```
每项: i32 class_id
      u8  is_stripped
      i16 script_type_index
      [16B script_id   仅 class_id == 114（MonoBehaviour）]
      16B old_type_hash
```

class_id 49 = **TextAsset**；114 = MonoBehaviour。

## 对象表

```
每项: 【4 对齐】i64 path_id
      i64 byte_start          ← 相对 data_offset
      u32 byte_size
      i32 type_id             ← 类型表索引
```

## TextAsset 对象体

```
i32 名长 + UTF-8 名 + 【4 对齐】
i32 脚本长 + UTF-8 脚本 + 【4 对齐】
```

## 回写策略（追加式，同 .NET 思路）

新对象数据**追加到文件尾**（4 对齐），就地修补对象表条目的
byte_start/byte_size，更新头部 file_size。老数据成为死区。
追加内容 = 名字字段之前的前缀 + 新脚本长 + 新脚本 + 4 对齐尾
——注意原尾部的对齐填充按新长度重算，不能照抄（实测照抄会让
UnityPy 报 "Expected to read N bytes"）。

## 限制（如实告知）

- enableTypeTree = 1（编辑器构建/部分游戏）需完整类型树解析——
  暂不支持，明确报错
- UnityFS / UnityWeb 包裹文件（LZ4/LZMA 压缩）暂不支持
- MonoBehaviour 内嵌字符串（TextMeshPro m_text 等）暂不支持
- IL2CPP 元程（global-metadata.dat）体系完全不同，不适用

## SerializedFile v15–21（Unity 2017–2021，全小端）

v2 游戏适配扩展覆盖的旧版本族。与 v22 的差异（依据 UnityPy
SerializedFile.py / ObjectReader.py 逐字段对照）：

```
u32  metadata_size      @0
u32  file_size          @4
u32  version            @8   （15–21；v22 此处为大端 i32）
u32  data_offset        @12  （实测一般为 0）
u8   endian             @16  （0 = 小端）
3 字节 reserved
strz unity_version
i32  target_platform
u8   enable_type_tree
i32  type_count
[类型表]                     v<16 无 isStrippedType；v≥17 多 scriptTypeIndex；
                             script_id（Hash128）仅 v≥16 且 class_id==114
                             （v<16 时为 class_id<0）
i32  object_count
[对象表]                     每项：align4 → i64 path_id → u32 byte_start（绝对）
                             → u32 byte_size → i32 type_id
                             v15/16 额外：i16 scriptTypeIndex + u8 isStripped
（script types / externals / ref types 在对象表之后，本工具不读）
```

- `byte_start` 为**绝对文件偏移**（v22 是相对 data_offset 的 i64）
- v15–21 头部**没有** fileSize 之外的扩展字段；回写追加数据后把
  `@4` 的 fileSize 同步（仅当原值等于原文件长度）
- 对象表 byte_size 字段在 `byte_start` 后 **4 字节**（v22 是 8）——
  就地修补时两者偏移不同，UnityObject.entry_size_off 区分

## 回写（v15–22 通用）

追加式：新对象数据写到文件尾（4 字节对齐），就地修补对象表的
byte_start/byte_size；v22 另更新头部 fileSize（大端 i64 @0x18）。
老数据成为死区。与 .NET PE 手术同思路，已通过 v15/16/17/19/21/22
合成样本 round-trip 测试（tests/test_gamefiles.py）。
