# -*- coding: utf-8 -*-
"""验证「学习中心 → 题库刷题」页签的完整交互。"""
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
PORT = 9488


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


ok_n = fail_n = 0


def check(desc, cond, detail=''):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print('  OK   ' + desc)
    else:
        fail_n += 1
        print('  FAIL ' + desc + ('  → ' + str(detail) if detail else ''))


profile = os.path.join(os.environ.get('TEMP', '.'), 'yl_lab_probe')
shutil.rmtree(profile, ignore_errors=True)
proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
                         '--no-default-browser-check', '--remote-debugging-port=%d' % PORT,
                         '--user-data-dir=' + profile, '--window-size=1500,1000', 'about:blank'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
      const r = await API.post('/api/auth/login', {username:'teacher', password:'Pass@123'});
      if (!r.ok) return 'login: ' + r.msg;
      API.token = r.data.token; localStorage.setItem('lc_token', API.token);
      const me = await API.get('/api/auth/me');
      API.user = me.data.user; API.site = me.data.site;
      await App.start();
      await new Promise(s => setTimeout(s, 1200));
      App.go('learn');
      await new Promise(s => setTimeout(s, 900));
      Learn.tab('lab');
      await new Promise(s => setTimeout(s, 2000));
      return 'ok';
    })()""", awaitp=True)
    check('进入学习中心并切到题库页签', st == 'ok', st)
    time.sleep(1.5)

    print()
    print('=== 题库页签渲染 ===')
    body = ws.js("(document.getElementById('learn-body')||{}).textContent||''")
    print('  文本:', (body or '').replace('\n', ' ')[:200])
    check('出现「知否刷题平台」', '知否' in (body or ''), body)
    check('显示平台地址', '127.0.0.1:8080' in (body or '') or 'http' in (body or ''), body)
    check('有「新窗口打开」', '新窗口打开' in (body or ''), body)
    check('有「刷新」', '刷新' in (body or ''), body)
    check('有「进入题库」按钮', ws.js(
        "Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('进入题库'))"))
    dot = ws.js("(document.querySelector('.lab-dot')||{}).className || ''")
    print('  状态灯:', dot)

    print()
    print('=== 点「进入题库」（跨端口应提示改用新窗口）===')
    r2 = ws.js("""(async () => {
      const b = Array.from(document.querySelectorAll('button'))
        .find(x => x.textContent.includes('进入题库'));
      if (!b) return {err:'no btn'};
      b.click();
      await new Promise(s => setTimeout(s, 2500));
      const wrap = document.getElementById('lab-frame-wrap');
      return { text: (wrap ? wrap.textContent : '').replace(/\\s+/g,' ').trim().slice(0, 160),
               hasIframe: !!document.querySelector('.lab-frame') };
    })()""", awaitp=True)
    print('  结果:', json.dumps(r2, ensure_ascii=False))
    # 云聊 8996 与知否 8080 不同源，应给出跨源提示而不是白屏
    txt = (r2 or {}).get('text', '') if isinstance(r2, dict) else ''
    check('跨源时给出明确提示', ('不同源' in txt) or ('新窗口' in txt) or (r2 or {}).get('hasIframe'),
          r2)
finally:
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass

print()
print('=' * 50)
print('  通过 %d 项，失败 %d 项' % (ok_n, fail_n))
print('=' * 50)
sys.exit(1 if fail_n else 0)
