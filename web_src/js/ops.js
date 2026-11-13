/* ============ 扩展功能：远程协助 / 权限管理 / 数据库 / 小游戏 ============ */
(function () {
  'use strict';

  /* ================================================================
   * RemoteOps —— 远程协助（管理员端）
   * 列表 / 发起请求 / 画面查看 / 键鼠控制，全部走 WebSocket
   * ================================================================ */
  const RemoteOps = {
    agents: [],       // 被控端列表
    session: null,    // 进行中的协助会话
    _frameUrl: null,
    _timer: null,
    _lastMove: 0,

    init() {
      WS.on('remote_agents', d => {
        this.agents = (d && d.list) || [];
        const body = document.getElementById('admin-body');
        if (body && document.querySelector('[data-atab="remote"].active')) this.render(body);
      });
      WS.on('remote_request_result', d => {
        if (!d) return;
        UI.toast(d.msg, d.ok ? 'ok' : 'err');
        if (d.ok) this._pending = true;
      });
      WS.on('remote_start', d => this.onStart(d));
      WS.on('remote_end', d => this.onEnd(d));
    },

    async render(box) {
      const [agents, sess] = await Promise.all([
        API.get('/api/admin/remote/agents'),
        API.get('/api/admin/remote/sessions')
      ]);
      if (!agents.ok) { box.innerHTML = '<div class="empty">' + UI.esc(agents.msg) + '</div>'; return; }
      this.agents = (agents.data.list) || [];
      this._sessions = (sess.ok ? sess.data.list : []) || [];
      const enabled = agents.data.enabled;
      const badge = s => ({ busy: '<span class="tag warn">协助中</span>',
        pending: '<span class="tag">待确认</span>', idle: '<span class="tag ok">待命</span>' }[s] || UI.esc(s));
      box.innerHTML =
        '<div class="toolbar"><b>远程协助</b>' +
        '<span style="color:var(--text-2);font-size:13px">' +
        (enabled ? '<span class="tag ok">已开启</span>' : '<span class="tag danger">未开启</span>') +
        ' 总开关在「系统设置 → 远程协助」中控制</span>' +
        '<span style="margin-left:auto;color:var(--text-3);font-size:12px">' +
        '员工需在电脑上运行被控端程序：<code>python tools/remote_agent.py --server http://' +
        location.host + '</code>（依赖 mss / pyautogui / pillow）</span></div>' +
        '<div class="panel"><div class="panel-head"><b>在线被控端（' + this.agents.length + '）</b></div>' +
        '<table><thead><tr><th>用户</th><th>部门</th><th>设备</th><th>IP</th><th>分辨率</th><th>状态</th><th>操作</th></tr></thead><tbody>' +
        (this.agents.length ? this.agents.map(a =>
          '<tr><td><b>' + UI.esc(a.user_name) + '</b><br><small style="color:var(--text-3)">@' + UI.esc(a.username) + '</small></td>' +
          '<td>' + UI.esc(a.dept || '—') + '</td><td>' + UI.esc(a.device) + '</td>' +
          '<td><small style="font-family:monospace">' + UI.esc(a.ip || '—') + '</small></td>' +
          '<td><small>' + UI.esc(a.screen) + '</small></td><td>' + badge(a.state) + '</td>' +
          '<td><div class="row-actions">' +
          (a.state === 'idle'
            ? '<button class="btn sm primary" onclick="RemoteOps.request(' + a.user_id + ')">请求协助</button>'
            : '<button class="btn sm danger" onclick="RemoteOps.endByAgent(' + a.user_id + ')">结束会话</button>') +
          '</div></td></tr>').join('') :
          '<tr><td colspan="7"><div class="empty">暂无在线被控端。请员工先运行被控端程序并登录。</div></td></tr>') +
        '</tbody></table></div>' +
        '<div class="panel"><div class="panel-head"><b>进行中的会话</b></div><div>' +
        (this._sessions.length ? this._sessions.map(s =>
          '<div class="list-item"><div class="li-main"><b>' + UI.esc(s.admin_name) + ' → ' +
          UI.esc(s.agent_name) + '</b><small>' + UI.esc(s.status === 'active' ? '协助中' : '待确认') +
          ' · 上限 ' + s.max_minutes + ' 分钟 · 输入 ' + s.input_count + ' 次' +
          (s.control ? ' · 可控制' : ' · 仅查看') + '</small></div>' +
          '<button class="btn sm danger" onclick="RemoteOps.endSession(\'' + UI.esc(s.id) + '\')">结束</button></div>').join('') :
          '<div class="empty">当前无进行中的协助会话</div>') + '</div></div>';
    },

    request(userId) {
      WS.send('remote_request', { user_id: userId });
      UI.toast('已发送协助请求，等待对方在本机确认…', 'ok');
    },

    async endByAgent(userId) {
      const s = (this._sessions || []).find(x => x.agent_user_id === userId && x.status === 'active');
      if (s) return this.endSession(s.id);
      UI.toast('该用户的会话已结束或不存在', 'err');
    },

    endSession(sid) {
      UI.confirm('确定结束该协助会话？', async () => {
        const r = await API.post('/api/admin/remote/session/' + encodeURIComponent(sid) + '/end', {});
        UI.toast(r.msg || '已结束', r.ok ? 'ok' : 'err');
        if (r.ok) this.render(document.getElementById('admin-body'));
      }, true);
    },

    /* ---------- 会话查看与控制 ---------- */
    onStart(d) {
      if (!d || d.role !== 'admin') return;
      this.session = {
        id: d.session_id, peer: d.peer_name, dept: d.agent_dept || '',
        control: !!d.control, screen_w: d.screen_w || 0, screen_h: d.screen_h || 0,
        max_minutes: d.max_minutes || 60, expires_at: d.expires_at || 0, started: Date.now()
      };
      this._openViewer();
    },

    onEnd(d) {
      if (!this.session) return;
      const reason = (d && d.reason) || '会话已结束';
      const dur = (d && d.duration) ? Math.round(d.duration / 60) : 0;
      this._closeViewer();
      UI.toast('协助已结束：' + reason + (dur ? '（时长 ' + dur + ' 分钟）' : ''), 'err');
      const box = document.getElementById('admin-body');
      if (document.querySelector('[data-atab="remote"].active')) this.render(box);
    },

    onFrame(blob) {
      if (!this.session) return;
      const img = document.getElementById('rv-img');
      if (!img) return;
      const con = document.getElementById('rv-connecting');
      if (con) con.style.display = 'none';
      if (this._frameUrl) URL.revokeObjectURL(this._frameUrl);
      this._frameUrl = URL.createObjectURL(blob);
      img.src = this._frameUrl;
      img.classList.add('fresh');
      setTimeout(() => img.classList.remove('fresh'), 150);
    },

    _openViewer() {
      const s = this.session;
      UI.modal('远程协助 · ' + UI.esc(s.peer),
        '<div style="display:flex;gap:10px;align-items:center;margin-bottom:8px;flex-wrap:wrap">' +
        '<span class="tag ' + (s.control ? 'warn' : 'ok') + '">' + (s.control ? '可远程操作键鼠' : '仅查看画面') + '</span>' +
        '<span style="color:var(--text-2);font-size:12px">' + UI.esc(s.dept || '') + ' · 上限 ' + s.max_minutes + ' 分钟</span>' +
        '<span style="color:var(--text-2);font-size:12px">已持续 <b id="rv-timer">0:00</b></span>' +
        '<span style="margin-left:auto;color:var(--text-3);font-size:12px">被控端可随时一键断开</span></div>' +
        '<div style="position:relative;background:#000;border-radius:8px;overflow:hidden;border:1px solid var(--border)">' +
        '<img id="rv-img" alt="远程画面" style="width:100%;display:block;cursor:crosshair">' +
        '<div id="rv-connecting" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
        'background:rgba(0,0,0,.6);color:#ccc;font-size:13px">等待画面…</div></div>' +
        '<div class="grid2" style="margin-top:10px">' +
        '<label class="field"><span>输入文字（支持中文，自动粘贴发送）</span><input id="rv-text" placeholder="输入后点发送"></label>' +
        '<label class="field"><span>按键（如 Enter / Esc / F5 / ctrl+c，+ 组合）</span>' +
        '<div style="display:flex;gap:6px"><input id="rv-key" placeholder="如 Enter"><button class="btn sm" onclick="RemoteOps.sendKey()">按键</button></div></label></div>' +
        '<p style="font-size:12px;color:var(--text-3);margin-top:6px">' +
        '操作：在画面上点击 = 单击；鼠标移动实时同步；滚轮 = 上下滚动；右键 = 右键单击。' +
        (s.control ? '' : '（您仅有查看权限，操作按钮已禁用）') + '</p>',
        '<button class="btn" onclick="RemoteOps.sendText()">发送文字</button>' +
        '<button class="btn" onclick="RemoteOps.esc()">发送 Esc</button>' +
        '<button class="btn" onclick="RemoteOps.enter()">发送 Enter</button>' +
        '<button class="btn danger" onclick="RemoteOps.end()">结束协助</button>', 900);
      document.getElementById('rv-connecting');
      if (!s.control) {
        const te = document.getElementById('rv-text');
        if (te) te.disabled = true;
        const ke = document.getElementById('rv-key');
        if (ke) ke.disabled = true;
      }
      const img = document.getElementById('rv-img');
      const bind = () => {
        /* 鼠标控制（坐标 0~1 归一化，服务端换算真实像素） */
        let last = 0;
        img.addEventListener('mousemove', e => {
          const now = Date.now();
          if (now - last < 80) return;          // 节流 ~12Hz
          last = now;
          RemoteOps.sendInput({ k: 'move', x: e.offsetX / img.clientWidth, y: e.offsetY / img.clientHeight });
        });
        img.addEventListener('click', e => {
          RemoteOps.sendInput({ k: 'move', x: e.offsetX / img.clientWidth, y: e.offsetY / img.clientHeight });
          RemoteOps.sendInput({ k: 'click', x: e.offsetX / img.clientWidth, y: e.offsetY / img.clientHeight, button: 'left', clicks: 1 });
        });
        img.addEventListener('contextmenu', e => {
          e.preventDefault();
          RemoteOps.sendInput({ k: 'move', x: e.offsetX / img.clientWidth, y: e.offsetY / img.clientHeight });
          RemoteOps.sendInput({ k: 'click', x: e.offsetX / img.clientWidth, y: e.offsetY / img.clientHeight, button: 'right', clicks: 1 });
        });
        img.addEventListener('wheel', e => {
          e.preventDefault();
          RemoteOps.sendInput({ k: 'scroll', x: e.offsetX / img.clientWidth, y: e.offsetY / img.clientHeight, dy: e.deltaY > 0 ? -3 : 3 });
        }, { passive: false });
        document.getElementById('rv-key').addEventListener('keydown', e => {
          if (e.key === 'Enter') { e.preventDefault(); RemoteOps.sendKey(); }
        });
      };
      if (img.complete) bind(); else img.onload = bind;
      clearInterval(this._timer);
      this._timer = setInterval(() => {
        const el = document.getElementById('rv-timer');
        if (el) {
          const sec = Math.floor((Date.now() - this.session.started) / 1000);
          el.textContent = Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2);
        }
      }, 1000);
    },

    _closeViewer() {
      clearInterval(this._timer);
      this._timer = null;
      if (this._frameUrl) { URL.revokeObjectURL(this._frameUrl); this._frameUrl = null; }
      this.session = null;
      UI.closeModal();
    },

    sendInput(d) {
      if (!this.session) return;
      if (!this.session.control) return;
      d.session_id = this.session.id;
      WS.send('remote_input', d);
    },
    sendText() {
      const v = document.getElementById('rv-text').value;
      if (!v) return;
      this.sendInput({ k: 'text', text: v });
      document.getElementById('rv-text').value = '';
    },
    sendKey() {
      const v = (document.getElementById('rv-key').value || '').trim();
      if (!v) return;
      this.sendInput({ k: 'key', key: v });
      document.getElementById('rv-key').value = '';
    },
    esc() { this.sendInput({ k: 'key', key: 'esc' }); },
    enter() { this.sendInput({ k: 'key', key: 'enter' }); },
    end() {
      WS.send('remote_end', { reason: '管理员结束协助' });
      UI.toast('已发送结束指令…', 'ok');
    }
  };

  /* ================================================================
   * PermOps —— 权限管理（角色矩阵 + 单用户额外权限）
   * ================================================================ */
  const PermOps = {
    _groups: [], _roles: [], _override: {},
    _curUser: null,

    async render(box) {
      const r = await API.get('/api/admin/permissions');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      this._groups = r.data.groups || [];
      this._roles = r.data.roles || [];
      this._override = r.data.override || {};
      const roles = this._roles;
      const groupRows = g =>
        '<tr class="perm-group-row"><td class="perm-group-name" colspan="' + (roles.length + 1) + '">' +
        UI.esc(g.name) + '</td></tr>' +
        g.perms.map(p => '<tr><td class="perm-label" title="' + UI.esc(p.key) + '">' +
          UI.esc(p.name) + '</td>' +
          roles.map(role => {
            const has = role.fixed || role.perms.indexOf(p.key) >= 0;
            const checked = has ? ' checked' : '';
            const disabled = role.fixed ? ' disabled' : '';
            return '<td style="text-align:center"><input type="checkbox" ' + checked + disabled +
              ' data-perm="' + UI.esc(p.key) + '" data-role="' + UI.esc(role.key) + '"' +
              ' onchange="PermOps._onRolePerm(this)"></td>';
          }).join('') + '</tr>').join('');
      box.innerHTML =
        '<div class="toolbar"><b>角色权限矩阵</b>' +
        '<span style="color:var(--text-2);font-size:13px">勾选后点击保存，立即生效；' +
        '「总权限」勾选会自动拥有其全部子权限，超级管理员恒定全部权限。</span>' +
        '<button class="btn primary" style="margin-left:auto" onclick="PermOps.saveRoles()">保存角色权限</button></div>' +
        '<div class="panel"><div style="overflow:auto"><table class="perm-matrix"><thead><tr>' +
        '<th class="perm-label">权限 \ 角色</th>' + roles.map(x => '<th>' + UI.esc(x.name) + '</th>').join('') +
        '</tr></thead><tbody>' + this._groups.map(groupRows).join('') + '</tbody></table></div></div>' +
        '<div class="panel"><div class="panel-head"><b>单用户权限微调（在角色权限之上追加）</b>' +
        '<span style="color:var(--text-3);font-size:12px">适用于给个别用户单独开放/收窄某项权限</span></div>' +
        '<div class="panel-body">' +
        '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">' +
        '<input id="po-user" placeholder="输入用户名 / 姓名，回车搜索" style="flex:1;min-width:220px;padding:8px 10px;border:1px solid var(--border);border-radius:8px">' +
        '<button class="btn" onclick="PermOps.searchUser()">搜索</button>' +
        (this._curUser ? '<span class="tag ok">' + UI.esc(this._curUser.display_name) + '（@' + UI.esc(this._curUser.username) + '）</span>' : '') + '</div>' +
        '<div id="po-result"></div></div></div>';
      if (this._curUser) this._userPanel(document.getElementById('po-result'), true);
    },

    _onRolePerm(cb) {
      /* 勾选总权限时联动其子权限 */
      const perm = cb.dataset.perm;
      if (perm.indexOf('.manage') > 0 || perm.indexOf('.moderate') > 0) {
        const role = cb.dataset.role;
        document.querySelectorAll('[data-role="' + role + '"][data-perm^="' + perm.replace('.', '\\.') + '."]')
          .forEach(x => { x.checked = cb.checked; });
      }
    },

    async saveRoles() {
      const overrides = {};
      this._roles.forEach(role => {
        if (role.fixed) return;
        const checked = Array.from(document.querySelectorAll('[data-role="' + role.key + '"]:checked'))
          .map(c => c.dataset.perm);
        overrides[role.key] = checked;
      });
      const r = await API.put('/api/admin/permissions/roles', { overrides: overrides });
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) this.render(document.getElementById('admin-body'));
    },

    async searchUser() {
      const kw = (document.getElementById('po-user').value || '').trim();
      if (!kw) return UI.toast('请输入用户名或姓名', 'err');
      const r = await API.get('/api/admin/users', { q: kw, page: 1, page_size: 20 });
      const list = (r.ok ? r.data.list : []) || [];
      const el = document.getElementById('po-result');
      if (!list.length) { el.innerHTML = '<div class="empty">未找到用户</div>'; return; }
      el.innerHTML = list.map(u =>
        '<div class="list-item"><div class="li-main"><b>' + UI.esc(u.display_name) + '</b><small>@' +
        UI.esc(u.username) + ' · ' + UI.esc(u.role_name) + ' · ' + UI.esc(u.dept || '') + '</small></div>' +
        '<button class="btn sm primary" onclick="PermOps.pickUser(' + u.id + ')">选择</button></div>').join('');
    },

    async pickUser(id) {
      const r = await API.get('/api/admin/users', { page: 1, page_size: 500 });
      const u = ((r.ok ? r.data.list : []) || []).find(x => x.id === id);
      if (!u) return UI.toast('用户不存在', 'err');
      this._curUser = u;
      const box = document.getElementById('admin-body');
      const old = document.getElementById('po-user');
      if (old) old.value = u.username;
      this.render(box);
    },

    async _userPanel(el, keep) {
      const u = this._curUser;
      if (!u) { el.innerHTML = '<div class="empty">请先搜索并选择用户</div>'; return; }
      if (!keep) el = document.getElementById('po-result');
      const r = await API.get('/api/admin/permissions');
      const groups = r.ok ? (r.data.groups || []) : [];
      const extra = (u.extra_perms || '').split(',').map(s => s.trim()).filter(Boolean);
      el.innerHTML =
        '<div style="color:var(--text-2);font-size:13px;margin-bottom:8px">角色「' + UI.esc(u.role_name) +
        '」自带权限 + 下方额外勾选的权限 = 该用户实际权限（超级管理员除外）。</div>' +
        groups.map(g =>
          '<div style="margin-bottom:6px"><b style="font-size:13px;color:var(--text-3)">' + UI.esc(g.name) + '</b> ' +
          g.perms.map(p => {
            const on = extra.indexOf(p.key) >= 0;
            return '<label class="perm-chip' + (on ? ' on' : '') + '"><input type="checkbox" ' +
              (on ? 'checked' : '') + ' data-up="' + UI.esc(p.key) + '" onchange="PermOps._chip(this)"> ' +
              UI.esc(p.name) + '</label>';
          }).join('') + '</div>').join('') +
        '<div style="margin-top:10px"><button class="btn primary" onclick="PermOps.saveUser()">保存该用户权限</button></div>';
    },
    _chip(cb) {
      cb.parentElement.classList.toggle('on', cb.checked);
    },
    async saveUser() {
      if (!this._curUser) return;
      const perms = Array.from(document.querySelectorAll('[data-up]:checked')).map(c => c.dataset.up);
      const r = await API.put('/api/admin/users/' + this._curUser.id + '/perms', { perms: perms });
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
    }
  };

  /* ================================================================
   * DbOps —— 数据库管理（表浏览 / 行修改 / 只读 SQL）
   * ================================================================ */
  const DbOps = {
    _tables: [], _cur: null, _page: 1, _kw: '', _pageSize: 50,

    async render(box) {
      const r = await API.get('/api/admin/db/tables');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      this._tables = (r.data.tables || []).filter(t => t.count >= 0);
      const total = this._tables.reduce((a, t) => a + t.count, 0);
      box.innerHTML =
        '<div class="db-head">' +
          '<div><h2>数据库浏览</h2><p>共 ' + this._tables.length + ' 张表 · ' + total + ' 条记录；敏感认证列（密码散列 / 令牌 / 盐）不可查看或修改</p></div>' +
          '<div class="db-head-actions"><input id="db-search" class="db-search" placeholder="筛选表名…" oninput="DbOps.filterTables(this.value)">' +
          '<button class="btn" onclick="Admin.tab(\'db\')">刷新</button></div>' +
        '</div>' +
        '<div class="db-layout">' +
          '<aside class="db-side"><div class="db-side-head"><b>数据表</b><span>' + this._tables.length + '</span></div>' +
            '<div class="db-table-list">' +
            this._tables.map(t => '<button class="db-table-item' + (this._cur === t.name ? ' active' : '') + '" type="button" ' +
              'data-name="' + UI.esc(t.name) + '" onclick="DbOps.open(\'' + UI.esc(t.name) + '\')">' +
              '<span class="db-tname">' + UI.esc(t.name) + '</span><span class="db-tcount">' + t.count + '</span></button>').join('') +
            '</div></aside>' +
          '<div class="db-main">' +
            '<div id="db-main-body"><div class="panel"><div class="empty">选择左侧数据表查看记录，或在下方执行只读查询。</div></div></div>' +
            '<div class="panel db-sqlpanel"><div class="panel-head"><b>SQL 只读查询（SELECT）</b>' +
            '<span style="color:var(--text-3);font-size:12px">仅允许 SELECT · 最多返回 500 行</span></div>' +
            '<div class="db-sqlbar"><input id="db-sql" placeholder="SELECT * FROM messages WHERE room_id=1 ORDER BY id DESC LIMIT 50" ' +
            'onkeydown="if(event.key===\'Enter\')DbOps.runSql()"><button class="btn primary" onclick="DbOps.runSql()">执行</button></div>' +
            '<div id="db-sql-result"></div></div>' +
          '</div>' +
        '</div>';
      if (this._cur) this.open(this._cur);
    },

    filterTables(kw) {
      const q = (kw || '').toLowerCase();
      document.querySelectorAll('.db-table-item').forEach(el => {
        el.style.display = el.dataset.name.toLowerCase().indexOf(q) >= 0 ? '' : 'none';
      });
    },

    async open(name) {
      this._cur = name; this._page = 1; this._kw = '';
      const box = document.getElementById('admin-body');
      if (box && !document.querySelector('.db-layout')) { this.render(box); return; }
      document.querySelectorAll('.db-table-item').forEach(el => el.classList.toggle('active', el.dataset.name === name));
      await this.load();
    },

    search(kw) { this._kw = (kw || '').trim(); this._page = 1; this.load(); },

    goPage(p) { this._page = p; this.load(); },

    async load() {
      const name = this._cur;
      if (!name) return;
      const body = document.getElementById('db-main-body');
      if (body) body.innerHTML = '<div class="panel"><div class="empty">加载中…</div></div>';
      const r = await API.get('/api/admin/db/table/' + encodeURIComponent(name),
        { page: this._page, page_size: this._pageSize, kw: this._kw });
      if (!r.ok) { if (body) body.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      this._schema = d.columns;
      this._pk = d.pk;
      this._lastRows = d.rows;
      const cols = d.columns;
      const masked = { password_hash: 1, token: 1, salt: 1 };
      const rowHtml = (row, idx) => '<tr>' + cols.map(c => {
        const v = row[c.name];
        const text = masked[c.name] ? '••••••（受保护）' : (v === null || v === undefined ? '—' : String(v));
        return '<td class="db-cell' + (v === null || v === undefined ? ' null' : '') +
          '" title="' + UI.esc(text) + '">' + UI.esc(text) + '</td>';
      }).join('') + '<td class="db-ops"><div class="row-actions">' +
        '<button class="btn sm" onclick="DbOps.edit(' + idx + ')">编辑</button>' +
        '<button class="btn sm danger" onclick="DbOps.del(' + idx + ')">删除</button></div></td></tr>';
      body.innerHTML =
        '<div class="panel db-tablepanel">' +
        '<div class="panel-head"><div><b>表 ' + UI.esc(name) + '</b>' +
        '<p class="db-meta">主键 ' + UI.esc(d.pk.join(',')) + ' · ' +
        (this._kw ? '命中 ' + d.total + ' 行 · 关键词「' + UI.esc(this._kw) + '」' : '共 ' + d.total + ' 行') +
        ' · 敏感认证列不可修改</p></div>' +
        '<div class="db-tabletools"><input id="db-kw" class="db-search" placeholder="按主键搜索…" value="' + UI.esc(this._kw) + '" ' +
        'onkeydown="if(event.key===\'Enter\')DbOps.search(this.value)">' +
        '<button class="btn sm" onclick="DbOps.search(document.getElementById(\'db-kw\').value)">搜索</button>' +
        (this._kw ? '<button class="btn sm" onclick="document.getElementById(\'db-kw\').value=\'\';DbOps.search(\'\')">清除</button>' : '') +
        '</div></div>' +
        '<div class="tablewrap db-tablewrap"><table class="dbtable"><thead><tr>' +
        cols.map(c => '<th>' + UI.esc(c.name) + (c.pk ? ' <em>PK</em>' : '') +
          '<br><small>' + UI.esc(c.type || '') + '</small></th>').join('') +
        '<th class="db-ops">操作</th></tr></thead><tbody>' +
        (d.rows.length ? d.rows.map(rowHtml).join('') :
          '<tr><td colspan="' + (cols.length + 1) + '"><div class="empty">没有记录</div></td></tr>') +
        '</tbody></table></div>' +
        UI.pager(d.total, d.page, this._pageSize, 'DbOps.goPage') + '</div>';
    },

    async runSql() {
      const sql = (document.getElementById('db-sql').value || '').trim();
      if (!sql) return;
      const r = await API.post('/api/admin/db/query', { sql: sql });
      const el = document.getElementById('db-sql-result');
      if (!r.ok) { el.innerHTML = '<div class="errbox">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      el.innerHTML = '<div style="color:var(--text-2);font-size:12px;padding:6px 12px">返回 ' + d.count +
        ' 行' + (d.truncated ? '（已达上限截断）' : '') + '</div>' +
        (d.columns.length ? '<div style="overflow:auto"><table><thead><tr>' +
          d.columns.map(c => '<th>' + UI.esc(c) + '</th>').join('') + '</tr></thead><tbody>' +
          d.rows.map(row => '<tr>' + d.columns.map(c =>
            '<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
            UI.esc(row[c] === null ? '<null>' : String(row[c])) + '</td>').join('') + '</tr>').join('') +
          '</tbody></table></div>' : '<div class="empty">无结果</div>');
    },

    edit(idx) {
      const rows = this._lastRows;
      const name = this._cur;
      if (!rows || !rows[idx] || !name) return;
      const row = rows[idx];
      const cols = this._schema.filter(c => c.name !== 'password_hash' && c.name !== 'token' && c.name !== 'salt');
      UI.modal('编辑记录 · ' + UI.esc(name),
        '<div class="grid2">' + cols.map(c =>
          '<label class="field"><span>' + UI.esc(c.name) + ' <small style="color:var(--text-3)">' +
          UI.esc(c.type || '') + '</small></span><input id="dbe-' + UI.esc(c.name) + '" value="' +
          UI.esc(row[c.name] === null ? '' : String(row[c.name])) + '"></label>').join('') + '</div>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn primary" onclick="DbOps.saveEdit()">保存</button>', 760);
      this._lastRow = row;
    },
    async saveEdit() {
      const row = this._lastRow;
      const name = this._cur;
      if (!row || !name) return;
      const values = {};
      this._schema.filter(c => c.name !== 'password_hash' && c.name !== 'token' && c.name !== 'salt')
        .forEach(c => { values[c.name] = document.getElementById('dbe-' + c.name).value; });
      const pk = {};
      this._pk.forEach(c => pk[c] = row[c]);
      const r = await API.put('/api/admin/db/row/' + encodeURIComponent(name), { pk: pk, values: values });
      UI.closeModal();
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) this.load();
    },
    del(idx) {
      const rows = this._lastRows;
      const name = this._cur;
      if (!rows || !rows[idx] || !name) return;
      const pk = {};
      this._pk.forEach(c => pk[c] = rows[idx][c]);
      UI.confirm('确定删除该记录？此操作不可撤销。', async () => {
        const r = await API.del('/api/admin/db/row/' + encodeURIComponent(name), { pk: pk });
        UI.toast(r.msg, r.ok ? 'ok' : 'err');
        if (r.ok) this.load();
      }, true);
    }
  };

  /* ================================================================
   * GameOps —— 小游戏入口（管理员开启后显示）
   * ================================================================ */
  const GameOps = {
    catalog: [], _cur: null,

    async init() {
      window.addEventListener('message', e => {
        if (e.data && e.data.source === 'lc-game' && e.data.event === 'close') {
          this._cur = null;
          App.go('chat');
        }
      });
      const r = await API.get('/api/games/catalog');
      if (r.ok) this.catalog = r.data.games || [];
      const nav = document.getElementById('nav-games');
      const enabled = this.catalog.filter(g => g.enabled).length > 0;
      const showNav = this.catalog.some(g => g.show_nav);
      if (nav) nav.style.display = (enabled && showNav) ? '' : 'none';
    },

    async render(box) {
      if (!this.catalog.length) {
        const r = await API.get('/api/games/catalog');
        if (r.ok) this.catalog = r.data.games || [];
      }
      const enabled = this.catalog.filter(g => g.enabled);
      if (!enabled.length) {
        const anyGlobal = this.catalog.some(g => g.global_on);
        box.innerHTML = anyGlobal
          ? '<div class="empty">小游戏尚未对你开放。如需游玩，请联系管理员在「管理后台 → 用户管理 → 编辑用户 → 小游戏授权」中为你开启。</div>'
          : '<div class="empty">小游戏未开启。请管理员在「管理后台 → 系统设置 → 小游戏」中手动开启，' +
            '并勾选「在侧边栏显示小游戏入口」后刷新页面。</div>';
        return;
      }
      if (this._cur) return this._loadGame(box, this._cur);
      box.innerHTML =
        '<div class="toolbar"><b>小游戏</b><span style="color:var(--text-2);font-size:13px">' +
        '进入后若游戏已开启则直接可玩；联机游戏需两人分别进入同一房间号。</span></div>' +
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px">' +
        enabled.map(g =>
          '<div class="panel" style="cursor:pointer" onclick="GameOps.open(\'' + g.key + '\')">' +
          '<div class="panel-head"><b>' + UI.esc(g.name) + '</b>' +
          (g.online ? '<span class="tag ok">可联机</span>' : '<span class="tag gray">单机</span>') + '</div>' +
          '<div class="panel-body" style="color:var(--text-2);font-size:13px">' +
          (g.key === 'fireboy' ? '经典双人协作闯关（Flash 原版）：火娃与冰娃分别拾取红/蓝宝石，到达各自出口过关；支持联机，两人各控一个角色实时协同。'
            : '中国象棋：支持联机对战（房主执红、加入者执黑，走子实时同步），也可与内置 AI 对战或本地双人对弈。') +
          '<div style="margin-top:10px"><button class="btn primary">进入游戏</button>' +
          '</div></div></div>').join('') +
        '</div>';
    },

    open(key) {
      this._cur = key;
      const box = document.getElementById('games-body');
      /* 冰火人只保留 Flash 原版（自带联机） */
      this._loadGame(box, key === 'fireboy' ? 'fireboy-flash' : key);
    },

    _loadGame(box, key) {
      box.innerHTML = '<div class="toolbar"><button class="btn" onclick="GameOps.back()">← 返回游戏列表</button>' +
        '<b style="margin-left:8px">' + UI.esc(this._find(key).name) + '</b>' +
        '<span style="margin-left:auto;color:var(--text-3);font-size:12px">关闭窗口后回到聊天室</span></div>' +
        '<iframe src="/games/' + encodeURIComponent(key) + '.html" style="width:100%;height:calc(100vh - 130px);border:1px solid var(--border);border-radius:10px;background:#141821"></iframe>';
    },
    back() { this._cur = null; this.render(document.getElementById('games-body')); },
    _find(key) {
      if (key === 'fireboy-flash') return { name: '森林冰火人 · 原版(Flash)' };
      return this.catalog.find(g => g.key === key) || { name: key };
    }
  };

  /* 初始化：注册远程协助 WS 事件 */
  if (window.WS) RemoteOps.init();

  window.RemoteOps = RemoteOps;
  window.PermOps = PermOps;
  window.DbOps = DbOps;
  window.GameOps = GameOps;
})();
