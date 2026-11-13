# -*- coding: utf-8 -*-
"""云聊 —— Windows 桌面客户端 v1.1.1

- QQ 式系统消息推送：新消息时弹出托盘气泡（桌面弹窗）
- 授权码免密登录：浏览器已登录用户在个人中心生成 6 位授权码，客户端输入即免密登录
- 后台保活：点关闭按钮后隐藏到系统托盘常驻，新消息仍会弹窗；托盘左键/气泡点击恢复窗口
- 无缝连接现有服务端：加载服务端 Web 页面，全部功能与浏览器版一致
"""
import os
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "client_config.json")
LOG_PATH = os.path.join(APP_DIR, "client.log")


def _log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


_log("phase_import_base")

import json
import threading
import urllib.request

_log("phase_webview_importing")
import webview
_log("phase_webview_ok")


def _icon_path():
    """图标：打包后在 _MEIPASS（--add-data app.ico;.），源码在项目根"""
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        p = os.path.join(sys._MEIPASS, "app.ico")
        if os.path.isfile(p):
            return p
    p = os.path.join(APP_DIR, "app.ico")
    return p if os.path.isfile(p) else None


# ============================================================
# 系统托盘 + 气泡通知（ctypes Shell_NotifyIcon，无第三方依赖）
# ============================================================
import ctypes
from ctypes import wintypes

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 16
NIIF_INFO = 1
WM_APP = 0x8000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_DESTROY = 0x0012
NIN_BALLOONUSERCLICK = WM_APP + 5
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
TPM_RETURNCMD = 0x0100
MF_SEPARATOR = 0x0800


# Python 3.13 的 ctypes/wintypes 缺 WNDPROC/WNDCLASSEXW，自行定义
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    """ctypes.wintypes 在 Python 3.13 缺少 WNDCLASSEXW，自行定义"""
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", wintypes.HICON),
    ]


def _setup_win32():
    """显式声明 ctypes 签名：不声明时默认按 32 位 int 传参，
    打包后句柄为 64 位指针会 OverflowError（源码运行时碰巧不触发）。"""
    u, s, k = user32, shell32, kernel32
    u.RegisterClassExW.restype = ctypes.c_ushort
    u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    u.CreateWindowExW.restype = wintypes.HWND
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                  wintypes.HINSTANCE, wintypes.LPVOID]
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.LoadImageW.restype = wintypes.HANDLE
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.LoadIconW.restype = wintypes.HANDLE
    u.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                              wintypes.UINT, wintypes.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.TrackPopupMenu.restype = wintypes.UINT
    u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    u.DestroyMenu.argtypes = [wintypes.HMENU]
    u.DestroyWindow.argtypes = [wintypes.HWND]
    s.Shell_NotifyIconW.restype = wintypes.BOOL
    s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    return u


_setup_win32()


class TrayIcon(object):
    """系统托盘常驻图标：气泡通知 + 左键/气泡点击恢复 + 右键菜单（打开/退出）"""

    def __init__(self, icon_path, tooltip, on_activate, on_quit):
        self.on_activate = on_activate
        self.on_quit = on_quit
        self.nid = None
        self._wndproc_ref = None
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        self._wndproc_ref = WNDPROC(self._wndproc)
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.lpszClassName = "YunLiaoTrayWnd"
        wc.hInstance = kernel32.GetModuleHandleW(None)
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "YunLiaoTray",
                                           0, 0, 0, 0, 0, None, None, wc.hInstance, None)
        self.msgid = WM_APP + 1
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self.msgid
        nid.hIcon = self._load_icon(icon_path)
        try:
            nid.szTip = (tooltip or "云聊")[:127]
        except Exception:
            pass
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self.nid = nid
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _load_icon(self, path):
        if path and os.path.isfile(path):
            try:
                h = user32.LoadImageW(None, path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                if h:
                    return h
            except Exception:
                pass
        try:
            return user32.LoadIconW(None, IDI_APPLICATION)
        except Exception:
            return None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == getattr(self, "msgid", None):
            if lparam == WM_LBUTTONUP:
                self._safe(self.on_activate)
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            elif lparam == NIN_BALLOONUSERCLICK:
                self._safe(self.on_activate)
        elif msg == WM_DESTROY:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            except Exception:
                pass
        try:
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            _log("tray_action_err=%r" % (e,))

    def _show_menu(self):
        try:
            hmenu = user32.CreatePopupMenu()
            user32.SetForegroundWindow(self.hwnd)
            user32.AppendMenuW(hmenu, 0, 1, "打开聊天室")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, 0, 2, "退出")
            pos = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pos))
            cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD, pos.x, pos.y, 0, self.hwnd, None)
            user32.DestroyMenu(hmenu)
            if cmd == 1:
                self._safe(self.on_activate)
            elif cmd == 2:
                self._safe(self.on_quit)
        except Exception as e:
            _log("tray_menu_err=%r" % (e,))

    def notify(self, title, body):
        """QQ 式托盘气泡弹窗"""
        try:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(nid)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_INFO
            nid.dwInfoFlags = NIIF_INFO
            try:
                nid.szInfoTitle = (title or "云聊")[:63]
                nid.szInfo = (body or "收到一条新消息")[:255]
            except Exception:
                pass
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception as e:
            _log("notify_err=%r" % (e,))

    def remove(self):
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
        except Exception:
            pass
        try:
            user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
        except Exception:
            pass

    def _loop(self):
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ============================================================
# 页面桥接（js_api）：客户端页面 JS 通过 window.pywebview.api.* 调用
# 注意：pywebview 注入 bridge 时会递归遍历 js_api 实例的全部属性，
# 实例属性中的对象（窗口/托盘）会触发 .NET 属性无限递归导致卡死，
# 因此窗口/托盘/状态一律用模块级全局，Bridge 只暴露方法。
# ============================================================
_WIN = None      # 当前 pywebview 窗口
_TRAY = None     # 托盘
_SRV_URL = ""    # 当前服务端地址
_QUIT = False    # 是否请求退出


class Bridge(object):
    def connect(self, url):
        """配置页：保存服务器地址并跳转"""
        try:
            url = (url or "").strip()
            if not url:
                return {"ok": False, "msg": "请输入服务器地址"}
            if not (url.startswith("http://") or url.startswith("https://")):
                url = "http://" + url
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"server_url": url}, f, ensure_ascii=False)
            global _SRV_URL
            _SRV_URL = url.rstrip("/")
            try:
                _WIN.load_url(url)
            except Exception as e:
                _log("load_url_err=%r" % (e,))
            return {"ok": True}
        except Exception as e:
            _log("connect_err=%r" % (e,))
            return {"ok": False, "msg": "保存配置失败：" + str(e)}

    def notify(self, title, body):
        """页面 JS 调用的系统通知（QQ 式托盘气泡）"""
        try:
            if _TRAY is not None:
                _TRAY.notify(title, body)
        except Exception:
            pass
        return True

    def login_with_code(self, code, server_url):
        """授权码免密登录：调服务端换取 token，注入 WebView 后刷新"""
        try:
            code = (code or "").strip()
            url = ((server_url or "").strip()).rstrip("/")
            if not url:
                return {"ok": False, "msg": "请先填写服务器地址"}
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "http://" + url
            global _SRV_URL
            _SRV_URL = url
            req = urllib.request.Request(
                url + "/api/auth/client-login",
                data=json.dumps({"code": code}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    j = json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                try:
                    j = json.loads(e.read().decode("utf-8", "replace"))
                except Exception:
                    return {"ok": False, "msg": "登录失败：" + str(e)}
            if not j.get("ok"):
                return {"ok": False, "msg": j.get("msg", "授权码无效或已过期")}
            token = (j.get("data") or {}).get("token")
            if not token:
                return {"ok": False, "msg": "服务端未返回登录令牌"}
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"server_url": url}, f, ensure_ascii=False)
            js = ("localStorage.setItem('lc_token', '%s');"
                  "try{document.cookie='token='+encodeURIComponent('%s')+'; path=/; SameSite=Lax';}catch(e){}"
                  "location.href='%s';"
                  % (token, token, url))
            _WIN.evaluate_js(js)
            return {"ok": True, "msg": "登录成功，正在进入…"}
        except Exception as e:
            _log("code_login_err=%r" % (e,))
            return {"ok": False, "msg": "登录失败：" + str(e)}


# ============================================================
# 配置页（首次启动：连接服务器 / 授权码免密登录）
# ============================================================
START_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>云聊 · 连接服务端</title>
<style>
  body{margin:0;font-family:"Microsoft YaHei UI",Arial,sans-serif;background:linear-gradient(135deg,#1d2b53,#2f6fed 60%,#3aa0ff);display:flex;align-items:center;justify-content:center;height:100vh}
  .card{background:#fff;border-radius:12px;padding:30px 34px;width:430px;box-shadow:0 10px 40px rgba(0,0,0,.25)}
  h1{margin:0 0 6px;font-size:20px;color:#1f2d3d}
  .sub{color:#8a94a6;font-size:12px;margin:0 0 14px}
  .sec{border-top:1px solid #eef2f7;padding-top:14px;margin-top:14px}
  .sec b{display:block;font-size:13px;color:#1f2d3d;margin-bottom:8px}
  input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #d8dee8;border-radius:6px;font-size:14px;margin-bottom:8px;outline:none}
  input:focus{border-color:#2f6fed}
  button{width:100%;padding:10px;border:0;border-radius:6px;font-size:14px;cursor:pointer;background:#2f6fed;color:#fff}
  button.gray{background:#eef2f7;color:#1f2d3d;margin-top:2px}
  #msg{font-size:12px;color:#e0484e;min-height:16px;margin-top:8px}
  #msg.ok{color:#18a058}
</style>
</head>
<body>
<div class="card">
  <h1>云聊 · 客户端</h1>
  <p class="sub">连接服务器开始使用；也可输入授权码免密登录（v1.1.1）</p>
  <div class="sec">
    <b>服务器地址</b>
    <input id="srv" placeholder="如 http://10.0.0.5:8080（管理员提供）" autocomplete="off">
  </div>
  <div class="sec">
    <b>授权码免密登录</b>
    <input id="code" maxlength="6" placeholder="在浏览器「个人中心 → Windows 客户端」获取的 6 位授权码" autocomplete="off">
    <button onclick="doCode()">授权码免密登录</button>
    <div id="msg"></div>
  </div>
  <div class="sec">
    <button class="gray" onclick="doConnect()">直接连接服务器</button>
  </div>
</div>
<script>
  function msg(t, ok){ var el=document.getElementById('msg'); el.className=ok?'ok':''; el.textContent=t||''; }
  function doConnect(){
    var u=document.getElementById('srv').value.trim();
    if(!u){ msg('请先填写服务器地址'); return; }
    window.pywebview.api.connect(u).then(function(r){
      if(r && r.ok){ msg('连接成功…','ok'); }
      else { msg((r&&r.msg)||'连接失败'); }
    });
  }
  function doCode(){
    var u=document.getElementById('srv').value.trim();
    var c=document.getElementById('code').value.trim();
    if(!c){ msg('请输入授权码'); return; }
    msg('登录中…');
    window.pywebview.api.login_with_code(c, u).then(function(r){
      if(r && r.ok){ msg(r.msg||'登录成功','ok'); }
      else { msg((r&&r.msg)||'登录失败'); }
    });
  }
</script>
</body>
</html>
"""


def main():
    global _WIN, _TRAY, _SRV_URL, _QUIT
    cfg = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    icon = _icon_path()
    bridge = Bridge()
    saved = cfg.get("server_url") or ""
    _SRV_URL = (saved or "").rstrip("/")
    if saved:
        win = webview.create_window("云聊", saved, width=1280, height=820,
                                    min_size=(980, 640), js_api=bridge)
    else:
        win = webview.create_window("云聊 · 连接服务端", html=START_HTML,
                                    width=700, height=580, resizable=False, js_api=bridge)

    def on_loaded():
        _log("page_loaded url=%s" % getattr(win, "url", ""))
        try:
            t = win.evaluate_js("document.title")
            _log("page_title=%s" % str(t))
        except Exception as e:
            _log("title_err=%r" % (e,))

    try:
        win.events.loaded += on_loaded
    except Exception as e:
        _log("events_err=%r" % (e,))

    def on_closing():
        """点关闭按钮 → 隐藏到托盘保活（不退出）"""
        if _QUIT:
            return None
        try:
            win.hide()
            _log("hidden_to_tray")
        except Exception as e:
            _log("closing_hide_err=%r" % (e,))
        return False   # 阻止窗口关闭

    def on_activate():
        try:
            win.show()
            try:
                win.restore()
            except Exception:
                pass
        except Exception as e:
            _log("activate_err=%r" % (e,))

    def quit_app():
        global _QUIT
        _QUIT = True
        try:
            tray.remove()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    try:
        win.events.closing += on_closing
    except Exception as e:
        _log("closing_reg_err=%r" % (e,))

    if os.environ.get("LANCHAT_NO_TRAY") == "1":
        tray = None
        _log("tray_skipped")
    else:
        tray = TrayIcon(icon, "云聊 v1.1.1（消息推送 / 后台保活）",
                        on_activate=on_activate, on_quit=quit_app)
        _log("tray_created")
    _WIN = win
    _TRAY = tray
    try:
        webview.start(icon=icon)
    finally:
        try:
            tray.remove()
        except Exception:
            pass


if __name__ == "__main__":
    main()
