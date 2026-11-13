# -*- coding: utf-8 -*-
"""双击启动云聊提醒助手（.pyw 由 pythonw 运行，不弹控制台）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_agent  # noqa: E402

if __name__ == "__main__":
    notify_agent.main()
