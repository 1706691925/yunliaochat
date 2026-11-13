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
"""
纯标准库 WebSocket 实现（RFC 6455）
仅依赖 socket/base64/hashlib/struct，供内网自研聊天室使用。
"""
import base64
import hashlib
import os
import select
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


class WSError(Exception):
    pass


class WSConn(object):
    """一条 WebSocket 连接"""

    def __init__(self, sock, addr, server, preset=b""):
        self.sock = sock
        self.addr = addr
        self.server = server
        self.alive = True
        self.user = None          # 绑定登录用户 dict
        self.rooms = set()        # 订阅的房间
        self.last_seen = 0
        self._slock = threading.Lock()
        self._preset = preset     # HTTP 层预读的残留字节（升级瞬间客户端已发来的帧）

    # ---------- 帧编解码 ----------
    @staticmethod
    def _encode(opcode, payload, mask=False):
        b = bytearray()
        b.append(0x80 | opcode)
        n = len(payload)
        mask_bit = 0x80 if mask else 0
        if n < 126:
            b.append(mask_bit | n)
        elif n < 65536:
            b.append(mask_bit | 126)
            b += struct.pack(">H", n)
        else:
            b.append(mask_bit | 127)
            b += struct.pack(">Q", n)
        if mask:
            m = os.urandom(4)
            b += m
            payload = bytes(bytearray(c ^ m[i % 4] for i, c in enumerate(bytearray(payload))))
        b += payload
        return bytes(b)

    def _read_exact(self, n):
        buf = b""
        if self._preset:
            take = min(n, len(self._preset))
            buf, self._preset = self._preset[:take], self._preset[take:]
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise WSError("closed")
            buf += chunk
        return buf

    def _read_frame(self):
        """读取单个帧，返回 (fin, opcode, payload_bytes)"""
        b0 = self._read_exact(1)[0]
        b1 = self._read_exact(1)[0]
        fin = (b0 >> 7) & 1
        opcode = b0 & 0x0F
        masked = (b1 >> 7) & 1
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._read_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._read_exact(8))[0]
        if ln > 8 * 1024 * 1024:
            raise WSError("frame too large")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(ln) if ln else b""
        if masked:
            payload = bytes(bytearray(c ^ mask[i % 4] for i, c in enumerate(bytearray(payload))))
        return fin, opcode, payload

    def recv_frame(self, timeout=None):
        """返回 (opcode, payload_bytes)；超时或断开返回 (None, None)。支持分片帧重组。"""
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            fin, opcode, payload = self._read_frame()
            while not fin:
                f2, o2, p2 = self._read_frame()
                payload += p2
                fin = f2
            return opcode, payload
        except (socket.timeout, OSError, WSError):
            return None, None

    def _recv_raw(self):
        _, opcode, payload = self._read_frame()
        return opcode, payload

    # ---------- 发送 ----------
    def _send_raw(self, opcode, payload):
        if not self.alive:
            return False
        try:
            with self._slock:
                self.sock.sendall(self._encode(opcode, payload if isinstance(payload, bytes)
                                               else payload.encode("utf-8")))
            return True
        except Exception:
            self.alive = False
            return False

    def send(self, text):
        return self._send_raw(OP_TEXT, text)

    def send_bin(self, data):
        """发送二进制帧（远程协助画面等），data 必须是 bytes"""
        return self._send_raw(OP_BIN, data)

    def ping(self):
        return self._send_raw(OP_PING, b"")

    def pong(self, data=b""):
        return self._send_raw(OP_PONG, data)

    def close(self, code=1000, reason=""):
        if not self.alive:
            return
        try:
            payload = struct.pack(">H", code) + reason.encode()
            self._send_raw(OP_CLOSE, payload)
        except Exception:
            pass
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass

    # ---------- 握手 ----------
    @classmethod
    def handshake(cls, sock, request_lines):
        """request_lines: 已读取的请求头行列表（不含首行）"""
        headers = {}
        for line in request_lines:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        key = headers.get("sec-websocket-key")
        if not key:
            return None
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(key)
        )
        sock.sendall(resp.encode())
        return headers


def wait_readable(sock, timeout):
    try:
        r, _, _ = select.select([sock], [], [], timeout)
        return bool(r)
    except Exception:
        return False
