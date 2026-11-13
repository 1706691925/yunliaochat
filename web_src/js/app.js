/* ============ 应用主控：路由 / 通知 / 通讯录 / 个人中心 ============ */
(function () {
  'use strict';
  const App = {
    view: 'chat', unread: 0, notifies: [], contacts: [],

    async start() {
      document.getElementById('login-page').style.display = 'none';
      document.getElementById('app').style.display = 'flex';
      if (localStorage.getItem('lc_theme')) UI.applyTheme(localStorage.getItem('lc_theme'));
      this.renderBrand();
      this.bindWS();
      WS.connect();
      Chat.init();
      if (window.GameOps) GameOps.init();
      /* 首次交互时申请桌面通知权限（Windows 任务栏图标变黄闪烁依赖 Web Notification） */
      const reqNotify = () => { UI.requestNotifyPerm(); document.removeEventListener('click', reqNotify, true); };
      document.addEventListener('click', reqNotify, true);
      this.go(this.view);
      this.loadNotify();
      this.loadAnnounce();
      /* 用过桌面助手的用户：登录后自动把登录态交给它（浏览器可能弹一次确认） */
      try {
        if (localStorage.getItem('lc_desk') === '1' && API.token && !sessionStorage.getItem('lc_desk_auto')) {
          sessionStorage.setItem('lc_desk_auto', '1');
          setTimeout(() => { try { location.href = this._deskUrl(); } catch (e) { } }, 1500);
        }
      } catch (e) { }
      /* 空白区域右键：全局菜单（会话 / 消息 / 联系人 / 文件行命中专用菜单后不会冒泡到这里） */
      const appEl = document.getElementById('app');
      if (appEl && window.CtxMenu) {
        appEl.addEventListener('contextmenu', e => {
          if (e.defaultPrevented) return;
          CtxMenu.show(e, this.globalMenu());
        });
      }
      /* 桌面提醒助手点击提醒后跳转：/?room=<id> 直接打开对应会话 */
      try {
        const q = new URLSearchParams(location.search);
        const rid = parseInt(q.get('room') || '0', 10);
        if (rid > 0) {
          this.go('chat');
          setTimeout(() => Chat.openRoom(rid), 350);
        }
      } catch (e) { }
    },

    globalMenu() {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      return [
        { label: '发起会话 / 新建房间', icon: 'plus', onClick: () => { this.go('chat'); setTimeout(() => Chat.newRoom(), 60); } },
        { label: '搜索消息', icon: 'search', onClick: () => { this.go('chat'); setTimeout(() => Chat.searchInRoom(), 60); } },
        { label: '通知中心', icon: 'bell', onClick: () => this.toggleNotify() },
        { sep: true },
        { label: dark ? '切换到浅色模式' : '切换到深色模式', icon: 'theme', onClick: () => UI.toggleTheme() },
        { label: '刷新数据', icon: 'refresh', onClick: () => { Chat.loadRooms(); if (Chat.current) Chat.loadMessages(); this.loadNotify(); UI.toast('已刷新', 'ok'); } },
        { sep: true },
        { label: '退出登录', icon: 'exit', danger: true, onClick: () => Auth.logout() }
      ];
    },

    renderBrand() {
      const u = API.user, s = API.site || {};
      document.getElementById('site-name').textContent = s.name || '云聊';
      document.getElementById('brand-logo').textContent = (s.name || '云聊').slice(0, 2);
      const sa = document.getElementById('side-avatar');
      if (u.avatar) {
        sa.textContent = '';
        sa.style.backgroundImage = "url('" + u.avatar + '?token=' + encodeURIComponent(API.token) + "')";
        sa.style.backgroundSize = 'cover';
        sa.style.backgroundPosition = 'center';
        sa.style.backgroundColor = 'var(--panel-2)';
      } else {
        sa.textContent = (u.display_name || '').slice(0, 2);
        sa.style.backgroundImage = '';
        sa.style.background = UI.colorOf ? UI.colorOf(u.username) : '#2f6fed';
      }
      document.getElementById('side-name').innerHTML = UI.esc(u.display_name) +
        (u.title ? '<span class="title-tag">' + UI.esc(u.title) + '</span>' : '');
      document.getElementById('side-role').textContent = u.role_name + (u.dept ? ' · ' + u.dept : '');
      const hasAdmin = (u.perms || []).indexOf('admin.access') >= 0;
      document.querySelectorAll('.admin-only').forEach(e => e.style.display = hasAdmin ? '' : 'none');
      UI.applyAccent(s && s.theme_color);
      document.title = (s.name || '云聊');
    },

    bindWS() {
      WS.on('presence', () => this.loadOnline());
      /* WebSocket 层错误提示（如禁言发言被拒）：此前无人监听，消息被静默丢弃 */
      WS.on('error', d => {
        if (!d || !d.msg) return;
        UI.toast(d.msg, 'err');
        if (/禁言/.test(d.msg)) { Chat.muted = true; Chat.updateComposer(); }
      });
      WS.on('online', d => {
        const el = document.getElementById('online-stat');
        if (el) el.textContent = '在线 ' + (d ? d.length : 0) + ' 人';
      });
      WS.on('notice', d => {
        if (d.type === 'maintenance' && d.on) UI.toast(d.note || '系统进入维护模式', 'err');
        /* 管理员切换站点配色：在线客户端即时生效 */
        if (d.type === 'theme' && d.value) UI.applyAccent(d.value);
      });
      WS.on('notify', d => {
        this.loadNotify(); this.loadAnnounce(); if (window.Admin) Admin._proreqBadge();
        const t = (d && d.type) || '';
        if (document.hidden && (t === 'mention' || t === 'reply')) UI.notifyTitle(t === 'reply' ? '有人回复你' : '有人@你');
      });
      /* 账号不允许重复登录：在其他设备登录后，本会话被服务端强制下线 */
      WS.on('kick', d => {
        UI.toast((d && d.msg) || '您的账号已在其他设备登录，本会话已强制下线', 'err');
        clearTimeout(WS.timer); WS.retry = 9999;   // 阻止自动重连（旧 token 已失效）
        try { if (WS.sock) WS.sock.close(); } catch (e) { }
        API.token = '';
        localStorage.removeItem('lc_token');
        setTimeout(() => {
          document.getElementById('app').style.display = 'none';
          document.getElementById('login-page').style.display = 'flex';
        }, 1200);
      });
    },

    go(view) {
      this.view = view;
      ['chat', 'files', 'learn', 'sites', 'contacts', 'games', 'profile', 'admin'].forEach(v => {
        const el = document.getElementById('view-' + v);
        if (el) el.style.display = (v === view ? 'flex' : 'none');
      });
      document.querySelectorAll('.nav-item').forEach(a => a.classList.toggle('active', a.dataset.view === view));
      const titles = { chat: '聊天室', files: '文件中心', learn: '学习中心', sites: '常用网站', contacts: '通讯录', games: '小游戏', profile: '个人中心', admin: '管理后台' };
      document.getElementById('view-title').textContent = titles[view] || '';
      document.getElementById('view-sub').textContent = '';
      if (view === 'chat') { Chat.loadRooms(); }
      if (view === 'files' && window.Files) Files.render();
      if (view === 'learn' && window.Learn) { Learn.render(); App.updateLearnBadge(); }
      if (view === 'sites' && window.Sites) Sites.render();
      if (view === 'contacts') this.contactsView();
      if (view === 'games' && window.GameOps) GameOps.render(document.getElementById('games-body'));
      if (view === 'profile') this.profileView();
      if (view === 'admin') {
        if ((API.user.perms || []).indexOf('admin.access') < 0) { UI.toast('无权访问管理后台', 'err'); return this.go('chat'); }
        Admin.render();
        Admin._proreqBadge();
      }
      this.loadOnline();
      location.hash = '#' + view;
    },

    async loadOnline() {
      const r = await API.get('/api/chat/online');
      if (r.ok) document.getElementById('online-stat').textContent = '在线 ' + r.data.count + ' 人';
    },

    /* ---------- 通知 ---------- */
    async loadNotify(onlyUnread) {
      const r = await API.get('/api/notifications');
      if (!r.ok) return;
      this.notifies = r.data.list || [];
      const un = r.data.unread || 0;
      document.getElementById('notify-dot').style.display = un ? '' : 'none';
      const box = document.getElementById('notify-list');
      if (box) {
        box.innerHTML = this.notifies.length ? this.notifies.map(n =>
          '<div class="notif-item ' + (n.is_read ? '' : 'unread') + '" onclick="App.openNotify(' + n.id + ')">' +
          '<b>' + UI.esc(n.title) + '</b><p>' + UI.nl2br(n.content || '') + '</p>' +
          '<small>' + UI.time(n.created_at, 'full') + '</small></div>').join('') : '<div class="empty">暂无通知</div>';
      }
      if (!onlyUnread) this.updateUnread();
    },
    async openNotify(id) {
      await API.post('/api/notifications/read', { id: id });
      this.loadNotify();
    },
    async readAllNotify() {
      await API.post('/api/notifications/read', {});
      UI.toast('已全部标记为已读', 'ok');
      this.loadNotify();
    },
    toggleNotify() {
      const d = document.getElementById('notify-drawer');
      d.style.display = d.style.display === 'none' ? 'flex' : 'none';
      if (d.style.display === 'flex') {
        this.loadNotify();
        UI.updateNotifyBtn();
      }
    },
    updateUnread(d) {
      if (d && d.total !== undefined) this.unread = d.total;
      const el = document.getElementById('badge-chat');
      if (el) { el.textContent = this.unread ? (this.unread > 99 ? '99+' : this.unread) : ''; }
      API.get('/api/auth/me').then(r => { if (r.ok) this.unread = r.data.unread.total || 0; });
    },

    /* 学习中心红点：学生=待交作业+可考考试；教师=待批阅+待阅卷 */
    async updateLearnBadge() {
      try {
        const r = await API.get('/api/learn/my/todo');
        if (!r.ok) return;
        const d = r.data;
        const n = d.role_mode === 'teacher'
          ? (d.to_grade_homework || 0) + (d.to_grade_exams || 0)
          : (d.pending_homework || 0) + (d.available_exams || 0);
        const el = document.getElementById('badge-learn');
        if (el) {
          el.textContent = n ? (n > 99 ? '99+' : n) : '';
          el.style.display = n ? '' : 'none';
        }
      } catch (e) { /* 静默失败，不打扰用户 */ }
    },

    /* ---------- 公告 ---------- */
    async loadAnnounce() {
      const r = await API.get('/api/announcements');
      if (!r.ok) return;
      this.announces = r.data.list || [];
      this.renderAnnounceDrawer();
      /* 登录后弹窗：仅弹出勾选了「登录后显示」且本会话未读过的公告 */
      const popups = this.announces.filter(a => Number(a.popup) === 1 &&
        !sessionStorage.getItem('lc_ann_' + a.id));
      this._annQueue = popups;
      if (popups.length) this.showNextAnnounce();
    },
    renderAnnounceDrawer() {
      const box = document.getElementById('notify-ann');
      if (!box) return;
      const list = (this.announces || []).slice(0, 8);
      box.innerHTML = '<div class="drawer-sec-head"><b>公告</b><span class="muted">' + list.length + ' 条</span></div>' +
        (list.length ? list.map(a =>
          '<button class="ann-row" type="button" onclick="App.openAnnounce(' + a.id + ')">' +
          '<span class="ann-dot ' + UI.esc(a.level || 'info') + '"></span>' +
          '<span class="ann-main"><b>' + UI.esc(a.title) + '</b>' +
          '<small>' + UI.esc(a.created_str || '') + (Number(a.popup) === 1 ? ' · 登录弹窗' : '') +
          (Number(a.force_seconds) > 0 ? ' · 强制 ' + a.force_seconds + ' 秒' : '') + '</small></span>' +
          (a.pinned ? '<span class="tag">置顶</span>' : '') + '</button>').join('')
          : '<div class="empty sm">暂无公告</div>');
    },
    openAnnounce(id) {
      const a = (this.announces || []).filter(x => x.id === id)[0];
      if (!a) return;
      sessionStorage.setItem('lc_ann_' + a.id, '1');
      this.showAnnounce(a, 0);
    },
    /* 依次弹出公告；force>0 时倒计时结束才能关闭 */
    showNextAnnounce() {
      const q = this._annQueue || [];
      if (!q.length) return;
      const a = q.shift();
      sessionStorage.setItem('lc_ann_' + a.id, '1');
      this.showAnnounce(a, Math.max(0, parseInt(a.force_seconds, 10) || 0));
    },
    showAnnounce(a, force) {
      const lv = { urgent: '紧急', warning: '重要', info: '通知' }[a.level] || '通知';
      const cls = a.level === 'urgent' ? 'danger' : (a.level === 'warning' ? 'warn' : 'info');
      UI.modal('公告 · ' + UI.esc(a.title),
        '<div class="ann-meta"><span class="tag ' + cls + '">' + lv + '</span>' +
        '<span class="muted">' + UI.esc(a.author || '系统') + ' · ' + UI.esc(a.created_str || '') + '</span></div>' +
        '<div class="ann-content">' + UI.nl2br(a.content || '') + '</div>' +
        '<div class="ann-tip" id="ann-tip">' + (force > 0 ? '请阅读后关闭（不少于 ' + force + ' 秒）' : '') + '</div>',
        '<button class="btn primary" id="ann-ok" onclick="App.announceOk()"' + (force > 0 ? ' disabled' : '') + '>' +
        (force > 0 ? '请阅读 ' + force + ' 秒' : '知道了') + '</button>', 560);
      if (force > 0) {
        /* 强制公告：去掉关闭按钮与遮罩点击关闭，倒计时结束才可点 */
        const mask = document.querySelector('#modal-root .mask');
        if (mask) {
          mask.onclick = null;
          const x = mask.querySelector('.modal-head .icon-btn');
          if (x) x.remove();
        }
        const btn = document.getElementById('ann-ok');
        const tip = document.getElementById('ann-tip');
        let left = force;
        const timer = setInterval(() => {
          if (!document.getElementById('ann-ok')) { clearInterval(timer); return; }
          left -= 1;
          if (left <= 0) {
            clearInterval(timer);
            btn.disabled = false;
            btn.textContent = '知道了';
            if (tip) tip.textContent = '';
          } else {
            btn.textContent = '请阅读 ' + left + ' 秒';
          }
        }, 1000);
      }
    },
    announceOk() {
      UI.closeModal();
      this.showNextAnnounce();
    },

    /* ---------- 桌面助手（下载 / 同步登录） ---------- */
    _deskUrl() {
      return 'yunliao://auth?server=' + encodeURIComponent(location.origin) +
        '&token=' + encodeURIComponent(API.token || '');
    },
    async desktopHelper() {
      let exeOk = false, setupOk = false;
      try { setupOk = (await fetch('/downloads/云聊提醒-安装包.exe', { method: 'HEAD' })).ok; } catch (e) { }
      try { exeOk = (await fetch('/downloads/云聊提醒.exe', { method: 'HEAD' })).ok; } catch (e) { }
      const hasPkg = exeOk || setupOk;
      const used = localStorage.getItem('lc_desk') === '1';
      UI.modal('云聊桌面助手',
        '<div class="desk-intro">' +
        '<p>不用一直开着网页：有人 <b>@ 你</b>、给你 <b>发私聊</b>，或在群里发消息时，桌面右下角弹出提醒（可配声音）。</p>' +
        '<ul class="desk-list">' +
        '<li>提醒范围自选：@我 / 私聊 / 全部群消息</li>' +
        '<li>登录状态与网页同步：点「用当前账号打开」直接进入，不用再输密码</li>' +
        '<li>网页退出登录后，桌面助手会自动退出</li>' +
        '<li>支持开机自启、收起为悬浮球</li></ul>' +
        (hasPkg ? '' : '<p class="desk-warn">管理员还没打包安装包。可让管理员双击「打包安装包.bat」生成，或先用源码运行。</p>') +
        '<p class="desk-meta">' + (used ? '已开启“登录后自动打开桌面助手”；桌面助手登录一次后会记住密码，下次自动登录。' : '提示：浏览器询问是否打开「云聊提醒」时，选择「打开」并勾选“始终允许”。') + '</p>' +
        '<div class="desk-meta" style="margin-top:6px">登录后自动打开：' +
        '<b style="color:' + (used ? '#12a67a' : '#8a9ab0') + '">' + (used ? '已开启' : '未开启') + '</b>' +
        '<button class="btn sm" type="button" style="margin-left:8px" onclick="App.toggleDeskAuto()">' +
        (used ? '关闭' : '开启') + '</button></div>' +
        '</div>',
        (setupOk ? '<a class="btn" href="/downloads/云聊提醒-安装包.exe" download>下载安装包</a>' : '') +
        (exeOk ? '<a class="btn" href="/downloads/云聊提醒.exe" download>' + (setupOk ? '绿色版' : '下载安装包') + '</a>' : '') +
        '<button class="btn" onclick="App.copyDeskLink()">复制登录链接</button>' +
        '<button class="btn primary" onclick="App.openDesktop()">用当前账号打开</button>', 560);
    },
    openDesktop() {
      localStorage.setItem('lc_desk', '1');
      const url = this._deskUrl();
      try { location.href = url; } catch (e) { }
      setTimeout(() => UI.toast('如果浏览器没有弹窗，说明还没安装桌面助手，请先下载安装包', 'err'), 1800);
    },
    toggleDeskAuto() {
      const on = localStorage.getItem('lc_desk') === '1';
      if (on) localStorage.removeItem('lc_desk');
      else localStorage.setItem('lc_desk', '1');
      UI.toast(on ? '已关闭登录后自动打开' : '已开启：下次登录会自动打开桌面助手', 'ok');
      this.desktopHelper();
    },
    copyDeskLink() {
      const url = this._deskUrl();
      const done = () => UI.toast('登录链接已复制，可在桌面助手里打开', 'ok');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done).catch(() => this._copyFallback(url, done));
      } else this._copyFallback(url, done);
    },
    _copyFallback(text, done) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        done();
      } catch (e) { UI.toast('复制失败，请手动复制', 'err'); }
    },

    /* ---------- 通讯录 ---------- */
    async contactsView() {
      const box = document.getElementById('contacts-body');
      box.innerHTML = '<div class="empty">加载中…</div>';
      const r = await API.get('/api/users');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      this.contacts = r.data.list;
      const groups = {};
      this.contacts.forEach(u => { (groups[u.dept || '未分配'] = groups[u.dept || '未分配'] || []).push(u); });
      box.innerHTML =
        '<div class="toolbar"><input id="ct-kw" placeholder="搜索姓名/部门" oninput="App.filterContacts(this.value)"></div>' +
        Object.keys(groups).map(d =>
          '<div class="panel"><div class="panel-head"><b>' + UI.esc(d) + '（' + groups[d].length + '）</b></div>' +
          '<div class="panel-body" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px">' +
          groups[d].map(u => '<div class="list-item" data-name="' + UI.esc(u.display_name + ' ' + u.dept) + '" style="border:1px solid var(--border);border-radius:8px;padding:8px">' +
            UI.avatar(u.display_name, u.color, 0, u.avatar) +
            '<div class="li-main"><b>' + UI.esc(u.display_name) + (u.title ? '<span class="title-tag">' + UI.esc(u.title) + '</span>' : '') + (u.online ? ' <span class="tag ok">在线</span>' : '') + '</b>' +
            '<small>' + UI.esc(u.position || '') + ' · ' + UI.esc(u.username) + '</small></div>' +
            '<button class="btn sm" onclick="Chat.startDM(' + u.id + ')">私聊</button></div>').join('') +
          '</div></div>').join('');
    },
    filterContacts(kw) {
      document.querySelectorAll('#contacts-body [data-name]').forEach(el => {
        el.style.display = el.dataset.name.toLowerCase().indexOf(kw.toLowerCase()) >= 0 ? '' : 'none';
      });
    },

    /* ---------- 个人中心 ---------- */
    /* 设置自定义头像：上传图片（kind=avatar）→ 关联到当前用户 → 刷新展示 */
    async changeAvatar() {
      const inp = document.getElementById('avatar-file');
      if (!inp || !inp.files || !inp.files[0]) return;
      const f = inp.files[0];
      if (!/\.(png|jpe?g|gif|webp)$/i.test(f.name)) return UI.toast('头像仅支持图片', 'err');
      if (f.size > 2 * 1024 * 1024) return UI.toast('头像图片不能超过 2MB', 'err');
      const fd = new FormData();
      fd.append('file', f);
      fd.append('kind', 'avatar');
      const r = await API.upload('/api/files/upload', fd);
      if (!r.ok) return UI.toast(r.msg, 'err');
      const fid = (r.data && r.data.list && r.data.list[0] && r.data.list[0].id);
      if (!fid) return UI.toast('头像上传失败', 'err');
      const r2 = await API.put('/api/auth/profile', { avatar: fid });
      UI.toast(r2.ok ? '头像已更新' : r2.msg, r2.ok ? 'ok' : 'err');
      if (r2.ok) {
        await Auth.loadMe();
        this.renderBrand();
        this.profileView();
      }
      inp.value = '';
    },
    async profileView() {
      const u = API.user;
      const box = document.getElementById('profile-body');
      /* 姓名/头衔默认需管理员审核；后端返回 need_approval 前先用本地判断兜底 */
      const isAdminEditor = (u.perms || []).indexOf('user.manage') >= 0;
      let needApproval = !isAdminEditor;
      let reqs = [];
      const rr = await API.get('/api/auth/profile-requests');
      if (rr.ok) {
        needApproval = !!rr.data.need_approval;
        reqs = rr.data.list || [];
      }
      this._profileApproval = needApproval;
      const pendingReq = reqs.filter(x => x.status === 'pending')[0];
      const lockedField = (label, value, id) =>
        '<label class="field"><span>' + label + (needApproval
          ? '<em style="font-style:normal;color:var(--text-3)">（需管理员审核）</em>' : '') + '</span>' +
        '<input id="' + id + '" maxlength="40" value="' + UI.esc(value || '') + '"' +
        (needApproval ? ' readonly disabled' : '') + '></label>';
      const reqBadge = s => ({ pending: '<span class="tag warn">待审核</span>',
        approved: '<span class="tag ok">已通过</span>',
        rejected: '<span class="tag danger">已驳回</span>' }[s] || UI.esc(s));
      box.innerHTML =
        '<div class="panel"><div class="panel-head"><b>个人资料</b>' +
        (needApproval ? '<button class="btn sm primary" onclick="App.applyProfileChange()">申请修改头衔</button>' : '') +
        '</div><div class="panel-body">' +
        '<div style="display:flex;gap:16px;align-items:center;margin-bottom:14px">' +
        UI.avatar(u.display_name, '#2f6fed', 56, u.avatar) +
        '<div><b style="font-size:16px">' + UI.esc(u.display_name) +
        (u.title ? '<span class="title-tag">' + UI.esc(u.title) + '</span>' : '') + '</b><br>' +
        '<small style="color:var(--text-3)">@' + UI.esc(u.username) + ' · ' + UI.esc(u.role_name) + ' · ' + UI.esc(u.dept || '未分配') + '</small>' +
        '<div style="margin-top:6px"><label class="btn sm" style="cursor:pointer">设置头像' +
        '<input type="file" id="avatar-file" accept="image/png,image/jpeg,image/gif,image/webp" style="display:none" onchange="App.changeAvatar()">' +
        '</label> <span style="font-size:11px;color:var(--text-3)">png/jpg/gif/webp，≤2MB</span></div></div></div>' +
        (pendingReq ? '<p style="font-size:13px;color:var(--text-2);margin-bottom:10px">' +
          '当前有 <b>1</b> 条头衔修改申请待管理员审核：' +
          UI.esc(pendingReq.old_title || '（无）') + ' → <b>' + UI.esc(pendingReq.new_title || '（清空）') + '</b></p>' : '') +
        '<div class="grid2">' +
        '<label class="field"><span>昵称<em style="font-style:normal;color:var(--text-3)">（聊天、通讯录与 @提及 中显示）</em></span>' +
        '<input id="pf-name" maxlength="40" value="' + UI.esc(u.display_name) + '"></label>' +
        '<label class="field"><span>用户名<em style="font-style:normal;color:var(--text-3)">（登录账号，不可修改）</em></span>' +
        '<input value="' + UI.esc(u.username) + '" readonly disabled></label>' +
        lockedField('自定义头衔（≤20 字，聊天中展示）', u.title, 'pf-title') +
        '<label class="field"><span>个性签名</span><input id="pf-sign" value="' + UI.esc(u.signature || '') + '"></label>' +
        '<label class="field"><span>部门</span><input id="pf-dept" value="' + UI.esc(u.dept || '') + '"></label>' +
        '<label class="field"><span>岗位</span><input id="pf-pos" value="' + UI.esc(u.position || '') + '"></label>' +
        '<label class="field"><span>邮箱</span><input id="pf-email" value="' + UI.esc(u.email || '') + '"></label>' +
        '<label class="field"><span>手机</span><input id="pf-phone" value="' + UI.esc(u.phone || '') + '"></label></div>' +
        '<div style="margin-top:10px;display:flex;gap:8px">' +
        '<button class="btn primary" onclick="App.saveProfile()">保存资料</button>' +
        '<button class="btn" onclick="App.changePwd()">修改密码</button>' +
        '<button class="btn danger" onclick="Auth.logout()">退出登录</button></div>' +
        '</div></div>' +
        (reqs.length ? '<div class="panel"><div class="panel-head"><b>头衔修改申请（' + reqs.length + '）</b></div>' +
          '<div>' + reqs.map(q => '<div class="list-item"><div class="li-main">' +
            '<b>' + reqBadge(q.status) + ' ' + UI.esc(q.field_names || '') + '</b>' +
            '<small>' + UI.esc(q.old_title || '（无）') + ' → ' + UI.esc(q.new_title || '（清空）') + '</small>' +
            '<small>提交于 ' + UI.esc(q.created_str) +
            (q.reason ? ' · 理由：' + UI.esc(q.reason) : '') +
            (q.reviewed_str ? ' · 审核：' + UI.esc(q.reviewer_name || '') + ' ' + UI.esc(q.reviewed_str) : '') +
            (q.review_note ? ' · 批注：' + UI.esc(q.review_note) : '') + '</small></div></div>').join('') +
          '</div></div>' : '') +
        '<div class="panel"><div class="panel-head"><b>账号信息</b></div><div class="panel-body">' +
        '<div style="display:flex;gap:26px;flex-wrap:wrap;color:var(--text-2);font-size:13px">' +
        '<span>注册时间：' + UI.time(u.created_at, 'full') + '</span>' +
        '<span>最后登录：' + UI.time(u.last_login, 'full') + '</span>' +
        '<span>权限：' + (u.perms || []).length + ' 项</span></div></div></div>' +
        '<div class="panel"><div class="panel-head"><b>Windows 客户端</b></div><div class="panel-body" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
        '<span style="font-size:13px;color:var(--text-2)">在另一台电脑免密登录（授权码 60 秒有效，一次性）：</span>' +
        '<button class="btn primary sm" onclick="App.clientAuth()">生成授权码</button>' +
        '<a class="btn sm" href="/downloads/云聊-客户端.exe" download>下载 Windows 客户端</a></div></div>' +
        '<div class="panel"><div class="panel-head"><b>登录设备</b>' +
        '<span style="font-size:11px;color:var(--text-3)">同一账号只允许一台设备在线，新登录会自动下线旧设备</span></div>' +
        '<div class="panel-body" id="profile-devices">加载中…</div></div>';
      this.loadDevices();
    },
    /* 我的登录客户端/设备：当前会话 + 最近登录记录 */
    /* 客户端免密登录：生成一次性授权码 */
    async clientAuth() {
      const r = await API.post('/api/auth/client-code', {});
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      const code = r.data.code;
      let left = r.data.ttl || 60;
      UI.modal('Windows 客户端免密登录',
        '<p style="color:var(--text-2);font-size:13px;margin-bottom:10px">在另一台电脑打开「云聊」客户端，连接页选择「授权码登录」，输入以下授权码即可免密登录（无需密码）。授权码一次性使用，60 秒内有效。</p>' +
        '<div style="text-align:center;padding:18px 0"><span id="ca-code" style="font-size:42px;font-weight:bold;letter-spacing:8px;font-family:Consolas,monospace;color:var(--text-1)">' + code + '</span>' +
        '<p id="ca-tip" style="font-size:12px;color:var(--text-3);margin-top:10px">授权码 60 秒后失效</p></div>',
        '<button class="btn" onclick="UI.closeModal()">关闭</button>', 460);
      const tick = () => {
        const el = document.getElementById('ca-code');
        const tip = document.getElementById('ca-tip');
        if (!el) return;
        left--;
        if (left <= 0) { UI.closeModal(); UI.toast('授权码已过期，请重新获取', 'err'); return; }
        if (tip) tip.textContent = '授权码 ' + left + ' 秒后失效';
        setTimeout(tick, 1000);
      };
      setTimeout(tick, 1000);
    },
    async loadDevices() {
      const box = document.getElementById('profile-devices');
      if (!box) return;
      const r = await API.get('/api/auth/devices');
      if (!r.ok) { box.innerHTML = '<span style="color:var(--text-3);font-size:13px">' + UI.esc(r.msg) + '</span>'; return; }
      const cur = r.data.current;
      let html = cur
        ? '<div class="list-item"><div class="li-main"><b>当前设备：' + UI.esc(cur.client) + '</b>' +
          '<small>登录 IP ' + UI.esc(cur.ip || '—') + ' · 登录于 ' + UI.esc(cur.login_at || '') +
          (cur.last_seen ? ' · 最后活跃 ' + UI.esc(cur.last_seen) : '') + '</small></div></div>'
        : '<div class="empty" style="padding:8px 0">当前无活跃会话</div>';
      const hist = r.data.history || [];
      html += hist.length
        ? '<div style="margin-top:8px"><b style="font-size:13px">最近登录记录</b>' + hist.map(h =>
          '<div class="list-item" style="padding:7px 0"><div class="li-main"><b>' + UI.esc(h.ts) + '</b>' +
          '<small>' + UI.esc(h.detail || ('IP ' + UI.esc(h.ip))) + '</small></div></div>').join('') + '</div>'
        : '';
      box.innerHTML = html;
    },
    async saveProfile() {
      const name = UI.val('pf-name');
      if (!name) return UI.toast('昵称不能为空', 'err');
      const body = {
        display_name: name,                    // 昵称：本人可直接修改
        signature: UI.val('pf-sign'), dept: UI.val('pf-dept'),
        position: UI.val('pf-pos'), email: UI.val('pf-email'), phone: UI.val('pf-phone')
      };
      if (!this._profileApproval) body.title = UI.val('pf-title');   // 头衔：有权限时才能直接改
      const r = await API.put('/api/auth/profile', body);
      if (r.ok) { UI.toast(r.msg, 'ok'); API.user = r.data.user; this.renderBrand(); this.profileView(); }
      else UI.toast(r.msg, 'err');
    },
    /* 头衔修改申请弹窗（昵称可直接在个人资料里改） */
    applyProfileChange() {
      const u = API.user;
      UI.modal('申请修改头衔',
        '<p style="color:var(--text-2);font-size:13px;margin-bottom:10px">自定义头衔在聊天中对外展示，' +
        '修改需管理员审核通过后才会生效。昵称可直接在「个人资料」里修改，无需审核。</p>' +
        '<label class="field"><span>当前头衔</span><input value="' + UI.esc(u.title || '（无）') + '" readonly disabled></label>' +
        '<label class="field"><span>新头衔（≤20 字，留空表示申请清空）</span><input id="pr-title" maxlength="20" value="' + UI.esc(u.title || '') + '"></label>' +
        '<label class="field"><span>申请理由（供管理员判断）</span><input id="pr-reason" maxlength="200" placeholder="如：岗位调整后需要更新对外头衔"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="App.doApplyProfile()">提交申请</button>', 480);
    },
    async doApplyProfile() {
      const title = UI.val('pr-title');
      if (title === (API.user.title || '')) return UI.toast('新头衔与当前一致，无需申请', 'err');
      const r = await API.post('/api/auth/profile-request', {
        title: title, reason: UI.val('pr-reason')
      });
      UI.closeModal();
      UI.toast(r.ok ? r.msg : r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) { if (r.data.direct && r.data.user) { API.user = r.data.user; this.renderBrand(); } this.profileView(); }
    },
    changePwd() {
      UI.modal('修改密码',
        '<label class="field"><span>当前密码</span><input type="password" id="cp-old"></label>' +
        '<label class="field"><span>新密码</span><input type="password" id="cp-new"></label>' +
        '<label class="field"><span>确认新密码</span><input type="password" id="cp-new2"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="App.doChangePwd()">确定</button>', 420);
    },
    async doChangePwd() {
      const a = UI.val('cp-new'), b = UI.val('cp-new2');
      if (a !== b) return UI.toast('两次输入的新密码不一致', 'err');
      const r = await API.post('/api/auth/password', { old_password: UI.val('cp-old'), new_password: a });
      UI.closeModal();
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
    },
    forceChangePwd() {
      UI.modal('首次登录请修改密码',
        '<p style="color:var(--text-2)">出于安全要求，请先修改初始密码。</p>' +
        '<label class="field"><span>当前密码</span><input type="password" id="cp-old"></label>' +
        '<label class="field"><span>新密码</span><input type="password" id="cp-new"></label>' +
        '<label class="field"><span>确认新密码</span><input type="password" id="cp-new2"></label>',
        '<button class="btn primary" onclick="App.doChangePwd()">确定</button>', 420);
    },

    /* ---------- 全局搜索：聊天消息 / 文件 ---------- */
    async search(kw) {
      if (!kw) return;
      const [m, fl] = await Promise.all([
        API.get('/api/chat/search', { q: kw }),
        API.get('/api/files/list', { page_size: 200 })
      ]);
      const h = [];
      if ((m.ok ? m.data.list : []).length) {
        h.push('<div class="panel"><div class="panel-head"><b>聊天消息（' + m.data.list.length + '）</b></div><div>' +
          m.data.list.slice(0, 10).map(x => '<div class="list-item"><div class="li-main"><b>' + UI.esc(x.user.name) +
            '</b><small>' + UI.esc(x.room_name) + ' · ' + UI.time(x.created_at, 'full') + '</small>' +
            '<div style="margin-top:4px">' + UI.nl2br(x.content) + '</div></div>' +
            '<button class="btn sm" onclick="UI.closeModal();App.go(\'chat\');Chat.openRoom(' + x.room_id + ')">跳转</button></div>').join('') + '</div></div>');
      }
      const q = kw.toLowerCase();
      const files = ((fl.ok ? fl.data.list : []) || []).filter(f => String(f.name || '').toLowerCase().indexOf(q) >= 0);
      if (files.length) {
        h.push('<div class="panel"><div class="panel-head"><b>文件（' + files.length + '）</b>' +
          '<button class="link" onclick="UI.closeModal();App.go(\'files\')">前往文件中心</button></div><div>' +
          files.slice(0, 8).map(f => '<div class="list-item"><div class="li-main"><b>' + UI.esc(f.name) + '</b><small>' +
            UI.esc(f.size_str) + ' · ' + UI.esc(f.created_str) + '</small></div>' +
            '<a class="btn sm" href="' + f.url + '?token=' + encodeURIComponent(API.token) + '" target="_blank" rel="noopener">下载</a></div>').join('') + '</div></div>');
      }
      UI.modal('搜索：' + UI.esc(kw), h.join('') || '<div class="empty">没有找到相关内容</div>', '', 700);
    }
  };

  /* ---------- 启动 ---------- */
  document.addEventListener('DOMContentLoaded', async function () {
    document.querySelectorAll('.ltab').forEach(b => b.onclick = () => {
      document.querySelectorAll('.ltab').forEach(x => x.classList.toggle('active', x === b));
      document.getElementById('login-form').style.display = b.dataset.tab === 'login' ? '' : 'none';
      document.getElementById('register-form').style.display = b.dataset.tab === 'register' ? '' : 'none';
      document.getElementById('code-form').style.display = b.dataset.tab === 'code' ? '' : 'none';
    });
    document.querySelectorAll('.nav-item').forEach(a => a.onclick = () => App.go(a.dataset.view));
    if (API.token) {
      try { document.cookie = 'token=' + encodeURIComponent(API.token) + '; path=/; SameSite=Lax'; } catch (e) { }
      const ok = await Auth.loadMe();
      if (ok) { await App.start(); return; }
      localStorage.removeItem('lc_token');
    }
    document.getElementById('login-page').style.display = 'flex';
    /* 登录页：读取公开站点信息，应用管理员设置的站点名称与配色 */
    fetch('/api/site').then(r => r.json()).then(j => {
      if (!j || !j.ok || !j.data) return;
      const d = j.data;
      UI.applyAccent(d.theme_color);
      if (d.name) {
        document.title = d.name;
        const span = document.querySelector('#login-page .login-brand .brand span');
        if (span) span.textContent = d.name;
      }
      const sl = document.getElementById('login-slogan');
      if (sl && d.slogan) sl.textContent = d.slogan;
      const hint = document.getElementById('login-hint');
      if (hint && d.icp) hint.textContent = d.icp;
      /* 有桌面助手安装包时才显示下载入口（优先安装包，其次绿色版） */
      (async () => {
        const el = document.getElementById('dl-notify');
        if (!el) return;
        const a = el.querySelector('a');
        let url = '', ok = false;
        for (const name of ['云聊提醒-安装包.exe', '云聊提醒.exe']) {
          try {
            const r = await fetch('/downloads/' + name, { method: 'HEAD' });
            if (r.ok) { url = '/downloads/' + name; ok = true; break; }
          } catch (e) { }
        }
        if (ok) {
          if (a) a.setAttribute('href', url);
          el.style.display = '';
        }
      })();
    }).catch(() => { });
    const un = localStorage.getItem('lc_user');
    if (un) document.getElementById('login-username').value = un;
  });

  window.App = App;
})();
