# -*- coding: utf-8 -*-
"""验证前端真的把「已读」渲染出来（此前后端有数据、前端从不显示）。"""
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
PORT = 9466


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


profile = os.path.join(os.environ.get('TEMP', '.'), 'yl_read_probe')
shutil.rmtree(profile, ignore_errors=True)
proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
                         '--no-default-browser-check', '--remote-debugging-port=%d' % PORT,
                         '--user-data-dir=' + profile, '--window-size=1440,900', 'about:blank'],
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

    # 以 teacher 身份登录，打开「已读回执验证」房间
    setup = """(async () => {
      const r = await API.post('/api/auth/login', {username:'teacher', password:'Pass@123'});
      if (!r.ok) return 'login 失败: ' + r.msg;
      API.token = r.data.token; localStorage.setItem('lc_token', API.token);
      const me = await API.get('/api/auth/me');
      API.user = me.data.user; API.site = me.data.site;
      await App.start();
      await new Promise(s => setTimeout(s, 1200));
      const rooms = await API.get('/api/chat/rooms');
      const target = (rooms.data.list || []).find(x => x.name === '已读回执验证');
      if (!target) return '找不到房间';
      await Chat.openRoom(target.id);
      await new Promise(s => setTimeout(s, 1500));
      return 'ok room=' + target.id;
    })()"""
    print('准备:', ws.js(setup, awaitp=True))
    time.sleep(1.5)

    print()
    print('=== 已读标记渲染检查 ===')
    print('  消息条数    :', ws.js("document.querySelectorAll('#msg-list .msg').length"))
    print('  read-tag 数 :', ws.js("document.querySelectorAll('#msg-list .read-tag').length"))
    tags = ws.js("Array.from(document.querySelectorAll('#msg-list .read-tag')).map(x=>x.textContent.trim())")
    print('  标记文本    :', tags)
    print('  .all 高亮数 :', ws.js("document.querySelectorAll('#msg-list .read-tag.all').length"))

    ok = isinstance(tags, list) and len(tags) > 0
    print('  渲染成功    :', '✅ 是' if ok else '❌ 否')

    if ok:
        print()
        print('=== 点开已读详情 ===')
        detail = ws.js("""(async () => {
          const t = document.querySelector('#msg-list .read-tag');
          if (!t) return 'no tag';
          t.click();
          await new Promise(s => setTimeout(s, 900));
          const box = document.getElementById('modal-root');
          return { opened: !!document.querySelector('.mask'),
                   text: (box ? box.textContent : '').replace(/\\s+/g,' ').trim().slice(0, 160) };
        })()""", awaitp=True)
        print('  结果:', json.dumps(detail, ensure_ascii=False))
finally:
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass

print()
print('=' * 48)
print('  前端已读渲染：' + ('通过' if ok else '失败'))
print('=' * 48)
sys.exit(0 if ok else 1)
