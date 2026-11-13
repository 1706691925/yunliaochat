# -*- coding: utf-8 -*-
"""新功能验证脚本：小游戏路由/成绩、权限矩阵、数据库管理、静态游戏页、游戏联机 WS、远程协助 WS
用法：python tools/verify_ops.py
"""
import base64
import json
import os
import shutil
import socket
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, http  # noqa: E402
from app import Application    # noqa: E402
from tools.e2e_test import WSClient  # noqa: E402

PORT = 8922
DATA = os.path.join(os.environ.get("TEMP", "/tmp"), "lcdata_verify_ops")
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL, SKIP = [], [], []


def http_json(method, path, data=None, token=None, form=None, files=None, base=None):
    """e2e 同款请求工具，BASE 使用本脚本端口"""
    url = (base or BASE) + urllib.parse.quote(path, safe="/?&=,%2B")
    headers = {}
    if token:
        headers["X-Token"] = token
    body = None
    if files:
        boundary = "----verifybound"
        buf = b""
        for k, v in (form or {}).items():
            buf += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode()
        for fn, content in files.items():
            buf += ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n" % (boundary, fn)).encode()
            buf += content + b"\r\n"
        buf += ("--%s--\r\n" % boundary).encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        body = buf
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        try:
            return json.loads(raw.decode("utf-8")), dict(r.headers)
        except Exception:
            return {"raw": raw}, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw.decode("utf-8")), dict(e.headers)
        except Exception:
            return {"ok": False, "msg": raw[:200]}, {}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else "  FAIL ") + name + (("  -> " + str(extra)) if extra and not cond else ""))


def skip(name, why):
    """资源未随仓库交付时跳过，而不是记为失败。
    小游戏静态资源（fireboy/chess/ruffle）体积大且涉及第三方版权，未纳入仓库，
    部署方按需放入 <resource>/games/ 后这些用例才会真正执行。"""
    SKIP.append(name)
    print("  SKIP  " + name + "  (跳过: " + why + ")")


def admin_token():
    with open(os.path.join(DATA, "初始管理员密码.txt"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("密码："):
                return line.split("：", 1)[1].strip()
    return ""


def main():
    if os.path.exists(DATA):
        shutil.rmtree(DATA)
    app = Application(data_dir=DATA)
    app.bootstrap()
    app.cfg.set("port", PORT)
    srv = http.Server(("127.0.0.1", PORT), http.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)

    pwd = admin_token()
    tok, _ = http_json("POST", "/api/auth/login", {"username": "admin", "password": pwd})
    check("管理员登录", tok.get("ok"), tok)
    token = tok["data"]["token"]
    demo_pwd = getattr(app, "_demo_pwd", pwd)
    stok, _ = http_json("POST", "/api/auth/login", {"username": "student", "password": demo_pwd})
    check("学员登录", stok.get("ok"), stok)
    student_token = stok["data"]["token"]
    # 解析学员 id（供权限微调 / 远程协助测试使用）
    ul0, _ = http_json("GET", "/api/admin/users?q=student&page=1&page_size=5", token=token)
    STUDENT_ID = 3
    if ul0.get("ok"):
        for x in ul0["data"]["list"]:
            if x.get("username") == "student":
                STUDENT_ID = x["id"]
                break
    print("  [info] 学员 id =", STUDENT_ID)

    print("\n[1] 小游戏静态页与路由")
    # 实际随仓库交付的静态页：象棋（原生 HTML5）与冰火人（Flash 原版 + Ruffle 播放）。
    # 注意没有 fireboy.html —— 前端 ops.js 把 fireboy 映射到 fireboy-flash，
    # 这里按真实文件名断言，不能再写不存在的 fireboy.html。
    gassets = os.path.join(config.resource_dir(), "games")
    has_games = os.path.isdir(gassets)
    for p in ("/games/fireboy-flash.html", "/games/chess.html"):
        if not has_games:
            skip("GET " + p, "games 资源目录不存在")
            continue
        try:
            r = urllib.request.urlopen(BASE + p, timeout=5)
            check("GET " + p, r.status == 200 and len(r.read()) > 1000, r.status)
        except Exception as e:
            check("GET " + p, False, e)
    # 目录穿越防护：无论有没有资源都必须拒绝上级目录
    try:
        urllib.request.urlopen(BASE + "/games/../config.json", timeout=5)
        check("games 目录穿越防护", False, "可访问上级目录")
    except urllib.error.HTTPError as e:
        check("games 目录穿越防护", e.code in (403, 404), e.code)
    except Exception as e:
        check("games 目录穿越防护", False, e)
    # Ruffle 运行时与 wasm MIME
    if not has_games:
        skip("GET /games/ruffle/ruffle.js", "games 资源目录不存在")
    else:
        try:
            r = urllib.request.urlopen(BASE + "/games/ruffle/ruffle.js", timeout=5)
            check("GET /games/ruffle/ruffle.js", r.status == 200 and len(r.read()) > 1000, r.status)
        except Exception as e:
            check("GET /games/ruffle/ruffle.js", False, e)
    try:
        gdir = os.path.join(config.resource_dir(), "games", "ruffle")
        wasm = [f for f in os.listdir(gdir) if f.endswith(".wasm")] if os.path.isdir(gdir) else []
        if wasm:
            req = urllib.request.Request(BASE + "/games/ruffle/" + urllib.parse.quote(wasm[0]),
                                         headers={"Range": "bytes=0-1"})
            r = urllib.request.urlopen(req, timeout=5)
            r.read(8)
            ct = r.headers.get("Content-Type", "")
            check(".wasm MIME 类型", "application/wasm" in ct, ct)
        else:
            skip(".wasm MIME 类型", "Ruffle wasm 不存在")
    except Exception as e:
        check(".wasm MIME 类型", False, e)

    cat, _ = http_json("GET", "/api/games/catalog", token=token)
    check("游戏目录（默认全关）", cat.get("ok") and all(not g["enabled"] for g in cat["data"]["games"]), cat)
    chess_online_flag = cat.get("ok") and any(g.get("key") == "chess" and g.get("online") for g in cat["data"]["games"])
    check("目录标记象棋可联机", chess_online_flag, cat)
    upd, _ = http_json("PUT", "/api/admin/settings", {"settings": {
        "enable_games": True, "game_fireboy": True, "game_chess": True, "game_show_nav": True}},
        token=token)
    check("管理员开启小游戏与显示开关", upd.get("ok"), upd)
    ga, _ = http_json("PUT", "/api/admin/users/%d" % STUDENT_ID,
                      {"games": "fireboy,chess"}, token=token)
    check("学员获得小游戏授权", ga.get("ok"), ga)
    cat2, _ = http_json("GET", "/api/games/catalog", token=token)
    check("目录反映开启状态", cat2.get("ok") and all(g["enabled"] for g in cat2["data"]["games"])
          and cat2["data"]["games"][0]["show_nav"], cat2)
    sc, _ = http_json("POST", "/api/games/score", {"game": "fireboy", "score": 120, "level": 1,
                                                   "result": "win", "mode": "mp"}, token=student_token)
    check("成绩落库 /api/games/score", sc.get("ok") and sc["data"]["saved"], sc)
    lb, _ = http_json("GET", "/api/games/leaderboard?game=fireboy", token=token)
    check("排行榜 /api/games/leaderboard", lb.get("ok") and lb["data"]["total"] >= 1, lb)

    print("\n[2] 权限管理")
    pm, _ = http_json("GET", "/api/admin/permissions", token=token)
    check("权限矩阵读取", pm.get("ok") and len(pm["data"]["groups"]) >= 5 and
          len(pm["data"]["roles"]) == 4, (pm.get("ok"), len(pm["data"].get("groups", [])) if pm.get("ok") else pm))
    save, _ = http_json("PUT", "/api/admin/permissions/roles", {"overrides": {
        "admin": ["admin.access", "user.manage", "remote.view", "remote.request"]}}, token=token)
    check("角色权限覆盖保存", save.get("ok") and save["data"]["override"].get("admin"), save)
    pm2, _ = http_json("GET", "/api/admin/permissions", token=token)
    admin_role = next(x for x in pm2["data"]["roles"] if x["key"] == "admin")
    check("覆盖立即生效", "remote.request" in admin_role["perms"] and "settings.manage" not in admin_role["perms"], admin_role["perms"])
    up, _ = http_json("PUT", "/api/admin/users/%d/perms" % STUDENT_ID, {"perms": ["stats.view"]}, token=token)
    check("单用户额外权限保存", up.get("ok") and up["data"]["perms"] == ["stats.view"], up)

    print("\n[3] 数据库管理")
    db, _ = http_json("GET", "/api/admin/db/tables", token=token)
    check("表清单（含 game_scores）", db.get("ok") and "game_scores" in [t["name"] for t in db["data"]["tables"]], db)
    trows, _ = http_json("GET", "/api/admin/db/table/users?page=1&page_size=5", token=token)
    check("表数据浏览", trows.get("ok") and trows["data"]["total"] >= 3 and len(trows["data"]["rows"]) >= 1, trows)
    q, _ = http_json("POST", "/api/admin/db/query", {"sql": "SELECT id, username FROM users ORDER BY id LIMIT 5"}, token=token)
    check("只读 SQL 查询", q.get("ok") and q["data"]["count"] >= 1, q)
    q2, _ = http_json("POST", "/api/admin/db/query", {"sql": "DELETE FROM users"}, token=token)
    check("拒绝非 SELECT 语句", not q2.get("ok"), q2)
    # 行修改：给学员记录加 title 验证备注
    up2, _ = http_json("PUT", "/api/admin/db/row/users", {"pk": {"id": STUDENT_ID}, "values": {"title": "验证备注"}}, token=token)
    check("行修改", up2.get("ok"), up2)
    t3, _ = http_json("POST", "/api/admin/db/query",
                      {"sql": "SELECT id, title FROM users WHERE id=%d" % STUDENT_ID}, token=token)
    check("行修改已生效", t3.get("ok") and any(r.get("title") == "验证备注" for r in t3["data"]["rows"]), t3)
    bad, _ = http_json("PUT", "/api/admin/db/row/users", {"pk": {"id": 1}, "values": {"password_hash": "x"}}, token=token)
    check("敏感列禁止修改", not bad.get("ok"), bad)

    print("\n[4] 远程协助 HTTP 侧")
    ra, _ = http_json("GET", "/api/admin/remote/agents", token=token)
    check("被控端列表（未开启）", ra.get("ok") and ra["data"]["enabled"] is False, ra)
    ren, _ = http_json("PUT", "/api/admin/settings", {"settings": {"enable_remote_assist": True}}, token=token)
    check("开启远程协助总开关", ren.get("ok"), ren)

    print("\n[5] 游戏联机 WS 全链路")
    ws_admin = WSClient(PORT)
    ws_admin.send({"t": "auth", "d": {"token": token}})
    check("管理员 WS 认证", ws_admin.recv_until("auth")["d"].get("ok"))
    ws_student = WSClient(PORT)
    ws_student.send({"t": "auth", "d": {"token": student_token}})
    check("学员 WS 认证", ws_student.recv_until("auth")["d"].get("ok"))
    ws_admin.send({"t": "game", "d": {"g": "fireboy", "op": "create"}})
    m = ws_admin.recv_until("game", 5)
    check("建房 game/create", m and m["d"].get("ev") == "room" and m["d"].get("role") == "host", m)
    code = m["d"]["room"]
    ws_student.send({"t": "game", "d": {"g": "fireboy", "op": "join", "room": code}})
    m2 = ws_student.recv_until("game", 5)
    check("加入房间 game/join", m2 and m2["d"].get("ev") == "room" and m2["d"].get("role") == "guest", m2)
    ws_admin.send({"t": "game", "d": {"g": "fireboy", "op": "move", "x": 0.5, "y": 0.3, "vx": 0, "vy": 0, "ground": True}})
    moved = None
    for _ in range(10):
        mm = ws_student.recv()
        if mm and mm.get("t") == "game" and mm["d"].get("ev") == "move":
            moved = mm
            break
    check("对端收到 game/move 转发", moved and moved["d"].get("from") == "fire", moved)
    ws_student.send({"t": "game", "d": {"g": "fireboy", "op": "leave"}})
    ws_admin.send({"t": "game", "d": {"g": "fireboy", "op": "leave"}})

    # ---- 象棋联机中继 ----
    ws_admin.drain()
    ws_student.drain()
    ws_admin.send({"t": "game", "d": {"g": "chess", "op": "create"}})
    mc = ws_admin.recv_until("game", 5)
    check("象棋建房 game/create", mc and mc["d"].get("ev") == "room" and mc["d"].get("role") == "host", mc)
    ccode = mc["d"]["room"]
    ws_student.send({"t": "game", "d": {"g": "chess", "op": "join", "room": ccode}})
    mj = ws_student.recv_until("game", 5)
    check("象棋加入 game/join", mj and mj["d"].get("ev") == "room" and mj["d"].get("role") == "guest", mj)
    # host 下发权威局面 state（对应 chess.html 收到 peer_join 时的自动同步）-> guest 收到
    ws_admin.send({"t": "game", "d": {"g": "chess", "op": "state",
                                      "state": {"board": [[None] * 9 for _ in range(10)], "turn": "r", "moves": []}}})
    st_recv = None
    for _ in range(10):
        mm = ws_student.recv()
        if mm and mm.get("t") == "game" and mm["d"].get("ev") == "state" and mm["d"].get("state"):
            st_recv = mm
            break
    check("加入后收到 host 权威局面 state", bool(st_recv) and
          isinstance(st_recv["d"]["state"].get("board"), list), st_recv)
    # 走子转发：host(红) 走中炮 -> guest 收到 move
    ws_admin.send({"t": "game", "d": {"g": "chess", "op": "move", "fr": 9, "fc": 7, "tr": 7, "tc": 7,
                                      "notation": "炮二平五", "state": {"board": [[]], "turn": "b", "moves": []}}})
    mv_recv = None
    for _ in range(10):
        mm = ws_student.recv()
        if mm and mm.get("t") == "game" and mm["d"].get("ev") == "move" and mm["d"].get("fr") == 9:
            mv_recv = mm
            break
    check("对端收到象棋 game/move 转发", mv_recv and mv_recv["d"].get("notation") == "炮二平五", mv_recv)
    # 认输转发：guest 认输 -> host 收到 resign
    ws_student.send({"t": "game", "d": {"g": "chess", "op": "resign"}})
    rg_recv = None
    for _ in range(10):
        mm = ws_admin.recv()
        if mm and mm.get("t") == "game" and mm["d"].get("ev") == "resign":
            rg_recv = mm
            break
    check("对端收到象棋 resign 转发", bool(rg_recv), rg_recv)
    # 和棋提议与接受
    ws_admin.send({"t": "game", "d": {"g": "chess", "op": "draw"}})
    dr_recv = None
    for _ in range(10):
        mm = ws_student.recv()
        if mm and mm.get("t") == "game" and mm["d"].get("ev") == "draw":
            dr_recv = mm
            break
    check("对端收到和棋提议 draw", bool(dr_recv), dr_recv)
    ws_student.send({"t": "game", "d": {"g": "chess", "op": "draw_accept"}})
    da_recv = None
    for _ in range(10):
        mm = ws_admin.recv()
        if mm and mm.get("t") == "game" and mm["d"].get("ev") == "draw_accept":
            da_recv = mm
            break
    check("对端收到接受和棋 draw_accept", bool(da_recv), da_recv)
    ws_student.send({"t": "game", "d": {"g": "chess", "op": "leave"}})
    ws_admin.send({"t": "game", "d": {"g": "chess", "op": "leave"}})

    print("\n[6] 远程协助 WS 全链路")
    # 学员注册为被控端
    ws_student.send({"t": "remote_reg", "d": {"device": "测试机", "note": "验证", "screen_w": 1920, "screen_h": 1080, "monitors": 1}})
    reg = ws_student.recv_until("remote_reg", 5)
    check("被控端注册 remote_reg", reg and reg["d"].get("ok"), reg)
    ra2, _ = http_json("GET", "/api/admin/remote/agents", token=token)
    check("管理员列表出现被控端", ra2.get("ok") and len(ra2["data"]["list"]) == 1, ra2)
    # 管理员发起请求
    ws_admin.send({"t": "remote_request", "d": {"user_id": STUDENT_ID}})
    rr = ws_admin.recv_until("remote_request_result", 5)
    check("管理员发起请求 remote_request", rr and rr["d"].get("ok"), rr)
    ask = ws_student.recv_until("remote_ask", 5)
    check("被控端收到确认弹窗 remote_ask", ask and ask["d"].get("session_id"), ask)
    ws_student.send({"t": "remote_accept", "d": {"session_id": ask["d"]["session_id"]}})
    rstart = ws_student.recv_until("remote_start", 5)
    check("被控端收到 remote_start", rstart and rstart["d"].get("role") == "agent", rstart)
    # 管理员端也收到 start
    astart = None
    for _ in range(10):
        mm = ws_admin.recv()
        if mm and mm.get("t") == "remote_start":
            astart = mm
            break
    check("管理员收到 remote_start", astart and astart["d"].get("role") == "admin", astart)
    # 管理员下发键鼠指令 → 被控端收到
    ws_admin.send({"t": "remote_input", "d": {"k": "move", "x": 0.5, "y": 0.5}})
    inp = None
    for _ in range(10):
        mm = ws_student.recv()
        if mm and mm.get("t") == "remote_input":
            inp = mm
            break
    check("输入指令转发 remote_input", inp and inp["d"].get("k") == "move", inp)
    # 被控端结束
    ws_student.send({"t": "remote_end", "d": {"session_id": ask["d"]["session_id"], "reason": "验证结束"}})
    end = None
    for _ in range(10):
        mm = ws_admin.recv()
        if mm and mm.get("t") == "remote_end":
            end = mm
            break
    check("会话结束 remote_end", end and "验证结束" in end["d"].get("reason", ""), end)

    ws_admin.close()
    ws_student.close()

    print("\n[7] 批量操作")
    bl, _ = http_json("POST", "/api/admin/batch/logs", {"ids": [1], "action": "delete"}, token=token)
    check("批量删除日志", bl.get("ok"), bl)
    bu, _ = http_json("POST", "/api/admin/batch/users", {"ids": [STUDENT_ID], "action": "enable"}, token=token)
    check("批量启用用户", bu.get("ok"), bu)
    bb, _ = http_json("POST", "/api/admin/batch/users", {"ids": [STUDENT_ID], "action": "blacklist", "reason": "验证"}, token=token)
    check("批量拉黑用户", bb.get("ok"), bb)

    print("\n============================================================")
    if SKIP:
        print("  通过 %d 项，失败 %d 项，跳过 %d 项" % (len(PASS), len(FAIL), len(SKIP)))
        print("  跳过项需要可选的静态资源，不影响核心功能。")
    else:
        print("  通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    print("============================================================")
    srv.shutdown()
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
