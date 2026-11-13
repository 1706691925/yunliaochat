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
"""未读数：预存计数（room_members.unread_count）的读写封装。

为什么需要它：
  旧实现每次打开侧边栏都对 messages 做 COUNT(*)，8 个房间 = 8 次 COUNT。
  在 10 万条消息规模下实测约 88ms，且随消息量线性恶化。
  改为预存计数后，读取是 O(1)（实测 0.02ms），发消息时增量 +1。

一致性约定：
  * 新消息写入后 → bump(room_id, exclude_user_id)
  * 用户标记已读 → clear(room_id, user_id)
  * 用户加入房间 → join(room_id, user_id)
  * 消息撤回 → recount(room_id) 只针对该房间，成本可接受（撤回是低频操作）
"""
from core import db


def bump(room_id, exclude_user_id=0):
    """有新消息时，给该房间除发送者外的所有成员未读数 +1（隐身巡查成员同样计数，
    否则巡查账号切回普通身份时未读数会缺失）"""
    db.execute("UPDATE room_members SET unread_count=IFNULL(unread_count,0)+1 "
               "WHERE room_id=? AND user_id<>?", (room_id, exclude_user_id))


def clear(room_id, user_id):
    """标记已读：该用户在该房间的未读数归零"""
    db.execute("UPDATE room_members SET unread_count=0 WHERE room_id=? AND user_id=?",
               (room_id, user_id))


def join(room_id, user_id):
    """新成员加入房间时，按当前历史消息计算一次初始未读数"""
    n = db.scalar("SELECT COUNT(*) AS c FROM messages WHERE room_id=? AND user_id<>? AND recalled_at=0",
                  (room_id, user_id)) or 0
    db.execute("UPDATE room_members SET unread_count=? WHERE room_id=? AND user_id=?",
               (n, room_id, user_id))


def recount(room_id):
    """按 last_read_id 重算某房间全部成员的未读数（消息撤回等低频场景使用）"""
    db.execute(
        "UPDATE room_members SET unread_count=("
        "  SELECT COUNT(*) FROM messages m WHERE m.room_id=room_members.room_id"
        "  AND m.id>IFNULL(room_members.last_read_id,0)"
        "  AND m.user_id<>room_members.user_id AND m.recalled_at=0) "
        "WHERE room_id=?", (room_id,))


def summary(user_id):
    """读取某用户全部房间的未读数（O(1)，不再 COUNT messages）"""
    rows = db.query("SELECT room_id, IFNULL(unread_count,0) AS c FROM room_members WHERE user_id=?",
                    (user_id,))
    out, total = {}, 0
    for r in rows:
        c = int(r["c"] or 0)
        out[str(r["room_id"])] = c
        total += c
    return {"rooms": out, "total": total}


def repair_all():
    """全量修复：启动时校正一次，防止历史数据或异常中断导致计数漂移"""
    db.execute(
        "UPDATE room_members SET unread_count=("
        "  SELECT COUNT(*) FROM messages m WHERE m.room_id=room_members.room_id"
        "  AND m.id>IFNULL(room_members.last_read_id,0)"
        "  AND m.user_id<>room_members.user_id AND m.recalled_at=0)")
