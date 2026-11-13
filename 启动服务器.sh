#!/usr/bin/env bash
# Linux / macOS 直接以源码方式启动（无需打包）
cd "$(dirname "$0")"
PORT=${1:-8080}
exec python3 app.py --port "$PORT"
