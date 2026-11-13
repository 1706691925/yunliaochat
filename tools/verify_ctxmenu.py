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
"""验证右键菜单在 prefers-reduced-motion 下依然可见。

关键点：旧样式里 .ctxmenu 只有 animation:ctxIn（from opacity:0），
基线没有 opacity，一旦动画被系统"减少动态效果"关掉，菜单永久停在
opacity:0 —— DOM 里有、也能点，但人眼看不见，表现为"右键没反应"。
"""
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
PORT = 9455


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


def run_probe(profile, reduced_motion, label):
    if os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)
    args = [EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
            '--no-default-browser-check', '--remote-debugging-port=%d' % PORT,
            '--user-data-dir=' + profile, '--window-size=1440,900', 'about:blank']
    if reduced_motion:
        args.insert(1, '--force-prefers-reduced-motion')
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % PORT,
                                            timeout=1) as r:
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

        setup = """(async () => {
          const r = await API.post('/api/auth/login', {username:'teacher', password:'Pass@123'});
          if (!r.ok) return 'login 失败: ' + r.msg;
          API.token = r.data.token; localStorage.setItem('lc_token', API.token);
          const me = await API.get('/api/auth/me');
          API.user = me.data.user; API.site = me.data.site;
          await App.start();
          await new Promise(s => setTimeout(s, 1200));
          return 'ok';
        })()"""
        st = ws.js(setup, awaitp=True)
        time.sleep(1.5)

        print()
        print('===== %s =====' % label)
        print('  登录+启动 :', st)
        print('  reduced   :', ws.js(
            "matchMedia('(prefers-reduced-motion: reduce)').matches"))
        print('  房间数    :', ws.js("document.querySelectorAll('#room-list .room-item').length"))

        res = ws.js("""(() => {
          const el = document.querySelector('#room-list .room-item');
          if (!el) return {err:'no room item'};
          el.dispatchEvent(new MouseEvent('contextmenu',
            {bubbles:true, cancelable:true, clientX:300, clientY:300}));
          const m = document.querySelector('.ctxmenu');
          if (!m) return {err:'menu 未创建'};
          // 必须等动画跑完再读 computed style，否则读到的是 ctxIn 的起始帧（opacity 0），
          // 会被误判成"不可见"。这里取动画结束之后的稳定态。
          return new Promise(resolve => {
            const done = () => {
              const s = getComputedStyle(m);
              const b = m.querySelector('.ctxitem');
              const rc = b ? b.getBoundingClientRect() : null;
              const hit = rc ? document.elementFromPoint(rc.x + rc.width/2, rc.y + rc.height/2) : null;
              resolve({
                items: Array.from(m.querySelectorAll('.ctxitem')).map(x => x.textContent.trim()),
                opacity: s.opacity, display: s.display, visibility: s.visibility,
                hitInside: !!(hit && m.contains(hit))
              });
            };
            const anims = m.getAnimations ? m.getAnimations() : [];
            if (!anims.length) return setTimeout(done, 300);
            Promise.all(anims.map(a => a.finished.catch(() => {}))).then(() => setTimeout(done, 60));
          });
        })()""", awaitp=True)
        print('  右键菜单  :', json.dumps(res, ensure_ascii=False))
        vis = (isinstance(res, dict) and res.get('opacity') == '1'
               and res.get('visibility') == 'visible' and res.get('hitInside')
               and len(res.get('items') or []) > 0)
        print('  可见可用  :', '✅ 是' if vis else '❌ 否')
        return vis
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        time.sleep(0.8)


tmp = os.environ.get('TEMP', '.')
a = run_probe(os.path.join(tmp, 'yl_ctx_a'), True, '模拟系统「减少动态效果」')
b = run_probe(os.path.join(tmp, 'yl_ctx_b'), False, '普通动画环境')
print()
print('=' * 50)
print('  结果：减少动态效果 %s ／ 普通环境 %s' % ('通过' if a else '失败', '通过' if b else '失败'))
print('=' * 50)
sys.exit(0 if (a and b) else 1)
