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
"""导入即注册所有 API 路由"""
from api import base            # noqa: F401
from api import auth            # noqa: F401
from api import files           # noqa: F401
from api import chat            # noqa: F401
from api import learn           # noqa: F401
from api import classes         # noqa: F401  班级（教学组织单位）
from api import lab             # noqa: F401  题库平台（知否）嵌入与单点登录
from api import admin           # noqa: F401
from api import sites           # noqa: F401  常用网站导航
from api import batch          # noqa: F401  后台批量操作
from api import ops            # noqa: F401  小游戏 / 远程协助 / 权限 / 数据库管理
