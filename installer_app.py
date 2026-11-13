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
云聊提醒 · Windows 安装程序（源码）

打包：pyinstaller Installer.spec --noconfirm
产物：dist/云聊提醒-安装包.exe（内置绿色版 exe，双击即安装）

安装内容：
  %LOCALAPPDATA%\\Programs\\云聊提醒\\云聊提醒.exe
  开始菜单 / 桌面快捷方式（可选）
  yunliao:// 协议注册（网页「用当前账号打开」直接唤起）
  开机自启（可选）
  卸载信息（控制面板可卸载）
"""
import os
import shutil
import subprocess
import sys
import time

APP_NAME = "云聊提醒"
APP_EXE = "云聊提醒.exe"
APP_VER = "1.2.0"
PROTOCOL = "yunliao"
RUN_KEY = "yunliao-notify"
UNINST_KEY = "云聊提醒"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def local_appdata():
    return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")


def install_dir():
    return os.path.join(local_appdata(), "Programs", APP_NAME)


def payload_exe():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for name in (APP_EXE, os.path.join("dist", APP_EXE)):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return ""


def start_menu_dir():
    return os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def desktop_dir():
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
        return buf.value
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def ps_quote(s):
    return str(s).replace("'", "''")


def make_shortcut(lnk, target, args="", icon="", workdir=""):
    try:
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
                  "$s.TargetPath='%s';$s.Arguments='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s';$s.Save()"
                  % (ps_quote(lnk), ps_quote(target), ps_quote(args), ps_quote(workdir or os.path.dirname(target)),
                     ps_quote(icon or target)))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       creationflags=CREATE_NO_WINDOW, capture_output=True, timeout=30)
        return True
    except Exception:
        return False


def kill_app():
    """只结束已安装的提醒助手，绝不能误杀安装程序自身"""
    cur = ""
    try:
        if getattr(sys, "frozen", False):
            cur = os.path.basename(sys.executable).lower()
    except Exception:
        pass
    if APP_EXE.lower() == cur:
        return
    try:
        subprocess.run(["taskkill", "/F", "/IM", APP_EXE], creationflags=CREATE_NO_WINDOW,
                       capture_output=True, timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 安装 / 卸载
# ---------------------------------------------------------------------------
def do_install(autostart=True, desktop=True, launch=True, log=print, target_dir=None):
    src = payload_exe()
    if not src:
        raise RuntimeError("安装包损坏：找不到 %s" % APP_EXE)
    target_dir = target_dir or install_dir()
    os.makedirs(target_dir, exist_ok=True)
    dst = os.path.join(target_dir, APP_EXE)

    log("正在关闭已运行的提醒助手…")
    kill_app()
    time.sleep(0.6)

    log("正在复制程序文件…")
    shutil.copyfile(src, dst)

    # 协议：yunliao://
    log("正在注册 yunliao:// 协议…")
    import winreg
    base = r"Software\Classes\%s" % PROTOCOL
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:云聊提醒")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, '"%s" "%%1"' % dst)

    # 开机自启
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as k:
        if autostart:
            winreg.SetValueEx(k, RUN_KEY, 0, winreg.REG_SZ, '"%s" --minimized' % dst)
        else:
            try:
                winreg.DeleteValue(k, RUN_KEY)
            except FileNotFoundError:
                pass

    # 快捷方式
    log("正在创建快捷方式…")
    start_lnk = os.path.join(start_menu_dir(), APP_NAME + ".lnk")
    make_shortcut(start_lnk, dst)
    if desktop:
        make_shortcut(os.path.join(desktop_dir(), APP_NAME + ".lnk"), dst)

    # 卸载程序：把安装包自身复制到安装目录
    log("正在写入卸载信息…")
    uninst_exe = os.path.join(target_dir, "卸载 %s.exe" % APP_NAME)
    try:
        me = os.path.abspath(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
        if me.lower().endswith(".exe") and os.path.basename(me) != os.path.basename(uninst_exe):
            shutil.copyfile(me, uninst_exe)
    except Exception:
        pass
    if not os.path.exists(uninst_exe):
        uninst_exe = dst
    make_shortcut(os.path.join(start_menu_dir(), "卸载 %s.lnk" % APP_NAME), uninst_exe, "--uninstall")

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Uninstall\%s" % UNINST_KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VER)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "云聊")
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, dst)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, target_dir)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, '"%s" --uninstall' % uninst_exe)
        try:
            size = int(os.path.getsize(dst) / 1024)
            winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size)
        except Exception:
            pass

    if launch:
        log("正在启动提醒助手…")
        try:
            subprocess.Popen([dst], cwd=target_dir, close_fds=True)
        except Exception:
            pass
    return target_dir


def do_uninstall(log=print):
    import winreg
    log("正在关闭提醒助手…")
    kill_app()
    time.sleep(0.8)

    # 协议 / 自启 / 卸载信息
    for path in (r"Software\Classes\%s" % PROTOCOL,):
        try:
            _delete_tree(winreg.HKEY_CURRENT_USER, path)
        except Exception:
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_KEY)
    except Exception:
        pass
    try:
        _delete_tree(winreg.HKEY_CURRENT_USER,
                     r"Software\Microsoft\Windows\CurrentVersion\Uninstall\%s" % UNINST_KEY)
    except Exception:
        pass

    # 快捷方式
    for lnk in (os.path.join(start_menu_dir(), APP_NAME + ".lnk"),
                os.path.join(start_menu_dir(), "卸载 %s.lnk" % APP_NAME),
                os.path.join(desktop_dir(), APP_NAME + ".lnk")):
        try:
            os.remove(lnk)
        except Exception:
            pass

    # 安装目录：交给 cmd 延迟删除（本进程可能就在目录里）
    d = install_dir()
    log("正在删除安装目录…")
    try:
        subprocess.Popen('cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "%s"' % d,
                         shell=True, creationflags=CREATE_NO_WINDOW)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)


def _delete_tree(root, path):
    import winreg
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
    except FileNotFoundError:
        return
    try:
        winreg.DeleteKey(root, path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 图形界面
# ---------------------------------------------------------------------------
def run_ui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("%s 安装程序 v%s" % (APP_NAME, APP_VER))
    root.geometry("480x420")
    root.resizable(False, False)
    root.configure(bg="#f3f7fd")
    try:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon = os.path.join(base, "app.ico")
        if os.path.exists(icon):
            root.iconbitmap(icon)
    except Exception:
        pass

    head = tk.Frame(root, bg="#2f6fed", height=86)
    head.pack(fill="x")
    head.pack_propagate(False)
    tk.Label(head, text="云聊提醒 · 桌面助手", bg="#2f6fed", fg="#ffffff",
             font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=20, pady=(18, 2))
    tk.Label(head, text="新消息在桌面右下角提醒，登录状态与网页同步", bg="#2f6fed", fg="#d7e6ff",
             font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=20)

    body = tk.Frame(root, bg="#f3f7fd")
    body.pack(fill="both", expand=True, padx=20, pady=14)

    tk.Label(body, text="安装位置", bg="#f3f7fd", fg="#5a6b82",
             font=("Microsoft YaHei UI", 9)).pack(anchor="w")
    path_var = tk.StringVar(value=install_dir())
    ent = tk.Entry(body, textvariable=path_var, font=("Microsoft YaHei UI", 9),
                   relief="solid", bd=1, bg="#ffffff")
    ent.pack(fill="x", ipady=4, pady=(4, 12))

    v_auto = tk.BooleanVar(value=True)
    v_desk = tk.BooleanVar(value=True)
    v_run = tk.BooleanVar(value=True)
    for text, var in (("开机自动启动（推荐，后台常驻）", v_auto),
                      ("创建桌面快捷方式", v_desk),
                      ("安装完成后立即运行", v_run)):
        tk.Checkbutton(body, text=text, variable=var, bg="#f3f7fd", fg="#33475f",
                       activebackground="#f3f7fd", selectcolor="#ffffff", anchor="w",
                       font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0).pack(fill="x", pady=2)

    status = tk.StringVar(value="点击「开始安装」")
    tk.Label(body, textvariable=status, bg="#f3f7fd", fg="#7c8da6",
             font=("Microsoft YaHei UI", 9), anchor="w", justify="left", wraplength=420).pack(fill="x", pady=(12, 0))

    bar = ttk.Progressbar(body, mode="determinate", maximum=100)
    bar.pack(fill="x", pady=8)

    btns = tk.Frame(body, bg="#f3f7fd")
    btns.pack(fill="x", side="bottom")

    def install():
        btn_install.configure(state="disabled", text="安装中…")
        bar["value"] = 8

        def step(msg):
            status.set(msg)
            bar["value"] = min(100, bar["value"] + 18)
            root.update()

        try:
            target = path_var.get().strip() or install_dir()
            step("正在关闭已运行的提醒助手…")
            step("正在复制程序文件…")
            do_install(autostart=v_auto.get(), desktop=v_desk.get(), launch=v_run.get(),
                       log=step, target_dir=target)
            bar["value"] = 100
            status.set("安装完成！\n程序目录：%s\n开始菜单已创建快捷方式，控制面板可卸载。" % target)
            btn_install.configure(text="已完成", state="disabled")
            root.after(1200, root.destroy)
        except Exception as e:
            status.set("安装失败：%s" % e)
            btn_install.configure(state="normal", text="重试")

    def close():
        root.destroy()

    btn_install = tk.Button(btns, text="开始安装", command=install, bg="#2f6fed", fg="#ffffff",
                            activebackground="#1f5fd0", activeforeground="#ffffff", relief="flat",
                            bd=0, font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2",
                            padx=26, pady=7)
    btn_install.pack(side="right")
    tk.Button(btns, text="取消", command=close, bg="#eef3fb", fg="#44566f", relief="flat", bd=0,
              font=("Microsoft YaHei UI", 9), cursor="hand2", padx=16, pady=7).pack(side="right", padx=8)

    root.mainloop()


def main():
    if "--uninstall" in sys.argv:
        try:
            do_uninstall()
        except Exception:
            pass
        return
    # 静默安装（机房批量部署）：云聊提醒-安装包.exe /S [--no-autostart] [--no-desktop]
    if "--silent" in sys.argv or "/S" in sys.argv:
        logf = os.path.join(os.environ.get("TEMP", "."), "yunliao-install.log")
        try:
            def _log(m):
                with open(logf, "a", encoding="utf-8") as f:
                    f.write("%s %s\n" % (time.strftime("%H:%M:%S"), m))
            _log("payload=%s" % payload_exe())
            do_install(autostart=("--no-autostart" not in sys.argv),
                       desktop=("--no-desktop" not in sys.argv),
                       launch=False, log=_log)
            _log("install finished")
        except Exception as e:
            import traceback
            with open(logf, "a", encoding="utf-8") as f:
                f.write("ERROR %r\n" % (e,))
                traceback.print_exc(file=f)
        return
    # 已安装且带参数（协议唤起）：直接交给已安装程序
    if len(sys.argv) > 1 and str(sys.argv[1]).lower().startswith(PROTOCOL + "://"):
        exe = os.path.join(install_dir(), APP_EXE)
        if os.path.exists(exe):
            subprocess.Popen([exe, sys.argv[1]], close_fds=True)
            return
    run_ui()


if __name__ == "__main__":
    main()
