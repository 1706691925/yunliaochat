#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 web_src/ 下的模板、样式与 js/ 模块编译成单文件 web/index.html。

设计要点：
  * 页面结构 = 设计稿的严格复刻
  * 脚本沿用原有业务模块（api / chat / learn / admin / ops / app），仅做“结构适配”补丁
  * 产物是唯一自包含文件：web/index.html，不含任何外链 css/js

用法：python web_src/build.py [--check]
"""
import io
import os
import re
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
JS_DIR = os.path.join(SRC, 'js')
OUT = os.path.join(ROOT, 'web', 'index.html')

# learn.js 已恢复接入（学习中心回归）；顺序保证 API/UI 先于 Learn 定义
FILES = ['api.js', 'menu.js', 'chat.js', 'files.js', 'learn.js', 'sites.js', 'admin.js', 'ops.js', 'app.js']


def read(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return f.read()


def fail(msg):
    print('\n  [BUILD ERROR] ' + msg)
    sys.exit(1)


def patch(code, old, new, name, count=1):
    if old not in code:
        fail('补丁锚点未找到：%s' % name)
    return code.replace(old, new, count)


def patch_re(code, pattern, new, name):
    code2, n = re.subn(pattern, lambda m: new, code, count=1, flags=re.S)
    if n != 1:
        fail('正则补丁未命中：%s' % name)
    return code2


# ============================================================
# 1. 读取模块
# ============================================================
mods = {}
for f in FILES:
    p = os.path.join(JS_DIR, f)
    if not os.path.isfile(p):
        fail('缺少脚本：%s' % p)
    mods[f] = read(p)

# ============================================================
# 2. api.js —— 记住账号（主题切换已在源码中实现，深浅色由 data-theme 驱动）
# ============================================================
# 记住账号
anchor = "      if (!r.ok) { msg.textContent = r.msg || '登录失败'; return false; }\n      API.token = r.data.token;"
remember = ("      if (!r.ok) { msg.textContent = r.msg || '登录失败'; return false; }\n"
            "      try {\n"
            "        const _rem = document.getElementById('remember');\n"
            "        if (_rem && _rem.checked) localStorage.setItem('lc_user', UI.val('login-username'));\n"
            "        else localStorage.removeItem('lc_user');\n"
            "      } catch (e) { }\n"
            "      API.token = r.data.token;")
mods['api.js'] = patch(mods['api.js'], anchor, remember, 'api.remember')

# ============================================================
# 3. app.js —— 框架适配（grid 布局 / 全宽视图 / 角标 / 通讯录 / 登录页交互）
# ============================================================
app = mods['app.js']
app = patch(app,
            "      document.getElementById('app').style.display = 'flex';",
            "      document.getElementById('app').style.display = 'grid';",
            'app.start.display')
app = patch(app,
            "    go(view) {\n      this.view = view;",
            "    go(view) {\n      this.view = view;\n"
            "      /* 非聊天视图隐藏会话栏，让文件/通讯录/后台铺满（与原设计整页结构一致） */\n"
            "      document.getElementById('app').classList.toggle('full', view !== 'chat');",
            'app.go.full')

app = app.replace("document.getElementById('login-page').style.display = 'flex';",
                  "document.getElementById('login-page').style.display = '';")
if "style.display = 'flex';" in app and 'login-page' in app:
    pass

app = patch(app,
            "      const el = document.getElementById('badge-chat');\n"
            "      if (el) { el.textContent = this.unread ? (this.unread > 99 ? '99+' : this.unread) : ''; }",
            "      const el = document.getElementById('badge-chat');\n"
            "      if (el) {\n"
            "        el.textContent = this.unread ? (this.unread > 99 ? '99+' : this.unread) : '';\n"
            "        el.style.display = this.unread ? '' : 'none';\n"
            "      }",
            'app.updateUnread.badge')

# 品牌区 + 管理后台身份
app = patch(app,
            "      document.querySelectorAll('.admin-only').forEach(e => e.style.display = hasAdmin ? '' : 'none');",
            "      document.querySelectorAll('.admin-only').forEach(e => e.style.display = hasAdmin ? '' : 'none');\n"
            "      const _railMe = document.getElementById('rail-me');\n"
            "      if (_railMe) _railMe.textContent = (u.display_name || '我').slice(0, 1);\n"
            "      ['admin-name', 'admin-name-2'].forEach(function (id) {\n"
            "        const el = document.getElementById(id); if (el) el.textContent = u.display_name || '管理员';\n"
            "      });\n"
            "      ['admin-me', 'admin-avatar'].forEach(function (id) {\n"
            "        const el = document.getElementById(id); if (el) el.textContent = (u.display_name || '管').slice(0, 1);\n"
            "      });\n"
            "      const _site = document.getElementById('admin-site');\n"
            "      if (_site && s.name) _site.textContent = s.name;",
            'app.renderBrand.admin')

# 通讯录整段替换
contacts = read(os.path.join(SRC, 'patches', 'contacts_view.js'))
app = patch_re(app,
               r"    /\* ---------- 通讯录 ---------- \*/.*?(?=    /\* ---------- 个人中心 ---------- \*/)",
               contacts, 'app.contactsView')

# 登录页交互（显示密码 / 忘记密码 / 第三方占位 / 记住账号）
old_tabs = """    document.querySelectorAll('.ltab').forEach(b => b.onclick = () => {
      document.querySelectorAll('.ltab').forEach(x => x.classList.toggle('active', x === b));
      document.getElementById('login-form').style.display = b.dataset.tab === 'login' ? '' : 'none';
      document.getElementById('register-form').style.display = b.dataset.tab === 'register' ? '' : 'none';
      document.getElementById('code-form').style.display = b.dataset.tab === 'code' ? '' : 'none';
    });"""
new_tabs = old_tabs + """
    const _tp = document.getElementById('togglePassword');
    if (_tp) _tp.onclick = function () {
      const p = document.getElementById('login-password');
      p.type = p.type === 'password' ? 'text' : 'password';
    };
    const _fl = document.getElementById('forgotLink');
    if (_fl) _fl.onclick = function (e) {
      e.preventDefault();
      UI.toast('忘记密码：请联系管理员在「用户管理 → 重置密码」中处理', 'err');
    };
    document.querySelectorAll('#login-page [data-method]').forEach(function (b) {
      b.onclick = function () { UI.toast(b.dataset.method + '登录接口预留中', 'err'); };
    });
    document.querySelectorAll('#login-page [data-info]').forEach(function (a) {
      a.onclick = function (e) { e.preventDefault(); UI.toast(a.dataset.info + '页面待补充', 'err'); };
    });
    const _rm = document.getElementById('remember');
    const _saved = localStorage.getItem('lc_user');
    if (_saved && _rm) {
      document.getElementById('login-username').value = _saved;
      _rm.checked = true;
    }"""
app = patch(app, old_tabs, new_tabs, 'app.loginExtras')

app = patch(app,
            "    const un = localStorage.getItem('lc_user');",
            "    const un = localStorage.getItem('lc_user') || localStorage.getItem('yunliao_account');",
            'app.loginPrefill')
mods['app.js'] = app

# ============================================================
# 4. chat.js —— 会话头像取房间名首字
# ============================================================
mods['chat.js'] = patch(
    mods['chat.js'],
    "      document.getElementById('chat-room-name').textContent = r.data.name;",
    "      document.getElementById('chat-room-name').textContent = r.data.name;\n"
    "      const _ca = document.getElementById('chat-avatar');\n"
    "      if (_ca) _ca.textContent = (r.data.name || '聊').slice(0, 1);",
    'chat.roomAvatar')

# ============================================================
# 5. admin.js —— 侧栏面包屑 + 仪表盘复刻
# ============================================================
mods['admin.js'] = patch(
    mods['admin.js'],
    "      document.querySelectorAll('[data-atab]').forEach(b => b.classList.toggle('active', b.dataset.atab === this.cur));",
    "      document.querySelectorAll('[data-atab]').forEach(b => b.classList.toggle('active', b.dataset.atab === this.cur));\n"
    "      const _crumb = document.getElementById('admin-crumb');\n"
    "      if (_crumb) {\n"
    "        const _names = { dash: '数据总览', users: '用户管理', proreq: '资料申请', rooms: '房间管理',\n"
    "          msg: '消息审核', announce: '公告', sites: '常用网站', logs: '审计日志',\n"
    "          ipbl: 'IP 访问控制', perms: '权限管理', remote: '远程协助', db: '数据库', settings: '系统设置' };\n"
    "        _crumb.textContent = _names[this.cur] || '';\n"
    "      }",
    'admin.crumb')

dash = read(os.path.join(SRC, 'patches', 'admin_dash.js'))
mods['admin.js'] = patch_re(mods['admin.js'],
                            r"    /\* ---------- 仪表盘 ---------- \*/.*?(?=    /\* ---------- 用户管理 ---------- \*/)",
                            dash, 'admin.dash')

# ============================================================
# 6. 组装
# ============================================================
css = read(os.path.join(SRC, 'theme.css'))
# 学习中心样式独立成文件，追加在主题之后（复用同一套 token）
_learncss = os.path.join(SRC, 'learn.css')
if os.path.isfile(_learncss):
    css += '\n' + read(_learncss)
tpl = read(os.path.join(SRC, 'index.template.html'))

js_parts = []
for f in FILES:
    code = mods[f]
    if '</script' in code.lower():
        fail('%s 含有 </script>，无法内联' % f)
    js_parts.append('/* ==================== %s ==================== */\n%s' % (f, code.strip()))
js = '\n\n'.join(js_parts)

if '/*__DSH_CSS__*/' not in tpl or '/*__DSH_JS__*/' not in tpl:
    fail('模板缺少占位标记')
html = tpl.replace('/*__DSH_CSS__*/', css).replace('/*__DSH_JS__*/', js)

# 简单完整性校验
for token in ['id="login-page"', 'id="app"', 'id="room-list"', 'id="msg-list"',
              'id="admin-body"', 'id="contacts-body"', 'id="toast-wrap"', 'id="modal-root"']:
    if token not in html:
        fail('产物缺少 %s' % token)

with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
    f.write(html)

print('  单文件已生成：%s' % OUT)
print('  大小：%.1f KB（CSS %.1f KB / JS %.1f KB）' % (
    len(html.encode('utf-8')) / 1024.0, len(css.encode('utf-8')) / 1024.0, len(js.encode('utf-8')) / 1024.0))
print('  内联脚本模块：%s' % ', '.join(FILES))
