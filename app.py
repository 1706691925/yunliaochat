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
"""
云聊 —— 主程序
纯 Python 标准库实现：HTTP + WebSocket + SQLite，零第三方依赖，可打包为单个 EXE。
"""
import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core                                                    # noqa: E402  (__version__)
from core import config, db, http, ipsec, realtime, security, unread, util, ws   # noqa: E402
from core import app_ctx                              # noqa: E402
import core.app_ctx                                              # noqa: E402
# Tee 把 stdout 同时写进 data/logs/server.log；直接跑 app.py（不经 launcher）时也要能用。
# 放在 core/teelog.py 而不是 launcher.py：launcher 会 import app，从这里反向导入会循环。
from core.teelog import Tee                                       # noqa: E402

BANNER = r"""
   ______                      _
  |  ____|                    (_)
  | |__   _ __  _ __ ___  ___ _ ____   _____ _ __
  |  __| | '_ \| '__/ _ \/ __| |_ \ \ / / _ \ '__|
  | |____| |_) | | |  __/ (__| | | \ V /  __/ |
  |______| .__/|_|  \___|\___|_|_|  \_/ \___|_|
         | |        局域网 · 学习 · 聊天 · 考试
         |_|
"""


class Application(object):
    # 版本号唯一来源在 core/__init__.py，这里只是便捷引用
    version = core.__version__

    def __init__(self, data_dir=None, host=None, port=None, quiet=False):
        self.cfg = config.init(data_dir)
        if host:
            self.cfg.set("host", host)
        if port:
            self.cfg.set("port", port)
        self.quiet = quiet
        self.started_at = util.now_ts()
        self.users_cache = {}
        self.sensitive = security.SensitiveFilter([], self.cfg.get("sensitive_replace", "***"))
        self._load_sensitive()

    # ---------------- 初始化 ----------------
    def bootstrap(self):
        import api  # noqa: F401  注册所有路由
        db.init(self.cfg)
        self._migrate()
        ipsec.reload_rules(True)
        self._seed()
        self._seed_sites()
        # 未读数全量校正一次（防止历史数据或异常中断导致计数漂移），仅在启动时执行
        try:
            unread.repair_all()
        except Exception as e:
            print("[warn] 未读数校正失败：%s" % e)
        threading.Thread(target=realtime.heartbeat_loop, args=(30,), daemon=True).start()
        threading.Thread(target=self._clean_loop, daemon=True).start()
        core.app_ctx.APP = self

    def _migrate(self):
        v = db.get_setting("schema_version", "0")
        if v == "0":
            db.set_setting("schema_version", "1")
        # 字段级迁移：旧库自动补 messages.ip / users.title（幂等，可重复执行）
        try:
            mcols = {r["name"] for r in db.query("PRAGMA table_info(messages)")}
            if mcols and "ip" not in mcols:
                db.execute("ALTER TABLE messages ADD COLUMN ip TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            ucols = {r["name"] for r in db.query("PRAGMA table_info(users)")}
            if ucols and "title" not in ucols:
                db.execute("ALTER TABLE users ADD COLUMN title TEXT DEFAULT ''")
        except Exception:
            pass
        # 隐身巡查：room_members 增加 silent 标记（旧库自动补列）
        try:
            rcols = {r["name"] for r in db.query("PRAGMA table_info(room_members)")}
            if rcols and "silent" not in rcols:
                db.execute("ALTER TABLE room_members ADD COLUMN silent INTEGER DEFAULT 0")
        except Exception:
            pass
        # 小游戏按人授权：users 增加 games 字段（旧库自动补列，默认全关）
        try:
            ucols2 = {r["name"] for r in db.query("PRAGMA table_info(users)")}
            if ucols2 and "games" not in ucols2:
                db.execute("ALTER TABLE users ADD COLUMN games TEXT DEFAULT ''")
        except Exception:
            pass
        # 自定义头像：users 增加 avatar 字段（旧库自动补列，存文件 id）
        try:
            ucols3 = {r["name"] for r in db.query("PRAGMA table_info(users)")}
            if ucols3 and "avatar" not in ucols3:
                db.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
        except Exception:
            pass
        # 旧版本默认白名单过窄（.bin/.gz/.psd 等被拦），升级后自动放宽为黑名单模式
        try:
            from api.files import LEGACY_ALLOW
            cur = str(self.cfg.get("allow_ext", "") or "")
            if cur and ",".join([e.strip().lower() for e in cur.split(",") if e.strip()]) == LEGACY_ALLOW:
                self.cfg.set("allow_ext", "")
                if not self.cfg.get("deny_ext"):
                    self.cfg.set("deny_ext", "exe,msi,bat,cmd,com,scr,pif,vbs,vbe,ps1,psm1,jar,apk,dll,sys,lnk,reg")
                self.cfg.save()
        except Exception:
            pass

    def _load_sensitive(self):
        try:
            rows = db.query("SELECT word FROM sensitive_words")
            self.sensitive.reload([r["word"] for r in rows], self.cfg.get("sensitive_replace", "***"))
        except Exception:
            pass

    def _seed(self):
        """首次运行初始化：部门、管理员、默认房间、示例课程"""
        if db.count("users") > 0:
            return
        ts = util.now_ts()
        # 部门
        for d in ("总经办", "人力资源部", "技术研发部", "市场销售部", "财务部", "生产运营部"):
            db.execute("INSERT OR IGNORE INTO depts (name, sort) VALUES (?,?)", (d, 0))
        # 超级管理员
        pwd = security.gen_initial_password()
        db.insert("users", {
            "username": "admin", "display_name": "系统管理员",
            "password_hash": security.hash_password(pwd), "role": "superadmin",
            "dept": "总经办", "position": "系统管理员", "status": "active",
            "must_change_pwd": 1, "created_at": ts, "created_by": 0, "theme": "light",
        })
        pwd_file = os.path.join(self.cfg.data_dir, "初始管理员密码.txt")
        try:
            with open(pwd_file, "w", encoding="utf-8") as f:
                f.write("云聊 —— 初始管理员账号\n")
                f.write("账号：admin\n密码：%s\n" % pwd)
                f.write("首次登录后请立即修改密码。\n")
        except Exception:
            pass
        self._initial_admin_pwd = pwd

        # 讲师与学员示例账号（全部为虚构演示数据，与任何真实人员无关）
        demo = security.gen_initial_password()
        tid = db.insert("users", {
            "username": "teacher", "display_name": "培训讲师", "role": "teacher",
            "password_hash": security.hash_password(demo), "dept": "人力资源部",
            "position": "培训专员", "status": "active", "created_at": ts, "must_change_pwd": 1,
        })
        sid = db.insert("users", {
            "username": "student", "display_name": "示例学员", "role": "student",
            "password_hash": security.hash_password(demo), "dept": "技术研发部",
            "position": "工程师", "status": "active", "created_at": ts, "must_change_pwd": 1,
        })
        # 其余演示成员，便于体验通讯录 / 群成员 / 权限差异
        # 注意：username 必须全局唯一，且不能与上面的 admin/teacher/student 重名
        demo_members = [
            ("演示讲师", "teacher02", "市场销售部", "产品经理", "teacher"),
            ("演示组长", "leader", "技术研发部", "研发组长", "teacher"),
            ("演示成员一", "user01", "市场销售部", "销售专员", "student"),
            ("演示成员二", "user02", "财务部", "会计", "student"),
            ("演示成员三", "user03", "生产运营部", "运营专员", "student"),
        ]
        for name, uname, dept, pos, role in demo_members:
            db.insert("users", {
                "username": uname, "display_name": name, "role": role,
                "password_hash": security.hash_password(demo), "dept": dept,
                "position": pos, "status": "active", "created_at": ts, "must_change_pwd": 1,
            })
        self._demo_pwd = demo

        # 默认房间
        admin = db.one("SELECT id FROM users WHERE username='admin'")
        rooms = [
            ("综合讨论区", "public", "全员自由交流，禁止发布与工作无关内容"),
            ("学习交流", "public", "课程学习、考试、资料相关讨论"),
            ("讲师答疑", "public", "讲师值班答疑，提问请描述清楚问题背景"),
            ("系统公告", "announce", "重要通知发布频道（仅管理员可发言）"),
        ]
        for name, typ, topic in rooms:
            rid = db.insert("rooms", {"name": name, "type": typ, "owner_id": admin["id"],
                                      "topic": topic, "created_at": ts})
            for uid in (admin["id"], tid, sid):
                db.execute("INSERT OR IGNORE INTO room_members (room_id, user_id, role, joined_at)"
                           " VALUES (?,?,?,?)", (rid, uid, "admin" if uid == admin["id"] else "member", ts))
        db.insert("messages", {"room_id": 1, "user_id": admin["id"],
                               "content": "欢迎使用云聊！请遵守公司信息安全规范，勿外传内部资料。",
                               "created_at": ts})

        # 示例课程
        cid = db.insert("courses", {
            "title": "新员工入职安全与合规培训", "subtitle": "信息安全 / 合规 / 制度",
            "category": "合规培训", "teacher_id": tid, "cover_color": "#2f6fed",
            "desc": "本课程覆盖公司信息安全红线、数据分级、账号口令规范与常见风险场景。",
            "status": "published", "required": 1, "created_at": ts,
        })
        lessons = [("信息安全红线", "1. 禁止将内部数据外发至互联网；\n2. 禁止使用个人网盘存储公司资料；\n3. 发现风险须 30 分钟内上报。"),
                   ("账号与口令规范", "口令长度≥8 位，含两类字符；90 天更换一次；禁止复用。"),
                   ("数据分级与外发", "公开/内部/秘密/机密四级；机密级外发需部门负责人审批。")]
        for i, (t, c) in enumerate(lessons):
            db.insert("lessons", {"course_id": cid, "title": t, "content": c,
                                  "sort": i, "minutes": 15, "created_at": ts})
        # 用更清晰的写法插入示例题目
        db.insert("questions", {"course_id": cid, "qtype": "single", "score": 10,
                                "stem": "公司内部“机密级”数据外发需要经过？",
                                "options": json.dumps(["A. 部门负责人审批", "B. 同事口头同意", "C. 无需审批", "D. 自行决定"], ensure_ascii=False),
                                "answer": "A", "analysis": "机密级数据外发须经部门负责人审批并留痕。",
                                "difficulty": "easy", "created_by": tid, "created_at": ts})
        db.insert("questions", {"course_id": cid, "qtype": "multi", "score": 10,
                                "stem": "以下哪些行为违反信息安全红线？（多选）",
                                "options": json.dumps(["A. 用个人网盘备份公司资料", "B. 将客户名单发到外部邮箱", "C. 在内网系统内共享资料", "D. 使用公司电脑访问外部论坛并下载软件"], ensure_ascii=False),
                                "answer": "A,B,D", "analysis": "内网合规共享不属于违规；其余均构成外泄风险。",
                                "difficulty": "medium", "created_by": tid, "created_at": ts})
        db.insert("questions", {"course_id": cid, "qtype": "judge", "score": 10,
                                "stem": "口令可以多个系统复用，只要长度足够。",
                                "options": json.dumps(["A. 正确", "B. 错误"], ensure_ascii=False),
                                "answer": "B", "analysis": "禁止口令复用。", "difficulty": "easy",
                                "created_by": tid, "created_at": ts})
        eid = db.insert("exams", {"course_id": cid, "title": "入职安全合规测试",
                                  "desc": "共 3 题，满分 30 分，18 分及格。", "duration": 20,
                                  "total_score": 30, "pass_score": 18, "status": "published",
                                  "shuffle": 1, "show_answer": 1, "max_attempts": 3,
                                  "created_by": tid, "created_at": ts})
        for q in db.query("SELECT id FROM questions WHERE course_id=?", (cid,)):
            db.execute("INSERT INTO exam_questions (exam_id, question_id, score, sort) VALUES (?,?,?,?)",
                       (eid, q["id"], 10, q["id"]))
        db.insert("assignments", {"course_id": cid, "title": "提交一份岗位信息风险自查清单",
                                  "content": "结合本岗位，列出至少 5 项日常工作中可能触及的信息安全风险点及控制措施。",
                                  "deadline": ts + 7 * 86400, "created_by": tid, "created_at": ts, "allow_late": 1})
        db.insert("announcements", {"title": "系统上线通知", "content": "云聊已上线，请各部门组织员工完成入职安全合规培训与测试。",
                                    "level": "info", "pinned": 1, "popup": 1, "force_seconds": 0,
                                    "created_by": admin["id"], "created_at": ts})
        db.log_action(admin["id"], "系统", "初始化", "system", "创建默认账号/房间/课程")

    def _seed_sites(self):
        """首次运行导入「常用网站」示例（管理员可在后台改成自己的内网导航）"""
        try:
            if db.count("quick_sites") > 0:
                return
        except Exception:
            return
        ts = util.now_ts()
        # 仅示例条目，指向公开站点；真实内网地址请在后台「常用网站」里自行维护
        data = [
            ("办公协同", "企业官网", "https://www.example.com/"),
            ("办公协同", "知识库示例", "https://www.example.org/"),
            ("学习资源", "公开课示例", "https://www.example.net/"),
        ]
        for i, (cat, name, url) in enumerate(data):
            db.insert("quick_sites", {"name": name, "url": url, "category": cat, "note": "",
                                      "sort": i, "enabled": 1, "created_at": ts})

    # ---------------- 会话 ----------------
    def create_session(self, user_id, ip="", ua=""):
        hours = int(self.cfg.get("session_hours", 12))
        token = security.new_token(32)
        ts = util.now_ts()
        db.execute("INSERT INTO sessions (token, user_id, created_at, expires_at, ip, ua, last_seen)"
                   " VALUES (?,?,?,?,?,?,?)", (token, user_id, ts, ts + hours * 3600, ip, ua, ts))
        return token

    def kick_other_sessions(self, user_id, keep_token="", ip=""):
        """单账号单 IP：同一账号允许多个客户端并存，但必须来自同一个 IP。
        登录时删除「其它 IP」上的会话并精确踢掉对应 WebSocket；同 IP 的其它端不受影响。"""
        ip = (ip or "").strip()
        others = []
        try:
            rows = db.query("SELECT token, ip FROM sessions WHERE user_id=? AND token<>?",
                            (user_id, keep_token))
            others = [r["token"] for r in rows if (r.get("ip") or "").strip() != ip]
            if others:
                db.execute("DELETE FROM sessions WHERE token IN (%s)" % ",".join("?" * len(others)), others)
        except Exception:
            others = []
        if others:
            try:
                realtime.close_by_tokens(others, {"t": "kick", "d": {
                    "msg": "该账号已在其他 IP 登录，本会话已下线（同一账号仅允许一个 IP 同时登录）"}},
                    reason="ip-kicked")
            except Exception:
                pass

    def auth_user_by_token(self, token, ip=""):
        if not token:
            return None
        row = db.one("SELECT s.*, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                     (token,))
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < util.now_ts():
            db.delete("sessions", "token=?", (token,))
            return None
        if row["status"] != "active":
            # 账号已停用/待审核：立即失效其全部会话
            db.delete("sessions", "user_id=?", (row["user_id"],))
            return None
        # 单账号单 IP：换网络/换设备后旧会话立即失效
        sess_ip = (row.get("ip") or "").strip()
        cur_ip = (ip or "").strip()
        if sess_ip and cur_ip and sess_ip != cur_ip:
            db.delete("sessions", "token=?", (token,))
            return None
        if util.now_ts() - (row["last_seen"] or 0) > 60:
            db.execute("UPDATE sessions SET last_seen=? WHERE token=?", (util.now_ts(), token))
        return dict(row)

    def logout_all(self, user_id, reason="logout"):
        """退出登录：失效该账号全部会话（浏览器退出后桌面助手也随之退出）"""
        try:
            db.delete("sessions", "user_id=?", (user_id,))
        except Exception:
            pass
        try:
            realtime.send_and_close(user_id, {"t": "logout", "d": {"msg": "账号已退出登录"}}, reason)
        except Exception:
            pass

    def _clean_loop(self):
        while True:
            time.sleep(600)
            try:
                db.execute("DELETE FROM sessions WHERE expires_at < ?", (util.now_ts(),))
                days = int(self.cfg.get("log_retention_days", 90))
                db.execute("DELETE FROM audit_logs WHERE ts < ?", (util.now_ts() - days * 86400))
                ipsec.reload_rules()
            except Exception:
                pass
            try:
                self._deadline_reminders()
            except Exception as e:
                print("[warn] 截止提醒失败：%s" % e)

    def _deadline_reminders(self):
        """截止前 24 小时内，给还没交作业 / 还没考的学生发一次提醒。

        用一个 reminded_at 时间戳保证「同一条作业只提醒一次」，
        否则每 10 分钟跑一轮会把学生刷屏。
        """
        now = util.now_ts()
        soon = now + 86400
        # 作业
        for a in db.query("SELECT * FROM assignments WHERE deadline>? AND deadline<=? "
                          "AND IFNULL(reminded_at,0)=0", (now, soon)):
            if a["class_id"]:
                targets = [r["user_id"] for r in
                           db.query("SELECT user_id FROM class_members WHERE class_id=?", (a["class_id"],))]
            else:
                targets = [r["id"] for r in db.query("SELECT id FROM users WHERE status='active'")]
            done = {r["user_id"] for r in
                    db.query("SELECT user_id FROM submissions WHERE assignment_id=?", (a["id"],))}
            left = max(0, int((a["deadline"] - now) // 3600))
            n = 0
            for uid in targets:
                if uid in done:
                    continue
                db.notify(uid, "homework", "作业即将截止：%s" % a["title"],
                          "还剩约 %d 小时，请及时提交" % left, "assignment", a["id"])
                n += 1
            db.update("assignments", {"reminded_at": now}, "id=?", (a["id"],))
            if n:
                print("[提醒] 作业「%s」已提醒 %d 人" % (a["title"], n))
        # 考试
        for e in db.query("SELECT * FROM exams WHERE end_at>? AND end_at<=? AND status='published' "
                          "AND IFNULL(reminded_at,0)=0", (now, soon)):
            if e["class_id"]:
                targets = [r["user_id"] for r in
                           db.query("SELECT user_id FROM class_members WHERE class_id=?", (e["class_id"],))]
            else:
                targets = [r["id"] for r in db.query("SELECT id FROM users WHERE status='active'")]
            taken = {r["user_id"] for r in
                     db.query("SELECT user_id FROM exam_records WHERE exam_id=? AND status<>'doing'", (e["id"],))}
            left = max(0, int((e["end_at"] - now) // 3600))
            n = 0
            for uid in targets:
                if uid in taken:
                    continue
                db.notify(uid, "exam", "考试即将结束：%s" % e["title"],
                          "还剩约 %d 小时" % left, "exam", e["id"])
                n += 1
            db.update("exams", {"reminded_at": now}, "id=?", (e["id"],))
            if n:
                print("[提醒] 考试「%s」已提醒 %d 人" % (e["title"], n))

    # ---------------- WebSocket ----------------
    def on_ws_connect(self, conn, headers, ip):
        realtime.register(conn)
        conn.ip = ip
        hit = ipsec.blocked(ip)
        if hit:
            conn.send(json.dumps({"t": "banned", "d": {"msg": "该 IP 已被列入内网黑名单"}}))
            conn.close(4003, "ip_blacklist")
            realtime.unregister(conn)
            return
        conn.send(json.dumps({"t": "hello", "d": {"server_time": util.now_ts(),
                                                  "site": self.cfg.get("site_name")}}))
        try:
            while conn.alive:
                opcode, payload = conn.recv_frame(timeout=60)
                if opcode is None:
                    break
                if opcode == ws.OP_CLOSE:
                    break
                if opcode == ws.OP_PING:
                    conn.pong(payload)
                    continue
                if opcode == ws.OP_PONG:
                    continue
                if opcode not in (ws.OP_TEXT, ws.OP_BIN):
                    continue
                # 二进制帧：远程协助画面流（被控端 → 管理员），不做 JSON 解析
                if opcode == ws.OP_BIN:
                    try:
                        from core import remote
                        remote.forward_frame(conn, payload)
                    except Exception:
                        pass
                    continue
                try:
                    self.on_ws_message(conn, json.loads(payload.decode("utf-8")))
                except Exception as e:
                    conn.send(json.dumps({"t": "error", "d": {"msg": str(e)}}))
        except Exception:
            pass
        finally:
            realtime.unregister(conn)

    def on_ws_message(self, conn, msg):
        t = msg.get("t")
        d = msg.get("d") or {}
        if t == "auth":
            token = d.get("token") or ""
            user = self.auth_user_by_token(token, getattr(conn, "ip", ""))
            if not user:
                conn.send(json.dumps({"t": "auth", "d": {"ok": False, "msg": "令牌无效或已过期"}}))
                conn.close(4000, "auth-failed")
                return
            conn.token = token
            realtime.bind_user(conn, self.public_user(user))
            conn.send(json.dumps({"t": "auth", "d": {"ok": True, "user": self.public_user(user)}}))
            # 上线后推送在线列表与未读
            self.push_online(conn)
            self.push_unread(conn, user)
            return
        if not conn.user:
            conn.send(json.dumps({"t": "error", "d": {"msg": "请先认证"}}))
            return
        if t == "ping":
            conn.send(json.dumps({"t": "pong", "d": {}}))
        elif t == "sub":
            self.ws_sub(conn, d)
        elif t == "unsub":
            realtime.unsubscribe(conn, int(d.get("room_id", 0)))
        elif t == "msg":
            self.ws_msg(conn, d)
        elif t == "typing":
            rid = int(d.get("room_id", 0))
            if self.can_access_room(conn.user, rid):
                # 隐身巡查：静默加入的房间不发输入状态，避免暴露身份
                mem = db.one("SELECT silent FROM room_members WHERE room_id=? AND user_id=?",
                             (rid, conn.user["id"]))
                if not (mem or {}).get("silent"):
                    realtime.send_to_room(rid, {"t": "typing", "d": {
                        "room_id": rid, "user_id": conn.user["id"],
                        "name": conn.user["display_name"]}}, exclude=conn)
        elif t == "read":
            rid = int(d.get("room_id", 0))
            mid = int(d.get("msg_id", 0))
            db.execute("UPDATE room_members SET last_read_id=? WHERE room_id=? AND user_id=?",
                       (mid, rid, conn.user["id"]))
            unread.clear(rid, conn.user["id"])
            realtime.send_to_user(conn.user["id"], {"t": "unread", "d": self.unread_summary(conn.user["id"])})
            # 已读回执：通知房间内其他成员（前端据此刷新消息已读状态）
            if rid and mid:
                realtime.send_to_room(rid, {"t": "read", "d": {
                    "room_id": rid, "msg_id": mid, "user_id": conn.user["id"],
                    "name": conn.user["display_name"]}}, exclude=conn)
        elif t == "presence":
            st = d.get("status", "online")
            realtime._online_state[conn.user["id"]] = {"status": st, "ts": util.now_ts()}
            # 隐身巡查账号：在线状态不对普通用户广播
            if not security.is_patrol_admin(conn.user):
                realtime.broadcast_presence(conn.user["id"], st)
        elif t == "online":
            self.push_online(conn)
        elif t == "game":
            from core import gameroom
            gameroom.handle(conn, d, conn.user)
        elif t == "remote_reg":
            # 被控端程序上线注册（含断线重连）
            from core import remote
            ok, msg = remote.register_agent(conn, conn.user,
                                            d.get("device", ""), d.get("note", ""))
            remote.set_agent_screen(conn, d.get("screen_w", 0), d.get("screen_h", 0),
                                    d.get("monitors", 1))
            conn.send(json.dumps({"t": "remote_reg", "d": {"ok": ok, "msg": msg}}))
        elif t == "remote_accept":
            from core import remote
            ok, msg = remote.accept_session(conn, str(d.get("session_id", "")), conn.user["id"])
            if not ok:
                conn.send(json.dumps({"t": "error", "d": {"msg": msg}}))
        elif t == "remote_reject":
            from core import remote
            remote.reject_session(conn, str(d.get("session_id", "")), conn.user["id"],
                                  d.get("reason", ""))
        elif t == "remote_request":
            from core import remote
            ok, msg, sid = remote.request_session(conn.user, d.get("user_id", 0), conn)
            conn.send(json.dumps({"t": "remote_request_result",
                                  "d": {"ok": ok, "msg": msg, "session_id": sid}}))
        elif t == "remote_input":
            from core import remote
            ok, msg = remote.forward_input(conn, d)
            if not ok and msg:
                conn.send(json.dumps({"t": "error", "d": {"msg": msg}}))
        elif t == "remote_end":
            from core import remote
            s = remote.session_of_conn(conn)
            if s:
                by = "agent" if s["agent_user_id"] == conn.user["id"] else "admin"
                remote.end_session(s["id"], d.get("reason", "协助已结束"), by=by)

    def ws_sub(self, conn, d):
        rid = int(d.get("room_id", 0))
        if not self.can_access_room(conn.user, rid):
            conn.send(json.dumps({"t": "error", "d": {"msg": "无权访问该房间"}}))
            return
        realtime.subscribe(conn, rid)
        conn.send(json.dumps({"t": "sub", "d": {"room_id": rid, "ok": True}}))

    def ws_msg(self, conn, d):
        from api.chat import create_message
        # 发言前复查账号状态：已被停用则立即踢下线
        cur = db.one("SELECT status FROM users WHERE id=?", (conn.user["id"],))
        if not cur or cur["status"] != "active":
            conn.send(json.dumps({"t": "banned", "d": {"msg": "账号已被停用，请重新登录"}}))
            conn.close(4001, "banned")
            return
        rid = int(d.get("room_id", 0))
        if not self.can_access_room(conn.user, rid):
            conn.send(json.dumps({"t": "error", "d": {"msg": "无权在该房间发言"}}))
            return
        # 公告频道限制与禁言校验统一由 create_message 处理（异常会转成 error 帧回传）
        msg = create_message(conn.user, rid, d, ip=getattr(conn, "ip", ""))
        realtime.send_to_room(rid, {"t": "msg", "d": msg})
        realtime.send_to_room(rid, {"t": "typing_end", "d": {"room_id": rid, "user_id": conn.user["id"]}})

    # ---------------- 辅助 ----------------
    def public_user(self, u, full=False):
        if not u:
            return None
        base = {
            "id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "role": u["role"], "role_name": security.role_name(u["role"]),
            "dept": u.get("dept", ""), "position": u.get("position", ""),
            "title": u.get("title", ""),
            "status": u.get("status", "active"), "signature": u.get("signature", ""),
            "theme": u.get("theme", "light"), "points": u.get("points", 0),
            "learn_minutes": u.get("learn_minutes", 0),
            "must_change_pwd": u.get("must_change_pwd", 0),
            "online": realtime.is_online(u["id"]),
            "color": util.color_of(u["username"]),
            "perms": security.perms_of(u["role"], u.get("extra_perms")),
            "games": u.get("games", "") or "",   # 小游戏授权（逗号分隔 game key）
            # 自定义头像访问路径（users.avatar 存 files.id；空=默认字母头像）
            "avatar": ("/api/files/%s/download" % u["avatar"]) if str(u.get("avatar") or "").isdigit() else "",
            "patrol": security.is_patrol_admin(u),   # 隐身巡查账号（用户名为 admin 的单个用户）
        }
        if full:
            base.update({"email": u.get("email", ""), "phone": u.get("phone", ""),
                         "created_at": u.get("created_at", 0), "last_login": u.get("last_login", 0),
                         "last_seen": u.get("last_seen", 0), "extra_perms": u.get("extra_perms", "")})
        return base

    def can_access_room(self, user, room_id):
        if not user:
            return False
        if security.has_perm(user, "room.manage"):
            return True
        room = db.one("SELECT * FROM rooms WHERE id=?", (room_id,))
        if not room:
            return False
        if room["type"] in ("public", "announce"):
            return True
        return bool(db.one("SELECT 1 FROM room_members WHERE room_id=? AND user_id=?",
                           (room_id, user["id"])))

    def unread_summary(self, user_id):
        """未读计数：读取预存列 unread_count（O(1)），不再逐房间 COUNT。
        10 万条消息规模下，实测从 88ms 降到 0.02ms。"""
        return unread.summary(user_id)

    def push_unread(self, conn, user):
        conn.send(json.dumps({"t": "unread", "d": self.unread_summary(user["id"])}))

    def push_online(self, conn):
        ids = realtime.online_ids()
        if not ids:
            conn.send(json.dumps({"t": "online", "d": []}))
            return
        ph = ",".join("?" * len(ids))
        rows = db.query("SELECT id, username, display_name, dept, role, title FROM users "
                        "WHERE id IN (%s) AND status='active'" % ph, ids)
        # 隐身巡查账号：对普通用户不可见（巡查账号自己仍能看到自己）
        me_patrol = security.is_patrol_admin(conn.user)
        rows = [r for r in rows if not (security.is_patrol_admin(r) and not me_patrol)]
        conn.send(json.dumps({"t": "online", "d": [{"id": r["id"], "name": r["display_name"],
                                                    "username": r["username"], "dept": r["dept"],
                                                    "role": r["role"], "title": r["title"] or ""}
                                                   for r in rows]}))

    # ---------------- 启动 ----------------
    def run(self, open_browser=True):
        host = self.cfg.get("host", "0.0.0.0")
        port = int(self.cfg.get("port", 8080))
        try:
            srv = http.Server((host, port), http.Handler)
        except OSError:
            print("\n  [错误] 端口 %d 已被占用或无权绑定。" % port)
            print("  解决：命令行加 --port 9000，或编辑 data/config.json 中的 port 后重启。\n")
            return None
        self.httpd = srv
        https_srv = self._start_https(host)
        ips = self._local_ips()
        print(BANNER)
        print("  版本：%s" % self.version)
        print("  系统名称：%s" % self.cfg.get("site_name"))
        print("  数据目录：%s" % self.cfg.data_dir)
        print("  监听地址：%s:%d" % (host, port))
        print("  本机访问：http://127.0.0.1:%d" % port)
        for ip in ips:
            print("  内网访问：http://%s:%d" % (ip, port))
        if https_srv is not None:
            hp = self.https_port
            cn = self._cert_cn()
            port_suffix = "" if hp == 443 else ":%d" % hp
            # 证书只对 CN/SAN 里的域名有效。用 IP 访问浏览器必然报"证书名称不匹配"，
            # 用户很容易误以为"HTTPS 坏了"。这里直接把能用的地址摆在最前面。
            print("  HTTPS 访问：https://%s%s   ← 证书匹配，浏览器不会报警" % (cn, port_suffix))
            for ip in ips:
                print("  HTTPS(IP) ：https://%s%s   ← 会提示证书名称不匹配（正常现象）"
                      % (ip, port_suffix))
            print("  证书主机名：%s（Let's Encrypt 只签了域名，内网 IP 无法签发有效证书）" % cn)
        print("  用户总数：%d    在线：%d" % (db.count("users"), realtime.online_count()))
        try:
            if hasattr(self, "_initial_admin_pwd"):
                print("  ★ 初始管理员：admin / %s （已写入 data/初始管理员密码.txt）" % self._initial_admin_pwd)
        except Exception:
            pass
        print("\n  提示：关闭本窗口或按 Ctrl+C 停止服务\n")
        if open_browser and self.cfg.get("auto_open_browser", True):
            threading.Timer(1.0, lambda: self._open(port)).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n正在停止服务…")
            srv.shutdown()
            try:
                if getattr(self, "httpsd", None):
                    self.httpsd.shutdown()
            except Exception:
                pass
            # Ctrl+C 退出时也要关掉内嵌 MySQL
            try:
                _srv = db.server()
                if _srv is not None:
                    _srv.stop()
            except Exception:
                pass
        return srv

    # ---------------- HTTPS ----------------
    def _start_https(self, host):
        """按配置启动 HTTPS 监听（与 HTTP 共用同一套路由与 WebSocket）"""
        if not self.cfg.get("https_enabled"):
            return None
        try:
            hp = int(self.cfg.get("https_port", 8443))
        except Exception:
            hp = 8443
        ctx = self._ssl_context()
        if ctx is None:
            return None
        try:
            srv = http.Server((host, hp), http.Handler, ssl_context=ctx)
        except OSError as e:
            # 443 在 Windows 上常被 IIS / 其他服务占用，或需要管理员权限。
            # 这里不能静默失败——用户会以为"HTTPS 开了"却打不开页面。
            print("\n  [警告] HTTPS 端口 %d 无法监听：%s" % (hp, e))
            print("  常见原因：端口被占用（IIS / 其他网站服务）或低于 1024 需要管理员权限。")
            print("  处理建议：把 https_port 改成 8443 等高位端口（后台「系统设置 → 安全与访问」可改），")
            print("            或先停掉占用该端口的程序。本次仅提供 HTTP 服务。\n")
            fallback = 8443 if hp != 8443 else 0
            if fallback:
                try:
                    srv = http.Server((host, fallback), http.Handler, ssl_context=ctx)
                    print("  [提示] 已自动改用备用 HTTPS 端口 %d。" % fallback)
                    threading.Thread(target=srv.serve_forever, daemon=True, name="lchttps").start()
                    self.httpsd = srv
                    self.https_port = fallback
                    return srv
                except OSError as e2:
                    print("  [警告] 备用端口 %d 也无法监听：%s\n" % (fallback, e2))
            return None
        threading.Thread(target=srv.serve_forever, daemon=True, name="lchttps").start()
        self.httpsd = srv
        self.https_port = hp
        return srv

    def _ssl_context(self):
        import ssl
        cert = config.resolve_path(self.cfg.get("cert_file"))
        key = config.resolve_path(self.cfg.get("key_file"))
        if not cert or not key or not os.path.exists(cert) or not os.path.exists(key):
            print("\n  [警告] 未找到证书文件，HTTPS 未启用。")
            print("  查找位置：cert=%s" % cert)
            print("            key =%s" % key)
            print("  处理建议：把证书放到 data/config.json 里 cert_file / key_file 指定的路径，")
            print("            或用命令行 --cert/--key 指定，或关掉「安全与访问 → 启用 HTTPS」。\n")
            return None
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            try:
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            except Exception:
                pass
            return ctx
        except Exception as e:
            print("\n  [警告] 证书加载失败：%s" % e)
            print("  常见原因：私钥与证书不匹配、私钥有密码保护、或文件格式不是 PEM。")
            print("  PEM 私钥应是 -----BEGIN PRIVATE KEY----- 或 -----BEGIN RSA PRIVATE KEY----- 开头；")
            print("  如果是 IIS 的 .pfx，需要先转成 PEM（openssl pkcs12 -in x.pfx -nodes -out x.pem）。\n")
            return None

    def _cert_cn(self):
        """读取证书 CN 用于启动提示（失败则返回配置路径）"""
        try:
            import ssl
            cert = config.resolve_path(self.cfg.get("cert_file"))
            d = ssl._ssl._test_decode_cert(cert)
            for rdn in d.get("subject", ()):
                for k, v in rdn:
                    if k == "commonName":
                        return v
        except Exception:
            pass
        return str(self.cfg.get("cert_file") or "")

    def _open(self, port):
        try:
            webbrowser.open("http://127.0.0.1:%d" % port)
        except Exception:
            pass

    def _local_ips(self):
        ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips and not ip.startswith("127."):
                    ips.append(ip)
        except Exception:
            pass
        return ips[:4]


def _compat_check():
    """运行环境自检：拦截已知会导致启动失败的环境问题"""
    if sys.version_info >= (3, 13):
        # cgi / cgitb 等模块在 3.13 被移除，本项目已不使用，仅提示
        pass
    if sys.version_info < (3, 8):
        print("  [错误] 需要 Python 3.8 及以上版本，当前：%d.%d"
              % (sys.version_info[0], sys.version_info[1]))
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="云聊")
    ap.add_argument("--port", type=int, default=None, help="监听端口，默认 8080")
    ap.add_argument("--host", default=None, help="监听地址，默认 0.0.0.0")
    ap.add_argument("--data-dir", default=None, help="数据目录，默认程序目录下 data/")
    ap.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    ap.add_argument("--quiet", action="store_true", help="不输出访问日志")
    ap.add_argument("--reset-admin", action="store_true", help="重置管理员密码并打印")
    ap.add_argument("--set-theme", choices=["blue", "bluepink"],
                    help="直接设置站点配色（blue=云聊蓝 / bluepink=蓝粉）后退出，无需登录")
    ap.add_argument("--https", action="store_true",
                    help="启用 HTTPS（配合 --cert/--key，或使用配置里已有的证书路径）")
    ap.add_argument("--cert", default=None, help="HTTPS 证书文件路径（PEM/CRT）")
    ap.add_argument("--key", default=None, help="HTTPS 私钥文件路径")
    args = ap.parse_args()

    _compat_check()
    app = Application(data_dir=args.data_dir, host=args.host, port=args.port, quiet=args.quiet)

    # 命令行指定证书时，覆盖配置后再 bootstrap（bootstrap 里会读取配置决定是否启用 HTTPS）
    if args.cert or args.key:
        if args.cert:
            app.cfg.set("cert_file", os.path.abspath(args.cert))
        if args.key:
            app.cfg.set("key_file", os.path.abspath(args.key))
        app.cfg.set("https_enabled", True)
        app.cfg.save()
    elif args.https:
        app.cfg.set("https_enabled", True)
        app.cfg.save()

    app.bootstrap()

    # 日志同时写入文件（便于无控制台模式排查）
    # 注意：这里必须把"日志路径"传给 Tee，而不是已经打开的文件对象；
    # 之前写成 Tee(sys.stdout, lf) 且 app.py 根本没导入 Tee，
    # 异常被 except 吞掉，导致 data/logs/server.log 永远是空文件——
    # 排查 HTTPS/证书问题时完全没有线索。
    try:
        import datetime
        log_path = os.path.join(app.cfg.log_dir, "server.log")
        sys.stdout = Tee(sys.stdout, log_path)
        sys.stderr = Tee(sys.stderr, log_path)
    except Exception as _e:
        print("  [提示] 日志文件写入已禁用（%s），控制台输出不受影响" % _e)

    if args.reset_admin:
        pwd = security.gen_initial_password()
        db.execute("UPDATE users SET password_hash=?, must_change_pwd=1, failed_attempts=0, locked_until=0"
                   " WHERE username='admin'", (security.hash_password(pwd),))
        print("管理员密码已重置：admin / %s" % pwd)
        return

    if args.set_theme:
        app.cfg.set("theme_color", args.set_theme)
        app.cfg.save()
        print("站点配色已设置为：%s（%s）。重启服务端或在后台刷新即可看到效果。"
              % (args.set_theme, "蓝粉" if args.set_theme == "bluepink" else "云聊蓝"))
        return

    def on_exit(*a):
        try:
            db.commit()
        except Exception:
            pass
        # 关闭随程序拉起的 mysqld，避免残留进程占端口
        try:
            srv = db.server()
            if srv is not None:
                srv.stop()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)
    app.run(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
