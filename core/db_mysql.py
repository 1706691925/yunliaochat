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
"""云聊 —— MySQL 引擎后端

用内置的免安装 MySQL 8.0（vendor/mysql），程序托管其启动/关闭，
同时把项目里的 SQLite 方言翻译成 MySQL 方言。

引擎接口由 core/db.py 定义并调用，见该文件顶部说明。
"""
import re

from core import mysql_server

# ---- SQLite 方言 -> MySQL 方言 ----
_RE_PRAGMA_TI = re.compile(r"^\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z0-9_]+)\s*\)\s*$", re.I)
_RE_INSERT_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.I)
_RE_INSERT_REPLACE = re.compile(r"\bINSERT\s+OR\s+REPLACE\b", re.I)
_RE_CAST_TEXT = re.compile(r"\bCAST\s*\(\s*([A-Za-z0-9_\.]+)\s+AS\s+TEXT\s*\)", re.I)


class MySQLEngine(object):
    """MySQL 引擎：托管内置 mysqld，每线程一条连接。"""

    name = "mysql"
    display_name = "MySQL 8.0（内置免安装）"

    def __init__(self, data_dir, port=None, database="yunliao",
                 user="yunliao", password="", quiet=True):
        self.data_dir = data_dir
        self.port_hint = port
        self.database = database
        self.user = user
        self.password = password or ""
        self.quiet = quiet
        self.server = None
        self._local = None   # 由 open() 设置
        self._cfg = None

    # ---------------- 生命周期 ----------------
    def open(self, cfg=None):
        """启动/复用内置 mysqld，建库建用户，返回连接信息。"""
        import threading
        self._local = threading.local()
        srv = mysql_server.MySQLServer(
            self.data_dir, port=self.port_hint or 0,
            database=self.database, user=self.user,
            password=self.password, quiet=self.quiet)
        port = srv.ensure_ready()
        self.server = srv
        # 端口可能被自动避让改过，回写配置，下次直接用同一个端口
        self.port_hint = port
        try:
            if cfg is not None and hasattr(cfg, "set"):
                if int(cfg.get("mysql_port", 0) or 0) != int(port):
                    cfg.set("mysql_port", int(port))
                    cfg.save()
        except Exception:
            pass
        self._cfg = {"host": "127.0.0.1", "port": port,
                     "user": srv.user, "password": srv.password,
                     "database": srv.database}
        return self.info()

    def close(self):
        """只关闭本进程拉起的 mysqld（复用别人启动的实例时不动它）。"""
        try:
            if self.server is not None:
                self.server.stop()
        except Exception:
            pass
        try:
            if self._local is not None:
                c = getattr(self._local, "conn", None)
                if c is not None:
                    c.close()
                    self._local.conn = None
        except Exception:
            pass

    def info(self):
        if self._cfg:
            return dict(self._cfg, engine="mysql")
        return {"engine": "mysql", "port": self.port_hint,
                "database": self.database, "user": self.user}

    # ---------------- 连接 ----------------
    def conn(self):
        """每线程一条连接；失效时自动重连。

        必须 autocommit=True：只读查询不会 commit/rollback，
        若用事务模式会让连接长期持有元数据锁，阻塞 DDL 并读到陈旧快照。
        """
        import pymysql
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.ping(reconnect=True)
                return c
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
        c = self.new_conn()
        self._local.conn = c
        return c

    def new_conn(self, read_timeout=120):
        import pymysql
        if not self._cfg:
            raise RuntimeError("db not initialized")
        return pymysql.connect(
            host=self._cfg["host"], port=self._cfg["port"],
            user=self._cfg["user"], password=self._cfg.get("password", ""),
            database=self._cfg["database"], charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor, autocommit=True,
            connect_timeout=10, read_timeout=read_timeout,
            write_timeout=read_timeout)

    # ---------------- SQL 处理 ----------------
    def prepare(self, sql, args=None):
        """把 SQLite 写法翻译成 MySQL 写法；args 非空时同时转换占位符。"""
        s = sql
        # INSERT OR REPLACE INTO x  ->  REPLACE INTO x（保留原有 INTO，避免出现 INTO INTO）
        s = re.sub(r"\bINSERT\s+OR\s+REPLACE\b", "REPLACE", s, flags=re.I)
        s = _RE_INSERT_IGNORE.sub("INSERT IGNORE", s)
        s = _RE_CAST_TEXT.sub(r"CAST(\1 AS CHAR)", s)
        if args:
            # 顺序关键：先把字面量 % 转义，再把 ? 换成 %s
            s = s.replace("%", "%%").replace("?", "%s")
        return s

    def table_info(self, table):
        """把 PRAGMA table_info 翻译成 information_schema 查询，
        返回与 sqlite 同形的字典（cid/name/type/notnull/dflt_value/pk）。"""
        c = self.conn()
        cur = c.cursor()
        try:
            cur.execute(
                "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type,"
                " IS_NULLABLE AS nullable, COLUMN_DEFAULT AS dflt_value,"
                " COLUMN_KEY AS ckey, EXTRA AS extra"
                " FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s"
                " ORDER BY ORDINAL_POSITION", (table,))
            rows = cur.fetchall()
        finally:
            cur.close()
        out = []
        for i, r in enumerate(rows):
            extra = (r.get("extra") or "").lower()
            out.append({
                "cid": i,
                "name": r["name"],
                "type": r["type"],
                "notnull": 0 if (r.get("nullable") == "YES") else 1,
                "dflt_value": r.get("dflt_value"),
                "pk": 1 if (r.get("ckey") == "PRI" or "auto_increment" in extra) else 0,
            })
        return out

    def normalize(self, row):
        """Decimal -> int/float，bytes -> str（JSON 可序列化）。"""
        from decimal import Decimal
        out = {}
        for k, v in row.items():
            if isinstance(v, Decimal):
                f = float(v)
                v = int(f) if f.is_integer() else f
            elif isinstance(v, (bytes, bytearray)):
                v = v.decode("utf-8", "replace")
            out[k] = v
        return out

    # ---------------- 其它 ----------------
    def vacuum(self):
        for name in self.table_names():
            for sql in ("OPTIMIZE TABLE `%s`" % name, "ANALYZE TABLE `%s`" % name):
                try:
                    self.execute_raw(sql)
                except Exception:
                    pass

    def execute_raw(self, sql):
        c = self.conn()
        cur = c.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    def table_names(self):
        c = self.conn()
        cur = c.cursor()
        try:
            cur.execute(
                "SELECT TABLE_NAME AS name FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'"
                " ORDER BY TABLE_NAME")
            return [r["name"] for r in cur.fetchall()]
        finally:
            cur.close()

    def size(self):
        try:
            c = self.conn()
            cur = c.cursor()
            try:
                cur.execute(
                    "SELECT COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) AS s"
                    " FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
                r = cur.fetchone()
                return int(r["s"] or 0)
            finally:
                cur.close()
        except Exception:
            return 0
