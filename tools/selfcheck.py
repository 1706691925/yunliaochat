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
"""仓库自检脚本（CI 与本地通用，只依赖标准库）

用法：
    python tools/selfcheck.py            # 全部检查
    python tools/selfcheck.py --quick    # 跳过冒烟测试（不起服务）

检查项：
    1. 全量语法检查（py_compile）
    2. 零依赖约束：业务代码必须经 core/db.py 访问数据库
    3. 仓库卫生：不应有真实数据 / 证书 / 密钥入库
    4. 冒烟测试：全新安装 → 登录 → 核心接口（默认执行）
"""
import os
import pathlib
import py_compile
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 只有这几个文件可以直接接触具体的数据库驱动 / 引擎
DRIVER_ALLOW = {
    "core/db_sqlite.py",
    "core/db_mysql.py",
    "core/mysql_server.py",
    "tools/migrate_sqlite_to_mysql.py",
    "tools/selfcheck.py",
}
DRIVER_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(sqlite3|pymysql)\b|from\s+(sqlite3|pymysql)\b)", re.M)

BANNED_SUFFIX = {".db", ".db-wal", ".db-shm", ".pem", ".key", ".pfx", ".crt", ".sql"}
BANNED_DIRS = {"data", "cert", "vendor"}

SKIP_PARTS = {".git", "vendor", "build", "dist", "__pycache__", "node_modules"}


def walk_files(suffix=None):
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        parts = set(p.relative_to(ROOT).parts)
        if parts & SKIP_PARTS:
            continue
        if suffix and p.suffix.lower() != suffix:
            continue
        yield p


def check_syntax():
    bad = []
    for p in walk_files(".py"):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append("%s: %s" % (p.relative_to(ROOT).as_posix(), e))
    if bad:
        print("[1/5] 语法检查：失败 %d 个" % len(bad))
        for b in bad:
            print("      " + b)
        return False
    print("[1/5] 语法检查：通过")
    return True


def check_no_direct_driver():
    bad = []
    for p in walk_files(".py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel in DRIVER_ALLOW:
            continue
        # 用 utf-8-sig 读取：Windows 下用记事本/PowerShell 保存的文件常带 BOM，
        # 普通 utf-8 读出来首行是 \ufeffimport ...，会让 ^\s*import 失配而漏检。
        txt = p.read_text(encoding="utf-8-sig", errors="replace")
        for m in DRIVER_IMPORT_RE.finditer(txt):
            bad.append("%s: %s" % (rel, m.group(0).strip()))
    if bad:
        print("[2/5] 零依赖约束：有文件绕过 core/db.py")
        for b in bad[:20]:
            print("      " + b)
        return False
    print("[2/5] 零依赖约束：通过")
    return True


def check_repo_hygiene():
    bad = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if ".git" in rel.parts or "vendor" in rel.parts:
            continue
        if set(rel.parts) & BANNED_DIRS:
            bad.append(rel.as_posix())
            continue
        if p.suffix.lower() in BANNED_SUFFIX:
            bad.append(rel.as_posix())
    if bad:
        print("[3/5] 仓库卫生：发现不应存在的文件 %d 个" % len(bad))
        for b in bad[:20]:
            print("      " + b)
        return False
    print("[3/5] 仓库卫生：通过")
    return True


def check_license():
    """许可证必须存在且是 GNU AGPL-3.0。

    许可证文件属于易被误删/误改的内容（例如重新初始化仓库、
    或换成别的协议后忘记同步 README 里的声明），因此单列一项检查。
    这里同时校验「许可证原文」与「源文件版权头」两处，避免只改一处。
    """
    lic = ROOT / "LICENSE"
    if not lic.is_file():
        print("[4/5] 许可证：缺少 LICENSE 文件")
        return False
    txt = lic.read_text(encoding="utf-8", errors="replace")
    required = (
        "GNU AFFERO GENERAL PUBLIC LICENSE",
        "Version 3, 19 November 2007",
        # 第 13 条是 AGPL 与 GPL 的核心差异（网络交互也要开源）
        "13. Remote Network Interaction",
        "END OF TERMS AND CONDITIONS",
    )
    missing = [k for k in required if k not in txt]
    if missing:
        print("[4/5] 许可证：LICENSE 不是完整的 AGPL-3.0，缺少：%s"
              % "、".join(missing))
        return False
    # 顶部应是我们自己的版权声明（AGPL 附录模板里的 <one line to give ...> 未填则为错）
    head = txt[:600]
    if "Copyright (C) 2026" not in head:
        print("[4/5] 许可证：LICENSE 顶部缺少本项目 Copyright 版权声明")
        return False

    # README 里的许可证声明必须同步，否则自相矛盾
    for name in ("README.md", "README.en.md"):
        rd = ROOT / name
        if rd.is_file():
            rtxt = rd.read_text(encoding="utf-8", errors="replace")
            if "AGPL" not in rtxt:
                print("[4/5] 许可证：%s 未声明 AGPL" % name)
                return False

    # 源文件版权头抽查：任意一个 .py 仍写别的协议，说明替换不彻底
    stale = []
    for p in ROOT.rglob("*.py"):
        if set(p.parts) & {"vendor", "build", "dist", "__pycache__"}:
            continue
        head = p.read_text(encoding="utf-8-sig", errors="replace")[:800]
        if "GNU Affero General Public License" not in head:
            stale.append(p.relative_to(ROOT).as_posix())
    if stale:
        print("[4/5] 许可证：以下源文件缺少 AGPL 版权头（共 %d 个）："
              % len(stale))
        for s in stale[:10]:
            print("      " + s)
        return False

    print("[4/5] 许可证：通过（AGPL-3.0，含源文件版权头）")
    return True


def check_smoke(port=8199):
    """全新安装并跑通核心接口（需要能起本地服务）。"""
    import json
    import shutil
    import socket
    import subprocess
    import time
    import urllib.request

    data_dir = ROOT / "data"
    if data_dir.exists():
        shutil.rmtree(str(data_dir), ignore_errors=True)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "app.py", "--port", str(port), "--no-browser", "--quiet"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(60):
            s = socket.socket()
            s.settimeout(0.4)
            try:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    ready = True
                    break
            finally:
                s.close()
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if not ready:
            print("[5/5] 冒烟测试：服务启动失败")
            return False

        pw = None
        pwd_file = data_dir / "初始管理员密码.txt"
        if pwd_file.exists():
            m = re.search(r"密码：\s*(\S+)", pwd_file.read_text(encoding="utf-8"))
            if m:
                pw = m.group(1)
        if not pw:
            print("[5/5] 冒烟测试：未生成初始管理员密码")
            return False

        base = "http://127.0.0.1:%d" % port

        def call(path, data=None, token=None):
            req = urllib.request.Request(base + path)
            if data is not None:
                req.data = json.dumps(data).encode("utf-8")
                req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("x-token", token)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))

        j = call("/api/auth/login", {"username": "admin", "password": pw})
        if not j.get("ok"):
            print("[5/5] 冒烟测试：登录失败 %s" % j)
            return False
        tok = j["data"]["token"]

        eps = ["/api/auth/me", "/api/chat/rooms", "/api/admin/dashboard",
               "/api/admin/db/tables", "/api/learn/courses", "/api/announcements",
               "/api/admin/users", "/api/admin/backups", "/api/files/folders"]
        for ep in eps:
            r = call(ep, token=tok)
            if not r.get("ok"):
                print("[5/5] 冒烟测试：%s 返回异常 %s" % (ep, r))
                return False

        d = call("/api/admin/dashboard", token=tok)["data"]
        if not d["server"]["db_engine"].startswith("SQLite"):
            print("[5/5] 冒烟测试：引擎应为 SQLite，实际 %s" % d["server"]["db_engine"])
            return False
        if d["users"] < 8 or d["rooms"] < 4:
            print("[5/5] 冒烟测试：种子数据不完整 users=%s rooms=%s"
                  % (d["users"], d["rooms"]))
            return False

        print("[5/5] 冒烟测试：通过（%d 个接口，引擎=%s，用户=%d，房间=%d）"
              % (len(eps), d["server"]["db_engine"], d["users"], d["rooms"]))
        return True
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def main():
    quick = "--quick" in sys.argv
    # 顺序与编号一致：1 语法 → 2 零依赖 → 3 仓库卫生 → 4 许可证 → 5 冒烟
    results = [check_syntax(),
               check_no_direct_driver(),
               check_repo_hygiene(),
               check_license()]
    if quick:
        print("[5/5] 冒烟测试：已跳过（--quick）")
    else:
        results.append(check_smoke())
    ok = all(results)
    print()
    print("结果：" + ("全部通过 ✔" if ok else "存在失败项 ✘"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
