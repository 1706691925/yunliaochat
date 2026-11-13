# -*- coding: utf-8 -*-
"""小游戏房间中继：冰火人联机的房间管理、消息转发与成绩落库。

设计为「轻量中继 + host 仲裁」：服务端不理解游戏逻辑，只负责
建房/加入/配对、把双方消息转发给对方、维护 host 权威字段的仲裁权
（关卡进度、宝石权威计数只接受 host 下发），以及断线通知与超时清理。
"""
import json
import random
import string
import threading
import time

from core import db, util

_lock = threading.RLock()
_rooms = {}          # room_code -> room dict
_by_conn = {}        # conn_id -> room_code

GAMES = {
    "fireboy": {"name": "森林冰火人", "max_players": 2, "online": True},
    "chess": {"name": "中国象棋", "max_players": 2, "online": True},
}

# 仅 host 可下发的权威操作，guest 发送一律拒绝（按游戏区分；象棋由双方自行走子，无 host 仲裁项）
HOST_ONLY = {
    "fireboy": ("level", "restart", "sync", "start"),
    "chess": (),
}
# 转发给对端的操作
FORWARD = ("move", "gem", "door", "chat", "level", "restart", "sync", "key", "start", "ready",
           "new", "resign", "draw", "draw_accept", "draw_reject", "state")

ROOM_TTL = 30 * 60       # 空房间存活时长
PAIR_WAIT = 30 * 60      # 等待同伴加入的最长时长


def enabled(game):
    """游戏是否被管理员开启：总开关 + 单游戏开关"""
    try:
        from core import config
        if not config.CFG.get("enable_games", False):
            return False
        return bool(config.CFG.get("game_" + game, False))
    except Exception:
        return False


def user_games(user):
    """该用户被授权的小游戏集合（users.games 逗号分隔；超级管理员恒有全部，默认全关按人开）"""
    if not user:
        return set()
    if user.get("role") == "superadmin":
        return set(GAMES.keys())
    raw = str(user.get("games") or "")
    return {g.strip() for g in raw.split(",") if g.strip() in GAMES}


def can_play(user, game):
    """该用户是否有权玩指定小游戏（与全局开关无关，仅个人授权判定）"""
    return bool(user and game in GAMES and game in user_games(user))


def enabled_for(user, game):
    """全局开启 且 该用户被授权，才对该用户可用"""
    return enabled(game) and can_play(user, game)


def game_catalog(user):
    """前端可展示的游戏清单（按当前用户过滤：仅返回该用户可玩的游戏；不暴露未授权入口）"""
    try:
        from core import config
        show_nav = bool(config.CFG.get("game_show_nav", False))
    except Exception:
        show_nav = False
    out = []
    for key, meta in GAMES.items():
        global_on = enabled(key)
        out.append({
            "key": key, "name": meta["name"],
            "global_on": global_on,                    # 系统是否全局开启
            "enabled": global_on and can_play(user, key),   # 对该用户是否可用
            "online": meta["online"], "max_players": meta["max_players"],
            "show_nav": show_nav,
        })
    return out


def _new_code():
    for _ in range(20):
        code = "".join(random.choice(string.ascii_uppercase + string.digits)
                       for _ in range(4))
        if code not in _rooms:
            return code
    return "R%d" % int(time.time() * 1000) % 100000


def _send(conn, ev, d):
    """统一封装下行消息：{"t":"game","d":{...,"ev":ev}}"""
    if not conn or not getattr(conn, "alive", False):
        return False
    try:
        msg = {"t": "game", "d": dict(d)}
        msg["d"].setdefault("g", "fireboy")
        msg["d"]["ev"] = ev
        return conn.send(json.dumps(msg, ensure_ascii=False))
    except Exception:
        return False


def _err(conn, msg, game="fireboy"):
    return _send(conn, "error", {"g": game, "msg": msg})


def _peer_conn(room, exclude_user_id):
    """取房间内另一名玩家的连接"""
    for p in room["players"].values():
        if p["user_id"] != exclude_user_id:
            c = p["conn"]
            if c and getattr(c, "alive", False):
                return c
    return None


def _room_view(room):
    return {
        "room": room["code"],
        "players": [{"user_id": p["user_id"], "name": p["name"], "side": p["side"],
                     "role": ("host" if p["user_id"] == room["host_id"] else "guest"),
                     "online": bool(p["conn"] and getattr(p["conn"], "alive", False))}
                    for p in room["players"].values()],
        "host_id": room["host_id"],
        "created_at": room["created_at"],
    }


def handle(conn, d, user):
    """WS 消息 t == 'game' 的入口"""
    game = str(d.get("g") or "fireboy")
    if game not in GAMES:
        return _err(conn, "未知的游戏类型", game)
    if not enabled_for(user, game):
        return _err(conn, "您没有该小游戏的访问权限（未授权），请联系管理员开启", game)
    op = str(d.get("op") or "")
    try:
        if op == "create":
            return create_room(conn, user, game)
        if op == "join":
            return join_room(conn, user, game, str(d.get("room") or ""), spec=bool(d.get("spec")))
        if op == "leave":
            return leave(conn, user, "主动离开房间")
        if op == "score":
            return save_score(user, game, d)
        if op in FORWARD:
            return relay(conn, user, game, op, d)
        return _err(conn, "未知的操作：%s" % op, game)
    except Exception as e:
        return _err(conn, "服务器处理失败：%s" % e, game)


def create_room(conn, user, game):
    cid = id(conn)
    with _lock:
        if cid in _by_conn:
            return _err(conn, "您已在房间中，请先退出", game)
        code = _new_code()
        room = {
            "code": code, "game": game, "host_id": user["id"],
            "players": {user["id"]: {"user_id": user["id"], "name": user.get("display_name", ""),
                                     "side": "fire", "conn": conn, "joined_at": util.now_ts()}},
            "created_at": util.now_ts(), "started_at": 0, "last_active": util.now_ts(),
            "gems": {"r": 0, "b": 0}, "deaths": 0, "level": 1,
            "doors": {"fire": False, "ice": False}, "chess_state": None,
            "spectators": {},
        }
        _rooms[code] = room
        _by_conn[cid] = code
    _send(conn, "room", {"g": game, "room": code, "role": "host", "you": "fire",
                         "peer": None, "players": _room_view(room)["players"]})
    return True


def join_room(conn, user, game, code, spec=False):
    code = (code or "").strip().upper()
    cid = id(conn)
    if not code:
        return _err(conn, "请输入房间号", game)
    is_admin = "admin.access" in (user.get("perms") or [])
    with _lock:
        if cid in _by_conn:
            return _err(conn, "您已在房间中，请先退出", game)
        room = _rooms.get(code)
        if not room or room["game"] != game:
            return _err(conn, "房间不存在或已解散，请核对房间号", game)
        if spec:
            # 管理员无痕观战：不占席位、不广播 peer_join、不出现在成员列表，仅同步局面
            if not is_admin:
                return _err(conn, "无观战权限", game)
            room.setdefault("spectators", {})[user["id"]] = {
                "user_id": user["id"], "name": user.get("display_name", ""),
                "conn": conn, "joined_at": util.now_ts()}
            _by_conn[cid] = code
            room["last_active"] = util.now_ts()
            players_view = _room_view(room)["players"]
            snap = {"level": room["level"], "gems": dict(room["gems"]),
                    "doors": dict(room["doors"]), "deaths": room["deaths"],
                    "elapsed": round(util.now_ts() - (room["started_at"] or room["created_at"]), 1)}
            if game == "chess" and room.get("chess_state"):
                try:
                    snap["chess_state"] = json.loads(room["chess_state"])
                except Exception:
                    pass
            _send(conn, "room", {"g": game, "room": code, "role": "spectator", "you": None,
                                 "peer": None, "players": players_view})
            snap["g"] = game
            _send(conn, "sync", snap)
            return True
        if len(room["players"]) >= GAMES[game]["max_players"]:
            return _err(conn, "房间已满", game)
        if user["id"] in room["players"]:
            # 断线重连：恢复原席位
            p = room["players"][user["id"]]
            p["conn"] = conn
            room["last_active"] = util.now_ts()
            _by_conn[cid] = code
            side = p["side"]
            role = "host" if room["host_id"] == user["id"] else "guest"
            snapshot = {"g": game, "room": code, "role": role, "you": side,
                        "peer": None, "players": _room_view(room)["players"]}
        else:
            side = "ice" if "fire" in [x["side"] for x in room["players"].values()] else "fire"
            room["players"][user["id"]] = {
                "user_id": user["id"], "name": user.get("display_name", ""),
                "side": side, "conn": conn, "joined_at": util.now_ts()}
            _by_conn[cid] = code
            room["started_at"] = util.now_ts()
            room["last_active"] = util.now_ts()
            role = "host" if room["host_id"] == user["id"] else "guest"
            snapshot = {"g": game, "room": code, "role": role, "you": side,
                        "peer": {"name": room["players"][room["host_id"]]["name"],
                                 "you": room["players"][room["host_id"]]["side"]},
                        "players": _room_view(room)["players"]}
        # 通知双方配对状态（连接引用与快照都在锁内取好，锁外只做发送）
        my_name = user.get("display_name", "")
        others = [(p.get("conn"), p["side"], p["name"]) for uid, p in room["players"].items()
                  if uid != user["id"]]
        players_view = _room_view(room)["players"]
        snap = {"level": room["level"], "gems": dict(room["gems"]),
                "doors": dict(room["doors"]), "deaths": room["deaths"],
                "elapsed": round(util.now_ts() - (room["started_at"] or room["created_at"]), 1)}
        if game == "chess" and room.get("chess_state"):
            try:
                snap["chess_state"] = json.loads(room["chess_state"])
            except Exception:
                pass
    _send(conn, "room", snapshot)
    for oconn, oside, oname in others:
        if oconn and getattr(oconn, "alive", False):
            _send(oconn, "peer_join", {"g": game, "peer": {"name": my_name, "you": side},
                                       "players": players_view})
    # guest 中途加入或重连后，用 host 维护的权威快照对齐本地状态
    if role == "guest":
        snap["g"] = game
        _send(conn, "sync", snap)
    return True


def relay(conn, user, game, op, d):
    cid = id(conn)
    with _lock:
        code = _by_conn.get(cid)
        if not code:
            return _err(conn, "您不在任何房间中", game)
        room = _rooms.get(code)
        if not room:
            _by_conn.pop(cid, None)
            return _err(conn, "房间已解散", game)
        room["last_active"] = util.now_ts()
        is_host = room["host_id"] == user["id"]
        if op in HOST_ONLY.get(game, ()) and not is_host:
            return _err(conn, "只有房主可以执行该操作", game)
        me = room["players"].get(user["id"])
        if not me:
            # 观战者（管理员无痕）：只可读取状态，不可参与操作
            if room.get("spectators", {}).get(user["id"]):
                if op == "state":
                    return True
                return _err(conn, "观战模式不可操作", game)
            return _err(conn, "您不在该房间中", game)
        side = me["side"]
        # 维护权威状态：宝石计数、门状态、关卡、死亡数
        if op == "gem":
            g = str(d.get("side") or "")
            if g in ("r", "b"):
                # 只允许拾取与自己角色匹配的宝石
                if (side == "fire" and g == "r") or (side == "ice" and g == "b"):
                    room["gems"][g] = room["gems"].get(g, 0) + 1
        elif op == "door":
            room["doors"][side] = bool(d.get("on"))
        elif op == "level":
            try:
                room["level"] = max(1, int(d.get("n") or 1))
                room["doors"] = {"fire": False, "ice": False}
            except Exception:
                pass
        elif op == "sync":
            try:
                room["level"] = max(1, int(d.get("level") or room["level"]))
                room["deaths"] = int(d.get("deaths") or room["deaths"])
                g = d.get("gems") or {}
                if isinstance(g, dict):
                    room["gems"] = {"r": int(g.get("r", 0)), "b": int(g.get("b", 0))}
            except Exception:
                pass
        peer = _peer_conn(room, user["id"])
        out = {"g": game, "from": side, "by": ("host" if is_host else "guest")}
        for k in ("x", "y", "vx", "vy", "ground", "side", "on", "n", "text",
                  "level", "gems", "doors", "deaths", "elapsed", "k", "down", "ms", "v"):
            if k in d:
                out[k] = d[k]
        # 象棋：透传走子与状态字段，服务端不解析棋规，仅做转发
        if game == "chess":
            for k in ("fr", "fc", "tr", "tc", "notation", "state", "seconds"):
                if k in d:
                    out[k] = d[k]
            if op == "state" and is_host:
                try:
                    room["chess_state"] = json.dumps(d.get("state"), ensure_ascii=False)
                except Exception:
                    pass
            if op == "new":
                room["chess_state"] = None
        if op == "gem":
            out["total_r"] = room["gems"].get("r", 0)
            out["total_b"] = room["gems"].get("b", 0)
        if op == "chat":
            out["name"] = me["name"]
            out["text"] = str(d.get("text") or "")[:200]
    if peer is None:
        # 单机/无对端也允许上报，避免客户端报错刷屏
        return True
    _send(peer, op, out)
    # 管理员无痕观战：实时同步局面（不回复观战者，仅推送）
    for suid, sp in room.get("spectators", {}).items():
        sconn = sp.get("conn")
        if sconn and sconn is not conn and getattr(sconn, "alive", False):
            try:
                _send(sconn, op, out)
            except Exception:
                pass
    return True


def leave(conn, user, reason=""):
    cid = id(conn)
    with _lock:
        code = _by_conn.pop(cid, None)
        if not code:
            return False
        room = _rooms.get(code)
        if not room:
            return False
        me = room["players"].pop(user["id"], None)
        sp = room.get("spectators", {}).pop(user["id"], None) if me is None else None
        peer = None
        for p in room["players"].values():
            peer = p
            break
        empty = not room["players"]
        if empty:
            _rooms.pop(code, None)
            # 房间解散：通知观战者
            for suid, sobj in (room.get("spectators", {}) or {}).items():
                sconn = sobj.get("conn")
                if sconn and getattr(sconn, "alive", False):
                    try:
                        _send(sconn, "closed", {"g": room["game"], "reason": "房间已解散"})
                    except Exception:
                        pass
            room["spectators"] = {}
        elif room["host_id"] == user["id"]:
            # 房主离开：若仍有同伴则移交 host，否则解散
            if peer:
                room["host_id"] = peer["user_id"]
        remaining = _room_view(room)["players"] if not empty else []
        # 锁内取好锁外要用的字段，避免读取被并发改写的值
        game = room["game"]
        peer_conn = peer.get("conn") if peer else None
        peer_is_host = bool(peer) and room["host_id"] == peer["user_id"]
    if peer_conn and getattr(peer_conn, "alive", False):
        _send(peer_conn, "peer_leave", {"g": game, "reason": reason or "对方已离开房间",
                                        "new_host": peer_is_host, "players": remaining})
    if (me or sp) and conn and getattr(conn, "alive", False):
        _send(conn, "closed", {"g": game, "reason": reason or "已离开房间"})
    return True


def on_disconnect(conn):
    """连接断开时调用"""
    user = getattr(conn, "user", None) or {}
    if not user:
        with _lock:
            _by_conn.pop(id(conn), None)
        return
    leave(conn, user, "对方连接已断开")
    with _lock:
        _by_conn.pop(id(conn), None)


def save_score(user, game, d, mode="single"):
    """成绩落库（联机经 WS 上报，单机经 HTTP 上报，最终都走这里）"""
    try:
        score = int(d.get("score") or 0)
        level = int(d.get("level") or 0)
    except Exception:
        score, level = 0, 0
    result = str(d.get("result") or "")[:10]
    if result not in ("win", "lose", "draw"):
        result = "lose"
    score = max(0, min(score, 999999))
    mode = str(d.get("mode") or mode)[:10] or "single"
    db.insert("game_scores", {
        "user_id": user["id"], "game": game, "score": score, "level": level,
        "result": result, "mode": mode, "created_at": util.now_ts(),
    })
    return {"saved": True, "score": score}


def leaderboard(game, limit=10):
    rows = db.query(
        "SELECT s.score, s.level, s.result, s.mode, s.created_at, u.display_name, u.dept "
        "FROM game_scores s JOIN users u ON u.id=s.user_id "
        "WHERE s.game=? ORDER BY s.score DESC, s.created_at ASC LIMIT ?", (game, int(limit)))
    for r in rows:
        r["time_str"] = util.ts_to_str(r["created_at"])
    best = db.query(
        "SELECT u.id AS user_id, u.display_name AS name, MAX(s.score) AS best "
        "FROM game_scores s JOIN users u ON u.id=s.user_id WHERE s.game=? "
        "GROUP BY u.id ORDER BY best DESC LIMIT ?", (game, int(limit)))
    return {"recent": rows, "best": best, "total": db.count("game_scores", "game=?", (game,))}


def room_stats():
    with _lock:
        return {"rooms": len(_rooms), "players": sum(len(r["players"]) for r in _rooms.values()),
                "list": [_room_view(r) for r in _rooms.values()]}


def tick():
    """清理超时房间（由 realtime 心跳循环周期调用）"""
    now = util.now_ts()
    with _lock:
        dead = []
        for code, room in _rooms.items():
            alive = any(p["conn"] and getattr(p["conn"], "alive", False)
                        for p in room["players"].values())
            idle = now - room["last_active"]
            if not alive and idle > 60:
                dead.append(code)
            elif alive and idle > ROOM_TTL:
                dead.append(code)
            elif not alive and room["created_at"] and now - room["created_at"] > PAIR_WAIT:
                dead.append(code)
        for code in dead:
            room = _rooms.pop(code, None)
            if not room:
                continue
            for uid, p in list(room["players"].items()):
                _by_conn.pop(id(p["conn"]), None)
