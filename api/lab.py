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
"""题库平台（知否）嵌入：单点登录 + 健康检查。

为什么是"嵌入"而不是"合并"：
  知否是独立的一套 Node 服务（45 张表、20,463 道题、判分逻辑、商城、PK），
  和云聊的 Python/SQLite 栈完全不同。把数据搬进云聊的表结构意味着重写判分、
  错题、闯关、复习全套逻辑，风险远大于收益。所以采用**同机双进程 + SSO**：
  云聊只负责验证"你是谁"，然后把带签名的票据交给知否，由知否自己签发会话。

握手协议（两边共用 ZHIFOU_SSO_SECRET）：
  sig = HMAC_SHA256(secret, "sso|" + username + "|" + ts + "|" + nonce)
  知否侧比对签名 + 校验时间窗（300 秒）+ nonce 防重放。
"""
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request

from core import db
from core import app_ctx
from core.http import ApiError, route
from api import base


def _cfg(key, default=""):
    try:
        return app_ctx.APP.cfg.get(key, default)
    except Exception:
        return default


def _base_url():
    return str(_cfg("study_center_url", "") or "").rstrip("/")


def _secret():
    return str(_cfg("study_center_secret", "") or "")


def _enabled():
    return bool(_cfg("study_center_enabled", False)) and bool(_base_url())


def _sign(username, ts, nonce, secret):
    msg = ("sso|" + username + "|" + ts + "|" + nonce).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _post_json(url, payload, timeout=8):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8")), None
        except Exception:
            return None, "题库平台返回 HTTP %d" % e.code
    except urllib.error.URLError as e:
        return None, "连不上题库平台（%s）：请确认知否服务已启动，地址配置正确" % (
            getattr(e, "reason", e))
    except Exception as e:
        return None, "请求题库平台失败：%s" % e


@route("/api/learn/lab/status", "GET")
def lab_status(ctx):
    """题库平台状态：是否启用 / 地址 / 是否可达。前端据此决定显示入口还是提示。"""
    if not _enabled():
        return {"enabled": False, "url": "", "reachable": False,
                "msg": "题库平台未启用"}
    url = _base_url()
    reachable, detail = False, ""
    try:
        with urllib.request.urlopen(url + "/api/auth/register-status", timeout=4) as r:
            reachable = r.status == 200
    except Exception as e:
        detail = str(getattr(e, "reason", e))
    return {"enabled": True, "url": url, "reachable": reachable,
            "has_secret": bool(_secret()),
            "msg": "正常" if reachable else ("题库平台无法访问：" + detail)}


@route("/api/learn/lab/sso", "POST")
def lab_sso(ctx):
    """给当前云聊用户签发一张题库平台的登录票据。

    返回 {url, token, user, expires_in}：前端拿到后可直接把 token 写进
    题库平台的 localStorage（同源 iframe）或新窗口打开时带上。
    """
    if not _enabled():
        raise ApiError("题库平台未启用，请管理员在「系统设置 → 学习中心」中开启", 403)
    secret = _secret()
    if not secret:
        raise ApiError("题库平台未配置共享密钥，请联系管理员", 403)
    username = (ctx.user.get("username") or "").strip()
    if not username:
        raise ApiError("当前账号缺少用户名，无法登录题库平台", 400)

    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sig = _sign(username, ts, nonce, secret)
    # 账号映射：云聊用户名常是 ks+学号，题库平台用的是身份证号一类的登录名。
    # 管理员可在用户「学号」字段里填题库登录名，或全局配置前缀规则。
    zf_name = ""
    try:
        zf_name = str(ctx.user.get("student_no") or "").strip()
    except Exception:
        zf_name = ""
    if not zf_name:
        # 支持「用户名去掉固定前缀」的简单规则，例如 ks20240001 → 20240001
        prefix = str(_cfg("study_center_user_prefix", "") or "")
        if prefix and username.startswith(prefix):
            zf_name = username[len(prefix):]
    data, err = _post_json(_base_url() + "/api/auth/sso",
                           {"username": username, "zhifou_username": zf_name,
                            "ts": ts, "nonce": nonce, "sig": sig})
    if err:
        raise ApiError(err, 502)
    if not data or data.get("code") != 0:
        msg = (data or {}).get("msg") or "题库平台拒绝了登录请求"
        raise ApiError(msg, 400)
    payload = data.get("data") or {}
    token = payload.get("token") or ""
    if not token:
        raise ApiError("题库平台未返回登录凭证", 502)
    base.audit(ctx, "题库平台单点登录", username, "知否刷题平台")
    return {"url": _base_url(), "token": token,
            "user": payload.get("user") or {},
            "expires_in": 3 * 24 * 3600}
