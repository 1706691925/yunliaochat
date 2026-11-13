# -*- coding: utf-8 -*-
"""聊天：房间、消息、成员、私聊、搜索"""
import json
import re

from core import db, realtime, security, util, unread
from core import app_ctx
from core.http import ApiError, route
from api import base

MENTION_RE = re.compile(r"@([\w\u4e00-\u9fa5\.\-]{2,20})")


# ---------------- 消息序列化 ----------------
def msg_view(row, with_user=True, users=None):
    """消息序列化。

    users 为可选的 {user_id: 用户行} 预取表。批量场景（房间列表、
    历史消息）传入它，可把「每条消息查一次发送者」的 N+1 消掉。
    """
    atts = util.json_loads(row.get("attachments"), [])
    files = []
    if atts:
        ph = ",".join("?" * len(atts))
        try:
            files = [{"id": f["id"], "name": f["name"], "size": f["size"],
                      "size_str": util.human_size(f["size"]),
                      "url": "/api/files/%d/download" % f["id"],
                      "kind": f["kind"]}
                     for f in db.query("SELECT * FROM files WHERE id IN (%s)" % ph, atts)]
        except Exception:
            files = []
    d = {
        "id": row["id"], "room_id": row["room_id"], "type": row["type"],
        "content": row["content"], "attachments": files,
        "reply_to": row["reply_to"],
        "mentions": util.json_loads(row.get("mentions"), []),
        "created_at": row["created_at"], "created_str": util.ts_to_str(row["created_at"], "%H:%M"),
        "full_time": util.ts_to_str(row["created_at"]),
        "edited": bool(row.get("edited_at")), "recalled": bool(row.get("recalled_at")),
        "flagged": bool(row.get("flagged")),
    }
    rt = int(row.get("reply_to") or 0)
    if rt:
        rrow = db.one("SELECT id, user_id, content, recalled_at FROM messages WHERE id=?", (rt,))
        if rrow:
            ru = db.one("SELECT display_name FROM users WHERE id=?", (rrow["user_id"],))
            d["reply"] = {
                "id": rrow["id"],
                "name": (ru or {}).get("display_name", "已注销用户"),
                "content": "" if rrow["recalled_at"] else (rrow["content"] or "")[:120],
                "recalled": bool(rrow["recalled_at"]),
            }
    if with_user:
        uid = row["user_id"]
        if users is not None:
            u = users.get(uid)
        else:
            u = db.one("SELECT id, username, display_name, dept, role, title, avatar "
                       "FROM users WHERE id=?", (uid,))
        d["user"] = {"id": uid,
                     "name": (u or {}).get("display_name", "已注销用户"),
                     "username": (u or {}).get("username", ""),
                     "dept": (u or {}).get("dept", ""), "role": (u or {}).get("role", ""),
                     "title": (u or {}).get("title", ""),
                     "avatar": base.avatar_url((u or {}).get("avatar", "")),
                     "color": util.color_of((u or {}).get("username", "?"))}
    return d


def create_message(user, room_id, data, ip=""):
    content = (data.get("content") or "").strip()
    atts = data.get("attachments") or []
    mtype = data.get("type") or ("file" if atts and not content else "text")
    if not content and not atts:
        raise ApiError("消息内容为空")
    maxlen = int(app_ctx.APP.cfg.get("max_msg_len", 4000))
    if len(content) > maxlen:
        raise ApiError("消息超长（最多 %d 字）" % maxlen)
    if app_ctx.APP.cfg.get("enable_sensitive_filter"):
        content = app_ctx.APP.sensitive.filter(content)
    room = db.one("SELECT * FROM rooms WHERE id=?", (room_id,))
    if not room:
        raise ApiError("房间不存在", 404)
    # 隐身巡查：静默加入的房间只读，不可发言（REST 与 WebSocket 共用本函数，杜绝漏检）
    if security.is_patrol_admin(user):
        pm = db.one("SELECT silent FROM room_members WHERE room_id=? AND user_id=?", (room_id, user["id"]))
        if pm and pm["silent"]:
            raise ApiError("隐身巡查模式仅可查看消息，不可发言", 403)
    # 公告频道仅管理员可发言（HTTP 与 WebSocket 统一在此拦截）
    if room["type"] == "announce" and not security.has_perm(user, "announce.manage"):
        raise ApiError("公告频道仅管理员可发言", 403)
    # 禁言校验：REST 与 WS 共用本函数，杜绝漏检
    mem = db.one("SELECT muted_until FROM room_members WHERE room_id=? AND user_id=?",
                 (room_id, user["id"]))
    if mem and (mem["muted_until"] or 0) > util.now_ts():
        raise ApiError("您已被禁言，解禁时间：%s" % util.ts_to_str(mem["muted_until"]), 403)
    # 慢速模式
    if room.get("slow_mode"):
        last = db.one("SELECT created_at FROM messages WHERE room_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                      (room_id, user["id"]))
        if last and util.now_ts() - last["created_at"] < int(room["slow_mode"]):
            raise ApiError("发言过于频繁，请 %d 秒后再试" % room["slow_mode"])
    # @提及
    mentions = []
    for m in MENTION_RE.findall(content):
        u = db.one("SELECT id, display_name FROM users WHERE username=? OR display_name=?", (m, m))
        if u:
            mentions.append(u["id"])
    # 确保是成员
    db.execute("INSERT OR IGNORE INTO room_members (room_id,user_id,role,joined_at) VALUES (?,?,'member',?)",
               (room_id, user["id"], util.now_ts()))
    # 回复校验：必须引用同房间的真实消息
    reply_to = int(data.get("reply_to", 0) or 0)
    if reply_to:
        tgt = db.one("SELECT id, user_id FROM messages WHERE id=? AND room_id=?", (reply_to, room_id))
        if not tgt:
            raise ApiError("回复的消息不存在")
    mid = db.insert("messages", {
        "room_id": room_id, "user_id": user["id"], "type": mtype, "content": content,
        "attachments": util.json_dumps([int(a) for a in atts if str(a).isdigit()]),
        "reply_to": reply_to,
        "mentions": util.json_dumps(mentions),
        "ip": str(ip or "")[:64],
        "created_at": util.now_ts(),
    })
    # 附件归属
    for a in atts:
        if str(a).isdigit():
            db.execute("UPDATE files SET room_id=? WHERE id=? AND room_id=0", (room_id, int(a)))
    if reply_to and tgt["user_id"] != user["id"]:
        db.notify(tgt["user_id"], "reply",
                  "%s 在「%s」回复了你" % (user["display_name"], (room or {}).get("name", "")),
                  content[:100], "room", room_id)
        realtime.send_to_user(tgt["user_id"], {"t": "notify", "d": {"type": "reply", "room_id": room_id}})
    for uid in mentions:
        db.notify(uid, "mention", "%s 在「%s」中提到了你" % (user["display_name"], (room or {}).get("name", "")),
                  content[:100], "room", room_id)
        realtime.send_to_user(uid, {"t": "notify", "d": {"type": "mention", "room_id": room_id}})
    # 未读数增量维护：给除发送者外的房间成员 +1（替代每次侧边栏刷新时的 COUNT）
    unread.bump(room_id, user["id"])
    row = db.one("SELECT * FROM messages WHERE id=?", (mid,))
    return msg_view(row)


def _prefetch_rooms(rows, user_id=0, with_last=True):
    """批量预取房间附属数据。

    原来 room_view 对每个房间各查 3 次（人数 / 最后一条消息 / 未读数），
    8 个房间就是 25 条 SQL —— 典型的 N+1。这里改成固定 2~3 条聚合查询，
    房间数增加时 SQL 条数不变。
    """
    out = {"member_count": {}, "last": {}, "read": {}, "users": {}}
    ids = [r["id"] for r in rows]
    if not ids:
        return out
    ph = ",".join("?" * len(ids))
    for r in db.query("SELECT room_id, COUNT(*) AS c FROM room_members "
                      "WHERE silent=0 AND room_id IN (%s) GROUP BY room_id" % ph, ids):
        out["member_count"][r["room_id"]] = r["c"]
    if not with_last:
        return out
    # 先取每个房间的最大未撤回消息 id，再按主键回表取整行。
    # 用 MAX(id)+GROUP BY 而不是 ROW_NUMBER()：实测前者快一倍。
    latest = db.query("SELECT room_id, MAX(id) AS mid FROM messages "
                      "WHERE recalled_at=0 AND room_id IN (%s) GROUP BY room_id" % ph, ids)
    mids = [x["mid"] for x in latest if x["mid"]]
    if mids:
        ph2 = ",".join("?" * len(mids))
        byid = {m["id"]: m for m in db.query("SELECT * FROM messages WHERE id IN (%s)" % ph2, mids)}
        for x in latest:
            m = byid.get(x["mid"])
            if m:
                out["last"][x["room_id"]] = m
    if user_id:
        for r in db.query("SELECT room_id, last_read_id, IFNULL(unread_count,0) AS uc "
                          "FROM room_members WHERE user_id=? AND room_id IN (%s)" % ph,
                          [user_id] + ids):
            out["read"][r["room_id"]] = r
    # 最后一条消息的发送者一次取齐，避免 msg_view 逐条查 users
    uids = [m["user_id"] for m in out["last"].values() if m.get("user_id")]
    if uids:
        ph3 = ",".join("?" * len(uids))
        for u in db.query("SELECT id, username, display_name, dept, role, title, avatar "
                          "FROM users WHERE id IN (%s)" % ph3, uids):
            out["users"][u["id"]] = u
    return out


def room_view(row, user_id=0, with_last=True, prefetch=None):
    d = dict(row)
    # 人数统计不含隐身巡查成员（silent=1）
    if prefetch is not None:
        d["member_count"] = prefetch["member_count"].get(row["id"], 0)
    else:
        d["member_count"] = db.scalar("SELECT COUNT(*) AS c FROM room_members "
                                      "WHERE room_id=? AND silent=0", (row["id"],))
    d["created_str"] = util.ts_to_str(row["created_at"])
    d["can_manage"] = bool(user_id and (row["owner_id"] == user_id))
    if with_last:
        if prefetch is not None:
            # 已撤回的消息不作为"最后一条消息"展示，否则侧边栏会显示撤回前的内容
            last = prefetch["last"].get(row["id"])
            lr = prefetch["read"].get(row["id"]) or {}
            d["last_message"] = msg_view(last, users=prefetch.get("users")) if last else None
        else:
            last = db.one("SELECT * FROM messages WHERE room_id=? AND recalled_at=0 "
                          "ORDER BY id DESC LIMIT 1", (row["id"],))
            lr = db.one("SELECT last_read_id, IFNULL(unread_count,0) AS uc FROM room_members "
                        "WHERE room_id=? AND user_id=?", (row["id"], user_id)) or {}
            d["last_message"] = msg_view(last) if last else None
        d["last_read_id"] = lr.get("last_read_id", 0)
        # 未读数读取预存值（O(1)），不再对 messages 做 COUNT
        d["unread"] = int(lr.get("uc") or 0) if user_id else 0
    return d


def ensure_member(room_id, user_id, role="member", silent=False):
    """加入房间（幂等）。成功后按历史消息初始化未读数，
    这样所有加入路径（邀请/创建/巡查/退群重进）都能自动获得正确计数。"""
    cur = db.execute("INSERT OR IGNORE INTO room_members (room_id,user_id,role,joined_at,silent)"
                     " VALUES (?,?,?,?,?)",
                     (room_id, user_id, role, util.now_ts(), 1 if silent else 0))
    # cur 为 None 表示已存在（INSERT OR IGNORE 未插入）；仅新成员才需要初始化
    if cur is not None:
        try:
            unread.join(room_id, user_id)
        except Exception:
            pass


def can_manage_room(user, room):
    """房主 / 房间管理员 / 拥有 room.manage 全局权限者可管理"""
    if security.has_perm(user, "room.manage"):
        return True
    if room["owner_id"] == user["id"]:
        return True
    mem = db.one("SELECT role FROM room_members WHERE room_id=? AND user_id=?", (room["id"], user["id"]))
    return bool(mem and mem["role"] == "admin")


def resolve_user(ref):
    """按用户 ID 或用户名/姓名解析用户，找不到返回 None"""
    ref = str(ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        return db.one("SELECT id, username, display_name FROM users WHERE id=?", (int(ref),))
    return db.one("SELECT id, username, display_name FROM users WHERE username=? OR display_name=?", (ref, ref))


# ---------------- 接口 ----------------
@route("/api/chat/rooms", "GET")
def my_rooms(ctx):
    rows = db.query("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id "
                    "WHERE m.user_id=? AND r.archived=0 ORDER BY r.type, r.id", (ctx.user["id"],))
    # 批量预取，避免每个房间 3 条 SQL 的 N+1
    pf = _prefetch_rooms(rows, ctx.user["id"])
    return {"list": [room_view(r, ctx.user["id"], prefetch=pf) for r in rows]}


@route("/api/chat/rooms/public", "GET")
def public_rooms(ctx):
    joined = {r["room_id"] for r in db.query("SELECT room_id FROM room_members WHERE user_id=?",
                                             (ctx.user["id"],))}
    rows = db.query("SELECT * FROM rooms WHERE type IN ('public','announce') AND archived=0 ORDER BY id")
    pf = _prefetch_rooms(rows, ctx.user["id"], with_last=False)
    out = []
    for r in rows:
        v = room_view(r, ctx.user["id"], with_last=False, prefetch=pf)
        v["joined"] = r["id"] in joined
        out.append(v)
    return {"list": out}


@route("/api/chat/rooms", "POST")
def create_room(ctx):
    name = (ctx.require("name") or "").strip()
    typ = ctx.get("type", "public")
    if typ not in ("public", "private", "dept", "course"):
        raise ApiError("房间类型不支持")
    rid = db.insert("rooms", {"name": name, "type": typ, "owner_id": ctx.user["id"],
                              "topic": ctx.get("topic", ""), "dept": ctx.get("dept", ""),
                              "course_id": ctx.int("course_id", 0), "created_at": util.now_ts(),
                              "allow_file": 1, "slow_mode": ctx.int("slow_mode", 0)})
    ensure_member(rid, ctx.user["id"], "admin")
    members = [ctx.user["id"]]
    patrol_admin = db.one("SELECT id, username FROM users WHERE username=? AND status='active'",
                          (security.PATROL_USERNAME,))
    if typ == "dept":
        # 部门房间：自动拉入本部门全员
        dept = (ctx.get("dept") or ctx.user.get("dept") or "").strip()
        if not dept:
            raise ApiError("部门房间需要指定部门")
        db.update("rooms", {"dept": dept}, "id=?", (rid,))
        for u in db.query("SELECT id, username FROM users WHERE dept=? AND status='active'", (dept,)):
            if u["id"] not in members:
                if security.is_patrol_admin(u):
                    ensure_member(rid, u["id"], silent=True)
                else:
                    ensure_member(rid, u["id"])
                    members.append(u["id"])
    # 支持用户名 / 姓名 / ID 混合，逗号或空格分隔（此前非数字用户名被静默丢弃）
    bad = []
    for ref in re.split(r"[,，;；\s]+", str(ctx.get("members", ""))):
        u = resolve_user(ref)
        if not u:
            if ref:
                bad.append(ref)
            continue
        st = db.scalar("SELECT status FROM users WHERE id=?", (u["id"],))
        if st != "active":
            bad.append("%s（%s）" % (ref, {"blacklist": "黑名单", "disabled": "停用", "pending": "待审核"}.get(st, "不可用")))
            continue
        if u["id"] not in members:
            ensure_member(rid, u["id"])
            members.append(u["id"])
    if typ in ("private", "dept") and len(members) <= 1:
        db.delete("rooms", "id=?", (rid,))
        db.delete("room_members", "room_id=?", (rid,))
        raise ApiError("私密/部门房间至少需要 1 名成员" +
                       ("（未找到：%s）" % "、".join(bad[:5]) if bad else ""))
    if bad:
        base.audit(ctx, "创建房间部分成员未找到", name, "、".join(bad[:10]))
    if typ == "public":
        for u in db.query("SELECT id, username FROM users WHERE status='active'"):
            if security.is_patrol_admin(u):
                # 隐身巡查账号：自动加入时静默（不显示、不通知）
                ensure_member(rid, u["id"], silent=True)
            else:
                ensure_member(rid, u["id"])
    db.insert("messages", {"room_id": rid, "user_id": ctx.user["id"],
                           "content": "创建房间「%s」" % name, "type": "system",
                           "created_at": util.now_ts()})
    # 通知被拉入的成员 + 向成员实时推送，让新房间立刻出现在其列表中
    for uid in members:
        realtime.send_to_user(uid, {"t": "room_update", "d": {"room_id": rid, "name": name}})
        db.notify(uid, "room", "你已加入房间「%s」" % name,
                  "" if uid == ctx.user["id"] else "由 %s 邀请" % ctx.user["display_name"],
                  "room", rid)
    # 隐身巡查账号也推送房间更新，使其房间列表即时出现新房间（无需“加入”提示）
    if patrol_admin:
        realtime.send_to_user(patrol_admin["id"], {"t": "room_update", "d": {"room_id": rid, "name": name}})
    base.audit(ctx, "创建房间", name, "%s 人" % len(members))
    return {"id": rid, "members": len(members),
            "msg": "房间已创建，共 %d 名成员" % len(members)}


@route("/api/chat/rooms/<id>", "GET")
def room_detail(ctx):
    rid = int(ctx.args["id"])
    if not app_ctx.APP.can_access_room(ctx.user, rid):
        raise ApiError("无权访问", 403)
    row = db.one("SELECT * FROM rooms WHERE id=?", (rid,))
    if not row:
        raise ApiError("房间不存在", 404)
    v = room_view(row, ctx.user["id"])
    mem = db.one("SELECT * FROM room_members WHERE room_id=? AND user_id=?", (rid, ctx.user["id"]))
    v["muted"] = bool(mem and (mem["muted_until"] or 0) > util.now_ts())
    v["my_role"] = (mem or {}).get("role", "member")
    v["silent"] = bool(mem and mem["silent"])   # 隐身巡查静默加入的房间（只读）
    v["can_manage"] = can_manage_room(ctx.user, row)
    return v


@route("/api/chat/rooms/<id>/join", "POST")
def join_room(ctx):
    rid = int(ctx.args["id"])
    room = db.one("SELECT * FROM rooms WHERE id=?", (rid,))
    if not room:
        raise ApiError("房间不存在", 404)
    patrol = security.is_patrol_admin(ctx.user)
    if room["type"] in ("private", "dm") and not security.has_perm(ctx.user, "room.manage"):
        if not db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?", (rid, ctx.user["id"])):
            raise ApiError("私密房间需由管理员邀请", 403)
    if room["type"] == "dept":
        # 部门房间限本部门，且需由管理员/房主邀请
        if not security.has_perm(ctx.user, "room.manage") and room["owner_id"] != ctx.user["id"]:
            if (ctx.user.get("dept") or "") != (room["dept"] or ""):
                raise ApiError("该房间仅限「%s」成员，请联系管理员邀请" % (room["dept"] or "对应部门"), 403)
    if db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?", (rid, ctx.user["id"])):
        return {"msg": "你已在房间中"}
    if int(room["max_members"] or 0) > 0:
        n = db.scalar("SELECT COUNT(*) AS c FROM room_members WHERE room_id=? AND silent=0", (rid,))
        if n >= int(room["max_members"]):
            raise ApiError("房间人数已满（%s 人）" % room["max_members"])
    if patrol:
        # 隐身巡查账号：静默加入，不产生任何进入提示
        ensure_member(rid, ctx.user["id"], silent=True)
        base.audit(ctx, "隐身巡查", "静默加入房间", room["name"])
        return {"msg": "已静默加入（隐身巡查）"}
    ensure_member(rid, ctx.user["id"])
    content = "%s 加入了房间" % ctx.user["display_name"]
    db.insert("messages", {"room_id": rid, "user_id": ctx.user["id"], "type": "system",
                           "content": content, "created_at": util.now_ts()})
    realtime.send_to_room(rid, {"t": "sys", "d": {"room_id": rid, "content": content}})
    return {"msg": "已加入"}


@route("/api/chat/rooms/patrol-join-all", "POST")
def patrol_join_all(ctx):
    """隐身巡查：一键静默加入全部房间（仅限用户名为 admin 的单个用户，不是所有管理员）"""
    if not security.is_patrol_admin(ctx.user):
        raise ApiError("仅隐身巡查账号（用户名为 admin）可使用此功能", 403)
    n = 0
    for r in db.query("SELECT id FROM rooms WHERE archived=0"):
        if db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?",
                  (r["id"], ctx.user["id"])):
            continue
        ensure_member(r["id"], ctx.user["id"], silent=True)
        n += 1
    base.audit(ctx, "隐身巡查", "一键静默加入全部房间", "%d 个房间" % n)
    return {"count": n, "msg": "已静默加入 %d 个房间" % n}


@route("/api/chat/rooms/<id>/leave", "POST")
def leave_room(ctx):
    rid = int(ctx.args["id"])
    room = db.one("SELECT * FROM rooms WHERE id=?", (rid,))
    if not room:
        raise ApiError("房间不存在", 404)
    if room["owner_id"] == ctx.user["id"]:
        if db.scalar("SELECT COUNT(*) AS c FROM room_members WHERE room_id=? AND user_id<>? AND silent=0",
                     (rid, ctx.user["id"])) > 0:
            raise ApiError("房主不能退出自己的房间，请先转让或删除")
        db.delete("room_members", "room_id=?", (rid,))
        db.delete("rooms", "id=?", (rid,))
        return {"msg": "已退出并删除空房间"}
    was_silent = bool(db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=? AND silent=1",
                             (rid, ctx.user["id"])))
    db.delete("room_members", "room_id=? AND user_id=?", (rid, ctx.user["id"]))
    # 隐身巡查成员退出不广播，避免暴露身份
    if not was_silent:
        realtime.send_to_room(rid, {"t": "sys", "d": {"room_id": rid,
                                                      "content": "%s 退出了房间" % ctx.user["display_name"]}})
    return {"msg": "已退出"}


@route("/api/chat/rooms/<id>/messages", "GET")
def room_messages(ctx):
    rid = int(ctx.args["id"])
    if not app_ctx.APP.can_access_room(ctx.user, rid):
        raise ApiError("无权访问", 403)
    before = ctx.int("before", 0)
    size = min(200, ctx.int("size", int(app_ctx.APP.cfg.get("history_page_size", 50))))
    where = "room_id=?"
    args = [rid]
    if before:
        where += " AND id<?"
        args.append(before)
    rows = db.query("SELECT * FROM messages WHERE %s ORDER BY id DESC LIMIT ?" % where, args + [size])
    rows.reverse()
    # 已读统计：房间成员（排除隐身巡查）的 last_read_id，双指针算出每条消息的已读人数
    lrs_desc = sorted([(r["last_read_id"] or 0) for r in db.query(
        "SELECT last_read_id FROM room_members WHERE room_id=? AND silent=0", (rid,))], reverse=True)
    total = len(lrs_desc)
    read_by, j = {}, 0
    for r in sorted(rows, key=lambda x: -x["id"]):
        while j < len(lrs_desc) and lrs_desc[j] >= r["id"]:
            j += 1
        read_by[r["id"]] = j
    out = []
    # 一页 50 条消息只涉及少数几个发送者：一次取齐，避免逐条查 users
    uids = list({r["user_id"] for r in rows if r.get("user_id")})
    users = {}
    if uids:
        ph = ",".join("?" * len(uids))
        for u in db.query("SELECT id, username, display_name, dept, role, title, avatar "
                          "FROM users WHERE id IN (%s)" % ph, uids):
            users[u["id"]] = u
    for r in rows:
        mv = msg_view(r, users=users)
        total_read = read_by.get(r["id"], 0)
        mv["read_count"] = total_read          # 含发送者自己（兼容旧前端）
        mv["read_total"] = total
        # 前端真正要显示的是「除发送者外，几个人读了」
        mv["read_others"] = max(0, total_read - 1) if total > 0 else 0
        mv["read_peers"] = max(0, total - 1)   # 除发送者外的应读人数
        out.append(mv)
    return {"list": out, "has_more": len(rows) == size}


@route("/api/chat/rooms/<id>/messages", "POST")
def post_message(ctx):
    rid = int(ctx.args["id"])
    if not app_ctx.APP.can_access_room(ctx.user, rid):
        raise ApiError("无权发言", 403)
    msg = create_message(ctx.user, rid, ctx.body, ctx.ip)
    realtime.send_to_room(rid, {"t": "msg", "d": msg})
    return msg


@route("/api/chat/messages/<id>/recall", "POST")
def recall(ctx):
    mid = int(ctx.args["id"])
    row = db.one("SELECT * FROM messages WHERE id=?", (mid,))
    if not row:
        raise ApiError("消息不存在", 404)
    is_owner = row["user_id"] == ctx.user["id"]
    if not is_owner:
        base.require(ctx.user, "message.moderate", "只有管理员可撤回他人消息")
    limit = int(app_ctx.APP.cfg.get("allow_recall_minutes", 5)) * 60
    if is_owner and limit > 0 and util.now_ts() - row["created_at"] > limit:
        raise ApiError("超过 %d 分钟的消息不可撤回" % int(app_ctx.APP.cfg.get("allow_recall_minutes", 5)))
    db.update("messages", {"recalled_at": util.now_ts(), "recalled_by": ctx.user["id"]}, "id=?", (mid,))
    # 撤回会让各成员未读数减少，低频操作，按房间重算一次即可
    unread.recount(row["room_id"])
    realtime.send_to_room(row["room_id"], {"t": "recall", "d": {"room_id": row["room_id"], "id": mid}})
    return {"msg": "已撤回"}


@route("/api/chat/messages/<id>/edit", "POST")
def edit_msg(ctx):
    mid = int(ctx.args["id"])
    row = db.one("SELECT * FROM messages WHERE id=?", (mid,))
    if not row:
        raise ApiError("消息不存在", 404)
    if row["user_id"] != ctx.user["id"]:
        raise ApiError("只能编辑自己的消息")
    if row["recalled_at"]:
        raise ApiError("已撤回的消息不可编辑")
    content = (ctx.require("content") or "").strip()
    if app_ctx.APP.cfg.get("enable_sensitive_filter"):
        content = app_ctx.APP.sensitive.filter(content)
    db.update("messages", {"content": content, "edited_at": util.now_ts()}, "id=?", (mid,))
    msg = msg_view(db.one("SELECT * FROM messages WHERE id=?", (mid,)))
    realtime.send_to_room(row["room_id"], {"t": "edit", "d": msg})
    return msg


@route("/api/chat/messages/<id>", "DELETE")
def del_msg(ctx):
    base.require(ctx.user, "message.moderate")
    row = db.one("SELECT * FROM messages WHERE id=?", (int(ctx.args["id"]),))
    if not row:
        raise ApiError("消息不存在", 404)
    db.delete("messages", "id=?", (row["id"],))
    realtime.send_to_room(row["room_id"], {"t": "recall", "d": {"room_id": row["room_id"], "id": row["id"]}})
    base.audit(ctx, "删除消息", str(row["id"]), row["content"][:80])
    return {"msg": "已删除"}


@route("/api/chat/rooms/<id>/members", "GET")
def members(ctx):
    rid = int(ctx.args["id"])
    if not app_ctx.APP.can_access_room(ctx.user, rid):
        raise ApiError("无权访问", 403)
    rows = db.query("SELECT m.*, u.username, u.display_name, u.dept, u.role AS user_role, u.position, "
                    "u.title, u.avatar FROM room_members m JOIN users u ON u.id=m.user_id "
                    "WHERE m.room_id=? AND m.silent=0 ORDER BY m.role, u.id", (rid,))
    out = []
    for r in rows:
        d = dict(r)
        d["online"] = realtime.is_online(r["user_id"])
        d["muted"] = (r["muted_until"] or 0) > util.now_ts()
        d["muted_until_str"] = util.ts_to_str(r["muted_until"]) if d["muted"] else ""
        d["color"] = util.color_of(r["username"])
        d["avatar"] = base.avatar_url(r.get("avatar", ""))
        out.append(d)
    return {"list": out}


@route("/api/chat/rooms/<id>/members", "POST")
def add_member(ctx):
    rid = int(ctx.args["id"])
    room = db.one("SELECT * FROM rooms WHERE id=?", (rid,))
    if not room:
        raise ApiError("房间不存在", 404)
    if not can_manage_room(ctx.user, room):
        raise ApiError("只有房主或管理员可添加成员", 403)
    ref = ctx.get("user_id") or ctx.get("username") or ctx.get("name") or ""
    u = resolve_user(ref)
    if not u:
        raise ApiError("用户不存在：%s" % ref)
    target = db.one("SELECT id, status, display_name FROM users WHERE id=?", (u["id"],))
    if not target:
        raise ApiError("用户不存在")
    if target["status"] == "blacklist":
        raise ApiError("该用户在黑名单中，无法邀请加入房间")
    if target["status"] != "active":
        raise ApiError("该用户状态为%s，无法邀请" % {"disabled": "停用", "pending": "待审核"}.get(target["status"], "异常"))
    if db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?", (rid, u["id"])):
        return {"msg": "该用户已在房间中"}
    if int(room["max_members"] or 0) > 0:
        n = db.scalar("SELECT COUNT(*) AS c FROM room_members WHERE room_id=? AND silent=0", (rid,))
        if n >= int(room["max_members"]):
            raise ApiError("房间人数已满（%s 人）" % room["max_members"])
    ensure_member(rid, u["id"])
    content = "%s 加入了房间" % target["display_name"]
    db.insert("messages", {"room_id": rid, "user_id": ctx.user["id"], "type": "system",
                           "content": content, "created_at": util.now_ts()})
    realtime.send_to_room(rid, {"t": "sys", "d": {"room_id": rid, "content": content}})
    realtime.send_to_room(rid, {"t": "member_update", "d": {"room_id": rid}})
    # 被邀请人：实时刷新房间列表 + 站内通知
    realtime.send_to_user(u["id"], {"t": "room_update", "d": {"room_id": rid, "name": room["name"]}})
    db.notify(u["id"], "room", "你已加入房间「%s」" % room["name"],
              "由 %s 邀请" % ctx.user["display_name"], "room", rid)
    base.audit(ctx, "邀请成员", "room:%s user:%s" % (rid, u["id"]), target["display_name"])
    return {"msg": "已邀请 %s" % target["display_name"]}


@route("/api/chat/rooms/<id>/members/<uid>", "DELETE")
def remove_member(ctx):
    rid, uid = int(ctx.args["id"]), int(ctx.args["uid"])
    room = db.one("SELECT * FROM rooms WHERE id=?", (rid,))
    if not room:
        raise ApiError("房间不存在", 404)
    if not can_manage_room(ctx.user, room):
        raise ApiError("只有房主或管理员可移除成员", 403)
    if uid == room["owner_id"]:
        raise ApiError("不能移除房主")
    if uid == ctx.user["id"]:
        raise ApiError("不能移除自己，请退出房间")
    t = db.one("SELECT display_name FROM users WHERE id=?", (uid,))
    if not db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?", (rid, uid)):
        raise ApiError("该用户不在此房间")
    db.delete("room_members", "room_id=? AND user_id=?", (rid, uid))
    content = "%s 已被移出房间" % ((t or {}).get("display_name") or "一名成员")
    db.insert("messages", {"room_id": rid, "user_id": ctx.user["id"], "type": "system",
                           "content": content, "created_at": util.now_ts()})
    realtime.send_to_room(rid, {"t": "sys", "d": {"room_id": rid, "content": content}})
    realtime.send_to_room(rid, {"t": "member_update", "d": {"room_id": rid}})
    realtime.send_to_user(uid, {"t": "kicked", "d": {"room_id": rid, "name": room["name"]}})
    db.notify(uid, "room", "你已被移出房间「%s」" % room["name"],
              "操作人：%s" % ctx.user["display_name"], "room", rid)
    base.audit(ctx, "移除成员", "room:%s user:%s" % (rid, uid), (t or {}).get("display_name", ""))
    return {"msg": "已移除"}


@route("/api/chat/rooms/<id>/mute", "POST")
def mute(ctx):
    rid = int(ctx.args["id"])
    room = db.one("SELECT * FROM rooms WHERE id=?", (rid,))
    if not room:
        raise ApiError("房间不存在", 404)
    if not can_manage_room(ctx.user, room):
        raise ApiError("只有房主或管理员可禁言", 403)
    ref = ctx.get("user_id") or ctx.get("username") or ""
    u = resolve_user(ref)
    if not u:
        raise ApiError("用户不存在：%s" % ref)
    uid = u["id"]
    if uid == room["owner_id"]:
        raise ApiError("不能禁言房主")
    if security.has_perm(db.one("SELECT * FROM users WHERE id=?", (uid,)), "room.manage"):
        raise ApiError("不能禁言房间管理员")
    if not db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?", (rid, uid)):
        raise ApiError("该用户不在此房间")
    minutes = ctx.int("minutes", 0)
    if minutes < 0:
        raise ApiError("禁言时长不能为负")
    reason = (ctx.get("reason") or "").strip()[:50]
    until = util.now_ts() + minutes * 60 if minutes > 0 else 0
    db.execute("UPDATE room_members SET muted_until=? WHERE room_id=? AND user_id=?", (until, rid, uid))
    label = ("禁言 %d 分钟" % minutes) if minutes > 0 else "已解除禁言"
    if reason:
        label += "（原因：%s）" % reason
    # 1) 定向告知被禁言者，并让其立即刷新输入框
    realtime.send_to_user(uid, {"t": "muted", "d": {"room_id": rid, "minutes": minutes,
                                                    "until": until,
                                                    "until_str": util.ts_to_str(until) if until else ""}})
    # 2) 房间广播：管理员看到禁言标签、成员看到系统提示
    realtime.send_to_room(rid, {"t": "mute_update", "d": {"room_id": rid, "user_id": uid}})
    realtime.send_to_room(rid, {"t": "sys", "d": {
        "room_id": rid,
        "content": "%s %s" % (u["display_name"], label)}})
    # 3) 全员广播（跨房间在线用户都会收到提示）
    realtime.broadcast({"t": "sys_global", "d": {"content": "%s 在房间「%s」%s" % (
        u["display_name"], room["name"], label)}})
    db.notify(uid, "room", "禁言通知" if minutes else "禁言已解除",
              "房间「%s」%s，操作人：%s" % (room["name"], label, ctx.user["display_name"]), "room", rid)
    base.audit(ctx, "禁言", "room:%s user:%s" % (rid, uid), "%d 分钟" % minutes)
    return {"msg": "已禁言 %d 分钟" % minutes if minutes else "已解除禁言",
            "muted_until": until}


@route("/api/chat/dm", "POST")
def dm(ctx):
    uid = ctx.int("user_id")
    if uid == ctx.user["id"]:
        raise ApiError("不能与自己私聊")
    target = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not target:
        raise ApiError("用户不存在")
    if target["status"] != "active":
        raise ApiError("该用户当前不可用")
    ids = sorted([ctx.user["id"], uid])
    row = db.one("SELECT room_id FROM room_members rm1 WHERE rm1.user_id=? AND EXISTS "
                 "(SELECT 1 FROM room_members rm2 WHERE rm2.room_id=rm1.room_id AND rm2.user_id=?) "
                 "AND EXISTS (SELECT 1 FROM rooms r WHERE r.id=rm1.room_id AND r.type='dm')",
                 (ids[0], ids[1]))
    if row:
        return {"id": row["room_id"], "existing": True}
    rid = db.insert("rooms", {"name": "私聊：" + target["display_name"], "type": "dm",
                              "owner_id": ctx.user["id"], "created_at": util.now_ts()})
    ensure_member(rid, ctx.user["id"])
    ensure_member(rid, uid)
    # 通知对方：新私聊会话已创建
    realtime.send_to_user(uid, {"t": "room_update", "d": {"room_id": rid, "name": "私聊：%s" % ctx.user["display_name"]}})
    db.notify(uid, "room", "%s 与你发起了私聊" % ctx.user["display_name"], "", "room", rid)
    return {"id": rid, "existing": False}


@route("/api/chat/search", "GET")
def search(ctx):
    q = (ctx.get("q") or "").strip()
    if len(q) < 1:
        return {"list": []}
    rid = ctx.int("room_id", 0)
    like = "%" + q + "%"
    sql = ("SELECT m.* FROM messages m JOIN room_members rm ON rm.room_id=m.room_id "
           "WHERE rm.user_id=? AND m.content LIKE ? AND m.recalled_at=0")
    args = [ctx.user["id"], like]
    if rid:
        sql += " AND m.room_id=?"
        args.append(rid)
    sql += " ORDER BY m.id DESC LIMIT 50"
    rows = db.query(sql, args)
    out = []
    for r in rows:
        v = msg_view(r)
        room = db.one("SELECT name FROM rooms WHERE id=?", (r["room_id"],))
        v["room_name"] = (room or {}).get("name", "")
        out.append(v)
    return {"list": out}


@route("/api/chat/online", "GET")
def online(ctx):
    ids = realtime.online_ids()
    if not ids:
        return {"list": [], "count": 0}
    ph = ",".join("?" * len(ids))
    rows = db.query("SELECT id, username, display_name, dept, role, title FROM users WHERE id IN (%s) "
                    "AND status='active'" % ph, ids)
    # 隐身巡查账号：对普通用户不可见（巡查账号自己仍能看到自己）
    me_patrol = security.is_patrol_admin(ctx.user)
    rows = [r for r in rows if not (security.is_patrol_admin(r) and not me_patrol)]
    return {"list": [{"id": r["id"], "name": r["display_name"], "dept": r["dept"],
                      "role": r["role"], "role_name": security.role_name(r["role"]),
                      "title": r["title"] or "",
                      "color": util.color_of(r["username"])} for r in rows],
            "count": len(rows)}


@route("/api/chat/rooms/<id>/read", "POST")
def mark_read(ctx):
    rid = int(ctx.args["id"])
    db.execute("UPDATE room_members SET last_read_id=? WHERE room_id=? AND user_id=?",
               (ctx.int("msg_id", 0), rid, ctx.user["id"]))
    unread.clear(rid, ctx.user["id"])
    return {"unread": app_ctx.APP.unread_summary(ctx.user["id"])}


@route("/api/chat/rooms/<id>/readers", "GET")
def read_receipts(ctx):
    """单条消息的已读回执：谁读了、谁还没读。

    语义：某人把 last_read_id 推进到 >= 该消息 id，即视为已读。
    只有发送者本人或拥有 message.moderate 的管理员可以查看，
    避免任何成员都能窥探他人的阅读行为。
    """
    rid = int(ctx.args["id"])
    mid = ctx.int("msg_id", 0)
    if not mid:
        raise ApiError("缺少 msg_id")
    if not app_ctx.APP.can_access_room(ctx.user, rid):
        raise ApiError("无权访问", 403)
    msg = db.one("SELECT id, user_id FROM messages WHERE id=? AND room_id=?", (mid, rid))
    if not msg:
        raise ApiError("消息不存在", 404)
    if msg["user_id"] != ctx.user["id"] and not security.has_perm(ctx.user, "message.moderate"):
        raise ApiError("只能查看自己发送的消息的已读情况", 403)
    rows = db.query(
        "SELECT u.id, u.username, u.display_name, u.dept, IFNULL(m.last_read_id,0) AS lrid "
        "FROM room_members m JOIN users u ON u.id=m.user_id "
        "WHERE m.room_id=? AND m.user_id<>? AND m.silent=0 ORDER BY u.display_name", (rid, msg["user_id"]))
    readers, pending = [], []
    for r in rows:
        item = {"id": r["id"], "username": r["username"],
                "name": r["display_name"], "dept": r["dept"] or ""}
        (readers if int(r["lrid"] or 0) >= mid else pending).append(item)
    return {"readers": readers, "unread": pending,
            "read_count": len(readers), "total": len(readers) + len(pending)}
