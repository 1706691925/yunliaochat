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
"""文件：上传、下载、列表、删除（全部落在本地 data/files）"""
import os

from core import config, db, security, util
from core import app_ctx
from core.http import ApiError, route
from api import base

MAX_INLINE = 5 * 1024 * 1024


LEGACY_ALLOW = ("txt,log,md,csv,json,pdf,doc,docx,xls,xlsx,ppt,pptx,zip,rar,7z,png,jpg,jpeg,"
               "gif,bmp,mp4,mp3,wav,avi,mov,sql,py,java,js,html,css")


def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _ext_ok(name, cfg):
    """allow_ext 非空时按白名单放行；否则只按 deny_ext 黑名单拦截"""
    ext = _ext(name)
    if not ext:
        return True
    allow = [e.strip().lower() for e in str(cfg.get("allow_ext", "")).split(",") if e.strip()]
    # 兼容旧版本：若仍是出厂白名单，则视为"不限制"
    if allow and ",".join(allow) == LEGACY_ALLOW:
        allow = []
    if allow:
        return ext in allow
    deny = [e.strip().lower() for e in str(cfg.get("deny_ext", "")).split(",") if e.strip()]
    return ext not in deny


def _ext_reason(name, cfg):
    """返回被拦截的可读原因，便于前端提示"""
    ext = _ext(name)
    allow = [e.strip().lower() for e in str(cfg.get("allow_ext", "")).split(",") if e.strip()]
    if allow and ",".join(allow) == LEGACY_ALLOW:
        allow = []
    if allow and ext not in allow:
        return "该文件类型不在允许范围内（.%s）。管理员可在「系统设置 → 允许上传的扩展名」中放行" % ext
    deny = [e.strip().lower() for e in str(cfg.get("deny_ext", "")).split(",") if e.strip()]
    if ext in deny:
        return "出于安全考虑禁止上传 .%s 文件。管理员可在「系统设置 → 禁止上传的扩展名」中调整" % ext
    return ""


def save_upload(f, user_id, room_id=0, kind="chat", folder=""):
    cfg = config.CFG
    if not _ext_ok(f.filename, cfg):
        raise ApiError(_ext_reason(f.filename, cfg) or ("文件类型不允许上传：%s" % f.filename))
    limit = int(cfg.get("max_upload_mb", 50)) * 1024 * 1024
    if f.size > limit:
        raise ApiError("文件超过大小限制（%sMB）" % cfg.get("max_upload_mb", 50))
    d = os.path.join(cfg.files_dir, util.now_str("%Y%m"))
    os.makedirs(d, exist_ok=True)
    stored = util.uid("f_") + ("." + f.filename.rsplit(".", 1)[-1] if "." in f.filename else "")
    dst = os.path.join(d, stored)
    with open(f.tmp_path, "rb") as src, open(dst, "wb") as out:
        while True:
            chunk = src.read(65536)
            if not chunk:
                break
            out.write(chunk)
    f.remove()
    return db.insert("files", {"name": util.safe_name(f.filename), "stored": stored,
                               "size": f.size, "mime": "", "uploader_id": user_id,
                               "room_id": int(room_id or 0), "kind": kind,
                               "folder": folder or "",
                               "created_at": util.now_ts()})


def file_view(r):
    return {"id": r["id"], "name": r["name"], "size": r["size"],
            "size_str": util.human_size(r["size"]), "kind": r["kind"],
            "room_id": r["room_id"], "uploader_id": r["uploader_id"],
            "folder": r.get("folder", ""),
            "created_at": r["created_at"], "created_str": util.ts_to_str(r["created_at"]),
            "downloads": r["downloads"], "pinned": r.get("pinned", 0),
            "url": "/api/files/%d/download" % r["id"]}


def can_read(f, user):
    if not user:
        return False
    if f["uploader_id"] == user["id"]:
        return True
    if f["room_id"]:
        return app_ctx.APP.can_access_room(user, f["room_id"])
    return f["kind"] in ("center", "material", "avatar", "assignment", "course")


@route("/api/files/upload", "POST")
def upload(ctx):
    if not ctx.files:
        raise ApiError("请选择文件")
    room_id = ctx.int("room_id", 0)
    kind = ctx.get("kind", "chat")
    if room_id and not app_ctx.APP.can_access_room(ctx.user, room_id):
        raise ApiError("无权上传到该房间", 403)
    if room_id:
        room = db.one("SELECT * FROM rooms WHERE id=?", (room_id,))
        if room and not room["allow_file"] and not security.has_perm(ctx.user, "room.manage"):
            raise ApiError("该房间已禁用文件上传")
        if room and room["type"] == "announce" and not security.has_perm(ctx.user, "announce.manage"):
            raise ApiError("公告频道仅管理员可上传")
        mem = db.one("SELECT muted_until, silent FROM room_members WHERE room_id=? AND user_id=?",
                     (room_id, ctx.user["id"]))
        if mem and (mem["muted_until"] or 0) > util.now_ts():
            raise ApiError("您已被禁言，无法上传文件", 403)
        if mem and mem["silent"]:
            # 隐身巡查：静默加入的房间只读，禁止上传暴露身份
            raise ApiError("隐身巡查模式仅可查看，无法上传文件", 403)
    if kind in ("material", "course") and not security.has_perm(ctx.user, "material.manage"):
        kind = "chat"
    if kind not in ("chat", "center", "material", "course", "avatar", "assignment"):
        kind = "chat"
    if kind == "avatar":
        # 自定义头像：仅允许小体积图片
        for f in ctx.files:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                raise ApiError("头像仅支持 png / jpg / jpeg / gif / webp 图片")
            if f.size > 2 * 1024 * 1024:
                raise ApiError("头像图片不能超过 2MB")
    out = []
    folder = ctx.get("folder", "") if kind == "center" else ""
    for f in ctx.files[:10]:
        out.append(file_view(db.one("SELECT * FROM files WHERE id=?", (save_upload(f, ctx.user["id"], room_id, kind, folder),))))
    return {"list": out}


def _folders():
    """文件中心文件夹列表（meta 空文件夹 + 实际文件去重合并，含计数）"""
    folders, seen = [], set()
    for m in db.query("SELECT `key` FROM meta WHERE `key` LIKE 'file_folder:%' ORDER BY `key`"):
        nm = m["key"][len("file_folder:"):]
        if nm and nm not in seen:
            seen.add(nm)
            folders.append({"name": nm, "count": 0})
    for r in db.query("SELECT folder, COUNT(*) AS c FROM files WHERE kind='center' AND folder!='' GROUP BY folder"):
        nm = r["folder"]
        if nm in seen:
            for fo in folders:
                if fo["name"] == nm:
                    fo["count"] = r["c"]
                    break
        else:
            seen.add(nm)
            folders.append({"name": nm, "count": r["c"]})
    folders.sort(key=lambda x: (-x["count"], x["name"]))
    return folders


@route("/api/files/folders", "GET")
def folders_list(ctx):
    return {"list": _folders(),
            "uncategorized": db.scalar("SELECT COUNT(*) AS c FROM files WHERE kind='center' AND (folder IS NULL OR folder='')")}


@route("/api/files/folders", "POST")
def folder_create(ctx):
    name = (ctx.get("name") or "").strip()
    if not name:
        raise ApiError("文件夹名不能为空")
    if len(name) > 30:
        raise ApiError("文件夹名不能超过 30 个字符")
    db.execute("INSERT OR IGNORE INTO meta(`key`, `value`) VALUES(?, '1')", ("file_folder:" + name,))
    return {"msg": "已创建文件夹", "name": name}


@route("/api/files/folders/<name>", "DELETE")
def folder_delete(ctx):
    if not security.has_perm(ctx.user, "material.manage"):
        raise ApiError("只有管理员可以删除文件夹", 403)
    import urllib.parse
    name = urllib.parse.unquote(ctx.args["name"])
    db.execute("DELETE FROM meta WHERE `key`=?", ("file_folder:" + name,))
    db.execute("UPDATE files SET folder='' WHERE folder=?", (name,))
    return {"msg": "已删除文件夹"}


@route("/api/files/<id>/folder", "PUT")
def file_move_folder(ctx):
    fid = int(ctx.args["id"])
    f = db.one("SELECT * FROM files WHERE id=?", (fid,))
    if not f:
        raise ApiError("文件不存在", 404)
    if f["uploader_id"] != ctx.user["id"] and not security.has_perm(ctx.user, "material.manage"):
        raise ApiError("无权移动该文件", 403)
    folder = (ctx.get("folder") or "").strip()
    db.update("files", {"folder": folder}, "id=?", (fid,))
    if folder:
        db.execute("INSERT OR IGNORE INTO meta(`key`, `value`) VALUES(?, '1')", ("file_folder:" + folder,))
    return {"msg": "已移动"}


def _can_pin(user):
    return (security.has_perm(user, "message.moderate") or
            security.has_perm(user, "settings.manage"))


@route("/api/files/center", "GET")
def center_list(ctx):
    """文件中心：只展示从文件中心上传的文件（聊天附件/头像不在此列出）"""
    folder = ctx.get("folder", "")
    where, args = ["kind='center'"], []
    if folder and folder != "__none__":
        where.append("folder=?")
        args.append(folder)
    elif folder == "__none__":
        where.append("(folder IS NULL OR folder='')")
    rows = db.query("SELECT * FROM files WHERE %s ORDER BY pinned DESC, id DESC LIMIT 500" % " AND ".join(where), args)
    return {"total": len(rows), "list": [file_view(r) for r in rows],
            "can_pin": _can_pin(ctx.user),
            "folders": _folders(),
            "uncategorized": db.scalar("SELECT COUNT(*) AS c FROM files WHERE kind='center' AND (folder IS NULL OR folder='')")}


@route("/api/files/<id>/pin", "PUT")
def pin(ctx):
    if not _can_pin(ctx.user):
        raise ApiError("只有管理员可以置顶文件", 403)
    fid = int(ctx.args["id"])
    f = db.one("SELECT * FROM files WHERE id=?", (fid,))
    if not f or f["kind"] != "center":
        raise ApiError("文件不存在", 404)
    val = 1 if ctx.int("pinned", 1) else 0
    db.update("files", {"pinned": val}, "id=?", (fid,))
    base.audit(ctx, "文件置顶" if val else "取消文件置顶", f["name"])
    return {"msg": "已置顶" if val else "已取消置顶", "pinned": val}


@route("/api/files/list", "GET")
def file_list(ctx):
    where, args = ["1=1"], []
    kind = ctx.get("kind")
    if kind:
        where.append("kind=?")
        args.append(kind)
    rid = ctx.int("room_id", 0)
    if rid:
        where.append("room_id=?")
        args.append(rid)
    if not security.has_perm(ctx.user, "material.manage"):
        where.append("(uploader_id=? OR kind IN ('material','avatar','assignment'))")
        args.append(ctx.user["id"])
    p, size, off = base.page_args(ctx, 30)
    total = db.scalar("SELECT COUNT(*) AS c FROM files WHERE %s" % " AND ".join(where), args)
    rows = db.query("SELECT * FROM files WHERE %s ORDER BY id DESC LIMIT ? OFFSET ?"
                    % " AND ".join(where), args + [size, off])
    return {"total": total, "list": [file_view(r) for r in rows]}


@route("/api/files/<id>/download", "GET")
def download(ctx):
    fid = int(ctx.args["id"])
    f = db.one("SELECT * FROM files WHERE id=?", (fid,))
    if not f:
        raise ApiError("文件不存在", 404)
    if not can_read(f, ctx.user):
        raise ApiError("无权下载该文件", 403)
    d = os.path.join(config.CFG.files_dir, util.ts_to_str(f["created_at"], "%Y%m"))
    paths = [os.path.join(d, f["stored"])]
    if not os.path.exists(paths[0]):
        for root, _, names in os.walk(config.CFG.files_dir):
            if f["stored"] in names:
                paths.append(os.path.join(root, f["stored"]))
    path = next((p for p in paths if os.path.exists(p)), None)
    if not path:
        raise ApiError("文件已丢失", 404)
    db.execute("UPDATE files SET downloads=downloads+1 WHERE id=?", (fid,))
    with open(path, "rb") as fp:
        data = fp.read()
    import urllib.parse
    fn = urllib.parse.quote(f["name"].encode("utf-8"))
    ext = f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else ""
    mimes = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
             "bmp": "image/bmp", "svg": "image/svg+xml", "pdf": "application/pdf",
             "txt": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8",
             "csv": "text/csv; charset=utf-8", "json": "application/json; charset=utf-8",
             "mp4": "video/mp4", "mp3": "audio/mpeg", "wav": "audio/wav",
             "zip": "application/zip", "doc": "application/msword",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "xls": "application/vnd.ms-excel",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "ppt": "application/vnd.ms-powerpoint",
             "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
    ctype = mimes.get(ext, "application/octet-stream")
    inline = (ext in ("png", "jpg", "jpeg", "gif", "bmp", "svg", "pdf", "mp4", "txt", "md"))
    disp = "inline" if inline else "attachment"
    headers = {"Content-Disposition": '%s; filename="%s"; filename*=UTF-8\'\'%s' % (disp, fn, fn),
               "Accept-Ranges": "bytes"}
    # 大文件流式发送：不整体读入内存（修复大文件下载卡顿/内存占用）
    size = os.path.getsize(path)
    if size > 2 * 1024 * 1024:
        try:
            ctx.handler._send_stream(200, path, ctype, headers)
            return (200, b"", ctype, headers)   # 已直接写出，_dispatch 会再发空体
        except Exception:
            raise ApiError("文件发送失败", 500)
    with open(path, "rb") as fp:
        data = fp.read()
    if not inline or len(data) >= MAX_INLINE:
        disp2 = "attachment"
        headers["Content-Disposition"] = '%s; filename="%s"; filename*=UTF-8\'\'%s' % (disp2, fn, fn)
    return (200, data, ctype, headers)


@route("/api/files/<id>", "DELETE")
def delete_file(ctx):
    fid = int(ctx.args["id"])
    f = db.one("SELECT * FROM files WHERE id=?", (fid,))
    if not f:
        raise ApiError("文件不存在", 404)
    if f["uploader_id"] != ctx.user["id"]:
        base.require(ctx.user, "material.manage", "无权删除他人文件")
    d = os.path.join(config.CFG.files_dir, util.ts_to_str(f["created_at"], "%Y%m"))
    p = os.path.join(d, f["stored"])
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass
    db.delete("files", "id=?", (fid,))
    return {"msg": "已删除"}
