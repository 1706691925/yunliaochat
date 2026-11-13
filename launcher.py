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
"""云聊 —— Windows 桌面启动器（打包为单文件 EXE）

双击运行：后台启动 HTTP + WebSocket 服务，弹出控制台窗口显示运行状态与日志，
可一键打开浏览器进入聊天室。数据目录默认保存在 exe 同目录的 data/ 下。
"""
import os
import queue
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as server_app        # noqa: E402  主程序（含全部路由注册）
from core import config         # noqa: E402
from core.teelog import Tee     # noqa: E402  与 app.py 共用同一套日志分流

LOG_QUEUE = queue.Queue()
STOPPED = threading.Event()


def run_server(app):
    try:
        app.run(open_browser=False)
    except Exception:
        import traceback
        print(traceback.format_exc())
    finally:
        STOPPED.set()


def main():
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        # 无图形环境兜底：退化为命令行模式
        server_app.main()
        return

    # 支持 --data-dir 指定数据目录（便于测试与自定义安装位置）
    data_dir = None
    if "--data-dir" in sys.argv:
        i = sys.argv.index("--data-dir")
        if i + 1 < len(sys.argv):
            data_dir = sys.argv[i + 1]

    # ---- 启动服务器（后台线程） ----
    app = server_app.Application(data_dir=data_dir)
    app.bootstrap()
    try:
        import datetime
        log_path = os.path.join(app.cfg.log_dir, "server.log")
        sys.stdout = Tee(sys.stdout, log_path)
        sys.stderr = Tee(sys.stderr, log_path)
    except Exception:
        pass
    t = threading.Thread(target=run_server, args=(app,), daemon=True)
    t.start()

    # ---- 窗口 ----
    root = tk.Tk()
    root.title("云聊")
    root.geometry("780x560")
    root.minsize(600, 420)
    host = app.cfg.get("host", "0.0.0.0")
    port = int(app.cfg.get("port", 8080))
    url = "http://127.0.0.1:%d" % port

    top = tk.Frame(root, padx=14, pady=10)
    top.pack(fill="x")
    tk.Label(top, text="云聊", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    tk.Label(top, text="数据目录：%s   监听：%s:%d" % (app.cfg.data_dir, host, port),
             fg="#666", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
    tk.Label(top, text="访问地址：%s   （同一内网可用 http://<本机IP>:%d 访问）"
             % (url, port), fg="#2f6fed", font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    btns = tk.Frame(top, pady=6)
    btns.pack(anchor="w")

    def open_site():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def stop_server():
        try:
            if getattr(app, "httpd", None):
                app.httpd.shutdown()
        except Exception:
            pass
        status.set("正在停止服务…")
        root.after(300, lambda: (status.set("服务已停止，可关闭窗口"), btn_stop.config(state="disabled")))

    tk.Button(btns, text="打开聊天室", command=open_site, width=14).pack(side="left", padx=(0, 8))
    btn_stop = tk.Button(btns, text="停止服务", command=stop_server, width=14)
    btn_stop.pack(side="left")
    status = tk.StringVar(value="服务启动中…")
    tk.Label(top, textvariable=status, fg="#0a0", font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    log_box = scrolledtext.ScrolledText(root, height=20, font=("Consolas", 9), state="disabled",
                                        bg="#0f172a", fg="#cbd5e1", wrap="word")
    log_box.pack(fill="both", expand=True, padx=14, pady=(0, 12))

    def poll_logs():
        try:
            while True:
                line = LOG_QUEUE.get_nowait()
                log_box.config(state="normal")
                log_box.insert("end", line)
                log_box.see("end")
                log_box.config(state="disabled")
        except queue.Empty:
            pass
        if not STOPPED.is_set():
            status.set("服务运行中")
        root.after(200, poll_logs)

    root.after(200, poll_logs)

    def on_close():
        try:
            if getattr(app, "httpd", None):
                app.httpd.shutdown()
        except Exception:
            pass
        root.destroy()
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
