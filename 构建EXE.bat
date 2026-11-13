@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 企业内网学习聊天室 - 打包工具

echo ============================================================
echo   企业内网学习聊天室 - EXE 一键打包工具
echo   支持完全离线打包，内网机器无需联网
echo ============================================================
echo.

rem ---------- 1. 检测 Python ----------
set "PYCMD="
python --version >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
  py --version >nul 2>nul
  if not errorlevel 1 set "PYCMD=py"
)
if not defined PYCMD (
  echo [错误] 未检测到 Python。
  echo.
  echo 请先安装 Python 3.8 ~ 3.12，安装时必须勾选 Add Python to PATH。
  echo 离线环境可使用离线安装包 python-3.11.9-amd64.exe。
  echo.
  pause
  exit /b 1
)

echo [信息] 已检测到 Python
%PYCMD% --version

rem ---------- 2. 安装打包工具（仅打包需要，运行时零依赖） ----------
echo.
echo [提示] 检查打包工具 PyInstaller...
%PYCMD% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 PyInstaller，正在安装...
  %PYCMD% -m pip install --disable-pip-version-check --no-warn-script-location pyinstaller
)

%PYCMD% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo [错误] PyInstaller 未能导入，请查看上方日志。
  pause
  exit /b 1
)
echo [信息] 打包工具就绪。

rem ---------- 3. 选择打包方式 ----------
echo.
echo 请选择打包方式：
echo   [1] 单文件 EXE    推荐，便于分发，启动稍慢
echo   [2] 目录版        启动更快，dist 下为文件夹
echo   [3] 单文件无窗口  后台静默运行，不显示控制台
echo.
set "CHOICE=1"
set /p CHOICE=请输入 1 或 2 或 3，直接回车默认 1 ： 

set "LC_ONEFILE=1"
set "LC_CONSOLE=1"
if "!CHOICE!"=="2" (
  set "LC_ONEFILE=0"
  set "LC_CONSOLE=1"
)
if "!CHOICE!"=="3" (
  set "LC_ONEFILE=1"
  set "LC_CONSOLE=0"
)
if not defined LC_APP_NAME set "LC_APP_NAME=EnterpriseLearnChat"

echo.
echo [信息] 开始打包，请耐心等待 1 到 3 分钟...
echo.

%PYCMD% -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 (
  echo.
  echo [错误] 打包失败，请查看上方日志。
  echo.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   打包完成！
echo.
if "!LC_ONEFILE!"=="1" (
  echo   输出文件： dist\EnterpriseLearnChat.exe
) else (
  echo   输出目录： dist\EnterpriseLearnChat\EnterpriseLearnChat.exe
)
echo.
echo   使用步骤：
echo   1. 将生成的 EXE 复制到服务器任意目录，例如 D:\LearnChat
echo   2. 双击运行，首次启动会自动创建 data 数据目录
echo   3. 初始管理员密码写在 data\初始管理员密码.txt
echo   4. 浏览器访问 http://本机IP:8080
echo   5. 若 Windows 防火墙弹窗，请勾选 专用网络 允许访问
echo ============================================================
echo.
pause
