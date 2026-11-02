#!/bin/sh
# build_mingw.sh —— 用 MinGW-w64 (windres + gcc) 构建 DemoApp。
# 用法：在 MinGW 环境中执行 ./build_mingw.sh，产物 DemoApp.exe。
set -e
cd "$(dirname "$0")"
windres app.rc appres.o
gcc main.c appres.o -mwindows -o DemoApp.exe -lcomctl32
echo "BUILD_OK: $(pwd)/DemoApp.exe"
