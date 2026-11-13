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
"""学习中心：课程 / 课时 / 资料库 / 题库 / 考试 / 作业 / 打卡 / 学习统计"""
import json

from core import db, realtime, security, util
from core import app_ctx
from core.http import ApiError, route
from api import base
from api.files import file_view, save_upload

QTYPE_NAME = {"single": "单选题", "multi": "多选题", "judge": "判断题",
              "blank": "填空题", "short": "简答题"}


def _short_time(ts):
    """把时间戳格式化成人读起来舒服的形式：今年只显示 MM-DD HH:MM，跨年才带年份。
    待办卡片空间窄，带秒的完整时间会溢出换行。"""
    if not ts:
        return "不限"
    hero = util.ts_to_str(ts)
    if not hero:
        return "不限"
    try:
        import datetime
        dt = datetime.datetime.fromtimestamp(int(ts))
        if dt.year == datetime.date.today().year:
            return dt.strftime("%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return hero[:16]


def course_view(row, user_id=0):
    d = dict(row)
    d["created_str"] = util.ts_to_str(row["created_at"])
    teacher = db.one("SELECT display_name FROM users WHERE id=?", (row["teacher_id"],))
    d["teacher_name"] = (teacher or {}).get("display_name", "—")
    d["lesson_count"] = db.scalar("SELECT COUNT(*) AS c FROM lessons WHERE course_id=?", (row["id"],))
    d["material_count"] = db.scalar("SELECT COUNT(*) AS c FROM materials WHERE course_id=?", (row["id"],))
    d["enrolled_count"] = db.scalar("SELECT COUNT(*) AS c FROM enrollments WHERE course_id=?", (row["id"],))
    d["enrolled"] = False
    d["progress"] = 0
    if user_id:
        e = db.one("SELECT * FROM enrollments WHERE course_id=? AND user_id=?", (row["id"], user_id))
        if e:
            d["enrolled"] = True
            d["progress"] = e["progress"]
            d["completed_at"] = e["completed_at"]
    return d


# ---------------- 课程 ----------------
@route("/api/learn/courses", "GET")
def courses(ctx):
    where, args = ["1=1"], []
    if ctx.get("category"):
        where.append("category=?")
        args.append(ctx.get("category"))
    if ctx.get("q"):
        where.append("(title LIKE ? OR subtitle LIKE ? OR `desc` LIKE ?)")
        args += ["%%%s%%" % ctx.get("q")] * 3
    if ctx.get("status") and security.has_perm(ctx.user, "course.manage"):
        where.append("status=?")
        args.append(ctx.get("status"))
    else:
        where.append("status='published'")
    if ctx.int("mine", 0):
        where.append("EXISTS (SELECT 1 FROM enrollments e WHERE e.course_id=courses.id AND e.user_id=?)")
        args.append(ctx.user["id"])
    p, size, off = base.page_args(ctx, 12)
    total = db.scalar("SELECT COUNT(*) AS c FROM courses WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT * FROM courses WHERE %s ORDER BY sort DESC, id DESC LIMIT ? OFFSET ?"
                    % " AND ".join(where), args + [size, off])
    cats = [r["category"] for r in db.query("SELECT DISTINCT category FROM courses")]
    return {"total": total, "list": [course_view(r, ctx.user["id"]) for r in rows], "categories": cats}


@route("/api/learn/courses/<id>", "GET")
def course_detail(ctx):
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM courses WHERE id=?", (cid,))
    if not row:
        raise ApiError("课程不存在", 404)
    v = course_view(row, ctx.user["id"])
    v["lessons"] = db.query("SELECT * FROM lessons WHERE course_id=? ORDER BY sort, id", (cid,))
    v["materials"] = db.query("SELECT m.*, f.name AS file_name, f.size AS file_size FROM materials m "
                              "LEFT JOIN files f ON f.id=m.file_id WHERE m.course_id=? ORDER BY m.id DESC", (cid,))
    v["exams"] = db.query("SELECT id, title, duration, total_score, pass_score, status FROM exams "
                          "WHERE course_id=? AND status='published'", (cid,))
    v["assignments"] = db.query("SELECT id, title, deadline FROM assignments WHERE course_id=? ORDER BY id DESC", (cid,))
    en = db.one("SELECT * FROM enrollments WHERE course_id=? AND user_id=?", (cid, ctx.user["id"]))
    v["finished"] = util.json_loads((en or {}).get("finished_lessons"), [])
    v["can_manage"] = security.has_perm(ctx.user, "course.manage") or row["teacher_id"] == ctx.user["id"]
    return v


@route("/api/learn/courses", "POST")
def course_create(ctx):
    base.require(ctx.user, "course.manage")
    title = ctx.require("title")
    cid = db.insert("courses", {"title": title, "subtitle": ctx.get("subtitle", ""),
                                "category": ctx.get("category", "通用"), "teacher_id": ctx.user["id"],
                                "cover_color": ctx.get("cover_color", "#2f6fed"),
                                "desc": ctx.get("desc", ""), "status": ctx.get("status", "published"),
                                "required": ctx.int("required", 0),
                                "class_id": ctx.int("class_id", 0), "created_at": util.now_ts()})
    base.audit(ctx, "创建课程", title, str(cid))
    return {"id": cid}


@route("/api/learn/courses/<id>", "PUT")
def course_update(ctx):
    cid = int(ctx.args["id"])
    row = db.one("SELECT * FROM courses WHERE id=?", (cid,))
    if not row:
        raise ApiError("课程不存在", 404)
    if row["teacher_id"] != ctx.user["id"]:
        base.require(ctx.user, "course.manage")
    data = {k: ctx.get(k) for k in ("title", "subtitle", "category", "desc", "status",
                                    "cover_color", "required") if ctx.get(k) is not None}
    if ctx.int("teacher_id", 0) and security.has_perm(ctx.user, "course.manage"):
        data["teacher_id"] = ctx.int("teacher_id")
    if data:
        db.update("courses", data, "id=?", (cid,))
    return {"msg": "已保存"}


@route("/api/learn/courses/<id>", "DELETE")
def course_delete(ctx):
    base.require(ctx.user, "course.manage")
    cid = int(ctx.args["id"])
    db.delete("lessons", "course_id=?", (cid,))
    db.delete("enrollments", "course_id=?", (cid,))
    db.delete("courses", "id=?", (cid,))
    base.audit(ctx, "删除课程", str(cid))
    return {"msg": "已删除"}


@route("/api/learn/courses/<id>/enroll", "POST")
def enroll(ctx):
    cid = int(ctx.args["id"])
    if not db.one("SELECT 1 FROM courses WHERE id=?", (cid,)):
        raise ApiError("课程不存在", 404)
    db.execute("INSERT OR IGNORE INTO enrollments (course_id,user_id,joined_at,progress,finished_lessons)"
               " VALUES (?,?,?,0,'[]')", (cid, ctx.user["id"], util.now_ts()))
    db.insert("study_logs", {"user_id": ctx.user["id"], "course_id": cid, "action": "enroll",
                             "created_at": util.now_ts(), "detail": "报名课程"})
    return {"msg": "已报名"}


@route("/api/learn/courses/<id>/lessons", "POST")
def lesson_create(ctx):
    cid = int(ctx.args["id"])
    if not (security.has_perm(ctx.user, "course.manage") or
            (db.one("SELECT teacher_id FROM courses WHERE id=?", (cid,)) or {}).get("teacher_id") == ctx.user["id"]):
        raise ApiError("无权编辑该课程", 403)
    lid = db.insert("lessons", {"course_id": cid, "title": ctx.require("title"),
                                "content": ctx.get("content", ""), "sort": ctx.int("sort", 0),
                                "minutes": ctx.int("minutes", 15), "created_at": util.now_ts()})
    return {"id": lid}


@route("/api/learn/lessons/<id>", "PUT")
def lesson_update(ctx):
    lid = int(ctx.args["id"])
    row = db.one("SELECT l.*, c.teacher_id FROM lessons l JOIN courses c ON c.id=l.course_id WHERE l.id=?", (lid,))
    if not row:
        raise ApiError("课时不存在", 404)
    if row["teacher_id"] != ctx.user["id"]:
        base.require(ctx.user, "course.manage")
    data = {k: ctx.get(k) for k in ("title", "content", "sort", "minutes") if ctx.get(k) is not None}
    if data:
        db.update("lessons", data, "id=?", (lid,))
    return {"msg": "已保存"}


@route("/api/learn/lessons/<id>", "DELETE")
def lesson_delete(ctx):
    base.require(ctx.user, "course.manage")
    db.delete("lessons", "id=?", (int(ctx.args["id"]),))
    return {"msg": "已删除"}


@route("/api/learn/lessons/<id>/finish", "POST")
def lesson_finish(ctx):
    lid = int(ctx.args["id"])
    row = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    if not row:
        raise ApiError("课时不存在", 404)
    cid = row["course_id"]
    db.execute("INSERT OR IGNORE INTO enrollments (course_id,user_id,joined_at,progress,finished_lessons)"
               " VALUES (?,?,?,0,'[]')", (cid, ctx.user["id"], util.now_ts()))
    en = db.one("SELECT * FROM enrollments WHERE course_id=? AND user_id=?", (cid, ctx.user["id"]))
    fin = set(util.json_loads(en["finished_lessons"], []))
    fin.add(lid)
    total = db.scalar("SELECT COUNT(*) AS c FROM lessons WHERE course_id=?", (cid,)) or 1
    prog = int(min(100, round(len(fin) * 100.0 / total)))
    db.update("enrollments", {"finished_lessons": util.json_dumps(sorted(fin)), "progress": prog,
                              "completed_at": util.now_ts() if prog >= 100 else 0},
              "course_id=? AND user_id=?", (cid, ctx.user["id"]))
    db.insert("study_logs", {"user_id": ctx.user["id"], "course_id": cid, "action": "lesson",
                             "minutes": int(row["minutes"] or 15), "detail": row["title"],
                             "created_at": util.now_ts()})
    db.execute("UPDATE users SET learn_minutes=learn_minutes+?, points=points+2 WHERE id=?",
               (int(row["minutes"] or 15), ctx.user["id"]))
    return {"progress": prog}


@route("/api/learn/study-log", "POST")
def study_log(ctx):
    minutes = min(600, max(1, ctx.int("minutes", 10)))
    db.insert("study_logs", {"user_id": ctx.user["id"], "course_id": ctx.int("course_id", 0),
                             "action": ctx.get("action", "study"), "minutes": minutes,
                             "detail": ctx.get("detail", ""), "created_at": util.now_ts()})
    db.execute("UPDATE users SET learn_minutes=learn_minutes+? WHERE id=?", (minutes, ctx.user["id"]))
    return {"msg": "已记录"}


# ---------------- 资料库 ----------------
@route("/api/learn/materials", "GET")
def materials(ctx):
    where, args = ["1=1"], []
    if ctx.get("q"):
        where.append("(title LIKE ? OR content LIKE ?)")
        args += ["%%%s%%" % ctx.get("q")] * 2
    if ctx.get("category"):
        where.append("category=?")
        args.append(ctx.get("category"))
    if ctx.int("course_id", 0):
        where.append("course_id=?")
        args.append(ctx.int("course_id"))
    p, size, off = base.page_args(ctx, 20)
    total = db.scalar("SELECT COUNT(*) AS c FROM materials WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT m.*, f.name AS file_name, f.size AS file_size, u.display_name AS uploader "
                    "FROM materials m LEFT JOIN files f ON f.id=m.file_id "
                    "LEFT JOIN users u ON u.id=m.uploader_id WHERE %s ORDER BY m.id DESC LIMIT ? OFFSET ?"
                    % " AND ".join(where), args + [size, off])
    for r in rows:
        r["created_str"] = util.ts_to_str(r["created_at"])
        r["size_str"] = util.human_size(r["file_size"] or 0)
        r["url"] = "/api/files/%d/download" % r["file_id"] if r["file_id"] else ""
    return {"total": total, "list": rows}


@route("/api/learn/materials", "POST")
def material_create(ctx):
    base.require(ctx.user, "material.manage")
    fid = 0
    if ctx.files:
        fid = save_upload(ctx.files[0], ctx.user["id"], 0, "material")
    if not fid and not ctx.get("content"):
        raise ApiError("请上传文件或填写内容")
    mid = db.insert("materials", {"title": ctx.require("title"), "course_id": ctx.int("course_id", 0),
                                  "category": ctx.get("category", "通用"), "mtype": "file" if fid else "text",
                                  "content": ctx.get("content", ""), "file_id": fid,
                                  "uploader_id": ctx.user["id"], "created_at": util.now_ts()})
    db.notify_all("资料库更新", "新资料：%s" % ctx.get("title"), "material", "material", mid)
    realtime.broadcast({"t": "notify", "d": {"type": "material", "title": ctx.get("title")}})
    base.audit(ctx, "上传资料", ctx.get("title"))
    return {"id": mid}


@route("/api/learn/materials/<id>", "DELETE")
def material_delete(ctx):
    base.require(ctx.user, "material.manage")
    db.delete("materials", "id=?", (int(ctx.args["id"]),))
    return {"msg": "已删除"}


# ---------------- 题库 ----------------
@route("/api/learn/questions", "GET")
def questions(ctx):
    base.require(ctx.user, "question.manage")
    where, args = ["1=1"], []
    if ctx.int("course_id", 0):
        where.append("course_id=?")
        args.append(ctx.int("course_id"))
    if ctx.get("qtype"):
        where.append("qtype=?")
        args.append(ctx.get("qtype"))
    if ctx.get("q"):
        where.append("stem LIKE ?")
        args.append("%%%s%%" % ctx.get("q"))
    p, size, off = base.page_args(ctx, 20)
    total = db.scalar("SELECT COUNT(*) AS c FROM questions WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT * FROM questions WHERE %s ORDER BY id DESC LIMIT ? OFFSET ?"
                    % " AND ".join(where), args + [size, off])
    for r in rows:
        r["qtype_name"] = QTYPE_NAME.get(r["qtype"], r["qtype"])
        r["options_list"] = util.json_loads(r["options"], [])
        r["created_str"] = util.ts_to_str(r["created_at"])
    return {"total": total, "list": rows}


@route("/api/learn/questions", "POST")
def question_create(ctx):
    base.require(ctx.user, "question.manage")
    qid = db.insert("questions", {
        "course_id": ctx.int("course_id", 0), "qtype": ctx.get("qtype", "single"),
        "stem": ctx.require("stem"), "options": util.json_dumps(ctx.body.get("options", [])),
        "answer": ctx.get("answer", ""), "analysis": ctx.get("analysis", ""),
        "score": ctx.int("score", 5), "difficulty": ctx.get("difficulty", "medium"),
        "tags": ctx.get("tags", ""), "created_by": ctx.user["id"], "created_at": util.now_ts()})
    return {"id": qid}


@route("/api/learn/questions/<id>", "PUT")
def question_update(ctx):
    base.require(ctx.user, "question.manage")
    data = {k: ctx.get(k) for k in ("course_id", "qtype", "stem", "answer", "analysis",
                                    "score", "difficulty", "tags") if ctx.get(k) is not None}
    if ctx.body.get("options") is not None:
        data["options"] = util.json_dumps(ctx.body["options"])
    if data:
        db.update("questions", data, "id=?", (int(ctx.args["id"]),))
    return {"msg": "已保存"}


@route("/api/learn/questions/<id>", "DELETE")
def question_delete(ctx):
    base.require(ctx.user, "question.manage")
    db.delete("questions", "id=?", (int(ctx.args["id"]),))
    return {"msg": "已删除"}


@route("/api/learn/questions/import", "POST")
def question_import(ctx):
    """批量导入：JSON 数组 或 文本（每行：题型|题干|选项A;选项B|答案|解析）"""
    base.require(ctx.user, "question.manage")
    raw = ctx.require("data")
    n = 0
    try:
        arr = json.loads(raw)
        for q in arr:
            db.insert("questions", {"course_id": ctx.int("course_id", 0) or q.get("course_id", 0),
                                    "qtype": q.get("qtype", "single"), "stem": q["stem"],
                                    "options": util.json_dumps(q.get("options", [])),
                                    "answer": q.get("answer", ""), "analysis": q.get("analysis", ""),
                                    "score": q.get("score", 5), "difficulty": q.get("difficulty", "medium"),
                                    "created_by": ctx.user["id"], "created_at": util.now_ts()})
            n += 1
    except Exception:
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            qtype, stem, opts = parts[0], parts[1], parts[2]
            answer = parts[3] if len(parts) > 3 else ""
            analysis = parts[4] if len(parts) > 4 else ""
            db.insert("questions", {"course_id": ctx.int("course_id", 0),
                                    "qtype": {"单选": "single", "多选": "multi", "判断": "judge",
                                              "填空": "blank", "简答": "short"}.get(qtype, qtype),
                                    "stem": stem, "options": util.json_dumps([o for o in opts.split(";") if o]),
                                    "answer": answer, "analysis": analysis, "score": 5,
                                    "created_by": ctx.user["id"], "created_at": util.now_ts()})
            n += 1
    return {"count": n, "msg": "导入 %d 题" % n}


# ---------------- 考试 ----------------
@route("/api/learn/exams", "GET")
def exams(ctx):
    rows = db.query("SELECT e.*, c.title AS course_title FROM exams e LEFT JOIN courses c ON c.id=e.course_id "
                    "WHERE e.status='published' ORDER BY e.id DESC")
    out = []
    for r in rows:
        d = dict(r)
        d["start_str"] = util.ts_to_str(r["start_at"]) if r["start_at"] else ""
        d["end_str"] = util.ts_to_str(r["end_at"]) if r["end_at"] else ""
        rec = db.one("SELECT * FROM exam_records WHERE exam_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                     (r["id"], ctx.user["id"]))
        d["attempted"] = db.scalar("SELECT COUNT(*) AS c FROM exam_records WHERE exam_id=? AND user_id=? AND status<>'doing'",
                                   (r["id"], ctx.user["id"]))
        d["best_score"] = db.scalar("SELECT MAX(total_score) AS c FROM exam_records WHERE exam_id=? AND user_id=? AND status<>'doing'",
                                    (r["id"], ctx.user["id"]))
        d["question_count"] = db.scalar("SELECT COUNT(*) AS c FROM exam_questions WHERE exam_id=?", (r["id"],))
        d["can_take"] = (not r["start_at"] or r["start_at"] <= util.now_ts()) and \
                        (not r["end_at"] or util.now_ts() <= r["end_at"]) and \
                        d["attempted"] < int(r["max_attempts"] or 1)
        out.append(d)
    return {"list": out}


@route("/api/learn/exams", "POST")
def exam_create(ctx):
    base.require(ctx.user, "exam.manage")
    eid = db.insert("exams", {"course_id": ctx.int("course_id", 0), "title": ctx.require("title"),
                              "desc": ctx.get("desc", ""), "duration": ctx.int("duration", 60),
                              "total_score": ctx.int("total_score", 100), "pass_score": ctx.int("pass_score", 60),
                              "start_at": ctx.int("start_at", 0), "end_at": ctx.int("end_at", 0),
                              "status": ctx.get("status", "published"), "shuffle": ctx.int("shuffle", 1),
                              "show_answer": ctx.int("show_answer", 1), "max_attempts": ctx.int("max_attempts", 1),
                              "class_id": ctx.int("class_id", 0),
                              "created_by": ctx.user["id"], "created_at": util.now_ts()})
    for qid in str(ctx.get("question_ids", "")).split(","):
        if qid.strip().isdigit():
            db.execute("INSERT OR IGNORE INTO exam_questions (exam_id,question_id,score,sort) VALUES (?,?,?,?)",
                       (eid, int(qid), 5, int(qid)))
    return {"id": eid}


@route("/api/learn/exams/<id>/questions", "POST")
def exam_set_questions(ctx):
    base.require(ctx.user, "exam.manage")
    eid = int(ctx.args["id"])
    db.delete("exam_questions", "exam_id=?", (eid,))
    total = 0
    for i, item in enumerate(ctx.body.get("questions", []) if isinstance(ctx.body.get("questions"), list) else []):
        qid = int(item.get("id", 0))
        score = int(item.get("score", 5))
        db.execute("INSERT OR IGNORE INTO exam_questions (exam_id,question_id,score,sort) VALUES (?,?,?,?)",
                   (eid, qid, score, i))
        total += score
    db.update("exams", {"total_score": total}, "id=?", (eid,))
    return {"msg": "已保存", "total_score": total}


@route("/api/learn/exams/<id>/start", "POST")
def exam_start(ctx):
    if not app_ctx.APP.cfg.get("enable_exam", True):
        raise ApiError("考试功能已关闭")
    eid = int(ctx.args["id"])
    ex = db.one("SELECT * FROM exams WHERE id=?", (eid,))
    if not ex or ex["status"] != "published":
        raise ApiError("考试不存在或未发布", 404)
    attempted = db.scalar("SELECT COUNT(*) AS c FROM exam_records WHERE exam_id=? AND user_id=? AND status<>'doing'",
                          (eid, ctx.user["id"]))
    if attempted >= int(ex["max_attempts"] or 1):
        raise ApiError("已达到最大考试次数（%s 次）" % ex["max_attempts"])
    rid = db.insert("exam_records", {"exam_id": eid, "user_id": ctx.user["id"], "attempt": attempted + 1,
                                     "started_at": util.now_ts(), "status": "doing", "answers": "{}", "ip": ctx.ip})
    qs = db.query("SELECT q.*, eq.score FROM exam_questions eq JOIN questions q ON q.id=eq.question_id "
                  "WHERE eq.exam_id=? ORDER BY eq.sort", (eid,))
    import random
    if ex["shuffle"]:
        random.shuffle(qs)
    return {"record_id": rid, "exam": {"id": ex["id"], "title": ex["title"], "duration": ex["duration"],
                                       "desc": ex["desc"], "pass_score": ex["pass_score"]},
            "questions": [{"id": q["id"], "qtype": q["qtype"], "stem": q["stem"],
                           "options": util.json_loads(q["options"], []), "score": q["score"],
                           "qtype_name": QTYPE_NAME.get(q["qtype"], q["qtype"])} for q in qs],
            "deadline": util.now_ts() + int(ex["duration"]) * 60}


def _judge(q, ans):
    """返回 (得分, 是否正确)"""
    std = (q["answer"] or "").strip().upper().replace("，", ",").replace(" ", "")
    if q["qtype"] in ("single", "judge"):
        a = str(ans or "").strip().upper()
        a = a[0] if a else ""
        return (1.0 if a and a == std[:1] else 0.0)
    if q["qtype"] == "multi":
        got = set(x.strip().upper()[:1] for x in str(ans or "").replace("，", ",").split(",") if x.strip())
        want = set(x.strip()[:1] for x in std.split(",") if x.strip())
        if not want:
            return 0.0
        if got == want:
            return 1.0
        wrong = len(got - want)
        right = len(got & want)
        return max(0.0, (right - wrong * 0.5) / len(want))
    if q["qtype"] == "blank":
        stds = [s.strip() for s in (q["answer"] or "").split("|") if s.strip()]
        a = str(ans or "").strip()
        return 1.0 if any(a == s for s in stds) else 0.0
    return 0.0


@route("/api/learn/exams/<id>/submit", "POST")
def exam_submit(ctx):
    eid = int(ctx.args["id"])
    rid = ctx.int("record_id", 0)
    ex = db.one("SELECT * FROM exams WHERE id=?", (eid,))
    if not ex:
        raise ApiError("考试不存在", 404)
    rec = db.one("SELECT * FROM exam_records WHERE id=? AND user_id=?", (rid, ctx.user["id"]))
    if not rec:
        raise ApiError("考试记录不存在", 404)
    if rec["status"] != "doing":
        raise ApiError("该考试已提交")
    answers = ctx.body.get("answers") or {}
    if isinstance(answers, str):
        answers = util.json_loads(answers, {})
    qs = db.query("SELECT q.*, eq.score FROM exam_questions eq JOIN questions q ON q.id=eq.question_id "
                  "WHERE eq.exam_id=?", (eid,))
    obj, sub_pending, detail = 0.0, False, {}
    for q in qs:
        ans = answers.get(str(q["id"]), answers.get(q["id"], ""))
        detail[str(q["id"])] = ans
        if q["qtype"] in ("single", "multi", "judge", "blank"):
            obj += float(q["score"]) * _judge(q, ans)
        else:
            sub_pending = True
    status = "submitted" if sub_pending else "graded"
    total = round(obj, 1)
    passed = 1 if total >= int(ex["pass_score"] or 60) else 0
    db.update("exam_records", {"answers": util.json_dumps(detail), "obj_score": obj,
                               "sub_score": 0, "total_score": total, "status": status,
                               "submitted_at": util.now_ts(), "passed": passed}, "id=?", (rid,))
    db.insert("study_logs", {"user_id": ctx.user["id"], "course_id": ex["course_id"], "action": "exam",
                             "minutes": int(ex["duration"] or 60), "detail": ex["title"], "created_at": util.now_ts()})
    db.execute("UPDATE users SET points=points+? WHERE id=?", (5 if passed else 1, ctx.user["id"]))
    if passed:
        db.notify(ctx.user["id"], "exam", "考试通过：%s" % ex["title"], "得分 %s" % total, "exam", eid)
    return {"score": total, "passed": bool(passed), "status": status,
            "show_answer": bool(ex["show_answer"]),
            "msg": "已提交，等待阅卷" if sub_pending else "已提交"}


@route("/api/learn/exams/<id>/result/<rid>", "GET")
def exam_result(ctx):
    eid, rid = int(ctx.args["id"]), int(ctx.args["rid"])
    ex = db.one("SELECT * FROM exams WHERE id=?", (eid,))
    rec = db.one("SELECT * FROM exam_records WHERE id=?", (rid,))
    if not rec:
        raise ApiError("记录不存在", 404)
    if rec["user_id"] != ctx.user["id"]:
        base.require(ctx.user, "exam.grade")
    show = bool(ex and ex["show_answer"]) or security.has_perm(ctx.user, "exam.grade")
    answers = util.json_loads(rec["answers"], {})
    qs = db.query("SELECT q.*, eq.score FROM exam_questions eq JOIN questions q ON q.id=eq.question_id "
                  "WHERE eq.exam_id=? ORDER BY eq.sort", (eid,))
    qlist = []
    for q in qs:
        ans = answers.get(str(q["id"]), "")
        rate = _judge(q, ans)
        qlist.append({"id": q["id"], "stem": q["stem"], "qtype": q["qtype"],
                      "options": util.json_loads(q["options"], []), "score": q["score"],
                      "my_answer": ans, "correct": rate >= 1.0, "rate": rate,
                      "got": round(float(q["score"]) * rate, 1),
                      "answer": q["answer"] if show else "",
                      "analysis": q["analysis"] if show else ""})
    return {"record": dict(rec), "exam": dict(ex or {}), "questions": qlist, "show_answer": show}


@route("/api/learn/my/records", "GET")
def my_records(ctx):
    rows = db.query("SELECT r.*, e.title AS exam_title, e.pass_score, c.title AS course_title "
                    "FROM exam_records r JOIN exams e ON e.id=r.exam_id "
                    "LEFT JOIN courses c ON c.id=e.course_id WHERE r.user_id=? "
                    "ORDER BY r.id DESC LIMIT 100", (ctx.user["id"],))
    for r in rows:
        r["submitted_str"] = util.ts_to_str(r["submitted_at"])
        r["duration_used"] = max(0, (r["submitted_at"] or 0) - r["started_at"]) // 60
    return {"list": rows}


@route("/api/learn/my/todo", "GET")
def my_todo(ctx):
    """今日待办：登录后第一眼要看的东西。

    角色分流很重要：
      * 学生看到「待交作业 / 可参加考试」
      * 教师看到「待批阅作业 / 待阅卷」——老师从不提交作业，
        如果沿用学生的口径，老师会看到"全部作业都待交"，纯属噪音。
    """
    uid = ctx.user["id"]
    now = util.now_ts()
    can_grade_hw = security.has_perm(ctx.user, "assignment.manage")
    can_grade_exam = security.has_perm(ctx.user, "exam.grade")

    # ① 进行中的课程（已报名但未完成）
    courses = db.query(
        "SELECT e.course_id, e.progress, c.title FROM enrollments e "
        "JOIN courses c ON c.id=e.course_id "
        "WHERE e.user_id=? AND IFNULL(e.completed_at,0)=0 AND c.status='published' "
        "ORDER BY e.progress DESC LIMIT 5", (uid,))

    # ② 作业：学生看「待交」，教师看「待批阅」
    if can_grade_hw:
        hw = db.query(
            "SELECT a.id, a.title, a.deadline, c.title AS course_title, "
            "  (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.status='submitted') AS to_grade "
            "FROM assignments a LEFT JOIN courses c ON c.id=a.course_id "
            "WHERE (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.status='submitted')>0 "
            "ORDER BY a.deadline ASC LIMIT 5")
    else:
        hw = db.query(
            "SELECT a.id, a.title, a.deadline, c.title AS course_title, "
            "  0 AS to_grade FROM assignments a "
            "LEFT JOIN courses c ON c.id=a.course_id "
            "WHERE a.id NOT IN (SELECT assignment_id FROM submissions s WHERE s.user_id=?) "
            "  AND (IFNULL(a.deadline,0)=0 OR a.deadline>=?) "
            "ORDER BY CASE WHEN IFNULL(a.deadline,0)=0 THEN 1 ELSE 0 END, a.deadline ASC LIMIT 5",
            (uid, now))
    for h in hw:
        h["deadline_str"] = _short_time(h["deadline"])
        h["overdue"] = bool(h["deadline"] and h["deadline"] < now)
        h["left_days"] = int((h["deadline"] - now) // 86400) if h["deadline"] else -1

    # 学生视角额外统计逾期未交（单列，避免与"待交"混在一起）
    overdue = 0
    if not can_grade_hw:
        overdue = db.scalar(
            "SELECT COUNT(*) AS c FROM assignments a WHERE a.deadline>0 AND a.deadline<? "
            "AND a.id NOT IN (SELECT assignment_id FROM submissions s WHERE s.user_id=?)",
            (now, uid)) or 0

    # ③ 考试：学生看「可参加」，教师看「待阅卷」
    exams = []
    if can_grade_exam:
        for r in db.query(
                "SELECT e.id, e.title, COUNT(x.id) AS n FROM exams e "
                "JOIN exam_records x ON x.exam_id=e.id "
                "WHERE x.status IN ('submitted','doing') AND IFNULL(x.sub_score,0)=0 "
                "GROUP BY e.id ORDER BY e.id DESC LIMIT 5"):
            exams.append({"id": r["id"], "title": r["title"], "to_grade": r["n"],
                          "duration": 0, "end_at": 0, "end_str": ""})
        grading_exams = sum(e["to_grade"] for e in exams)
    else:
        grading_exams = 0
        for r in db.query("SELECT * FROM exams WHERE status='published' ORDER BY id DESC"):
            attempted = db.scalar(
                "SELECT COUNT(*) AS c FROM exam_records WHERE exam_id=? AND user_id=? AND status<>'doing'",
                (r["id"], uid)) or 0
            if attempted >= int(r["max_attempts"] or 1):
                continue
            if r["start_at"] and r["start_at"] > now:
                continue
            if r["end_at"] and r["end_at"] < now:
                continue
            exams.append({"id": r["id"], "title": r["title"], "duration": r["duration"],
                          "end_at": r["end_at"] or 0, "end_str": _short_time(r["end_at"]),
                          "attempted": attempted})

    # ④ 打卡与连续天数
    days = db.scalar("SELECT COUNT(*) AS c FROM checkins WHERE user_id=?", (uid,)) or 0
    today = util.today_str()
    checked = bool(db.one("SELECT 1 FROM checkins WHERE user_id=? AND day=?", (uid, today)))
    streak = 0
    try:
        import datetime
        d = datetime.date.today()
        if not checked:
            d -= datetime.timedelta(days=1)
        while db.one("SELECT 1 FROM checkins WHERE user_id=? AND day=?", (uid, d.isoformat())):
            streak += 1
            d -= datetime.timedelta(days=1)
    except Exception:
        streak = 0

    u = db.one("SELECT learn_minutes, points FROM users WHERE id=?", (uid,))
    return {
        "role_mode": "teacher" if (can_grade_hw or can_grade_exam) else "student",
        "courses": courses,
        "course_count": len(courses),
        "homework": hw,
        "pending_homework": 0 if can_grade_hw else len(hw),
        "to_grade_homework": sum(h["to_grade"] for h in hw) if can_grade_hw else 0,
        "overdue_homework": overdue,
        "exams": exams[:5],
        "available_exams": 0 if can_grade_hw else len(exams),
        "to_grade_exams": grading_exams,
        "checkin_days": days,
        "checked_today": checked,
        "streak": streak,
        "learn_minutes": (u or {}).get("learn_minutes", 0),
        "points": (u or {}).get("points", 0),
    }


@route("/api/learn/my/overview", "GET")
def my_overview(ctx):
    uid = ctx.user["id"]
    courses = db.scalar("SELECT COUNT(*) AS c FROM enrollments WHERE user_id=?", (uid,))
    done = db.scalar("SELECT COUNT(*) AS c FROM enrollments WHERE user_id=? AND completed_at>0", (uid,))
    exams = db.scalar("SELECT COUNT(*) AS c FROM exam_records WHERE user_id=? AND status<>'doing'", (uid,))
    passed = db.scalar("SELECT COUNT(*) AS c FROM exam_records WHERE user_id=? AND passed=1", (uid,))
    avg = db.scalar("SELECT AVG(total_score) AS c FROM exam_records WHERE user_id=? AND status<>'doing'", (uid,))
    homework = db.scalar("SELECT COUNT(*) AS c FROM submissions WHERE user_id=?", (uid,))
    pending = db.scalar("SELECT COUNT(*) AS c FROM assignments a WHERE a.id NOT IN "
                        "(SELECT assignment_id FROM submissions WHERE user_id=?) AND a.deadline>?",
                        (uid, util.now_ts()))
    days = db.scalar("SELECT COUNT(*) AS c FROM checkins WHERE user_id=?", (uid,))
    u = db.one("SELECT learn_minutes, points FROM users WHERE id=?", (uid,))
    logs = db.query("SELECT action, minutes, detail, created_at FROM study_logs WHERE user_id=? "
                    "ORDER BY id DESC LIMIT 8", (uid,))
    return {"courses": courses, "finished_courses": done, "exams": exams, "passed": passed,
            "avg_score": round(avg or 0, 1), "homework": homework, "pending_homework": pending,
            "checkin_days": days, "learn_minutes": u["learn_minutes"] if u else 0,
            "points": u["points"] if u else 0,
            "logs": [dict(l, created_str=util.ts_to_str(l["created_at"])) for l in logs]}


@route("/api/learn/exams/<id>/records", "GET")
def exam_records(ctx):
    base.require(ctx.user, "exam.grade")
    rows = db.query("SELECT r.*, u.display_name, u.username, u.dept FROM exam_records r "
                    "JOIN users u ON u.id=r.user_id WHERE r.exam_id=? ORDER BY r.id DESC",
                    (int(ctx.args["id"]),))
    for r in rows:
        r["submitted_str"] = util.ts_to_str(r["submitted_at"])
    return {"list": rows}


@route("/api/learn/records/<id>/grade", "POST")
def grade_record(ctx):
    base.require(ctx.user, "exam.grade")
    rid = int(ctx.args["id"])
    rec = db.one("SELECT * FROM exam_records WHERE id=?", (rid,))
    if not rec:
        raise ApiError("记录不存在", 404)
    sub = float(ctx.get("sub_score", 0) or 0)
    ex = db.one("SELECT * FROM exams WHERE id=?", (rec["exam_id"],))
    total = round(float(rec["obj_score"] or 0) + sub, 1)
    passed = 1 if total >= int((ex or {}).get("pass_score", 60)) else 0
    db.update("exam_records", {"sub_score": sub, "total_score": total, "status": "graded",
                               "passed": passed, "submitted_at": rec["submitted_at"] or util.now_ts()},
              "id=?", (rid,))
    db.notify(rec["user_id"], "exam", "考试已阅卷：%s" % (ex or {}).get("title", ""),
              "得分 %s" % total, "exam", rec["exam_id"])
    base.audit(ctx, "阅卷", str(rid), "得分 %s" % total)
    return {"msg": "已阅卷", "total_score": total}


# ---------------- 作业 ----------------
@route("/api/learn/assignments", "GET")
def assignments(ctx):
    where, args = ["1=1"], []
    if ctx.int("course_id", 0):
        where.append("course_id=?")
        args.append(ctx.int("course_id"))
    rows = db.query("SELECT a.*, c.title AS course_title FROM assignments a "
                    "LEFT JOIN courses c ON c.id=a.course_id WHERE %s ORDER BY a.id DESC" % " AND ".join(where), args)
    out = []
    for r in rows:
        d = dict(r)
        d["deadline_str"] = util.ts_to_str(r["deadline"])
        d["expired"] = (r["deadline"] or 0) < util.now_ts()
        sub = db.one("SELECT * FROM submissions WHERE assignment_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                     (r["id"], ctx.user["id"]))
        d["submitted"] = bool(sub)
        d["my_score"] = (sub or {}).get("score", 0)
        d["feedback"] = (sub or {}).get("feedback", "")
        d["graded"] = bool((sub or {}).get("graded_by"))
        d["sub_status"] = (sub or {}).get("status", "")      # submitted / graded / returned
        d["returned"] = (sub or {}).get("status") == "returned"
        d["can_resubmit"] = bool(sub) and (sub or {}).get("status") != "graded"
        d["late"] = bool((sub or {}).get("late"))
        d["submit_count"] = db.scalar("SELECT COUNT(*) AS c FROM submissions WHERE assignment_id=?", (r["id"],))
        out.append(d)
    return {"list": out}


@route("/api/learn/assignments", "POST")
def assignment_create(ctx):
    base.require(ctx.user, "assignment.manage")
    ts = ctx.int("deadline", 0)
    dstr = str(ctx.get("deadline_str", "") or "").strip()
    if dstr:
        ts = util.str_to_ts(dstr, "%Y-%m-%d %H:%M")
        if not ts:
            raise ApiError("截止时间格式应为 YYYY-MM-DD HH:MM")
    cid = ctx.int("class_id", 0)
    if cid and not db.one("SELECT id FROM classes WHERE id=?", (cid,)):
        raise ApiError("班级不存在", 404)
    aid = db.insert("assignments", {"course_id": ctx.int("course_id", 0), "title": ctx.require("title"),
                                    "content": ctx.get("content", ""), "deadline": ts,
                                    "created_by": ctx.user["id"], "created_at": util.now_ts(),
                                    "allow_late": ctx.int("allow_late", 1),
                                    "class_id": cid})
    # 指定班级时只通知该班学生，避免打扰无关的人
    title = ctx.get("title")
    if cid:
        for m in db.query("SELECT user_id FROM class_members WHERE class_id=?", (cid,)):
            db.notify(m["user_id"], "homework", "新作业：%s" % title,
                      "截止 %s" % _short_time(ts), "assignment", aid)
    else:
        db.notify_all("新作业", title, "assignment", "assignment", aid)
    return {"id": aid}


@route("/api/learn/assignments/<id>/submit", "POST")
def submit_assignment(ctx):
    aid = int(ctx.args["id"])
    a = db.one("SELECT * FROM assignments WHERE id=?", (aid,))
    if not a:
        raise ApiError("作业不存在", 404)
    if (a["deadline"] or 0) < util.now_ts() and not a["allow_late"]:
        raise ApiError("已截止，无法提交")
    fid = 0
    if ctx.files:
        fid = save_upload(ctx.files[0], ctx.user["id"], 0, "assignment")
    if not fid and not ctx.get("content"):
        raise ApiError("请填写内容或上传附件")
    now = util.now_ts()
    late = 1 if (a["deadline"] or 0) < now else 0
    # 覆盖式提交：同一学生对同一作业只保留一条记录，避免老师看到一堆重复提交
    old = db.one("SELECT * FROM submissions WHERE assignment_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                 (aid, ctx.user["id"]))
    if old:
        # 已批阅且未被退回的作业不允许再改，防止篡改成绩
        if old.get("status") == "graded":
            raise ApiError("该作业已批阅，如需修改请联系老师退回重做", 403)
        data = {"content": ctx.get("content", "") or old.get("content", ""),
                "submitted_at": now, "late": late, "status": "submitted"}
        if fid:
            data["file_id"] = fid
        db.update("submissions", data, "id=?", (old["id"],))
        return {"id": old["id"], "msg": "已重新提交", "resubmitted": True}
    sid = db.insert("submissions", {"assignment_id": aid, "user_id": ctx.user["id"],
                                    "content": ctx.get("content", ""), "file_id": fid,
                                    "submitted_at": now, "late": late, "status": "submitted"})
    return {"id": sid, "msg": "已提交"}


@route("/api/learn/assignments/<id>/submissions", "GET")
def submissions(ctx):
    base.require(ctx.user, "assignment.manage")
    rows = db.query("SELECT s.*, u.display_name, u.username, u.dept, f.name AS file_name "
                    "FROM submissions s JOIN users u ON u.id=s.user_id "
                    "LEFT JOIN files f ON f.id=s.file_id WHERE s.assignment_id=? ORDER BY s.id DESC",
                    (int(ctx.args["id"]),))
    for r in rows:
        r["submitted_str"] = util.ts_to_str(r["submitted_at"])
    return {"list": rows}


@route("/api/learn/submissions/<id>/grade", "POST")
def grade_submission(ctx):
    base.require(ctx.user, "assignment.manage")
    sid = int(ctx.args["id"])
    db.update("submissions", {"score": float(ctx.get("score", 0) or 0), "feedback": ctx.get("feedback", ""),
                              "status": "graded",
                              "graded_by": ctx.user["id"], "graded_at": util.now_ts()}, "id=?", (sid,))
    s = db.one("SELECT * FROM submissions WHERE id=?", (sid,))
    db.notify(s["user_id"], "homework", "作业已批阅", "得分 %s" % ctx.get("score", 0), "submission", sid)
    return {"msg": "已批阅"}


@route("/api/learn/submissions/<id>/return", "POST")
def return_submission(ctx):
    """退回重做：老师可让学生重新提交（批阅后发现不合格时使用）"""
    base.require(ctx.user, "assignment.manage")
    sid = int(ctx.args["id"])
    s = db.one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not s:
        raise ApiError("提交记录不存在", 404)
    note = ctx.get("feedback", "") or "请按老师要求修改后重新提交"
    db.update("submissions", {"status": "returned", "feedback": note,
                              "graded_by": ctx.user["id"], "graded_at": util.now_ts()},
              "id=?", (sid,))
    db.notify(s["user_id"], "homework", "作业被退回", note[:100], "submission", sid)
    return {"msg": "已退回，学生可重新提交"}


# ---------------- 打卡 ----------------
@route("/api/learn/checkin", "GET")
def checkin_list(ctx):
    rows = db.query("SELECT * FROM checkins WHERE user_id=? ORDER BY day DESC LIMIT 60", (ctx.user["id"],))
    today = util.today_str()
    return {"list": rows, "today": bool(db.one("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                                               (ctx.user["id"], today))),
            "total": db.scalar("SELECT COUNT(*) AS c FROM checkins WHERE user_id=?", (ctx.user["id"],))}


@route("/api/learn/checkin", "POST")
def checkin(ctx):
    if not app_ctx.APP.cfg.get("enable_checkin", True):
        raise ApiError("打卡功能已关闭")
    day = util.today_str()
    if db.one("SELECT 1 FROM checkins WHERE user_id=? AND day=?", (ctx.user["id"], day)):
        return {"msg": "今日已打卡", "repeat": True}
    db.insert("checkins", {"user_id": ctx.user["id"], "day": day, "note": ctx.get("note", ""),
                           "created_at": util.now_ts()})
    db.execute("UPDATE users SET points=points+1 WHERE id=?", (ctx.user["id"],))
    return {"msg": "打卡成功", "repeat": False}
