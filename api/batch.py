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
"""后台批量操作：各列表页复选框多选后的批量执行接口。
逐条复用单条接口的校验规则，失败项跳过并返回原因，整体记一条审计。"""
from core import db, ipsec, realtime, security, util
from core import app_ctx
from core.http import ApiError, route
from api import base


def _ids(ctx):
    raw = ctx.body.get("ids") or []
    if isinstance(raw, str):
        raw = raw.split(",")
    ids, seen = [], set()
    for x in raw:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            ids.append(i)
    if not ids:
        raise ApiError("请先勾选要操作的项目")
    if len(ids) > 200:
        raise ApiError("单次批量操作最多 200 条")
    return ids


def _result(label, ok, fail, extra=None):
    msg = "%s：成功 %d 项" % (label, ok)
    if fail:
        msg += "，跳过/失败 %d 项" % len(fail)
    out = {"ok": ok, "fail": fail, "msg": msg}
    if extra:
        out.update(extra)
    return out


# ---------------- 用户 ----------------
@route("/api/admin/batch/users", "POST", perm="user.manage")
def batch_users(ctx):
    ids = _ids(ctx)
    action = str(ctx.get("action") or "").strip()
    reason = (ctx.get("reason") or "").strip()[:50]
    me = ctx.user
    ok, fail, passwords = 0, [], []
    label = {"disable": "批量停用", "enable": "批量启用", "blacklist": "批量加入黑名单",
             "unblacklist": "批量移出黑名单", "delete": "批量删除用户",
             "reset_pwd": "批量重置密码"}.get(action, "批量操作")
    for uid in ids:
        row = db.one("SELECT * FROM users WHERE id=?", (uid,))
        if not row:
            fail.append({"id": uid, "msg": "用户不存在"})
            continue
        try:
            if action == "disable":
                if uid == me["id"]:
                    raise ApiError("不能停用自己")
                if row["role"] == "superadmin" and me["role"] != "superadmin":
                    raise ApiError("无权停用超级管理员")
                if row["status"] == "blacklist":
                    raise ApiError("黑名单用户请先移出黑名单")
                if row["status"] == "disabled":
                    raise ApiError("已是停用状态")
                db.update("users", {"status": "disabled"}, "id=?", (uid,))
                db.delete("sessions", "user_id=?", (uid,))
                t = "您的账号已被管理员停用，如有疑问请联系管理员" + ("（原因：%s）" % reason if reason else "")
                realtime.send_and_close(uid, {"t": "banned", "d": {"msg": t}})
                db.notify(uid, "account", "账号已被停用",
                          "操作人：%s%s" % (me["display_name"], "，原因：%s" % reason if reason else ""),
                          "user", uid)
                realtime.broadcast({"t": "presence", "d": {"user_id": uid, "status": "offline"}})
                realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": "disabled",
                                                                 "name": row["display_name"]}})
                realtime.broadcast({"t": "sys_global", "d": {"content": "「%s」已被管理员停用（封禁）%s" %
                                    (row["display_name"], "，原因：%s" % reason if reason else "")}})
            elif action == "enable":
                if row["status"] != "disabled":
                    raise ApiError("该用户不是停用状态")
                db.update("users", {"status": "active"}, "id=?", (uid,))
                realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": "active",
                                                                 "name": row["display_name"]}})
                realtime.broadcast({"t": "sys_global", "d": {"content": "「%s」已恢复使用" % row["display_name"]}})
            elif action == "blacklist":
                if uid == me["id"]:
                    raise ApiError("不能将自己加入黑名单")
                if row["role"] == "superadmin" and me["role"] != "superadmin":
                    raise ApiError("无权将超级管理员加入黑名单")
                if row["status"] == "blacklist":
                    raise ApiError("已在黑名单中")
                db.update("users", {"status": "blacklist"}, "id=?", (uid,))
                db.delete("sessions", "user_id=?", (uid,))
                t = "您的账号已被管理员列入黑名单，禁止登录与使用" + ("（原因：%s）" % reason if reason else "")
                realtime.send_and_close(uid, {"t": "banned", "d": {"msg": t}})
                db.notify(uid, "account", "账号已被列入黑名单",
                          "操作人：%s%s" % (me["display_name"], "，原因：%s" % reason if reason else ""),
                          "user", uid)
                db.delete("room_members", "user_id=?", (uid,))
                realtime.broadcast({"t": "presence", "d": {"user_id": uid, "status": "offline"}})
                realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": "blacklist",
                                                                 "name": row["display_name"]}})
                realtime.broadcast({"t": "sys_global", "d": {"content": "「%s」已被管理员列入黑名单%s" %
                                    (row["display_name"], "，原因：%s" % reason if reason else "")}})
            elif action == "unblacklist":
                if row["status"] != "blacklist":
                    raise ApiError("该用户不在黑名单中")
                db.update("users", {"status": "active"}, "id=?", (uid,))
                realtime.broadcast({"t": "account_update", "d": {"user_id": uid, "status": "active",
                                                                 "name": row["display_name"]}})
                realtime.broadcast({"t": "sys_global", "d": {"content": "「%s」已被管理员移出黑名单" % row["display_name"]}})
                db.notify(uid, "account", "已移出黑名单", "操作人：%s，现可正常登录使用" % me["display_name"],
                          "user", uid)
            elif action == "delete":
                if uid == me["id"]:
                    raise ApiError("不能删除自己")
                if row["role"] == "superadmin":
                    raise ApiError("不能删除超级管理员")
                db.delete("sessions", "user_id=?", (uid,))
                db.delete("room_members", "user_id=?", (uid,))
                db.delete("users", "id=?", (uid,))
            elif action == "reset_pwd":
                if row["role"] == "superadmin" and me["role"] != "superadmin":
                    raise ApiError("无权重置超级管理员密码")
                if uid == me["id"]:
                    raise ApiError("不能重置自己的密码（请走个人改密）")
                pwd = security.gen_initial_password()
                db.update("users", {"password_hash": security.hash_password(pwd), "must_change_pwd": 1,
                                    "failed_attempts": 0, "locked_until": 0}, "id=?", (uid,))
                db.delete("sessions", "user_id=?", (uid,))
                passwords.append({"username": row["username"], "name": row["display_name"], "password": pwd})
            else:
                raise ApiError("未知的操作类型")
            ok += 1
        except ApiError as e:
            fail.append({"id": uid, "name": row["display_name"], "msg": getattr(e, "msg", str(e))})
        except Exception as e:
            fail.append({"id": uid, "name": row["display_name"], "msg": str(e)})
    base.audit(ctx, label, "user:%s" % ",".join(map(str, ids[:80])),
               ("成功 %d，失败 %d%s" % (ok, len(fail), ("，原因：%s" % reason) if reason else "")))
    return _result(label, ok, fail, {"passwords": passwords} if action == "reset_pwd" else None)


# ---------------- 房间 ----------------
@route("/api/admin/batch/rooms", "POST", perm="room.manage")
def batch_rooms(ctx):
    ids = _ids(ctx)
    action = str(ctx.get("action") or "").strip()
    label = {"archive": "批量归档", "unarchive": "批量取消归档", "delete": "批量删除房间"}.get(action, "批量操作房间")
    ok, fail = 0, []
    for rid in ids:
        row = db.one("SELECT id, name FROM rooms WHERE id=?", (rid,))
        if not row:
            fail.append({"id": rid, "msg": "房间不存在"})
            continue
        try:
            if action == "archive":
                db.update("rooms", {"archived": 1}, "id=?", (rid,))
            elif action == "unarchive":
                db.update("rooms", {"archived": 0}, "id=?", (rid,))
            elif action == "delete":
                db.delete("messages", "room_id=?", (rid,))
                db.delete("room_members", "room_id=?", (rid,))
                db.delete("rooms", "id=?", (rid,))
            else:
                raise ApiError("未知的操作类型")
            ok += 1
        except Exception as e:
            fail.append({"id": rid, "name": row["name"], "msg": str(getattr(e, "msg", e))})
    base.audit(ctx, label, "room:%s" % ",".join(map(str, ids[:80])), "成功 %d，失败 %d" % (ok, len(fail)))
    return _result(label, ok, fail)


# ---------------- 消息 ----------------
@route("/api/admin/batch/messages", "POST", perm="message.moderate")
def batch_messages(ctx):
    ids = _ids(ctx)
    action = str(ctx.get("action") or "").strip()
    label = {"recall": "批量撤回消息", "delete": "批量删除消息"}.get(action, "批量操作消息")
    ok, fail = 0, []
    for mid in ids:
        row = db.one("SELECT id, room_id, content, recalled_at FROM messages WHERE id=?", (mid,))
        if not row:
            fail.append({"id": mid, "msg": "消息不存在"})
            continue
        try:
            if action == "recall":
                if row["recalled_at"]:
                    raise ApiError("已撤回过")
                db.update("messages", {"recalled_at": util.now_ts(), "recalled_by": ctx.user["id"]}, "id=?", (mid,))
                realtime.send_to_room(row["room_id"], {"t": "recall", "d": {"room_id": row["room_id"], "id": mid}})
            elif action == "delete":
                db.delete("messages", "id=?", (mid,))
                realtime.send_to_room(row["room_id"], {"t": "recall", "d": {"room_id": row["room_id"], "id": mid}})
            else:
                raise ApiError("未知的操作类型")
            ok += 1
        except ApiError as e:
            fail.append({"id": mid, "msg": getattr(e, "msg", str(e))})
        except Exception as e:
            fail.append({"id": mid, "msg": str(e)})
    base.audit(ctx, label, "msg:%s" % ",".join(map(str, ids[:80])), "成功 %d，失败 %d" % (ok, len(fail)))
    return _result(label, ok, fail)


# ---------------- 公告 ----------------
@route("/api/admin/batch/announcements", "POST", perm="announce.manage")
def batch_announcements(ctx):
    ids = _ids(ctx)
    action = str(ctx.get("action") or "").strip()
    label = {"pin": "批量置顶公告", "unpin": "批量取消置顶", "delete": "批量删除公告"}.get(action, "批量操作公告")
    ok, fail = 0, []
    for aid in ids:
        row = db.one("SELECT id, title FROM announcements WHERE id=?", (aid,))
        if not row:
            fail.append({"id": aid, "msg": "公告不存在"})
            continue
        try:
            if action == "pin":
                db.update("announcements", {"pinned": 1}, "id=?", (aid,))
            elif action == "unpin":
                db.update("announcements", {"pinned": 0}, "id=?", (aid,))
            elif action == "delete":
                db.delete("announcements", "id=?", (aid,))
            else:
                raise ApiError("未知的操作类型")
            ok += 1
        except Exception as e:
            fail.append({"id": aid, "name": row["title"], "msg": str(e)})
    base.audit(ctx, label, "announce:%s" % ",".join(map(str, ids[:80])), "成功 %d，失败 %d" % (ok, len(fail)))
    return _result(label, ok, fail)


# ---------------- IP 黑名单 ----------------
@route("/api/admin/batch/ipblacklist", "POST", perm="user.manage")
def batch_ipbl(ctx):
    ids = _ids(ctx)
    ok, fail = 0, []
    for rid in ids:
        row = db.one("SELECT id, pattern FROM ip_blacklist WHERE id=?", (rid,))
        if not row:
            fail.append({"id": rid, "msg": "规则不存在"})
            continue
        good, msg = ipsec.remove(rid, ctx.user["id"], ctx.user["display_name"], ctx.ip)
        if good:
            ok += 1
        else:
            fail.append({"id": rid, "name": row["pattern"], "msg": msg})
    if ok:
        db.notify_all("IP 黑名单更新", "管理员批量移出了 %d 条 IP 封禁规则" % ok, "system", "ip_blacklist")
    base.audit(ctx, "批量移出 IP 黑名单", "id:%s" % ",".join(map(str, ids[:80])), "成功 %d，失败 %d" % (ok, len(fail)))
    return _result("批量移出 IP 规则", ok, fail)


# ---------------- 课程 / 题库 ----------------
@route("/api/admin/batch/learn/courses", "POST", perm="course.manage")
def batch_courses(ctx):
    ids = _ids(ctx)
    ok, fail = 0, []
    for cid in ids:
        row = db.one("SELECT id, title FROM courses WHERE id=?", (cid,))
        if not row:
            fail.append({"id": cid, "msg": "课程不存在"})
            continue
        try:
            db.delete("lessons", "course_id=?", (cid,))
            db.delete("enrollments", "course_id=?", (cid,))
            db.delete("courses", "id=?", (cid,))
            ok += 1
        except Exception as e:
            fail.append({"id": cid, "name": row["title"], "msg": str(e)})
    base.audit(ctx, "批量删除课程", "course:%s" % ",".join(map(str, ids[:80])), "成功 %d，失败 %d" % (ok, len(fail)))
    return _result("批量删除课程", ok, fail)


@route("/api/admin/batch/learn/questions", "POST", perm="question.manage")
def batch_questions(ctx):
    ids = _ids(ctx)
    ok, fail = 0, []
    for qid in ids:
        row = db.one("SELECT id, stem FROM questions WHERE id=?", (qid,))
        if not row:
            fail.append({"id": qid, "msg": "题目不存在"})
            continue
        try:
            db.delete("questions", "id=?", (qid,))
            db.delete("exam_questions", "question_id=?", (qid,))
            ok += 1
        except Exception as e:
            fail.append({"id": qid, "name": (row["stem"] or "")[:30], "msg": str(e)})
    base.audit(ctx, "批量删除题目", "question:%s" % ",".join(map(str, ids[:80])), "成功 %d，失败 %d" % (ok, len(fail)))
    return _result("批量删除题目", ok, fail)


# ---------------- 审计日志 ----------------
@route("/api/admin/batch/logs", "POST", perm="settings.manage")
def batch_logs(ctx):
    ids = _ids(ctx)
    n = db.scalar("SELECT COUNT(*) AS c FROM audit_logs WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
    ph = ",".join("?" * len(ids))
    db.delete("audit_logs", "id IN (%s)" % ph, ids)
    base.audit(ctx, "批量删除审计日志", "log:%s" % ",".join(map(str, ids[:80])), "删除 %d 条" % n)
    return _result("批量删除审计日志", n, [])
