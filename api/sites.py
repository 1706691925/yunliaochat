# -*- coding: utf-8 -*-
"""常用网站（教学资源导航）：全员可读，管理员可增删改与批量导入"""
import re

from core import db, util
from core.http import ApiError, route
from api.base import audit

RE_URL = re.compile(r"^(https?://)?([\w\-.]+)(:\d+)?(/.*)?$", re.I)


def _row(r):
    return {"id": r["id"], "name": r["name"], "url": r["url"],
            "category": r["category"] or "常用", "note": r["note"] or "",
            "sort": r["sort"] or 0, "enabled": bool(r["enabled"])}


def _norm_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^(https?://)", url, re.I):
        url = "http://" + url
    return url[:500]


@route("/api/sites", "GET")
def list_sites(ctx):
    """全员可见：按分类、排序返回启用的常用网站"""
    rows = db.query("SELECT * FROM quick_sites WHERE enabled=1 ORDER BY sort, id")
    cats = []
    for r in rows:
        c = r["category"] or "常用"
        if c not in cats:
            cats.append(c)
    return {"list": [_row(r) for r in rows], "categories": cats}


@route("/api/admin/sites", "GET", perm="settings.manage")
def admin_sites(ctx):
    rows = db.query("SELECT * FROM quick_sites ORDER BY sort, id")
    cats = []
    for r in rows:
        c = r["category"] or "常用"
        if c not in cats:
            cats.append(c)
    return {"list": [_row(r) for r in rows], "categories": cats}


def _payload(ctx):
    name = (ctx.get("name") or "").strip()
    url = _norm_url(ctx.get("url"))
    if not name:
        raise ApiError("名称不能为空")
    if not url or not RE_URL.match(url):
        raise ApiError("地址格式不正确")
    return {"name": name[:60], "url": url,
            "category": ((ctx.get("category") or "").strip() or "常用")[:40],
            "note": (ctx.get("note") or "").strip()[:160],
            "sort": ctx.int("sort", 0), "enabled": 1 if ctx.int("enabled", 1) else 0}


@route("/api/admin/sites", "POST", perm="settings.manage")
def site_create(ctx):
    d = _payload(ctx)
    d["created_at"] = util.now_ts()
    sid = db.insert("quick_sites", d)
    audit(ctx, "新增常用网站", d["name"])
    return {"id": sid, "msg": "已添加「%s」" % d["name"]}


@route("/api/admin/sites/<id>", "PUT", perm="settings.manage")
def site_update(ctx):
    sid = int(ctx.args["id"])
    row = db.one("SELECT * FROM quick_sites WHERE id=?", (sid,))
    if not row:
        raise ApiError("网站不存在", 404)
    d = _payload(ctx)
    db.update("quick_sites", d, "id=?", (sid,))
    audit(ctx, "修改常用网站", d["name"])
    return {"msg": "已保存"}


@route("/api/admin/sites/<id>", "DELETE", perm="settings.manage")
def site_delete(ctx):
    sid = int(ctx.args["id"])
    row = db.one("SELECT * FROM quick_sites WHERE id=?", (sid,))
    if not row:
        raise ApiError("网站不存在", 404)
    db.delete("quick_sites", "id=?", (sid,))
    audit(ctx, "删除常用网站", row["name"])
    return {"msg": "已删除「%s」" % row["name"]}


@route("/api/admin/sites/import", "POST", perm="settings.manage")
def site_import(ctx):
    """批量导入：每行 `分类,名称,地址` / `名称,地址` / `地址`"""
    text = ctx.get("text") or ""
    mode = ctx.get("mode") or "append"      # append=追加 / replace=覆盖
    if mode == "replace":
        db.execute("DELETE FROM quick_sites")
    existing = {r["url"] for r in db.query("SELECT url FROM quick_sites")}
    ts = util.now_ts()
    added = skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,，\t|]", line) if p.strip()]
        if len(parts) >= 3:
            cat, name, url = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            cat, name, url = "常用", parts[0], parts[1]
        else:
            cat, name, url = "常用", parts[0], parts[0]
        url = _norm_url(url)
        if not url or url in existing:
            skipped += 1
            continue
        db.insert("quick_sites", {"name": name[:60], "url": url, "category": cat[:40],
                                  "note": "", "sort": 0, "enabled": 1, "created_at": ts})
        existing.add(url)
        added += 1
    audit(ctx, "导入常用网站", "新增 %d，跳过 %d" % (added, skipped))
    return {"msg": "导入完成：新增 %d 条，跳过 %d 条" % (added, skipped), "added": added, "skipped": skipped}
