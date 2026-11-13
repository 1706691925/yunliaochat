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
"""认证 / 密码 / 会话 / 角色权限（PBKDF2-SHA256，标准库实现）"""
import base64
import hashlib
import hmac
import os
import secrets
import time

from core import util

PBKDF2_ITER = 120000

# ---------------- 角色与权限 ----------------
ROLES = {
    "superadmin": {"name": "超级管理员", "level": 100},
    "admin": {"name": "系统管理员", "level": 80},
    "teacher": {"name": "讲师/培训专员", "level": 60},
    "student": {"name": "学员", "level": 20},
}

# 隐身巡查专用账号：仅限用户名为 admin 的单个用户（不是所有管理员）
PATROL_USERNAME = "admin"


def is_patrol_admin(user):
    """是否隐身巡查账号：用户名为 admin 的单个用户（其他管理员一律不算）"""
    return bool(user and str(user.get("username") or "") == PATROL_USERNAME)

# 权限分组：(权限键, 中文名)。用于后台权限矩阵渲染，按业务域归类。
# 粗粒度权限（如 user.manage）保留以兼容既有路由与既有角色配置，
# 通过 IMPLIES 自动展开为细粒度权限，因此细化不会破坏历史数据。
PERM_GROUPS = [
    {"key": "system", "name": "系统与后台", "perms": [
        ("admin.access", "进入管理后台"),
        ("settings.manage", "系统参数设置"),
        ("log.view", "审计日志查看"),
        ("log.clear", "审计日志清理"),
        ("backup.manage", "备份与恢复"),
        ("db.view", "数据库结构/数据查看"),
        ("db.manage", "数据库数据修改"),
        ("db.sql", "执行自定义 SELECT 查询"),
        ("stats.view", "统计报表查看"),
        ("stats.export", "统计报表导出"),
    ]},
    {"key": "user", "name": "用户与组织", "perms": [
        ("user.manage", "用户管理（总权限）"),
        ("user.view", "查看用户列表"),
        ("user.create", "新建用户"),
        ("user.edit", "编辑用户资料"),
        ("user.role", "调整角色与权限"),
        ("user.disable", "停用/启用账号"),
        ("user.blacklist", "用户黑名单管理"),
        ("user.delete", "删除用户"),
        ("user.resetpwd", "重置用户密码"),
        ("user.import", "批量导入用户"),
        ("user.export", "导出用户 CSV"),
        ("dept.manage", "部门管理"),
        ("profile.approve", "姓名/头衔修改审核"),
    ]},
    {"key": "room", "name": "房间与消息", "perms": [
        ("room.manage", "房间管理（总权限）"),
        ("room.create", "创建房间"),
        ("room.edit", "编辑房间设置"),
        ("room.delete", "删除/归档房间"),
        ("room.member", "房间成员管理"),
        ("message.moderate", "消息审核（总权限）"),
        ("message.recall", "撤回他人消息"),
        ("message.delete", "删除消息"),
        ("message.mute", "禁言/解禁用户"),
        ("message.flag", "标记消息待审"),
        ("announce.manage", "公告管理（总权限）"),
        ("announce.publish", "发布公告"),
        ("announce.delete", "删除公告"),
    ]},
    {"key": "learn", "name": "课程与考试", "perms": [
        ("course.manage", "课程管理（总权限）"),
        ("course.create", "创建课程"),
        ("course.edit", "编辑课程/课时"),
        ("course.delete", "删除课程"),
        ("material.manage", "资料库管理（总权限）"),
        ("material.upload", "上传资料"),
        ("material.delete", "删除资料"),
        ("question.manage", "题库管理（总权限）"),
        ("question.create", "新增题目"),
        ("question.delete", "删除题目"),
        ("exam.manage", "考试管理（总权限）"),
        ("exam.create", "创建/组卷考试"),
        ("exam.delete", "删除考试"),
        ("exam.grade", "阅卷与批阅"),
        ("assignment.manage", "作业管理（总权限）"),
        ("assignment.grade", "批阅作业"),
    ]},
    {"key": "ops", "name": "运维与协助", "perms": [
        ("remote.view", "查看被控端在线列表"),
        ("remote.request", "发起远程协助请求"),
        ("remote.control", "远程操作鼠标键盘"),
        ("game.manage", "小游戏开关管理"),
    ]},
]

# 扁平权限清单（由分组生成，保证 UI 与校验同源）
ALL_PERMS = [p for g in PERM_GROUPS for p, _ in g["perms"]]
PERM_LABELS = {p: n for g in PERM_GROUPS for p, n in g["perms"]}

# 粗粒度 → 细粒度隐含关系：持有总权限即自动拥有其下全部子权限。
# 这样旧库中已存在的 user.manage / room.manage 等配置无需迁移即可继续生效。
IMPLIES = {
    "user.manage": ["user.view", "user.create", "user.edit", "user.role", "user.disable",
                    "user.blacklist", "user.delete", "user.resetpwd", "user.import",
                    "user.export", "profile.approve"],
    "room.manage": ["room.create", "room.edit", "room.delete", "room.member"],
    "message.moderate": ["message.recall", "message.delete", "message.mute", "message.flag"],
    "announce.manage": ["announce.publish", "announce.delete"],
    "course.manage": ["course.create", "course.edit", "course.delete"],
    "material.manage": ["material.upload", "material.delete"],
    "question.manage": ["question.create", "question.delete"],
    "exam.manage": ["exam.create", "exam.delete"],
    "assignment.manage": ["assignment.grade"],
    "settings.manage": ["log.clear", "stats.export"],
    "db.manage": ["db.view", "db.sql"],
    "remote.control": ["remote.view", "remote.request"],
}

# 角色默认权限（superadmin 恒定拥有全部，见 has_perm）
ROLE_PERMS = {
    "superadmin": ALL_PERMS,
    "admin": ["admin.access", "user.manage", "dept.manage", "room.manage", "message.moderate",
              "announce.manage", "course.manage", "material.manage", "question.manage",
              "exam.manage", "exam.grade", "assignment.manage", "log.view", "backup.manage",
              "stats.view", "stats.export", "remote.view", "remote.request", "remote.control"],
    "teacher": ["admin.access", "course.manage", "material.manage", "question.manage",
                "exam.manage", "exam.grade", "assignment.manage", "stats.view"],
    "student": [],
}

# 角色权限覆盖：管理员在后台矩阵中自定义后写入 settings 表，运行时加载。
# key 为 settings 表键名，value 为 {role: [perm, ...]}
ROLE_OVERRIDE_KEY = "role_perm_override"
_role_override = {}


def load_role_overrides(mapping=None):
    """载入角色权限覆盖（传 None 时从数据库读取，幂等可重复调用）"""
    global _role_override
    if mapping is None:
        try:
            import json
            from core import db
            raw = db.get_setting(ROLE_OVERRIDE_KEY, "")
            mapping = json.loads(raw) if raw else {}
        except Exception:
            mapping = {}
    if not isinstance(mapping, dict):
        mapping = {}
    _role_override = {str(k): [str(x) for x in (v or []) if str(x) in PERM_LABELS]
                      for k, v in mapping.items() if str(k) in ROLE_PERMS}
    return _role_override


def role_overrides():
    return dict(_role_override)


def save_role_overrides(mapping):
    """持久化角色权限覆盖并立即生效"""
    import json
    from core import db
    load_role_overrides(mapping)
    db.set_setting(ROLE_OVERRIDE_KEY, json.dumps(_role_override, ensure_ascii=False))
    return dict(_role_override)


def base_role_perms(role):
    """角色的基准权限（覆盖优先，其次默认），不含 extra_perms"""
    if _role_override.get(role) is not None:
        return list(_role_override[role])
    return list(ROLE_PERMS.get(role, []))


def expand_perms(perms):
    """把粗粒度总权限展开为含全部子权限的集合"""
    out = []
    seen = set()
    stack = list(perms or [])
    while stack:
        p = stack.pop(0)
        p = (p or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
        for child in IMPLIES.get(p, []):
            if child not in seen:
                stack.append(child)
    return out


def role_name(role):
    return ROLES.get(role, {}).get("name", role or "未知")


def perms_of(role, extra=None):
    p = base_role_perms(role)
    if role != "superadmin":
        for x in (extra or "").split(","):
            x = x.strip()
            if x and x not in p:
                p.append(x)
    return p


def effective_perms(user):
    """用户实际生效的完整权限集合（已展开细粒度）"""
    if not user:
        return []
    if user.get("role") == "superadmin":
        return list(ALL_PERMS)
    return expand_perms(perms_of(user.get("role"), user.get("extra_perms")))


def has_perm(user, perm):
    if not user:
        return False
    if user.get("role") == "superadmin":
        return True
    granted = user.get("perms")
    if not granted:
        granted = perms_of(user.get("role"), user.get("extra_perms"))
    if perm in granted:
        return True
    # 反向隐含：任一已授予的总权限若隐含目标权限，则视为拥有
    for g in granted:
        if perm in IMPLIES.get(g, ()):
            return True
    return False


# ---------------- 密码 ----------------
def hash_password(pwd, salt=None, iters=PBKDF2_ITER):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), iters)
    return "pbkdf2$%d$%s$%s" % (iters, salt, base64.b64encode(dk).decode())


def verify_password(pwd, stored):
    try:
        algo, iters, salt, h = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), int(iters))
        return hmac.compare_digest(base64.b64encode(dk).decode(), h)
    except Exception:
        return False


def password_error(pwd, min_len=8, need_complex=True):
    pwd = pwd or ""
    if len(pwd) < min_len:
        return "密码长度不能少于 %d 位" % min_len
    if need_complex:
        kinds = sum([bool(any(c.islower() for c in pwd)),
                     bool(any(c.isupper() for c in pwd)),
                     bool(any(c.isdigit() for c in pwd)),
                     bool(any(not c.isalnum() for c in pwd))])
        if kinds < 2:
            return "密码需至少包含大小写字母、数字、特殊字符中的两类"
    return ""


def weak_password_check(pwd):
    weak = {"123456", "12345678", "123456789", "admin123", "password", "qwerty",
            "abc123", "111111", "000000", "iloveyou", "a123456", "admin@123"}
    return str(pwd or "").lower() in weak


# ---------------- 令牌 / 会话 ----------------
def new_token(n=32):
    return secrets.token_urlsafe(n)


def sign(payload, secret):
    """简单 HMAC 签名令牌：payload 为字符串"""
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return payload + "." + sig


def unsign(token, secret, max_age=None):
    try:
        payload, sig = token.rsplit(".", 1)
        expect = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        if max_age:
            ts = int(payload.split("|")[0])
            if time.time() - ts > max_age:
                return None
        return payload
    except Exception:
        return None


def sha256_hex(b):
    return hashlib.sha256(b if isinstance(b, bytes) else str(b).encode()).hexdigest()


def gen_initial_password():
    return "Lc@" + secrets.token_hex(3) + "9"


# ---------------- 敏感词 ----------------
class SensitiveFilter(object):
    def __init__(self, words=None, replace="***"):
        self.words = [w for w in (words or []) if w]
        self.replace = replace

    def reload(self, words, replace="***"):
        self.words = [w for w in words if w]
        self.replace = replace

    def hit(self, text):
        hits = []
        for w in self.words:
            if w and w in (text or ""):
                hits.append(w)
        return hits

    def filter(self, text):
        out = text or ""
        for w in self.words:
            if w and w in out:
                out = out.replace(w, self.replace)
        return out


# ---------------- 登录失败锁定 ----------------
def login_lock_info(user, cfg):
    """返回 (locked:bool, remain_seconds:int)"""
    lu = user.get("locked_until") or 0
    if lu and time.time() < lu:
        return True, int(lu - time.time())
    return False, 0


def should_lock(failed, cfg):
    """failed 为本次失败后的累计次数"""
    return int(failed or 0) >= int(cfg.get("max_failed_login", 5))
