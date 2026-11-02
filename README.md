# zhres

**Windows 静态资源汉化工具**：对 exe/dll 中的界面资源（字符串表、菜单、对话框、Delphi DFM）执行 **提取 → 翻译 → 回写**，让没有官方中文的 Windows 软件持久显示简体中文——无需运行时注入，改前自动备份，可一键还原。

> 仅用于汉化自己合法拥有的软件；修改后的文件请勿再分发；本工具不用于规避任何版权保护。

## 状态

🚧 开发中。当前仓库包含完整的[开发任务书](TASKBOOK.md)（技术方案、格式规格、测试与验收标准）。

## 计划中的工作流

```
zhres extract demo.exe      # 提取界面资源到翻译工程 JSON
zhres import ...            # 填入译文（人工 CSV / 翻译记忆 / 可选 LLM）
zhres writeback demo.exe    # 自动备份 → 回写资源
zhres verify demo.exe       # 校验回写结果
```

一次处理，持久生效，直到软件更新。
