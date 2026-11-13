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
"""
BUG 复现脚本
  1) 发消息重复（HTTP 返回 + WS 广播各渲染一次）
  2) 文件上传链路
  3) 主要 GET 接口冒烟（找 500 / 异常）
用法：python3 tools/bug_test.py
"""
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, http    # noqa: E402
from app import Application      # noqa: E402
from tools.e2e_test import http_json, WSClient  # noqa: E402

PORT = 8931
DATA = "/tmp/lcdata_bug"
BASE = "http://127.0.0.1:%d" % PORT


def main():
    shutil.rmtree(DATA, ignore_errors=True)
    os.makedirs(DATA, exist_ok=True)
    app = Application(data_dir=DATA)
    app.bootstrap()
    app.cfg.set("port", PORT)
    srv = http.Server(("127.0.0.1", PORT), http.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)

    # ---- 取初始管理员密码 ----
    pwd = None
    for fn in os.listdir(DATA):
        if fn.startswith("初始管理员"):
            import re as _re
            txt = open(os.path.join(DATA, fn), encoding="utf-8").read()
            m = _re.search(r"密码：(\S+)", txt)
            pwd = m.group(1) if m else txt.strip()
    print("管理员密码:", pwd)

    r, _ = http_json("POST", "/api/auth/login",
                     {"username": "admin", "password": pwd}, base=BASE)
    if not r.get("ok"):
        print("登录失败:", r)
        return
    token = r["data"]["token"]
    print("登录成功，token =", token[:12], "...")

    # ---- 取房间 ----
    r, _ = http_json("GET", "/api/chat/rooms", token=token, base=BASE)
    rooms = r.get("data", {}).get("list", [])
    print("房间数:", len(rooms))
    rid = rooms[0]["id"] if rooms else 0

    # ---- BUG1：发消息重复 ----
    print("\n" + "=" * 60)
    print("BUG1 检测：发送者是否会收到『HTTP 返回』+『WS 广播』两份")
    ws = WSClient(PORT)
    ws.send({"t": "auth", "d": {"token": token}})
    time.sleep(0.4)
    ws.send({"t": "sub", "d": {"room_id": rid}})
    time.sleep(0.4)
    ws.drain()

    r, _ = http_json("POST", "/api/chat/rooms/%d/messages" % rid,
                     {"content": "重复测试消息", "attachments": [], "reply_to": 0},
                     token=token, base=BASE)
    http_id = r.get("data", {}).get("id") if r.get("ok") else None
    print("  HTTP 返回消息 id:", http_id, "ok =", r.get("ok"))

    got = ws.collect("msg", 2.0)
    print("  WS 推送收到的 msg 条数:", len(got))
    for g in got:
        print("     -> id =", g.get("id"), "content =", g.get("content"))
    print("  结论：前端若把 HTTP 返回值也 push 一次，则同一条消息会渲染 %d 次"
          % (len(got) + 1))

    # ---- BUG2：文件上传 ----
    print("\n" + "=" * 60)
    print("BUG2 检测：文件上传")
    r, _ = http_json("POST", "/api/files/upload",
                     form={"room_id": str(rid), "kind": "chat"},
                     files={"t.txt": b"hello-file-content-1234567890"},
                     token=token, base=BASE)
    print("  上传结果 ok =", r.get("ok"), "msg =", r.get("msg", ""))
    if r.get("ok"):
        fl = r["data"]["list"]
        print("  文件列表:", [(f["id"], f["name"], f["size"]) for f in fl])
        fid = fl[0]["id"]
        # 带附件发消息
        r2, _ = http_json("POST", "/api/chat/rooms/%d/messages" % rid,
                          {"content": "带附件", "attachments": [fid], "reply_to": 0},
                          token=token, base=BASE)
        print("  带附件发消息 ok =", r2.get("ok"), "msg =", r2.get("msg", ""))
        if r2.get("ok"):
            print("  attachments 字段:", r2["data"].get("attachments"))
        # 下载
        r3, h3 = http_json("GET", "/api/files/%d/download" % fid, token=token, base=BASE)
        print("  下载 ok =", r3.get("ok"), "类型 =", type(r3).__name__,
              "内容长度 =", len(r3.get("raw", b"")) if isinstance(r3.get("raw"), bytes) else "N/A")
    else:
        print("  ❌ 上传失败，这就是『发送不了文件』的根因")

    # ---- BUG3：接口冒烟 ----
    print("\n" + "=" * 60)
    print("BUG3 检测：主要接口冒烟")
    apis = [
        "/api/chat/rooms", "/api/chat/rooms/public", "/api/chat/online",
        "/api/notifications", "/api/announcements", "/api/users",
        "/api/learn/courses", "/api/learn/materials", "/api/learn/exams",
        "/api/learn/homework", "/api/learn/mine", "/api/files/list",
        "/api/admin/dashboard", "/api/admin/users", "/api/admin/rooms",
        "/api/admin/settings", "/api/admin/sensitive", "/api/admin/backups",
        "/api/admin/logs", "/api/admin/announcements",
    ]
    for p in apis:
        r, _ = http_json("GET", p, token=token, base=BASE)
        ok = r.get("ok")
        flag = "✅" if ok else "❌"
        print("  %s %-32s %s" % (flag, p, "" if ok else r.get("msg", "")))

    print("\n" + "=" * 60)
    print("检测完成")


if __name__ == "__main__":
    main()
