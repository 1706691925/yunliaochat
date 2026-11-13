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
"""象棋联机对手：player2 作为 guest 加入指定房间，响应 host 走子、聊天与认输。
用法: python tools/mp_opponent.py <ROOM>
"""
import json, socket, base64, os, time, sys, urllib.request

BASE = 'http://127.0.0.1:8899'
ROOM = (sys.argv[1] if len(sys.argv) > 1 else '').strip().upper()
LOG = r'G:\EnterpriseLearnChat\EnterpriseLearnChat\mp_opponent.log'
log = open(LOG, 'w', encoding='utf-8')
def L(m):
    log.write(m + "\n"); log.flush()

req = urllib.request.Request(BASE + '/api/auth/login',
                             data=json.dumps({"username": "player2", "password": "Player@2026"}).encode(),
                             headers={'Content-Type': 'application/json'})
tok = json.loads(urllib.request.urlopen(req, timeout=5).read())['data']['token']

s = socket.create_connection(("127.0.0.1", 8899), timeout=30)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET /ws HTTP/1.1\r\nHost: 127.0.0.1:8899\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
buf = b""
while b"\r\n\r\n" not in buf:
    buf += s.recv(1024)
rest = buf.split(b"\r\n\r\n", 1)[1]

def read(n):
    global rest
    while len(rest) < n:
        d = s.recv(4096)
        if not d:
            raise IOError("closed")
        rest += d
    out, rest = rest[:n], rest[n:]
    return out

def recv(timeout=8):
    s.settimeout(timeout)
    try:
        b0 = read(1)[0]
        b1 = read(1)[0]
    except Exception:
        return None
    ln = b1 & 0x7F
    if ln == 126:
        ln = int.from_bytes(read(2), 'big')
    elif ln == 127:
        ln = int.from_bytes(read(8), 'big')
    payload = read(ln) if ln else b""
    try:
        return json.loads(payload.decode())
    except Exception:
        return None

def send(obj):
    payload = json.dumps(obj).encode()
    b = bytearray([0x81])
    n = len(payload)
    mask = os.urandom(4)
    if n < 126:
        b.append(0x80 | n)
    elif n < 65536:
        b.append(0x80 | 126)
        b += n.to_bytes(2, 'big')
    else:
        b.append(0x80 | 127)
        b += n.to_bytes(8, 'big')
    b += mask
    b += bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    s.sendall(bytes(b))

send({"t": "auth", "d": {"token": tok}})
deadline = time.time() + 10
while time.time() < deadline:
    m = recv(3)
    if m and m.get("t") == "auth" and m["d"].get("ok"):
        L("auth ok")
        break
else:
    L("auth FAIL")
    sys.exit(1)

send({"t": "game", "d": {"g": "chess", "op": "join", "room": ROOM}})
L("join sent " + ROOM)

moved = False
end = time.time() + 600
while time.time() < end:
    m = recv(20)
    if not m:
        L("recv none")
        continue
    L("recv " + json.dumps(m, ensure_ascii=False)[:300])
    d = m.get("d", {})
    if not isinstance(d, dict):
        continue
    ev = d.get("ev")
    if m.get("t") == "game" and ev == "room":
        L("JOINED role=" + str(d.get("role")))
    elif ev == "move" and not moved:
        moved = True
        L("HOST_MOVE %s,%s->%s,%s notation=%s" % (d.get("fr"), d.get("fc"), d.get("tr"), d.get("tc"), d.get("notation")))
        # 规范应子：黑方跳马 8进7（(0,1)->(2,2)）
        time.sleep(0.6)
        send({"t": "game", "d": {"g": "chess", "op": "move", "fr": 0, "fc": 1, "tr": 2, "tc": 2,
                                 "notation": "马8进7"}})
        L("guest move sent 马8进7")
    elif ev == "chat":
        L("CHAT " + str(d.get("name")) + ": " + str(d.get("text")))
        if d.get("text") == "测试联机聊天":
            time.sleep(0.5)
            send({"t": "game", "d": {"g": "chess", "op": "resign"}})
            L("resign sent")
            time.sleep(1.5)
            L("done")
            sys.exit(0)

L("idle exit")
