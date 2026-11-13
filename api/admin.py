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
"""后台管理：仪表盘、用户、部门、房间、公告、日志、设置、敏感词、备份"""
import csv
import io
import os

from core import config, db, ipsec, realtime, security, util
from core import app_ctx
from core.http import ApiError, route
from api import base


# ---------------- 仪表盘 ----------------
@route("/api/admin/dashboard", "GET", perm="admin.access")
def dashboard(ctx):
    today = util.now_ts() - 86400
    d = {
        "users": db.count("users"),
        "users_active": db.count("users", "status='active'"),
        "users_pending": db.count("users", "status='pending'"),
        "online": realtime.online_count(),
        "rooms": db.count("rooms", "archived=0"),
        "messages": db.count("messages"),
        "messages_today": db.count("messages", "created_at>?", (today,)),
        "courses": db.count("courses"),
        "exams": db.count("exam_records", "status<>'doing'"),
        "materials": db.count("materials"),
        "files": db.count("files"),
        "files_size": db.scalar("SELECT SUM(size) AS c FROM files") or 0,
        "assignments": db.count("assignments"),
        "submissions": db.count("submissions"),
        "login_today": db.scalar("SELECT COUNT(*) AS c FROM audit_logs WHERE action='登录' AND ts>?", (today,)),
    }
    d["files_size_str"] = util.human_size(d["files_size"])
    # 近 7 天消息趋势
    trend = []
    for i in range(6, -1, -1):
        start = util.now_ts() - i * 86400
        end = start + 86400
        trend.append({"day": util.ts_to_str(start, "%m-%d"),
                      "msg": db.scalar("SELECT COUNT(*) AS c FROM messages WHERE created_at>=? AND created_at<?",
                                       (start, end)),
                      "new_user": db.scalar("SELECT COUNT(*) AS c FROM users WHERE created_at>=? AND created_at<?",
                                            (start, end))})
    d["trend"] = trend
    d["top_rooms"] = db.query("SELECT r.name, COUNT(m.id) AS c FROM rooms r LEFT JOIN messages m "
                              "ON m.room_id=r.id AND m.created_at>? GROUP BY r.id ORDER BY c DESC LIMIT 6",
                              (util.now_ts() - 7 * 86400,))
    d["top_users"] = db.query("SELECT u.display_name, u.dept, COUNT(m.id) AS c FROM users u "
                              "JOIN messages m ON m.user_id=u.id WHERE m.created_at>? "
                              "GROUP BY u.id ORDER BY c DESC LIMIT 8", (util.now_ts() - 7 * 86400,))
    d["recent_logs"] = db.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8")
    for r in d["recent_logs"]:
        r["time"] = util.ts_to_str(r["ts"])
    d["dept_stats"] = db.query("SELECT dept AS name, COUNT(*) AS c FROM users GROUP BY dept ORDER BY c DESC")
    d["server"] = {"started_at": app_ctx.APP.started_at, "started_str": util.ts_to_str(app_ctx.APP.started_at),
                   "uptime_hours": round((util.now_ts() - app_ctx.APP.started_at) / 3600.0, 1),
                   "data_dir": config.CFG.data_dir,
                   "db_size": util.human_size(db.db_size()),
                   "db_engine": _db_engine_label(),
                   "version": app_ctx.APP.version}
    return d


def _db_engine_label():
    """当前数据库引擎的可读名称（SQLite / MySQL 自动识别）。"""
    try:
        eng = db.engine()
        return eng.display_name if eng is not None else "未初始化"
    except Exception:
        return "未知"


# ---------------- 用户管理 ----------------
def _email_taken(email, exclude_id=None):
    """邮箱是否已被别的账号占用。

    登录支持「账号或邮箱」，所以邮箱必须唯一，否则登录无法确定身份。
    用户自助注册/改资料在 api/auth.py 里也做同样校验。
    """
    email = str(email or "").strip()
    if not email:
        return False
    if exclude_id:
        return bool(db.one("SELECT 1 FROM users WHERE LOWER(email)=? AND id<>?",
                           (email.lower(), exclude_id)))
    return bool(db.one("SELECT 1 FROM users WHERE LOWER(email)=?", (email.lower(),)))


def _norm_games(raw):
    """规范化小游戏授权字段：只保留合法 game key，逗号分隔（超级管理员恒有全部，授权字段对其无意义）"""
    from core import gameroom
    raw = str(raw or "")
    valid = set(gameroom.GAMES.keys())
    return ",".join(sorted({g.strip() for g in raw.split(",") if g.strip() in valid}))


def _avatar_url(fid):
    """自定义头像访问路径（fid 为 files.id 或空；空=默认字母头像）"""
    fid = str(fid or "").strip()
    if not fid or not fid.isdigit():
        return ""
    return "/api/files/%s/download" % fid


def user_row(r):
    return {"id": r["id"], "username": r["username"], "display_name": r["display_name"],
            "role": r["role"], "role_name": security.role_name(r["role"]), "dept": r["dept"],
            "position": r["position"], "title": r["title"], "email": r["email"], "phone": r["phone"],
            "status": r["status"], "online": realtime.is_online(r["id"]),
            "created_at": r["created_at"], "created_str": util.ts_to_str(r["created_at"]),
            "last_login": r["last_login"], "last_login_str": util.ts_to_str(r["last_login"]),
            "learn_minutes": r["learn_minutes"], "points": r["points"],
            "games": r["games"] or "",   # 小游戏授权（逗号分隔 game key，默认空=全关）
            "avatar": _avatar_url(r.get("avatar", "")),   # 自定义头像访问路径（空=默认字母头像）
            "must_change_pwd": r["must_change_pwd"], "locked": (r["locked_until"] or 0) > util.now_ts(),
            "color": util.color_of(r["username"])}


@route("/api/admin/users", "GET", perm="user.manage")
def users(ctx):
    where, args = ["1=1"], []
    for k in ("role", "dept", "status"):
        if ctx.get(k):
            where.append("%s=?" % k)
            args.append(ctx.get(k))
    if ctx.get("q"):
        where.append("(username LIKE ? OR display_name LIKE ? OR email LIKE ? OR phone LIKE ?)")
        args += ["%%%s%%" % ctx.get("q")] * 4
    if ctx.int("online", 0):
        ids = realtime.online_ids() or [0]
        where.append("id IN (%s)" % ",".join("?" * len(ids)))
        args += ids
    p, size, off = base.page_args(ctx, 20, 500)
    total = db.scalar("SELECT COUNT(*) AS c FROM users WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT * FROM users WHERE %s ORDER BY id DESC LIMIT ? OFFSET ?"
                    % " AND ".join(where), args + [size, off])
    return {"total": total, "list": [user_row(r) for r in rows],
            "depts": [r["name"] for r in db.query("SELECT name FROM depts ORDER BY sort, id")]}


@route("/api/admin/users/<id>/devices", "GET", perm="user.manage")
def user_devices(ctx):
    """指定用户的登录客户端/设备：当前活跃会话 + 最近登录记录（管理员可据此定位异常登录）"""
    uid = int(ctx.args["id"])
    row = db.one("SELECT id, username, display_name FROM users WHERE id=?", (uid,))
    if not row:
        raise ApiError("用户不存在", 404)
    cur = db.one("SELECT token, ip, ua, created_at, last_seen FROM sessions WHERE user_id=? "
                 "ORDER BY last_seen DESC LIMIT 1", (uid,))
    current = None
    if cur:
        current = {"client": util.client_of(cur["ua"]), "ip": cur["ip"] or "",
                   "login_at": util.ts_to_str(cur["created_at"]),
                   "last_seen": util.ts_to_str(cur["last_seen"]) if cur["last_seen"] else ""}
    rows = db.query("SELECT ts, ip, detail FROM audit_logs WHERE actor_id=? AND action='登录' "
                    "ORDER BY id DESC LIMIT 20", (uid,))
    history = [{"ts": util.ts_to_str(r["ts"]), "ip": r["ip"] or "", "detail": r["detail"] or ""}
               for r in rows]
    return {"user": {"id": row["id"], "name": row["display_name"], "username": row["username"]},
            "current": current, "history": history}


@route("/api/admin/users/<id>/kick", "POST", perm="user.manage")
def user_kick_sessions(ctx):
    """强制下线指定用户的全部登录会话（删除会话 + 旧 WebSocket 立即断开）"""
    uid = int(ctx.args["id"])
    row = db.one("SELECT id, username, display_name FROM users WHERE id=?", (uid,))
    if not row:
        raise ApiError("用户不存在", 404)
    if row["id"] == ctx.user["id"]:
        raise ApiError("不能强制下线自己的会话")
    app_ctx.APP.kick_other_sessions(uid)
    base.audit(ctx, "强制下线", row["display_name"], "清空该账号全部登录会话")
    return {"msg": "%s 已强制下线" % row["display_name"]}


@route("/api/admin/users", "POST", perm="user.manage")
def user_create(ctx):
    username = (ctx.require("username") or "").strip().lower()
    if db.one("SELECT 1 FROM users WHERE username=?", (username,)):
        raise ApiError("用户名已存在")
    if _email_taken(ctx.get("email")):
        raise ApiError("该邮箱已被其他账号使用")
    pwd = ctx.get("password") or security.gen_initial_password()
    err = security.password_error(pwd, int(app_ctx.APP.cfg.get("password_min_len", 8)),
                                  bool(app_ctx.APP.cfg.get("password_need_complex", True)))
    if err and not ctx.get("password"):
        pwd = security.gen_initial_password()
    elif err:
        raise ApiError(err)
    uid = db.insert("users", {"username": username, "display_name": ctx.get("display_name") or username,
                              "password_hash": security.hash_password(pwd),
                              "role": ctx.get("role", "student"), "dept": ctx.get("dept", ""),
                              "position": ctx.get("position", ""), "title": str(ctx.get("title", "") or "")[:20],
                              "email": ctx.get("email", ""),
                              "phone": ctx.get("phone", ""), "status": ctx.get("status", "active"),
                              "must_change_pwd": ctx.int("must_change_pwd", 1),
                              "games": _norm_games(ctx.get("games", "")),
                              "created_at": util.now_ts(), "created_by": ctx.user["id"]})
    for r in db.query("SELECT id FROM rooms WHERE type IN ('public','announce') AND archived=0"):
        db.execute("INSERT OR IGNORE INTO room_members (room_id,user_id,role,joined_at) VALUES (?,?,'member',?)",
                   (r["id"], uid, util.now_ts()))
    base.audit(ctx, "创建用户", username, "角色 %s" % ctx.get("role", "student"))
    return {"id": uid, "password": pwd, "msg": "用户已创建，初始密码：%s" % pwd}


@route("/api/admin/users/<id>", "PUT", perm="user.manage")
def user_update(ctx):
    uid = int(ctx.args["id"])
    row = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise ApiError("用户不存在", 404)
    if row["role"] == "superadmin" and ctx.user["role"] != "superadmin":
        raise ApiError("无权修改超级管理员")
    data = {k: ctx.get(k) for k in ("display_name", "role", "dept", "position", "title", "email",
                                    "phone", "status", "extra_perms") if ctx.get(k) is not None}
    if "games" in ctx.body:
        data["games"] = _norm_games(ctx.get("games", ""))
    if "title" in data:
        data["title"] = str(data["title"] or "").strip()[:20]
    # 邮箱参与登录，必须唯一（排除自己）
    if "email" in data:
        data["email"] = str(data["email"] or "").strip()
        if _email_taken(data["email"], uid):
            raise ApiError("该邮箱已被其他账号使用")
    if "must_change_pwd" in ctx.body:
        data["must_change_pwd"] = ctx.int("must_change_pwd")
    if data:
        db.update("users", data, "id=?", (uid,))
    base.audit(ctx, "修改用户", row["username"], str(list(data.keys())))
    return {"msg": "已保存"}


@route("/api/admin/users/<id>", "DELETE", perm="user.manage")
def user_delete(ctx):
    uid = int(ctx.args["id"])
    if uid == ctx.user["id"]:
        raise ApiError("不能删除自己")
    row = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise ApiError("用户不存在", 404)
    if row["role"] == "superadmin":
        raise ApiError("不能删除超级管理员")
    db.delete("sessions", "user_id=?", (uid,))
    db.delete("room_members", "user_id=?", (uid,))
    db.delete("users", "id=?", (uid,))
    base.audit(ctx, "删除用户", row["username"])
    return {"msg": "已删除"}


@route("/api/admin/users/<id>/reset-pwd", "POST", perm="user.manage")
def reset_pwd(ctx):
    uid = int(ctx.args["id"])
    pwd = ctx.get("password") or security.gen_initial_password()
    err = security.password_error(pwd, int(app_ctx.APP.cfg.get("password_min_len", 8)),
                                  bool(app_ctx.APP.cfg.get("password_need_complex", True)))
    if err and ctx.get("password"):
        raise ApiError(err)
    db.update("users", {"password_hash": security.hash_password(pwd), "must_change_pwd": 1,
                        "failed_attempts": 0, "locked_until": 0}, "id=?", (uid,))
    db.delete("sessions", "user_id=?", (uid,))
    base.audit(ctx, "重置密码", str(uid))
    return {"password": pwd, "msg": "密码已重置为 %s" % pwd}


@route("/api/admin/users/<id>/toggle", "POST", perm="user.manage")
def toggle_user(ctx):
    uid = int(ctx.args["id"])
    row = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise ApiError("用户不存在", 404)
    if uid == ctx.user["id"]:
        raise ApiError("不能停用自己的账号")
    if row["role"] == "superadmin" and ctx.user["role"] != "superadmin":
        raise ApiError("无权停用超级管理员")
    if row["status"] == "blacklist":
        raise ApiError("该用户已在黑名单中，请先移出黑名单再停用/启用")
    st = "disabled" if row["status"] == "active" else "active"
    reason = (ctx.get("reason") or "").strip()[:50]
    db.update("users", {"status": st}, "id=?", (uid,))
    if st == "disabled":
        # 封禁：清会话 → 踢下线 → 站内信 → 全员广播
        db.delete("sessions", "user_id=?", (uid,))
        msg = "您的账号已被管理员停用，如有疑问请联系管理员"
        if reason:
            msg += "（原因：%s）" % reason
        realtime.send_and_close(uid, {"t": "banned", "d": {"msg": msg}})
        db.notify(uid, "account", "账号已被停用",
                  "操作人：%s%s" % (ctx.user["display_name"],
                                    "，原因：%s" % reason if reason else ""), "user", uid)
        notice = "「%s」已被管理员停用（封禁）%s" % (row["display_name"],
                                                 "，原因：%s" % reason if reason else "")
    else:
        notice = "「%s」已恢复使用" % row["display_name"]
    realtime.broadcast({"t": "presence", "d": {"user_id": uid, "status": "offline"}})
    realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": st,
                                                     "name": row["display_name"]}})
    realtime.broadcast({"t": "sys_global", "d": {"content": notice}})
    base.audit(ctx, "账号" + ("停用(封禁)" if st == "disabled" else "启用"), row["username"],
               "IP:%s" % ctx.ip)
    return {"status": st, "msg": "已" + ("停用并强制下线" if st == "disabled" else "启用")}


@route("/api/admin/users/<id>/blacklist", "POST", perm="user.manage")
def blacklist_user(ctx):
    """加入/移出黑名单：永久性处罚，比普通停用更强，需单独移出。"""
    uid = int(ctx.args["id"])
    row = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise ApiError("用户不存在", 404)
    if uid == ctx.user["id"]:
        raise ApiError("不能将自己加入黑名单")
    if row["role"] == "superadmin" and ctx.user["role"] != "superadmin":
        raise ApiError("无权将超级管理员加入黑名单")
    action = (ctx.get("action") or "add").strip()
    reason = (ctx.get("reason") or "").strip()[:50]
    if action == "remove":
        if row["status"] != "blacklist":
            raise ApiError("该用户不在黑名单中")
        db.update("users", {"status": "active"}, "id=?", (uid,))
        realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": "active",
                                                         "name": row["display_name"]}})
        realtime.broadcast({"t": "sys_global", "d": {"content": "「%s」已被管理员移出黑名单" % row["display_name"]}})
        db.notify(uid, "account", "已移出黑名单", "操作人：%s，现可正常登录使用" % ctx.user["display_name"],
                  "user", uid)
        base.audit(ctx, "移出黑名单", row["username"], "IP:%s" % ctx.ip)
        return {"status": "active", "msg": "已移出黑名单"}
    # 默认加入黑名单
    if row["status"] == "blacklist":
        return {"status": "blacklist", "msg": "该用户已在黑名单中"}
    db.update("users", {"status": "blacklist"}, "id=?", (uid,))
    # 永久封禁：清会话 → 强制踢下线 → 站内信 → 全员广播
    db.delete("sessions", "user_id=?", (uid,))
    msg = "您的账号已被管理员列入黑名单，禁止登录与使用"
    if reason:
        msg += "（原因：%s）" % reason
    realtime.send_and_close(uid, {"t": "banned", "d": {"msg": msg}})
    db.notify(uid, "account", "账号已被列入黑名单",
              "操作人：%s%s" % (ctx.user["display_name"],
                                "，原因：%s" % reason if reason else ""), "user", uid)
    # 黑名单用户自动从所有房间成员关系中移除（防止残留在房间列表）
    db.delete("room_members", "user_id=?", (uid,))
    notice = "「%s」已被管理员列入黑名单%s" % (row["display_name"],
                                          "，原因：%s" % reason if reason else "")
    realtime.broadcast({"t": "presence", "d": {"user_id": uid, "status": "offline"}})
    realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": "blacklist",
                                                     "name": row["display_name"]}})
    realtime.broadcast({"t": "sys_global", "d": {"content": notice}})
    base.audit(ctx, "加入黑名单", row["username"],
               ("原因:%s " % reason if reason else "") + "IP:%s" % ctx.ip)
    return {"status": "blacklist", "msg": "已加入黑名单并强制下线"}


@route("/api/admin/users/export", "GET", perm="user.manage")
def export_users(ctx):
    rows = db.query("SELECT username, display_name, role, dept, position, email, phone, status, "
                    "created_at, last_login FROM users ORDER BY id")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["用户名", "姓名", "角色", "部门", "岗位", "邮箱", "手机", "状态", "注册时间", "最后登录"])
    for r in rows:
        w.writerow([r["username"], r["display_name"], security.role_name(r["role"]), r["dept"],
                    r["position"], r["email"], r["phone"], r["status"],
                    util.ts_to_str(r["created_at"]), util.ts_to_str(r["last_login"])])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return (200, data, "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="users.csv"'})


@route("/api/admin/users/import", "POST", perm="user.manage")
def import_users(ctx):
    """CSV/文本导入：用户名,姓名,角色,部门,岗位,邮箱,手机,密码"""
    raw = ctx.get("data", "")
    if ctx.files:
        with open(ctx.files[0].tmp_path, "rb") as f:
            raw = f.read().decode("utf-8-sig", "ignore")
        ctx.files[0].remove()
    if not raw.strip():
        raise ApiError("没有可导入的数据")
    ok_n, fail_n, msgs = 0, 0, []
    for i, line in enumerate(raw.splitlines()):
        line = line.strip()
        if not line or i == 0 and line.startswith("用户名"):
            continue
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        if len(parts) < 2:
            fail_n += 1
            continue
        username = parts[0].lower()
        if not username or db.one("SELECT 1 FROM users WHERE username=?", (username,)):
            fail_n += 1
            msgs.append("第%d行：用户名重复或为空" % (i + 1))
            continue
        pwd = parts[7] if len(parts) > 7 and parts[7] else security.gen_initial_password()
        _em = parts[5] if len(parts) > 5 else ""
        if _email_taken(_em):
            fail_n += 1
            msgs.append("第%d行：邮箱 %s 已被其他账号使用" % (i + 1, _em))
            continue
        try:
            uid = db.insert("users", {"username": username, "display_name": parts[1] or username,
                                      "password_hash": security.hash_password(pwd),
                                      "role": parts[2] if len(parts) > 2 and parts[2] in security.ROLES else "student",
                                      "dept": parts[3] if len(parts) > 3 else "",
                                      "position": parts[4] if len(parts) > 4 else "",
                                      "email": parts[5] if len(parts) > 5 else "",
                                      "phone": parts[6] if len(parts) > 6 else "",
                                      "status": "active", "must_change_pwd": 1,
                                      "created_at": util.now_ts(), "created_by": ctx.user["id"]})
            for r in db.query("SELECT id FROM rooms WHERE type IN ('public','announce') AND archived=0"):
                db.execute("INSERT OR IGNORE INTO room_members (room_id,user_id,role,joined_at) VALUES (?,?,'member',?)",
                           (r["id"], uid, util.now_ts()))
            ok_n += 1
        except Exception as e:
            fail_n += 1
            msgs.append("第%d行：%s" % (i + 1, e))
    base.audit(ctx, "导入用户", "%d 成功 / %d 失败" % (ok_n, fail_n))
    return {"ok": ok_n, "fail": fail_n, "msgs": msgs[:20]}


# ---------------- 姓名/头衔修改申请审核 ----------------
PROFILE_REQ_STATUS = {"pending": "待审核", "approved": "已通过", "rejected": "已驳回"}


def _profile_req_item(r):
    from api import auth as auth_api
    d = auth_api._request_view(r)
    u = db.one("SELECT id, username, display_name, dept, role FROM users WHERE id=?", (r["user_id"],))
    d["username"] = (u or {}).get("username", "")
    d["user_name"] = (u or {}).get("display_name", "已注销用户")
    d["dept"] = (u or {}).get("dept", "")
    d["role"] = (u or {}).get("role", "")
    d["role_name"] = security.role_name((u or {}).get("role", ""))
    d["status_name"] = PROFILE_REQ_STATUS.get(r["status"], r["status"])
    d["changed"] = auth_api.profile_request_fields(r)
    d["name_changed"] = (r["new_display_name"] or "") != (r["old_display_name"] or "")
    d["title_changed"] = (r["new_title"] or "") != (r["old_title"] or "")
    return d


@route("/api/admin/profile-requests", "GET", perm="user.manage")
def profile_requests(ctx):
    st = ctx.get("status", "pending")
    if st not in PROFILE_REQ_STATUS:
        st = "pending"
    rows = db.query("SELECT * FROM profile_requests WHERE status=? ORDER BY id DESC LIMIT 200", (st,))
    return {"list": [_profile_req_item(r) for r in rows],
            "pending": db.count("profile_requests", "status='pending'"),
            "approval_on": bool(app_ctx.APP.cfg.get("profile_change_need_approval", True))}


@route("/api/admin/profile-requests/<id>/review", "POST", perm="user.manage")
def profile_request_review(ctx):
    """通过则把申请值写入用户资料，驳回则仅记录结论；两者都会站内通知申请人"""
    rid = int(ctx.args["id"])
    req = db.one("SELECT * FROM profile_requests WHERE id=?", (rid,))
    if not req:
        raise ApiError("申请不存在", 404)
    action = (ctx.get("action") or "").strip()
    if action not in ("approve", "reject"):
        raise ApiError("操作需为 approve 或 reject")
    if req["status"] != "pending":
        raise ApiError("该申请已被处理（%s），不能重复审核" %
                       PROFILE_REQ_STATUS.get(req["status"], req["status"]))
    note = str(ctx.get("note") or "").strip()[:200]
    u = db.one("SELECT * FROM users WHERE id=?", (req["user_id"],))
    if not u:
        raise ApiError("申请对应的用户已不存在，无法审核")

    approve = action == "approve"
    new_name = str(req["new_display_name"] or "").strip()[:40]
    new_title = str(req["new_title"] or "")[:20]
    if approve and not new_name:
        # 先校验再落库，避免出现“申请已通过但姓名未生效”的中间状态
        raise ApiError("申请的姓名为空，无法通过，请驳回该申请")

    data = {"status": "approved" if approve else "rejected",
            "review_note": note, "reviewed_by": ctx.user["id"], "reviewed_at": util.now_ts()}
    db.update("profile_requests", data, "id=?", (rid,))

    applied = {}
    if approve:
        fields = {"display_name": new_name, "title": new_title}
        db.update("users", fields, "id=?", (u["id"],))
        applied = fields

    detail = "姓名：%s → %s；头衔：%s → %s" % (
        req["old_display_name"] or "", req["new_display_name"] or "",
        req["old_title"] or "（无）", req["new_title"] or "（无）")
    base.audit(ctx, "资料申请" + ("通过" if approve else "驳回"),
               "profile_request:%d user:%s" % (rid, u["username"]),
               detail + (("；批注：" + note) if note else ""))

    db.notify(u["id"], "profile_result",
              "姓名/头衔修改申请已" + ("通过" if approve else "驳回"),
              (("您申请的%s已生效。" % detail) if approve
               else ("您的申请未通过。%s" % ("批注：" + note if note else "如有疑问请联系管理员。"))),
              "profile_request", rid)
    realtime.send_to_user(u["id"], {"t": "notify", "d": {"type": "profile_result"}})
    realtime.broadcast({"t": "user_update", "d": {"user_id": u["id"]}})
    return {"msg": "已" + ("通过，资料已更新" if approve else "驳回"),
            "status": data["status"], "applied": applied,
            "pending": db.count("profile_requests", "status='pending'")}


@route("/api/admin/profile-requests/pending", "GET", perm="user.manage")
def profile_requests_pending(ctx):
    return {"pending": db.count("profile_requests", "status='pending'")}


# ---------------- 部门 ----------------
@route("/api/admin/depts", "GET", perm="user.manage")
def depts(ctx):
    rows = db.query("SELECT d.name, COUNT(u.id) AS user_count FROM depts d "
                    "LEFT JOIN users u ON u.dept=d.name GROUP BY d.name ORDER BY d.sort, d.id")
    return {"list": rows}


@route("/api/admin/depts", "POST", perm="dept.manage")
def dept_create(ctx):
    db.execute("INSERT OR IGNORE INTO depts (name, parent, sort) VALUES (?,?,?)",
               (ctx.require("name"), ctx.get("parent", ""), ctx.int("sort", 0)))
    return {"msg": "已添加"}


@route("/api/admin/depts/<name>", "DELETE", perm="dept.manage")
def dept_delete(ctx):
    db.delete("depts", "name=?", (ctx.args["name"],))
    return {"msg": "已删除"}


# ---------------- 房间管理 ----------------
@route("/api/admin/rooms", "GET", perm="room.manage")
def admin_rooms(ctx):
    rows = db.query("SELECT * FROM rooms ORDER BY archived, id DESC")
    out = []
    for r in rows:
        v = dict(r)
        v["member_count"] = db.scalar("SELECT COUNT(*) AS c FROM room_members WHERE room_id=? AND silent=0", (r["id"],))
        v["msg_count"] = db.scalar("SELECT COUNT(*) AS c FROM messages WHERE room_id=?", (r["id"],))
        v["created_str"] = util.ts_to_str(r["created_at"])
        owner = db.one("SELECT display_name FROM users WHERE id=?", (r["owner_id"],))
        v["owner_name"] = (owner or {}).get("display_name", "—")
        out.append(v)
    return {"list": out}


@route("/api/admin/rooms/<id>", "PUT", perm="room.manage")
def admin_room_update(ctx):
    rid = int(ctx.args["id"])
    data = {k: ctx.get(k) for k in ("name", "topic", "type", "archived", "allow_file",
                                    "slow_mode", "max_members") if ctx.get(k) is not None}
    if data:
        db.update("rooms", data, "id=?", (rid,))
    return {"msg": "已保存"}


@route("/api/admin/rooms/<id>", "DELETE", perm="room.manage")
def admin_room_delete(ctx):
    rid = int(ctx.args["id"])
    db.delete("messages", "room_id=?", (rid,))
    db.delete("room_members", "room_id=?", (rid,))
    db.delete("rooms", "id=?", (rid,))
    base.audit(ctx, "删除房间", str(rid))
    return {"msg": "已删除"}


# ---------------- 公告 ----------------
@route("/api/announcements", "GET")
def announcements(ctx):
    rows = db.query("SELECT a.*, u.display_name AS author FROM announcements a "
                    "LEFT JOIN users u ON u.id=a.created_by "
                    "WHERE (a.expires_at=0 OR a.expires_at>?) ORDER BY a.pinned DESC, a.id DESC LIMIT 30",
                    (util.now_ts(),))
    for r in rows:
        r["created_str"] = util.ts_to_str(r["created_at"])
    return {"list": rows}


@route("/api/admin/announcements", "GET", perm="announce.manage")
def admin_announcements(ctx):
    rows = db.query("SELECT a.*, u.display_name AS author FROM announcements a "
                    "LEFT JOIN users u ON u.id=a.created_by ORDER BY a.id DESC")
    for r in rows:
        r["created_str"] = util.ts_to_str(r["created_at"])
    return {"list": rows}


@route("/api/admin/announcements", "POST", perm="announce.manage")
def announcement_create(ctx):
    aid = db.insert("announcements", {"title": ctx.require("title"), "content": ctx.get("content", ""),
                                      "level": ctx.get("level", "info"), "pinned": ctx.int("pinned", 0),
                                      "popup": ctx.int("popup", 0), "force_seconds": ctx.int("force_seconds", 0),
                                      "scope": ctx.get("scope", "all"), "created_by": ctx.user["id"],
                                      "created_at": util.now_ts(), "expires_at": ctx.int("expires_at", 0)})
    db.notify_all(ctx.get("title"), (ctx.get("content") or "")[:100], "announce", "announce", aid)
    realtime.broadcast({"t": "notify", "d": {"type": "announce", "title": ctx.get("title")}})
    base.audit(ctx, "发布公告", ctx.get("title"))
    return {"id": aid}


@route("/api/admin/announcements/<id>", "PUT", perm="announce.manage")
def announcement_update(ctx):
    data = {k: ctx.get(k) for k in ("title", "content", "level", "pinned", "expires_at",
                                    "popup", "force_seconds")
            if ctx.get(k) is not None}
    if data:
        db.update("announcements", data, "id=?", (int(ctx.args["id"]),))
    return {"msg": "已保存"}


@route("/api/admin/announcements/<id>", "DELETE", perm="announce.manage")
def announcement_delete(ctx):
    db.delete("announcements", "id=?", (int(ctx.args["id"]),))
    return {"msg": "已删除"}


# ---------------- 日志 / 统计 ----------------
@route("/api/admin/logs", "GET", perm="log.view")
def logs(ctx):
    where, args = ["1=1"], []
    if ctx.get("action"):
        where.append("action LIKE ?")
        args.append("%%%s%%" % ctx.get("action"))
    if ctx.get("actor"):
        where.append("actor LIKE ?")
        args.append("%%%s%%" % ctx.get("actor"))
    if ctx.get("q"):
        where.append("(detail LIKE ? OR target LIKE ?)")
        args += ["%%%s%%" % ctx.get("q")] * 2
    if ctx.get("date"):
        s = ctx.get("date") + " 00:00:00"
        st = util.str_to_ts(s, "%Y-%m-%d %H:%M:%S")
        where.append("ts>=? AND ts<?")
        args += [st, st + 86400]
    p, size, off = base.page_args(ctx, 30, 500)
    total = db.scalar("SELECT COUNT(*) AS c FROM audit_logs WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT * FROM audit_logs WHERE %s ORDER BY id DESC LIMIT ? OFFSET ?"
                    % " AND ".join(where), args + [size, off])
    for r in rows:
        r["time"] = util.ts_to_str(r["ts"])
    return {"total": total, "list": rows}


@route("/api/admin/messages", "GET", perm="message.moderate")
def admin_messages(ctx):
    where, args = ["1=1"], []
    if ctx.get("q"):
        where.append("m.content LIKE ?")
        args.append("%%%s%%" % ctx.get("q"))
    if ctx.int("room_id", 0):
        where.append("m.room_id=?")
        args.append(ctx.int("room_id"))
    if ctx.int("user_id", 0):
        where.append("m.user_id=?")
        args.append(ctx.int("user_id"))
    if ctx.int("flagged", 0):
        where.append("m.flagged=1")
    if ctx.get("ip"):
        where.append("m.ip LIKE ?")
        args.append("%%%s%%" % ctx.get("ip"))
    p, size, off = base.page_args(ctx, 30)
    total = db.scalar("SELECT COUNT(*) AS c FROM messages m WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT m.*, u.display_name, u.title, u.username AS sender_username, "
                    "u.dept AS sender_dept, r.name AS room_name FROM messages m "
                    "LEFT JOIN users u ON u.id=m.user_id LEFT JOIN rooms r ON r.id=m.room_id "
                    "WHERE %s ORDER BY m.id DESC LIMIT ? OFFSET ?" % " AND ".join(where), args + [size, off])
    for r in rows:
        r["created_str"] = util.ts_to_str(r["created_at"])
        r["ip"] = r.get("ip") or ""
        r["title"] = r.get("title") or ""
    return {"total": total, "list": rows}


@route("/api/admin/stats/export", "GET", perm="stats.view")
def stats_export(ctx):
    rows = db.query("SELECT u.username, u.display_name, u.dept, u.role, u.learn_minutes, u.points,"
                    " (SELECT COUNT(*) FROM messages m WHERE m.user_id=u.id) AS msg_count,"
                    " (SELECT COUNT(*) FROM exam_records r WHERE r.user_id=u.id AND r.status<>'doing') AS exam_count,"
                    " (SELECT COUNT(*) FROM checkins c WHERE c.user_id=u.id) AS checkin_days,"
                    " (SELECT COUNT(*) FROM submissions s WHERE s.user_id=u.id) AS hw_count,"
                    " (SELECT COUNT(*) FROM enrollments e WHERE e.user_id=u.id AND e.completed_at>0) AS course_done"
                    " FROM users u ORDER BY u.id")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["用户名", "姓名", "部门", "角色", "学习时长(分钟)", "积分", "发言数",
                "考试次数", "打卡天数", "作业数", "完成课程数"])
    for r in rows:
        w.writerow([r["username"], r["display_name"], r["dept"], security.role_name(r["role"]),
                    r["learn_minutes"], r["points"], r["msg_count"], r["exam_count"],
                    r["checkin_days"], r["hw_count"], r["course_done"]])
    return (200, ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="learning-stats.csv"'})


# ---------------- 敏感词 ----------------
@route("/api/admin/sensitive", "GET", perm="settings.manage")
def sensitive_list(ctx):
    return {"list": db.query("SELECT * FROM sensitive_words ORDER BY id DESC")}


@route("/api/admin/sensitive", "POST", perm="settings.manage")
def sensitive_add(ctx):
    w = (ctx.require("word") or "").strip()
    db.execute("INSERT OR IGNORE INTO sensitive_words (word, level) VALUES (?,?)", (w, ctx.get("level", "replace")))
    app_ctx.APP._load_sensitive()
    return {"msg": "已添加"}


@route("/api/admin/sensitive/<id>", "DELETE", perm="settings.manage")
def sensitive_del(ctx):
    db.delete("sensitive_words", "id=?", (int(ctx.args["id"]),))
    app_ctx.APP._load_sensitive()
    return {"msg": "已删除"}


# ---------------- 设置 ----------------
@route("/api/admin/settings", "GET", perm="admin.access")
def settings_get(ctx):
    return {"settings": app_ctx.APP.cfg.as_dict(public=True), "roles": security.ROLES,
            "perms": security.ALL_PERMS, "role_perms": security.ROLE_PERMS}


@route("/api/admin/settings", "PUT", perm="settings.manage")
def settings_update(ctx):
    data = ctx.body.get("settings") or ctx.body
    allowed = ("site_name", "site_slogan", "welcome", "icp_text", "allow_register",
               "register_need_approval", "default_role", "profile_change_need_approval",
               "password_min_len", "password_need_complex", "theme_color",
               "max_failed_login", "lock_minutes", "session_hours", "max_upload_mb", "allow_ext",
               "max_msg_len", "history_page_size", "allow_recall_minutes", "enable_sensitive_filter",
               "sensitive_replace", "enable_exam", "enable_checkin", "maintenance_mode",
               "maintenance_note", "auto_open_browser", "log_retention_days", "trust_real_ip",
               # HTTPS（证书文件路径；由部署者自行准备证书后在此填写）
               "https_enabled", "https_port", "cert_file", "key_file",
               # IP 访问白名单（逗号分隔：单IP / CIDR / 前缀通配；留空=不启用）
               "ip_whitelist",
               # 小游戏（管理员手动开启 + 是否允许显示界面）
               "enable_games", "game_fireboy", "game_chess", "game_show_nav",
               # 远程协助（屏幕共享/远程控制）
               "enable_remote_assist", "remote_fps", "remote_quality", "remote_max_minutes")
    n = 0
    for k, v in data.items():
        if k in allowed:
            app_ctx.APP.cfg.set(k, v)
            n += 1
    app_ctx.APP.cfg.save()
    app_ctx.APP.sensitive.reload([r["word"] for r in db.query("SELECT word FROM sensitive_words")],
                         app_ctx.APP.cfg.get("sensitive_replace", "***"))
    # 站点配色变更：实时推送给在线客户端，无需刷新
    if "theme_color" in data:
        realtime.broadcast({"t": "notice",
                            "d": {"type": "theme", "value": app_ctx.APP.cfg.get("theme_color") or "blue"}})
    base.audit(ctx, "修改系统设置", "%d 项" % n)
    return {"msg": "设置已保存（%d 项）" % n, "settings": app_ctx.APP.cfg.as_dict(public=True)}


@route("/api/admin/settings/port", "POST", perm="settings.manage")
def settings_port(ctx):
    app_ctx.APP.cfg.set("port", int(ctx.require("port")))
    app_ctx.APP.cfg.save()
    return {"msg": "端口已更新，重启服务后生效"}


# ---------------- 备份 ----------------
@route("/api/admin/backups", "GET", perm="backup.manage")
def backups(ctx):
    d = config.CFG.backup_dir
    os.makedirs(d, exist_ok=True)
    out = []
    for name in sorted(os.listdir(d), reverse=True):
        p = os.path.join(d, name)
        if os.path.isfile(p) and (name.endswith(".sql") or name.endswith(".db")):
            out.append({"name": name, "size": os.path.getsize(p),
                        "size_str": util.human_size(os.path.getsize(p)),
                        "time": util.ts_to_str(int(os.path.getmtime(p)))})
    return {"list": out[:50], "dir": d}


@route("/api/admin/backup", "POST", perm="backup.manage")
def backup(ctx):
    d = config.CFG.backup_dir
    os.makedirs(d, exist_ok=True)
    name = "backup_%s.sql" % util.now_str("%Y%m%d_%H%M%S")
    p = os.path.join(d, name)
    db.backup_to(p)
    base.audit(ctx, "数据备份", name)
    return {"name": name, "size": os.path.getsize(p), "msg": "备份完成：%s" % name}


@route("/api/admin/backup/download", "GET", perm="backup.manage")
def backup_download(ctx):
    name = util.safe_name(ctx.require("name"))
    p = os.path.join(config.CFG.backup_dir, name)
    if not os.path.exists(p):
        raise ApiError("备份不存在", 404)
    with open(p, "rb") as f:
        data = f.read()
    return (200, data, "application/octet-stream",
            {"Content-Disposition": 'attachment; filename="%s"' % name})


@route("/api/admin/backup/restore", "POST", perm="backup.manage")
def backup_restore(ctx):
    if ctx.user["role"] != "superadmin":
        raise ApiError("仅超级管理员可恢复数据", 403)
    name = util.safe_name(ctx.require("name"))
    p = os.path.join(config.CFG.backup_dir, name)
    if not os.path.exists(p):
        raise ApiError("备份不存在", 404)
    cur = os.path.join(config.CFG.backup_dir, "before_restore_%s.sql" % util.now_str("%Y%m%d_%H%M%S"))
    db.backup_to(cur)
    db.restore_from(p)
    base.audit(ctx, "数据恢复", name)
    return {"msg": "已恢复（当前数据已自动备份为 %s）" % os.path.basename(cur)}


@route("/api/admin/maintenance", "POST", perm="settings.manage")
def maintenance(ctx):
    on = ctx.int("enable", 0)
    app_ctx.APP.cfg.set("maintenance_mode", bool(on))
    app_ctx.APP.cfg.set("maintenance_note", ctx.get("note", "系统维护中，请稍后访问。"))
    app_ctx.APP.cfg.save()
    realtime.broadcast({"t": "notice", "d": {"type": "maintenance", "on": bool(on),
                                             "note": app_ctx.APP.cfg.get("maintenance_note")}})
    return {"msg": "维护模式已" + ("开启" if on else "关闭")}


# ---------------- 通知 ----------------
@route("/api/notifications", "GET")
def notifications(ctx):
    rows = db.query("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",
                    (ctx.user["id"],))
    for r in rows:
        r["created_str"] = util.ts_to_str(r["created_at"])
    return {"list": rows,
            "unread": db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND is_read=0",
                                (ctx.user["id"],))}


@route("/api/notifications/read", "POST")
def notif_read(ctx):
    nid = ctx.int("id", 0)
    if nid:
        db.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, ctx.user["id"]))
    else:
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (ctx.user["id"],))
    return {"msg": "已读"}


@route("/api/users", "GET")
def user_list(ctx):
    """通讯录：全员简要列表（用于 @ / 私聊 / 加人）"""
    rows = db.query("SELECT id, username, display_name, dept, role, position, title, avatar FROM users "
                    "WHERE status='active' ORDER BY dept, id")
    return {"list": [dict(r, color=util.color_of(r["username"]), online=realtime.is_online(r["id"]),
                          avatar=_avatar_url(r.get("avatar", "")))
                     for r in rows]}


# ---------------- IP 黑名单 ----------------
def _online_ip_stat():
    """统计当前在线连接的来源 IP（辅助管理员决定封谁）"""
    stat = {}
    with realtime._conns_lock:
        conns = list(realtime._conns.values())
    for c in conns:
        if c and c.alive and getattr(c, "ip", None):
            name = (c.user or {}).get("display_name", "未登录")
            slot = stat.setdefault(c.ip, {"n": 0, "users": []})
            slot["n"] += 1
            if name not in slot["users"]:
                slot["users"].append(name)
    for k in stat:
        stat[k]["users"] = "、".join(stat[k]["users"][:4])
    return stat


@route("/api/admin/ipblacklist", "GET", perm="user.manage")
def ipbl_list(ctx):
    ipsec.reload_rules()
    rows = db.query("SELECT * FROM ip_blacklist ORDER BY id DESC")
    ts = util.now_ts()
    for r in rows:
        r["created_str"] = util.ts_to_str(r["created_at"])
        r["expires_str"] = util.ts_to_str(r["expires_at"]) if r["expires_at"] else "永久"
        r["expired"] = bool(r["expires_at"] and r["expires_at"] < ts)
    return {"list": rows,
            "enabled": str(db.get_setting("ip_blacklist_enabled", "1")) != "0",
            "online_ip": [{"ip": k, "n": v["n"], "users": v["users"]}
                          for k, v in _online_ip_stat().items()]}


@route("/api/admin/ipblacklist", "POST", perm="user.manage")
def ipbl_add(ctx):
    pattern = str(ctx.get("pattern", "")).strip()
    reason = str(ctx.get("reason", "")).strip()[:50]
    hours = ctx.int("hours", 0)
    if hours < 0 or hours > 24 * 365:
        raise ApiError("封禁时长需在 0-8760 小时之间")
    okk, msg = ipsec.add(pattern, reason, hours,
                         ctx.user["id"], ctx.user["display_name"], ctx.ip)
    if not okk:
        raise ApiError(msg)
    # 当前在线连接命中新规则的，立即踢下线
    kicked = 0
    try:
        hit_ip = [ip for ip in _online_ip_stat() if ipsec.blocked(ip)]
        for ip in hit_ip:
            kicked += realtime.close_by_ip(
                ip, {"t": "banned", "d": {"msg": "该 IP 已被列入内网黑名单，连接已断开"}})
    except Exception:
        pass
    db.notify_all("IP 黑名单更新",
                  "管理员已将 %s 加入黑名单，该来源无法再访问本系统" % pattern,
                  "system", "ip_blacklist")
    msg += ("，并踢下 %d 条在线连接" % kicked) if kicked else ""
    return {"msg": msg}


@route("/api/admin/ipblacklist/<id>/remove", "POST", perm="user.manage")
def ipbl_remove(ctx):
    okk, msg = ipsec.remove(ctx.args["id"], ctx.user["id"], ctx.user["display_name"], ctx.ip)
    if not okk:
        raise ApiError(msg)
    db.notify_all("IP 黑名单更新", "管理员已移出 IP 黑名单规则，来源恢复访问",
                  "system", "ip_blacklist")
    return {"msg": msg}


@route("/api/admin/ipblacklist/toggle", "POST", perm="user.manage")
def ipbl_toggle(ctx):
    on = bool(ctx.int("on", 1))
    ipsec.set_enabled(on, ctx.user["id"], ctx.user["display_name"], ctx.ip)
    return {"msg": "IP 黑名单已" + ("启用" if on else "停用")}
