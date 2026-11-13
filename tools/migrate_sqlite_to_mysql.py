# -*- coding: utf-8 -*-
"""把旧的 SQLite 库（data/app.db）导入到内嵌 MySQL。

用法：
    python tools/migrate_sqlite_to_mysql.py                    # 自动找 data/app.db
    python tools/migrate_sqlite_to_mysql.py --src 别的.db
    python tools/migrate_sqlite_to_mysql.py --dry-run          # 只统计不写入

做了什么：
  1. 启动/连接内嵌 MySQL，建库建表（与 db.SCHEMA 一致）
  2. 逐表读 SQLite，按列名对齐后批量写入 MySQL
  3. 保留原始 id，写完把 AUTO_INCREMENT 校正到 max(id)+1
  4. 空表跳过；已存在数据的表默认跳过（--force 覆盖）
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _mysql_cfg():
    from core import config
    cfg = config.init(os.path.join(ROOT, "data"))
    return cfg


def sqlite_tables(sq):
    rows = sq.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name").fetchall()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "data", "app.db"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="目标表已有数据也覆盖")
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        print("找不到 SQLite 库：%s" % args.src)
        return 2

    from core import db, config
    cfg = _mysql_cfg()

    print("源库：%s" % args.src)
    sq = sqlite3.connect(args.src)
    sq.row_factory = sqlite3.Row

    if not args.dry_run:
        print("启动内嵌 MySQL ...")
        db.init(cfg)
        print("MySQL 就绪")
        target_tables = set(db.table_names())
    else:
        target_tables = set()

    src_tables = sqlite_tables(sq)
    print("SQLite 表 %d 张，MySQL 表 %d 张" % (len(src_tables), len(target_tables)))

    total_rows = 0
    skipped = []
    for t in src_tables:
        rows = sq.execute("SELECT * FROM %s" % t).fetchall()
        if not rows:
            continue
        if not args.dry_run and t not in target_tables:
            skipped.append((t, len(rows), "MySQL 无此表"))
            continue
        # 只取两边都有的列，避免老库缺列/多列导致失败
        if args.dry_run:
            cols = [d[0] for d in sq.execute("SELECT * FROM %s LIMIT 0" % t).description]
        else:
            mcols = {r["name"] for r in db.query("PRAGMA table_info(%s)" % t)}
            cols = [d[0] for d in sq.execute("SELECT * FROM %s LIMIT 0" % t).description
                    if d[0] in mcols]
            missing = [d[0] for d in sq.execute("SELECT * FROM %s LIMIT 0" % t).description
                       if d[0] not in mcols]
            if missing:
                print("  [注意] %s 在 MySQL 缺少列，已跳过：%s" % (t, ", ".join(missing)))

        if not cols:
            continue

        if not args.dry_run:
            existing = db.scalar("SELECT COUNT(*) AS c FROM `%s`" % t)
            if existing and not args.force:
                skipped.append((t, len(rows), "目标已有 %d 行" % existing))
                continue
            if existing and args.force:
                db.execute("DELETE FROM `%s`" % t)

            collist = ",".join("`%s`" % c for c in cols)
            ph = ",".join(["?"] * len(cols))
            sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (t, collist, ph)
            data = [tuple(r[c] for c in cols) for r in rows]
            # 分批，避免单条 SQL 过大
            step = 200
            for i in range(0, len(data), step):
                db.executemany(sql, data[i:i + step])
            # 校正自增起点，保证新插入的 id 不会撞已有记录
            try:
                mx = db.scalar("SELECT MAX(id) AS m FROM `%s`" % t)
                if mx:
                    db.execute("ALTER TABLE `%s` AUTO_INCREMENT=%d" % (t, int(mx) + 1))
            except Exception:
                pass
        total_rows += len(rows)
        print("  %-18s %6d 行  -> %s" % (t, len(rows), "已写入" if not args.dry_run else "将写入"))

    if skipped:
        print("\n跳过：")
        for t, n, why in skipped:
            print("  %-18s %6d 行  (%s)" % (t, n, why))

    print("\n合计 %d 行%s" % (total_rows, "（dry-run，未写入）" if args.dry_run else " 已导入"))
    sq.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
