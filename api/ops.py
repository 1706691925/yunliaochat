# -*- coding: utf-8 -*-
"""扩展后台 API：小游戏、远程协助（HTTP 侧）、权限管理、数据库管理

- 小游戏：目录查询 / 成绩上报 / 排行榜
- 远程协助：被控端在线列表 / 会话查询 / HTTP 侧结束会话（完整协助流程走 WebSocket）
- 权限管理：权限矩阵读取 / 角色权限覆盖保存 / 单用户额外权限调整
- 数据库：表清单 / 数据浏览 / 只读 SELECT / 行修改 / 行删除（受 db.view / db.manage / db.sql 权限约束）
"""
import json
import re

from core import db, security, util
from core.http import ApiError, route

# ---------------- 小游戏 ----------------
@route("/api/games/catalog", "GET")
def games_catalog(ctx):
    from core import gameroom
    # 按当前用户过滤：仅返回该用户被授权（且全局开启）的游戏
    return {"games": gameroom.game_catalog(ctx.user)}


@route("/api/games/score", "POST")
def games_score(ctx):
    from core import gameroom
    game = str(ctx.get("game") or "")[:20]
    if game not in gameroom.GAMES:
        raise ApiError("未知的游戏类型")
    if not gameroom.enabled_for(ctx.user, game):
        raise ApiError("您没有该小游戏的访问权限（未授权），请联系管理员开启", 403)
    return gameroom.save_score(ctx.user, game, ctx.body or {})


@route("/api/games/leaderboard", "GET")
def games_leaderboard(ctx):
    from core import gameroom
    game = str(ctx.get("game") or "fireboy")[:20]
    if game not in gameroom.GAMES:
        raise ApiError("未知的游戏类型")
    return gameroom.leaderboard(game, ctx.int("limit", 10))


# ---------------- 远程协助（HTTP 侧） ----------------
@route("/api/admin/remote/agents", "GET", perm="remote.view")
def remote_agents(ctx):
    from core import remote
    return {"list": remote.agent_list(), "enabled": remote.feature_on()}


@route("/api/admin/remote/sessions", "GET", perm="remote.view")
def remote_sessions(ctx):
    from core import remote
    rows = remote.active_sessions()
    out = []
    for s in rows:
        out.append({k: s.get(k) for k in
                    ("id", "admin_user_id", "admin_name", "agent_user_id", "agent_name",
                     "agent_dept", "status", "requested_at", "started_at", "expires_at",
                     "max_minutes", "control", "end_reason", "input_count")})
    return {"list": out}


@route("/api/admin/remote/session/<sid>/end", "POST", perm="remote.request")
def remote_session_end(ctx):
    from core import remote
    sid = str(ctx.args.get("sid", ""))
    s = remote.session_of_conn  # noqa: F841 占位避免误用
    # 通过会话 id 直接查找：remote 未提供按 id 查，改为遍历 active_sessions
    target = None
    for x in remote.active_sessions():
        if x.get("id") == sid:
            target = x
            break
    if not target:
        raise ApiError("会话不存在或已结束")
    if not (security.has_perm(ctx.user, "remote.control") or
            target.get("admin_user_id") == ctx.user["id"]):
        raise ApiError("无权结束该协助会话")
    remote.end_session(sid, ctx.get("reason") or "管理员在后台结束", by="admin")
    return {"ok": True, "msg": "已结束协助会话"}


# ---------------- 权限管理 ----------------
def _perm_groups_public():
    """权限分组（含每个权限的键与名称），供权限矩阵 UI 渲染"""
    return [{"key": g["key"], "name": g["name"],
             "perms": [{"key": k, "name": n} for k, n in g["perms"]]}
            for g in security.PERM_GROUPS]


@route("/api/admin/permissions", "GET", perm="user.role")
def permissions_get(ctx):
    roles = []
    for rk, meta in security.ROLES.items():
        roles.append({
            "key": rk, "name": meta["name"], "level": meta["level"],
            "perms": security.expand_perms(security.base_role_perms(rk)),
            "fixed": rk == "superadmin",   # 超级管理员恒定全部权限，不可编辑
        })
    return {
        "groups": _perm_groups_public(),
        "roles": roles,
        "override": security.role_overrides(),
        "labels": security.PERM_LABELS,
    }


@route("/api/admin/permissions/roles", "PUT", perm="user.role")
def permissions_roles_save(ctx):
    mapping = ctx.get("overrides") or {}
    if not isinstance(mapping, dict):
        raise ApiError("参数格式错误")
    clean = {}
    for rk, plist in mapping.items():
        rk = str(rk)
        if rk == "superadmin" or rk not in security.ROLE_PERMS:
            continue
        perms = [str(p) for p in (plist or []) if str(p) in security.PERM_LABELS]
        clean[rk] = perms
    security.save_role_overrides(clean)
    db.log_action(ctx.user["id"], ctx.user.get("display_name", ""), "修改角色权限", "perm",
                  json.dumps(clean, ensure_ascii=False)[:500], ctx.ip)
    return {"ok": True, "msg": "角色权限已保存并立即生效", "override": security.role_overrides()}


@route("/api/admin/users/<id>/perms", "PUT", perm="user.role")
def user_perms_save(ctx):
    uid = int(ctx.args["id"])
    u = db.one("SELECT id, display_name, username, role, extra_perms FROM users WHERE id=?", (uid,))
    if not u:
        raise ApiError("用户不存在")
    if u["role"] == "superadmin":
        raise ApiError("超级管理员恒定拥有全部权限，无需也无法调整")
    plist = ctx.get("perms") or []
    if not isinstance(plist, (list, tuple)):
        raise ApiError("参数格式错误")
    perms = [str(p).strip() for p in plist if str(p).strip() in security.PERM_LABELS]
    db.update("users", {"extra_perms": ",".join(perms)}, "id=?", (uid,))
    db.log_action(ctx.user["id"], ctx.user.get("display_name", ""), "调整用户权限", "perm",
                  "%s（@%s）：%d 项额外权限" % (u["display_name"], u["username"], len(perms)), ctx.ip)
    return {"ok": True, "msg": "用户权限已保存并立即生效", "perms": perms}


# ---------------- 数据库管理 ----------------
# 不可修改的敏感列（避免通过数据库界面破坏认证体系）
_SENSITIVE_COLUMNS = {"password_hash", "token", "salt"}
# SQL 执行仅允许只读语句
_SQL_OK_RE = re.compile(r"^\s*(select|explain\s+select|with)\b", re.IGNORECASE)


def _tables():
    """列出所有数据表（引擎无关，由 db 层按当前引擎实现）。"""
    return db.table_names()


def _table_schema(name):
    cols = db.query("PRAGMA table_info(%s)" % name)
    out = []
    for c in cols:
        out.append({"name": c["name"], "type": c["type"], "pk": bool(c["pk"]),
                    "notnull": bool(c["notnull"]), "dflt": c["dflt_value"]})
    return out


def _coerce(value, coltype):
    """按列类型亲和性把前端传来的字符串/数字转换为 SQLite 存储类型"""
    if value is None:
        return None
    t = (coltype or "").upper()
    if "INT" in t:
        try:
            return int(value)
        except Exception:
            return value
    if "REAL" in t or "FLOA" in t or "DOUB" in t or "NUM" in t or "DEC" in t:
        try:
            return float(value)
        except Exception:
            return value
    if "BOOL" in t:
        return 1 if value in (True, 1, "1", "true", "True") else 0
    return value


@route("/api/admin/db/tables", "GET", perm="db.view")
def db_tables(ctx):
    out = []
    for name in _tables():
        try:
            n = db.scalar("SELECT COUNT(*) AS c FROM %s" % name)
        except Exception:
            n = -1
        out.append({"name": name, "count": n, "columns": [c["name"] for c in _table_schema(name)]})
    return {"tables": out}


@route("/api/admin/db/table/<name>", "GET", perm="db.view")
def db_table_rows(ctx):
    name = str(ctx.args.get("name", ""))
    if name not in _tables():
        raise ApiError("表不存在")
    cols = _table_schema(name)
    pk = [c["name"] for c in cols if c["pk"]] or ["id"]
    page = max(1, ctx.int("page", 1))
    size = min(200, max(1, ctx.int("page_size", 50)))
    kw = str(ctx.get("q") or "").strip()
    where, args = "", []
    if kw and pk:
        # 按主键精确匹配优先，方便直接定位记录
        try:
            where = "CAST(%s AS TEXT)=?" % pk[0]
            args = [kw]
        except Exception:
            pass
    total = db.scalar("SELECT COUNT(*) AS c FROM %s%s" % (name, (" WHERE " + where) if where else ""),
                      args)
    rows = db.query("SELECT * FROM %s%s ORDER BY %s DESC LIMIT ? OFFSET ?" %
                    (name, (" WHERE " + where) if where else "", pk[0]),
                    args + [size, (page - 1) * size])
    return {"table": name, "columns": cols, "pk": pk, "total": total,
            "page": page, "rows": rows}


@route("/api/admin/db/query", "POST", perm="db.sql")
def db_query(ctx):
    sql = (ctx.get("sql") or "").strip()
    if not sql:
        raise ApiError("请输入查询语句")
    if not _SQL_OK_RE.match(sql):
        raise ApiError("仅允许执行只读的 SELECT 查询")
    # 用户已自带 LIMIT 则不再追加，避免语法错误
    if re.search(r"\blimit\b", sql, re.IGNORECASE):
        exec_sql = sql
    else:
        exec_sql = sql + " LIMIT 500"
    try:
        rows = db.query(exec_sql)
    except Exception as e:
        raise ApiError("查询失败：%s" % e)
    cols = list(rows[0].keys()) if rows else []
    return {"columns": cols, "rows": rows, "count": len(rows), "truncated": len(rows) >= 500}


@route("/api/admin/db/row/<name>", "PUT", perm="db.manage")
def db_row_update(ctx):
    name = str(ctx.args.get("name", ""))
    if name not in _tables():
        raise ApiError("表不存在")
    cols = _table_schema(name)
    pkcols = [c["name"] for c in cols if c["pk"]] or ["id"]
    pk = ctx.get("pk") or {}
    if not isinstance(pk, dict):
        raise ApiError("缺少主键")
    values = ctx.get("values") or {}
    if not isinstance(values, dict) or not values:
        raise ApiError("缺少要修改的字段")
    ctype = {c["name"]: c["type"] for c in cols}
    where, wargs, set_list, sargs = [], [], [], []
    for k in pkcols:
        if k not in pk:
            raise ApiError("缺少主键字段：%s" % k)
        where.append("%s=?" % k)
        wargs.append(_coerce(pk[k], ctype.get(k, "TEXT")))
    for k, v in values.items():
        if k not in ctype:
            raise ApiError("字段不存在：%s" % k)
        if k in _SENSITIVE_COLUMNS:
            raise ApiError("字段 %s 属于敏感认证数据，禁止通过数据库界面修改" % k)
        set_list.append("%s=?" % k)
        sargs.append(_coerce(v, ctype[k]))
    db.execute("UPDATE %s SET %s WHERE %s" % (name, ",".join(set_list), " AND ".join(where)),
               sargs + wargs)
    db.log_action(ctx.user["id"], ctx.user.get("display_name", ""), "修改数据库记录", "db",
                  "表 %s 主键 %s" % (name, json.dumps(pk, ensure_ascii=False)[:120]), ctx.ip)
    return {"ok": True, "msg": "记录已更新"}


@route("/api/admin/db/row/<name>", "DELETE", perm="db.manage")
def db_row_delete(ctx):
    name = str(ctx.args.get("name", ""))
    if name not in _tables():
        raise ApiError("表不存在")
    cols = _table_schema(name)
    pkcols = [c["name"] for c in cols if c["pk"]] or ["id"]
    pk = ctx.get("pk") or {}
    if not isinstance(pk, dict):
        raise ApiError("缺少主键")
    ctype = {c["name"]: c["type"] for c in cols}
    where, wargs = [], []
    for k in pkcols:
        if k not in pk:
            raise ApiError("缺少主键字段：%s" % k)
        where.append("%s=?" % k)
        wargs.append(_coerce(pk[k], ctype.get(k, "TEXT")))
    db.execute("DELETE FROM %s WHERE %s" % (name, " AND ".join(where)), wargs)
    db.log_action(ctx.user["id"], ctx.user.get("display_name", ""), "删除数据库记录", "db",
                  "表 %s 主键 %s" % (name, json.dumps(pk, ensure_ascii=False)[:120]), ctx.ip)
    return {"ok": True, "msg": "记录已删除"}
