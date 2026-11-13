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
"""
极简 CDP 截图工具（仅标准库）：启动/连接 Edge headless，按脚本执行动作并截图。

用法：
  python tools/cdp_shot.py --url http://127.0.0.1:8099/ --out _shots --script shot_plan.json
或作为库：
  from cdp_shot import CDP
"""
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'


class WS(object):
    def __init__(self, url, timeout=20):
        assert url.startswith('ws://')
        rest = url[5:]
        hostport, path = rest.split('/', 1)
        path = '/' + path
        host, port = hostport.split(':')
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            'GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
            'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError('WS handshake closed')
            buf += chunk
        head = buf.split(b'\r\n\r\n', 1)[0].decode('latin1')
        if '101' not in head.split('\r\n')[0]:
            raise RuntimeError('WS handshake failed: ' + head.split('\r\n')[0])
        self.rest = buf.split(b'\r\n\r\n', 1)[1]
        self._id = 0

    def _recv_exact(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError('WS closed')
            self.rest += chunk
        out = self.rest[:n]
        self.rest = self.rest[n:]
        return out

    def send_text(self, text):
        data = text.encode('utf-8')
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack('>H', n)
        else:
            header.append(0x80 | 127)
            header += struct.pack('>Q', n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self):
        while True:
            b1, b2 = self._recv_exact(2)
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack('>H', self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', self._recv_exact(8))[0]
            masked = b2 & 0x80
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError('WS close frame')
            if opcode == 0x9:
                continue
            if opcode in (0x1, 0x2):
                return payload.decode('utf-8', 'replace')

    def close(self):
        try:
            self.send_text('')
            self.sock.close()
        except Exception:
            pass


class CDP(object):
    def __init__(self, port=9222, profile=None, width=1440, height=900, verbose=False):
        self.port = port
        self.verbose = verbose
        self.proc = None
        self.ws = None
        self.seq = 0
        self.events = []
        self.profile = profile or os.path.join(os.environ.get('TEMP', '.'), 'cdp-profile')
        args = [EDGE, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
                '--no-default-browser-check', '--disable-extensions', '--mute-audio',
                '--remote-debugging-port=%d' % port, '--remote-allow-origins=*',
                '--user-data-dir=%s' % self.profile,
                '--window-size=%d,%d' % (width, height), 'about:blank']
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = None
        for _ in range(60):
            time.sleep(0.4)
            try:
                data = json.loads(urllib.request.urlopen(
                    'http://127.0.0.1:%d/json/list' % port, timeout=2).read().decode('utf-8'))
            except Exception:
                continue
            pages = [t for t in data if t.get('type') == 'page']
            if pages:
                target = pages[0]
                break
        if not target:
            raise RuntimeError('无法连接 Edge 调试端口')
        self.ws = WS(target['webSocketDebuggerUrl'])
        self.send('Page.enable')
        self.send('Runtime.enable')
        self.send('Emulation.setDeviceMetricsOverride',
                  {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})

    def send(self, method, params=None, timeout=20):
        self.seq += 1
        mid = self.seq
        self.ws.send_text(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv_text())
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError('%s -> %s' % (method, msg['error']))
                return msg.get('result', {})
            if 'method' in msg:
                self.events.append(msg)
        raise RuntimeError('timeout: ' + method)

    def eval(self, expr, await_promise=False):
        r = self.send('Runtime.evaluate', {'expression': expr, 'returnByValue': True,
                                           'awaitPromise': await_promise})
        res = r.get('result', {})
        if r.get('exceptionDetails'):
            raise RuntimeError('JS error: %s' % json.dumps(r['exceptionDetails'], ensure_ascii=False))
        return res.get('value')

    def goto(self, url, wait=1.6):
        self.send('Page.navigate', {'url': url})
        time.sleep(wait)

    def shot(self, path, full=False):
        params = {'format': 'png'}
        if full:
            params['captureBeyondViewport'] = True
        r = self.send('Page.captureScreenshot', params)
        data = base64.b64decode(r['data'])
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, 'wb') as f:
            f.write(data)
        return len(data)

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:8099/')
    ap.add_argument('--out', default='_shots')
    ap.add_argument('--port', type=int, default=9222)
    ap.add_argument('--profile', default=None)
    ap.add_argument('--plan', default=None, help='JSON: [{"js": "...", "wait": 1, "shot": "name.png"}]')
    a = ap.parse_args()

    plan = json.load(open(a.plan, encoding='utf-8')) if a.plan else []
    c = CDP(port=a.port, profile=a.profile)
    try:
        c.goto(a.url, 2.0)
        for step in plan:
            if step.get('js'):
                c.eval(step['js'])
            time.sleep(step.get('wait', 0.8))
            if step.get('shot'):
                n = c.shot(os.path.join(a.out, step['shot']), step.get('full', False))
                print('  shot %-22s %7d bytes' % (step['shot'], n))
    finally:
        c.close()


if __name__ == '__main__':
    main()
