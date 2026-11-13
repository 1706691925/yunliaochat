@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 云聊 · 桌面提醒助手

rem 优先启动已打包的 EXE，其次用 Python 运行源码
if exist "dist\云聊提醒.exe" (
    start "" "dist\云聊提醒.exe"
    exit /b 0
)
if exist "云聊提醒.exe" (
    start "" "云聊提醒.exe"
    exit /b 0
)

where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3.9+ 或使用打包好的 云聊提醒.exe
    pause
    exit /b 1
)
start "" pythonw.exe "notify_agent.py"
