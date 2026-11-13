# -*- coding: utf-8 -*-
# Copyright (c) 2026 夜屿
# Yunliao (云聊) is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
"""core 包初始化：版本号定义 + 把随包 vendor 目录加入 sys.path。

PyMySQL 以「随包 vendor」方式分发（vendor/pymysql），
这样打包成 EXE 后无需用户在系统里 pip install 任何东西。
"""
import os
import sys

# ---------------------------------------------------------------- 版本号
# 单一事实来源：升级版本时只改这一处。
# （历史上版本号散落在 app.py / api/admin.py / client.py 等多处硬编码，
#   发版时极易漏改，导致界面显示的版本与实际标签不一致。）
__version__ = "1.1.2"
APP_NAME = "云聊"
APP_NAME_EN = "Yunliao"


def _add_vendor():
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [
        os.path.join(os.path.dirname(here), "vendor"),   # 源码运行：new/vendor
        getattr(sys, "_MEIPASS", "") and os.path.join(sys._MEIPASS, "vendor"),  # 打包后
    ]
    for p in roots:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


_add_vendor()
