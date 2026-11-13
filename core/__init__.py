# -*- coding: utf-8 -*-
# Yunliao (云聊) - LAN collaboration platform
# Copyright (C) 2026 夜屿
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
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
__version__ = "1.1.3"
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
