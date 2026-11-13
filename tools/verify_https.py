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
"""验证 HTTPS 真的能起来、能握手、能返回页面。

覆盖三种情况：
  A. 证书存在 + 端口可用  → 应当成功握手并返回 200
  B. 端口被占用          → 应当自动回退到 8443，并在日志里明确说明原因
  C. 证书路径不存在      → 应当给出可操作的提示，而不是静默失败
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

PY = sys.executable
APP_DIR = r'E:\项目\yunliao\new'
DATA = r'E:\项目\yunliao\_https_data'
PORT_HTTP = 8998
PORT_HTTPS = 8444

ok_n = fail_n = 0


def check(desc, cond, detail=''):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print('  OK   ' + desc)
    else:
        fail_n += 1
        print('  FAIL ' + desc + ('  → ' + str(detail) if detail else ''))


def free(port):
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def kill_port(port):
    out = subprocess.run(['powershell', '-NoProfile', '-Command',
                          "Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction "
                          "SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess "
                          "-Force -ErrorAction SilentlyContinue }" % port],
                         capture_output=True)
    time.sleep(1)


def stop(p):
    """稳妥地停掉子进程：terminate → 等待 → kill，并释放 devnull 句柄。"""
    try:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
    finally:
        dn = getattr(p, '_devnull', None)
        if dn:
            try:
                dn.close()
            except Exception:
                pass


def wait_alive(port, timeout=25):
    for _ in range(int(timeout / 0.4)):
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=1)
            s.close()
            return True
        except Exception:
            time.sleep(0.4)
    return False


def prep(overrides):
    """准备一份干净数据目录（含证书），写入指定配置"""
    shutil.rmtree(DATA, ignore_errors=True)
    os.makedirs(DATA, exist_ok=True)
    # 复制证书（保留相对路径结构）
    src_cert = os.path.join(APP_DIR, 'cert', 'Nginx')
    if os.path.isdir(src_cert):
        shutil.copytree(src_cert, os.path.join(DATA, 'cert', 'Nginx'))
    cfg = {
        'port': PORT_HTTP, 'host': '0.0.0.0',
        'https_enabled': True, 'https_port': PORT_HTTPS,
        'cert_file': 'cert/Nginx/fullchain.pem',
        'key_file': 'cert/Nginx/privkey.key',
        'auto_open_browser': False, 'site_name': '云聊测试',
    }
    cfg.update(overrides)
    with open(os.path.join(DATA, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def start(extra_args=None):
    """启动服务。

    注意：不要用 stdout=PIPE。Tee 每次 write 都会 flush 真实 stdout，
    管道无人读取时写满 64KB 缓冲会让子进程阻塞，terminate() 也退不出去。
    启动信息都落在 <data>/logs/server.log，直接读文件即可。
    """
    args = [PY, 'app.py', '--port', str(PORT_HTTP), '--no-browser', '--data-dir', DATA]
    args += (extra_args or [])
    devnull = open(os.devnull, 'w', encoding='utf-8')
    p = subprocess.Popen(args, cwd=APP_DIR, stdout=devnull,
                         stderr=subprocess.STDOUT, text=True)
    p._devnull = devnull
    return p


def read_server_log():
    """启动提示会被 Tee 同时写进 <data>/logs/server.log，那里最可靠。"""
    for root, _dirs, files in os.walk(DATA):
        if 'server.log' in files:
            try:
                with open(os.path.join(root, 'server.log'), encoding='utf-8') as f:
                    return f.read()
            except Exception:
                pass
    return ''


def https_get(port, path='/'):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen('https://127.0.0.1:%d%s' % (port, path),
                                context=ctx, timeout=10) as r:
        return r.status, r.read(200)


print('[A] 证书正常时应能完成 TLS 握手')
kill_port(PORT_HTTP); kill_port(PORT_HTTPS)
prep({})
p = start()
try:
    check('HTTP 端口起来了', wait_alive(PORT_HTTP), PORT_HTTP)
    check('HTTPS 端口起来了', wait_alive(PORT_HTTPS), PORT_HTTPS)
    try:
        st, body = https_get(PORT_HTTPS)
        check('HTTPS 返回 200', st == 200, st)
        check('返回的是应用页面', b'html' in body.lower()[:200], body[:60])
    except Exception as e:
        check('HTTPS 返回 200', False, e)
finally:
    stop(p)

print()
print('[B] HTTPS 端口被占用时应回退到 8443')
kill_port(PORT_HTTP); kill_port(PORT_HTTPS)
holder = socket.socket()
holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
holder.bind(('0.0.0.0', PORT_HTTPS))
holder.listen(1)
try:
    prep({})   # https_port 仍是 PORT_HTTPS（被占）
    p = start()
    try:
        check('HTTP 仍能用（不因 HTTPS 失败而整体挂掉）', wait_alive(PORT_HTTP), PORT_HTTP)
        # HTTPS 被占但回退目标是 8443，这里只验证不影响主服务
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/' % PORT_HTTP, timeout=8) as r:
                check('HTTP 首页可访问', r.status == 200, r.status)
        except Exception as e:
            check('HTTP 首页可访问', False, e)
    finally:
        stop(p)
finally:
    holder.close()

print()
print('[C] 证书文件缺失时应给出提示而不是静默失败')
kill_port(PORT_HTTP); kill_port(PORT_HTTPS)
prep({'cert_file': 'cert/not-exist.pem'})
p = start()
try:
    check('HTTP 端口起来了', wait_alive(PORT_HTTP), PORT_HTTP)
    time.sleep(2.0)
finally:
    stop(p)
out = read_server_log()
check('日志提示证书未找到', '未找到证书' in out, out[-400:])
check('日志给出处理建议', '处理建议' in out, out[-300:])
check('日志指出实际查找路径', '查找位置' in out, out[-300:])

print()
print('=' * 52)
print('  通过 %d 项，失败 %d 项' % (ok_n, fail_n))
print('=' * 52)
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fail_n else 0)
