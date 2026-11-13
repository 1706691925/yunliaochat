# -*- coding: utf-8 -*-
"""IP 黑名单：支持单 IP / CIDR 网段 / 前缀通配，内存缓存 + DB 持久化。
回环与本机链路地址豁免，防止管理员把自己锁在门外。"""
import ipaddress
import re
import threading
import time

from core import db, util

_lock = threading.RLock()
_rules = []            # [(matcher_fn, raw, id, expires_at)]
_enabled = True
_last_reload = 0.0
_last_hit_flush = 0.0

IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

SAFE_PREFIXES = ("127.", "::1", "0:0:0:0:0:0:0:1", "fe80:")


def is_safe_ip(ip):
    """本机/回环地址不允许被封，避免自锁"""
    if not ip:
        return True
    return ip.startswith(SAFE_PREFIXES) or ip == "localhost"


def compile_pattern(raw):
    """把一条规则编译成匹配函数；非法返回 None"""
    raw = (raw or "").strip()
    if not raw:
        return None
    # CIDR：192.168.1.0/24
    if "/" in raw:
        try:
            net = ipaddress.ip_network(raw, strict=False)
            return lambda ip, n=net: _in_net(ip, n)
        except ValueError:
            return None
    # 前缀通配：192.168.1.* 或 10.2.*
    if "*" in raw:
        parts = raw.split(".")
        if any(p and (not p.isdigit() and p != "*") for p in parts):
            return None
        prefix = ".".join(p for p in parts if p != "*")
        if prefix == "":
            return lambda ip: True
        return lambda ip, pf=prefix: ip == pf or ip.startswith(pf + ".")
    # 纯 IPv4 精确
    m = IPV4_RE.match(raw)
    if m and all(int(g) <= 255 for g in m.groups()):
        return lambda ip, r=raw: ip == r
    # IPv6 精确
    try:
        ipaddress.ip_address(raw)
        return lambda ip, r=raw: ip == r
    except ValueError:
        return None


def _in_net(ip, net):
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False


def reload_rules(force=False):
    """从 DB 重载规则；定期或写操作后调用"""
    global _rules, _enabled, _last_reload
    now = time.time()
    if not force and now - _last_reload < 3.0:
        return
    with _lock:
        try:
            _enabled = str(db.get_setting("ip_blacklist_enabled", "1")) != "0"
            ts = util.now_ts()
            rows = db.query("SELECT * FROM ip_blacklist")
            rules = []
            expired = []
            for r in rows:
                if r["expires_at"] and r["expires_at"] < ts:
                    expired.append(r["id"])
                    continue
                fn = compile_pattern(r["pattern"])
                if fn:
                    rules.append((fn, r["pattern"], r["id"], r["expires_at"]))
            if expired:
                ph = ",".join("?" * len(expired))
                db.execute("DELETE FROM ip_blacklist WHERE id IN (%s)" % ph, tuple(expired))
                db.log_action(0, "系统", "IP黑名单", "自动解封",
                              "过期规则已移除 %d 条" % len(expired), "")
            _rules = rules
        except Exception:
            _rules = []
        _last_reload = now


def blocked(ip):
    """请求入口判定：返回命中的规则 (id, pattern)，否则 None"""
    if not _enabled or not ip or is_safe_ip(ip):
        return None
    for fn, raw, rid, exp in list(_rules):
        try:
            if fn(ip):
                return rid, raw
        except Exception:
            continue
    return None


def allowed(ip, whitelist_text):
    """IP 白名单判定：whitelist_text 逗号分隔（单 IP / CIDR / 前缀通配），ip 命中任一规则则放行。
    白名单为空 → 全部放行（未启用白名单）。"""
    raw = (whitelist_text or "").strip()
    if not raw:
        return True
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        fn = compile_pattern(item)
        if fn:
            try:
                if fn(ip):
                    return True
            except Exception:
                continue
    return False


def touch_hit(rule_id):
    """命中计数按 30 秒节流合并写，避免每请求 UPDATE"""
    global _last_hit_flush
    now = time.time()
    if now - _last_hit_flush < 30:
        return
    _last_hit_flush = now
    try:
        db.execute("UPDATE ip_blacklist SET hits = hits + 1 WHERE id=?", (rule_id,))
    except Exception:
        pass


def add(pattern, reason="", hours=0, operator_id=0, operator="", ip=""):
    """新增规则；返回 (ok, msg)"""
    pattern = (pattern or "").strip()
    fn = compile_pattern(pattern)
    if not fn:
        return False, "格式不合法：支持单IP（192.168.1.23）、CIDR 网段（192.168.1.0/24）或前缀通配（192.168.1.*）"
    if is_safe_ip(pattern):
        return False, "本机/回环地址不允许加入黑名单，避免把自己锁在门外"
    if db.one("SELECT id FROM ip_blacklist WHERE pattern=?", (pattern,)):
        return False, "该规则已存在"
    exp = util.now_ts() + int(hours) * 3600 if hours else 0
    db.insert("ip_blacklist", {
        "pattern": pattern, "reason": (reason or "")[:50], "expires_at": exp,
        "hits": 0, "created_by": operator_id or 0, "created_at": util.now_ts(),
    })
    db.log_action(operator_id, operator, "IP黑名单", pattern,
                  "加入黑名单" + (("（原因：%s）" % reason) if reason else "") +
                  ("，%d 小时后自动解封" % hours if hours else "，永久"), ip)
    reload_rules(True)
    return True, "已加入黑名单"


def remove(rule_id, operator_id=0, operator="", ip=""):
    r = db.one("SELECT * FROM ip_blacklist WHERE id=?", (rule_id,))
    if not r:
        return False, "规则不存在"
    db.delete("ip_blacklist", "id=?", (rule_id,))
    db.log_action(operator_id, operator, "IP黑名单", r["pattern"], "移出黑名单", ip)
    reload_rules(True)
    return True, "已移出黑名单"


def set_enabled(on, operator_id=0, operator="", ip=""):
    db.set_setting("ip_blacklist_enabled", "1" if on else "0")
    db.log_action(operator_id, operator, "IP黑名单",
                  "总开关", "启用" if on else "停用", ip)
    reload_rules(True)
