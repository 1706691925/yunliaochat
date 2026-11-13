# -*- coding: utf-8 -*-
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
