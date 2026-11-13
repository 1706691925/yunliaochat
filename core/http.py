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
HTTP 服务 v2 —— 重构版引擎
- 自写 socket HTTP/1.1 解析（替代 http.server，头部解析更快、无每请求线程开销）
- 线程池承载连接 + keep-alive 复用（静态资源/API 少 TCP 握手）
- WebSocket 升级后连接移交独立线程，不占用池线程
- 域名绑定：配置 server_names 后按 Host 头校验（未绑定域名直接拒绝）
对外契约与 v1 完全一致：ROUTES / route() / Ctx / ApiError / NoPerm / Server / Handler
"""
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from core import config, db, ipsec, realtime, security, util, ws

ROUTES = []          # (method, path, func, perm, desc)
WS_HANDLER = None

_REASONS = {200: "OK", 201: "Created", 204: "No Content", 206: "Partial Content",
            301: "Moved Permanently", 302: "Found", 304: "Not Modified",
            400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
            500: "Internal Server Error"}


def route(path, method="GET", perm=None, desc=""):
    def deco(func):
        ROUTES.append((method, path, func, perm, desc))
        return func
    return deco


class Ctx(object):
    def __init__(self, handler, body, query, files, user, ip):
        self.handler = handler
        self.body = body or {}
        self.query = query or {}
        self.files = files or []
        self.user = user
        self.ip = ip
        self.path = handler.path.split("?")[0]
        self.headers = handler.headers
        self.args = {}

    def get(self, key, default=None):
        if key in self.body and self.body[key] not in (None, ""):
            return self.body[key]
        if key in self.query and self.query[key] not in (None, ""):
            return self.query[key]
        return default

    @property
    def token(self):
        """当前请求的会话令牌（HTTP 头统一小写存储，这里兼容两种写法）"""
        h = self.headers or {}
        return h.get("x-token") or h.get("X-Token") or ""

    def int(self, key, default=0):
        try:
            return int(self.get(key, default))
        except Exception:
            return default

    def require(self, key, name=None):
        v = self.get(key)
        if v in (None, ""):
            raise ApiError("缺少必填参数：%s" % (name or key))
        return v


class ApiError(Exception):
    def __init__(self, msg, code=400):
        Exception.__init__(self, msg)
        self.msg = msg
        self.code = code


class NoPerm(Exception):
    pass


MIME_MAP = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
    ".swf": "application/x-shockwave-flash",
}


def guess_mime(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in MIME_MAP:
        return MIME_MAP[ext]
    t = mimetypes.guess_type(path)[0]
    return (t or "application/octet-stream") + ("; charset=utf-8" if t and t.startswith("text") else "")


def parse_content_type(value):
    """解析 Content-Type，返回 (主类型小写, 参数字典)。替代已废弃的 cgi.parse_header"""
    parts = (value or "").split(";")
    main = parts[0].strip().lower()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().lower()] = v.strip().strip('"')
    return main, params


# ---------------- 域名绑定 ----------------
def _local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return ips


def host_allowed(host_header):
    """域名绑定：server_names 为空=不限制；否则仅放行白名单域名/本机回环/本机网卡 IP。
    返回 (ok, 归一化 host)。"""
    names = str(config.CFG.get("server_names", "") or "").strip()
    if not names:
        return True, (host_header or "").split(":")[0]
    host = (host_header or "").split(":")[0].strip().lower()
    if host in ("127.0.0.1", "localhost", "::1", ""):
        return True, host
    wl = [n.strip().lower() for n in names.split(",") if n.strip()]
    if host in wl:
        return True, host
    if host in _local_ips():
        return True, host
    return False, host


# ---------------- 流式输入包装 ----------------
class _StreamReader(object):
    """把 Handler 的手动缓冲暴露为 file-like（支持 read/read1，供 multipart 流式解析）。
    read1 遵循"有数据即返回、最多 n 字节"语义（不等满），否则大文件尾部会与客户端发完
    的 body 死锁。"""

    def __init__(self, h):
        self.h = h

    def read(self, n=-1):
        return self.h._read_bytes(n if n > 0 else 65536)

    def read1(self, n=-1):
        return self.h._read_some(n if n > 0 else 65536)


class Handler(object):
    server_version = "YunLiao/2.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, conn, addr, server):
        self.connection = conn
        self.client_address = addr
        self.server = server
        self.command = None
        self.path = None
        self.headers = {}
        self.close_connection = False
        self._rbuf = b""
        self._in_ws = False
        self._status = 200
        self._hbuf = []

    # ---------- 底层 IO ----------
    def _read_bytes(self, n):
        while len(self._rbuf) < n:
            chunk = self.connection.recv(min(n - len(self._rbuf), 65536))
            if not chunk:
                raise EOFError("connection closed")
            self._rbuf += chunk
        out, self._rbuf = self._rbuf[:n], self._rbuf[n:]
        return out

    def _read_some(self, n):
        """有数据即返回（最多 n 字节），不阻塞等满；用于流式读取（multipart 大文件）。"""
        if not self._rbuf:
            chunk = self.connection.recv(min(n, 65536))
            if not chunk:
                raise EOFError("connection closed")
            self._rbuf += chunk
        take = min(n, len(self._rbuf))
        out, self._rbuf = self._rbuf[:take], self._rbuf[take:]
        return out

    def _readline_limited(self, limit=16384):
        while True:
            idx = self._rbuf.find(b"\r\n")
            if idx >= 0:
                line, self._rbuf = self._rbuf[:idx + 2], self._rbuf[idx + 2:]
                return line
            if len(self._rbuf) > limit:
                raise ValueError("header too long")
            chunk = self.connection.recv(65536)
            if not chunk:
                return b""
            self._rbuf += chunk

    def _parse_request(self):
        line = self._readline_limited()
        if not line:
            return False
        try:
            parts = line.decode("latin-1").strip().split(" ")
        except Exception:
            return False
        if len(parts) < 3:
            return False
        self.command, self.path = parts[0].upper(), parts[1]
        self._http_version = parts[2]
        headers = {}
        while True:
            hl = self._readline_limited()
            if hl in (b"", b"\r\n"):
                break
            try:
                s = hl.decode("latin-1").rstrip("\r\n")
            except Exception:
                continue
            if ":" not in s:
                continue
            k, v = s.split(":", 1)
            headers[k.strip().lower()] = v.strip()
        self.headers = headers
        return True

    # ---------- 响应 ----------
    def send_response(self, code, msg=None):
        self._status = code
        self._reason = msg or _REASONS.get(code, "")
        self._hbuf = ["HTTP/1.1 %d %s\r\n" % (code, self._reason)]

    def send_header(self, k, v):
        self._hbuf.append("%s: %s\r\n" % (k, v))

    def end_headers(self):
        self._hbuf.append("\r\n")
        try:
            self.connection.sendall("".join(self._hbuf).encode("latin-1"))
        except Exception:
            raise EOFError("peer closed")

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None, head_only=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        if getattr(self, "command", "") == "HEAD":
            head_only = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only and body:
            self.connection.sendall(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _redirect(self, loc):
        self._send(302, b"", ctype="text/plain", extra={"Location": loc})

    def _send_stream(self, code, path, ctype="application/octet-stream", extra=None):
        """流式发送文件：不把大文件整体读入内存（头像/聊天文件/客户端下载）"""
        size = os.path.getsize(path)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self._responded = True
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.connection.sendall(chunk)

    # ---------- 请求解析 ----------
    def _read_multipart(self):
        ctype = self.headers.get("content-type", "")
        if not ctype.lower().startswith("multipart/form-data"):
            return {}, []
        main, params = parse_content_type(ctype)
        try:
            pdict = {"boundary": params["boundary"].encode(),
                     "CONTENT-LENGTH": int(self.headers.get("content-length", 0))}
        except Exception:
            return {}, []
        import core.multipart as mp
        try:
            return mp.parse(_StreamReader(self), pdict["boundary"], pdict["CONTENT-LENGTH"])
        except Exception:
            traceback.print_exc()
            return {}, []

    def _parse_body(self):
        length = int(self.headers.get("content-length") or 0)
        ctype = (self.headers.get("content-type") or "").lower()
        if length <= 0:
            return {}, []
        if ctype.startswith("multipart/form-data"):
            return self._read_multipart()
        try:
            raw = self._read_bytes(length)
        except Exception:
            return {}, []
        if not raw:
            return {}, []
        if "json" in ctype:
            try:
                return json.loads(raw.decode("utf-8")), []
            except Exception:
                return {}, []
        try:
            q = urllib.parse.parse_qs(raw.decode("utf-8", "ignore"))
            return {k: v[0] for k, v in q.items()}, []
        except Exception:
            return {}, []

    def _current_user(self):
        from core import app_ctx
        token = self.headers.get("x-token") or ""
        if not token:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            token = (q.get("token") or [""])[0]
        if not token:
            cookie = self.headers.get("cookie") or ""
            for part in cookie.split(";"):
                if part.strip().startswith("token="):
                    token = part.strip()[6:]
        if not token:
            return None
        return app_ctx.APP.auth_user_by_token(token, self._ip())

    # ---------- 日志 / 访问控制 ----------
    def log_message(self, fmt, *args):
        try:
            from core import app_ctx
            if app_ctx.APP and getattr(app_ctx.APP, "quiet", False):
                return
        except Exception:
            pass
        try:
            sys.stderr.write("[%s] %s - %s\n" % (util.now_str(), self.address_string(), fmt % args))
        except Exception:
            pass

    def address_string(self):
        return "%s:%s" % (self.client_address[0], self.client_address[1])

    def _ip(self):
        """默认只信真实连接地址；X-Real-IP 头仅在配置 trust_real_ip 开启（反代部署）时才采用"""
        if (self.headers.get("x-real-ip") and
                str(config.CFG.get("trust_real_ip", "")) in ("1", "true", "True")):
            return self.headers.get("x-real-ip")
        return self.client_address[0]

    def _ip_allowed(self):
        wl = str(config.CFG.get("ip_whitelist") or "").strip()
        if not wl:
            return False
        ip = self._ip()
        if ipsec.allowed(ip, wl):
            return False
        try:
            body = ("<html><head><meta charset='utf-8'><title>403</title></head><body "
                    "style='font-family:sans-serif;padding:48px;text-align:center'>"
                    "<h2>⛔ 访问被拒绝</h2><p>该 IP（%s）不在系统访问白名单内。</p>"
                    "<p style='color:#888'>如需恢复访问，请联系系统管理员将你的 IP 加入白名单。</p></body></html>"
                    % ip.replace("<", "&lt;").replace(">", "&gt;")).encode("utf-8")
            self._send(403, body, ctype="text/html; charset=utf-8")
        except Exception:
            pass
        return True

    def _ip_blocked(self):
        ip = self._ip()
        hit = ipsec.blocked(ip)
        if not hit:
            return False
        rid, raw = hit
        ipsec.touch_hit(rid)
        try:
            body = ("<html><head><meta charset='utf-8'><title>403</title></head><body "
                    "style='font-family:sans-serif;padding:48px;text-align:center'>"
                    "<h2>⛔ 访问被拒绝</h2><p>该 IP（%s）已被管理员列入内网黑名单。</p>"
                    "<p style='color:#888'>如需恢复访问，请联系系统管理员。</p></body></html>"
                    % ip.replace("<", "&lt;").replace(">", "&gt;")).encode("utf-8")
            self._send(403, body, ctype="text/html; charset=utf-8")
        except Exception:
            pass
        return True

    def _host_check(self):
        """域名绑定总闸：配置 server_names 后，未绑定的 Host 直接拒绝"""
        ok, host = host_allowed(self.headers.get("host", ""))
        if ok:
            return False
        try:
            body = ("<html><head><meta charset='utf-8'><title>400</title></head><body "
                    "style='font-family:sans-serif;padding:48px;text-align:center'>"
                    "<h2>🌐 域名未绑定</h2><p>当前访问域名 <code>%s</code> 未在服务器绑定名单内。</p>"
                    "<p style='color:#888'>请在服务器管理界面配置「绑定域名」，或使用服务器 IP/内网地址访问。</p>"
                    "</body></html>" % host.replace("<", "&lt;").replace(">", "&gt;")).encode("utf-8")
            self._send(400, body, ctype="text/html; charset=utf-8")
        except Exception:
            pass
        return True

    # ---------- 方法分发 ----------
    def do_GET(self):
        if self._ip_blocked() or self._ip_allowed():
            return
        self._dispatch("GET")

    def do_HEAD(self):
        if self._ip_blocked() or self._ip_allowed():
            return
        self._dispatch("HEAD")

    def do_POST(self):
        if self._ip_blocked() or self._ip_allowed():
            return
        self._dispatch("POST")

    def do_PUT(self):
        if self._ip_blocked() or self._ip_allowed():
            return
        self._dispatch("PUT")

    def do_DELETE(self):
        if self._ip_blocked() or self._ip_allowed():
            return
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self._send(204, b"", extra={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-Token",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        })

    def _ws_upgrade(self):
        if (self.headers.get("upgrade") or "").lower() != "websocket":
            return False
        key = self.headers.get("sec-websocket-key")
        if not key:
            return False
        resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % ws.accept_key(key))
        try:
            self.connection.sendall(resp.encode())
        except Exception:
            return False
        conn = ws.WSConn(self.connection, self.client_address, self.server, preset=self._rbuf)
        conn.handshake_done = True
        self._in_ws = True
        threading.Thread(target=self._ws_loop, args=(conn,), daemon=True).start()
        return True

    def _ws_loop(self, conn):
        try:
            self.connection.settimeout(None)
        except Exception:
            pass
        try:
            from core import app_ctx
            app_ctx.APP.on_ws_connect(conn, self.headers, self._ip())
        except Exception:
            traceback.print_exc()
        self.close_connection = True

    def _dispatch(self, method):
        if self._host_check():
            return
        path = self.path.split("?")[0]
        if path == "/ws" and method == "GET":
            if self._ws_upgrade():
                return
            self._send(400, b"websocket upgrade required", ctype="text/plain")
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query).items()}
        body, files = ({}, [])
        if method in ("POST", "PUT", "DELETE"):
            try:
                body, files = self._parse_body()
            except Exception:
                body, files = {}, []
        user = self._current_user()

        func, perm, args = self._match(method, path)
        if func:
            try:
                if perm and perm != "*":
                    if not user:
                        raise ApiError("未登录", 401)
                    if not security.has_perm(user, perm):
                        db.log_action(user.get("id"), user.get("display_name"), "越权访问",
                                      path, "", self._ip())
                        raise ApiError("权限不足", 403)
                if perm is None and not user and path.startswith("/api/"):
                    raise ApiError("未登录", 401)
                ctx = Ctx(self, body, query, files, user, self._ip())
                ctx.args = args
                res = func(ctx)
                if isinstance(res, tuple):
                    ctype = res[2] if len(res) > 2 else "application/octet-stream"
                    extra = res[3] if len(res) > 3 else None
                    if not getattr(self, "_responded", False):
                        self._send(res[0], res[1], ctype, extra)
                    return
                self._json({"ok": True, "data": res if res is not None else {}})
            except ApiError as e:
                self._json({"ok": False, "msg": e.msg}, e.code)
            except Exception as e:
                traceback.print_exc()
                self._json({"ok": False, "msg": "服务器内部错误：%s" % e}, 500)
            return

        if path.startswith("/api/"):
            self._json({"ok": False, "msg": "接口不存在：" + path}, 404)
            return

        if path.startswith("/downloads/"):
            fname = urllib.parse.unquote(path[len("/downloads/"):])
            for base in (os.path.join(config.resource_dir(), "web", "downloads"),
                         os.path.join(config.app_dir(), "data", "downloads")):
                full = os.path.normpath(os.path.join(base, fname))
                if full.startswith(os.path.normpath(base)) and os.path.isfile(full):
                    disp = ('attachment; filename="client.exe"; filename*=UTF-8\'\'%s'
                            % urllib.parse.quote(os.path.basename(full)))
                    self._send_stream(200, full, "application/octet-stream",
                                      {"Content-Disposition": disp})
                    return
            self._send(404, "下载文件不存在".encode("utf-8"), ctype="text/plain; charset=utf-8")
            return

        # 单点登录中转页：浏览器不允许一个站点替另一个站点写 localStorage，
        # 所以在「目标站点自己的源」上执行写入，再跳首页。
        # 这里返回的页面不含任何用户数据，只做一次 localStorage 赋值 + 跳转。
        if path == "/sso-bridge":
            self._sso_bridge()
            return

        self._static(path)

    def _sso_bridge(self):
        """渲染 /sso-bridge?to=<目标站点>&token=<票据>。

        安全约束：
          · 只接受 http/https 的 to，且必须是我们自己的题库平台地址，避免被当成开放跳转；
          · token 只写在目标站点的 localStorage（exam_token），不写 cookie、不下发到第三方；
          · 页面加 no-store，防止票据被中间层缓存。
        """
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        to = (qs.get("to") or [""])[0].strip()
        token = (qs.get("token") or [""])[0].strip()
        allowed = ""
        try:
            from core import app_ctx as _ac
            allowed = str(_ac.APP.cfg.get("study_center_url", "") or "").rstrip("/")
        except Exception:
            allowed = ""
        err = ""
        if not to or not token:
            err = "缺少参数"
        elif not (to.startswith("http://") or to.startswith("https://")):
            err = "目标地址不合法"
        elif allowed and to.rstrip("/") != allowed:
            err = "目标地址不在允许范围内"
        if err:
            self._send(400, ("<meta charset='utf-8'><p>跳转失败：%s</p>" % err).encode("utf-8"),
                       ctype="text/html; charset=utf-8")
            return
        # token 通过 JSON 注入，避免引号注入
        tgt = json.dumps(to.rstrip("/") + "/")
        tk = json.dumps(token)
        html = (
            "<!doctype html><meta charset='utf-8'><title>正在进入题库…</title>"
            "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;"
            "display:grid;place-items:center;height:100vh;margin:0;background:#f6f9ff;color:#41506a}"
            ".b{font-size:14px}</style>"
            "<div class='b'>正在进入题库…</div><script>"
            "var to=%s,token=%s;"
            "try{localStorage.setItem('exam_token',token);}catch(e){}"
            "location.replace(to);"
            "</script>" % (tgt, tk))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _match(self, method, path):
        for m, p, func, perm, desc in ROUTES:
            if m != method:
                continue
            if p == path:
                return func, perm, {}
            if p.endswith("*") and path.startswith(p[:-1]):
                return func, perm, {"rest": path[len(p) - 1:]}
            if "<" in p:
                seg_p, seg_r = p.split("/"), path.split("/")
                if len(seg_p) != len(seg_r):
                    continue
                args, ok = {}, True
                for a, b in zip(seg_p, seg_r):
                    if a.startswith("<") and a.endswith(">"):
                        args[a[1:-1]] = b
                    elif a != b:
                        ok = False
                        break
                if ok:
                    return func, perm, args
        return None, None, {}

    def _static(self, path):
        from core import app_ctx
        if path in ("/", ""):
            path = "/index.html"
        if path == "/favicon.ico":
            path = "/favicon.svg"
        if path.startswith("/games/"):
            gdir = os.path.join(config.resource_dir(), "games")
            gfull = os.path.normpath(os.path.join(gdir, path[len("/games/"):]))
            if not gfull.startswith(os.path.normpath(gdir)) or not os.path.isfile(gfull):
                self._send(404, self._notfound(), ctype="text/html; charset=utf-8")
                return
            with open(gfull, "rb") as f:
                gdata = f.read()
            self._send(200, gdata, guess_mime(gfull), {"Cache-Control": "no-cache"})
            return
        root = config.CFG.web_dir
        full = os.path.normpath(os.path.join(root, path.lstrip("/")))
        if not full.startswith(os.path.normpath(root)):
            self._send(403, b"forbidden", ctype="text/plain")
            return
        if not os.path.isfile(full):
            full = os.path.join(root, "index.html")
            if not os.path.isfile(full):
                self._send(404, self._notfound(), ctype="text/html; charset=utf-8")
                return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception:
            self._send(404, self._notfound(), ctype="text/html; charset=utf-8")
            return
        ext = os.path.splitext(full)[1].lower()
        if ext in (".js", ".css", ".html"):
            etag = '"%s"' % util.sha256_hex(data)[:16]
            if self.headers.get("if-none-match") == etag:
                self._send(304, b"", guess_mime(full))
                return
            self._send(200, data, guess_mime(full), {"ETag": etag})
            return
        rng = self.headers.get("range")
        if rng and rng.startswith("bytes="):
            try:
                s, e = rng[6:].split("-")
                start = int(s)
                end = int(e) if e else len(data) - 1
                end = min(end, len(data) - 1)
                self._send(206, data[start:end + 1], guess_mime(full), {
                    "Content-Range": "bytes %d-%d/%d" % (start, end, len(data)),
                    "Accept-Ranges": "bytes",
                })
                return
            except Exception:
                pass
        self._send(200, data, guess_mime(full), {"Accept-Ranges": "bytes"})

    def _notfound(self):
        return ("<html><head><meta charset='utf-8'><title>404</title></head>"
                "<body style='font-family:sans-serif;padding:40px'><h2>404 页面不存在</h2>"
                "<p>请确认已通过 <code>/</code> 访问系统首页。</p></body></html>").encode()


# ---------------- 引擎 ----------------
class Server(object):
    """线程池 HTTP 服务器：socket accept 循环 + 连接 keep-alive 复用

    ssl_context 不为空时同一套逻辑即 HTTPS 服务器（WebSocket 自动走 wss）。
    """

    def __init__(self, addr, handler_cls, workers=128, ssl_context=None):
        self.addr = addr
        self.handler_cls = handler_cls
        self.timeout = 120
        self.ssl_context = ssl_context
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(addr)
        self._sock.listen(256)
        self._sock.settimeout(1.0)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lchttp")
        self._running = True

    def serve_forever(self):
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(30)
                self._pool.submit(self._handle_conn, conn, addr)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass

    def shutdown(self):
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass
        self._pool.shutdown(wait=False)

    def _handle_conn(self, conn, addr):
        h = None
        if self.ssl_context is not None:
            try:
                conn.settimeout(15)
                conn = self.ssl_context.wrap_socket(conn, server_side=True)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                return
        try:
            h = self.handler_cls(conn, addr, self)
            while not h.close_connection:
                try:
                    if not h._parse_request():
                        break
                except (socket.timeout, EOFError, ValueError, OSError):
                    break
                # 请求方要求关闭或体过大则响应后断开
                conn_hdr = h.headers.get("connection", "").lower()
                if conn_hdr == "close":
                    h.close_connection = True
                meth = getattr(h, "do_" + h.command, None)
                if meth is None:
                    h._send(405, b"method not allowed", ctype="text/plain")
                    h.close_connection = True
                else:
                    meth()
                if h._in_ws:
                    return
                # HTTP/1.0 默认不 keep-alive
                if getattr(h, "_http_version", "HTTP/1.1") != "HTTP/1.1":
                    h.close_connection = True
        except Exception:
            traceback.print_exc()
        finally:
            # WS 升级后连接由 WS 线程接管，此处不得关闭
            if h is not None and not h._in_ws:
                try:
                    conn.close()
                except Exception:
                    pass
