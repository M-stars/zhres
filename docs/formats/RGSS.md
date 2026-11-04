# RPG Maker XP/VX/VX Ace（RGSS）格式笔记

> 实现依据：ruby/ruby 官方 marshal.c（w_long/r_long/w_object 逐字段）
> + arc_unpacker RGSS 解码器（并用其官方测试档案验证算法，未采用其
> 任何代码/数据）；合成样本 round-trip 测试（tests/test_gamefiles.py）。

## 载体

| 引擎 | 档案 | 数据文件 | Ruby | 文本编码 |
|---|---|---|---|---|
| XP | `Game.rgssad`（v1） | `Data/*.rxdata` | 1.8 | 无标记 → GBK |
| VX | `Game.rgssad`（v1） | `Data/*.rvdata` | 1.8 | 无标记 → GBK |
| VX Ace | `Game.rgss3a`（v3） | `Data/*.rvdata2` | 1.9 | `E:T` → UTF-8 |

散装 `.rxdata`/`.rvdata`/`.rvdata2`（编辑器工程/解包产物）直接支持
（Marshal 魔数 `\x04\x08` + 扩展名识别）。`Scripts.*` 整体跳过
（内嵌 zlib 压缩的 Ruby 代码，翻译必坏）。

## RGSSAD 档案

### v1（XP/VX）

```
"RGSSAD\x00\x01"
key = 0xDEADCAFE
条目*:
  u32  名长 ^ key;      key = key*7+3
  byte* 名字（逐字节 ^ key&0xFF，逐字节 key=key*7+3）
  u32  数据长 ^ key;    key = key*7+3
  数据（用当前 key 快照 4 字节块 XOR 流解密；**主 key 不随数据推进**）
```

### v3（VX Ace）

```
"RGSSAD\x00\x03"
u32  种子;  key = 种子*9+3
条目*:
  u32 数据偏移 ^ key    （=0 → 结束标记）
  u32 数据长 ^ key
  u32 该条目数据密钥 ^ key
  u32 名长 ^ key
  byte* 名字（第 i 字节 ^ key >> ((i%4)*8)，key 不推进）
数据块（用条目密钥 XOR 流解密，位于文件尾按序排列）
```

两种算法已用外部参考实现的真实测试档案逐字节验证
（v1：`abc.txt`→"123"；v3：结束标记 `0 ^ key` 亦复现）。

## Ruby Marshal 4.8

按 marshal.c 精确实现（未修改内容序列化后**字节级一致**）：

- **变长整数**（w_long）：`0`→`0x00`；`0<x<123`→`x+5`；
  `-124<x<0`→`(x-5)&0xFF`；否则 LE 字节 + 头（正=`长度`，
  负=`256-长度`，终态判据 `x==0` / `x==-1`）。**首字节按有符号
  char 解读**
- **链接表 0 基**：对象 `'@'`/符号 `';'`，首次出现登记（w_remember
  在内容**之前**；UserDef 例外在内容之后）
- **`I` ivar 包装**：`'I'` 写在内容**之前**（String 的编码标记
  `E:T`/`E:F` 就在里面）
- **`u` UserDef**（RPG Maker 的 Table/Color/Tone/Rect）：`u` + 类名
  符号 + 长度前缀原始载荷，**原样保留**
- 顶层 Symbol、尾随数据校验均已处理

## 回写

改写字符串 → 重新序列化 Marshal → 档案整体重加密（条目顺序、
名字原样；未修改条目密文逐字节复现）。无改动时 apply 原样返回。

## 限制（如实告知）

- XP/VX 日文数据按 GBK 解码可能出现乱码条目（噪音，忽略即可）
- `TYPE_EXTENDED`（单例类）等罕见标签明确报错
- 档案重加密要求全部条目可读——损坏档案按解析失败原样保留
