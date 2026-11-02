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
