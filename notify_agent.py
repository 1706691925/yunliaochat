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
云聊 · 桌面提醒助手（Windows 小工具）

作用：常驻后台连接云聊服务器，有人 @ 你、给你发私聊、或在群里发消息时，
     在桌面右下角弹出提醒（可配声音 / 停留时长），点击提醒直接打开对应会话。

提醒范围可自选：@我 / 私聊 / 全部群消息。

纯标准库实现（tkinter + socket + urllib），可打包为单个 EXE：
    pyinstaller --noconsole --onefile --icon app.ico --name 云聊提醒 notify_agent.py
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

APP_NAME = "云聊提醒"
APP_ID = "yunliao-notify"
APP_VERSION = "1.2.0"
PROTOCOL = "yunliao"
IPC_PORT = 47653
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA

SCOPE_LABEL = {"mention": "@我", "dm": "私聊", "all": "群消息"}


# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
def _blob(data):
    import ctypes
    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    return BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def secret_save(text):
    """用 Windows DPAPI 加密（只有当前用户能解密），返回 base64"""
    try:
        import base64, ctypes
        blob_in, _keep = _blob(text.encode("utf-8"))
        blob_out = ctypes.create_string_buffer(0)
        out = ctypes.cast(ctypes.pointer(blob_out), ctypes.c_void_p)
        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]
        ob = BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(ob))
        if not ok:
            return ""
        try:
            raw = ctypes.string_at(ob.pbData, ob.cbData)
            return base64.b64encode(raw).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(ob.pbData)
    except Exception:
        return ""


def secret_load(b64):
    try:
        import base64, ctypes
        if not b64:
            return ""
        data = base64.b64decode(b64)
        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]
        blob_in, _keep = _blob(data)
        ob = BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(ob))
        if not ok:
            return ""
        try:
            return ctypes.string_at(ob.pbData, ob.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(ob.pbData)
    except Exception:
        return ""


def asset_path(name):
    """图标等随包资源：兼容源码运行与 PyInstaller 打包（_MEIPASS）"""
    here = os.path.dirname(os.path.abspath(__file__))
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    bases.append(here)
    for base in bases:
        for rel in (os.path.join("assets", name), name):
            p = os.path.join(base, rel)
            if os.path.exists(p):
                return p
    return ""


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_FILE = os.path.join(config_dir(), "config.json")

DEFAULT_CFG = {
    "server": "",
    "username": "",
    "token": "",
    "user": {},
    "remember": True,
    "scope_mention": True,
    "scope_dm": True,
    "scope_all": False,
    "sound": True,
    "toast_sec": 8,
    "autostart": False,
    "minimized": False,
    "auto_login": True,
    "secret": "",
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        pass



# ----------------------------------------------------------------------------
# 与网页联动：yunliao:// 协议 + 单实例通道
# ----------------------------------------------------------------------------
def protocol_command():
    if getattr(sys, "frozen", False):
        return '"%s" "%%1"' % sys.executable
    py = sys.executable
    pyw = py.replace("python.exe", "pythonw.exe")
    script = os.path.abspath(__file__)
    return '"%s" "%s" "%%1"' % (pyw if os.path.exists(pyw) else py, script)


def register_protocol():
    """注册 yunliao:// 协议：网页点「在桌面助手中打开」即可带着登录态唤起本程序"""
    try:
        import winreg
        base = r"Software\Classes\%s" % PROTOCOL
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:云聊提醒")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, protocol_command())
        return True
    except Exception:
        return False


def parse_protocol(argv):
    """解析命令行里的 yunliao://auth?server=..&token=.."""
    try:
        from urllib.parse import urlparse, parse_qs, unquote
        for a in argv:
            if not a or not a.lower().startswith(PROTOCOL + "://"):
                continue
            u = urlparse(a)
            q = parse_qs(u.query or "")
            token = unquote((q.get("token") or [""])[0])
            server = unquote((q.get("server") or [""])[0])
            if token:
                return {"server": server, "token": token}
    except Exception:
        pass
    return None


def ipc_send(payload, timeout=2.0):
    """把登录态交给已在运行的实例；没有实例时返回 False"""
    try:
        s = socket.create_connection(("127.0.0.1", IPC_PORT), timeout=timeout)
        s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        s.close()
        return True
    except Exception:
        return False


def ipc_server(cb):
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", IPC_PORT))
        srv.listen(8)
    except Exception:
        return None

    def loop():
        while True:
            try:
                c, _ = srv.accept()
                c.settimeout(3)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                c.close()
                if data.strip():
                    cb(json.loads(data.decode("utf-8")))
            except Exception:
                continue
    threading.Thread(target=loop, daemon=True, name="lcipc").start()
    return srv


# ----------------------------------------------------------------------------
# WebSocket 客户端（最小实现，仅文本帧）
# ----------------------------------------------------------------------------
class WSClient(object):
    def __init__(self):
        self.sock = None
        self.alive = False
        self._lock = threading.Lock()

    def connect(self, url, timeout=10):
        if url.startswith("wss://"):
            scheme, rest = "wss", url[6:]
        elif url.startswith("ws://"):
            scheme, rest = "ws", url[5:]
        else:
            raise ValueError("无效的 WebSocket 地址：%s" % url)
        slash = rest.find("/")
        host_port, path = (rest, "/") if slash < 0 else (rest[:slash], rest[slash:])
        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
            port = int(port)
        else:
            host, port = host_port, (443 if scheme == "wss" else 80)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        if scheme == "wss":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(self.sock, server_hostname=host)

        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host_port, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("握手阶段连接断开")
            buf += chunk
        if b"101" not in buf.split(b"\r\n")[0]:
            raise ConnectionError("WebSocket 握手失败：%s" % buf.split(b"\r\n")[0].decode("latin1"))
        self.alive = True
        self.sock.settimeout(None)

    # ---- 帧 ----
    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接已关闭")
            buf += chunk
        return buf

    def _read_frame(self):
        hdr = self._read_exact(2)
        fin, opcode = (hdr[0] >> 7) & 1, hdr[0] & 0x0F
        masked, length = (hdr[1] >> 7) & 1, hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length) if length else b""
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return fin, opcode, payload

    def recv_frame(self, timeout=30):
        try:
            self.sock.settimeout(timeout)
            fin, opcode, payload = self._read_frame()
            while not fin:
                f2, _, p2 = self._read_frame()
                payload += p2
                fin = f2
            return opcode, payload
        except (socket.timeout, OSError, ConnectionError, struct.error):
            return None, None
        finally:
            try:
                self.sock.settimeout(None)
            except Exception:
                pass

    def _frame(self, opcode, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        b = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            b.append(0x80 | n)
        elif n < 65536:
            b.append(0x80 | 126)
            b += struct.pack(">H", n)
        else:
            b.append(0x80 | 127)
            b += struct.pack(">Q", n)
        mask = os.urandom(4)
        b += mask
        b += bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return bytes(b)

    def send_text(self, obj):
        if not self.alive:
            return False
        try:
            with self._lock:
                self.sock.sendall(self._frame(OP_TEXT, json.dumps(obj, ensure_ascii=False)))
            return True
        except Exception:
            self.alive = False
            return False

    def send_pong(self, data=b""):
        try:
            with self._lock:
                self.sock.sendall(self._frame(OP_PONG, data))
        except Exception:
            self.alive = False

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# HTTP API
# ----------------------------------------------------------------------------
class Api(object):
    def __init__(self, server):
        s = (server or "").strip().rstrip("/")
        if s and not s.startswith("http://") and not s.startswith("https://"):
            s = "http://" + s
        self.server = s

    def _req(self, method, path, body=None, token=""):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method,
                                     headers={"Content-Type": "application/json; charset=utf-8",
                                              "X-Token": token or ""})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "msg": "HTTP %d" % e.code}
        except Exception as e:
            return {"ok": False, "msg": "连接失败：%s" % e}

    def login(self, username, password):
        return self._req("POST", "/api/auth/login", {"username": username, "password": password})

    def me(self, token):
        return self._req("GET", "/api/auth/me", token=token)

    def rooms(self, token):
        return self._req("GET", "/api/chat/rooms", token=token)

    def ws_url(self):
        if self.server.startswith("https://"):
            return "wss://" + self.server[8:] + "/ws"
        return "ws://" + self.server[7:] + "/ws"


# ----------------------------------------------------------------------------
# 后台连接与筛选
# ----------------------------------------------------------------------------
class Notifier(threading.Thread):
    def __init__(self, api, token, user_id, cfg, emit):
        super().__init__(daemon=True)
        self.api = api
        self.token = token
        self.user_id = int(user_id or 0)
        self.cfg = cfg
        self.emit = emit          # emit(dict) 线程安全回调
        self.rooms = {}
        self.stop_flag = threading.Event()
        self.conn = None

    # 提醒范围判断
    def classify(self, msg):
        try:
            if int((msg.get("user") or {}).get("id") or 0) == self.user_id:
                return None
            if msg.get("recalled"):
                return None
            rid = int(msg.get("room_id") or 0)
            room = self.rooms.get(rid) or {}
            if self.user_id in [int(x) for x in (msg.get("mentions") or [])]:
                return "mention"
            if (room.get("type") == "dm") and self.cfg.get("scope_dm", True):
                return "dm"
            if self.cfg.get("scope_all"):
                return "all"
        except Exception:
            return None
        return None

    def load_rooms(self):
        r = self.api.rooms(self.token)
        if r.get("ok"):
            self.rooms = {int(x["id"]): x for x in (r["data"].get("list") or [])}
        return self.rooms

    def run(self):
        backoff = 2
        while not self.stop_flag.is_set():
            try:
                self.load_rooms()
                self.emit({"kind": "status", "text": "连接中…", "state": "wait"})
                conn = WSClient()
                conn.connect(self.api.ws_url())
                self.conn = conn
                conn.send_text({"t": "auth", "d": {"token": self.token}})
                ok, deadline = False, time.time() + 12
                while time.time() < deadline and not self.stop_flag.is_set():
                    op, payload = conn.recv_frame(timeout=5)
                    if op is None:
                        continue
                    if op == OP_PING:
                        conn.send_pong(payload)
                        continue
                    try:
                        m = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    if m.get("t") == "auth":
                        if (m.get("d") or {}).get("ok"):
                            ok = True
                            break
                        self.emit({"kind": "fatal", "text": (m.get("d") or {}).get("msg") or "登录已失效"})
                        conn.close()
                        return
                if not ok:
                    conn.close()
                    raise ConnectionError("鉴权超时")
                for rid in list(self.rooms):
                    conn.send_text({"t": "sub", "d": {"room_id": rid}})
                self.emit({"kind": "status", "text": "已连接", "state": "ok"})
                backoff = 2
                last_ping = time.time()
                while not self.stop_flag.is_set():
                    op, payload = conn.recv_frame(timeout=30)
                    if op is None:
                        if not conn.alive:
                            raise ConnectionError("连接已断开")
                        if time.time() - last_ping > 25:
                            conn.send_text({"t": "ping"})
                            last_ping = time.time()
                        continue
                    if op == OP_PING:
                        conn.send_pong(payload)
                        continue
                    if op == OP_CLOSE:
                        raise ConnectionError("服务端关闭连接")
                    if op not in (OP_TEXT, OP_BIN):
                        continue
                    try:
                        m = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    t = m.get("t")
                    d = m.get("d") or {}
                    if t == "msg":
                        cat = self.classify(d)
                        if cat:
                            room = self.rooms.get(int(d.get("room_id") or 0)) or {}
                            self.emit({"kind": "msg", "cat": cat, "msg": d, "room": room})
                    elif t == "room_update":
                        rid = int(d.get("room_id") or 0)
                        if rid:
                            self.load_rooms()
                            conn.send_text({"t": "sub", "d": {"room_id": rid}})
                    elif t in ("kick", "logout"):
                        if t == "logout":
                            self.emit({"kind": "logout",
                                       "msg": d.get("msg") or "账号已在网页退出登录"})
                        else:
                            self.emit({"kind": "fatal", "text": d.get("msg") or "账号已在其他 IP 登录"})
                        conn.close()
                        return
                    elif t == "banned":
                        self.emit({"kind": "fatal", "text": d.get("msg") or "连接被拒绝"})
                        conn.close()
                        return
            except Exception as e:
                self.emit({"kind": "status", "text": "连接中断：%s" % e, "state": "err"})
            finally:
                if self.conn:
                    self.conn.close()
                    self.conn = None
            if self.stop_flag.is_set():
                break
            time.sleep(backoff)
            backoff = min(30, backoff * 2)

    def stop(self):
        self.stop_flag.set()
        if self.conn:
            self.conn.close()

# ----------------------------------------------------------------------------
# 开机自启
# ----------------------------------------------------------------------------
def autostart_command():
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return '"%s" --minimized' % exe
    py = sys.executable
    pyw = py.replace("python.exe", "pythonw.exe")
    script = os.path.abspath(__file__)
    return '"%s" "%s" --minimized' % (pyw if os.path.exists(pyw) else py, script)


def set_autostart(on):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\\Microsoft\\Windows\\CurrentVersion\\Run", 0,
                             winreg.KEY_SET_VALUE)
        if on:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def play_sound():
    try:
        import winsound
        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 桌面提醒（右下角弹窗）
# ----------------------------------------------------------------------------
CAT_COLOR = {"mention": "#f59e0b", "dm": "#2f6fed", "all": "#0ea5e9"}
CAT_TEXT = {"mention": "有人 @ 你", "dm": "新私聊", "all": "新消息"}


class Toast(object):
    """右下角提醒卡片，自动堆叠、自动消失"""

    alive = []

    def __init__(self, app, title, body, cat, on_click=None, seconds=8):
        self.app = app
        self.on_click = on_click
        self.closed = False
        import tkinter as tk
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#dfe8f6")
        wrap = tk.Frame(self.win, bg="#ffffff")
        wrap.pack(fill="both", expand=True, padx=1, pady=1)
        bar = tk.Frame(wrap, bg=CAT_COLOR.get(cat, "#2f6fed"), width=4)
        bar.pack(side="left", fill="y")
        inner = tk.Frame(wrap, bg="#ffffff")
        inner.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        head = tk.Frame(inner, bg="#ffffff")
        head.pack(fill="x")
        tk.Label(head, text=title, bg="#ffffff", fg="#1f3350",
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(side="left")
        tk.Label(head, text=CAT_TEXT.get(cat, "提醒"), bg="#eef4ff", fg=CAT_COLOR.get(cat, "#2f6fed"),
                 font=("Microsoft YaHei UI", 8), padx=6, pady=1).pack(side="left", padx=6)
        close = tk.Label(head, text="✕", bg="#ffffff", fg="#9aa9bf", cursor="hand2",
                         font=("Microsoft YaHei UI", 10))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        tk.Label(inner, text=body, bg="#ffffff", fg="#5a6b82", justify="left", anchor="w",
                 wraplength=270, font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(6, 0))
        self.win.bind("<Button-1>", self._click)
        for w in (wrap, inner, head):
            w.bind("<Button-1>", self._click)
        Toast.alive.append(self)
        self._place()
        self.timer = self.win.after(max(2, int(seconds)) * 1000, self.close)

    def _place(self):
        try:
            self.win.update_idletasks()
            w, h = 320, self.win.winfo_reqheight()
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            idx = len([t for t in Toast.alive if t is not self and not t.closed])
            x = sw - w - 22
            y = sh - 90 - h - idx * (h + 10)
            self.win.geometry("%dx%d+%d+%d" % (w, h, x, max(20, y)))
        except Exception:
            pass

    def _click(self, e=None):
        if self.on_click:
            try:
                self.on_click()
            except Exception:
                pass
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.win.after_cancel(self.timer)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        try:
            Toast.alive.remove(self)
        except ValueError:
            pass


class Bubble(object):
    """桌面悬浮球：品牌图标 + 右上角 ×（点击立即退出助手，机房应急隐藏）"""

    def __init__(self, app):
        import tkinter as tk
        self.app = app
        self.size = 84
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", "#ff00ff")
        except Exception:
            pass
        self.canvas = tk.Canvas(self.win, width=self.size, height=self.size, bg="#ff00ff",
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack()

        self.photo = None
        img = asset_path("notify_icon_76.png")
        if img:
            try:
                self.photo = tk.PhotoImage(file=img)
                self.canvas.create_image(self.size // 2, self.size // 2, image=self.photo)
            except Exception:
                self.photo = None
        if not self.photo:      # 兜底：蓝粉渐变圆
            for i in range(14):
                t = i / 13.0
                col = "#%02x%02x%02x" % (int(0x4f + (0xec - 0x4f) * t),
                                         int(0x92 + (0x6f - 0x92) * t),
                                         int(0xf0 + (0xa4 - 0xf0) * t))
                self.canvas.create_oval(6 + i, 6 + i, 78 - i, 78 - i, fill=col, outline="")
            self.canvas.create_text(42, 42, text="云", fill="#ffffff",
                                    font=("Microsoft YaHei UI", 18, "bold"))

        # 未读角标（左下，避免压住图标右上角的 ×）
        self.badge = self.canvas.create_oval(4, 60, 30, 82, fill="#ff4d6d", outline="#ffffff",
                                             width=2, state="hidden")
        self.count = self.canvas.create_text(17, 71, text="", fill="#ffffff",
                                             font=("Microsoft YaHei UI", 9, "bold"), state="hidden")

        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("%dx%d+%d+%d" % (self.size, self.size, sw - 120, sh - 240))

        # 图标右上角 × 的位置（76px 图标内约 57,14；画布居中偏移 4）
        self.hot = (4 + 57, 4 + 14, 17)
        self._drag = None
        self._moved = False
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)

    def _in_hot(self, x, y):
        hx, hy, r = self.hot
        return (x - hx) ** 2 + (y - hy) ** 2 <= r * r

    def _hover(self, e):
        try:
            self.canvas.configure(cursor="hand2" if self._in_hot(e.x, e.y) else "fleur")
        except Exception:
            pass

    def _press(self, e):
        self._drag = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())
        self._moved = False

    def _motion(self, e):
        if not self._drag:
            return
        dx, dy = e.x_root - self._drag[0], e.y_root - self._drag[1]
        if abs(dx) + abs(dy) > 3:
            self._moved = True
        self.win.geometry("+%d+%d" % (self._drag[2] + dx, self._drag[3] + dy))

    def _release(self, e):
        drag, self._drag = self._drag, None
        if self._moved:
            return
        # 点在图标上的 × → 直接退出助手（不弹确认，机房应急）
        if self._in_hot(e.x, e.y):
            self.app.quit()
            return
        self.app.restore_from_bubble()

    def set_unread(self, n):
        if n:
            self.canvas.itemconfigure(self.badge, state="normal")
            self.canvas.itemconfigure(self.count, text=(str(n) if n < 100 else "99+"), state="normal")
        else:
            self.canvas.itemconfigure(self.badge, state="hidden")
            self.canvas.itemconfigure(self.count, state="hidden")

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class AgentApp(object):
    def __init__(self, cfg, pending=None):
        import queue as _queue
        import tkinter as tk
        self.tk = tk
        self.queue = _queue.Queue()
        self.cfg = cfg
        self.api = None
        self.token = cfg.get("token") or ""
        self.user = cfg.get("user") or {}
        self.notifier = None
        self.bubble = None
        self.unread = 0
        self.recent = []
        self.pending = pending
        self._watch_gen = 0

        self.root = tk.Tk()
        self.root.title("云聊 · 桌面提醒 v" + APP_VERSION)
        self.root.geometry("396x640")
        self.root.minsize(360, 520)
        self.root.configure(bg="#f3f7fd")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_bubble)
        try:
            self.root.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico"))
        except Exception:
            pass

        self._build()
        self.root.after(120, self._pump)
        self._boot()

    # ---------- 布局 ----------
    def _build(self):
        tk = self.tk
        self.head = tk.Frame(self.root, bg="#2f6fed", height=64)
        self.head.pack(fill="x")
        self.head.pack_propagate(False)
        tk.Label(self.head, text="云聊 · 桌面提醒", bg="#2f6fed", fg="#ffffff",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", padx=16)
        self.dot = tk.Label(self.head, text="● 未连接", bg="#2f6fed", fg="#cfe0ff",
                            font=("Microsoft YaHei UI", 9))
        self.dot.pack(side="right", padx=16)

        self.body = tk.Frame(self.root, bg="#f3f7fd")
        self.body.pack(fill="both", expand=True)

        self.login_frame = self._build_login()
        self.main_frame = self._build_main()

    def _build_login(self):
        tk = self.tk
        f = tk.Frame(self.body, bg="#f3f7fd")
        box = tk.Frame(f, bg="#ffffff", highlightbackground="#e3eaf4", highlightthickness=1)
        box.pack(fill="x", padx=16, pady=16)
        tk.Label(box, text="登录后自动接收消息提醒", bg="#ffffff", fg="#5a6b82",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(14, 6))

        def field(label, show=None, default=""):
            tk.Label(box, text=label, bg="#ffffff", fg="#465a75",
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(8, 2))
            e = tk.Entry(box, font=("Microsoft YaHei UI", 10), relief="solid", bd=1,
                         highlightthickness=0, bg="#fbfdff")
            if show:
                e.configure(show=show)
            if default:
                e.insert(0, default)
            e.pack(fill="x", padx=16, ipady=5)
            return e

        self.ed_server = field("服务器地址", default=self.cfg.get("server") or "http://127.0.0.1:8080")
        self.ed_user = field("账号", default=self.cfg.get("username") or "")
        self.ed_pwd = field("密码", show="●")
        self.ed_pwd.bind("<Return>", lambda e: self.do_login())
        self.v_remember = tk.BooleanVar(value=bool(self.cfg.get("auto_login", True)))
        tk.Checkbutton(box, text="记住密码，下次自动登录（本机加密保存）", variable=self.v_remember,
                       bg="#ffffff", fg="#465a75", activebackground="#ffffff", selectcolor="#ffffff",
                       anchor="w", font=("Microsoft YaHei UI", 9), bd=0,
                       highlightthickness=0).pack(fill="x", padx=14, pady=(8, 0))

        self.lg_err = tk.Label(box, text="", bg="#ffffff", fg="#e05353",
                               font=("Microsoft YaHei UI", 9), wraplength=320, justify="left")
        self.lg_err.pack(anchor="w", padx=16, pady=(8, 0))

        self.btn_login = tk.Button(box, text="登 录", command=self.do_login, bg="#2f6fed",
                                   fg="#ffffff", activebackground="#1f5fd0", activeforeground="#ffffff",
                                   relief="flat", bd=0, font=("Microsoft YaHei UI", 10, "bold"),
                                   cursor="hand2", height=2)
        self.btn_login.pack(fill="x", padx=16, pady=(12, 16))
        return f

    def _build_main(self):
        tk = self.tk
        f = tk.Frame(self.body, bg="#f3f7fd")

        # 账号
        acc = tk.Frame(f, bg="#ffffff", highlightbackground="#e3eaf4", highlightthickness=1)
        acc.pack(fill="x", padx=14, pady=(12, 10))
        self.lb_name = tk.Label(acc, text="", bg="#ffffff", fg="#1f3350",
                                font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        self.lb_name.pack(side="left", padx=12, pady=9)
        tk.Button(acc, text="切换账号", command=self.logout, bg="#f2f6fd", fg="#3d6cb0",
                  relief="flat", bd=0, font=("Microsoft YaHei UI", 8), cursor="hand2").pack(
            side="right", padx=10, pady=8)

        # 提醒范围
        sec = tk.Frame(f, bg="#ffffff", highlightbackground="#e3eaf4", highlightthickness=1)
        sec.pack(fill="x", padx=14)
        tk.Label(sec, text="提醒范围", bg="#ffffff", fg="#1f3350",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        self.v_mention = tk.BooleanVar(value=bool(self.cfg.get("scope_mention", True)))
        self.v_dm = tk.BooleanVar(value=bool(self.cfg.get("scope_dm", True)))
        self.v_all = tk.BooleanVar(value=bool(self.cfg.get("scope_all", False)))
        self._check(sec, "有人 @ 我时提醒（推荐）", self.v_mention)
        self._check(sec, "收到私聊消息时提醒", self.v_dm)
        self._check(sec, "群里的所有新消息都提醒（消息多时较吵）", self.v_all)
        tk.Frame(sec, bg="#ffffff", height=8).pack()

        # 选项
        opt = tk.Frame(f, bg="#ffffff", highlightbackground="#e3eaf4", highlightthickness=1)
        opt.pack(fill="x", padx=14, pady=10)
        self.v_sound = tk.BooleanVar(value=bool(self.cfg.get("sound", True)))
        self.v_auto = tk.BooleanVar(value=bool(self.cfg.get("autostart", False)))
        self._check(opt, "声音提醒", self.v_sound, command=self._on_sound)
        self._check(opt, "开机自动启动", self.v_auto, command=self._on_autostart)
        row = tk.Frame(opt, bg="#ffffff")
        row.pack(fill="x", padx=12, pady=(2, 10))
        tk.Label(row, text="提醒停留", bg="#ffffff", fg="#465a75",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.sp_sec = tk.Spinbox(row, from_=3, to=60, width=4, font=("Microsoft YaHei UI", 9),
                                 relief="solid", bd=1, highlightthickness=0, justify="center")
        self.sp_sec.delete(0, "end")
        self.sp_sec.insert(0, str(self.cfg.get("toast_sec", 8)))
        self.sp_sec.pack(side="left", padx=6)
        self.sp_sec.bind("<FocusOut>", lambda e: self._on_sec())
        tk.Label(row, text="秒（弹出后自动消失）", bg="#ffffff", fg="#7c8da6",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        # 按钮
        btns = tk.Frame(f, bg="#f3f7fd")
        btns.pack(fill="x", padx=14)
        self._btn(btns, "测试提醒", self.test_toast, "#eef4ff", "#2f6fed").pack(side="left", ipadx=8)
        self._btn(btns, "收起为悬浮球", self.hide_to_bubble, "#eef4ff", "#2f6fed").pack(side="left", padx=8, ipadx=6)
        self._btn(btns, "退出", self.quit, "#feecec", "#d64545").pack(side="right", ipadx=10)

        # 最近提醒
        tk.Label(f, text="最近提醒", bg="#f3f7fd", fg="#7c8da6",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(10, 2))
        wrap = tk.Frame(f, bg="#ffffff", highlightbackground="#e3eaf4", highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.lst = tk.Listbox(wrap, bd=0, highlightthickness=0, bg="#ffffff", fg="#33475f",
                              font=("Microsoft YaHei UI", 9), activestyle="none", selectbackground="#e8f0ff")
        self.lst.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = tk.Scrollbar(wrap, command=self.lst.yview, width=10)
        sb.pack(side="right", fill="y")
        self.lst.configure(yscrollcommand=sb.set)
        self.lst.bind("<Double-Button-1>", self._open_selected)
        self._recent_rooms = []
        return f

    def _check(self, parent, text, var, command=None):
        tk = self.tk
        cb = tk.Checkbutton(parent, text=text, variable=var, bg="#ffffff", fg="#33475f",
                            activebackground="#ffffff", activeforeground="#1f3350",
                            selectcolor="#ffffff", anchor="w", justify="left",
                            font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0,
                            command=command or self._save_scope)
        cb.pack(fill="x", padx=12, pady=2)
        return cb

    def _btn(self, parent, text, cmd, bg, fg):
        tk = self.tk
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat", bd=0,
                         font=("Microsoft YaHei UI", 9), cursor="hand2", padx=6, pady=6)

    # ---------- 启动 ----------
    def _boot(self):
        self.api = Api(self.cfg.get("server") or "")
        register_protocol()
        ipc_server(self._ipc_apply)
        if self.pending and self.pending.get("token"):
            self.apply_auth(self.pending.get("server"), self.pending.get("token"))
            self.cfg["minimized"] = self.cfg.get("minimized", False)
            return
        if self.token and self.cfg.get("server"):
            if not self.cfg.get("minimized"):
                self.show_main()
            self.root.after(50, self._verify_token)
        elif self.cfg.get("secret") and self.cfg.get("username"):
            self.show_login()
            self.lg_err.configure(text="正在自动登录…")
            self._auto_login()
        else:
            self.show_login()
        if self.cfg.get("minimized"):
            self.root.withdraw()
            self.root.after(700, self.hide_to_bubble)

    # ---------- 网页同步登录 ----------
    def _ipc_apply(self, payload):
        if not isinstance(payload, dict) or not payload.get("token"):
            return
        self.root.after(0, lambda: self.apply_auth(payload.get("server"), payload.get("token")))

    def apply_auth(self, server, token):
        """用网页传来的登录态直接进入（不需要再输账号密码）"""
        if server:
            self.cfg["server"] = server
            self.api = Api(server)
        if not self.cfg.get("server"):
            self.cfg["server"] = self.ed_server.get().strip() or "http://127.0.0.1:8080"
            self.api = Api(self.cfg["server"])
        self.token = token or ""
        self.cfg["token"] = self.token
        save_cfg(self.cfg)
        try:
            self.lg_err.configure(text="已从网页同步登录，正在连接…")
        except Exception:
            pass
        self.show_main()
        self._verify_token()

    def _watch_session(self, gen):
        """每 30 秒检查一次登录态：网页退出后这里也退出"""
        while gen == self._watch_gen and self.token:
            time.sleep(30)
            if gen != self._watch_gen or not self.token:
                return
            r = self.api.me(self.token)
            if not r.get("ok"):
                self.queue.put({"kind": "logout", "msg": "账号已在网页退出登录"})
                return

    def _verify_token(self):
        def work():
            r = self.api.me(self.token)
            if r.get("ok"):
                self.user = r["data"]["user"]
                self.cfg["user"] = self.user
                save_cfg(self.cfg)
                self.queue.put({"kind": "authed"})
                self.start_notifier()
            else:
                self.queue.put({"kind": "token_dead"})
        threading.Thread(target=work, daemon=True).start()

    def _auto_login(self):
        """用 DPAPI 保存的密码自动登录（token 过期 / 网页退出后仍可恢复）"""
        def work():
            pwd = secret_load(self.cfg.get("secret") or "")
            user = self.cfg.get("username") or ""
            if not (pwd and user):
                self.queue.put({"kind": "autologin_fail", "msg": "没有保存的登录信息"})
                return
            api = Api(self.cfg.get("server") or "")
            r = api.login(user, pwd)
            self.queue.put({"kind": "autologin", "r": r, "api": api})
        threading.Thread(target=work, daemon=True).start()

    def show_login(self):
        self.main_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True)
        self.dot.configure(text="● 未登录", fg="#cfe0ff")

    def show_main(self):
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)
        name = (self.user or {}).get("display_name") or "未登录"
        role = (self.user or {}).get("role_name") or ""
        self.lb_name.configure(text="%s%s" % (name, ("  · " + role) if role else ""))
        self.root.deiconify()

    # ---------- 登录 ----------
    def do_login(self):
        server = self.ed_server.get().strip()
        user = self.ed_user.get().strip()
        pwd = self.ed_pwd.get()
        if not server or not user or not pwd:
            self.lg_err.configure(text="请填写服务器地址、账号和密码")
            return
        self.lg_err.configure(text="正在登录…")
        self.btn_login.configure(state="disabled", text="登录中…")

        def work():
            api = Api(server)
            r = api.login(user, pwd)
            self.queue.put({"kind": "login", "r": r, "server": server, "username": user,
                            "api": api, "pwd": pwd})
        threading.Thread(target=work, daemon=True).start()

    def _on_login(self, ev):
        r = ev["r"]
        if not r.get("ok"):
            self.btn_login.configure(state="normal", text="登 录")
            self.lg_err.configure(text=(r.get("msg") or "登录失败").replace("错误：", ""))
            return
        data = r["data"]
        self.api = ev["api"]
        self.token = data["token"]
        self.user = data.get("user") or {}
        self.cfg.update({"server": ev["server"], "username": ev["username"],
                         "token": self.token, "user": self.user})
        try:
            remember = bool(self.v_remember.get()) if hasattr(self, "v_remember") else True
        except Exception:
            remember = True
        self.cfg["auto_login"] = remember
        pwd = ev.get("pwd") or ""
        if remember and pwd:
            self.cfg["secret"] = secret_save(pwd)
        elif not remember:
            self.cfg["secret"] = ""
        save_cfg(self.cfg)
        self.btn_login.configure(state="normal", text="登 录")
        self.lg_err.configure(text="")
        self.show_main()
        self.start_notifier()

    def logout(self):
        self.stop_notifier()
        self.token = ""
        self.cfg["token"] = ""
        save_cfg(self.cfg)
        self.show_login()

    # ---------- 通知线程 ----------
    def start_notifier(self):
        self.stop_notifier()
        self._watch_gen += 1
        threading.Thread(target=self._watch_session, args=(self._watch_gen,), daemon=True).start()
        if not self.token:
            return
        self.notifier = Notifier(self.api, self.token, (self.user or {}).get("id") or 0,
                                 self.cfg, lambda ev: self.queue.put(ev))
        self.notifier.start()

    def stop_notifier(self):
        if self.notifier:
            try:
                self.notifier.stop()
            except Exception:
                pass
            self.notifier = None

    def _pump(self):
        try:
            for _ in range(20):
                ev = self.queue.get_nowait()
                self._handle(ev)
        except Exception:
            pass
        self.root.after(120, self._pump)

    def _handle(self, ev):
        kind = ev.get("kind")
        if kind == "status":
            state = ev.get("state")
            color = {"ok": "#7ee0a6", "err": "#ffb0b0", "wait": "#cfe0ff"}.get(state, "#cfe0ff")
            self.dot.configure(text="● " + (ev.get("text") or ""), fg=color)
        elif kind == "authed":
            if not self.bubble:
                self.show_main()
        elif kind == "autologin":
            r = ev.get("r") or {}
            if r.get("ok"):
                self.api = ev.get("api") or self.api
                self.token = r["data"]["token"]
                self.user = r["data"].get("user") or {}
                self.cfg.update({"token": self.token, "user": self.user})
                save_cfg(self.cfg)
                try:
                    self.lg_err.configure(text="")
                except Exception:
                    pass
                self.show_main()
                self.start_notifier()
            else:
                self.queue.put({"kind": "autologin_fail", "msg": r.get("msg") or "自动登录失败"})
        elif kind == "autologin_fail":
            try:
                self.lg_err.configure(text=(ev.get("msg") or "") + "，请重新登录")
            except Exception:
                pass
        elif kind == "logout":
            self.logout()
            try:
                self.lg_err.configure(text=ev.get("msg") or "账号已退出登录")
            except Exception:
                pass
        elif kind == "token_dead":
            if self.cfg.get("secret") and self.cfg.get("username"):
                try:
                    self.lg_err.configure(text="登录已过期，正在自动登录…")
                except Exception:
                    pass
                self._auto_login()
            else:
                self.logout()
            self.lg_err.configure(text="登录已过期，请重新登录")
        elif kind == "fatal":
            self.dot.configure(text="● " + (ev.get("text") or "已断开"), fg="#ffb0b0")
            self.toast("云聊提醒", ev.get("text") or "连接已断开", "all", None)
        elif kind == "login":
            self._on_login(ev)
        elif kind == "msg":
            self.on_message(ev)

    # ---------- 收到消息 ----------
    def on_message(self, ev):
        msg = ev["msg"]
        room = ev.get("room") or {}
        cat = ev.get("cat")
        sender = (msg.get("user") or {}).get("name") or "有人"
        content = (msg.get("content") or "").strip()
        if not content and msg.get("attachments"):
            content = "[文件] " + ", ".join(a.get("name", "") for a in msg["attachments"][:2])
        if len(content) > 120:
            content = content[:120] + "…"
        room_name = room.get("name") or "会话"
        if cat == "mention":
            title = "%s 在「%s」@ 了你" % (sender, room_name)
        elif cat == "dm":
            title = "%s 发来私聊" % sender
        else:
            title = "%s · %s" % (room_name, sender)
        rid = int(msg.get("room_id") or 0)
        self.toast(title, content or "(空消息)", cat, lambda: self.open_room(rid))
        if self.cfg.get("sound", True):
            play_sound()
        self.add_recent(title, content, rid)
        self.unread += 1
        if self.bubble:
            self.bubble.set_unread(self.unread)

    def toast(self, title, body, cat, on_click):
        try:
            Toast(self, title, body, cat, on_click, int(self.cfg.get("toast_sec", 8)))
        except Exception:
            traceback.print_exc()

    def add_recent(self, title, content, rid):
        text = "%s  %s" % (time.strftime("%H:%M"), title)
        self.recent.insert(0, (text, rid))
        del self.recent[40:]
        try:
            self.lst.insert(0, text)
            self.lst.insert(1, "      " + content)
            while self.lst.size() > 40:
                self.lst.delete("end")
        except Exception:
            pass
        self._recent_rooms = [r for _, r in self.recent]

    def _open_selected(self, e=None):
        try:
            idx = self.lst.curselection()[0]
            rid = self._recent_rooms[idx // 2] if idx < len(self._recent_rooms) * 2 else 0
            self.open_room(rid)
        except Exception:
            pass

    def open_room(self, rid):
        try:
            du = self.cfg.get("server") or ""
            webbrowser.open(du.rstrip("/") + "/?room=%d" % rid)
        except Exception:
            pass

    def test_toast(self):
        self.toast("云聊提醒 · 测试", "这是一条测试提醒，点击可打开聊天窗口。", "mention",
                   lambda: self.open_room(0))

    # ---------- 设置保存 ----------
    def _save_scope(self):
        self.cfg.update({"scope_mention": bool(self.v_mention.get()),
                         "scope_dm": bool(self.v_dm.get()),
                         "scope_all": bool(self.v_all.get())})
        save_cfg(self.cfg)
        if self.notifier:
            self.notifier.cfg = self.cfg

    def _on_sound(self):
        self.cfg["sound"] = bool(self.v_sound.get())
        save_cfg(self.cfg)

    def _on_autostart(self):
        ok = set_autostart(bool(self.v_auto.get()))
        self.cfg["autostart"] = bool(self.v_auto.get())
        save_cfg(self.cfg)
        if not ok:
            self.v_auto.set(False)
            self.cfg["autostart"] = False
            save_cfg(self.cfg)

    def _on_sec(self):
        try:
            self.cfg["toast_sec"] = max(3, min(60, int(self.sp_sec.get())))
        except Exception:
            self.cfg["toast_sec"] = 8
        save_cfg(self.cfg)

    # ---------- 悬浮球 ----------
    def hide_to_bubble(self):
        try:
            if not self.bubble:
                self.bubble = Bubble(self)
                self.bubble.set_unread(self.unread)
            self.root.withdraw()
        except Exception:
            try:
                import traceback
                with open(os.path.join(config_dir(), "error.log"), "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S  hide_to_bubble\n"))
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def restore_from_bubble(self):
        try:
            if self.bubble:
                self.bubble.destroy()
                self.bubble = None
            self.root.deiconify()
            self.root.lift()
            self.unread = 0
        except Exception:
            pass

    def quit(self):
        self._save_scope()
        self.stop_notifier()
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    pending = parse_protocol(sys.argv[1:])
    if pending and ipc_send(pending):
        return          # 已有实例在运行：把登录态交给它，本进程直接退出
    cfg = load_cfg()
    if "--minimized" in sys.argv:
        cfg["minimized"] = True
    register_protocol()
    app = AgentApp(cfg, pending=pending)
    app.root.mainloop()


if __name__ == "__main__":
    main()
