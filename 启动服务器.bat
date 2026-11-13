@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 企业内网学习聊天室 - 服务端

set "PYCMD="
python --version >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
  py --version >nul 2>nul
  if not errorlevel 1 set "PYCMD=py"
)
if not defined PYCMD (
  echo 未检测到 Python，请先安装 Python 3.8 及以上版本，
  echo 或直接使用已打包好的 EnterpriseLearnChat.exe。
  echo.
  pause
  exit /b 1
)

set "PORT=8080"
set /p PORT=请输入端口号，直接回车使用 8080 ： 
if "!PORT!"=="" set "PORT=8080"

echo.
echo 正在启动服务，请勿关闭本窗口，关闭即停止服务。
echo 本机访问： http://127.0.0.1:!PORT!
echo 他人访问： http://本机内网IP:!PORT!
echo.

%PYCMD% app.py --port !PORT!
pause
