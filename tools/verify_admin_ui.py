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
"""确认新加的设置项真的渲染在后台页面上（不是只改了代码）。"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = os.environ.get('PROBE_URL', 'http://127.0.0.1:8996/')
PORT = 9477


class WS(object):
    def __init__(self, url):
        from urllib.parse import urlparse
        u = urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=30)
        req = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n'
               'Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n'
               'Sec-WebSocket-Version: 13\r\n\r\n') % (u.path or '/', u.hostname, u.port)
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            buf += self.s.recv(4096)
        self.uid = 0

    def send(self, obj):
        data = json.dumps(obj).encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += n.to_bytes(2, 'big')
        else:
            hdr.append(0x80 | 127); hdr += n.to_bytes(8, 'big')
        mask = os.urandom(4)
        hdr += mask
        self.s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        if not self.s.recv(1):
            raise IOError('closed')
        b2 = self.s.recv(1)[0]
        ln = b2 & 0x7F
        if ln == 126:
            ln = int.from_bytes(self.s.recv(2), 'big')
        elif ln == 127:
            ln = int.from_bytes(self.s.recv(8), 'big')
        buf = b''
        while len(buf) < ln:
            buf += self.s.recv(ln - len(buf))
        return json.loads(buf.decode())

    def call(self, method, params=None):
        self.uid += 1
        self.send({'id': self.uid, 'method': method, 'params': params or {}})
        while True:
            m = self.recv()
            if m.get('id') == self.uid:
                return m

    def js(self, expr, awaitp=False):
        r = self.call('Runtime.evaluate', {'expression': expr, 'returnByValue': True,
                                           'awaitPromise': awaitp, 'userGesture': True})
        res = r.get('result', {})
        if 'exceptionDetails' in res:
            return {'__error': res['exceptionDetails'].get('text')}
        return res.get('result', {}).get('value')


profile = os.path.join(os.environ.get('TEMP', '.'), 'yl_admin_probe')
shutil.rmtree(profile, ignore_errors=True)
proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
                         '--no-default-browser-check', '--remote-debugging-port=%d' % PORT,
                         '--user-data-dir=' + profile, '--window-size=1500,1000', 'about:blank'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ok = False
try:
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % PORT, timeout=1) as r:
                json.loads(r.read().decode())
            break
        except Exception:
            time.sleep(0.3)
    time.sleep(0.5)
    with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % PORT, timeout=3) as r:
        tgt = [t for t in json.loads(r.read().decode()) if t.get('type') == 'page']
    ws = WS(tgt[0]['webSocketDebuggerUrl'])
    ws.call('Page.enable')
    ws.call('Runtime.enable')
    ws.call('Page.navigate', {'url': URL})
    time.sleep(2.0)

    st = ws.js("""(async () => {
      const r = await API.post('/api/auth/login', {username:'admin', password:'Pass@123'});
      if (!r.ok) return 'login: ' + r.msg;
      API.token = r.data.token; localStorage.setItem('lc_token', API.token);
      const me = await API.get('/api/auth/me');
      API.user = me.data.user; API.site = me.data.site;
      await App.start();
      await new Promise(s => setTimeout(s, 1200));
      App.go('admin');
      await new Promise(s => setTimeout(s, 1500));
      return 'ok';
    })()""", awaitp=True)
    print('准备:', st)
    time.sleep(2.0)

    # 打开「系统设置」页签
    print('设置页:', ws.js("""(async () => {
      const btns = Array.from(document.querySelectorAll('button,a,.tab,.nav-item'));
      const t = btns.find(b => /系统设置|站点设置/.test(b.textContent));
      if (!t) return '找不到系统设置入口: ' + btns.map(b=>b.textContent.trim()).filter(Boolean).slice(0,20).join('|');
      t.click();
      await new Promise(s => setTimeout(s, 2000));
      return 'clicked';
    })()""", awaitp=True))
    time.sleep(2.5)

    print()
    print('=== 学习中心设置项是否存在 ===')
    print('  区块标题   :', ws.js(
        "Array.from(document.querySelectorAll('.settings-sec,.sec-title,h3,h4,b'))"
        ".map(x=>x.textContent.trim()).filter(x=>/题库|学习中心/.test(x)).slice(0,5)"))
    for k in ('study_center_enabled', 'study_center_url', 'study_center_secret',
              'study_center_user_prefix'):
        print('  %-26s' % ('st-' + k), ws.js("!!document.getElementById('st-%s')" % k))
    print('  测试连接按钮:', ws.js(
        "Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('测试连接'))"))

    print()
    print('=== 学习中心页签是否有「题库刷题」 ===')
    print('  tab:', ws.js(
        "Array.from(document.querySelectorAll('[data-ltab]')).map(x=>x.textContent.trim())"))

    ok = ws.js("!!document.getElementById('st-study_center_url')")
finally:
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass

print()
print('=' * 46)
print('  后台设置项渲染：' + ('通过' if ok else '失败'))
print('=' * 46)
sys.exit(0 if ok else 1)
