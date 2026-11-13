# -*- coding: utf-8 -*-
"""远程协助中枢：被控端注册、协助会话信令、画面帧转发、输入指令下发。

安全设计（桌面级远控属高危能力，以下约束为强制项，不提供关闭开关）：
1. 功能总开关 enable_remote_assist 关闭时，一切注册与请求直接拒绝；
2. 管理员发起协助后，必须由被控端用户在本机弹窗中手动确认，确认前不下发任何画面；
3. 被控端可随时一键断开；管理员侧断开同样立即生效；
4. 单次协助有最长时长上限，超时自动断开，防止被长期占用；
5. 键鼠注入需要 remote.control 权限，仅 remote.view/request 只能看画面不能操作；
6. 请求、确认、断开、每一次输入类别变更均写入审计日志。

坐标约定：管理员端上行鼠标坐标一律使用 0~1 归一化浮点数，由被控端换算为
本机真实像素，避免两端分辨率不一致导致的错位。
"""
import json
import threading
import time

from core import db, util

_lock = threading.RLock()

# 被控端注册表：conn_id -> info
_agents = {}
# user_id -> conn_id（同一用户多端登录时以最后注册者为准）
_agent_by_user = {}
# 协助会话：session_id -> session
_sessions = {}
# conn_id -> session_id（管理员侧与被控端侧都要能反查自己的会话）
_conn_session = {}

_seq = 0


def _next_sid():
    global _seq
    _seq += 1
    return "rs%d%d" % (int(time.time()), _seq)


def feature_on():
    """功能总开关"""
    try:
        from core import config
        return bool(config.CFG.get("enable_remote_assist", False))
    except Exception:
        return False


def cfg_val(key, default):
    try:
        from core import config
        v = config.CFG.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


# ---------------- 被控端注册 ----------------
def register_agent(conn, user, device="", note=""):
    """被控端程序连上并认证成功后调用。返回 (ok, msg)"""
    if not feature_on():
        return False, "远程协助功能未开启，请联系管理员"
    cid = id(conn)
    info = {
        "conn_id": cid,
        "conn": conn,
        "user_id": user["id"],
        "user_name": user.get("display_name", ""),
        "username": user.get("username", ""),
        "dept": user.get("dept", ""),
        "device": device or "未知设备",
        "note": note or "",
        "ip": getattr(conn, "ip", "") or "",
        "registered_at": util.now_ts(),
        "screen_w": 0,
        "screen_h": 0,
        "monitors": 1,
    }
    with _lock:
        _agents[cid] = info
        _agent_by_user[user["id"]] = cid
    broadcast_agents()
    db.log_action(user["id"], info["user_name"], "被控端上线", "remote",
                  "%s（%s）" % (info["device"], info["ip"]), info["ip"])
    return True, "已注册为可被协助端"


def unregister_agent(conn):
    """被控端断开（含异常断线）时调用"""
    cid = id(conn)
    with _lock:
        info = _agents.pop(cid, None)
        if not info:
            return None
        uid = info["user_id"]
        if _agent_by_user.get(uid) == cid:
            _agent_by_user.pop(uid, None)
    # 该被控端离线，其进行中的会话必须终止
    sid = _conn_session.get(cid)
    if sid:
        end_session(sid, "被控端已断开", by="system")
    broadcast_agents()
    try:
        db.log_action(info["user_id"], info["user_name"], "被控端离线", "remote",
                      info["device"], info["ip"])
    except Exception:
        pass
    return info


def set_agent_screen(conn, w, h, monitors=1):
    """被控端上报实际屏幕分辨率，供管理员端换算坐标"""
    cid = id(conn)
    with _lock:
        info = _agents.get(cid)
        if not info:
            return False
        info["screen_w"] = int(w or 0)
        info["screen_h"] = int(h or 0)
        info["monitors"] = int(monitors or 1)
    return True


def agent_list():
    """在线被控端列表（供管理员端展示）"""
    with _lock:
        rows = list(_agents.values())
    out = []
    busy_users = {s["agent_user_id"] for s in _sessions.values() if s["status"] == "active"}
    pending_users = {s["agent_user_id"] for s in _sessions.values() if s["status"] == "pending"}
    for a in rows:
        out.append({
            "user_id": a["user_id"], "user_name": a["user_name"], "username": a["username"],
            "dept": a["dept"], "device": a["device"], "ip": a["ip"], "note": a["note"],
            "screen": ("%dx%d" % (a["screen_w"], a["screen_h"])) if a["screen_w"] else "未上报",
            "online_since": a["registered_at"],
            "state": ("busy" if a["user_id"] in busy_users
                      else ("pending" if a["user_id"] in pending_users else "idle")),
        })
    return out


def broadcast_agents():
    """向所有具备查看权限的在线管理员推送被控端列表"""
    from core import realtime, security
    realtime.broadcast({"t": "remote_agents", "d": {"list": agent_list()}},
                       only_perm=lambda u: bool(u) and security.has_perm(u, "remote.view"))


def get_agent_conn(user_id):
    with _lock:
        cid = _agent_by_user.get(user_id)
        info = _agents.get(cid) if cid else None
        c = info["conn"] if info else None
    return c if (c and getattr(c, "alive", False)) else None


# ---------------- 协助会话 ----------------
def _send(conn, obj):
    try:
        return conn.send(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return False


def request_session(admin_user, target_user_id, conn):
    """管理员发起协助请求。返回 (ok, msg, session_id)"""
    from core import security
    if not feature_on():
        return False, "远程协助功能未开启", ""
    if not security.has_perm(admin_user, "remote.request"):
        return False, "无权发起远程协助", ""
    if int(target_user_id) == int(admin_user["id"]):
        return False, "不能对自己发起远程协助", ""
    target = db.one("SELECT id, display_name, dept, status FROM users WHERE id=?",
                    (int(target_user_id),))
    if not target:
        return False, "目标用户不存在", ""
    agent_conn = get_agent_conn(int(target_user_id))
    if not agent_conn:
        return False, "对方未运行被控端程序或已离线，无法协助", ""
    with _lock:
        for s in _sessions.values():
            if s["status"] in ("pending", "active") and s["agent_user_id"] == int(target_user_id):
                return False, "对方当前已有进行中或待确认的协助会话", ""
            if s["status"] == "active" and s["admin_user_id"] == int(admin_user["id"]):
                return False, "您已有正在进行的协助会话，请先结束", ""
        sid = _next_sid()
        max_min = int(cfg_val("remote_max_minutes", 60))
        s = {
            "id": sid,
            "admin_user_id": int(admin_user["id"]),
            "admin_name": admin_user.get("display_name", ""),
            "admin_conn_id": id(conn),
            "admin_conn": conn,
            "agent_user_id": int(target_user_id),
            "agent_name": target["display_name"],
            "agent_dept": target.get("dept", ""),
            "agent_conn": agent_conn,
            "status": "pending",
            "requested_at": util.now_ts(),
            "started_at": 0,
            "ended_at": 0,
            "expires_at": 0,
            "max_minutes": max_min,
            "end_reason": "",
            "input_count": 0,
            "control": bool(security.has_perm(admin_user, "remote.control")),
        }
        _sessions[sid] = s
        _conn_session[id(conn)] = sid
        _conn_session[id(agent_conn)] = sid
    # 向被控端弹窗请求确认（确认前绝不转发任何画面）
    _send(agent_conn, {"t": "remote_ask", "d": {
        "session_id": sid,
        "admin_name": s["admin_name"],
        "admin_dept": admin_user.get("dept", ""),
        "max_minutes": max_min,
        "can_control": s["control"],
        "msg": ("%s 请求查看并协助您的电脑屏幕%s，时长上限 %d 分钟。"
                "拒绝或随时断开都不会有任何影响。"
                % (s["admin_name"], "（含鼠标键盘操作）" if s["control"] else "（仅查看）", max_min)),
    }})
    try:
        db.log_action(admin_user["id"], s["admin_name"], "发起远程协助", "remote",
                      "目标：%s（user_id=%s）" % (s["agent_name"], target_user_id), "")
    except Exception:
        pass
    return True, "已发送协助请求，等待对方在本机确认", sid


def accept_session(agent_conn, sid, user_id):
    """被控端用户点击同意"""
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return False, "会话不存在或已结束"
        if s["agent_user_id"] != int(user_id):
            return False, "会话不匹配"
        if s["status"] != "pending":
            return False, "会话状态已变更"
        max_min = s["max_minutes"]
        s["status"] = "active"
        s["started_at"] = util.now_ts()
        s["expires_at"] = s["started_at"] + max_min * 60
        admin_conn = s["admin_conn"]
        payload = {"t": "remote_start", "d": {
            "session_id": sid, "peer_name": s["admin_name"], "role": "agent",
            "fps": int(cfg_val("remote_fps", 6)), "quality": int(cfg_val("remote_quality", 55)),
            "max_minutes": max_min, "expires_at": s["expires_at"],
        }}
        agent_payload = payload
        admin_d = {"t": "remote_start", "d": {
            "session_id": sid, "peer_name": s["agent_name"], "role": "admin",
            "agent_dept": s["agent_dept"], "control": s["control"],
            "screen_w": 0, "screen_h": 0,
            "max_minutes": max_min, "expires_at": s["expires_at"],
        }}
        info = _agents.get(id(agent_conn))
        if info:
            admin_d["d"]["screen_w"] = info.get("screen_w", 0)
            admin_d["d"]["screen_h"] = info.get("screen_h", 0)
    _send(agent_conn, agent_payload)
    _send(admin_conn, admin_d)
    _send(agent_conn, {"t": "remote_notice", "d": {
        "text": "协助已开始：%s 正在查看您的屏幕%s" % (s["admin_name"], "并可操作鼠标键盘" if s["control"] else "")}})
    try:
        db.log_action(s["agent_user_id"], s["agent_name"], "同意远程协助", "remote",
                      "发起人：%s（session=%s）" % (s["admin_name"], sid), "")
    except Exception:
        pass
    broadcast_agents()
    return True, "已开始协助"


def reject_session(agent_conn, sid, user_id, reason=""):
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return False, "会话不存在或已结束"
        if s["agent_user_id"] != int(user_id):
            return False, "会话不匹配"
        if s["status"] != "pending":
            return False, "会话状态已变更"
        s["status"] = "rejected"
        s["ended_at"] = util.now_ts()
        s["end_reason"] = reason or "对方拒绝了协助请求"
        admin_conn = s["admin_conn"]
        admin_name = s["admin_name"]
        agent_name = s["agent_name"]
        admin_uid = s["admin_user_id"]
        _conn_session.pop(s["admin_conn_id"], None)
        _conn_session.pop(id(agent_conn), None)
        _sessions.pop(sid, None)
    _send(admin_conn, {"t": "remote_end", "d": {"session_id": sid, "reason": s["end_reason"],
                                                "by_peer": True}})
    try:
        db.log_action(admin_uid, admin_name, "远程协助被拒绝", "remote",
                      "对方：%s%s" % (agent_name, ("，理由：%s" % reason) if reason else ""), "")
    except Exception:
        pass
    broadcast_agents()
    return True, "已拒绝"


def end_session(sid, reason="", by="admin", force=False):
    """结束会话。by: admin/agent/system/timeout"""
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return False
        if s["status"] in ("ended", "rejected"):
            _sessions.pop(sid, None)
            return False
        s["status"] = "ended"
        s["ended_at"] = util.now_ts()
        s["end_reason"] = reason or "会话已结束"
        admin_conn, agent_conn = s["admin_conn"], s["agent_conn"]
        agent_name, admin_name = s["agent_name"], s["admin_name"]
        agent_uid, admin_uid = s["agent_user_id"], s["admin_user_id"]
        started, input_count = s["started_at"], s["input_count"]
        _conn_session.pop(s["admin_conn_id"], None)
        try:
            _conn_session.pop(id(agent_conn), None)
        except Exception:
            pass
        _sessions.pop(sid, None)
    d_admin = {"t": "remote_end", "d": {"session_id": sid, "reason": s["end_reason"],
                                        "by_peer": by != "admin", "duration": (s["ended_at"] - started) if started else 0}}
    d_agent = {"t": "remote_end", "d": {"session_id": sid, "reason": s["end_reason"],
                                        "by_peer": by != "agent", "duration": (s["ended_at"] - started) if started else 0}}
    _send(admin_conn, d_admin)
    _send(agent_conn, d_agent)
    try:
        dur = int((s["ended_at"] - started) / 60) if started else 0
        detail = "对象：%s，时长 %d 分钟，输入指令 %d 次，原因：%s" % (
            agent_name if by != "agent" else admin_name, dur, input_count, s["end_reason"])
        actor_id, actor = (admin_uid, admin_name) if by != "agent" else (agent_uid, agent_name)
        action = {"timeout": "远程协助超时结束", "system": "远程协助中断"}.get(by, "结束远程协助")
        db.log_action(actor_id, actor, action, "remote", detail, "")
    except Exception:
        pass
    broadcast_agents()
    return True


def forward_frame(agent_conn, data):
    """被控端上行二进制画面帧 → 转发给管理员"""
    cid = id(agent_conn)
    with _lock:
        sid = _conn_session.get(cid)
        s = _sessions.get(sid) if sid else None
        admin_conn = s["admin_conn"] if s and s["status"] == "active" else None
    if admin_conn is None:
        return False
    try:
        return bool(admin_conn.send_bin(data))
    except Exception:
        return False


def forward_input(admin_conn, d):
    """管理员上行输入指令 → 转发给被控端。返回 (ok, msg)"""
    from core import security
    cid = id(admin_conn)
    with _lock:
        sid = _conn_session.get(cid)
        s = _sessions.get(sid) if sid else None
        if not s or s["status"] != "active":
            return False, "会话未在进行中"
        if not s["control"]:
            return False, "您没有远程操作权限，仅可查看画面"
        user = getattr(admin_conn, "user", None)
        if not user or not security.has_perm(user, "remote.control"):
            return False, "权限已变更，无法继续操作"
        agent_conn = s["agent_conn"]
        s["input_count"] += 1
        n = s["input_count"]
    d["session_id"] = sid
    ok = _send(agent_conn, {"t": "remote_input", "d": d})
    # 每 50 次输入补一条审计，既留痕又不淹没日志
    if ok and n % 50 == 1:
        try:
            db.log_action(s["admin_user_id"], s["admin_name"], "远程操作", "remote",
                          "对象：%s，累计输入 %d 次（session=%s）" % (s["agent_name"], n, sid), "")
        except Exception:
            pass
    return ok, ""


def active_sessions():
    with _lock:
        rows = [dict(s) for s in _sessions.values()]
    out = []
    for s in rows:
        s.pop("admin_conn", None)
        s.pop("agent_conn", None)
        out.append(s)
    return out


def session_of_conn(conn):
    with _lock:
        sid = _conn_session.get(id(conn))
        return _sessions.get(sid) if sid else None


def tick():
    """后台巡检：超时自动断开（由 realtime 心跳循环周期调用）"""
    now = util.now_ts()
    with _lock:
        expired = [s["id"] for s in _sessions.values()
                   if s["status"] == "active" and s["expires_at"] and s["expires_at"] < now]
        stale = [s["id"] for s in _sessions.values()
                 if s["status"] == "pending" and now - s["requested_at"] > 120]
    for sid in expired:
        end_session(sid, "已达单次协助时长上限，自动断开", by="timeout")
    for sid in stale:
        end_session(sid, "协助请求超时未被确认", by="system")
