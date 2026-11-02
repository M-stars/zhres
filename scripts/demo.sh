#!/bin/sh
# demo.sh —— zhres 端到端演示（离线）。
#
# 流程：构建 DemoApp（可选）→ extract → import CSV（离线翻译）→
#       writeback → verify → restore（SHA-256 循环校验）。
# 用法：在仓库根目录执行  sh scripts/demo.sh
# 前提：pip install -e .；构建需要 MinGW-w64（windres + gcc），
#       无法构建时可用已有的 DemoApp.exe 跳过。
set -e
cd "$(dirname "$0")/.."

DEMO_DIR=demo/DemoApp
EXE="$DEMO_DIR/DemoApp.exe"
PROJECT="$DEMO_DIR/DemoApp.zhres-project.json"

echo "=== [0/6] 检查环境 ==="
if command -v zhres >/dev/null 2>&1; then
    zhres_cmd() { zhres "$@"; }
elif command -v py >/dev/null 2>&1; then
    zhres_cmd() { py -3.12 -m zhres "$@"; }
else
    zhres_cmd() { python -m zhres "$@"; }
fi

if [ ! -f "$EXE" ]; then
    echo "未找到 $EXE，尝试用 MinGW 构建 ..."
    if command -v windres >/dev/null 2>&1 && command -v gcc >/dev/null 2>&1; then
        sh "$DEMO_DIR/build_mingw.sh"
    else
        echo "错误：没有 DemoApp.exe 也没有 MinGW 工具链（windres/gcc）。" >&2
        echo "请先安装 WinLibs（winget install BrechtSanders.WinLibs.POSIX.UCRT）或自行构建。" >&2
        exit 1
    fi
fi

echo
echo "=== [1/6] extract：提取界面资源 ==="
zhres_cmd extract "$EXE" -o "$PROJECT"

echo
echo "=== [2/6] import：离线导入人工翻译（examples/demo-translations.csv）==="
zhres_cmd import "$PROJECT" examples/demo-translations.csv --tm demo_tm.db

echo
echo "=== [3/6] writeback：备份 → 回写 → 字体补丁 → 自动校验 ==="
zhres_cmd writeback "$PROJECT"

echo
echo "=== [4/6] verify：复核目标文件 ==="
zhres_cmd verify "$PROJECT"

echo
echo ">>> 演示程序界面已持久汉化。可运行 $EXE 查看中文菜单/对话框/状态栏。"

echo
echo "=== [5/6] restore：还原原版（SHA-256 循环校验）==="
zhres_cmd restore "$EXE" --yes

echo
echo "=== [6/6] 完成：全部通过 ==="
