# -*- coding: utf-8 -*-
# Copyright (c) 2026 夜屿
# Yunliao (云聊) is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
"""实时通信中心：连接管理、房间订阅、广播、在线状态"""
import json
import threading
import time

from core import db, util

_conns_lock = threading.RLock()
_conns = {}          # conn_id -> WSConn
_by_user = {}        # user_id -> set(conn_id)
_by_room = {}        # room_id -> set(conn_id)

_online_state = {}   # user_id -> {'status': 'online'|'busy'|'away', 'ts': ts}


def register(conn):
    with _conns_lock:
        _conns[id(conn)] = conn


def unregister(conn):
    uid = conn.user.get("id") if conn.user else None
    # 被控端断开：注销注册并结束其进行中的协助会话
    try:
        from core import remote
        remote.unregister_agent(conn)
    except Exception:
        pass
    # 游戏房间成员断开：离开房间并通知对端
    try:
        from core import gameroom
        gameroom.on_disconnect(conn)
    except Exception:
        pass
    with _conns_lock:
        _conns.pop(id(conn), None)
        if uid:
            s = _by_user.get(uid)
            if s:
                s.discard(id(conn))
                if not s:
                    _by_user.pop(uid, None)
                    _online_state.pop(uid, None)
                    if not (conn.user or {}).get("patrol"):
                        broadcast_presence(uid, "offline")
        for rid in list(conn.rooms):
            s = _by_room.get(rid)
            if s:
                s.discard(id(conn))
                if not s:
                    _by_room.pop(rid, None)
    if uid:
        db.execute("UPDATE users SET last_seen=? WHERE id=?", (util.now_ts(), uid))


def bind_user(conn, user):
    conn.user = user
    uid = user["id"]
    with _conns_lock:
        _conns[id(conn)] = conn
        _by_user.setdefault(uid, set()).add(id(conn))
        _online_state[uid] = {"status": "online", "ts": util.now_ts()}
    # 隐身巡查账号：上线状态不对普通用户广播
    if not user.get("patrol"):
        broadcast_presence(uid, "online")


def subscribe(conn, room_id):
    with _conns_lock:
        conn.rooms.add(room_id)
        _by_room.setdefault(room_id, set()).add(id(conn))


def unsubscribe(conn, room_id):
    with _conns_lock:
        conn.rooms.discard(room_id)
        s = _by_room.get(room_id)
        if s:
            s.discard(id(conn))


def is_online(user_id):
    with _conns_lock:
        return user_id in _by_user


def online_ids():
    with _conns_lock:
        return list(_by_user.keys())


def online_count():
    return len(online_ids())


def _send(conn, obj):
    try:
        return conn.send(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return False


def send_to_user(user_id, obj):
    with _conns_lock:
        ids = list(_by_user.get(user_id, set()))
        conns = [_conns.get(i) for i in ids]
    ok = False
    for c in conns:
        if c and c.alive:
            ok = _send(c, obj) or ok
    return ok


def send_to_room(room_id, obj, exclude=None):
    with _conns_lock:
        ids = list(_by_room.get(room_id, set()))
        conns = [_conns.get(i) for i in ids]
    for c in conns:
        if c and c.alive and c is not exclude:
            _send(c, obj)


def send_and_close(user_id, obj, reason="banned"):
    """向用户所有连接发送提示后关闭（封禁踢下线）"""
    with _conns_lock:
        ids = list(_by_user.get(user_id, set()))
        conns = [_conns.get(i) for i in ids]
    for c in conns:
        if not c:
            continue
        _send(c, obj)
        try:
            c.close(4001, reason)
        except Exception:
            c.alive = False
    return len([c for c in conns if c])


def close_by_tokens(tokens, obj=None, reason="kicked"):
    """按会话 token 精确踢下线（单账号单 IP 模式只踢其它 IP 的连接）"""
    if not tokens:
        return 0
    toks = set(tokens)
    with _conns_lock:
        targets = [c for c in _conns.values() if c and getattr(c, "token", None) in toks]
    for c in targets:
        if obj:
            _send(c, obj)
        try:
            c.close(4001, reason)
        except Exception:
            c.alive = False
    return len(targets)


def close_by_ip(ip, obj=None, reason="ip_blacklist"):
    """把来源 IP 命中的全部在线连接（含未认证的）提示后踢下线"""
    with _conns_lock:
        targets = [c for c in _conns.values() if c and getattr(c, "ip", None) == ip]
    for c in targets:
        if obj:
            _send(c, obj)
        try:
            c.close(4003, reason)
        except Exception:
            c.alive = False
        unregister(c)
    return len(targets)


def broadcast(obj, exclude=None, only_perm=None):
    with _conns_lock:
        conns = list(_conns.values())
    for c in conns:
        if c and c.alive and c is not exclude:
            if only_perm and not (c.user and only_perm(c.user)):
                continue
            _send(c, obj)


def broadcast_presence(user_id, status):
    broadcast({"t": "presence", "d": {"user_id": user_id, "status": status}})


def heartbeat_loop(interval=30):
    """后台心跳 + 死连接清理"""
    while True:
        time.sleep(interval)
        try:
            with _conns_lock:
                conns = list(_conns.values())
            for c in conns:
                if not c.alive:
                    unregister(c)
                    continue
                try:
                    c.ping()
                except Exception:
                    c.alive = False
                    unregister(c)
        except Exception:
            pass
        # 远程协助会话巡检：超时/未确认的会话自动结束（独立容错，不影响心跳）
        try:
            from core import remote
            remote.tick()
        except Exception:
            pass
        # 小游戏房间巡检：清理超时/空房间
        try:
            from core import gameroom
            gameroom.tick()
        except Exception:
            pass
