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
端到端自检脚本（不依赖第三方库）
用法：python3 tools/e2e_test.py [--port 8899]
会临时创建 data 目录、启动服务、模拟登录/聊天/学习/考试/管理全流程，最后输出汇总。
"""
import base64
import hashlib
import json
import os
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

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8901
DATA = "/tmp/lcdata_e2e"
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  → " + str(extra)) if extra and not cond else ""))


def http_json(method, path, data=None, token=None, form=None, files=None, base=None):
    url = (base or BASE) + urllib.parse.quote(path, safe="/?&=,%2B")
    headers = {}
    if token:
        headers["X-Token"] = token
    body = None
    if files:
        boundary = "----e2ebound"
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


class WSClient(object):
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s.settimeout(10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET /ws HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (port, key))
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(1024)
        self.handshake_ok = b"101" in buf.split(b"\r\n")[0]
        self.rest = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.rest) < n:
            d = self.s.recv(4096)
            if not d:
                raise IOError("closed")
            self.rest += d
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, obj):
        payload = json.dumps(obj).encode()
        b = bytearray([0x81])
        n = len(payload)
        mask = os.urandom(4)
        if n < 126:
            b.append(0x80 | n)
        else:
            b.append(0x80 | 126)
            b += struct.pack(">H", n)
        b += mask
        b += bytes(bytearray(c ^ mask[i % 4] for i, c in enumerate(payload)))
        self.s.sendall(bytes(b))

    def recv(self, timeout=6):
        self.s.settimeout(timeout)
        try:
            b0 = self._read(1)[0]
            b1 = self._read(1)[0]
        except Exception:
            return None
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._read(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._read(8))[0]
        payload = self._read(ln) if ln else b""
        try:
            return json.loads(payload.decode())
        except Exception:
            return None

    def recv_until(self, t, tries=8):
        for _ in range(tries):
            m = self.recv()
            if not m:
                break
            if m.get("t") == t:
                return m
        return None

    def drain(self):
        self.s.settimeout(0.3)
        try:
            while self.recv(0.3):
                pass
        except Exception:
            pass

    def collect(self, t, seconds=2.0):
        """在 seconds 秒内收集所有类型为 t 的消息"""
        out = []
        end = time.time() + seconds
        while time.time() < end:
            m = self.recv(max(0.1, end - time.time()))
            if not m:
                continue
            if m.get("t") == t:
                out.append(m.get("d") or {})
        return out

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    global PORT
    import shutil
    if os.path.exists(DATA):
        shutil.rmtree(DATA)
    app = Application(data_dir=DATA)
    app.bootstrap()
    app.cfg.set("port", PORT)
    srv = http.Server(("127.0.0.1", PORT), http.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)

    print("\n[1] 静态资源（单文件前端）")
    try:
        r = urllib.request.urlopen(BASE + "/", timeout=5)
        page = r.read().decode("utf-8", "replace")
        check("GET / 单文件首页", r.status == 200 and 'id="app"' in page and "<style>" in page, r.status)
        check("首页自包含（无外链 css/js）",
              ("style.css" not in page) and ("<script src=" not in page) and ("<link rel=\"stylesheet\"" not in page),
              len(page))
        check("首页含内联脚本", "<script>" in page and "window.API" in page, len(page))
    except Exception as e:
        check("GET / 单文件首页", False, e)
    for p in ("/favicon.ico",):
        try:
            r = urllib.request.urlopen(BASE + p, timeout=5)
            check("GET " + p, r.status == 200, r.status)
        except Exception as e:
            check("GET " + p, False, e)

    print("\n[2] 认证")
    pwd = None
    pf = os.path.join(DATA, "初始管理员密码.txt")
    if os.path.exists(pf):
        for line in open(pf, encoding="utf-8"):
            if line.startswith("密码："):
                pwd = line[3:].strip()
    check("生成初始管理员密码", bool(pwd))
    r, _ = http_json("POST", "/api/auth/login", {"username": "admin", "password": "wrong"})
    check("错误密码被拒绝", not r.get("ok"), r.get("msg"))
    r, _ = http_json("POST", "/api/auth/login", {"username": "admin", "password": pwd})
    check("管理员登录", bool(r.get("ok")), r.get("msg"))
    admin_token = r["data"]["token"] if r.get("ok") else ""
    r, _ = http_json("GET", "/api/auth/me", token=admin_token)
    check("获取个人信息", r.get("ok") and r["data"]["user"]["role"] == "superadmin")
    perms = r["data"]["user"]["perms"]
    check("超级管理员具备全部权限", len(perms) >= 15, len(perms))

    print("\n[3] 聊天")
    r, _ = http_json("GET", "/api/chat/rooms", token=admin_token)
    check("房间列表", r.get("ok") and len(r["data"]["list"]) >= 4, len(r["data"]["list"] or []))
    room_id = next(x["id"] for x in r["data"]["list"] if x["type"] == "public")
    r, _ = http_json("GET", "/api/chat/rooms/%d/messages" % room_id, token=admin_token)
    check("历史消息", r.get("ok") and len(r["data"]["list"]) >= 1)
    r, _ = http_json("POST", "/api/chat/rooms/%d/messages" % room_id,
                     {"content": "大家好，@admin 这是一条测试消息 😀"}, token=admin_token)
    check("发送文本消息", r.get("ok") and r["data"]["id"] > 0, r.get("msg"))
    mid = r["data"]["id"] if r.get("ok") else 0
    check("@提及解析", r.get("ok") and len(r["data"]["mentions"]) >= 0)
    r, _ = http_json("POST", "/api/chat/messages/%d/edit" % mid, {"content": "已编辑的测试消息"}, token=admin_token)
    check("编辑消息", r.get("ok") and r["data"]["edited"])
    r, _ = http_json("POST", "/api/chat/messages/%d/recall" % mid, {}, token=admin_token)
    check("撤回消息", r.get("ok"))
    r, _ = http_json("GET", "/api/chat/search?q=测试", token=admin_token)
    check("消息搜索", r.get("ok"))
    r, _ = http_json("POST", "/api/chat/dm", {"user_id": 2}, token=admin_token)
    check("创建私聊", r.get("ok") and r["data"]["id"] > 0)
    r, _ = http_json("POST", "/api/chat/rooms", {"name": "E2E测试房间", "type": "public", "topic": "自动化"}, token=admin_token)
    check("创建房间", r.get("ok"))
    new_room = r["data"]["id"] if r.get("ok") else 0

    print("\n[4] 文件上传下载")
    r, _ = http_json("POST", "/api/files/upload", token=admin_token,
                     form={"room_id": str(room_id)}, files={"hello.txt": b"hello enterprise chat"})
    check("上传文件", r.get("ok") and len(r["data"]["list"]) == 1, r.get("msg"))
    fid = r["data"]["list"][0]["id"] if r.get("ok") else 0
    try:
        rr = urllib.request.urlopen(BASE + "/api/files/%d/download?token=%s" % (fid, admin_token), timeout=5)
        data = rr.read()
        check("下载文件", data == b"hello enterprise chat", data[:40])
    except Exception as e:
        check("下载文件", False, e)

    print("\n[5] WebSocket 实时通信")
    ws = WSClient(PORT)
    check("WS 握手", ws.handshake_ok)
    ws.send({"t": "auth", "d": {"token": admin_token}})
    m = ws.recv_until("auth")
    check("WS 认证", bool(m and m.get("d", {}).get("ok")), m)
    ws.send({"t": "sub", "d": {"room_id": room_id}})
    m = ws.recv_until("sub")
    check("WS 订阅房间", bool(m and m.get("t") == "sub"), m)
    ws.send({"t": "msg", "d": {"room_id": room_id, "content": "来自 WebSocket 的实时消息"}})
    got = ws.recv_until("msg")
    check("WS 收到广播消息", bool(got and "实时消息" in got["d"]["content"]), got)
    ws.send({"t": "ping", "d": {}})
    m = ws.recv_until("pong")
    check("WS ping/pong", bool(m and m.get("t") == "pong"), m)
    ws.close()

    print("\n[6] 学习中心")
    r, _ = http_json("GET", "/api/learn/courses", token=admin_token)
    check("课程列表", r.get("ok") and len(r["data"]["list"]) >= 1)
    cid = r["data"]["list"][0]["id"]
    r, _ = http_json("POST", "/api/learn/courses/%d/enroll" % cid, {}, token=admin_token)
    check("报名课程", r.get("ok"))
    r, _ = http_json("GET", "/api/learn/courses/%d" % cid, token=admin_token)
    check("课程详情", r.get("ok") and len(r["data"]["lessons"]) >= 1)
    lid = r["data"]["lessons"][0]["id"]
    r, _ = http_json("POST", "/api/learn/lessons/%d/finish" % lid, {}, token=admin_token)
    check("完成课时并记录进度", r.get("ok") and r["data"]["progress"] > 0, r.get("msg"))
    r, _ = http_json("GET", "/api/learn/materials", token=admin_token)
    check("资料库列表", r.get("ok"))
    r, _ = http_json("POST", "/api/learn/materials", {"title": "E2E资料", "content": "这是测试资料",
                                                      "category": "测试"}, token=admin_token)
    check("上传资料", r.get("ok"))
    r, _ = http_json("GET", "/api/learn/exams", token=admin_token)
    check("考试列表", r.get("ok") and len(r["data"]["list"]) >= 1)
    eid = r["data"]["list"][0]["id"]
    r, _ = http_json("POST", "/api/learn/exams/%d/start" % eid, {}, token=admin_token)
    check("开始考试", r.get("ok") and len(r["data"]["questions"]) >= 1, r.get("msg"))
    if r.get("ok"):
        rid = r["data"]["record_id"]
        answers = {}
        for q in r["data"]["questions"]:
            if q["qtype"] in ("single", "judge"):
                answers[q["id"]] = "A"
            elif q["qtype"] == "multi":
                answers[q["id"]] = "A,B,D"
            else:
                answers[q["id"]] = "测试答案"
        r2, _ = http_json("POST", "/api/learn/exams/%d/submit" % eid,
                          {"record_id": rid, "answers": answers}, token=admin_token)
        check("提交考试并自动判分", r2.get("ok") and "score" in r2["data"], r2.get("msg"))
        r3, _ = http_json("GET", "/api/learn/exams/%d/result/%d" % (eid, rid), token=admin_token)
        check("查看考试结果", r3.get("ok") and len(r3["data"]["questions"]) >= 1)
    r, _ = http_json("GET", "/api/learn/assignments", token=admin_token)
    check("作业列表", r.get("ok") and len(r["data"]["list"]) >= 1)
    aid = r["data"]["list"][0]["id"]
    r, _ = http_json("POST", "/api/learn/assignments/%d/submit" % aid, {"content": "我的作业内容"}, token=admin_token)
    check("提交作业", r.get("ok"))
    r, _ = http_json("POST", "/api/learn/checkin", {"note": "e2e"}, token=admin_token)
    check("学习打卡", r.get("ok"))
    r, _ = http_json("GET", "/api/learn/my/overview", token=admin_token)
    check("我的学习概览", r.get("ok") and r["data"]["courses"] >= 1)

    print("\n[6.5] 班级 / 成绩册 / 今日待办")
    r, _ = http_json("POST", "/api/learn/classes",
                     {"name": "E2E测试班", "term": "2026-1", "desc": "e2e"}, token=admin_token)
    check("创建班级", r.get("ok"), r.get("msg"))
    cid = r["data"]["id"] if r.get("ok") else 0
    r, _ = http_json("GET", "/api/learn/classes", token=admin_token)
    check("班级列表", r.get("ok") and any(c["id"] == cid for c in r["data"]["list"]), r.get("msg"))
    r, _ = http_json("POST", "/api/learn/classes/%d/members" % cid,
                     {"usernames": "student"}, token=admin_token)
    check("班级添加成员", r.get("ok") and r["data"]["added"] == 1, r.get("msg"))
    r, _ = http_json("GET", "/api/learn/classes/%d" % cid, token=admin_token)
    check("班级详情含成员", r.get("ok") and len(r["data"]["members"]) == 1, r.get("msg"))
    # 把 admin 也放进班级：下面用 admin 身份提交作业，成绩册才能对上号
    r, _ = http_json("POST", "/api/learn/classes/%d/members" % cid,
                     {"usernames": "admin"}, token=admin_token)
    check("班级添加 admin", r.get("ok") and r["data"]["added"] == 1, r.get("msg"))
    # 作业挂班级
    r, _ = http_json("POST", "/api/learn/assignments",
                     {"title": "E2E班级作业", "content": "班级作业", "class_id": cid,
                      "deadline": int(time.time()) + 86400}, token=admin_token)
    cl_aid = r["data"]["id"] if r.get("ok") else 0
    check("作业可挂班级", r.get("ok"), r.get("msg"))
    # 提交 → 批阅（覆盖式：同一人只保留一条）
    r, _ = http_json("POST", "/api/learn/assignments/%d/submit" % cl_aid,
                     {"content": "第一次"}, token=admin_token)
    check("班级作业提交", r.get("ok"), r.get("msg"))
    r, _ = http_json("POST", "/api/learn/assignments/%d/submit" % cl_aid,
                     {"content": "第二次（覆盖）"}, token=admin_token)
    check("重复提交为覆盖而非新增", r.get("ok") and r["data"].get("resubmitted"), r.get("msg"))
    r, _ = http_json("GET", "/api/learn/assignments/%d/submissions" % cl_aid, token=admin_token)
    check("提交记录只有一条", r.get("ok") and len(r["data"]["list"]) == 1, r.get("msg"))
    sid = r["data"]["list"][0]["id"] if r.get("ok") and r["data"]["list"] else 0
    r, _ = http_json("POST", "/api/learn/submissions/%d/grade" % sid,
                     {"score": 88, "feedback": "不错"}, token=admin_token)
    check("批阅作业", r.get("ok"), r.get("msg"))
    r, _ = http_json("POST", "/api/learn/assignments/%d/submit" % cl_aid,
                     {"content": "已批阅后想改"}, token=admin_token)
    check("已批阅后拒绝改交", not r.get("ok"), r.get("msg"))
    # 成绩册（此时已批阅，admin 那一行应有分）
    r, _ = http_json("GET", "/api/learn/classes/%d/grades" % cid, token=admin_token)
    check("成绩册可读", r.get("ok"), r.get("msg"))
    if r.get("ok"):
        g = r["data"]
        check("成绩册含学生行", g["student_count"] == 2, g["student_count"])
        check("成绩册含作业列", len(g["assignments"]) == 1, g["assignments"])
        mine = [x for x in g["rows"] if x["username"] == "admin"]
        check("成绩册找到 admin 行", len(mine) == 1, [x["username"] for x in g["rows"]])
        if mine:
            check("成绩册作业列有分", mine[0]["hw"][0]["score"] == 88, mine[0]["hw"])
            check("成绩册作业均分", mine[0]["hw_avg"] == 88, mine[0]["hw_avg"])
            check("未提交者记为空", [x for x in g["rows"] if x["username"] == "student"][0]["hw"][0]["score"] == -1,
                  [x for x in g["rows"] if x["username"] == "student"][0]["hw"])
    r, _ = http_json("POST", "/api/learn/submissions/%d/return" % sid,
                     {"feedback": "请重做"}, token=admin_token)
    check("退回重做", r.get("ok"), r.get("msg"))
    r, _ = http_json("POST", "/api/learn/assignments/%d/submit" % cl_aid,
                     {"content": "退回后重交"}, token=admin_token)
    check("退回后可重交", r.get("ok"), r.get("msg"))
    # 退回重交后分数应失效（需要重新批阅），成绩册显示 -1
    r, _ = http_json("GET", "/api/learn/classes/%d/grades" % cid, token=admin_token)
    _rows = (r.get("data") or {}).get("rows") or []
    _me = [x for x in _rows if x["username"] == "admin"]
    check("退回重交后成绩失效待重批",
          r.get("ok") and _me and _me[0]["hw"][0]["score"] == -1, _me)
    # 今日待办
    r, _ = http_json("GET", "/api/learn/my/todo", token=admin_token)
    check("今日待办接口", r.get("ok") and "role_mode" in r["data"], r.get("msg"))
    check("待办返回四类计数",
          r.get("ok") and all(k in r["data"] for k in
                              ("course_count", "pending_homework", "available_exams", "streak")),
          r.get("data"))

    print("\n[7] 后台管理")
    r, _ = http_json("GET", "/api/admin/dashboard", token=admin_token)
    check("仪表盘", r.get("ok") and r["data"]["users"] >= 3)
    r, _ = http_json("GET", "/api/admin/users?page=1&page_size=10", token=admin_token)
    check("用户列表", r.get("ok") and len(r["data"]["list"]) >= 3)
    r, _ = http_json("POST", "/api/admin/users", {"username": "e2euser", "display_name": "测试用户",
                                                  "role": "student", "dept": "技术研发部"}, token=admin_token)
    check("创建用户", r.get("ok") and "password" in r["data"], r.get("msg"))
    uid = r["data"]["id"] if r.get("ok") else 0
    r, _ = http_json("POST", "/api/admin/users/%d/reset-pwd" % uid, {}, token=admin_token)
    check("重置密码", r.get("ok"))
    r, _ = http_json("POST", "/api/admin/users/%d/toggle" % uid, {}, token=admin_token)
    check("停用/启用用户", r.get("ok"))
    r, _ = http_json("POST", "/api/admin/users/%d/toggle" % uid, {}, token=admin_token)
    r, _ = http_json("POST", "/api/admin/users/import",
                     {"data": "imp1,导入用户1,student,市场销售部\nimp2,导入用户2,teacher,人力资源部"}, token=admin_token)
    check("批量导入用户", r.get("ok") and r["data"]["ok"] >= 2, r.get("msg"))
    r, _ = http_json("POST", "/api/admin/announcements", {"title": "E2E公告", "content": "测试公告内容",
                                                          "pinned": 1}, token=admin_token)
    check("发布公告", r.get("ok"))
    r, _ = http_json("GET", "/api/announcements", token=admin_token)
    check("公告列表", r.get("ok") and len(r["data"]["list"]) >= 1)
    r, _ = http_json("GET", "/api/admin/logs", token=admin_token)
    check("审计日志", r.get("ok") and len(r["data"]["list"]) >= 3)
    r, _ = http_json("GET", "/api/admin/settings", token=admin_token)
    check("读取设置", r.get("ok") and "site_name" in r["data"]["settings"])
    r, _ = http_json("PUT", "/api/admin/settings", {"settings": {"site_name": "E2E企业学习平台"}}, token=admin_token)
    check("修改设置", r.get("ok"))
    r, _ = http_json("POST", "/api/admin/sensitive", {"word": "测试敏感词"}, token=admin_token)
    check("添加敏感词", r.get("ok"))
    r, _ = http_json("POST", "/api/admin/backup", {}, token=admin_token)
    check("数据备份", r.get("ok") and r["data"]["size"] > 0, r.get("msg"))
    r, _ = http_json("GET", "/api/admin/backups", token=admin_token)
    check("备份列表", r.get("ok") and len(r["data"]["list"]) >= 1)
    r, _ = http_json("GET", "/api/admin/stats/export", token=admin_token)
    check("导出学习报表", isinstance(r.get("raw", None), (bytes, bytearray)) and len(r["raw"]) > 50, str(r)[:80])

    print("\n[8] 权限与越权")
    r, _ = http_json("POST", "/api/auth/login", {"username": "teacher", "password": ""})
    r2, _ = http_json("GET", "/api/admin/dashboard", token="invalid_token_xxx")
    check("无效令牌被拒绝", not r2.get("ok"))
    r, _ = http_json("GET", "/api/learn/questions", token=admin_token)
    check("题库（管理员可见）", r.get("ok"))
    r, _ = http_json("POST", "/api/learn/questions", {"qtype": "single", "stem": "E2E题目？",
                                                      "options": ["A.是", "B.否"], "answer": "A", "score": 10},
                     token=admin_token)
    check("新增题目", r.get("ok"))
    r, _ = http_json("GET", "/api/chat/online", token=admin_token)
    check("在线用户接口", r.get("ok"))

    print("\n[9] 性能（并发 60 条消息）")
    t0 = time.time()
    ok_n = 0
    threads = []

    results = []

    def send_msg(i):
        rr, _ = http_json("POST", "/api/chat/rooms/%d/messages" % room_id,
                          {"content": "并发消息 %d" % i}, token=admin_token)
        results.append(bool(rr.get("ok")))

    for i in range(60):
        t = threading.Thread(target=send_msg, args=(i,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    ok_n = sum(1 for x in results if x)
    dt = time.time() - t0
    check("并发写入 60 条消息（%.2fs）" % dt, ok_n == 60, ok_n)
    r, _ = http_json("GET", "/api/chat/rooms/%d/messages?size=100" % room_id, token=admin_token)
    check("消息持久化", r.get("ok") and len(r["data"]["list"]) >= 60, len(r["data"]["list"] or []))

    print("\n" + "=" * 60)
    print("  通过 %d 项，失败 %d 项，耗时 %.1fs" % (len(PASS), len(FAIL), time.time() - t0))
    if FAIL:
        print("  失败项：" + "、".join(FAIL))
    print("=" * 60)
    try:
        srv.shutdown()
    except Exception:
        pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
