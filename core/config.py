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
"""系统配置：持久化在 data/config.json，支持后台在线修改"""
import json
import os
import sys
import threading

_lock = threading.RLock()

DEFAULTS = {
    # 站点
    "site_name": "云聊",
    "site_slogan": "内网协同 · 学习成长 · 安全可控",
    "welcome": "欢迎使用云聊，请遵守公司信息安全规范。",
    "icp_text": "内部系统 · 禁止外传",
    # 站点主题配色：blue=经典蓝 / bluepink=蓝粉渐变（管理员在「系统设置 → 站点信息」切换，全站生效）
    "theme_color": "blue",
    # 网络
    "host": "0.0.0.0",
    "port": 8080,
    "auto_open_browser": True,
    # 仅当部署在 Nginx 等反向代理之后才开启：开启后采信 X-Real-IP 头识别来源
    # 默认关闭，直连场景下该头可被伪造，关闭可防止绕过 IP 黑名单
    "trust_real_ip": False,
    # 域名绑定：逗号分隔的允许域名（如 chat.company.com, chat2.company.com），
    # 留空=不限制（任意 Host 均可访问）；配置后仅放行名单域名/本机回环/本机网卡 IP
    "server_names": "",
    # 安全
    "allow_register": True,
    "register_need_approval": False,
    "default_role": "student",
    # 姓名/头衔修改是否需要管理员审核（开启后普通用户在个人中心只能提交申请）
    "profile_change_need_approval": True,
    "password_min_len": 8,
    "password_need_complex": True,
    "max_failed_login": 5,
    "lock_minutes": 15,
    "session_hours": 12,
    "jwt_secret": "",
    # 上传
    "max_upload_mb": 50,
    # allow_ext 留空=不限制（企业内网推荐）；填写后仅允许名单内扩展名
    "allow_ext": "",
    # deny_ext：黑名单，始终生效（高危可执行文件默认拦截）
    "deny_ext": "exe,msi,bat,cmd,com,scr,pif,vbs,vbe,ps1,psm1,jar,apk,dll,sys,lnk,reg",
    # 聊天
    "max_msg_len": 4000,
    "history_page_size": 50,
    "allow_recall_minutes": 5,
    "enable_sensitive_filter": True,
    "sensitive_replace": "***",
    # 学习
    "enable_exam": True,
    "enable_checkin": True,
    # 小游戏（总开关 + 单游戏开关 + 是否在侧边栏显示入口，默认全关，需管理员手动开启）
    "enable_games": False,
    "game_fireboy": False,
    "game_chess": False,
    "game_show_nav": False,
    # 远程协助（桌面级屏幕共享与远程控制，高危能力，默认关闭）
    "enable_remote_assist": False,
    # 被控端截屏帧率与 JPEG 质量（帧率越高带宽占用越大）
    "remote_fps": 6,
    "remote_quality": 55,
    # 单次协助最长时长（秒），超时自动断开，防止长期占用
    "remote_max_minutes": 60,
    # 被控端每次会话是否都需要用户手动点确认（强制开启，不提供关闭项）
    "remote_need_confirm": True,
    # 维护
    "maintenance_mode": False,
    "maintenance_note": "系统维护中，请稍后访问。",
    "log_retention_days": 90,
    # HTTPS（默认关闭：仓库不附带证书，需自备后在此启用）
    # 启用步骤：把证书放到下面两个路径（或改成你的实际路径），
    # 再在后台「系统设置 → 安全与访问」勾选「启用 HTTPS」，或把本项改为 True。
    "https_enabled": False,
    "https_port": 8443,
    "cert_file": "cert/fullchain.pem",
    "key_file": "cert/privkey.key",
    # IP 访问白名单（逗号分隔；支持单个 IP、CIDR 网段 如 192.168.1.0/24、前缀通配 如 192.168.1.*；
    # 留空=不启用。启用后仅名单内的 IP 可访问本站（含登录页与全部接口），请确保包含管理员常用 IP）
    "ip_whitelist": "",
    # 题库刷题平台（知否）嵌入学习中心：云聊只做单点登录转发，
    # 题库数据仍在 zhifou 自己的 SQLite 里，两边不共库。
    "study_center_enabled": False,
    "study_center_url": "http://127.0.0.1:8080",
    "study_center_secret": "",
    # 用户名映射：云聊用户名去掉该前缀后就是题库平台的登录名
    # （例如 ks20240001 → 20240001）。留空则原样匹配。
    "study_center_user_prefix": "",
    # ===== 数据库引擎：sqlite（默认，零依赖）或 mysql（内置免安装）=====
    # sqlite：Python 自带，单文件 data/app.db，适合个人/小团队、开箱即用
    # mysql ：内置 MySQL 8.0，适合较大并发与多实例部署（需 vendor/mysql）
    "db_engine": "sqlite",
    # ===== 内嵌 MySQL（仅 db_engine=mysql 时生效）=====
    # port=0 表示自动挑选空闲端口；首次启动后会把实际端口写回这里
    "mysql_enabled": True,
    "mysql_port": 33070,
    "mysql_database": "yunliao",
    "mysql_user": "yunliao",
    "mysql_password": "",
}


def app_dir():
    """程序所在目录（打包后为 exe 所在目录）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """只读资源目录（打包后为临时解压目录）"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_path(path):
    """解析配置里的文件路径：绝对路径原样返回；相对路径依次在 程序目录、资源目录 下查找。

    打包成单文件 EXE 后，随包资源（如 cert/）会被解压到 _MEIPASS，
    因此证书路径既支持 exe 同目录的自定义证书，也支持安装包内置证书。
    """
    if not path:
        return ""
    path = str(path).strip()
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    for base in (app_dir(), resource_dir()):
        p = os.path.join(base, path)
        if os.path.exists(p):
            return p
    return os.path.join(app_dir(), path)


class Config(object):
    def __init__(self, data_dir=None):
        self.data_dir = data_dir or os.path.join(app_dir(), "data")
        self.path = os.path.join(self.data_dir, "config.json")
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        with _lock:
            if os.path.exists(self.path):
                try:
                    # utf-8-sig：兼容 Windows 记事本/PowerShell 保存时带的 BOM。
                    # 用普通 utf-8 会因 BOM 解析失败，导致用户改的配置被静默丢弃。
                    with open(self.path, "r", encoding="utf-8-sig") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self._data.update(loaded)
                except Exception as e:
                    # 不再静默忽略：配置写坏时要让用户知道，否则会以为设置生效了
                    try:
                        print("[警告] data/config.json 解析失败，本次使用默认配置：%s" % e)
                        print("        请检查该文件是否为合法 JSON（常见原因：多余逗号、缺引号）。")
                    except Exception:
                        pass
            if not self._data.get("jwt_secret"):
                import secrets
                self._data["jwt_secret"] = secrets.token_hex(24)
            return self._data

    def save(self):
        with _lock:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def get(self, key, default=None):
        with _lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        with _lock:
            self._data[key] = value

    def update(self, d):
        with _lock:
            for k, v in d.items():
                if k in DEFAULTS or k.startswith("custom_"):
                    self._data[k] = v

    def as_dict(self, public=False):
        with _lock:
            d = dict(self._data)
        if public:
            d.pop("jwt_secret", None)
        return d

    # 目录
    @property
    def db_path(self):
        """兼容旧名字：MySQL 模式下已不存在单文件数据库，
        指向数据目录下的 mysql 子目录，仅用于日志/提示显示。"""
        return os.path.join(self.data_dir, "mysql")

    @property
    def mysql_settings(self):
        """内嵌 MySQL 的连接参数（供 core.mysql_server / core.db 使用）"""
        return {
            "port": int(self.get("mysql_port", 33070) or 0),
            "database": self.get("mysql_database", "yunliao") or "yunliao",
            "user": self.get("mysql_user", "yunliao") or "yunliao",
            "password": self.get("mysql_password", "") or "",
        }

    @property
    def files_dir(self):
        return os.path.join(self.data_dir, "files")

    @property
    def backup_dir(self):
        return os.path.join(self.data_dir, "backups")

    @property
    def log_dir(self):
        return os.path.join(self.data_dir, "logs")

    @property
    def web_dir(self):
        rd = resource_dir()
        p = os.path.join(rd, "web")
        if os.path.isdir(p):
            return p
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


CFG = None


def init(data_dir=None):
    global CFG
    CFG = Config(data_dir)
    for d in (CFG.data_dir, CFG.files_dir, CFG.backup_dir, CFG.log_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    CFG.save()
    return CFG
