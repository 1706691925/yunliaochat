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
"""认证、注册、个人资料、密码"""
import json
import random

from core import db, security, util
from core import app_ctx
from core.http import ApiError, route
from api import base


def email_taken(email, exclude_id=None):
    """邮箱是否已被别的账号占用。

    登录支持「账号或邮箱」，因此邮箱必须唯一，否则无法确定登录的是谁。
    """
    email = (email or "").strip()
    if not email:
        return False
    if exclude_id:
        return bool(db.one("SELECT 1 FROM users WHERE LOWER(email)=? AND id<>?",
                           (email.lower(), exclude_id)))
    return bool(db.one("SELECT 1 FROM users WHERE LOWER(email)=?", (email.lower(),)))


@route("/api/auth/login", "POST", perm="*")
def login(ctx):
    username = (ctx.require("username") or "").strip()
    password = ctx.require("password")
    row = db.one("SELECT * FROM users WHERE username=?", (username,))
    if not row and "@" in username:
        # 登录框允许填「账号或邮箱」。邮箱按大小写不敏感匹配
        # （邮箱本身不区分大小写，用户可能随手输入大写）。
        row = db.one("SELECT * FROM users WHERE LOWER(email)=?", (username.lower(),))
    if not row:
        raise ApiError("用户名或密码错误", 401)
    locked, remain = security.login_lock_info(row, app_ctx.APP.cfg)
    if locked:
        raise ApiError("账号已被锁定，请 %d 分钟后重试" % (remain // 60 + 1), 403)
    if not security.verify_password(password, row["password_hash"]):
        failed = int(row["failed_attempts"] or 0) + 1
        data = {"failed_attempts": failed}
        if security.should_lock(failed, app_ctx.APP.cfg):
            data["locked_until"] = util.now_ts() + int(app_ctx.APP.cfg.get("lock_minutes", 15)) * 60
            db.update("users", data, "id=?", (row["id"],))
            db.log_action(row["id"], row["display_name"], "登录失败锁定", "auth", "", ctx.ip)
            raise ApiError("连续登录失败，账号已锁定 %s 分钟" % app_ctx.APP.cfg.get("lock_minutes", 15), 403)
        db.update("users", data, "id=?", (row["id"],))
        left = int(app_ctx.APP.cfg.get("max_failed_login", 5)) - failed
        raise ApiError("用户名或密码错误，还可尝试 %d 次" % max(left, 0), 401)
    if row["status"] == "blacklist":
        raise ApiError("账号已被列入黑名单，请联系管理员", 403)
    if row["status"] == "disabled":
        raise ApiError("账号已被停用，请联系管理员", 403)
    if row["status"] == "pending":
        raise ApiError("账号待审核，请联系管理员", 403)
    if app_ctx.APP.cfg.get("maintenance_mode") and row["role"] != "superadmin":
        raise ApiError(app_ctx.APP.cfg.get("maintenance_note", "系统维护中"), 503)

    ua = str(ctx.headers.get("User-Agent", ""))[:200]
    token = app_ctx.APP.create_session(row["id"], ctx.ip, ua)
    # 单账号单 IP：同 IP 多端可并存，其它 IP 的旧会话与连接会被顶掉
    app_ctx.APP.kick_other_sessions(row["id"], token, ctx.ip)
    db.update("users", {"failed_attempts": 0, "locked_until": 0,
                        "last_login": util.now_ts(), "last_seen": util.now_ts()},
              "id=?", (row["id"],))
    db.log_action(row["id"], row["display_name"], "登录", "auth",
                  "IP:%s 客户端:%s" % (ctx.ip, util.client_of(ua)), ctx.ip)
    return {"token": token, "user": app_ctx.APP.public_user(dict(row), full=True)}


@route("/api/auth/logout", "POST")
def logout(ctx):
    """退出登录：同时失效该账号的全部会话（浏览器退出后，桌面助手也会立即退出）"""
    token = ctx.token
    if token:
        app_ctx.APP.logout_all(ctx.user["id"])
    return {"ok": True}


@route("/api/auth/register", "POST", perm="*")
def register(ctx):
    if not app_ctx.APP.cfg.get("allow_register", True):
        raise ApiError("系统已关闭自助注册，请联系管理员开通账号", 403)
    username = (ctx.require("username") or "").strip().lower()
    password = ctx.require("password")
    name = (ctx.get("display_name") or username).strip()[:40]
    # 头衔属于需审核字段，注册时不接受客户端自带（如需赋予，由管理员在后台设置）
    if len(username) < 3 or not username.replace("_", "").replace(".", "").isalnum():
        raise ApiError("用户名需 3 位以上字母/数字/下划线")
    if db.one("SELECT 1 FROM users WHERE username=?", (username,)):
        raise ApiError("用户名已存在")
    if email_taken(ctx.get("email")):
        raise ApiError("该邮箱已被其他账号使用")
    err = security.password_error(password, int(app_ctx.APP.cfg.get("password_min_len", 8)),
                                  bool(app_ctx.APP.cfg.get("password_need_complex", True)))
    if err:
        raise ApiError(err)
    if security.weak_password_check(password):
        raise ApiError("密码过于简单，请更换")
    status = "pending" if app_ctx.APP.cfg.get("register_need_approval") else "active"
    uid = db.insert("users", {
        "username": username, "display_name": name,
        "password_hash": security.hash_password(password),
        "role": app_ctx.APP.cfg.get("default_role", "student"),
        "dept": ctx.get("dept", ""), "position": ctx.get("position", ""),
        "title": "",
        "email": ctx.get("email", ""), "phone": ctx.get("phone", ""),
        "status": status, "created_at": util.now_ts(), "must_change_pwd": 0,
    })
    # 自动加入公开房间与公告频道
    for r in db.query("SELECT id FROM rooms WHERE type IN ('public','announce') AND archived=0"):
        db.execute("INSERT OR IGNORE INTO room_members (room_id,user_id,role,joined_at) VALUES (?,?,'member',?)",
                   (r["id"], uid, util.now_ts()))
    db.log_action(uid, name, "注册", "auth", "IP:%s 客户端:%s"
                  % (ctx.ip, util.client_of(str(ctx.headers.get("User-Agent", ""))[:200])), ctx.ip)
    if status == "pending":
        return {"pending": True, "msg": "注册成功，等待管理员审核"}
    return {"pending": False, "token": app_ctx.APP.create_session(uid, ctx.ip), "msg": "注册成功"}


@route("/api/site", "GET", perm="*")
def site_info(ctx):
    """公开站点信息：登录页在未登录时也要读取站点名与主题配色"""
    cfg = app_ctx.APP.cfg
    return {"name": cfg.get("site_name") or "云聊",
            "slogan": cfg.get("site_slogan") or "",
            "welcome": cfg.get("welcome") or "",
            "icp": cfg.get("icp_text") or "",
            "theme_color": cfg.get("theme_color") or "blue",
            "allow_register": bool(cfg.get("allow_register")),
            "register_need_approval": bool(cfg.get("register_need_approval")),
            "maintenance": bool(cfg.get("maintenance_mode"))}


@route("/api/auth/me", "GET")
def me(ctx):
    row = db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
    return {"user": app_ctx.APP.public_user(dict(row), full=True),
            "site": {"name": app_ctx.APP.cfg.get("site_name"), "slogan": app_ctx.APP.cfg.get("site_slogan"),
                     "welcome": app_ctx.APP.cfg.get("welcome"), "icp": app_ctx.APP.cfg.get("icp_text"),
                     "theme_color": app_ctx.APP.cfg.get("theme_color") or "blue",
                     "allow_register": app_ctx.APP.cfg.get("allow_register"),
                     "enable_exam": app_ctx.APP.cfg.get("enable_exam"),
                     "enable_checkin": app_ctx.APP.cfg.get("enable_checkin"),
                     "max_upload_mb": app_ctx.APP.cfg.get("max_upload_mb"),
                     "allow_ext": app_ctx.APP.cfg.get("allow_ext"),
                     "max_msg_len": app_ctx.APP.cfg.get("max_msg_len"),
                     "maintenance": app_ctx.APP.cfg.get("maintenance_mode")},
            "unread": app_ctx.APP.unread_summary(ctx.user["id"])}


@route("/api/auth/devices", "GET")
def my_devices(ctx):
    """我的登录客户端/设备：当前活跃会话 + 最近登录记录（客户端类型来自 User-Agent 解析）"""
    token = ctx.token or ""
    cur = db.one("SELECT token, ip, ua, created_at, last_seen FROM sessions WHERE token=?", (token,))
    current = None
    if cur:
        current = {"client": util.client_of(cur["ua"]), "ip": cur["ip"] or "",
                   "login_at": util.ts_to_str(cur["created_at"]),
                   "last_seen": util.ts_to_str(cur["last_seen"]) if cur["last_seen"] else ""}
    rows = db.query("SELECT ts, ip, detail FROM audit_logs WHERE actor_id=? AND action='登录' "
                    "ORDER BY id DESC LIMIT 20", (ctx.user["id"],))
    history = [{"ts": util.ts_to_str(r["ts"]), "ip": r["ip"] or "", "detail": r["detail"] or ""}
               for r in rows]
    return {"current": current, "history": history, "single_session": True}


@route("/api/auth/password", "POST")
def change_password(ctx):
    old = ctx.require("old_password")
    new = ctx.require("new_password")
    row = db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
    if not security.verify_password(old, row["password_hash"]):
        raise ApiError("原密码不正确")
    err = security.password_error(new, int(app_ctx.APP.cfg.get("password_min_len", 8)),
                                  bool(app_ctx.APP.cfg.get("password_need_complex", True)))
    if err:
        raise ApiError(err)
    if security.weak_password_check(new):
        raise ApiError("新密码过于简单")
    db.update("users", {"password_hash": security.hash_password(new), "must_change_pwd": 0},
              "id=?", (row["id"],))
    db.log_action(row["id"], row["display_name"], "修改密码", "auth", "", ctx.ip)
    return {"msg": "密码已更新"}


@route("/api/auth/profile", "PUT")
def update_profile(ctx):
    # 昵称（display_name）可由本人直接修改；头衔默认需管理员审核；其余字段直接保存
    data = {k: ctx.get(k) for k in EDITABLE_FIELDS if ctx.get(k) is not None}
    # 昵称：允许改字，不允许清空（直接读 body，避免空串被 ctx.get 当成未提供）
    if isinstance(ctx.body, dict) and "display_name" in ctx.body:
        dn = str(ctx.body.get("display_name") or "").strip()
        if not dn:
            raise ApiError("昵称不能为空")
        data["display_name"] = dn[:40]
    # 头像只接受 files.id 或空（清空头像），防止写入任意内容
    if "avatar" in data:
        av = str(data["avatar"] or "").strip()
        if av and not av.isdigit():
            raise ApiError("头像参数不合法")
        data["avatar"] = av
    # 邮箱参与登录，必须唯一（排除自己）
    if "email" in data:
        data["email"] = str(data["email"] or "").strip()
        if email_taken(data["email"], ctx.user["id"]):
            raise ApiError("该邮箱已被其他账号使用")

    want = collect_locked_fields(ctx)

    applied = {}
    requested = None
    if want:
        if need_profile_approval(ctx.user):
            requested = _create_profile_request(ctx, want, ctx.get("reason", ""))
        else:
            applied = want

    if data:
        applied.update(data)
    if not applied and not requested:
        raise ApiError("没有可更新的字段")
    if applied:
        db.update("users", applied, "id=?", (ctx.user["id"],))
        from core import realtime
        realtime.broadcast({"t": "user_update", "d": {"user_id": ctx.user["id"]}})
    row = db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
    msg = "资料已保存"
    if requested and not applied:
        msg = "已提交修改申请，等待管理员审核"
    elif requested:
        msg = "其他资料已保存；头衔已提交申请，等待管理员审核"
    return {"user": app_ctx.APP.public_user(dict(row), full=True),
            "msg": msg, "request": requested,
            "need_approval": bool(requested)}


# ---------------- 昵称 / 头衔 ----------------
# 需审核的字段：只有自定义头衔（昵称可由本人随时修改）
PROFILE_LOCKED_FIELDS = ("title",)
# 用户可直接保存、不需审核的字段（avatar 存 files.id；display_name 即昵称）
EDITABLE_FIELDS = ("display_name", "signature", "theme", "email", "phone", "dept", "position", "avatar")


def submitted_fields(ctx):
    """按 body 原始键判断用户到底提交了哪些锁定字段。

    Ctx.get 会把空字符串当作“未提供”，而“清空头衔”本身就是合法诉求，
    因此这里直接读 body，避免空值提交被静默丢弃。
    """
    out = {}
    body = ctx.body if isinstance(ctx.body, dict) else {}
    for k in PROFILE_LOCKED_FIELDS:
        if k in body and body[k] is not None:
            out[k] = body[k]
    return out


def need_profile_approval(user):
    """开关开启且当前账号没有用户管理权限时，头衔修改需管理员审核（昵称始终可自改）"""
    if not bool(app_ctx.APP.cfg.get("profile_change_need_approval", True)):
        return False
    return not security.has_perm(user, "user.manage")


def normalize_profile_fields(want):
    """统一校验/清洗昵称与头衔，返回可直接落库的字典"""
    out = {}
    if "title" in want:
        t = str(want["title"] or "").strip()
        if len(t) > 20:
            raise ApiError("头衔最多 20 个字符")
        out["title"] = t
    if "display_name" in want:
        dn = str(want["display_name"] or "").strip()
        if not dn:
            raise ApiError("昵称不能为空")
        out["display_name"] = dn[:40]
    return out


def collect_locked_fields(ctx):
    return normalize_profile_fields(submitted_fields(ctx))


def _request_view(r):
    d = dict(r)
    d["created_str"] = util.ts_to_str(r["created_at"])
    d["reviewed_str"] = util.ts_to_str(r["reviewed_at"]) if r["reviewed_at"] else ""
    d["reviewer_name"] = ""
    if r["reviewed_by"]:
        rv = db.one("SELECT display_name FROM users WHERE id=?", (r["reviewed_by"],))
        d["reviewer_name"] = (rv or {}).get("display_name", "")
    return d


def profile_reviewers():
    """候选审核人：超管 / 系统管理员 / 显式授予 user.manage 的账号"""
    rows = db.query("SELECT * FROM users WHERE status='active' "
                    "AND (role IN ('superadmin','admin') OR extra_perms LIKE '%user.manage%')")
    return [r for r in rows if security.has_perm(r, "user.manage")]


def _notify_profile_reviewers(user):
    """向具备用户管理权限的账号推送审核提醒（站内信 + 实时通知）"""
    from core import realtime
    for u in profile_reviewers():
        if u["id"] == user["id"]:
            continue
        db.notify(u["id"], "profile_review", "有新的头衔修改申请",
                  "用户「%s」申请修改资料，请前往「管理后台 · 资料申请」审核"
                  % (user["display_name"] or ""), "profile_request", 0)
        realtime.send_to_user(u["id"], {"t": "notify", "d": {"type": "profile_review"}})


def _create_profile_request(ctx, want, reason=""):
    """写入/更新待审核申请；want 为已通过校验的目标值"""
    uid = ctx.user["id"]
    row = db.one("SELECT * FROM users WHERE id=?", (uid,))
    cur_name = row["display_name"] or ""
    cur_title = row["title"] or ""
    new_name = str(want.get("display_name", cur_name) or "").strip()
    new_title = str(want.get("title", cur_title) or "")
    if new_name == cur_name and new_title == cur_title:
        raise ApiError("提交的头衔与当前一致，无需申请")
    reason = str(reason or "").strip()[:200]
    pend = db.one("SELECT * FROM profile_requests WHERE user_id=? AND status='pending'", (uid,))
    fields = {
        "old_display_name": cur_name, "old_title": cur_title,
        "new_display_name": new_name, "new_title": new_title,
        "reason": reason,
    }
    if pend:
        fields["created_at"] = util.now_ts()
        db.update("profile_requests", fields, "id=?", (pend["id"],))
        req_id, action = pend["id"], "修改资料申请"
    else:
        fields.update({"user_id": uid, "status": "pending", "created_at": util.now_ts()})
        req_id = db.insert("profile_requests", fields)
        action = "提交资料申请"
    db.log_action(uid, cur_name, action, "profile_request:%d" % req_id,
                  "头衔：%s → %s" % (cur_title or "（无）", new_title or "（无）"), ctx.ip)
    _notify_profile_reviewers(row)
    return {"id": req_id, "status": "pending",
            "old_display_name": cur_name, "new_display_name": new_name,
            "old_title": cur_title, "new_title": new_title}


@route("/api/auth/profile-request", "POST")
def submit_profile_request(ctx):
    """提交头衔修改申请；未开启审核要求时直接生效（昵称走 /api/auth/profile 直接保存）"""
    want = collect_locked_fields(ctx)
    if not want:
        raise ApiError("请填写需要修改的头衔")
    if not need_profile_approval(ctx.user):
        db.update("users", want, "id=?", (ctx.user["id"],))
        from core import realtime
        realtime.broadcast({"t": "user_update", "d": {"user_id": ctx.user["id"]}})
        row = db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
        return {"direct": True, "user": app_ctx.APP.public_user(dict(row), full=True),
                "msg": "资料已更新"}
    req = _create_profile_request(ctx, want, ctx.get("reason", ""))
    return {"direct": False, "request": req, "msg": "申请已提交，等待管理员审核"}


@route("/api/auth/profile-requests", "GET")
def my_profile_requests(ctx):
    rows = db.query("SELECT * FROM profile_requests WHERE user_id=? ORDER BY id DESC LIMIT 20",
                    (ctx.user["id"],))
    for r in rows:
        r["field_names"] = profile_request_fields(r)
    return {"list": [_request_view(r) for r in rows],
            "need_approval": need_profile_approval(ctx.user),
            "enabled": bool(app_ctx.APP.cfg.get("profile_change_need_approval", True))}


def profile_request_fields(r):
    """该申请实际改动（或将改动）的字段中文名"""
    names = []
    if (r["new_display_name"] or "") != (r["old_display_name"] or ""):
        names.append("昵称")
    if (r["new_title"] or "") != (r["old_title"] or ""):
        names.append("头衔")
    return "、".join(names) or "—"


@route("/api/auth/avatar", "POST")
def avatar(ctx):
    """上传头像（存为文件，返回 id）"""
    if not ctx.files:
        raise ApiError("请选择图片文件")
    f = ctx.files[0]
    ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "bmp"):
        raise ApiError("仅支持图片格式")
    fid = _save_file(f, ctx.user["id"], 0, "avatar")
    db.update("users", {"signature": db.one("SELECT signature FROM users WHERE id=?",
                                            (ctx.user["id"],))["signature"]}, "id=?", (ctx.user["id"],))
    db.set_setting("avatar_%d" % ctx.user["id"], str(fid))
    return {"file_id": fid, "msg": "头像已更新"}


def _save_file(f, user_id, room_id, kind):
    import os
    from core import config
    d = os.path.join(config.CFG.files_dir, util.now_str("%Y%m"))
    os.makedirs(d, exist_ok=True)
    stored = util.uid("f_") + "." + util.safe_name(f.filename).rsplit(".", 1)[-1]
    dst = os.path.join(d, stored)
    with open(f.tmp_path, "rb") as src, open(dst, "wb") as out:
        out.write(src.read())
    f.remove()
    return db.insert("files", {"name": f.filename, "stored": stored, "size": f.size,
                               "mime": "", "uploader_id": user_id, "room_id": room_id,
                               "kind": kind, "created_at": util.now_ts()})


# ============ 客户端免密登录（一次性授权码，内存存储，60 秒有效） ============
_client_codes = {}   # code -> {"user_id","username","display_name","expires"}


@route("/api/auth/client-code", "POST")
def client_code(ctx):
    """浏览器已登录用户生成一次性授权码，供客户端免密登录"""
    code = "%06d" % random.randint(0, 999999)
    # 同一用户旧码全部作废
    for k in list(_client_codes):
        if _client_codes[k]["user_id"] == ctx.user["id"]:
            del _client_codes[k]
    _client_codes[code] = {"user_id": ctx.user["id"],
                           "username": ctx.user.get("username"),
                           "display_name": ctx.user.get("display_name"),
                           "expires": util.now_ts() + 60}
    return {"code": code, "ttl": 60}


@route("/api/auth/client-login", "POST", perm="*")
def client_login(ctx):
    """客户端凭一次性授权码免密换取登录令牌"""
    code = (ctx.require("code") or "").strip()
    rec = _client_codes.get(code)
    if not rec or rec["expires"] < util.now_ts():
        raise ApiError("授权码无效或已过期，请重新获取", 401)
    del _client_codes[code]
    row = db.one("SELECT * FROM users WHERE id=?", (rec["user_id"],))
    if not row or row["status"] in ("blacklist", "disabled", "pending"):
        raise ApiError("账号不可用，请联系管理员", 403)
    if app_ctx.APP.cfg.get("maintenance_mode") and row["role"] != "superadmin":
        raise ApiError(app_ctx.APP.cfg.get("maintenance_note", "系统维护中"), 503)
    ua = str(ctx.headers.get("User-Agent", ""))[:200]
    token = app_ctx.APP.create_session(row["id"], ctx.ip, ua)
    # 与正常登录一致：单点登录，顶掉旧会话
    app_ctx.APP.kick_other_sessions(row["id"], token, ctx.ip)
    db.update("users", {"last_login": util.now_ts(), "last_seen": util.now_ts()},
              "id=?", (row["id"],))
    db.log_action(row["id"], row["display_name"], "客户端免密登录", "auth",
                  "IP:%s 客户端:%s 授权码" % (ctx.ip, util.client_of(ua)), ctx.ip)
    return {"token": token, "user": app_ctx.APP.public_user(dict(row), full=True)}
