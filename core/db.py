# -*- coding: utf-8 -*-
"""云聊 —— 数据访问层（支持 MySQL / SQLite 双引擎）

对外 API 与历史版本保持一致（query/one/execute/insert/update/delete/scalar/
count/tx/vacuum/backup_to/restore_from/get_setting/set_setting/notify/notify_all），
业务代码不需要关心用的是哪个数据库。

引擎由 config.json 的 db_engine 决定：
  · "sqlite"（默认）—— Python 标准库自带，零依赖，单文件，适合个人/小团队
  · "mysql"          —— 内置免安装 MySQL 8.0，适合较大并发与多实例部署

引擎实现在 core/db_sqlite.py 与 core/db_mysql.py，两边提供相同的接口：
  open(cfg) / close() / info() / conn() / new_conn() / prepare(sql,args)
  table_info(t) / normalize(row) / vacuum() / table_names() / size()

并发模型沿用原设计：每线程一条连接（读并行），写操作由 _write_lock 串行化。
"""
import os
import re
import threading
import time

from core import util

_lock = threading.RLock()
_conn = None
_db_cfg = None
_server = None
_engine = None          # 当前引擎实例（SQLiteEngine / MySQLEngine）
_local = threading.local()
_write_lock = threading.RLock()
# 连接代次：恢复备份等重建表结构的操作之后自增，促使各线程丢弃旧连接
_conn_gen = 0


def _drop_thread_conns():
    """标记所有线程连接为过期，下次使用时自动重建。"""
    global _conn_gen
    _conn_gen += 1


def engine():
    """当前引擎实例（未初始化时为 None）。"""
    return _engine


def engine_name():
    return _engine.name if _engine is not None else ""


# ---------------------------------------------------------------- 表结构
# 注意：desc / key / value 是 MySQL 保留字，必须加反引号。
# 局部索引（SQLite 的 WHERE recalled_at=0）MySQL 不支持，
# 改为把 recalled_at 并入索引列，用覆盖索引达到同样的「不回表」效果。
SCHEMA = r"""
CREATE TABLE IF NOT EXISTS `users` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(191) NOT NULL UNIQUE,
  `display_name` VARCHAR(255) NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `role` VARCHAR(32) NOT NULL DEFAULT 'student',
  `extra_perms` TEXT DEFAULT (''),
  `dept` VARCHAR(191) DEFAULT '',
  `position` VARCHAR(191) DEFAULT '',
  `title` VARCHAR(255) DEFAULT '',
  `email` VARCHAR(191) DEFAULT '',
  `phone` VARCHAR(64) DEFAULT '',
  `status` VARCHAR(32) DEFAULT 'active',
  `must_change_pwd` TINYINT DEFAULT 0,
  `failed_attempts` INT DEFAULT 0,
  `locked_until` BIGINT DEFAULT 0,
  `signature` VARCHAR(255) DEFAULT '',
  `theme` VARCHAR(32) DEFAULT 'light',
  `created_at` BIGINT DEFAULT NULL,
  `last_login` BIGINT DEFAULT NULL,
  `last_seen` BIGINT DEFAULT NULL,
  `created_by` BIGINT DEFAULT 0,
  `learn_minutes` INT DEFAULT 0,
  `points` INT DEFAULT 0,
  `games` VARCHAR(255) DEFAULT '',
  `avatar` VARCHAR(64) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sessions` (
  `token` VARCHAR(64) NOT NULL PRIMARY KEY,
  `user_id` BIGINT DEFAULT NULL,
  `created_at` BIGINT DEFAULT NULL,
  `expires_at` BIGINT DEFAULT NULL,
  `ip` VARCHAR(64) DEFAULT '',
  `ua` VARCHAR(512) DEFAULT '',
  `last_seen` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `rooms` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `type` VARCHAR(32) DEFAULT 'public',
  `owner_id` BIGINT DEFAULT NULL,
  `topic` VARCHAR(512) DEFAULT '',
  `dept` VARCHAR(191) DEFAULT '',
  `course_id` BIGINT DEFAULT 0,
  `archived` TINYINT DEFAULT 0,
  `allow_file` TINYINT DEFAULT 1,
  `slow_mode` INT DEFAULT 0,
  `max_members` INT DEFAULT 0,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `room_members` (
  `room_id` BIGINT NOT NULL,
  `user_id` BIGINT NOT NULL,
  `role` VARCHAR(32) DEFAULT 'member',
  `joined_at` BIGINT DEFAULT NULL,
  `last_read_id` BIGINT DEFAULT 0,
  `muted_until` BIGINT DEFAULT 0,
  `notify` TINYINT DEFAULT 1,
  `silent` TINYINT DEFAULT 0,
  `unread_count` INT DEFAULT 0,
  PRIMARY KEY (`room_id`, `user_id`),
  KEY `idx_rm_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `messages` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `room_id` BIGINT DEFAULT NULL,
  `user_id` BIGINT DEFAULT NULL,
  `type` VARCHAR(32) DEFAULT 'text',
  `content` TEXT DEFAULT (''),
  `attachments` TEXT DEFAULT ('[]'),
  `reply_to` BIGINT DEFAULT 0,
  `mentions` TEXT DEFAULT ('[]'),
  `created_at` BIGINT DEFAULT NULL,
  `edited_at` BIGINT DEFAULT 0,
  `recalled_at` BIGINT DEFAULT 0,
  `recalled_by` BIGINT DEFAULT 0,
  `flagged` TINYINT DEFAULT 0,
  `ip` VARCHAR(64) DEFAULT '',
  KEY `idx_msg_room` (`room_id`, `id`),
  KEY `idx_msg_user` (`user_id`),
  KEY `idx_msg_last` (`room_id`, `id`, `recalled_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `files` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(512) DEFAULT NULL,
  `stored` VARCHAR(512) DEFAULT NULL,
  `size` BIGINT DEFAULT NULL,
  `mime` VARCHAR(191) DEFAULT '',
  `uploader_id` BIGINT DEFAULT NULL,
  `room_id` BIGINT DEFAULT 0,
  `kind` VARCHAR(32) DEFAULT 'chat',
  `created_at` BIGINT DEFAULT NULL,
  `downloads` INT DEFAULT 0,
  `pinned` TINYINT DEFAULT 0,
  `folder` VARCHAR(191) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `courses` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `title` VARCHAR(255) NOT NULL,
  `subtitle` VARCHAR(255) DEFAULT '',
  `category` VARCHAR(64) DEFAULT '通用',
  `teacher_id` BIGINT DEFAULT NULL,
  `cover_color` VARCHAR(32) DEFAULT '#2f6fed',
  `desc` TEXT DEFAULT (''),
  `status` VARCHAR(32) DEFAULT 'published',
  `required` TINYINT DEFAULT 0,
  `sort` INT DEFAULT 0,
  `created_at` BIGINT DEFAULT NULL,
  `class_id` BIGINT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `lessons` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `course_id` BIGINT DEFAULT NULL,
  `title` VARCHAR(255) DEFAULT NULL,
  `content` TEXT DEFAULT (''),
  `sort` INT DEFAULT 0,
  `minutes` INT DEFAULT 15,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `enrollments` (
  `course_id` BIGINT NOT NULL,
  `user_id` BIGINT NOT NULL,
  `joined_at` BIGINT DEFAULT NULL,
  `progress` INT DEFAULT 0,
  `finished_lessons` TEXT DEFAULT ('[]'),
  `completed_at` BIGINT DEFAULT 0,
  PRIMARY KEY (`course_id`, `user_id`),
  KEY `idx_enroll_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `materials` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `title` VARCHAR(255) DEFAULT NULL,
  `course_id` BIGINT DEFAULT 0,
  `category` VARCHAR(64) DEFAULT '通用',
  `mtype` VARCHAR(32) DEFAULT 'file',
  `content` TEXT DEFAULT (''),
  `file_id` BIGINT DEFAULT 0,
  `uploader_id` BIGINT DEFAULT NULL,
  `downloads` INT DEFAULT 0,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `questions` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `course_id` BIGINT DEFAULT 0,
  `qtype` VARCHAR(32) DEFAULT 'single',
  `stem` TEXT DEFAULT NULL,
  `options` TEXT DEFAULT ('[]'),
  `answer` TEXT DEFAULT (''),
  `analysis` TEXT DEFAULT (''),
  `score` INT DEFAULT 5,
  `difficulty` VARCHAR(32) DEFAULT 'medium',
  `tags` VARCHAR(512) DEFAULT '',
  `created_by` BIGINT DEFAULT NULL,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `exams` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `course_id` BIGINT DEFAULT 0,
  `title` VARCHAR(255) DEFAULT NULL,
  `desc` TEXT DEFAULT (''),
  `duration` INT DEFAULT 60,
  `total_score` INT DEFAULT 100,
  `pass_score` INT DEFAULT 60,
  `start_at` BIGINT DEFAULT 0,
  `end_at` BIGINT DEFAULT 0,
  `status` VARCHAR(32) DEFAULT 'published',
  `shuffle` TINYINT DEFAULT 1,
  `show_answer` TINYINT DEFAULT 1,
  `max_attempts` INT DEFAULT 1,
  `created_by` BIGINT DEFAULT NULL,
  `created_at` BIGINT DEFAULT NULL,
  `class_id` BIGINT DEFAULT 0,
  `reminded_at` BIGINT DEFAULT 0,
  KEY `idx_exam_class` (`class_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `exam_questions` (
  `exam_id` BIGINT NOT NULL,
  `question_id` BIGINT NOT NULL,
  `score` INT DEFAULT 5,
  `sort` INT DEFAULT 0,
  PRIMARY KEY (`exam_id`, `question_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `exam_records` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `exam_id` BIGINT DEFAULT NULL,
  `user_id` BIGINT DEFAULT NULL,
  `attempt` INT DEFAULT 1,
  `started_at` BIGINT DEFAULT NULL,
  `submitted_at` BIGINT DEFAULT 0,
  `answers` TEXT DEFAULT ('{}'),
  `obj_score` DOUBLE DEFAULT 0,
  `sub_score` DOUBLE DEFAULT 0,
  `total_score` DOUBLE DEFAULT 0,
  `status` VARCHAR(32) DEFAULT 'doing',
  `passed` TINYINT DEFAULT 0,
  `ip` VARCHAR(64) DEFAULT '',
  KEY `idx_exrec_exam` (`exam_id`, `user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `assignments` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `course_id` BIGINT DEFAULT 0,
  `title` VARCHAR(255) DEFAULT NULL,
  `content` TEXT DEFAULT (''),
  `deadline` BIGINT DEFAULT NULL,
  `created_by` BIGINT DEFAULT NULL,
  `created_at` BIGINT DEFAULT NULL,
  `allow_late` TINYINT DEFAULT 1,
  `class_id` BIGINT DEFAULT 0,
  `reminded_at` BIGINT DEFAULT 0,
  KEY `idx_asg_deadline` (`deadline`),
  KEY `idx_asg_class` (`class_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `submissions` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `assignment_id` BIGINT DEFAULT NULL,
  `user_id` BIGINT DEFAULT NULL,
  `content` TEXT DEFAULT (''),
  `file_id` BIGINT DEFAULT 0,
  `submitted_at` BIGINT DEFAULT NULL,
  `late` TINYINT DEFAULT 0,
  `score` DOUBLE DEFAULT 0,
  `feedback` TEXT DEFAULT (''),
  `graded_by` BIGINT DEFAULT 0,
  `graded_at` BIGINT DEFAULT 0,
  `status` VARCHAR(32) DEFAULT 'submitted',
  KEY `idx_sub_asg` (`assignment_id`, `user_id`),
  KEY `idx_sub_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `announcements` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `title` VARCHAR(255) DEFAULT NULL,
  `content` TEXT DEFAULT NULL,
  `level` VARCHAR(32) DEFAULT 'info',
  `pinned` TINYINT DEFAULT 0,
  `scope` VARCHAR(64) DEFAULT 'all',
  `created_by` BIGINT DEFAULT NULL,
  `created_at` BIGINT DEFAULT NULL,
  `expires_at` BIGINT DEFAULT 0,
  `popup` TINYINT DEFAULT 0,
  `force_seconds` INT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `notifications` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `user_id` BIGINT DEFAULT NULL,
  `ntype` VARCHAR(32) DEFAULT 'system',
  `title` VARCHAR(255) DEFAULT NULL,
  `content` TEXT DEFAULT (''),
  `ref_type` VARCHAR(64) DEFAULT '',
  `ref_id` BIGINT DEFAULT 0,
  `is_read` TINYINT DEFAULT 0,
  `created_at` BIGINT DEFAULT NULL,
  KEY `idx_notif_user` (`user_id`, `is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `profile_requests` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `user_id` BIGINT NOT NULL,
  `old_display_name` VARCHAR(255) DEFAULT '',
  `old_title` VARCHAR(255) DEFAULT '',
  `new_display_name` VARCHAR(255) DEFAULT '',
  `new_title` VARCHAR(255) DEFAULT '',
  `reason` VARCHAR(512) DEFAULT '',
  `status` VARCHAR(32) DEFAULT 'pending',
  `review_note` VARCHAR(512) DEFAULT '',
  `reviewed_by` BIGINT DEFAULT 0,
  `reviewed_at` BIGINT DEFAULT 0,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `study_logs` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `user_id` BIGINT DEFAULT NULL,
  `course_id` BIGINT DEFAULT 0,
  `action` VARCHAR(64) DEFAULT '',
  `minutes` INT DEFAULT 0,
  `detail` VARCHAR(512) DEFAULT '',
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `checkins` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `user_id` BIGINT DEFAULT NULL,
  `day` VARCHAR(32) DEFAULT NULL,
  `note` VARCHAR(512) DEFAULT '',
  `created_at` BIGINT DEFAULT NULL,
  UNIQUE KEY `uniq_checkin_user_day` (`user_id`, `day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `ts` BIGINT DEFAULT NULL,
  `actor_id` BIGINT DEFAULT 0,
  `actor` VARCHAR(191) DEFAULT '',
  `action` VARCHAR(191) DEFAULT NULL,
  `target` VARCHAR(255) DEFAULT '',
  `detail` TEXT DEFAULT NULL,
  `ip` VARCHAR(64) DEFAULT '',
  KEY `idx_logs_ts` (`ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sensitive_words` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `word` VARCHAR(191) UNIQUE,
  `level` VARCHAR(32) DEFAULT 'replace'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `depts` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(191) UNIQUE,
  `parent` VARCHAR(191) DEFAULT '',
  `sort` INT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `ip_blacklist` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `pattern` VARCHAR(191) NOT NULL UNIQUE,
  `reason` VARCHAR(255) DEFAULT '',
  `expires_at` BIGINT DEFAULT 0,
  `hits` INT DEFAULT 0,
  `created_by` BIGINT DEFAULT 0,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `quick_sites` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `url` VARCHAR(1024) NOT NULL,
  `category` VARCHAR(64) DEFAULT '常用',
  `note` VARCHAR(512) DEFAULT '',
  `sort` INT DEFAULT 0,
  `enabled` TINYINT DEFAULT 1,
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `settings` (
  `key` VARCHAR(191) NOT NULL PRIMARY KEY,
  `value` TEXT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `meta` (
  `key` VARCHAR(191) NOT NULL PRIMARY KEY,
  `value` TEXT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `game_scores` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `user_id` BIGINT DEFAULT NULL,
  `game` VARCHAR(64) DEFAULT '',
  `score` INT DEFAULT 0,
  `level` INT DEFAULT 0,
  `result` VARCHAR(32) DEFAULT '',
  `mode` VARCHAR(32) DEFAULT 'single',
  `created_at` BIGINT DEFAULT NULL,
  KEY `idx_game_scores_game` (`game`, `score`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `classes` (
  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `term` VARCHAR(64) DEFAULT '',
  `teacher_id` BIGINT DEFAULT NULL,
  `desc` TEXT DEFAULT (''),
  `status` VARCHAR(32) DEFAULT 'active',
  `created_at` BIGINT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `class_members` (
  `class_id` BIGINT NOT NULL,
  `user_id` BIGINT NOT NULL,
  `joined_at` BIGINT DEFAULT NULL,
  PRIMARY KEY (`class_id`, `user_id`),
  KEY `idx_class_members_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


# ---------------------------------------------------------------- SQL 方言与连接
# 具体翻译/连接逻辑在两个引擎实现里，这里只做委托，业务代码无感知。
_RE_PRAGMA_TI = re.compile(r"^\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z0-9_]+)\s*\)\s*$", re.I)


def _pragma_table_info(table):
    """返回与 sqlite 同形的字典（cid/name/type/notnull/dflt_value/pk），
    这样 app.py / ops.py 里的调用点不用改。"""
    return _engine.table_info(table)


def _prepare(sql, args=None):
    """把 SQL 交给当前引擎翻译（SQLite 原样返回，MySQL 转换方言与占位符）。"""
    return _engine.prepare(sql, args)


def _normalize(row):
    """把行转成 JSON 可序列化的普通字典（MySQL 的 Decimal -> int/float）。"""
    return _engine.normalize(row)


def _new_conn(read_timeout=120):
    return _engine.new_conn(read_timeout)


def _thread_conn():
    """每线程一条连接；连接失效时由引擎负责重连。

    引擎换代（例如刚做过恢复备份）后，各线程连接自动重建。
    """
    if getattr(_local, "gen", -1) != _conn_gen:
        try:
            c = getattr(_local, "conn", None)
            if c is not None:
                c.close()
        except Exception:
            pass
        _local.conn = None
    _local.gen = _conn_gen
    return _engine.conn()


def _raw_query(sql, args=()):
    """内部用：已经翻译好的 SQL，不再二次翻译"""
    conn = _thread_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args)) if args else cur.execute(sql)
        rows = cur.fetchall()
        return [_engine.normalize(r) for r in rows]
    finally:
        cur.close()


def init(cfg):
    """按配置选择引擎，打开数据库并建表。

    引擎由 config.json 的 db_engine 决定："sqlite"（默认）或 "mysql"。
    cfg 传 core.config.Config 对象。
    """
    global _db_cfg, _server, _engine
    from core import config as _config

    if isinstance(cfg, str):
        raise RuntimeError(
            "db.init() 需要 Config 对象而不是数据库路径。请改用 db.init(config.CFG)。")

    data_dir = cfg.data_dir
    want = ""
    try:
        want = (cfg.get("db_engine", "sqlite") or "sqlite").strip().lower()
    except Exception:
        want = "sqlite"
    if want not in ("sqlite", "mysql"):
        want = "sqlite"

    if want == "mysql":
        from core import db_mysql
        ms = getattr(cfg, "mysql_settings", None)
        if ms is None:
            ms = {"port": 33070, "database": "yunliao", "user": "yunliao", "password": ""}
        elif callable(ms):      # 兼容把 mysql_settings 写成方法的情况
            ms = ms()
        _engine = db_mysql.MySQLEngine(
            data_dir,
            port=ms.get("port") or 0,
            database=ms.get("database") or "yunliao",
            user=ms.get("user") or "yunliao",
            password=ms.get("password") or "",
            quiet=True)
        _db_cfg = _engine.open(cfg)
        _server = _engine.server
    else:
        from core import db_sqlite
        _engine = db_sqlite.SQLiteEngine(data_dir)
        _db_cfg = _engine.open(cfg)
        _server = None

    with _write_lock:
        _exec_script(SCHEMA)
        _migrate()
    return _db_cfg


def _exec_script(script):
    """执行建表脚本。

    SCHEMA 用 MySQL 语法书写（单一事实源）。SQLite 引擎下先翻译 DDL，
    并在建表后补建索引（SQLite 不支持 CREATE TABLE 里的行内 KEY）。
    """
    conn = _thread_conn()
    cur = conn.cursor()
    try:
        if engine_name() == "sqlite":
            from core import db_sqlite
            script, indexes = db_sqlite.mysql_ddl_to_sqlite(script)
        else:
            indexes = []
        buf = []
        for line in script.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            buf.append(line)
            if stripped.endswith(";"):
                stmt = "\n".join(buf).strip().rstrip(";").strip()
                buf = []
                if not stmt:
                    continue
                try:
                    cur.execute(stmt)
                except Exception as e:
                    raise RuntimeError("建表失败：%s\n--- SQL ---\n%s"
                                       % (e, stmt[:800]))
        for stmt in indexes:
            try:
                cur.execute(stmt.rstrip(";"))
            except Exception:
                pass        # 索引已存在或列不适用时忽略
        conn.commit()
    finally:
        cur.close()


def _migrate():
    """增量迁移：老库补字段（幂等）"""
    def cols(table):
        try:
            return {r["name"] for r in _pragma_table_info(table)}
        except Exception:
            return set()

    def add(table, col, ddl):
        if col not in cols(table):
            # SQLite 的类型是动态的；把 MySQL 类型收敛成它能接受的写法
            if engine_name() == "sqlite":
                if re.search(r"TINYINT|SMALLINT|BIGINT|\bINT\b", ddl, re.I):
                    ddl = re.sub(r"TINYINT|SMALLINT|BIGINT|\bINT\b", "INTEGER", ddl, flags=re.I)
                elif re.search(r"VARCHAR\s*\(\s*\d+\s*\)", ddl, re.I):
                    ddl = re.sub(r"VARCHAR\s*\(\s*\d+\s*\)", "TEXT", ddl, flags=re.I)
            try:
                _raw_execute("ALTER TABLE `%s` ADD COLUMN `%s` %s" % (table, col, ddl))
            except Exception:
                pass

    add("announcements", "popup", "TINYINT DEFAULT 0")
    add("announcements", "force_seconds", "INT DEFAULT 0")
    add("files", "pinned", "TINYINT DEFAULT 0")
    add("files", "folder", "VARCHAR(191) DEFAULT ''")
    try:
        _raw_execute("UPDATE files SET kind='center' WHERE kind='chat'"
                     " AND (room_id IS NULL OR room_id=0)")
    except Exception:
        pass
    add("room_members", "unread_count", "INT DEFAULT 0")
    add("submissions", "status", "VARCHAR(32) DEFAULT 'submitted'")
    add("assignments", "class_id", "BIGINT DEFAULT 0")
    add("exams", "class_id", "BIGINT DEFAULT 0")
    add("courses", "class_id", "BIGINT DEFAULT 0")
    add("assignments", "reminded_at", "BIGINT DEFAULT 0")
    add("exams", "reminded_at", "BIGINT DEFAULT 0")


def _raw_execute(sql, args=()):
    with _write_lock:
        conn = _thread_conn()
        cur = conn.cursor()
        try:
            cur.execute(sql, list(args)) if args else cur.execute(sql)
            conn.commit()
            return cur.rowcount
        finally:
            cur.close()


# ---------------------------------------------------------------- 对外 API
def query(sql, args=()):
    if _RE_PRAGMA_TI.match(sql or ""):
        return _pragma_table_info(_RE_PRAGMA_TI.match(sql).group(1))
    cur = _thread_conn().cursor()
    try:
        cur.execute(_prepare(sql, args), list(args)) if args else cur.execute(_prepare(sql, args))
        return [_normalize(r) for r in cur.fetchall()]
    finally:
        cur.close()


def one(sql, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql, args=()):
    """执行写操作，返回自增主键。

    对 INSERT IGNORE 而言：被忽略（已存在）时返回 None，
    与旧 SQLite 版「cur is None 表示已存在」的约定保持一致。
    """
    is_ignore = bool(re.match(r"^\s*INSERT\s+OR\s+IGNORE\b", sql or "", re.I))
    with _write_lock:
        conn = _thread_conn()
        cur = conn.cursor()
        try:
            cur.execute(_prepare(sql, args), list(args)) if args else cur.execute(_prepare(sql, args))
            conn.commit()
            if is_ignore and cur.rowcount == 0:
                return None
            return cur.lastrowid
        finally:
            cur.close()


def executemany(sql, seq):
    seq = list(seq)
    if not seq:
        return
    with _write_lock:
        conn = _thread_conn()
        cur = conn.cursor()
        try:
            cur.executemany(_prepare(sql, seq[0]), [list(r) for r in seq])
            conn.commit()
        finally:
            cur.close()


def commit():
    with _write_lock:
        _thread_conn().commit()


def scalar(sql, args=()):
    r = one(sql, args)
    if not r:
        return 0
    v = list(r.values())[0]
    return v if v is not None else 0


def count(table, where="1=1", args=()):
    return scalar("SELECT COUNT(*) AS c FROM `%s` WHERE %s" % (table, where), args)


def insert(table, data):
    keys = list(data.keys())
    sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (
        table, ",".join("`%s`" % k for k in keys), ",".join("?" * len(keys)))
    return execute(sql, [data[k] for k in keys])


def update(table, data, where, args=()):
    keys = list(data.keys())
    sql = "UPDATE `%s` SET %s WHERE %s" % (
        table, ",".join("`%s`=?" % k for k in keys), where)
    return execute(sql, [data[k] for k in keys] + list(args))


def delete(table, where, args=()):
    return execute("DELETE FROM `%s` WHERE %s" % (table, where), args)


def tx(func):
    """事务装饰器：写锁包住整段，并保证整段是一个真正的数据库事务。

    连接默认 autocommit=True，所以这里显式 begin，异常时回滚。
    """
    def wrapper(*a, **kw):
        with _write_lock:
            conn = _thread_conn()
            conn.begin()
            try:
                r = func(*a, **kw)
                conn.commit()
                return r
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
    return wrapper


def vacuum():
    """整理数据库（SQLite 走 VACUUM，MySQL 走 OPTIMIZE/ANALYZE）。"""
    with _write_lock:
        try:
            _engine.vacuum()
        except Exception:
            pass


def table_names():
    return _engine.table_names()


# ---------------------------------------------------------------- 备份 / 恢复
_DUMP_HEADER = "-- yunliao backup v2 (engine: %s)"


def backup_to(path):
    """把整库导出为一个 .sql 文本文件（纯 Python，不依赖 mysqldump）。

    引擎无关实现：逐表读数据、拼 INSERT。在线备份，不影响服务运行。
    导出的文件可在任意引擎上恢复（表结构按当前引擎重新生成）。
    """
    tmp = path + ".tmp"
    with _write_lock:
        conn = _thread_conn()
        cur = conn.cursor()
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write((_DUMP_HEADER % engine_name()) + "\n")
                for name in table_names():
                    cols, rows = _dump_table(cur, name)
                    if cols is None:
                        continue
                    collist = ",".join("`%s`" % c for c in cols)
                    # 每 200 行一批，避免单条语句过长
                    batch = []
                    for r in rows:
                        vals = [_sql_literal(r.get(c)) for c in cols]
                        batch.append("(" + ",".join(vals) + ")")
                        if len(batch) >= 200:
                            f.write("INSERT INTO `%s` (%s) VALUES %s;\n"
                                    % (name, collist, ",".join(batch)))
                            batch = []
                    if batch:
                        f.write("INSERT INTO `%s` (%s) VALUES %s;\n"
                                % (name, collist, ",".join(batch)))
            os.replace(tmp, path)
        finally:
            cur.close()


def _dump_table(cur, name):
    """读一张表的列名与全部行；表不存在时返回 (None, [])。

    行统一转成普通 dict（sqlite3.Row 与 pymysql 的 DictCursor 形状不同）。
    """
    try:
        cur.execute("SELECT * FROM `%s`" % name)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = []
        for r in cur.fetchall():
            rows.append(dict(r) if not isinstance(r, dict) else r)
        return cols, rows
    except Exception:
        return None, []


def _sql_literal(v):
    """把 Python 值转成 SQL 字面量。

    两个引擎的字符串转义规则不同，必须分开处理：
      · MySQL ：反斜杠是转义符，需转义 \\ ' \\n \\r \\0
      · SQLite：反斜杠是普通字符，只把单引号写成两个单引号（标准 SQL）
    搞错会让备份里的路径/换行数据损坏。
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (bytes, bytearray)):
        # 两者都接受 X'..' 十六进制字面量；MySQL 的 _binary 前缀改由列类型决定
        return "X'%s'" % bytes(v).hex()
    s = str(v)
    if engine_name() == "mysql":
        s = (s.replace("\\", "\\\\").replace("'", "\\'")
              .replace("\n", "\\n").replace("\r", "\\r").replace("\x00", "\\0"))
    else:
        s = s.replace("'", "''")
    return "'" + s + "'"


def restore_from(path):
    """用 .sql 备份覆盖当前库（不重启进程，各线程连接立即可见新数据）。

    引擎无关实现，分两步：
      1. 清空所有表（用 DELETE，保留表结构，避免跨引擎 DDL 语法差异）；
      2. 执行备份里的 INSERT 语句回填数据。
    备份里若含 DROP/CREATE（旧版 MySQL 备份），会被跳过——表结构由本程序
    的 SCHEMA 统一保证，跨引擎恢复也能正常工作。
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    stmts = _split_sql(content)
    # 只保留 INSERT；DROP/CREATE/SET 一律跳过
    inserts = []
    for s in stmts:
        head = s.lstrip()[:20].upper()
        if head.startswith("INSERT") or head.startswith("REPLACE"):
            inserts.append(s)

    with _write_lock:
        conn = _new_conn()
        cur = conn.cursor()
        done = 0
        try:
            _disable_fk(cur)
            # 1) 清空现有数据（倒序关闭外键后逐表 DELETE）
            for name in table_names():
                try:
                    cur.execute("DELETE FROM `%s`" % name)
                except Exception:
                    pass
            # 2) 回填
            for stmt in inserts:
                cur.execute(stmt)
                done += 1
            _enable_fk(cur)
            try:
                conn.commit()
            except Exception:
                pass
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise RuntimeError(
                "恢复失败（已写入 %d/%d 条）：%s\n"
                "提示：请确认备份文件由本程序导出，且未被改动。"
                % (done, len(inserts), e))
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass
    # 让所有线程连接重建，确保看到恢复后的新数据
    _drop_thread_conns()
    return _conn


def _disable_fk(cur):
    """按引擎关闭外键检查（SQLite 与 MySQL 语法不同）。"""
    if engine_name() == "mysql":
        for sql in ("SET SESSION FOREIGN_KEY_CHECKS=0", "SET SESSION lock_wait_timeout=30"):
            try:
                cur.execute(sql)
            except Exception:
                pass
    else:
        try:
            cur.execute("PRAGMA foreign_keys=OFF")
        except Exception:
            pass


def _enable_fk(cur):
    if engine_name() == "mysql":
        try:
            cur.execute("SET SESSION FOREIGN_KEY_CHECKS=1")
        except Exception:
            pass
    else:
        try:
            cur.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass


def _split_sql(content):
    """把 .sql 文本切成可执行语句（能正确跳过字符串里的分号和注释）。

    反斜杠只有在 MySQL 里才是转义符；SQLite 用 '' 表示单引号，
    所以这里按当前引擎决定是否把 `\\` 当转义，否则含反斜杠的数据会切错。
    """
    stmts = []
    buf = []
    i = 0
    n = len(content)
    in_str = False
    quote = ""
    backslash_escapes = (engine_name() == "mysql")
    while i < n:
        ch = content[i]
        if in_str:
            buf.append(ch)
            if backslash_escapes and ch == "\\" and i + 1 < n:
                buf.append(content[i + 1])
                i += 2
                continue
            if ch == quote:
                # SQLite 用连续两个引号表示一个字面引号，不算结束
                if not backslash_escapes and i + 1 < n and content[i + 1] == quote:
                    buf.append(content[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and content[i + 1] == "-":
            while i < n and content[i] not in "\r\n":
                i += 1
            continue
        if ch == ";":
            s = "".join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    s = "".join(buf).strip()
    if s:
        stmts.append(s)
    return stmts


# ---------------------------------------------------------------- 审计与通知
def log_action(actor_id, actor, action, target="", detail="", ip=""):
    try:
        execute("INSERT INTO audit_logs (ts, actor_id, actor, action, target, detail, ip)"
                " VALUES (?,?,?,?,?,?,?)",
                (util.now_ts(), actor_id, actor, action, target, detail, ip))
    except Exception:
        pass


def get_setting(key, default=None):
    r = one("SELECT `value` FROM settings WHERE `key`=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    execute("INSERT OR REPLACE INTO settings (`key`, `value`) VALUES (?,?)",
            (str(key), str(value)))


def notify(user_id, ntype, title, content="", ref_type="", ref_id=0):
    execute("INSERT INTO notifications (user_id, ntype, title, content, ref_type, ref_id,"
            " is_read, created_at) VALUES (?,?,?,?,?,?,0,?)",
            (user_id, ntype, title, content, ref_type, ref_id, util.now_ts()))


def notify_all(title, content, ntype="system", ref_type="", ref_id=0):
    rows = query("SELECT id FROM users WHERE status='active'")
    ts = util.now_ts()
    executemany("INSERT INTO notifications (user_id, ntype, title, content, ref_type,"
                " ref_id, is_read, created_at) VALUES (?,?,?,?,?,?,0,?)",
                [(r["id"], ntype, title, content, ref_type, ref_id, ts) for r in rows])


def db_size():
    """当前库占用字节数（供后台首页展示）"""
    try:
        return int(_engine.size())
    except Exception:
        return 0


def server():
    """内置 mysqld 实例（SQLite 引擎下为 None）。"""
    return _server
