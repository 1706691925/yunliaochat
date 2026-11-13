#!/usr/bin/env bash
# Linux / macOS 打包脚本（生成可执行文件，非 Windows exe；Windows 请使用 构建EXE.bat）
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  局域网聊天室 —— 打包（Linux/macOS）"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未检测到 python3"; exit 1
fi

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "[提示] 安装 PyInstaller..."
  python3 -m pip install pyinstaller
fi

export LC_APP_NAME=${LC_APP_NAME:-EnterpriseLearnChat}
export LC_ONEFILE=${LC_ONEFILE:-1}
export LC_CONSOLE=${LC_CONSOLE:-1}

python3 -m PyInstaller --noconfirm --clean build.spec

echo
echo "打包完成：dist/${LC_APP_NAME}"
echo "运行：./dist/${LC_APP_NAME} --port 8080"
