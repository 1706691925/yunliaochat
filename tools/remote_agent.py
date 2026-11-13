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
企业学习聊天系统 - 被控端原生程序（Remote Agent）

员工在自己电脑上运行此程序，连接到主服务并注册为"可被协助端"。
管理员发起协助请求后，本机弹窗确认，同意则开始截屏上传并接受键鼠控制。

依赖：mss（截屏）、pyautogui（键鼠注入）、pillow（图像处理）
仅允许这三个第三方库，其余全部使用标准库。
兼容 Python 3.8+。
"""

import argparse
import base64
import getpass
import hashlib
import json
import os
import platform
import signal
import socket
import struct
import sys
import threading
import time
import traceback

# ============================================================
# 第三方库优雅降级导入
# ============================================================
_HAS_MSS = False
_HAS_PYAUTOGUI = False
_HAS_PILLOW = False
_MISSING_LIBS = []

try:
    import mss
    _HAS_MSS = True
except ImportError:
    _MISSING_LIBS.append("mss")

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:
    _MISSING_LIBS.append("pyautogui")

try:
    from PIL import Image
    import io as _io_mod
    _HAS_PILLOW = True
except ImportError:
    _MISSING_LIBS.append("pillow")

if _MISSING_LIBS:
    print("=" * 60)
    print("缺少以下必要的第三方库：%s" % ", ".join(_MISSING_LIBS))
    print("")
    print("请执行以下命令安装：")
    print("  pip install mss pyautogui pillow")
    print("")
    if _HAS_MSS and not _HAS_PYAUTOGUI:
        print('提示：已检测到 mss，若仅缺 pyautogui 可进入"仅共享画面"模式。')
        print("      加 --no-control 参数即可跳过 pyautogui 检查。")
    print("=" * 60)
    # 如果连 mss 都没有，完全无法工作
    if not _HAS_MSS:
        sys.exit(1)
    # 仅有 mss 但没有 pyautogui 且没有 pillow，也无法截屏编码
    if not _HAS_PILLOW:
        print("错误：pillow 用于图像编码，不可缺失。")
        sys.exit(1)
    # mss + pillow 可用，pyautogui 缺失 → 降级模式
    if not _HAS_PYAUTOGUI:
        print('将以"仅共享画面、不接受控制"的降级模式运行。')
        print("")

# pyautogui 安全阀
if _HAS_PYAUTOGUI:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0


# ============================================================
# 工具函数
# ============================================================
def _ts():
    """当前时间戳字符串"""
    return time.strftime("%H:%M:%S")


def _log(msg):
    """带时间戳的控制台日志"""
    print("[%s] %s" % (_ts(), msg))


def _http_json(url, data=None, token=None, method="POST"):
    """用标准库 urllib 发 HTTP JSON 请求，返回 (dict, status_code)"""
    import urllib.request
    import urllib.error
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8")
        return json.loads(raw), resp.getcode()
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8")
        except Exception:
            pass
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {"ok": False, "msg": "HTTP %d: %s" % (e.code, raw[:200])}
        return obj, e.code
    except Exception as e:
        return {"ok": False, "msg": str(e)}, 0


# ============================================================
# WebSocket 客户端（纯标准库实现，RFC 6455）
# ============================================================
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WSClient(object):
    """WebSocket 客户端，支持文本/二进制帧、ping/pong、close、分片重组、掩码"""

    def __init__(self):
        self.sock = None
        self.alive = False
        self._send_lock = threading.Lock()

    def connect(self, url):
        """
        连接 WebSocket 服务端并完成握手。
        url 格式：ws://host:port/path 或 wss://host:port/path
        """
        # 解析 URL
        if url.startswith("wss://"):
            scheme = "wss"
            rest = url[6:]
        elif url.startswith("ws://"):
            scheme = "ws"
            rest = url[5:]
        else:
            raise ValueError("无效的 WebSocket URL：%s" % url)

        slash_idx = rest.find("/")
        if slash_idx < 0:
            host_port = rest
            path = "/"
        else:
            host_port = rest[:slash_idx]
            path = rest[slash_idx:]

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 443 if scheme == "wss" else 80

        # TCP 连接
        self.sock = socket.create_connection((host, port), timeout=15)
        self.sock.settimeout(30)

        # WSS 暂不支持（内网部署一般用 ws），如需可扩展 ssl
        if scheme == "wss":
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(self.sock, server_hostname=host)

        # 生成 Sec-WebSocket-Key
        ws_key = base64.b64encode(os.urandom(16)).decode()

        # 发送握手请求
        handshake = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ) % (path, host_port, ws_key)
        self.sock.sendall(handshake.encode())

        # 读取响应
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("握手阶段连接断开")
            buf += chunk

        status_line = buf.split(b"\r\n")[0].decode()
        if "101" not in status_line:
            raise ConnectionError("WebSocket 握手失败：%s" % status_line)

        # 校验 Sec-WebSocket-Accept
        expected_accept = base64.b64encode(
            hashlib.sha1((ws_key + WS_GUID).encode()).digest()
        ).decode()
        headers_raw = buf.split(b"\r\n\r\n", 1)[0].decode()
        accept_found = False
        for line in headers_raw.split("\r\n"):
            if line.lower().startswith("sec-websocket-accept:"):
                val = line.split(":", 1)[1].strip()
                if val == expected_accept:
                    accept_found = True
                break
        # 部分服务端可能不严格返回 Accept，这里只做警告
        if not accept_found:
            _log("警告：未收到有效的 Sec-WebSocket-Accept 响应头")

        self.alive = True
        self.sock.settimeout(None)  # 后续由 recv 自行管理超时

    def _read_exact(self, n):
        """精确读取 n 字节"""
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接已关闭")
            buf += chunk
        return buf

    def _read_frame(self):
        """读取单个帧，返回 (fin, opcode, payload_bytes)"""
        hdr = self._read_exact(2)
        b0, b1 = hdr[0], hdr[1]
        fin = (b0 >> 7) & 1
        opcode = b0 & 0x0F
        masked = (b1 >> 7) & 1
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        if masked:
            mask_key = self._read_exact(4)
        else:
            mask_key = None
        payload = self._read_exact(length) if length > 0 else b""
        if mask_key:
            payload = bytes(bytearray(c ^ mask_key[i % 4] for i, c in enumerate(bytearray(payload))))
        return fin, opcode, payload

    def recv_frame(self, timeout=None):
        """
        接收一帧（自动重组分片），返回 (opcode, payload)。
        超时或断开返回 (None, None)。
        """
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            fin, opcode, payload = self._read_frame()
            # 分片重组
            while not fin:
                f2, o2, p2 = self._read_frame()
                payload += p2
                fin = f2
            return opcode, payload
        except (socket.timeout, OSError, ConnectionError, struct.error):
            return None, None
        finally:
            if timeout is not None:
                try:
                    self.sock.settimeout(None)
                except Exception:
                    pass

    def _encode_frame(self, opcode, payload, mask=True):
        """编码 WebSocket 帧。客户端必须置掩码位。"""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        b = bytearray()
        b.append(0x80 | opcode)  # FIN=1
        n = len(payload)
        mask_bit = 0x80 if mask else 0
        if n < 126:
            b.append(mask_bit | n)
        elif n < 65536:
            b.append(mask_bit | 126)
            b += struct.pack(">H", n)
        else:
            b.append(mask_bit | 127)
            b += struct.pack(">Q", n)
        if mask:
            m = os.urandom(4)
            b += m
            payload = bytes(bytearray(c ^ m[i % 4] for i, c in enumerate(bytearray(payload))))
        b += payload
        return bytes(b)

    def send_text(self, text):
        """发送文本帧"""
        if not self.alive:
            return False
        frame = self._encode_frame(OP_TEXT, text, mask=True)
        try:
            with self._send_lock:
                self.sock.sendall(frame)
            return True
        except Exception:
            self.alive = False
            return False

    def send_binary(self, data):
        """发送二进制帧"""
        if not self.alive:
            return False
        frame = self._encode_frame(OP_BIN, data, mask=True)
        try:
            with self._send_lock:
                self.sock.sendall(frame)
            return True
        except Exception:
            self.alive = False
            return False

    def send_ping(self, data=b""):
        """发送 ping 帧"""
        if not self.alive:
            return False
        frame = self._encode_frame(OP_PING, data, mask=True)
        try:
            with self._send_lock:
                self.sock.sendall(frame)
            return True
        except Exception:
            self.alive = False
            return False

    def send_pong(self, data=b""):
        """发送 pong 帧（响应服务端 ping）"""
        if not self.alive:
            return False
        frame = self._encode_frame(OP_PONG, data, mask=True)
        try:
            with self._send_lock:
                self.sock.sendall(frame)
            return True
        except Exception:
            self.alive = False
            return False

    def send_close(self, code=1000, reason=""):
        """发送 close 帧"""
        payload = struct.pack(">H", code) + reason.encode("utf-8")
        frame = self._encode_frame(OP_CLOSE, payload, mask=True)
        try:
            with self._send_lock:
                self.sock.sendall(frame)
        except Exception:
            pass
        self.alive = False

    def close(self):
        """关闭连接"""
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass


# ============================================================
# 确认对话框（tkinter 优先，降级为控制台交互）
# ============================================================
class ConfirmDialog(object):
    """
    远程协助请求确认弹窗。
    优先用 tkinter，不可用时降级为控制台 input。
    弹窗在独立线程中运行，不阻塞 WS 接收。
    """

    @staticmethod
    def ask(d, callback):
        """
        弹出确认对话框。callback(result_dict) 会被调用。
        result_dict: {"accepted": bool, "reason": str}
        """
        t = threading.Thread(target=ConfirmDialog._ask_thread, args=(d, callback), daemon=True)
        t.start()

    @staticmethod
    def _ask_thread(d, callback):
        session_id = d.get("session_id", "")
        admin_name = d.get("admin_name", "未知")
        admin_dept = d.get("admin_dept", "")
        max_minutes = d.get("max_minutes", 60)
        can_control = d.get("can_control", False)
        msg = d.get("msg", "")

        try:
            import tkinter as tk
            from tkinter import ttk
            ConfirmDialog._tk_dialog(session_id, admin_name, admin_dept, max_minutes,
                                     can_control, msg, callback)
        except Exception:
            _log("tkinter 不可用，降级为控制台交互确认模式")
            ConfirmDialog._console_dialog(session_id, admin_name, admin_dept, max_minutes,
                                          can_control, msg, callback)

    @staticmethod
    def _tk_dialog(session_id, admin_name, admin_dept, max_minutes, can_control, msg, callback):
        """tkinter 置顶确认对话框"""
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("远程协助请求")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        root.grab_set()

        # 居中
        w, h = 480, 340
        sx = (root.winfo_screenwidth() - w) // 2
        sy = (root.winfo_screenheight() - h) // 2
        root.geometry("%dx%d+%d+%d" % (w, h, sx, sy))

        result = {"accepted": False, "reason": ""}

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="📢 远程协助请求", font=("", 14, "bold")).pack(anchor=tk.W)
        ttk.Separator(frame).pack(fill=tk.X, pady=8)

        info_text = "发起人：%s（%s）\n" % (admin_name, admin_dept or "未设置部门")
        info_text += "时长上限：%d 分钟\n" % max_minutes
        if can_control:
            info_text += "权限：可查看屏幕 + 操作鼠标键盘\n"
        else:
            info_text += "权限：仅查看屏幕\n"
        ttk.Label(frame, text=info_text, justify=tk.LEFT).pack(anchor=tk.W, pady=4)

        ttk.Label(frame, text=msg, wraplength=420, justify=tk.LEFT,
                  foreground="#cc0000").pack(anchor=tk.W, pady=4)

        ttk.Label(frame, text="⚠️ 对方将能看到你的整个屏幕！",
                  font=("", 10, "bold"), foreground="#cc0000").pack(anchor=tk.W, pady=4)

        reason_frame = ttk.Frame(frame)
        reason_frame.pack(fill=tk.X, pady=6)
        ttk.Label(reason_frame, text="拒绝理由（可选）：").pack(side=tk.LEFT)
        reason_var = tk.StringVar()
        reason_entry = ttk.Entry(reason_frame, textvariable=reason_var, width=30)
        reason_entry.pack(side=tk.LEFT, padx=4)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        def on_accept():
            result["accepted"] = True
            result["reason"] = ""
            root.destroy()

        def on_reject():
            result["accepted"] = False
            result["reason"] = reason_var.get().strip()
            root.destroy()

        reject_btn = ttk.Button(btn_frame, text="拒绝", command=on_reject, width=10)
        reject_btn.pack(side=tk.RIGHT, padx=8)
        accept_btn = ttk.Button(btn_frame, text="同意", command=on_accept, width=10)
        accept_btn.pack(side=tk.RIGHT, padx=8)

        root.protocol("WM_DELETE_WINDOW", on_reject)
        root.mainloop()
        callback(result)

    @staticmethod
    def _console_dialog(session_id, admin_name, admin_dept, max_minutes, can_control, msg, callback):
        """控制台降级确认"""
        print("")
        print("=" * 50)
        print("  【无图形界面降级模式】远程协助请求")
        print("=" * 50)
        print("  发起人：%s（%s）" % (admin_name, admin_dept or "未设置部门"))
        print("  时长上限：%d 分钟" % max_minutes)
        if can_control:
            print("  权限：可查看屏幕 + 操作鼠标键盘")
        else:
            print("  权限：仅查看屏幕")
        print("  消息：%s" % msg)
        print("  ⚠️  对方将能看到你的整个屏幕！")
        print("-" * 50)
        try:
            choice = input("  是否同意？(y/n): ").strip().lower()
        except EOFError:
            choice = "n"
        accepted = choice in ("y", "yes", "是")
        reason = ""
        if not accepted:
            try:
                reason = input("  拒绝理由（可选，直接回车跳过）：").strip()
            except EOFError:
                pass
        print("=" * 50)
        callback({"accepted": accepted, "reason": reason})


# ============================================================
# 悬浮提示条（协助进行中显示，可随时断开）
# ============================================================
class FloatingBar(object):
    """协助进行中的悬浮提示条，含「立即断开」按钮"""

    def __init__(self):
        self.root = None
        self.label = None
        self._thread = None
        self._stop_event = threading.Event()
        self._disconnect_cb = None

    def show(self, peer_name, can_control, on_disconnect):
        """显示悬浮条"""
        self._disconnect_cb = on_disconnect
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(peer_name, can_control), daemon=True)
        self._thread.start()

    def update_text(self, text):
        """更新显示文字（线程安全）"""
        if self.root and self.label:
            try:
                self.label.after(0, lambda: self.label.config(text=text))
            except Exception:
                pass

    def hide(self):
        """隐藏悬浮条"""
        self._stop_event.set()
        if self.root:
            try:
                self.root.after(0, self.root.destroy)
            except Exception:
                pass
        self.root = None
        self.label = None

    def _run(self, peer_name, can_control):
        try:
            import tkinter as tk
            root = tk.Tk()
            self.root = root
            root.title("远程协助中")
            root.attributes("-topmost", True)
            root.resizable(False, False)
            # 小窗口，右上角
            w, h = 320, 60
            sx = root.winfo_screenwidth() - w - 20
            sy = 20
            root.geometry("%dx%d+%d+%d" % (w, h, sx, sy))
            root.overrideredirect(False)

            frame = tk.Frame(root, bg="#fff3cd", padx=10, pady=5)
            frame.pack(fill=tk.BOTH, expand=True)

            ctrl_text = "并可操作鼠标键盘" if can_control else ""
            text = "%s 正在查看您的屏幕%s" % (peer_name, ctrl_text)
            self.label = tk.Label(frame, text=text, bg="#fff3cd", fg="#856404",
                                  font=("", 9), anchor=tk.W)
            self.label.pack(side=tk.LEFT, fill=tk.X, expand=True)

            def on_disconnect():
                self.hide()
                if self._disconnect_cb:
                    self._disconnect_cb()

            btn = tk.Button(frame, text="立即断开", bg="#dc3545", fg="white",
                            font=("", 9, "bold"), command=on_disconnect, padx=8)
            btn.pack(side=tk.RIGHT)

            root.protocol("WM_DELETE_WINDOW", on_disconnect)

            # 主循环直到停止
            while not self._stop_event.is_set():
                try:
                    root.update()
                    time.sleep(0.1)
                except Exception:
                    break
        except Exception:
            # tkinter 不可用，忽略
            pass


# ============================================================
# 截屏推流器
# ============================================================
class ScreenStreamer(object):
    """
    截屏推流线程：按指定帧率截屏、压缩、通过 WS 发送二进制帧。
    包含画面变化检测，无变化时降频以节省带宽。
    """

    def __init__(self, ws, fps, quality, screen_w, screen_h):
        self.ws = ws
        self.fps = max(1, min(fps, 30))  # 限制 1~30
        self.quality = max(10, min(quality, 95))
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._stop_event = threading.Event()
        self._thread = None
        self._last_thumb = None  # 上一帧缩略图用于变化检测
        self._max_long_edge = 1280  # 长边不超过此值

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ScreenStreamer")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._last_thumb = None

    def _capture_screen(self):
        """用 mss 抓取主屏，返回 PIL Image"""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # 主屏
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img

    def _resize_for_send(self, img):
        """缩放到长边不超过 _max_long_edge"""
        w, h = img.size
        long_edge = max(w, h)
        if long_edge <= self._max_long_edge:
            return img
        scale = self._max_long_edge / float(long_edge)
        new_w = int(w * scale)
        new_h = int(h * scale)
        return img.resize((new_w, new_h), Image.LANCZOS)

    def _to_jpeg_bytes(self, img):
        """PIL Image → JPEG bytes"""
        buf = _io_mod.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()

    def _make_thumb(self, img):
        """生成极小缩略图用于变化检测"""
        thumb = img.resize((32, 18), Image.LANCZOS)
        return list(thumb.getdata())

    def _is_changed(self, thumb):
        """与上一帧缩略图比较，判断画面是否有明显变化"""
        if self._last_thumb is None:
            return True
        prev = self._last_thumb
        if len(prev) != len(thumb):
            return True
        diff_count = 0
        threshold = 15  # 单通道差异阈值
        max_diff_pixels = len(thumb) // 10  # 超过 10% 像素有变化才算"有变化"
        for i in range(len(thumb)):
            p = prev[i]
            c = thumb[i]
            if isinstance(p, tuple) and isinstance(c, tuple):
                if abs(p[0] - c[0]) > threshold or abs(p[1] - c[1]) > threshold or abs(p[2] - c[2]) > threshold:
                    diff_count += 1
            else:
                if abs(p - c) > threshold:
                    diff_count += 1
            if diff_count > max_diff_pixels:
                return True
        return False

    def _loop(self):
        """截屏推流主循环"""
        idle_fps = 1  # 无变化时的最低帧率
        idle_interval = 1.0 / idle_fps
        active_interval = 1.0 / self.fps
        consecutive_idle = 0

        while not self._stop_event.is_set():
            try:
                img = self._capture_screen()
                resized = self._resize_for_send(img)
                thumb = self._make_thumb(resized)
                changed = self._is_changed(thumb)
                self._last_thumb = thumb

                if changed:
                    consecutive_idle = 0
                    jpeg_data = self._to_jpeg_bytes(resized)
                    self.ws.send_binary(jpeg_data)
                    interval = active_interval
                else:
                    consecutive_idle += 1
                    # 即使无变化也定期发一帧保持画面同步
                    if consecutive_idle % max(1, int(self.fps / idle_fps)) == 0:
                        jpeg_data = self._to_jpeg_bytes(resized)
                        self.ws.send_binary(jpeg_data)
                    interval = idle_interval

                # 分段 sleep 以便及时响应 stop
                elapsed = 0.0
                step = 0.05
                while elapsed < interval and not self._stop_event.is_set():
                    time.sleep(min(step, interval - elapsed))
                    elapsed += step

            except Exception as e:
                if self._stop_event.is_set():
                    break
                _log("截屏异常：%s" % str(e))
                time.sleep(0.5)


# ============================================================
# 输入指令执行器
# ============================================================
class InputExecutor(object):
    """
    接收管理员下发的键鼠指令并在本机执行。
    坐标为 0~1 归一化浮点数，需换算为本机真实像素。
    """

    def __init__(self, screen_w, screen_h, control_enabled):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.control_enabled = control_enabled
        self._warned_no_control = False

    def execute(self, d):
        """执行一条输入指令"""
        if not self.control_enabled:
            if not self._warned_no_control:
                _log("收到输入指令但本机未开启被控（--no-control 或缺少 pyautogui），已忽略")
                self._warned_no_control = True
            return

        if not _HAS_PYAUTOGUI:
            return

        k = d.get("k", "")
        try:
            if k == "move":
                self._do_move(d)
            elif k == "down":
                self._do_down(d)
            elif k == "up":
                self._do_up(d)
            elif k == "click":
                self._do_click(d)
            elif k == "scroll":
                self._do_scroll(d)
            elif k == "key":
                self._do_key(d)
            elif k == "text":
                self._do_text(d)
            else:
                _log("未知输入类型：%s" % k)
        except pyautogui.FailSafeException:
            # 安全阀触发：鼠标甩到左上角
            raise  # 向上抛出，由调用方处理
        except Exception as e:
            _log("输入指令执行失败（%s）：%s" % (k, str(e)))

    def _to_px(self, x, y):
        """归一化坐标 → 本机像素"""
        px = int(float(x) * self.screen_w)
        py = int(float(y) * self.screen_h)
        # 钳制到合法范围
        px = max(0, min(px, self.screen_w - 1))
        py = max(0, min(py, self.screen_h - 1))
        return px, py

    def _do_move(self, d):
        px, py = self._to_px(d.get("x", 0), d.get("y", 0))
        pyautogui.moveTo(px, py, _pause=False)

    def _do_down(self, d):
        button = d.get("button", "left")
        pyautogui.mouseDown(button=button, _pause=False)

    def _do_up(self, d):
        button = d.get("button", "left")
        pyautogui.mouseUp(button=button, _pause=False)

    def _do_click(self, d):
        px, py = self._to_px(d.get("x", 0), d.get("y", 0))
        clicks = int(d.get("clicks", 1))
        button = d.get("button", "left")
        pyautogui.click(px, py, clicks=clicks, button=button, _pause=False)

    def _do_scroll(self, d):
        # 先移动到指定位置
        if "x" in d and "y" in d:
            px, py = self._to_px(d.get("x", 0), d.get("y", 0))
            pyautogui.moveTo(px, py, _pause=False)
        dy = int(d.get("dy", 0))
        if dy != 0:
            pyautogui.scroll(dy, _pause=False)

    def _do_key(self, d):
        key = d.get("key", "")
        down = d.get("down", None)
        if "+" in key:
            # 组合键，如 ctrl+c
            parts = [p.strip() for p in key.split("+")]
            if down is True:
                for p in parts:
                    pyautogui.keyDown(p, _pause=False)
            elif down is False:
                for p in reversed(parts):
                    pyautogui.keyUp(p, _pause=False)
            else:
                # press：按下再释放
                for p in parts:
                    pyautogui.keyDown(p, _pause=False)
                for p in reversed(parts):
                    pyautogui.keyUp(p, _pause=False)
        else:
            if down is True:
                pyautogui.keyDown(key, _pause=False)
            elif down is False:
                pyautogui.keyUp(key, _pause=False)
            else:
                pyautogui.press(key, _pause=False)

    def _do_text(self, d):
        text = d.get("text", "")
        if not text:
            return
        # 检查是否包含非 ASCII 字符
        has_non_ascii = False
        for ch in text:
            if ord(ch) > 127:
                has_non_ascii = True
                break
        if has_non_ascii:
            # typewrite 不支持中文，降级为剪贴板粘贴
            self._paste_text(text)
        else:
            try:
                pyautogui.typewrite(text, _pause=False)
            except Exception as e:
                _log("typewrite 失败，尝试剪贴板粘贴：%s" % str(e))
                self._paste_text(text)

    def _paste_text(self, text):
        """通过剪贴板 + Ctrl+V 粘贴文本"""
        pasted = False
        # 尝试用 tkinter 剪贴板
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
            pasted = True
        except Exception:
            pass
        # 尝试 pyperclip（不在允许列表中，但作为可选辅助）
        if not pasted:
            try:
                import pyperclip
                pyperclip.copy(text)
                pasted = True
            except ImportError:
                pass
            except Exception:
                pass
        if pasted:
            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "v", _pause=False)
        else:
            _log("警告：无法粘贴非 ASCII 文本（缺少 tkinter 和 pyperclip），已跳过")


# ============================================================
# 主控制器
# ============================================================
class RemoteAgent(object):
    """被控端主控类，编排 WS 通信、状态管理、截屏、输入"""

    # 状态常量
    STATE_DISCONNECTED = "未连接"
    STATE_CONNECTED = "已连接"
    STATE_REGISTERED = "已注册待命"
    STATE_ACTIVE = "协助进行中"

    def __init__(self, server_url, token, note="", fps_override=0, no_control=False):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.note = note
        self.fps_override = fps_override
        self.no_control = no_control

        self.ws = WSClient()
        self.state = self.STATE_DISCONNECTED
        self.user_info = None
        self.session_id = None
        self.peer_name = ""
        self.can_control = False
        self.session_start_time = 0

        self.streamer = None
        self.input_executor = None
        self.floating_bar = FloatingBar()

        self._recv_thread = None
        self._heartbeat_thread = None
        self._stop_event = threading.Event()
        self._reconnect_delay = 1

        # 屏幕信息
        self.screen_w = 1920
        self.screen_h = 1080
        self.monitors = 1
        self._detect_screen()

    def _detect_screen(self):
        """探测主屏分辨率"""
        if _HAS_MSS:
            try:
                with mss.mss() as sct:
                    mon = sct.monitors[1]  # 主屏
                    self.screen_w = mon["width"]
                    self.screen_h = mon["height"]
                    self.monitors = len(sct.monitors) - 1  # monitors[0] 是虚拟全屏
            except Exception as e:
                _log("探测屏幕分辨率失败，使用默认值：%s" % str(e))
        else:
            _log("mss 不可用，使用默认分辨率 %dx%d" % (self.screen_w, self.screen_h))

    def _set_state(self, new_state, extra=""):
        """更新并打印状态"""
        old = self.state
        self.state = new_state
        if extra:
            _log("状态变更：%s → %s（%s）" % (old, new_state, extra))
        else:
            _log("状态变更：%s → %s" % (old, new_state))

    # ---------- 登录与信息获取 ----------
    def login(self, username, password):
        """HTTP 登录获取 token"""
        url = self.server_url + "/api/auth/login"
        _log("正在登录...")
        resp, code = _http_json(url, {"username": username, "password": password})
        if not resp.get("ok"):
            msg = resp.get("msg", "未知错误")
            _log("登录失败：%s" % msg)
            return False
        self.token = resp["data"]["token"]
        _log("登录成功")
        return True

    def fetch_user_info(self):
        """获取当前用户信息"""
        url = self.server_url + "/api/auth/me"
        resp, code = _http_json(url, method="GET", token=self.token)
        if not resp.get("ok"):
            _log("获取用户信息失败：%s" % resp.get("msg", "未知错误"))
            return False
        user = resp["data"].get("user", {})
        self.user_info = user
        name = user.get("display_name", user.get("username", ""))
        role = user.get("role", "")
        _log("已登录：%s（%s）" % (name, role))
        return True

    # ---------- WebSocket 连接与认证 ----------
    def _build_ws_url(self):
        """根据 server_url 构建 ws/wss URL"""
        if self.server_url.startswith("https://"):
            return "wss://" + self.server_url[8:] + "/ws"
        elif self.server_url.startswith("http://"):
            return "ws://" + self.server_url[7:] + "/ws"
        else:
            return "ws://" + self.server_url + "/ws"

    def _connect_ws(self):
        """建立 WS 连接并完成认证+注册"""
        ws_url = self._build_ws_url()
        _log("正在连接 %s ..." % ws_url)
        self.ws = WSClient()
        self.ws.connect(ws_url)
        self._set_state(self.STATE_CONNECTED)

        # 发送认证帧
        auth_msg = json.dumps({"t": "auth", "d": {"token": self.token}}, ensure_ascii=False)
        self.ws.send_text(auth_msg)

        # 等待认证响应（最多等 10 秒）
        deadline = time.time() + 10
        while time.time() < deadline and not self._stop_event.is_set():
            opcode, payload = self.ws.recv_frame(timeout=2)
            if opcode is None:
                continue
            if opcode == OP_TEXT:
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("t") == "auth":
                    d = msg.get("d", {})
                    if d.get("ok"):
                        user = d.get("user", {})
                        name = user.get("display_name", user.get("username", ""))
                        _log("WS 认证成功：%s" % name)
                        break
                    else:
                        reason = d.get("msg", "认证失败")
                        _log("WS 认证失败：%s" % reason)
                        return False
                elif msg.get("t") == "pong":
                    continue
            elif opcode == OP_PING:
                self.ws.send_pong(payload)
            elif opcode == OP_CLOSE:
                _log("服务端发送了 close 帧")
                return False
        else:
            _log("WS 认证超时")
            return False

        # 注册为被控端
        device = socket.gethostname()
        try:
            os_name = platform.system()
            if os_name:
                device = "%s (%s)" % (device, os_name)
        except Exception:
            pass

        reg_data = {
            "device": device,
            "note": self.note,
            "screen_w": self.screen_w,
            "screen_h": self.screen_h,
            "monitors": self.monitors,
        }
        reg_msg = json.dumps({"t": "remote_reg", "d": reg_data}, ensure_ascii=False)
        self.ws.send_text(reg_msg)

        # 等待注册响应
        deadline = time.time() + 10
        while time.time() < deadline and not self._stop_event.is_set():
            opcode, payload = self.ws.recv_frame(timeout=2)
            if opcode is None:
                continue
            if opcode == OP_TEXT:
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("t") == "remote_reg":
                    d = msg.get("d", {})
                    if d.get("ok"):
                        _log("注册成功：%s" % d.get("msg", "已注册"))
                        self._set_state(self.STATE_REGISTERED)
                        self._reconnect_delay = 1  # 重置退避
                        return True
                    else:
                        err_msg = d.get("msg", "注册失败")
                        _log("注册失败：%s" % err_msg)
                        if "未开启" in err_msg:
                            _log("远程协助功能未开启，请联系管理员后再试。")
                            return False
                        return False
                elif msg.get("t") == "pong":
                    continue
            elif opcode == OP_PING:
                self.ws.send_pong(payload)
            elif opcode == OP_CLOSE:
                _log("服务端发送了 close 帧")
                return False

        _log("注册响应超时")
        return False

    # ---------- 消息处理 ----------
    def _handle_message(self, msg):
        """处理收到的文本消息"""
        t = msg.get("t", "")
        d = msg.get("d", {})

        if t == "hello" or t == "pong":
            return

        elif t == "remote_ask":
            self._on_remote_ask(d)

        elif t == "remote_start":
            self._on_remote_start(d)

        elif t == "remote_input":
            self._on_remote_input(d)

        elif t == "remote_end":
            self._on_remote_end(d)

        elif t == "remote_notice":
            text = d.get("text", "")
            _log("通知：%s" % text)

        elif t == "banned":
            reason = d.get("msg", d.get("reason", "被封禁"))
            _log("账号被封禁：%s" % reason)
            self._stop_event.set()

        elif t == "auth":
            # 重连后的认证响应，已在 _connect_ws 中处理
            pass

        elif t == "remote_reg":
            # 重连后的注册响应，已在 _connect_ws 中处理
            pass

        else:
            _log("收到未知消息类型：%s" % t)

    def _on_remote_ask(self, d):
        """收到协助请求 → 弹窗确认"""
        _log("收到远程协助请求")

        def on_result(result):
            if result["accepted"]:
                accept_msg = json.dumps({
                    "t": "remote_accept",
                    "d": {"session_id": d["session_id"]}
                }, ensure_ascii=False)
                self.ws.send_text(accept_msg)
                _log("已同意协助请求")
            else:
                reject_msg = json.dumps({
                    "t": "remote_reject",
                    "d": {"session_id": d["session_id"], "reason": result.get("reason", "")}
                }, ensure_ascii=False)
                self.ws.send_text(reject_msg)
                _log("已拒绝协助请求")

        ConfirmDialog.ask(d, on_result)

    def _on_remote_start(self, d):
        """协助开始 → 启动截屏推流"""
        self.session_id = d.get("session_id", "")
        self.peer_name = d.get("peer_name", "未知")
        fps = d.get("fps", 6)
        quality = d.get("quality", 55)
        max_minutes = d.get("max_minutes", 60)
        expires_at = d.get("expires_at", 0)

        if self.fps_override > 0:
            fps = min(fps, self.fps_override)

        control_enabled = (not self.no_control) and _HAS_PYAUTOGUI
        self.can_control = control_enabled

        self.session_start_time = time.time()
        self._set_state(self.STATE_ACTIVE, "对端：%s，帧率：%d，质量：%d" % (self.peer_name, fps, quality))

        # 启动输入执行器
        self.input_executor = InputExecutor(self.screen_w, self.screen_h, control_enabled)

        # 启动截屏推流
        self.streamer = ScreenStreamer(self.ws, fps, quality, self.screen_w, self.screen_h)
        self.streamer.start()

        # 显示悬浮提示条
        def on_disconnect():
            self._user_disconnect()

        self.floating_bar.show(self.peer_name, d.get("control", control_enabled), on_disconnect)

        _log("协助已开始：%s 正在查看您的屏幕（最长 %d 分钟）" % (self.peer_name, max_minutes))

    def _on_remote_input(self, d):
        """收到输入指令"""
        if self.input_executor:
            try:
                self.input_executor.execute(d)
            except Exception as e:
                # 检查是否是 FailSafeException
                if _HAS_PYAUTOGUI and isinstance(e, pyautogui.FailSafeException):
                    _log("⚠️ 已通过左上角紧急中断！（FailSafe 触发）")
                    self._emergency_stop("用户通过左上角紧急中断")
                else:
                    _log("输入执行异常：%s" % str(e))

    def _on_remote_end(self, d):
        """会话结束"""
        reason = d.get("reason", "未知原因")
        by_peer = d.get("by_peer", False)
        duration = d.get("duration", 0)

        self._stop_streaming()

        dur_min = int(duration / 60) if duration else 0
        who = "对方" if by_peer else "系统"
        _log("协助已结束：%s结束了会话，原因：%s，时长：%d 分钟" % (who, reason, dur_min))

        self.session_id = None
        self.peer_name = ""
        self._set_state(self.STATE_REGISTERED, "回到待命状态")

    def _user_disconnect(self):
        """用户主动断开"""
        if self.session_id:
            end_msg = json.dumps({
                "t": "remote_end",
                "d": {"session_id": self.session_id, "reason": "被控端用户主动断开"}
            }, ensure_ascii=False)
            self.ws.send_text(end_msg)
        self._stop_streaming()
        self.session_id = None
        self.peer_name = ""
        self._set_state(self.STATE_REGISTERED, "用户主动断开")

    def _emergency_stop(self, reason):
        """紧急停止（FailSafe 等）"""
        if self.session_id:
            end_msg = json.dumps({
                "t": "remote_end",
                "d": {"session_id": self.session_id, "reason": reason}
            }, ensure_ascii=False)
            self.ws.send_text(end_msg)
        self._stop_streaming()
        self.session_id = None
        self.peer_name = ""
        self._set_state(self.STATE_REGISTERED, reason)

    def _stop_streaming(self):
        """停止截屏和悬浮条"""
        if self.streamer:
            self.streamer.stop()
            self.streamer = None
        self.floating_bar.hide()
        self.input_executor = None

    # ---------- 接收线程 ----------
    def _recv_loop(self):
        """WS 消息接收主循环"""
        while not self._stop_event.is_set():
            if not self.ws.alive:
                break
            opcode, payload = self.ws.recv_frame(timeout=2)
            if opcode is None:
                if not self.ws.alive:
                    break
                continue
            try:
                if opcode == OP_TEXT:
                    msg = json.loads(payload.decode("utf-8"))
                    self._handle_message(msg)
                elif opcode == OP_BIN:
                    # 被控端不应收到二进制帧，忽略
                    pass
                elif opcode == OP_PING:
                    self.ws.send_pong(payload)
                elif opcode == OP_PONG:
                    pass  # 心跳响应，忽略
                elif opcode == OP_CLOSE:
                    _log("收到服务端 close 帧")
                    self.ws.send_close()
                    break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                _log("消息处理异常：%s" % str(e))

    # ---------- 心跳线程 ----------
    def _heartbeat_loop(self):
        """每 25 秒发一次 ping 保活"""
        while not self._stop_event.is_set():
            time.sleep(25)
            if self._stop_event.is_set():
                break
            if self.ws.alive:
                ping_msg = json.dumps({"t": "ping", "d": {}}, ensure_ascii=False)
                self.ws.send_text(ping_msg)
                # 同时发 WS 层 ping
                self.ws.send_ping()

    # ---------- 状态刷新线程 ----------
    def _status_loop(self):
        """协助进行中时定期刷新控制台状态"""
        while not self._stop_event.is_set():
            time.sleep(5)
            if self._stop_event.is_set():
                break
            if self.state == self.STATE_ACTIVE and self.session_start_time > 0:
                elapsed = int(time.time() - self.session_start_time)
                minutes = elapsed // 60
                seconds = elapsed % 60
                bar_text = "%s 正在查看您的屏幕（已持续 %d:%02d）" % (
                    self.peer_name, minutes, seconds)
                self.floating_bar.update_text(bar_text)

    # ---------- 主运行循环（含断线重连）----------
    def run(self):
        """主入口：连接、认证、注册、接收消息，断线自动重连"""
        # 获取用户信息
        if not self.fetch_user_info():
            _log("无法获取用户信息，退出")
            return

        # 启动心跳和状态刷新线程
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="Heartbeat")
        self._heartbeat_thread.start()
        status_thread = threading.Thread(target=self._status_loop, daemon=True, name="StatusRefresh")
        status_thread.start()

        while not self._stop_event.is_set():
            try:
                if self._connect_ws():
                    # 连接成功，启动接收线程
                    self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="WSRecv")
                    self._recv_thread.start()
                    self._recv_thread.join()  # 阻塞直到接收线程退出

                if self._stop_event.is_set():
                    break

                # 连接断开，准备重连
                self._stop_streaming()
                self.ws.close()
                self._set_state(self.STATE_DISCONNECTED)

                if self._stop_event.is_set():
                    break

                _log("连接断开，%d 秒后重连..." % self._reconnect_delay)
                # 分段 sleep 以便响应 Ctrl+C
                waited = 0
                while waited < self._reconnect_delay and not self._stop_event.is_set():
                    time.sleep(0.5)
                    waited += 0.5

                # 指数退避，最长 10 秒
                self._reconnect_delay = min(self._reconnect_delay * 2, 10)

            except KeyboardInterrupt:
                break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                _log("连接异常：%s" % str(e))
                self._set_state(self.STATE_DISCONNECTED)
                time.sleep(min(self._reconnect_delay, 10))
                self._reconnect_delay = min(self._reconnect_delay * 2, 10)

        # 清理
        self._cleanup()

    def _cleanup(self):
        """干净退出"""
        _log("正在清理资源...")
        self._stop_event.set()
        self._stop_streaming()
        if self.session_id:
            try:
                end_msg = json.dumps({
                    "t": "remote_end",
                    "d": {"session_id": self.session_id, "reason": "被控端程序退出"}
                }, ensure_ascii=False)
                self.ws.send_text(end_msg)
            except Exception:
                pass
        try:
            self.ws.send_close()
        except Exception:
            pass
        self.ws.close()
        _log("已退出")

    def stop(self):
        """外部调用停止"""
        self._stop_event.set()


# ============================================================
# 命令行入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="企业学习聊天系统 - 被控端程序",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python remote_agent.py --server http://10.0.0.5:8080 --username zhang --password xxx
  python remote_agent.py --server http://10.0.0.5:8080   （交互式输入密码）
  python remote_agent.py --token <已有token> --server http://10.0.0.5:8080
        """
    )
    parser.add_argument("--server", default="", help="主服务地址，如 http://10.0.0.5:8080")
    parser.add_argument("--username", default="", help="登录用户名")
    parser.add_argument("--password", default="", help="登录密码（不建议明文传入，推荐交互式输入）")
    parser.add_argument("--token", default="", help="直接使用已有 token，跳过登录")
    parser.add_argument("--note", default="", help="备注文字，显示在管理员端列表")
    parser.add_argument("--fps", type=int, default=0, help="覆盖服务端建议帧率上限")
    parser.add_argument("--no-control", action="store_true", help="强制禁用被控，只共享画面")

    args = parser.parse_args()

    # 确定 server 地址
    server = args.server
    if not server:
        server = os.environ.get("LC_SERVER", "")
    if not server:
        print("错误：必须指定 --server 参数或设置 LC_SERVER 环境变量")
        print("用法：python remote_agent.py --server http://主机:端口 [--username 用户名]")
        sys.exit(1)

    # 确定 token
    token = args.token
    if not token:
        username = args.username
        password = args.password
        if not username:
            try:
                username = input("用户名：").strip()
            except EOFError:
                print("错误：未提供用户名")
                sys.exit(1)
        if not username:
            print("错误：用户名不能为空")
            sys.exit(1)
        if not password:
            try:
                password = getpass.getpass("密码：")
            except EOFError:
                print("错误：未提供密码")
                sys.exit(1)

        # 降级模式检查
        no_control = args.no_control
        if not _HAS_PYAUTOGUI and not no_control:
            _log("pyautogui 不可用，自动启用 --no-control 降级模式")
            no_control = True

        agent = RemoteAgent(server, "", note=args.note, fps_override=args.fps,
                            no_control=no_control)
        if not agent.login(username, password):
            sys.exit(1)
        token = agent.token
    else:
        # 直接用 token，仍需验证
        pass

    no_control = args.no_control
    if not _HAS_PYAUTOGUI and not no_control:
        _log("pyautogui 不可用，自动启用 --no-control 降级模式")
        no_control = True

    agent = RemoteAgent(server, token, note=args.note, fps_override=args.fps,
                        no_control=no_control)

    # 注册信号处理
    def sig_handler(sig, frame):
        _log("收到中断信号，正在退出...")
        agent.stop()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    _log("被控端程序启动")
    _log("服务器：%s" % server)
    if no_control:
        _log("模式：仅共享画面（不接受键鼠控制）")
    else:
        _log("模式：共享画面 + 接受键鼠控制")

    try:
        agent.run()
    except KeyboardInterrupt:
        _log("收到 Ctrl+C，正在退出...")
        agent.stop()
    except Exception as e:
        _log("致命错误：%s" % str(e))
        traceback.print_exc()
        agent.stop()


if __name__ == "__main__":
    main()
