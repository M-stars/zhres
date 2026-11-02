@echo off
rem build_msvc.bat —— 用 MSVC (rc + cl /W4) 构建 DemoApp。
rem 需在 "x64 Native Tools Command Prompt for VS" 中执行。
cd /d "%~dp0"
rc /fo appres.res app.rc
cl /W4 /Fe:DemoApp.exe main.c appres.res user32.lib gdi32.lib comctl32.lib /link /SUBSYSTEM:WINDOWS
if errorlevel 1 (echo BUILD_FAILED & exit /b 1)
echo BUILD_OK: %cd%\DemoApp.exe
