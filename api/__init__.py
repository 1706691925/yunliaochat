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
