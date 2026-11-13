# -*- coding: utf-8 -*-
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
