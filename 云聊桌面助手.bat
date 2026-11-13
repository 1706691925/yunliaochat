@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 构建 云聊提醒.exe

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo 正在安装 PyInstaller ...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo 安装失败，请检查网络或手动执行：pip install pyinstaller
        pause
        exit /b 1
    )
)

python -m PyInstaller Notify.spec --noconfirm
if errorlevel 1 (
    echo 构建失败，请查看上面的错误信息
    pause
    exit /b 1
)

copy /y "dist\云聊提醒.exe" "web\downloads\云聊提醒.exe" >nul 2>nul
echo.
echo 构建完成：dist\云聊提醒.exe
echo 已复制到 web\downloads\云聊提醒.exe（成员可从网页下载）
pause
