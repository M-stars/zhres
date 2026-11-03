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
