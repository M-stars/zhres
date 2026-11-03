# .NET 托管资源格式笔记

> 实现依据：ECMA-335（COR20 头）+ dotnet/runtime 官方
> ResourceReader/ResourceWriter 源码逐行核对 + csc 生成样本与系统真实
> 程序集（GAC）实测。回写结果用 **官方 ResourceReader** 交叉验证。

## 定位

托管资源不在 Win32 资源树里，而是由 COR20 头（DataDirectory[14]）
指向的 CLI 元数据区承载。因此 `UpdateResourceW` 无法触达——本适配器
通过 **PE 手术**（原位/扩节）改写 blob 并修补 COR20 头的 Resources
RVA/Size 字段。

## 托管资源 blob（外层）

```
entry* := [DWORD size][size 字节数据][补齐到 8 字节对齐]
```

- 名字在 **ManifestResource 元数据表**（v2 首批用条目序号做 id，
  名字留待后续接入元数据解析）
- 条目内容为 `.resources`（ResourceWriter 二进制）时魔数 0xBEEFCACE

## .resources（RuntimeResource / ResourceWriter 格式）

```
ResourceManager 头:
  int32 magic = 0xBEEFCACE
  int32 resMgrHeaderVersion (=1)
  int32 numBytesToSkip
  v1 时: [7bit 字节长 + UTF-8 readerType 名]
         [7bit 字节长 + UTF-8 resourceSetType 名]
  （v2+ 头直接跳过 numBytesToSkip）

RuntimeResourceSet 头（userState）:
  int32 version (1 或 2)
  int32 numResources
  int32 numTypes
  numTypes × [7bit 长度 + UTF-8 类型名]
  【8 字节对齐】
  numResources × int32 nameHash      ← 升序
  numResources × int32 namePosition  ← 名字区内偏移
  int32 dataSectionOffset            ← 相对 .resources 内容起始

名字区（names_start .. dataSectionOffset）:
  每项: [7bit 字节长][UTF-16LE 名][int32 dataPos]

数据区（dataSectionOffset 起）:
  每项（按 dataPos 寻址，7bit typeCode 开头）:
    String(1):  [7bit 字节长][UTF-8]      ← 注意是 UTF-8 不是 UTF-16！
    Boolean/Char/Byte/.../TimeSpan: 定长
    ByteArray(0x20)/Stream(0x21): int32 长度 + 字节
    用户类型(≥0x40): BinaryFormatter 变长（无长度前缀）
```

**踩坑记录**：

1. String 值是 **UTF-8**（BinaryWriter.ReadString 语义）——想当然写成
   UTF-16 会全错。名字区才是 UTF-16LE。
2. hash 表与 namePos 表是**两个独立数组**（先全部 hash、再全部
   namePos），不是交错对。
3. hash 表前有 8 字节对齐（ResourceReader 同款处理）。
4. 用户类型记录无长度前缀——重建时用 **dataPos 排序切片**推边界
   （记录在数据区内按发射序连续存放），切片失败（dataPos 非递增）
   则放弃重建、原样保留。
5. COR20 头字段偏移：Resources RVA = coff+24、Size = coff+28
   （cb/版本/元数据/flags/ep 共 24 字节在前）。曾误按资源 blob 的
   RVA 换算，补丁打飞 144 字节。

## PE 手术策略（按优先级）

1. **节内余量**：新 blob 放进旧 blob 所在节（通常 .text）的 raw
   末尾余量内——不动节表，只改 Resources Size。首选。
2. **扩展最后一个节**：尾部无 overlay 时把 blob 追加到最后一个节的
   raw 之后（改 SizeOfRawData/VirtualSize/SizeOfImage + Resources
   RVA/Size）。文件需补零覆盖声明的新 raw 范围（否则 CLR 映射缺页，
   实测挂起）。
3. **追加新节头**：头部有空闲 40 字节时追加 .zres 节。
4. 都不行 → 拒绝（文件保持原样）。

## 限制（如实告知）

- **强名签名 / Authenticode**：修改后验签失败——警告但不绕过
- **WPF BAML**（.g.resources 中 .baml 键）：识别到即原样保留
  （BAML 记录流修改风险高，v2 后续）
- **宿主 exe（apphost）**：.NET Core/5+ 的 exe 只是原生宿主，
  资源在配套 .dll 里——对 dll 操作即可
- 本机杀软可能拦截/恢复被修改的 .NET 程序集（实测 Defender 会
  恢复原文件）——这是环境行为；验证用官方 ResourceReader 交叉读
