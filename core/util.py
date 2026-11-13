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
"""通用工具函数（仅依赖标准库）"""
import json
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime, timedelta, timezone

CN_TZ = timedelta(hours=8)


def _cn_dt(ts):
    """把 UTC 时间戳按内置 +08:00 偏移转为 naive datetime（不依赖系统时区）"""
    return datetime.fromtimestamp(int(ts), timezone.utc).astimezone(timezone(CN_TZ))



def now_ts():
    return int(time.time())


def now_str(fmt="%Y-%m-%d %H:%M:%S"):
    """本地时间字符串（内置 UTC+8 偏移，不依赖系统时区）"""
    return _cn_dt(time.time()).strftime(fmt)


def today_str():
    return now_str("%Y-%m-%d")


def ts_to_str(ts, fmt="%Y-%m-%d %H:%M:%S"):
    if not ts:
        return ""
    try:
        return _cn_dt(int(ts)).strftime(fmt)
    except Exception:
        return ""


def str_to_ts(s, fmt="%Y-%m-%d %H:%M"):
    """与 ts_to_str 对齐：按 +08:00 解释，不依赖系统时区"""
    try:
        dt = datetime.strptime(s, fmt).replace(tzinfo=timezone(CN_TZ))
        return int(dt.timestamp())
    except Exception:
        return 0


def uid(prefix=""):
    return prefix + secrets.token_hex(8)


def rand_str(n=16):
    return secrets.token_hex(n // 2 + 1)[:n]


def safe_name(name):
    """文件名清洗，防止路径穿越"""
    name = unicodedata.normalize("NFKC", str(name or ""))
    name = re.sub(r"[\\\/\:\*\?\"\<\>\|]+", "_", name)
    name = name.strip().strip(".")
    return (name or "unnamed")[:120]


def human_size(n):
    try:
        n = float(n)
    except Exception:
        return "0B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f%s" % (n, unit) if unit != "B" else "%d%s" % (n, unit)
        n /= 1024
    return "%.1fPB" % n


def json_loads(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def json_dumps(o):
    try:
        return json.dumps(o, ensure_ascii=False)
    except Exception:
        return "null"


def escape_html(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def mask_ip(ip):
    if not ip:
        return ""
    parts = str(ip).split(".")
    return ".".join(parts[:3] + ["*"]) if len(parts) == 4 else str(ip)


def initials(name):
    s = str(name or "?").strip()
    if not s:
        return "?"
    if re.match(r"^[\u4e00-\u9fa5]", s):
        return s[-2:] if len(s) >= 2 else s
    return s[:2].upper()


def client_of(ua):
    """从 User-Agent 解析客户端/设备类型，返回展示用标签，如 "Windows · Chrome" / "Android · 浏览器" / "未知设备" """
    ua = str(ua or "").strip()
    if not ua:
        return "未知设备"
    low = ua.lower()
    # 操作系统
    if "windows" in low:
        os_ = "Windows"
    elif "android" in low:
        os_ = "Android"
    elif "iphone" in low or "ipad" in low or "ipod" in low:
        os_ = "iOS"
    elif "mac os" in low or "macintosh" in low:
        os_ = "macOS"
    elif "linux" in low:
        os_ = "Linux"
    else:
        os_ = "其他设备"
    # 浏览器 / 客户端
    if "edg/" in low:
        browser = "Edge"
    elif "chrome" in low and "chromium" not in low:
        browser = "Chrome"
    elif "firefox" in low or "fxios" in low:
        browser = "Firefox"
    elif "safari" in low or "mobile/1" in low or "mobile safari" in low:
        browser = "Safari"
    elif "curl" in low or "python" in low or "okhttp" in low or "httpclient" in low:
        browser = "脚本/接口"
    else:
        browser = "浏览器"
    return "%s · %s" % (os_, browser)


def color_of(seed):
    palette = ["#2f6fed", "#12a67a", "#e8734a", "#8a5cf6", "#d94f8a",
               "#0ea5e9", "#f2b13c", "#5b7085", "#c0392b", "#16a34a"]
    h = sum(ord(c) for c in str(seed))
    return palette[h % len(palette)]


def pinyin_initial(s):
    """极简首字母（用于搜索辅助，非完整拼音表）"""
    return str(s or "")


def clamp(v, lo, hi):
    try:
        v = float(v)
    except Exception:
        return lo
    return max(lo, min(hi, v))


def sha256_hex(b):
    import hashlib
    return hashlib.sha256(b if isinstance(b, bytes) else str(b).encode()).hexdigest()
