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
"""班级：教学组织的基本单位。

为什么需要它：
  现有系统只有 rooms（聊天）和 depts（部门）。老师带多个班时，靠房间或部门都无法表达
  「这个作业布置给 2026级计算机1班」这种关系。班级是所有教学数据的挂载轴。

设计要点：
  * 一个老师可带多个班；一个学生可属于多个班（跨班级选修）
  * 删除班级不删数据，只归档（status='archived'），保留历史记录
"""
from core import db, security, util
from core.http import ApiError, route
from api import base


def class_view(row, user_id=0):
    d = dict(row)
    d["created_str"] = util.ts_to_str(row["created_at"])
    teacher = db.one("SELECT display_name FROM users WHERE id=?", (row["teacher_id"],))
    d["teacher_name"] = (teacher or {}).get("display_name", "—")
    d["member_count"] = db.scalar("SELECT COUNT(*) AS c FROM class_members WHERE class_id=?",
                                  (row["id"],)) or 0
    d["joined"] = bool(user_id and db.one(
        "SELECT 1 FROM class_members WHERE class_id=? AND user_id=?", (row["id"], user_id)))
    d["can_manage"] = bool(user_id and (
        row["teacher_id"] == user_id))
    return d


# ---------------- 班级 ----------------
@route("/api/learn/classes", "GET")
def classes(ctx):
    where, args = ["1=1"], []
    if ctx.get("status"):
        where.append("status=?")
        args.append(ctx.get("status"))
    else:
        where.append("status='active'")
    if ctx.get("term"):
        where.append("term=?")
        args.append(ctx.get("term"))
    if ctx.int("mine", 0):
        where.append("EXISTS (SELECT 1 FROM class_members m WHERE m.class_id=classes.id AND m.user_id=?)")
        args.append(ctx.user["id"])
    if ctx.get("q"):
        where.append("(name LIKE ? OR `desc` LIKE ?)")
        args += ["%%%s%%" % ctx.get("q")] * 2
    rows = db.query("SELECT * FROM classes WHERE %s ORDER BY id DESC" % " AND ".join(where), args)
    terms = [r["term"] for r in db.query("SELECT DISTINCT term FROM classes WHERE term<>''")]
    return {"list": [class_view(r, ctx.user["id"]) for r in rows], "terms": terms}


@route("/api/learn/classes/<id>", "GET")
def class_detail(ctx):
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM classes WHERE id=?", (cid,))
    if not row:
        raise ApiError("班级不存在", 404)
    v = class_view(row, ctx.user["id"])
    v["members"] = db.query(
        "SELECT u.id, u.username, u.display_name, u.dept, u.position, m.joined_at "
        "FROM class_members m JOIN users u ON u.id=m.user_id WHERE m.class_id=? ORDER BY u.id", (cid,))
    v["courses"] = db.query("SELECT id, title, category FROM courses WHERE class_id=? ORDER BY id DESC", (cid,))
    v["assignments"] = db.query(
        "SELECT id, title, deadline FROM assignments WHERE class_id=? ORDER BY id DESC", (cid,))
    v["exams"] = db.query(
        "SELECT id, title, status FROM exams WHERE class_id=? ORDER BY id DESC", (cid,))
    v["can_manage"] = bool(v["can_manage"] or security.has_perm(ctx.user, "course.manage"))
    return v


@route("/api/learn/classes", "POST")
def class_create(ctx):
    name = ctx.get("name") or ctx.get("title") or ""
    if not str(name).strip():
        raise ApiError("请填写班级名称")
    cid = db.insert("classes", {
        "name": name, "term": ctx.get("term", ""),
        "teacher_id": ctx.user["id"], "desc": ctx.get("desc", ""),
        "status": "active", "created_at": util.now_ts()})
    base.audit(ctx, "创建班级", name, str(cid))
    return {"id": cid}


@route("/api/learn/classes/<id>", "PUT")
def class_update(ctx):
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM classes WHERE id=?", (cid,))
    if not row:
        raise ApiError("班级不存在", 404)
    if row["teacher_id"] != ctx.user["id"]:
        base.require(ctx.user, "course.manage")
    data = {}
    for k in ("name", "term", "desc", "status"):
        if ctx.get(k) is not None:
            data[k] = ctx.get(k)
    if data:
        db.update("classes", data, "id=?", (cid,))
    return {"msg": "已保存"}


@route("/api/learn/classes/<id>", "DELETE")
def class_delete(ctx):
    """删除班级：默认归档而非物理删除，保留历史教学数据"""
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM classes WHERE id=?", (cid,))
    if not row:
        raise ApiError("班级不存在", 404)
    if row["teacher_id"] != ctx.user["id"]:
        base.require(ctx.user, "course.manage")
    db.update("classes", {"status": "archived"}, "id=?", (cid,))
    base.audit(ctx, "归档班级", row["name"], str(cid))
    return {"msg": "已归档（历史数据保留）"}


# ---------------- 班级成员 ----------------
@route("/api/learn/classes/<id>/members", "POST")
def class_add_members(ctx):
    """批量添加成员：接受 user_ids 数组或 usernames 数组"""
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM classes WHERE id=?", (cid,))
    if not row:
        raise ApiError("班级不存在", 404)
    if row["teacher_id"] != ctx.user["id"]:
        base.require(ctx.user, "course.manage")

    ids = []
    raw_ids = ctx.body.get("user_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [x for x in raw_ids.split(",") if x.strip()]
    for v in raw_ids:
        if str(v).strip().isdigit():
            ids.append(int(v))

    names = ctx.body.get("usernames") or []
    if isinstance(names, str):
        names = [x.strip() for x in names.replace("\n", ",").split(",") if x.strip()]
    missing = []
    for n in names:
        u = db.one("SELECT id FROM users WHERE username=? OR display_name=?", (n, n))
        if u:
            ids.append(u["id"])
        else:
            missing.append(n)

    added = 0
    for uid in set(ids):
        if not db.one("SELECT id FROM users WHERE id=?", (uid,)):
            continue
        cur = db.execute("INSERT OR IGNORE INTO class_members (class_id,user_id,joined_at) VALUES (?,?,?)",
                         (cid, uid, util.now_ts()))
        if cur:
            added += 1
    return {"added": added, "missing": missing,
            "msg": "已添加 %d 人%s" % (added, ("，%d 个用户名未找到" % len(missing)) if missing else "")}


@route("/api/learn/classes/<id>/members/<uid>", "DELETE")
def class_remove_member(ctx):
    cid, uid = int(ctx.args["id"]), int(ctx.args["uid"])
    row = db.one("SELECT * FROM classes WHERE id=?", (cid,))
    if not row:
        raise ApiError("班级不存在", 404)
    if row["teacher_id"] != ctx.user["id"]:
        base.require(ctx.user, "course.manage")
    db.delete("class_members", "class_id=? AND user_id=?", (cid, uid))
    return {"msg": "已移出班级"}


@route("/api/learn/classes/<id>/join", "POST")
def class_join(ctx):
    """学生自助加入班级"""
    cid = int(ctx.args["id"])
    if not db.one("SELECT id FROM classes WHERE id=? AND status='active'", (cid,)):
        raise ApiError("班级不存在或已归档", 404)
    db.execute("INSERT OR IGNORE INTO class_members (class_id,user_id,joined_at) VALUES (?,?,?)",
               (cid, ctx.user["id"], util.now_ts()))
    return {"msg": "已加入班级"}


# ---------------- 成绩册 ----------------
@route("/api/learn/classes/<id>/grades", "GET")
def class_grades(ctx):
    """班级成绩册：学生 × (作业 + 考试) 的二维成绩表。

    老师改完作业/阅完卷最想知道的是「这个班整体怎么样、谁掉队了」，
    而不是逐个作业翻提交列表。这里一次性把整个班的分摊成矩阵，
    前端渲染成表格，可导出 CSV。

    缺失成绩统一返回 -1（前端显示为「—」），与真实的 0 分区分开。
    """
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM classes WHERE id=?", (cid,))
    if not row:
        raise ApiError("班级不存在", 404)
    base.require(ctx.user, "assignment.manage")

    students = db.query(
        "SELECT u.id, u.username, u.display_name FROM class_members m "
        "JOIN users u ON u.id=m.user_id WHERE m.class_id=? ORDER BY u.display_name", (cid,))

    # 本班作业：直接挂班，或挂在属于本班的课程下
    asgs = db.query(
        "SELECT id, title FROM assignments WHERE class_id=? "
        "   OR course_id IN (SELECT id FROM courses WHERE class_id=?) "
        "ORDER BY id", (cid, cid))
    exms = db.query(
        "SELECT id, title, total_score, pass_score FROM exams WHERE class_id=? "
        "   OR course_id IN (SELECT id FROM courses WHERE class_id=?) "
        "ORDER BY id", (cid, cid))

    # 批量取成绩，避免每个学生每列都查库
    hw_map = {}
    if asgs:
        ids = [a["id"] for a in asgs]
        ph = ",".join("?" * len(ids))
        for s in db.query(
                "SELECT assignment_id, user_id, score, status FROM submissions "
                "WHERE assignment_id IN (%s)" % ph, ids):
            hw_map[(s["user_id"], s["assignment_id"])] = s

    ex_map = {}
    if exms:
        ids = [e["id"] for e in exms]
        ph = ",".join("?" * len(ids))
        # 多次考试取最高分
        for s in db.query(
                "SELECT exam_id, user_id, MAX(total_score) AS total_score, MAX(passed) AS passed "
                "FROM exam_records WHERE exam_id IN (%s) AND status<>'doing' "
                "GROUP BY exam_id, user_id" % ph, ids):
            ex_map[(s["user_id"], s["exam_id"])] = s

    rows = []
    for st in students:
        hw_cells, ex_cells = [], []
        hw_sum = hw_n = 0
        ex_sum = ex_n = 0
        for a in asgs:
            s = hw_map.get((st["id"], a["id"]))
            if not s or s.get("status") != "graded":
                hw_cells.append({"aid": a["id"], "score": -1,
                                 "state": (s or {}).get("status") or "none"})
            else:
                sc = float(s.get("score") or 0)
                hw_cells.append({"aid": a["id"], "score": sc, "state": "graded"})
                hw_sum += sc
                hw_n += 1
        for e in exms:
            s = ex_map.get((st["id"], e["id"]))
            if not s:
                ex_cells.append({"eid": e["id"], "score": -1, "passed": 0})
            else:
                sc = float(s.get("total_score") or 0)
                ex_cells.append({"eid": e["id"], "score": sc, "passed": s.get("passed") or 0})
                ex_sum += sc
                ex_n += 1
        rows.append({
            "user_id": st["id"], "username": st["username"],
            "name": st["display_name"],
            "hw": hw_cells, "exam": ex_cells,
            "hw_avg": round(hw_sum / hw_n, 1) if hw_n else -1,
            "exam_avg": round(ex_sum / ex_n, 1) if ex_n else -1,
            "graded_hw": hw_n, "taken_exam": ex_n,
        })

    return {
        "class": {"id": row["id"], "name": row["name"], "term": row["term"]},
        "assignments": [dict(a) for a in asgs],
        "exams": [dict(e) for e in exms],
        "rows": rows,
        "student_count": len(students),
    }
