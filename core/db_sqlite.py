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
"""云聊 —— SQLite 引擎后端

零依赖模式：只用 Python 标准库自带的 sqlite3，不需要安装任何数据库。
SQL 语法沿用项目原本的 SQLite 写法（? 占位符、INSERT OR IGNORE、
INSERT OR REPLACE、PRAGMA table_info），因此不需要任何方言翻译。

引擎接口由 core/db.py 定义并调用，见该文件顶部说明。
"""
import os
import re
import sqlite3
import threading


# ---------------------------------------------------------------- DDL 翻译
# 项目只有一份表结构定义（core/db.py 的 SCHEMA，MySQL 语法）。
# SQLite 引擎把它按需翻译过来，避免维护两套 30 张表的 DDL。

_RE_AUTOINC_PK = re.compile(
    r"`(\w+)`\s+(?:BIGINT|INT|INTEGER)\s+NOT\s+NULL\s+AUTO_INCREMENT\s+PRIMARY\s+KEY",
    re.I)
_RE_TYPE = [
    (re.compile(r"\bBIGINT\b|\bSMALLINT\b|\bTINYINT\b|\bINT\b|\bINTEGER\b", re.I), "INTEGER"),
    (re.compile(r"\bVARCHAR\s*\(\s*\d+\s*\)|\bCHAR\s*\(\s*\d+\s*\)|\bLONGTEXT\b|"
                r"\bMEDIUMTEXT\b|\bTEXT\b", re.I), "TEXT"),
    (re.compile(r"\bDOUBLE\b|\bFLOAT\b|\bREAL\b", re.I), "REAL"),
    (re.compile(r"\bDECIMAL\s*\(\s*\d+\s*,\s*\d+\s*\)", re.I), "REAL"),
    (re.compile(r"\bDATETIME\b|\bTIMESTAMP\b", re.I), "TEXT"),
]
_RE_INLINE_KEY = re.compile(r"^\s*(?:UNIQUE\s+)?KEY\s+`(\w+)`\s*(\([^)]*\))", re.I)
_RE_TABLE_SUFFIX = re.compile(
    r"\)\s*ENGINE\s*=\s*\w+[^;]*;", re.I)


def mysql_ddl_to_sqlite(ddl):
    """把 MySQL 的 CREATE TABLE 脚本翻译成 SQLite 可执行脚本。

    返回 (主脚本, 索引语句列表)。SQLite 也接受反引号标识符，
    所以列名不用改写，主要处理类型、自增主键、行内 KEY 与表尾选项。
    """
    body_parts = []
    index_stmts = []
    for stmt in _split_statements(ddl):
        m = re.match(r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`(\w+)`\s*\((.*)\)\s*"
                     r"(?:ENGINE[^;]*)?;?\s*$", stmt, re.I | re.S)
        if not m:
            body_parts.append(stmt.rstrip(";") + ";")
            continue
        table, body = m.group(1), m.group(2)
        cols = []
        for line in body.splitlines():
            s = line.strip().rstrip(",")
            if not s:
                continue
            km = _RE_INLINE_KEY.match(s)
            if km:
                idx_name, idx_cols = km.group(1), km.group(2)
                index_stmts.append(
                    "CREATE INDEX IF NOT EXISTS `%s` ON `%s` %s;"
                    % (idx_name, table, idx_cols))
                continue
            # 行内自增主键 -> SQLite 的 INTEGER PRIMARY KEY AUTOINCREMENT
            if _RE_AUTOINC_PK.search(s):
                col = _RE_AUTOINC_PK.search(s).group(1)
                cols.append("`%s` INTEGER PRIMARY KEY AUTOINCREMENT" % col)
                continue
            for rx, rep in _RE_TYPE:
                s = rx.sub(rep, s)
            # TEXT DEFAULT ('') -> TEXT DEFAULT ''
            s = re.sub(r"DEFAULT\s*\(\s*('(?:[^']|'')*')\s*\)", r"DEFAULT \1", s)
            s = re.sub(r"\bUNSIGNED\b", "", s, flags=re.I)
            s = re.sub(r"\s+", " ", s).strip()
            cols.append(s)
        body_parts.append(
            "CREATE TABLE IF NOT EXISTS `%s` (\n  %s\n);"
            % (table, ",\n  ".join(cols)))
    return "\n".join(body_parts), index_stmts


def _split_statements(script):
    """按分号切分 SQL（跳过字符串内的分号）。"""
    out, buf, i, n = [], [], 0, len(script)
    in_str = False
    quote = ""
    while i < n:
        ch = script[i]
        if in_str:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(script[i + 1]); i += 2; continue
            if ch == quote:
                in_str = False
            i += 1; continue
        if ch in ("'", '"'):
            in_str = True; quote = ch; buf.append(ch); i += 1; continue
        if ch == "-" and i + 1 < n and script[i + 1] == "-":
            while i < n and script[i] not in "\r\n":
                i += 1
            continue
        if ch == ";":
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []; i += 1; continue
        buf.append(ch); i += 1
    s = "".join(buf).strip()
    if s:
        out.append(s)
    return out


class SQLiteEngine(object):
    """SQLite 引擎：每线程一条连接，WAL 模式提升并发读写。"""

    name = "sqlite"
    display_name = "SQLite（Python 内置，零依赖）"

    def __init__(self, data_dir, quiet=True):
        self.data_dir = os.path.abspath(data_dir)
        self.db_path = os.path.join(self.data_dir, "app.db")
        self.quiet = quiet
        self._local = threading.local()

    # ---------------- 生命周期 ----------------
    def open(self, cfg=None):
        """打开数据库（首次会自动创建文件），返回连接信息。"""
        os.makedirs(self.data_dir, exist_ok=True)
        conn = self.conn()
        # WAL：读写不互相阻塞；NORMAL：兼顾安全与速度
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL",
                       "foreign_keys=ON", "temp_store=MEMORY"):
            try:
                conn.execute("PRAGMA " + pragma)
            except Exception:
                pass
        return self.info()

    def close(self):
        try:
            c = getattr(self._local, "conn", None)
            if c is not None:
                c.close()
                self._local.conn = None
        except Exception:
            pass

    def info(self):
        return {"engine": "sqlite", "path": self.db_path}

    # ---------------- 连接 ----------------
    def conn(self):
        """每线程一条连接（sqlite3 连接不能跨线程共享）。

        isolation_level=None 即 autocommit，与 MySQL 引擎语义一致：
        每条语句自成事务，避免隐式事务长期占用写锁。
        """
        c = getattr(self._local, "conn", None)
        if c is not None:
            return c
        c = sqlite3.connect(self.db_path, timeout=30,
                            check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        self._local.conn = c
        return c

    def new_conn(self, read_timeout=120):
        """新建一条连接（read_timeout 仅为接口对齐，SQLite 用 timeout 参数）。"""
        c = sqlite3.connect(self.db_path, timeout=max(30, read_timeout),
                            check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # ---------------- SQL 处理 ----------------
    def prepare(self, sql, args=None):
        """SQLite 原生支持项目里使用的全部语法，无需翻译。"""
        return sql

    def table_info(self, table):
        """PRAGMA table_info，返回 sqlite 原生形状的字典列表。"""
        c = self.conn()
        rows = c.execute("PRAGMA table_info(%s)" % table).fetchall()
        return [{"cid": r["cid"], "name": r["name"], "type": r["type"],
                 "notnull": r["notnull"], "dflt_value": r["dflt_value"],
                 "pk": r["pk"]} for r in rows]

    def normalize(self, row):
        """sqlite3 返回的都是 Python 原生类型，直接转普通 dict。"""
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return row

    # ---------------- 其它 ----------------
    def vacuum(self):
        try:
            self.conn().execute("VACUUM")
        except Exception:
            pass

    def table_names(self):
        rows = self.conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def size(self):
        try:
            total = 0
            for suffix in ("", "-wal", "-shm"):
                p = self.db_path + suffix
                if os.path.isfile(p):
                    total += os.path.getsize(p)
            return total
        except Exception:
            return 0
