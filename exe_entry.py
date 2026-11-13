# -*- coding: utf-8 -*-
"""云聊 —— 打包入口
先显式导入 api 包（注册全部路由），再进入主程序。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: F401  注册全部路由
import app  # noqa: E402

if __name__ == "__main__":
    app.main()
