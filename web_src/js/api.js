/* ============ 基础：API / UI / WebSocket / 认证 ============ */
(function () {
  'use strict';

  const API = {
    token: localStorage.getItem('lc_token') || '',
    user: null,
    site: null,
    async req(method, path, data, isForm) {
      const opt = { method: method, headers: { 'X-Token': this.token } };
      let url = path;
      if (data && !isForm) {
        if (method === 'GET') {
          const q = new URLSearchParams();
          for (const k in data) if (data[k] !== undefined && data[k] !== null && data[k] !== '') q.append(k, data[k]);
          const s = q.toString();
          if (s) url += (url.indexOf('?') >= 0 ? '&' : '?') + s;
        } else {
          opt.headers['Content-Type'] = 'application/json';
          opt.body = JSON.stringify(data);
        }
      } else if (isForm) {
        opt.body = data;
      }
      try {
        const r = await fetch(url, opt);
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('json')) return { ok: r.ok, raw: await r.blob(), headers: r.headers };
        let j = {};
        try { j = await r.json(); } catch (e) { j = { ok: false, msg: '返回解析失败' }; }
        if (j.ok && j.data && j.data.msg && !j.msg) j.msg = j.data.msg;
        if (!j.ok && j.msg && r.status !== 404) {
          if (r.status === 401 && !/auth\/(login|register)/.test(path)) { Auth.logout(true); }
        }
        j.status = r.status;
        return j;
      } catch (e) {
        return { ok: false, msg: '网络错误：' + (e.message || '无法连接服务器') };
      }
    },
    get(p, d) { return this.req('GET', p, d); },
    post(p, d) { return this.req('POST', p, d); },
    put(p, d) { return this.req('PUT', p, d); },
    del(p, d) { return this.req('DELETE', p, d); },
    upload(p, fd) { return this.req('POST', p, fd, true); },
    download(name, blob) {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
    }
  };

  /* ---------- UI 工具 ---------- */
  const UI = {
    toast(msg, type) {
      const w = document.getElementById('toast-wrap');
      const d = document.createElement('div');
      d.className = 'toast ' + (type || '');
      d.textContent = msg;
      w.appendChild(d);
      setTimeout(() => { d.style.opacity = 0; d.style.transform = 'translateY(-8px)'; }, 2200);
      setTimeout(() => d.remove(), 2600);
    },
    /* 顶部引导横幅：变黄提醒时引导用户主动申请一次浏览器通知权限 */
    notifyGuide(cb) {
      if (document.getElementById('notify-guide')) return;
      const d = document.createElement('div');
      d.id = 'notify-guide';
      d.className = 'notify-guide';
      d.innerHTML = '<span class="ng-txt">🔔 开启浏览器通知，私聊 / @消息不错过（任务栏图标也会变黄提醒）</span>' +
        '<button class="btn btn-sm ng-yes">立即开启</button>' +
        '<button class="btn btn-sm ghost ng-no">暂不</button>';
      document.body.appendChild(d);
      d.querySelector('.ng-yes').onclick = () => { d.remove(); cb(true); };
      d.querySelector('.ng-no').onclick = () => { d.remove(); cb(false); };
      requestAnimationFrame(() => d.classList.add('show'));
    },
    esc(s) {
      return String(s === undefined || s === null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },
    nl2br(s) { return this.esc(s).replace(/\n/g, '<br>'); },
    avatar(name, color, size, avatarUrl) {
      const st = size ? 'width:' + size + 'px;height:' + size + 'px;line-height:' + size + 'px;' : '';
      if (avatarUrl) {
        const tk = API.token ? '?token=' + encodeURIComponent(API.token) : '';
        return '<div class="avatar avatar-photo" style="' + st + '">' +
          '<img src="' + this.esc(avatarUrl) + tk + '" alt="" class="avatar-img" onerror="this.parentNode.className=\'avatar\'">' +
          '</div>';
      }
      const t = (name || '?').trim();
      /* 与原设计稿一致：中文取首字，英文取前两位 */
      const txt = /[\u4e00-\u9fa5]/.test(t[0]) ? t.slice(0, 1) : t.slice(0, 2).toUpperCase();
      return '<div class="avatar" style="background:' + (color || '#2878f0') + ';' + st + '">' + this.esc(txt) + '</div>';
    },
    modal(title, bodyHtml, footHtml, width) {
      const root = document.getElementById('modal-root');
      root.innerHTML = '<div class="mask" onclick="if(event.target===this)UI.closeModal()"><div class="modal" style="max-width:' +
        (width || 620) + 'px"><div class="modal-head"><b>' + title + '</b><button class="icon-btn" onclick="UI.closeModal()">✕</button></div>' +
        '<div class="modal-body">' + bodyHtml + '</div>' +
        (footHtml ? '<div class="modal-foot">' + footHtml + '</div>' : '') + '</div></div>';
      return root.querySelector('.modal');
    },
    closeModal() { document.getElementById('modal-root').innerHTML = ''; },
    /* 内置输入弹窗：替代原生 prompt()（原生框在受限/内嵌浏览器中会被静默拦截） */
    prompt(o) {
      const inpId = 'ui-prompt-input';
      const type = o.type || 'text';
      const maxl = o.maxlength ? ' maxlength="' + o.maxlength + '"' : '';
      UI.modal(o.title || '请输入',
        '<label class="field"><span>' + UI.esc(o.label || '') + '</span>' +
        '<input type="' + type + '" id="' + inpId + '"' + maxl +
        ' placeholder="' + UI.esc(o.placeholder || '') + '" value="' + UI.esc(o.value || '') + '"></label>' +
        (o.hint ? '<p style="color:var(--text-3);font-size:12px">' + UI.esc(o.hint) + '</p>' : ''),
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn ' + (o.danger ? 'danger' : 'primary') + '" id="ui-prompt-ok">' +
        UI.esc(o.okText || '确定') + '</button>', o.width || 440);
      const inp = document.getElementById(inpId);
      const fire = () => {
        const v = inp.value.trim();
        if (o.validate) {
          const err = o.validate(v);
          if (err) { UI.toast(err, 'err'); inp.focus(); return; }   // 校验不过不关窗
        }
        UI.closeModal(); o.onOk(v);
      };
      inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); fire(); } };
      document.getElementById('ui-prompt-ok').onclick = fire;
      setTimeout(() => { inp.focus(); if (inp.select) inp.select(); }, 30);
    },
    confirm(msg, onYes, danger) {
      UI.modal('确认操作', '<p>' + UI.esc(msg) + '</p>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn ' + (danger ? 'danger' : 'primary') + '" id="confirm-yes">确定</button>', 420);
      document.getElementById('confirm-yes').onclick = function () { UI.closeModal(); onYes && onYes(); };
    },
    time(ts, fmt) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      const p = n => (n < 10 ? '0' : '') + n;
      const base = p(d.getHours()) + ':' + p(d.getMinutes());
      if (fmt === 'full') return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + base;
      const now = new Date();
      if (d.toDateString() === now.toDateString()) return base;
      return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + base;
    },
    size(n) {
      n = n || 0;
      const u = ['B', 'KB', 'MB', 'GB']; let i = 0;
      while (n >= 1024 && i < 3) { n /= 1024; i++; }
      return (i === 0 ? n : n.toFixed(1)) + u[i];
    },
    pager(total, page, size, fn) {
      const pages = Math.max(1, Math.ceil(total / size));
      if (total <= size) return '';
      let h = '<div class="pager"><button class="btn sm" ' + (page <= 1 ? 'disabled' : '') +
        ' onclick="' + fn + '(' + (page - 1) + ')">上一页</button><span>' + page + ' / ' + pages +
        '（共 ' + total + ' 条）</span><button class="btn sm" ' + (page >= pages ? 'disabled' : '') +
        ' onclick="' + fn + '(' + (page + 1) + ')">下一页</button></div>';
      return h;
    },
    colorOf(seed) {
      const p = ['#2f6fed', '#12a67a', '#e8734a', '#8a5cf6', '#d94f8a', '#0ea5e9', '#f2b13c', '#5b7085'];
      let h = 0;
      String(seed || '?').split('').forEach(c => h += c.charCodeAt(0));
      return p[h % p.length];
    },
    toggleTheme() {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      const next = dark ? 'light' : 'dark';
      this.applyTheme(next);
      try { localStorage.setItem('lc_theme', next); } catch (e) { }
      if (API.user) {
        API.user.theme = next;
        API.post('/api/auth/profile', { theme: next });
      }
      this.toast(next === 'dark' ? '已切换到深色模式' : '已切换到浅色模式', 'ok');
    },
    applyTheme(t) {
      const dark = t === 'dark';
      document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', dark ? '#0f141c' : '#f5f8ff');
    },
    /* 站点配色（管理员在后台设置，全站生效）：blue / bluepink */
    applyAccent(name) {
      const ok = ['blue', 'bluepink'].indexOf(name) >= 0 ? name : 'blue';
      document.documentElement.setAttribute('data-accent', ok);
    },
    /* 浏览器标签页黄色闪烁 + Windows 任务栏图标变黄闪烁：@提及 / 私聊等新消息且页面不在前台时调用 */
    _titleFlash: null, _titleBase: '', _notifyPermReq: false, _notifyGuideShown: false,
    requestNotifyPerm(manual) {
      /* 主动请求浏览器通知权限：会弹出浏览器原生的「XX网站请求发送消息」确认框。
         Windows 任务栏图标变黄闪烁依赖 Web Notification 授权；manual=true 时由用户点击按钮触发 */
      if (!window.Notification) return 'unsupported';
      if (Notification.permission === 'granted') {
        if (manual) UI.toast('浏览器通知已开启：私聊/@ 消息会实时提醒', 'ok');
        this.updateNotifyBtn();
        return 'granted';
      }
      if (Notification.permission === 'denied') {
        if (manual) UI.toast('通知权限已被拒绝：请在浏览器地址栏左侧图标中为本站重新开启通知', 'err');
        this.updateNotifyBtn();
        return 'denied';
      }
      if (this._notifyPermReq && !manual) return 'default';   // 首次交互自动请求只触发一次
      try {
        const p = Notification.requestPermission();
        if (p && p.then) {
          p.then(pr => {
            this.updateNotifyBtn();
            if (pr === 'granted') UI.toast('浏览器通知已开启：私聊/@ 消息会实时提醒', 'ok');
            else if (pr === 'denied') UI.toast('通知权限被拒绝，可在浏览器地址栏左侧图标中重新开启', 'err');
          }).catch(() => { this.updateNotifyBtn(); });
        }
      } catch (e) { /* 忽略 */ }
      this._notifyPermReq = true;
      return 'requesting';
    },
    updateNotifyBtn() {
      const b = document.getElementById('notify-perm-btn');
      if (!b) return;
      if (!window.Notification) { b.style.display = 'none'; return; }
      b.style.display = '';
      const p = Notification.permission;
      b.textContent = p === 'granted' ? '✅ 浏览器通知已开启' : (p === 'denied' ? '⚠ 通知被拒绝' : '🔔 开启浏览器通知');
    },
    notifyTitle(label, opts) {
      if (!window.pywebview) this._maybeGuideNotifyPerm();   // 变黄提醒时，引导用户主动申请一次浏览器通知权限（客户端 WebView 无此权限概念，跳过）
      if (!document.hidden) return;
      /* 桌面客户端（pywebview）：走原生系统通知（QQ 式托盘气泡弹窗） */
      if (window.pywebview && window.pywebview.api && window.pywebview.api.notify) {
        try {
          const o = opts || {};
          window.pywebview.api.notify(o.title || label, o.body || '收到一条新消息，点击查看');
        } catch (e) { /* 桥接失败不影响标题闪烁 */ }
      }
      /* Windows 任务栏图标变黄闪烁：通过桌面通知实现（页面不在前台时浏览器图标闪烁） */
      try {
        if (window.Notification && Notification.permission === 'granted') {
          const o = opts || {};
          const n = new Notification(o.title || label, {
            body: o.body || '收到一条新消息，点击查看',
            tag: 'lc-desktop-notify'
          });
          setTimeout(() => { try { n.close(); } catch (e) { /* */ } }, 8000);
          n.onclick = () => { window.focus(); try { n.close(); } catch (e) { /* */ } };
        }
      } catch (e) { /* 桌面通知失败不影响标签闪烁 */ }
      if (this._titleFlash) { this._titleFlash.label = label; return; }  // 已在闪烁则更新文案
      const base = this._titleBase || document.title;
      this._titleBase = base;
      let on = false;
      this._titleFlash = {
        label: label,
        timer: setInterval(() => {
          if (!document.hidden) { this._stopTitleFlash(); return; }
          on = !on;
          document.title = on ? ('🟡 ' + this._titleFlash.label + ' - ' + base) : base;
        }, 750)
      };
      if (!this._titleVisibilityHandler) {
        this._titleVisibilityHandler = () => {
          if (!document.hidden) this._stopTitleFlash();
        };
        document.addEventListener('visibilitychange', this._titleVisibilityHandler);
      }
    },
    _stopTitleFlash() {
      if (this._titleFlash && this._titleFlash.timer) clearInterval(this._titleFlash.timer);
      this._titleFlash = null;
      if (this._titleBase) { document.title = this._titleBase; }
    },
    /* 变黄提醒时引导主动申请一次通知权限（未授权、本会话未引导过才弹；用户点「立即开启」才请求） */
    _maybeGuideNotifyPerm() {
      if (!window.Notification || Notification.permission !== 'default') return;
      if (this._notifyGuideShown) return;
      this._notifyGuideShown = true;
      const show = () => {
        if (Notification.permission !== 'default') return;
        UI.notifyGuide(ok => { if (ok) this.requestNotifyPerm(true); });
      };
      if (!document.hidden) { show(); }
      else if (!this._guideVisHandler) {
        this._guideVisHandler = () => {
          if (!document.hidden) {
            document.removeEventListener('visibilitychange', this._guideVisHandler);
            this._guideVisHandler = null;
            show();
          }
        };
        document.addEventListener('visibilitychange', this._guideVisHandler);
      }
    },
    val(id) { const e = document.getElementById(id); return e ? e.value.trim() : ''; }
  };

  /* ---------- WebSocket ---------- */
  const WS = {
    sock: null, handlers: {}, retry: 0, timer: null,
    on(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); },
    emit(t, d) {
      (this.handlers[t] || []).forEach(fn => { try { fn(d); } catch (e) { console.error(e); } });
    },
    connect() {
      if (!API.token) return;
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      try { this.sock = new WebSocket(proto + '://' + location.host + '/ws'); } catch (e) { return; }
      this.sock.onopen = () => {
        this.retry = 0;
        this.setState(true);
        this.send('auth', { token: API.token });
      };
      this.sock.onmessage = ev => {
        /* 二进制帧：远程协助画面流（管理员端接收被控端 JPEG 帧） */
        if (typeof ev.data === 'object' && ev.data instanceof Blob) {
          if (window.RemoteOps) RemoteOps.onFrame(ev.data);
          return;
        }
        let m = null;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.t === 'ping') { this.send('pong', {}); return; }
        WS.emit(m.t, m.d);
      };
      this.sock.onclose = () => {
        this.setState(false);
        this.retry++;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => WS.connect(), Math.min(10000, 1000 * this.retry));
      };
      this.sock.onerror = () => { try { this.sock.close(); } catch (e) { } };
    },
    send(t, d) {
      if (this.sock && this.sock.readyState === 1) this.sock.send(JSON.stringify({ t: t, d: d || {} }));
    },
    setState(ok) {
      const el = document.getElementById('conn-state');
      if (el) { el.textContent = ok ? '已连接' : '连接中断，重连中…'; el.className = 'conn ' + (ok ? 'on' : 'off'); }
    }
  };

  /* ---------- 认证 ---------- */
  const Auth = {
    async doLogin(e) {
      e.preventDefault();
      const msg = document.getElementById('login-msg');
      const btn = document.getElementById('login-btn');
      msg.textContent = ''; btn.disabled = true; btn.textContent = '登录中…';
      const r = await API.post('/api/auth/login', {
        username: UI.val('login-username'), password: UI.val('login-password')
      });
      btn.disabled = false; btn.textContent = '登 录';
      if (!r.ok) { msg.textContent = r.msg || '登录失败'; return false; }
      API.token = r.data.token;
      localStorage.setItem('lc_token', API.token);
      setTokenCookie(API.token);
      afterLogin(r.data.user);
      return false;
    },
    async doRegister(e) {
      e.preventDefault();
      const f = e.target;
      const d = {};
      new FormData(f).forEach((v, k) => d[k] = v);
      const msg = document.getElementById('reg-msg');
      if (d.password !== d.password2) { msg.textContent = '两次输入的密码不一致'; return false; }
      delete d.password2;
      const r = await API.post('/api/auth/register', d);
      if (!r.ok) { msg.textContent = r.msg; return false; }
      if (r.data.pending) { UI.toast('注册成功，请等待管理员审核', 'ok'); document.querySelector('.ltab[data-tab="login"]').click(); }
      else {
        API.token = r.data.token; localStorage.setItem('lc_token', API.token);
        setTokenCookie(API.token);
        await afterLogin(null);
      }
      return false;
    },
    async doCodeLogin(e) {
      e.preventDefault();
      const msg = document.getElementById('code-msg');
      const btn = document.getElementById('code-btn');
      msg.textContent = ''; btn.disabled = true; btn.textContent = '登录中…';
      const r = await API.post('/api/auth/client-login', { code: UI.val('code-input') });
      btn.disabled = false; btn.textContent = '免密登录';
      if (!r.ok) { msg.textContent = r.msg || '登录失败'; return false; }
      API.token = r.data.token;
      localStorage.setItem('lc_token', API.token);
      setTokenCookie(API.token);
      afterLogin(r.data.user);
      return false;
    },
    async loadMe() {
      const r = await API.get('/api/auth/me');
      if (!r.ok) return false;
      API.user = r.data.user; API.site = r.data.site;
      UI.applyTheme((function () { try { return localStorage.getItem('lc_theme'); } catch (e) { return null; } })()
        || API.user.theme || 'light');
      UI.applyAccent(API.site && API.site.theme_color);
      return true;
    },
    logout(silent) {
      if (!silent) API.post('/api/auth/logout', {});
      localStorage.removeItem('lc_token');
      try { document.cookie = 'token=; path=/; Max-Age=0'; } catch (e) { }
      API.token = ''; API.user = null;
      location.reload();
    }
  };

  function setTokenCookie(t) {
    try { document.cookie = 'token=' + encodeURIComponent(t || '') + '; path=/; SameSite=Lax'; } catch (e) { }
  }

  async function afterLogin(user) {
    setTokenCookie(API.token);
    const ok = await Auth.loadMe();
    if (!ok) { UI.toast('登录状态获取失败', 'err'); return; }
    App.start();
    UI.toast('欢迎回来，' + API.user.display_name, 'ok');
    if (API.user.must_change_pwd) setTimeout(() => App.forceChangePwd(), 600);
  }

  window.API = API; window.UI = UI; window.WS = WS; window.Auth = Auth;
})();
