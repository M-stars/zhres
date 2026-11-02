# 决策记录

按任务书 §15.1 记录所有自主决策（决策 / 理由 / 影响）。

## Phase 0 —— 环境

### D001 Python 解释器：winget 安装 Python 3.12.10
- **决策**：系统仅有 Python 3.9.13（低于任务书 ≥3.10 要求），通过
  `winget install Python.Python.3.12` 安装 3.12.10，全部开发/测试以
  `py -3.12` 运行。
- **理由**：3.12 自带 tomllib（任务书配置首选格式），免去 3.10 的
  zhres.json 回退路径被长期作为主路径。
- **影响**：config.py 仍保留 3.10 回退逻辑与 zhres.json 支持（未删），
  但 CI/日常验证均在 3.12 上。

### D002 编译器：winget 安装 WinLibs (GCC 16.2.0 + windres 2.47)
- **决策**：`winget install BrechtSanders.WinLibs.POSIX.UCRT`。
- **理由**：Phase 0 明确此路径；无需触发 §9 兜底条款，demo 可本机构建。
- **影响**：demo/DemoApp 用 `build_mingw.sh` 构建；gcc 不在系统 PATH，
  构建脚本内用绝对路径或提示。

## Phase 1 —— 骨架 / winapi / RT_STRING

### D010 新增模块 errors.py（任务书结构清单外）
- **决策**：`ZhresError` 层级独立成 `src/zhres/errors.py`，而非塞进
  `__init__.py`。
- **理由**：任务书 §15.5 要求自定义异常层级；独立模块避免 __init__
  膨胀、无循环导入。
- **影响**：结构清单新增一个文件。

### D011 Enum* 回调 type/name 参数用 c_void_p 接收
- **决策**：回调原型里 type/name 声明为 `c_void_p`，收到的值先经
  `decode_intresource`（高 16 位为 0 → 序数）再转字符串。
- **理由**：附录 C 的做法（声明 LPCWSTR 再判序数）在 ctypes 下有把
  小整数当指针解引用的风险；c_void_p 拿到原始值后自行判定最稳妥。
- **影响**：winapi.py 内部实现细节，对外接口不变。

### D012 PE 资源目录偏移编码：一律"节相对"（重要发现）
- **决策**：目录表内所有偏移（名字字符串、子目录、数据项）相对**资源
  节起始**；仅数据项内 DataRVA 是绝对 RVA。详见
  `docs/formats/RESOURCEDIR.md`（notepad.exe 实证）。
- **理由**：合成 PE 初版按"绝对 RVA"编码，加载器报 193/1813；与真实
  文件逐字节对照后裁定。
- **影响**：仅影响 fixture_pe.py（真实回写走系统 API，无此问题）。

### D013 字符串表 char_count = UTF-16 码元数（非 Python 字符数）
- **决策**：`serialize_block` 用 `len(s.encode('utf-16-le'))//2` 作为
  char_count。
- **理由**：emoji 等 astral 字符 1 字符 = 2 码元；Windows 文档语义为
  码元。round-trip 测试暴露（含 🎉 的 block 无法字节还原）。
- **影响**：stringtable.py / fixture_pe.py 同步修正。

### D014 解析失败的资源：整体保留、零条目、RT_X_failed 计数
- **决策**：某资源解析抛 ParseError 时，不产生任何条目（不输出"半截"
  条目），resources_seen 记 `RT_<TYPE>_failed`，警告列出。
- **理由**：半截条目的 id 不稳定，verify 无法依赖；保守可回滚。
- **影响**：任务书铁律 (d) 的具体化；失败资源在回写阶段保持原字节。

### D015 字符串表空槽位不产生条目
- **决策**：char_count=0 的空槽位不生成 Entry（其余 skip 规则仍记录
  skipped 条目）。
- **理由**：真实程序字符串表空槽位极多，记录会淹没工程文件；空槽位
  也永远不会被翻译。
- **影响**：verify 的 id 对照不受影响（空槽位两侧都不产生 id）。

### D016 非常规 block（条目数 <16）保持原条目数
- **决策**：parse 按实际字节读出 N 条（N 可 <16），serialize 重建 N 条
  （round-trip 优先）；仅当译文目标 position ≥ N 时补空串到该位。
- **理由**：任务书同时要求"block 保持 16 条"与"round-trip 字节级"，
  真实世界 block 恒为 16，冲突只在病态数据出现；此时字节保真优先。
- **影响**：apply 里的补齐逻辑仅是兜底。

### D017 法律提示"首次运行"用标记文件
- **决策**：`~/.zhres/.legal-notice-shown` 存在则不再提示（A12）。
- **理由**：满足验收项且不打扰；无注册表/安装器依赖。
- **影响**：测试里隔离 USERPROFILE。

### D018 新增测试文件 test_pe_winapi.py
- **决策**：任务书测试清单外加此文件，承载 winapi 枚举 / packerdetect
  （含 A9 壳样本）测试。
- **理由**：壳检测与枚举是文件级行为，归属其余清单文件都牵强。
- **影响**：测试结构微调。

### D019 build/ 目录不入库
- **决策**：`.gitignore` 排除 `build/`（临时探针脚本、冒烟产物）。
- **理由**：调试产物与仓库无关；探针脚本（probe_builder.py 等）是
  一次性调查工具。
- **影响**：无。

### D020 CLI 输出的资源类型名
- **决策**：标准类型用 RT_XXX 名（RT_STRING 等）；RT_RCDATA 区分
  `RT_RCDATA_DFM`（TPF0 签名）/ `RT_RCDATA_skipped`；解析失败记
  `RT_<TYPE>_failed`；自定义类型名原样加后缀。
- **理由**：与任务书 §4.1 resources_seen 示例键名对齐。
- **影响**：project JSON 的 resources_seen 键集合可能超出任务书示例
  （仅新增 _failed 后缀类）。

## Phase 2 —— MENU / DIALOG / DFM 适配器

### D030 MENUEX 头部没有独立的 dwHelpId 字段（与 Wine 不同）
- **决策**：MENUEX 头 = WORD version + WORD offset + offset 字节扩展
  （扩展内容即 dwHelpId 等）；读完扩展直接开始项流。**不**像 Wine
  `LoadMenuIndirectW` 那样再读 4 字节 dwHelpId。
- **理由**：按 Wine 实现解析真实资源全部错位；修正后 195 个本机系统
  程序的真实 MENUEX 资源 100% 字节还原，且手工构造的 MENUEX 能被
  真实 `LoadMenuW` 加载。
- **影响**：docs/formats/MENU.md 记录差异；ReactOS 同源代码同样多读。

### D031 MENUEX 布局的穷举验证方法
- **决策**：对"flags 位宽 × 前后对齐 × helpid 位置 × 头扩展"共 72 种
  参数组合做穷举，以"精确消费 + 字节还原全部 195 个真实资源"为判据
  锁定唯一布局族。
- **理由**：MS Learn 已无 MENUEX 二进制格式文档，Wine 实现与真实文件
  不符；穷举是可复现、无歧义的裁定方式。
- **影响**：build/ 下的搜索脚本不入库；方法记录于 docs/formats/MENU.md。

### D032 DFM 子对象没有 0xFF 前缀（与常见描述不同）
- **决策**：普通子对象直接以类名长度字节开始；仅 `$F0|flags` 高 nibble
  命中才视为前缀（inherited/inline/ffChildPos）。
- **理由**：本机 Inno（Delphi 系）与 Cheat Engine（Lazarus 系）两套
  写入方的真实 DFM 中子对象均无 0xFF；`TReader.ReadPrefix` 的判定
  `b & 0xF0 == 0xF0` 与"类名长度恒小于 0x10"不冲突。
- **影响**：解析器同时兼容有/无前缀两种形态，前缀字节原样保留。

### D033 DFM 的 TStrings 属性 = 带点属性名 + vaList
- **决策**：`Lines.Strings` 以**单个带点属性名**存储，值类型 vaList，
  列表项 = 类型字节 + 值。
- **理由**：TWizardForm 真实样本逐字节验证。
- **影响**：提取白名单按 `.Strings` 后缀与裸名 `Lines`/`Items` 双轨匹配。

### D034 vaCollection 采用保守解析
- **决策**：每项 = 可选顺序号(vaInt8/16/32) + 可选 0x01 标记 + 属性体
  （0x00 终止）；顺序号与 0x01 标记的有无**按原文件保留**。
- **理由**：Delphi TWriter 标准输出带 0x01；不假设其他写入方一致，
  round-trip 优先。
- **影响**：serialize 按解析到的原始形态重建。

### D035 真实资源样本提交 tests/fixtures/
- **决策**：7 个小样本（2×Inno DFM、2×Lazarus DFM、3×MENUEX，
  共约 18KB）随仓库分发作为 round-trip 测试基线。
- **理由**：测试必须自包含（fresh clone 可跑）；这些是几 KB 的结构
  数据，类似 Wine 测试的做法。
- **影响**：.gitignore 的 build/ 排除不影响 tests/fixtures/。

### D036 标准 DLGTEMPLATE 字体块只有 pointsize + typeface
- **决策**：非 EX 模板按 2 字段解析字体块；weight/italic/charset 仅
  EX 模板存在。EX 的 pointsize==0x7FFF 表示消息框字体（其后无字段）。
- **理由**：MS 文档 + Wine `DIALOG_ParseTemplate32` 一致；windres 产物
  round-trip 验证。
- **影响**：字体补丁对标准模板只替换 typeface（无 charset 可写）。

### D037 DFM Entry id 的 position 段 = 属性路径
- **决策**：按任务书 §4.2，DFM 的 id 形如
  `RT_RCDATA/<name>/<lang>/Form1/Lines/3`；apply 按完整路径段匹配
  （不用先序号）。
- **理由**：任务书明文要求；路径对同一 exe 天然稳定。
- **影响**：同 DFM 内对象名重复时可能撞路径（Delphi 组件名唯一，
  实际不发生）；`_position_of` 取第三段之后全部。

### D038 ANSI 解码失败 → 记录 skipped 条目（非静默丢弃）
- **决策**：vaString/vaLString 无法按 GBK 解码时，仍生成
  status=skipped 的条目（skip_reason=ANSI(GBK) 解码失败）。
- **理由**：任务书 §5.4 "转换失败则 skipped"；静默丢弃会让用户看不到
  该属性存在。
- **影响**：回写时 skipped 条目不参与写入，原文保持。

### D039 对话框控件对齐基准 = 资源起始
- **决策**：控件的 DWORD 对齐以**资源数据起始**为 0 基准（适配器只见
  字节流，无从得知节内偏移）。
- **理由**：RC/ windres 产物资源数据在节内 4 字节对齐，此时与加载器
  的运行时对齐一致；极端非对齐资源（罕见）会有偏差，记录为已知限制。
- **影响**：fixture 构建器同样按全长对齐（头 18/26 字节计入）。
