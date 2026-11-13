@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 打包 云聊提醒（绿色版 + 安装包）

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo 正在安装 PyInstaller ...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo 安装失败，请手动执行：pip install pyinstaller
        pause
        exit /b 1
    )
)

echo.
echo [1/2] 打包绿色版 云聊提醒.exe ...
python -m PyInstaller Notify.spec --noconfirm --distpath dist --workpath build\notify
if errorlevel 1 goto fail
if not exist "dist\云聊提醒.exe" goto fail

echo.
echo [2/2] 打包安装包 云聊提醒-安装包.exe ...
python -m PyInstaller Installer.spec --noconfirm --distpath dist --workpath build\installer
if errorlevel 1 goto fail
if not exist "dist\云聊提醒-安装包.exe" goto fail

echo.
echo 正在复制到网站下载目录 web\downloads ...
if not exist "web\downloads" mkdir "web\downloads"
copy /y "dist\云聊提醒.exe" "web\downloads\云聊提醒.exe" >nul
copy /y "dist\云聊提醒-安装包.exe" "web\downloads\云聊提醒-安装包.exe" >nul

echo.
echo ============================================
echo  打包完成：
echo    dist\云聊提醒.exe            （绿色版，9 MB 左右）
echo    dist\云聊提醒-安装包.exe     （双击安装，含卸载）
echo  已同步到 web\downloads\，网页「桌面助手」里可直接下载
echo ============================================
pause
exit /b 0

:fail
echo.
echo 打包失败，请查看上面的错误信息。
pause
exit /b 1
