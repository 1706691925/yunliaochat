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
"""公共 API 辅助"""
from core import db, security, util
from core.http import ApiError


def ok(data=None):
    return data if data is not None else {}


def require(user, perm, msg="权限不足"):
    if not user:
        raise ApiError("未登录", 401)
    if not security.has_perm(user, perm):
        raise ApiError(msg, 403)
    return True


def require_role(user, roles):
    if user.get("role") not in roles:
        raise ApiError("需要更高权限", 403)


def page_args(ctx, default=20, maxn=200):
    p = max(1, ctx.int("page", 1))
    size = min(maxn, max(1, ctx.int("page_size", ctx.int("size", default))))
    return p, size, (p - 1) * size


def paged(sql_count, sql_list, args_count, args_list, ctx, default=20):
    p, size, off = page_args(ctx, default)
    total = db.scalar(sql_count, args_count)
    rows = db.query(sql_list + " LIMIT ? OFFSET ?", list(args_list) + [size, off])
    return {"total": total, "page": p, "page_size": size, "list": rows}


def audit(ctx, action, target="", detail=""):
    u = ctx.user or {}
    db.log_action(u.get("id", 0), u.get("display_name", "匿名"), action, target, detail, ctx.ip)


def clean_rows(rows, fields=None):
    out = []
    for r in rows:
        d = dict(r)
        if fields:
            d = {k: v for k, v in d.items() if k in fields}
        out.append(d)
    return out


def avatar_url(fid):
    """用户自定义头像的访问路径（fid 为 files.id 或空）；空返回空串表示用默认字母头像"""
    fid = str(fid or "").strip()
    if not fid or not fid.isdigit():
        return ""
    return "/api/files/%s/download" % fid
