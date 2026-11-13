/* ============ 管理后台 ============ */
(function () {
  'use strict';
  const Admin = {
    cur: 'dash', page: 1, filters: {},

    async render() {
      /* 「资料申请」审核需要 user.manage，讲师等角色不展示该入口 */
      const canReview = (API.user.perms || []).indexOf('user.manage') >= 0;
      document.querySelectorAll('[data-atab="proreq"]').forEach(e => e.style.display = canReview ? '' : 'none');
      if (!canReview && this.cur === 'proreq') this.cur = 'dash';
      document.querySelectorAll('[data-atab]').forEach(b => b.classList.toggle('active', b.dataset.atab === this.cur));
      const box = document.getElementById('admin-body');
      box.innerHTML = '<div class="empty">加载中…</div>';
      const fn = { dash: this.dash, users: this.users, proreq: this.proreq, rooms: this.rooms,
        msg: this.messages, announce: this.announce, sites: this.sites, logs: this.logs, ipbl: this.ipbl, settings: this.settings,
        perms: () => PermOps.render(box), remote: () => RemoteOps.render(box),
        db: () => DbOps.render(box) }[this.cur];
      if (fn) await fn.call(this);
    },
    tab(t) { this.cur = t; this.page = 1; this.filters = {}; this.render(); },

    /* ---------- 仪表盘 ---------- */
    async dash() {
      const r = await API.get('/api/admin/dashboard');
      const box = document.getElementById('admin-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      const stat = (v, l, c) => '<div class="stat"><b style="color:' + (c || '') + '">' + v + '</b><span>' + l + '</span></div>';
      const maxMsg = Math.max(1, ...d.trend.map(t => t.msg));
      box.innerHTML =
        '<div class="stat-grid">' + stat(d.users, '注册用户') + stat(d.online, '当前在线', 'var(--ok)') +
        stat(d.users_pending, '待审核', d.users_pending ? 'var(--warn)' : '') + stat(d.login_today, '今日登录') +
        stat(d.messages, '消息总数') + stat(d.messages_today, '今日消息') + stat(d.courses, '课程数') +
        stat(d.materials, '资料数') + stat(d.exams, '考试人次') + stat(d.files_size_str, '附件占用') + '</div>' +
        '<div style="display:grid;grid-template-columns:1.4fr 1fr;gap:14px">' +
        '<div class="panel"><div class="panel-head"><b>近 7 天活跃趋势</b></div><div class="panel-body">' +
        d.trend.map(t => '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">' +
          '<span style="width:38px;font-size:12px;color:var(--text-3)">' + t.day + '</span>' +
          '<div style="flex:1;height:14px;background:var(--border);border-radius:8px;overflow:hidden">' +
          '<i style="display:block;height:100%;width:' + Math.round(t.msg * 100 / maxMsg) + '%;background:var(--primary)"></i></div>' +
          '<span style="width:90px;font-size:12px;color:var(--text-2)">消息 ' + t.msg + ' / 新增 ' + t.new_user + '</span></div>').join('') +
        '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>部门人数</b></div><div class="panel-body">' +
        (d.dept_stats || []).map(x => '<div style="display:flex;justify-content:space-between;padding:4px 0">' +
          '<span>' + UI.esc(x.name || '未分配') + '</span><b>' + x.c + '</b></div>').join('') + '</div></div>' +
        '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">' +
        '<div class="panel"><div class="panel-head"><b>活跃房间 TOP</b></div><div>' +
        (d.top_rooms || []).map(x => '<div class="list-item"><div class="li-main"><b>' + UI.esc(x.name) + '</b></div>' +
          '<span class="tag">' + (x.c || 0) + ' 条</span></div>').join('') + '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>活跃用户 TOP</b></div><div>' +
        (d.top_users || []).map(x => '<div class="list-item">' + UI.avatar(x.display_name, '#2f6fed') +
          '<div class="li-main"><b>' + UI.esc(x.display_name) + '</b><small>' + UI.esc(x.dept || '') + '</small></div>' +
          '<span class="tag">' + x.c + ' 条</span></div>').join('') + '</div></div></div>' +
        '<div class="panel"><div class="panel-head"><b>最近操作日志</b>' +
        '<button class="btn sm" onclick="Admin.tab(\'logs\')">查看全部</button></div><div>' +
        (d.recent_logs || []).map(l => '<div class="list-item"><div class="li-main"><b>' + UI.esc(l.action) + '</b>' +
          '<small>' + UI.esc(l.actor || '系统') + ' · ' + UI.esc(l.target || '') + ' · ' + UI.esc(l.detail || '') + '</small></div>' +
          '<small style="color:var(--text-3)">' + UI.esc(l.time) + '</small></div>').join('') + '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>服务信息</b></div><div class="panel-body">' +
        '<div style="display:flex;gap:26px;flex-wrap:wrap;color:var(--text-2);font-size:13px">' +
        '<span>版本：' + UI.esc(d.server.version) + '</span><span>启动时间：' + UI.esc(d.server.started_str) + '</span>' +
        '<span>已运行：' + d.server.uptime_hours + ' 小时</span><span>数据库：' + UI.esc(d.server.db_size) + '</span>' +
        '<span style="word-break:break-all">数据目录：' + UI.esc(d.server.data_dir) + '</span></div></div></div>';
    },

    /* ---------- 用户管理 ---------- */
    async users() {
      const f = this.filters;
      const r = await API.get('/api/admin/users', Object.assign({ page: this.page, page_size: 20 }, f));
      const box = document.getElementById('admin-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      this._rows = r.data.list || [];
      const rows = (r.data.list || []).map(u =>
        '<tr data-uid="' + u.id + '"><td>' + this._ck('users', u.id) + '</td>' +
        '<td><div class="usercell">' + UI.avatar(u.display_name, u.color, 32, u.avatar) +
        '<div><b>' + UI.esc(u.display_name) + '</b><small>@' + UI.esc(u.username) + '</small></div></div></td>' +
        '<td>' + UI.esc(u.dept || '—') + '<br><small style="color:var(--text-3)">' + UI.esc(u.position || '') + '</small></td>' +
        '<td>' + (u.title ? '<span class="title-tag">' + UI.esc(u.title) + '</span>' : '<span style="color:var(--text-3)">—</span>') + '</td>' +
        '<td><span class="tag ' + (u.role === 'superadmin' ? 'danger' : u.role === 'student' ? 'gray' : '') + '">' +
        UI.esc(u.role_name) + '</span></td>' +
        '<td>' + (u.online ? '<span class="tag ok">在线</span>' : '<span class="tag gray">离线</span>') +
        (u.status === 'disabled' ? ' <span class="tag danger">停用</span>' : '') +
        (u.status === 'blacklist' ? ' <span class="tag danger">黑名单</span>' : '') +
        (u.locked ? ' <span class="tag warn">锁定</span>' : '') + '</td>' +
        '<td><small>注册 ' + UI.esc(u.created_str || '—') + '<br>登录 ' + UI.esc(u.last_login_str || '未登录') + '</small></td>' +
        '<td><div class="row-actions">' +
        '<button class="btn sm" onclick="Admin.editUser(' + u.id + ')">编辑</button>' +
        '<button class="btn sm" onclick="Admin.userPerms(' + u.id + ')">权限</button>' +
        '<button class="btn sm" onclick="Admin.userActions(' + u.id + ')">更多</button></div></td></tr>').join('');
      box.innerHTML =
        '<div class="toolbar"><input id="u-kw" placeholder="搜索用户名/昵称/邮箱" value="' + UI.esc(f.q || '') + '" onkeydown="if(event.key===\'Enter\')Admin.setFilter(\'q\',this.value)">' +
        '<select onchange="Admin.setFilter(\'role\',this.value)"><option value="">全部角色</option>' +
        ['superadmin', 'admin', 'teacher', 'student'].map(x => '<option value="' + x + '" ' + (f.role === x ? 'selected' : '') + '>' +
          ({ superadmin: '超级管理员', admin: '系统管理员', teacher: '讲师', student: '学员' }[x]) + '</option>').join('') + '</select>' +
        '<select onchange="Admin.setFilter(\'status\',this.value)"><option value="">全部状态</option>' +
        '<option value="active" ' + (f.status === 'active' ? 'selected' : '') + '>正常</option>' +
        '<option value="disabled" ' + (f.status === 'disabled' ? 'selected' : '') + '>停用</option>' +
        '<option value="blacklist" ' + (f.status === 'blacklist' ? 'selected' : '') + '>黑名单</option>' +
        '<option value="pending" ' + (f.status === 'pending' ? 'selected' : '') + '>待审核</option></select>' +
        '<button class="btn" onclick="Admin.setFilter(\'q\',document.getElementById(\'u-kw\').value)">筛选</button>' +
        '<button class="btn primary" style="margin-left:auto" onclick="Admin.newUser()">＋ 新建用户</button>' +
        '<button class="btn" onclick="Admin.importUsers()">批量导入</button>' +
        '<a class="btn" href="/api/admin/users/export?token=' + API.token + '">导出 CSV</a>' +
        '<a class="btn" href="/api/admin/stats/export?token=' + API.token + '">统计报表</a></div>' +
        this._batchbar('users') +
        '<div class="panel"><div style="overflow:auto"><table><thead><tr><th class="ckcol"></th><th>成员</th><th>部门 / 岗位</th><th>头衔</th><th>角色</th>' +
        '<th>状态</th><th>注册 / 登录</th><th>操作</th></tr></thead><tbody>' +
        (rows || '<tr><td colspan="8"><div class="empty">暂无用户</div></td></tr>') + '</tbody></table></div>' +
        '</div>' +
        UI.pager(r.data.total || 0, this.page, 20, 'Admin.goPage');
      CtxMenu.attach(box, 'tr[data-uid]', el => this.userMenu(+el.dataset.uid));
    },

    /* 成员行右键菜单 */
    userMenu(id) {
      const u = (this._rows || []).filter(x => x.id === id)[0];
      if (!u) return [];
      const dis = u.status === 'disabled';
      return [
        { label: '编辑资料', icon: 'edit', onClick: () => this.editUser(id) },
        { label: '权限设置', icon: 'shield', onClick: () => this.userPerms(id) },
        { label: '登录设备', icon: 'users', onClick: () => this.userDevices(id) },
        { sep: true },
        { label: '重置密码', icon: 'undo', onClick: () => this.resetPwd(id) },
        u.status !== 'blacklist' ? { label: dis ? '启用账号' : '停用账号', icon: 'exit', onClick: () => this.toggleUser(id) } : null,
        u.status !== 'blacklist' ? { label: '加入黑名单', icon: 'trash', danger: true, onClick: () => this.blacklistUI(id) } : null,
        { label: '删除用户', icon: 'trash', danger: true, onClick: () => this.delUser(id) }
      ].filter(Boolean);
    },
    /* 成员更多操作：低频管理动作收进弹窗，保证表格一屏内可读 */
    userActions(id) {
      const u = (this._rows || []).filter(x => x.id === id)[0] || {};
      const name = UI.esc(u.display_name || ('用户 ' + id));
      UI.modal('成员操作 · ' + name,
        '<div class="u-actions">' +
        '<button class="btn" onclick="UI.closeModal();Admin.resetPwd(' + id + ')">重置密码</button>' +
        '<button class="btn" onclick="UI.closeModal();Admin.userDevices(' + id + ')">登录设备</button>' +
        (u.status === 'blacklist' ? '' : '<button class="btn" onclick="UI.closeModal();Admin.toggleUser(' + id + ')">' + (u.status === 'disabled' ? '启用账号' : '停用账号') + '</button>') +
        '<button class="btn danger" onclick="UI.closeModal();Admin.blacklistUI(' + id + ')">' + (u.status === 'blacklist' ? '移出黑名单' : '加入黑名单') + '</button>' +
        '<button class="btn danger" onclick="UI.closeModal();Admin.delUser(' + id + ')">删除用户</button>' +
        '</div>',
        '<button class="btn" onclick="UI.closeModal()">关闭</button>', 440);
    },
    goPage(p) { this.page = p; this.render(); },
    setFilter(k, v) { if (v) this.filters[k] = v; else delete this.filters[k]; this.page = 1; this.render(); },

    /* ---------- 复选框多选 + 批量操作（通用） ---------- */
    BATCH_DEFS: {
      users: { ep: '/api/admin/batch/users', actions: [
        { v: 'disable', t: '批量停用', danger: true, reason: true },
        { v: 'enable', t: '批量启用' },
        { v: 'blacklist', t: '批量加入黑名单', danger: true, reason: true },
        { v: 'unblacklist', t: '批量移出黑名单' },
        { v: 'reset_pwd', t: '批量重置密码' },
        { v: 'delete', t: '批量删除用户', danger: true } ] },
      rooms: { ep: '/api/admin/batch/rooms', actions: [
        { v: 'archive', t: '批量归档' }, { v: 'unarchive', t: '批量取消归档' },
        { v: 'delete', t: '批量删除房间', danger: true } ] },
      msg: { ep: '/api/admin/batch/messages', actions: [
        { v: 'recall', t: '批量撤回', danger: true }, { v: 'delete', t: '批量删除消息', danger: true } ] },
      announce: { ep: '/api/admin/batch/announcements', actions: [
        { v: 'pin', t: '批量置顶' }, { v: 'unpin', t: '批量取消置顶' },
        { v: 'delete', t: '批量删除公告', danger: true } ] },
      ipbl: { ep: '/api/admin/batch/ipblacklist', actions: [
        { v: 'remove', t: '批量移出黑名单', danger: true } ] },
      logs: { ep: '/api/admin/batch/logs', actions: [
        { v: 'delete', t: '批量删除日志', danger: true } ] },
    },
    _ck(scope, id) {
      return '<input type="checkbox" class="ck" data-scope="' + scope + '" data-id="' + id +
        '" onchange="Admin._onCk(this)">';
    },
    _ckAll(scope) {
      return '<input type="checkbox" class="ck-all" data-scope="' + scope +
        '" onchange="Admin._onCkAll(\'' + scope + '\',this.checked)">';
    },
    /* 批量选择条（表格上方）：全选 / 已选计数 / 批量动作 */
    _batchbar(scope) {
      const def = this.BATCH_DEFS[scope];
      const acts = (def && def.actions) || [];
      return '<div class="batchbar" id="selbar-' + scope + '">' +
        '<label class="batchall"><input type="checkbox" class="ck-all" data-scope="' + scope +
        '" onchange="Admin._onCkAll(\'' + scope + '\',this.checked)"><span>全选本页</span></label>' +
        '<span class="batchcount">已选 <b id="seln-' + scope + '">0</b> 项</span>' +
        '<button class="link batchclear" onclick="Admin.clearSel(\'' + scope + '\')">重置选择</button>' +
        '<div class="batchactions">' + acts.map(a => '<button class="btn sm ' + (a.danger ? 'danger' : '') +
          '" id="selact-' + scope + '-' + a.v + '" disabled' +
          ' onclick="Admin.doBatch(\'' + scope + '\',\'' + a.v + '\')">' + a.t + '</button>').join('') + '</div>' +
        '</div>';
    },
    _selbar(scope) { return this._batchbar(scope); },
    _onCk(el) { this._syncSel(el.dataset.scope); },
    _onCkAll(scope, checked) {
      document.querySelectorAll('.ck[data-scope="' + scope + '"]').forEach(c => { c.checked = checked; });
      this._syncSel(scope);
    },
    _selected(scope) {
      return Array.from(document.querySelectorAll('.ck[data-scope="' + scope + '"]:checked'))
        .map(c => parseInt(c.dataset.id, 10));
    },
    _syncSel(scope) {
      const bar = document.getElementById('selbar-' + scope);
      if (!bar) return;
      const boxes = document.querySelectorAll('.ck[data-scope="' + scope + '"]');
      const n = this._selected(scope).length;
      bar.classList.toggle('has-sel', n > 0);
      document.querySelectorAll('.batchbar [id^="selact-' + scope + '-"]').forEach(b => { b.disabled = !n; });
      document.getElementById('seln-' + scope).textContent = n;
      const all = document.querySelector('.ck-all[data-scope="' + scope + '"]');
      if (all) all.checked = n > 0 && n === boxes.length;
    },
    clearSel(scope) {
      document.querySelectorAll('.ck[data-scope="' + scope + '"],.ck-all[data-scope="' + scope + '"]')
        .forEach(c => { c.checked = false; });
      this._syncSel(scope);
    },
    doBatch(scope, act) {
      const ids = this._selected(scope);
      if (!ids.length) return UI.toast('请先勾选要操作的项目', 'err');
      const def = this.BATCH_DEFS[scope];
      const a = def.actions.find(x => x.v === act);
      if (!a) return;
      const run = async (reason) => {
        const r = await API.post(def.ep, { ids: ids, action: act, reason: reason || '' });
        if (!r.ok) return UI.toast(r.msg, 'err');
        const d = r.data || {};
        UI.toast(d.msg || '批量操作完成', (d.fail && d.fail.length) ? 'err' : 'ok');
        if (d.fail && d.fail.length) {
          setTimeout(() => UI.modal('部分项目被跳过（' + d.fail.length + ' 项）',
            '<p style="color:var(--text-2);font-size:12px;margin-bottom:8px">以下项目不满足条件，已自动跳过：</p>' +
            d.fail.map(f => '<div class="list-item"><div class="li-main"><b>' +
              UI.esc(f.name || ('#' + f.id)) + '</b><small>' + UI.esc(f.msg || '') + '</small></div>').join(''),
            '<button class="btn primary" onclick="UI.closeModal()">知道了</button>', 520), 350);
        }
        if (d.passwords && d.passwords.length) this._showPasswords(d.passwords);
        this.render();
      };
      if (a.reason) {
        UI.prompt({ title: a.t + '（已选 ' + ids.length + ' 项）', label: '原因（可选，写入通知与审计日志）',
          placeholder: '如：违规处理、组织架构调整', okText: '确认执行', width: 480,
          validate: v => v.length > 50 ? '原因不能超过 50 字' : '',
          onOk: v => run(v) });
      } else {
        UI.confirm(a.t + '：共 ' + ids.length + ' 项，确定执行？', () => run(''), !!a.danger);
      }
    },
    _showPasswords(list) {
      this._pwds = list;
      UI.modal('批量重置密码完成（' + list.length + ' 人）',
        '<p style="color:var(--text-2);font-size:12px;margin-bottom:8px">请妥善转告各用户，首次登录需修改密码：</p>' +
        '<div style="max-height:320px;overflow:auto">' + list.map(x =>
          '<div class="list-item"><div class="li-main"><b>' + UI.esc(x.name) + '</b><small>@' +
          UI.esc(x.username) + '</small></div><code style="font-size:14px">' + UI.esc(x.password) + '</code></div>').join('') +
        '</div>',
        '<button class="btn" onclick="Admin._copyPwds()">复制全部</button>' +
        '<button class="btn primary" onclick="UI.closeModal()">知道了</button>', 480);
    },
    _copyPwds() {
      const txt = (this._pwds || []).map(x => x.username + ' / ' + x.password).join('\n');
      const ta = document.createElement('textarea');
      ta.value = txt; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); UI.toast('已复制到剪贴板', 'ok'); }
      catch (e) { UI.toast('复制失败，请手动抄录', 'err'); }
      ta.remove();
    },

    newUser() {
      UI.modal('新建用户',
        '<div class="grid2">' +
        '<label class="field"><span>用户名 *</span><input id="nu-name"></label>' +
        '<label class="field"><span>昵称</span><input id="nu-display" placeholder="聊天中展示的名字"></label>' +
        '<label class="field"><span>角色</span><select id="nu-role"><option value="student">学员</option>' +
        '<option value="teacher">讲师</option><option value="admin">系统管理员</option></select></label>' +
        '<label class="field"><span>部门</span><input id="nu-dept"></label>' +
        '<label class="field"><span>岗位</span><input id="nu-pos"></label>' +
        '<label class="field"><span>自定义头衔（如：安全之星，≤20 字，可空）</span><input id="nu-title" maxlength="20"></label>' +
        '<label class="field"><span>手机</span><input id="nu-phone"></label>' +
        '<label class="field"><span>邮箱</span><input id="nu-email"></label>' +
        '<label class="field"><span>初始密码</span><input id="nu-pwd" placeholder="留空自动生成"></label>' +
        '<div class="field" style="grid-column:1/-1"><span>小游戏授权（默认全关，按人开启）</span>' +
        '<label style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;cursor:pointer"><input type="checkbox" id="nu-g-fireboy"> 森林冰火人</label>' +
        '<label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="nu-g-chess"> 中国象棋</label></div></div>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Admin.doNewUser()">创建</button>');
    },
    async doNewUser() {
      const r = await API.post('/api/admin/users', {
        username: UI.val('nu-name'), display_name: UI.val('nu-display'),
        role: document.getElementById('nu-role').value, dept: UI.val('nu-dept'),
        position: UI.val('nu-pos'), title: UI.val('nu-title'), phone: UI.val('nu-phone'), email: UI.val('nu-email'),
        password: UI.val('nu-pwd'),
        games: ['fireboy', 'chess'].filter(k => {
          const el = document.getElementById('nu-g-' + k);
          return el && el.checked;
        }).join(',')
      });
      UI.closeModal();
      if (r.ok) {
        UI.modal('创建成功', '<p>用户已创建。</p><p>初始密码：<b style="font-size:16px">' + UI.esc(r.data.password) +
          '</b></p><p style="color:var(--text-3);font-size:12px">请妥善告知该用户，首次登录将要求修改密码。</p>',
          '<button class="btn primary" onclick="UI.closeModal()">知道了</button>', 420);
      } else UI.toast(r.msg, 'err');
      this.render();
    },
    async editUser(id) {
      const r = await API.get('/api/admin/users', { page: 1, page_size: 500 });
      const u = (r.data.list || []).find(x => x.id === id);
      if (!u) return UI.toast('用户不存在', 'err');
      const r2 = await API.get('/api/admin/depts');
      this._editRole = u.role;
      const ug = (u.games || '').split(',').filter(Boolean);
      const gamesUi = u.role === 'superadmin'
        ? '<div class="field" style="grid-column:1/-1"><span>小游戏授权</span>' +
          '<span style="color:var(--text-2);font-size:12px">超级管理员恒定拥有全部小游戏，无需单独授权。</span></div>'
        : '<div class="field" style="grid-column:1/-1"><span>小游戏授权（默认全关，按人开启）</span>' +
          '<label style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;cursor:pointer"><input type="checkbox" id="eu-g-fireboy"' +
          (ug.indexOf('fireboy') >= 0 ? ' checked' : '') + '> 森林冰火人</label>' +
          '<label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="eu-g-chess"' +
          (ug.indexOf('chess') >= 0 ? ' checked' : '') + '> 中国象棋</label>' +
          '<span style="color:var(--text-3);font-size:11px;display:block;margin-top:4px">未勾选则该用户看不到小游戏入口与游戏。</span></div>';
      UI.modal('编辑用户 · ' + UI.esc(u.display_name) + '（@' + UI.esc(u.username) + '）',
        '<div class="grid2">' +
        '<label class="field"><span>昵称</span><input id="eu-display" maxlength="40" value="' + UI.esc(u.display_name) + '"></label>' +
        '<label class="field"><span>角色</span><select id="eu-role">' +
        ['superadmin', 'admin', 'teacher', 'student'].map(x => '<option value="' + x + '" ' + (u.role === x ? 'selected' : '') + '>' +
          ({ superadmin: '超级管理员', admin: '系统管理员', teacher: '讲师', student: '学员' }[x]) + '</option>').join('') + '</select></label>' +
        '<label class="field"><span>部门</span><input id="eu-dept" value="' + UI.esc(u.dept || '') + '" list="dept-list">' +
        '<datalist id="dept-list">' + (r2.ok ? r2.data.list.map(d => '<option value="' + UI.esc(d.name) + '">').join('') : '') + '</datalist></label>' +
        '<label class="field"><span>岗位</span><input id="eu-pos" value="' + UI.esc(u.position || '') + '"></label>' +
        '<label class="field"><span>自定义头衔（≤20 字）</span><input id="eu-title" maxlength="20" value="' + UI.esc(u.title || '') + '"></label>' +
        '<label class="field"><span>手机</span><input id="eu-phone" value="' + UI.esc(u.phone || '') + '"></label>' +
        '<label class="field"><span>邮箱</span><input id="eu-email" value="' + UI.esc(u.email || '') + '"></label>' +
        '<label class="field"><span>状态</span><select id="eu-status"><option value="active"' + (u.status === 'active' ? ' selected' : '') + '>正常</option>' +
        '<option value="disabled"' + (u.status === 'disabled' ? ' selected' : '') + '>停用</option>' +
        '<option value="blacklist"' + (u.status === 'blacklist' ? ' selected' : '') + '>黑名单</option>' +
        '<option value="pending"' + (u.status === 'pending' ? ' selected' : '') + '>待审核</option></select></label>' +
        '<label class="field"><span>强制改密</span><select id="eu-mcp"><option value="0">否</option><option value="1"' +
        (u.must_change_pwd ? ' selected' : '') + '>是</option></select></label>' +
        gamesUi + '</div>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Admin.doEditUser(' + id + ')">保存</button>');
    },
    async doEditUser(id) {
      const games = this._editRole === 'superadmin' ? undefined
        : ['fireboy', 'chess'].filter(k => {
            const el = document.getElementById('eu-g-' + k);
            return el && el.checked;
          }).join(',');
      const body = {
        display_name: UI.val('eu-display'), role: document.getElementById('eu-role').value,
        dept: UI.val('eu-dept'), position: UI.val('eu-pos'), title: UI.val('eu-title'), phone: UI.val('eu-phone'),
        email: UI.val('eu-email'), status: document.getElementById('eu-status').value,
        must_change_pwd: parseInt(document.getElementById('eu-mcp').value, 10)
      };
      if (games !== undefined) body.games = games;
      const r = await API.put('/api/admin/users/' + id, body);
      UI.closeModal();
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    /* 封号/启用窗口：内置弹窗，停用需确认并可填写原因 */
    toggleUser(id) {
      const u = (this._rows || []).find(x => x.id === id);
      if (!u) return UI.toast('请先刷新用户列表', 'err');
      const banning = u.status !== 'disabled';
      if (!banning) {   // 启用：简单确认即可
        return UI.confirm('恢复「' + u.display_name + '」的登录与发言权限？', async () => {
          const r = await API.post('/api/admin/users/' + id + '/toggle', {});
          UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
        });
      }
      UI.modal('封禁账号 · ' + UI.esc(u.display_name),
        '<p style="color:var(--text-2);font-size:13px">封禁后该账号将<b>立即被强制下线、所有登录凭据失效、' +
        '无法再次登录</b>，并向全体在线用户广播提示。账号数据与历史消息会保留，可随时启用恢复。</p>' +
        '<p style="font-size:13px;margin:8px 0">账号：<b>' + UI.esc(u.display_name) + '</b>（@' + UI.esc(u.username) +
        '）· ' + UI.esc(u.role_name) + ' · ' + UI.esc(u.dept || '未分配') + '</p>' +
        '<label class="field"><span>封禁原因（可选，≤50 字，写入通知与广播）</span>' +
        '<input id="ban-reason" maxlength="50" placeholder="如：严重违反企业通讯规范"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn danger" id="ban-ok">确认封禁</button>', 480);
      document.getElementById('ban-ok').onclick = async () => {
        const r = await API.post('/api/admin/users/' + id + '/toggle', { reason: UI.val('ban-reason') });
        UI.closeModal();
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      };
    },
    /* 黑名单窗口：比普通停用更强的永久处罚，加入需确认并可填原因，移出走简单确认 */
    blacklistUI(id) {
      const u = (this._rows || []).find(x => x.id === id);
      if (!u) return UI.toast('请先刷新用户列表', 'err');
      if (u.status === 'blacklist') {   // 移出黑名单
        return UI.confirm('将「' + u.display_name + '」移出黑名单，恢复其登录与使用？', async () => {
          const r = await API.post('/api/admin/users/' + id + '/blacklist', { action: 'remove' });
          UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
        });
      }
      UI.modal('加入黑名单 · ' + UI.esc(u.display_name),
        '<p style="color:var(--text-2);font-size:13px">加入黑名单后该账号将<b>立即强制下线、所有登录凭据失效、' +
        '永久禁止登录</b>，并<b>从其所在的全部房间移除</b>，无法再被邀请加入任何房间，同时向全体在线用户广播。' +
        '这是比普通停用更重的处罚，需在此手动移出才能恢复。账号数据与历史消息会保留。</p>' +
        '<p style="font-size:13px;margin:8px 0">账号：<b>' + UI.esc(u.display_name) + '</b>（@' + UI.esc(u.username) +
        '）· ' + UI.esc(u.role_name) + ' · ' + UI.esc(u.dept || '未分配') + '</p>' +
        '<label class="field"><span>加入原因（可选，≤50 字，写入通知与广播）</span>' +
        '<input id="bl-reason" maxlength="50" placeholder="如：恶意骚扰、泄露机密，永久拉黑"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn danger" id="bl-ok">确认加入黑名单</button>', 500);
      document.getElementById('bl-ok').onclick = async () => {
        const r = await API.post('/api/admin/users/' + id + '/blacklist', { action: 'add', reason: UI.val('bl-reason') });
        UI.closeModal();
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      };
    },
    resetPwd(id) {
      UI.prompt({ title: '重置密码', label: '新密码（留空则随机生成）', type: 'password',
        placeholder: '留空 = 系统随机生成', width: 440, onOk: async p => {
          const r = await API.post('/api/admin/users/' + id + '/reset-pwd', { password: p });
          if (r.ok) UI.modal('密码已重置', '<p>新密码：<b style="font-size:16px">' + UI.esc(r.data.password) + '</b></p>',
            '<button class="btn primary" onclick="UI.closeModal()">知道了</button>', 420);
          else UI.toast(r.msg, 'err');
        } });
    },
    delUser(id) {
      UI.confirm('确定删除该用户？其消息与成员关系将一并清理。', async () => {
        const r = await API.del('/api/admin/users/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      }, true);
    },
    /* 登录客户端/设备：当前会话 + 最近登录记录，可强制下线 */
    async userDevices(id) {
      const u = (this._rows || []).find(x => x.id === id);
      const r = await API.get('/api/admin/users/' + id + '/devices');
      if (!r.ok) return UI.toast(r.msg, 'err');
      const d = r.data;
      const cur = d.current;
      const name = (d.user || {}).name || (u ? u.display_name : '用户');
      let html = cur
        ? '<div class="list-item"><div class="li-main"><b>当前设备：' + UI.esc(cur.client) + '</b>' +
          '<small>登录 IP ' + UI.esc(cur.ip || '—') + ' · 登录于 ' + UI.esc(cur.login_at || '') +
          (cur.last_seen ? ' · 最后活跃 ' + UI.esc(cur.last_seen) : '') + '</small></div></div>'
        : '<div class="empty">该账号当前无活跃会话</div>';
      const hist = d.history || [];
      html += '<div style="margin-top:10px"><b style="font-size:13px">最近登录记录（最多 20 条）</b>' +
        (hist.length ? hist.map(h => '<div class="list-item" style="padding:7px 0"><div class="li-main"><b>' +
          UI.esc(h.ts) + '</b><small>' + UI.esc(h.detail || ('IP ' + UI.esc(h.ip))) + '</small></div></div>').join('')
          : '<p style="color:var(--text-3);font-size:13px">暂无登录记录</p>') + '</div>';
      const kickBtn = cur
        ? '<button class="btn danger" style="margin-top:12px" onclick="Admin.kickUserSessions(' + id + ')">强制下线该账号</button>' +
          '<span style="display:inline-block;margin-left:8px;font-size:11px;color:var(--text-3)">将清空其全部登录会话，立即断开</span>'
        : '';
      UI.modal('登录设备 · ' + UI.esc(name), html + kickBtn,
        '<button class="btn" onclick="UI.closeModal()">关闭</button>', 520);
    },
    async kickUserSessions(id) {
      const r = await API.post('/api/admin/users/' + id + '/kick', {});
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) { UI.closeModal(); this.render(); }
    },
    /* 单用户权限微调：在角色权限基础上追加额外权限（超级管理员恒定全部，无需调整） */
    async userPerms(id) {
      const u = (this._rows || []).find(x => x.id === id);
      if (!u) return UI.toast('请先刷新用户列表', 'err');
      if (u.role === 'superadmin') return UI.toast('超级管理员恒定拥有全部权限，无需调整', 'err');
      const r = await API.get('/api/admin/permissions');
      const groups = r.ok ? (r.data.groups || []) : [];
      const extra = (u.extra_perms || '').split(',').map(s => s.trim()).filter(Boolean);
      UI.modal('权限微调 · ' + UI.esc(u.display_name) + '（@' + UI.esc(u.username) + '）',
        '<p style="color:var(--text-2);font-size:13px;margin-bottom:8px">角色「' + UI.esc(u.role_name) +
        '」自带权限 + 下方勾选的额外权限 = 用户实际权限。</p>' +
        groups.map(g => '<div style="margin-bottom:6px"><b style="font-size:13px;color:var(--text-3)">' +
          UI.esc(g.name) + '</b> ' + g.perms.map(p => {
            const on = extra.indexOf(p.key) >= 0;
            return '<label class="perm-chip' + (on ? ' on' : '') + '"><input type="checkbox" ' +
              (on ? 'checked' : '') + ' data-up="' + UI.esc(p.key) + '" onchange="this.parentElement.classList.toggle(\'on\',this.checked)"> ' +
              UI.esc(p.name) + '</label>';
          }).join('') + '</div>').join(''),
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn primary" onclick="Admin.saveUserPerms(' + id + ')">保存权限</button>', 760);
    },
    async saveUserPerms(id) {
      const perms = Array.from(document.querySelectorAll('[data-up]:checked')).map(c => c.dataset.up);
      const r = await API.put('/api/admin/users/' + id + '/perms', { perms: perms });
      UI.closeModal();
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    importUsers() {
      UI.modal('批量导入用户',
        '<p style="color:var(--text-2);font-size:13px">格式（每行一条，逗号分隔）：<br>' +
        '<code>用户名,昵称,角色(student/teacher/admin),部门,岗位,邮箱,手机,密码</code><br>密码留空将自动生成。</p>' +
        '<label class="field"><span>粘贴数据</span><textarea id="imp-data" style="width:100%;height:160px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>' +
        '<label class="field"><span>或选择 CSV 文件</span><input type="file" id="imp-file" accept=".csv,.txt"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Admin.doImport()">导入</button>', 620);
    },
    async doImport() {
      const fd = new FormData();
      fd.append('data', document.getElementById('imp-data').value || '');
      const f = document.getElementById('imp-file').files[0];
      if (f) fd.append('file', f);
      const r = await API.upload('/api/admin/users/import', fd);
      UI.closeModal();
      if (r.ok) UI.toast('导入完成：成功 ' + r.data.ok + ' 条，失败 ' + r.data.fail + ' 条', r.data.fail ? 'err' : 'ok');
      else UI.toast(r.msg, 'err');
      this.render();
    },

    /* ---------- 资料申请（头衔审核） ---------- */
    proreq() {
      this._proreqRender();
    },
    _proreqFilter: 'pending',
    setProreqFilter(v) { this._proreqFilter = v; this._proreqRender(); },
    async _proreqRender() {
      const box = document.getElementById('admin-body');
      const st = this._proreqFilter;
      const r = await API.get('/api/admin/profile-requests', { status: st });
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      this._proreqRows = d.list || [];
      const diff = (label, a, b) => '<div style="font-size:13px"><span style="color:var(--text-3)">' + label +
        '：</span><s style="color:var(--text-3)">' + UI.esc(a || '（无）') + '</s> → <b>' + UI.esc(b || '（清空）') + '</b></div>';
      box.innerHTML =
        '<div class="toolbar">' +
        ['pending', 'approved', 'rejected'].map(x => '<button class="btn' + (st === x ? ' primary' : '') +
          '" onclick="Admin.setProreqFilter(\'' + x + '\')">' +
          ({ pending: '待审核', approved: '已通过', rejected: '已驳回' }[x]) +
          (x === 'pending' && d.pending ? '（' + d.pending + '）' : '') + '</button>').join('') +
        '<span style="margin-left:auto;font-size:12px;color:var(--text-3)">' +
        (d.approval_on ? '已开启「头衔修改需审核」，普通用户只能提交申请；昵称可由本人在个人中心直接修改'
          : '未开启审核开关，用户可直接修改自己的头衔') + '</span></div>' +
        '<div class="panel"><div>' + ((this._proreqRows.map(q =>
          '<div class="list-item"><div class="li-main"><b>' + UI.esc(q.user_name) +
          (q.status === 'pending' ? ' <span class="tag warn">待审核</span>' : '') + '</b>' +
          '<small>@' + UI.esc(q.username) + ' · ' + UI.esc(q.dept || '未分配') + ' · ' + UI.esc(q.role_name) +
          ' · 提交于 ' + UI.esc(q.created_str) + '</small>' +
          (q.name_changed ? diff('昵称', q.old_display_name, q.new_display_name) : '') +
          (q.title_changed ? diff('头衔', q.old_title, q.new_title) : '') +
          (q.reason ? '<small>理由：' + UI.esc(q.reason) + '</small>' : '<small>理由：未填写</small>') +
          (q.status !== 'pending' ? '<small>处理：' + UI.esc(q.status_name) + ' · ' + UI.esc(q.reviewer_name || '') +
            ' ' + UI.esc(q.reviewed_str || '') + (q.review_note ? ' · 批注：' + UI.esc(q.review_note) : '') + '</small>' : '') +
          '</div>' +
          (q.status === 'pending' ? '<div class="row-actions">' +
            '<button class="btn sm primary" onclick="Admin.proreqReview(' + q.id + ',\'approve\')">通过</button>' +
            '<button class="btn sm danger" onclick="Admin.proreqReview(' + q.id + ',\'reject\')">驳回</button></div>' : '') +
          '</div>').join('') || '<div class="empty">' + ({ pending: '暂无待审核的申请', approved: '暂无已通过的申请', rejected: '暂无已驳回的申请' }[st]) + '</div>')) +
        '</div></div>';
    },
    proreqReview(id, action) {
      const q = (this._proreqRows || []).find(x => x.id === id);
      if (!q) return UI.toast('请刷新列表后重试', 'err');
      const what = (q.name_changed ? '昵称「' + (q.old_display_name || '') + '」→「' + (q.new_display_name || '') + '」' : '') +
        (q.name_changed && q.title_changed ? '，' : '') +
        (q.title_changed ? '头衔「' + (q.old_title || '无') + '」→「' + (q.new_title || '清空') + '」' : '');
      if (action === 'approve') {
        return UI.confirm('确认通过「' + q.user_name + '」的申请（' + what + '）？通过后资料立即生效并同步到所有聊天窗口。', async () => {
          const r = await API.post('/api/admin/profile-requests/' + id + '/review', { action: 'approve' });
          UI.toast(r.msg, r.ok ? 'ok' : 'err');
          if (r.ok) { this._proreqRender(); this._proreqBadge(); }
        });
      }
      UI.prompt({ title: '驳回申请 · ' + q.user_name, label: '驳回批注（会通知申请人）',
        placeholder: '如：请使用与工牌一致的头衔，便于同事识别', width: 480,
        validate: v => v.length > 200 ? '批注不能超过 200 字' : '',
        onOk: async note => {
          const r = await API.post('/api/admin/profile-requests/' + id + '/review', { action: 'reject', note: note });
          UI.toast(r.msg, r.ok ? 'ok' : 'err');
          if (r.ok) { this._proreqRender(); this._proreqBadge(); }
        } });
    },
    async _proreqBadge() {
      const dot = document.getElementById('proreq-badge');
      if (!dot) return;
      if ((API.user.perms || []).indexOf('user.manage') < 0) { dot.textContent = ''; return; }
      const r = await API.get('/api/admin/profile-requests', { status: 'pending' });
      const n = r.ok ? (r.data.pending || 0) : 0;
      dot.textContent = n ? String(n) : '';
      dot.style.display = n ? '' : 'none';
    },

    /* ---------- 房间管理 ---------- */
    async rooms() {
      const r = await API.get('/api/admin/rooms');
      const box = document.getElementById('admin-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      box.innerHTML = '<div class="toolbar"><button class="btn primary" onclick="Chat.newRoom()">＋ 新建房间</button></div>' +
        this._batchbar('rooms') +
        '<div class="panel"><table><thead><tr><th class="ckcol"></th><th>ID</th><th>名称</th><th>类型</th><th>房主</th><th>成员</th>' +
        '<th>消息</th><th>创建时间</th><th>操作</th></tr></thead><tbody>' +
        ((r.data.list || []).map(x => '<tr><td>' + this._ck('rooms', x.id) + '</td><td>' + x.id + '</td><td><b>' + UI.esc(x.name) + '</b><br>' +
          '<small style="color:var(--text-3)">' + UI.esc(x.topic || '') + '</small></td>' +
          '<td><span class="tag gray">' + UI.esc(x.type) + '</span>' + (x.archived ? ' <span class="tag warn">已归档</span>' : '') + '</td>' +
          '<td>' + UI.esc(x.owner_name) + '</td><td>' + x.member_count + '</td><td>' + x.msg_count + '</td>' +
          '<td><small>' + UI.esc(x.created_str) + '</small></td><td><div class="row-actions">' +
          '<button class="btn sm" onclick="Admin.archiveRoom(' + x.id + ',' + (x.archived ? 0 : 1) + ')">' + (x.archived ? '取消归档' : '归档') + '</button>' +
          '<button class="btn sm danger" onclick="Admin.delRoom(' + x.id + ')">删除</button></div></td></tr>').join('') ||
          '<tr><td colspan="9"><div class="empty">暂无房间</div></td></tr>') + '</tbody></table>' +
        '</div>';
    },
    async archiveRoom(id, v) {
      const r = await API.put('/api/admin/rooms/' + id, { archived: v });
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    delRoom(id) {
      UI.confirm('删除房间将清空其全部消息，确定继续？', async () => {
        const r = await API.del('/api/admin/rooms/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      }, true);
    },

    /* ---------- 消息审核（按房间分类） ---------- */
    async messages() {
      const f = this.filters;
      const box = document.getElementById('admin-body');
      // 房间下拉：用于筛选与分组
      let rooms = [];
      try {
        const rr = await API.get('/api/admin/rooms');
        if (rr.ok) rooms = (rr.data && rr.data.list) || [];
      } catch (e) { /* 无权限等忽略 */ }
      const roomOpts = '<select id="m-room" onchange="Admin.setRoomFilter(this.value)">' +
        '<option value="">全部房间（按房间分组）</option>' +
        rooms.map(x => '<option value="' + x.id + '"' +
          (String(f.room_id || '') === String(x.id) ? ' selected' : '') + '>' +
          UI.esc(x.name) + '</option>').join('') + '</select>';
      const r = await API.get('/api/admin/messages', Object.assign({ page: this.page, page_size: 50 }, f));
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const list = r.data.list || [];
      const rowHtml = m =>
        '<tr><td>' + this._ck('msg', m.id) + '</td><td>' + m.id + '</td><td>' + UI.esc(m.display_name || '') +
        (m.title ? '<span class="title-tag">' + UI.esc(m.title) + '</span>' : '') +
        '<br><small style="color:var(--text-3)">' + UI.esc(m.sender_username || '') + ' · ' + UI.esc(m.sender_dept || '') + '</small></td>' +
        '<td>' + UI.esc(m.room_name || '') + '</td><td><span class="ellip" title="' + UI.esc(m.content || '') + '">' + UI.esc((m.content || '').slice(0, 80)) + '</span></td>' +
        '<td><small>' + UI.esc(m.created_str) + '</small></td>' +
        '<td><small style="font-family:monospace">' + UI.esc(m.ip || '—') + '</small></td>' +
        '<td><div class="row-actions">' +
        '<button class="btn sm" onclick="Admin.recallMsg(' + m.id + ')">撤回</button>' +
        '<button class="btn sm danger" onclick="Admin.delMsg(' + m.id + ')">删除</button></div></td></tr>';
      let bodyRows;
      if (f.room_id) {
        // 指定房间：平铺列表（顶部带房间标题行）
        bodyRows = '<tr class="room-sep"><td colspan="8">📁 ' + UI.esc((list[0] && list[0].room_name) || ('房间 #' + f.room_id)) + '</td></tr>' +
          (list.length ? list.map(rowHtml).join('') : '<tr><td colspan="8"><div class="empty">该房间暂无消息</div></td></tr>');
      } else {
        // 全部房间：按房间分组（组间插入分隔行）
        const groups = {};
        list.forEach(m => {
          const key = m.room_id || 0;
          (groups[key] = groups[key] || { name: m.room_name || '（未知房间）', rows: [] }).rows.push(m);
        });
        const keys = Object.keys(groups).sort((a, b) => (groups[b].rows[0].id || 0) - (groups[a].rows[0].id || 0));
        bodyRows = keys.map(k => {
          const g = groups[k];
          return '<tr class="room-sep"><td colspan="8">📁 ' + UI.esc(g.name) + ' · ' + g.rows.length + ' 条</td></tr>' +
            g.rows.map(rowHtml).join('');
        }).join('') ||
          '<tr><td colspan="8"><div class="empty">暂无消息</div></td></tr>';
      }
      box.innerHTML =
        '<div class="toolbar">' + roomOpts +
        '<input id="m-kw" placeholder="搜索消息内容" value="' + UI.esc(f.q || '') + '" onkeydown="if(event.key===\'Enter\')Admin.setFilter(\'q\',this.value)">' +
        '<input id="m-ip" placeholder="按 IP 筛选，如 192.168" value="' + UI.esc(f.ip || '') + '" onkeydown="if(event.key===\'Enter\')Admin.setFilter(\'ip\',this.value)">' +
        '<button class="btn" onclick="Admin.setFilter(\'q\',document.getElementById(\'m-kw\').value);Admin.filters.ip=document.getElementById(\'m-ip\').value;Admin.page=1;Admin.render()">搜索</button></div>' +
        this._batchbar('msg') +
        '<div class="panel"><table><thead><tr><th class="ckcol"></th><th>ID</th><th>发送人</th><th>房间</th><th>内容</th><th>时间</th><th>IP</th><th>操作</th></tr></thead><tbody>' +
        bodyRows + '</tbody></table>' +
        '</div>' +
        UI.pager(r.data.total || 0, this.page, 50, 'Admin.goPage');
    },
    setRoomFilter(v) {
      if (v) this.filters.room_id = v; else delete this.filters.room_id;
      this.page = 1; this.render();
    },
    async recallMsg(id) {
      const r = await API.post('/api/chat/messages/' + id + '/recall', {});
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    delMsg(id) {
      UI.confirm('确定删除该消息？', async () => {
        const r = await API.del('/api/chat/messages/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      }, true);
    },

    /* ---------- 公告 ---------- */
    async announce() {
      const r = await API.get('/api/admin/announcements');
      const box = document.getElementById('admin-body');
      const list = (r.ok ? r.data.list : []) || [];
      box.innerHTML =
        '<div class="toolbar"><button class="btn primary" onclick="Admin.announceForm()">＋ 发布公告</button>' +
        '<span class="db-meta">勾选「登录后弹窗」的公告会在成员登录后自动弹出；设了强制停留秒数则必须读够时间才能关闭</span></div>' +
        this._batchbar('announce') +
        '<div class="panel"><div>' + (list.length ? list.map(a => {
          const tags = (a.pinned ? '<span class="tag">置顶</span>' : '') +
            (Number(a.popup) ? '<span class="tag ok">登录弹窗</span>' : '') +
            (Number(a.force_seconds) ? '<span class="tag warn">强制 ' + a.force_seconds + ' 秒</span>' : '') +
            (Number(a.expires_at) ? '<span class="tag gray">' + new Date(Number(a.expires_at) * 1000).toLocaleDateString('zh-CN') + ' 截止</span>' : '');
          return '<div class="list-item">' +
            '<input type="checkbox" class="ck" data-scope="announce" data-id="' + a.id + '" onchange="Admin._onCk(this)" style="margin-right:8px">' +
            '<div class="ann-ico ' + UI.esc(a.level || 'info') + '">' + (a.level === 'urgent' ? '🚨' : a.level === 'warning' ? '⚠️' : '📢') + '</div>' +
            '<div class="li-main"><b>' + UI.esc(a.title) + ' ' + tags + '</b>' +
            '<small>' + UI.esc(a.author || '') + ' · ' + UI.esc(a.created_str) + '</small>' +
            '<div style="color:var(--text-2);font-size:13px;margin-top:4px">' + UI.nl2br(a.content || '') + '</div></div>' +
            '<button class="btn sm" onclick="Admin.pinAnnounce(' + a.id + ',' + (a.pinned ? 0 : 1) + ')">' + (a.pinned ? '取消置顶' : '置顶') + '</button>' +
            '<button class="btn sm" onclick="Admin.announceForm(' + a.id + ')">编辑</button>' +
            '<button class="btn sm danger" onclick="Admin.delAnnounce(' + a.id + ')">删除</button></div>';
        }).join('') : '<div class="empty">暂无公告</div>') + '</div></div>';
    },
    async announceForm(id) {
      let a = { title: '', content: '', level: 'info', pinned: 0, popup: 1, force_seconds: 0, expires_at: 0 };
      if (id) {
        const r = await API.get('/api/admin/announcements');
        a = ((r.ok && r.data.list) || []).filter(x => x.id === id)[0] || a;
      }
      const exp = Number(a.expires_at) ? new Date(Number(a.expires_at) * 1000) : null;
      const expStr = exp ? new Date(exp.getTime() - exp.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
      const opt = (v, t, cur) => '<option value="' + v + '"' + (String(cur) === String(v) ? ' selected' : '') + '>' + t + '</option>';
      UI.modal(id ? '编辑公告' : '发布公告',
        '<label class="field"><span>标题</span><input id="na-title" value="' + UI.esc(a.title || '') + '"></label>' +
        '<label class="field"><span>内容</span><textarea id="na-content" style="width:100%;height:136px;padding:10px;border:1px solid var(--border);border-radius:8px">' +
        UI.esc(a.content || '') + '</textarea></label>' +
        '<div class="grid2"><label class="field"><span>级别</span><select id="na-level">' +
        opt('info', '普通', a.level) + opt('warning', '重要', a.level) + opt('urgent', '紧急', a.level) + '</select></label>' +
        '<label class="field"><span>置顶</span><select id="na-pin">' + opt('0', '否', a.pinned) + opt('1', '是', a.pinned) + '</select></label>' +
        '<label class="field"><span>登录后弹窗显示</span><select id="na-popup">' + opt('1', '是', a.popup) + opt('0', '否', a.popup) + '</select></label>' +
        '<label class="field"><span>强制停留秒数（0 = 可立即关闭）</span><input id="na-force" type="number" min="0" max="600" value="' +
        (Number(a.force_seconds) || 0) + '"></label></div>' +
        '<label class="field"><span>展示截止时间（留空 = 长期有效）</span><input id="na-expires" type="datetime-local" value="' + expStr + '"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn primary" onclick="Admin.saveAnnounce(' + (id || 0) + ')">' + (id ? '保存' : '发布') + '</button>', 580);
    },
    async saveAnnounce(id) {
      const title = (UI.val('na-title') || '').trim();
      if (!title) { UI.toast('请填写公告标题', 'err'); return; }
      const exp = UI.val('na-expires');
      const payload = {
        title: title,
        content: document.getElementById('na-content').value,
        level: UI.val('na-level') || 'info',
        pinned: parseInt(UI.val('na-pin') || '0', 10) || 0,
        popup: parseInt(UI.val('na-popup') || '0', 10) || 0,
        force_seconds: Math.max(0, Math.min(600, parseInt(UI.val('na-force') || '0', 10) || 0)),
        expires_at: exp ? Math.floor(new Date(exp).getTime() / 1000) : 0
      };
      const r = id ? await API.put('/api/admin/announcements/' + id, payload)
                   : await API.post('/api/admin/announcements', payload);
      UI.closeModal();
      UI.toast(r.ok ? (id ? '公告已保存' : '公告已发布') : r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) this.render();
    },
    async pinAnnounce(id, v) {
      const r = await API.put('/api/admin/announcements/' + id, { pinned: v });
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    delAnnounce(id) {
      UI.confirm('确定删除该公告？', async () => {
        const r = await API.del('/api/admin/announcements/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      }, true);
    },

    /* ---------- 常用网站（教学资源导航） ---------- */
    async sites() {
      const r = await API.get('/api/admin/sites');
      const box = document.getElementById('admin-body');
      const list = (r.ok ? r.data.list : []) || [];
      this._sites = list;
      box.innerHTML =
        '<div class="toolbar">' +
        '<button class="btn primary" onclick="Admin.siteForm()">＋ 新增网站</button>' +
        '<button class="btn" onclick="Admin.siteImport()">批量导入</button>' +
        '<input id="sites-admin-kw" placeholder="搜索名称 / 地址 / 分类" oninput="Admin.sitesFilter(this.value)" style="max-width:220px">' +
        '<span class="db-meta">共 ' + list.length + ' 个站点，成员端「常用网站」实时同步</span></div>' +
        '<div class="panel"><div class="tablewrap"><table class="table"><thead><tr>' +
        '<th>网站</th><th>分类</th><th>地址</th><th>状态</th><th style="width:150px">操作</th></tr></thead><tbody>' +
        (list.length ? list.map(x => '<tr data-kw="' + UI.esc((x.name + ' ' + x.url + ' ' + x.category).toLowerCase()) + '">' +
          '<td><b>' + UI.esc(x.name) + '</b>' + (x.note ? '<br><small class="muted">' + UI.esc(x.note) + '</small>' : '') + '</td>' +
          '<td><span class="tag gray">' + UI.esc(x.category) + '</span></td>' +
          '<td><a class="link" href="' + UI.esc(x.url) + '" target="_blank" rel="noopener noreferrer">' + UI.esc(x.url) + '</a></td>' +
          '<td>' + (x.enabled ? '<span class="tag ok">显示</span>' : '<span class="tag gray">隐藏</span>') + '</td>' +
          '<td><button class="btn sm" onclick="Admin.siteForm(' + x.id + ')">编辑</button>' +
          '<button class="btn sm danger" onclick="Admin.siteDelete(' + x.id + ')">删除</button></td></tr>').join('')
          : '<tr><td colspan="5"><div class="empty">暂无网站，可点「新增网站」或「批量导入」</div></td></tr>') +
        '</tbody></table></div></div>';
    },
    sitesFilter(kw) {
      kw = (kw || '').trim().toLowerCase();
      document.querySelectorAll('#admin-body tbody tr[data-kw]').forEach(tr => {
        tr.style.display = !kw || tr.dataset.kw.indexOf(kw) >= 0 ? '' : 'none';
      });
    },
    siteForm(id) {
      const x = id ? ((this._sites || []).filter(v => v.id === id)[0] || {}) : {};
      const cats = [];
      (this._sites || []).forEach(v => { if (cats.indexOf(v.category) < 0) cats.push(v.category); });
      UI.modal(id ? '编辑网站' : '新增网站',
        '<div class="grid2"><label class="field"><span>名称</span><input id="st-name" value="' + UI.esc(x.name || '') + '" placeholder="如 睿达学习平台"></label>' +
        '<label class="field"><span>分类</span><input id="st-cat" list="site-cats" value="' + UI.esc(x.category || '') + '" placeholder="如 技能操作实训平台">' +
        '<datalist id="site-cats">' + cats.map(c => '<option value="' + UI.esc(c) + '">').join('') + '</datalist></label></div>' +
        '<label class="field"><span>地址（http:// 或 https://）</span><input id="st-url" value="' + UI.esc(x.url || '') + '" placeholder="https://www.example.com/"></label>' +
        '<div class="grid2"><label class="field"><span>备注（可选）</span><input id="st-note" value="' + UI.esc(x.note || '') + '"></label>' +
        '<label class="field"><span>状态</span><select id="st-enabled"><option value="1"' + (x.enabled === false ? '' : ' selected') + '>显示</option>' +
        '<option value="0"' + (x.enabled === false ? ' selected' : '') + '>隐藏</option></select></label></div>' +
        '<label class="field"><span>排序（数字越小越靠前）</span><input id="st-sort" type="number" value="' + (x.sort || 0) + '"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn primary" onclick="Admin.saveSite(' + (id || 0) + ')">保存</button>', 560);
    },
    async saveSite(id) {
      const payload = {
        name: (UI.val('st-name') || '').trim(), url: (UI.val('st-url') || '').trim(),
        category: (UI.val('st-cat') || '').trim(), note: (UI.val('st-note') || '').trim(),
        sort: parseInt(UI.val('st-sort') || '0', 10) || 0,
        enabled: parseInt(UI.val('st-enabled') || '1', 10)
      };
      if (!payload.name || !payload.url) { UI.toast('名称和地址不能为空', 'err'); return; }
      const r = id ? await API.put('/api/admin/sites/' + id, payload) : await API.post('/api/admin/sites', payload);
      UI.closeModal();
      UI.toast(r.ok ? (id ? '已保存' : r.data && r.data.msg || '已添加') : r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) this.render();
    },
    siteDelete(id) {
      UI.confirm('确定删除该网站？成员端将不再显示。', async () => {
        const r = await API.del('/api/admin/sites/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err');
        if (r.ok) this.render();
      }, true);
    },
    siteImport() {
      UI.modal('批量导入常用网站',
        '<p class="db-meta" style="margin-bottom:8px">每行一条，支持三种写法：<br>' +
        '① <code>分类,名称,地址</code>　② <code>名称,地址</code>　③ <code>地址</code>（名称自动取地址）<br>' +
        '地址可省略 http://，协议会自动补全；重复地址自动跳过。</p>' +
        '<textarea id="site-import-text" style="width:100%;height:200px;padding:10px;border:1px solid var(--border);border-radius:8px;font-family:Consolas,monospace" ' +
        'placeholder="办公协同,企业官网,https://www.example.com/&#10;学习资源,公开课,https://www.example.org/&#10;https://www.example.com/"></textarea>' +
        '<label class="field" style="margin-top:10px"><span>导入方式</span><select id="site-import-mode">' +
        '<option value="append">追加（保留现有）</option><option value="replace">覆盖（先清空）</option></select></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn primary" onclick="Admin.doSiteImport()">开始导入</button>', 620);
    },
    async doSiteImport() {
      const text = document.getElementById('site-import-text').value || '';
      if (!text.trim()) { UI.toast('请粘贴要导入的行', 'err'); return; }
      const mode = UI.val('site-import-mode') || 'append';
      const r = await API.post('/api/admin/sites/import', { text: text, mode: mode });
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      UI.closeModal();
      UI.toast(r.data.msg || r.msg, 'ok');
      this.render();
    },

    /* ---------- IP 访问控制（白名单 + 黑名单） ---------- */
    async ipbl() {
      const [r, s] = await Promise.all([API.get('/api/admin/ipblacklist'), API.get('/api/admin/settings')]);
      const box = document.getElementById('admin-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      const st = (s.ok ? s.data.settings : {}) || {};
      const wl = String(st.ip_whitelist || '').trim();
      const wlCount = wl ? wl.split(',').filter(x => x.trim()).length : 0;
      const badge = (txt, bg) => '<span class="tag" style="background:' + bg + ';color:#fff">' + txt + '</span>';
      box.innerHTML =
        '<div class="ip-head"><div><h2>IP 访问控制</h2>' +
        '<p>白名单只放行名单内的来源；黑名单拒绝指定来源；同名冲突时<span class="ip-warn">拒绝优先</span>。</p></div>' +
        '<div class="ip-actions"><button class="btn" onclick="Admin.tab(\'ipbl\')">刷新</button></div></div>' +

        '<section class="panel ip-panel"><div class="panel-head"><div><b>访问白名单</b>' +
        '<p class="db-meta">留空 = 不启用；支持 单 IP（192.168.1.23）、CIDR 网段（192.168.1.0/24）、前缀通配（10.20.*）；启用后<span class="ip-warn">请务必包含自己的 IP</span>，否则会把自己锁在门外</p></div>' +
        '<span class="tag ' + (wlCount ? 'ok' : 'gray') + '">' + (wlCount ? '已启用 · ' + wlCount + ' 条规则' : '未启用') + '</span></div>' +
        '<div class="panel-body"><textarea id="ip-wl" class="settings-area" placeholder="192.168.1.0/24, 10.0.0.5, 10.20.*">' + UI.esc(wl) + '</textarea>' +
        '<div class="settings-actions"><button class="btn primary" onclick="Admin.saveWhitelist()">保存白名单</button>' +
        '<button class="btn" onclick="Admin.clearWhitelist()">清空（不启用）</button>' +
        '<span class="db-meta">保存后立即生效，新连接即按新规则校验</span></div></div></section>' +

        '<section class="panel ip-panel"><div class="panel-head"><div><b>访问黑名单</b>' +
        '<p class="db-meta">命中后拒绝访问整个系统（页面 / 接口 / 实时连接），已在线连接立即踢下线；本机与 127.0.0.1 不可封禁</p></div>' +
        '<div class="ip-actions"><label class="ip-switchlabel">总开关</label>' +
        '<button class="btn sm ' + (d.enabled ? 'danger' : '') + '" onclick="Admin.ipblToggle(' + (d.enabled ? 0 : 1) + ')">' +
        (d.enabled ? '已启用，点击停用' : '已停用，点击启用') + '</button>' +
        '<button class="btn sm primary" onclick="Admin.ipblAddUI()">加入黑名单</button></div></div>' +
        this._batchbar('ipbl') +
        '<div class="tablewrap"><table><thead><tr><th class="ckcol"></th><th>ID</th><th>规则</th><th>类型</th><th>原因</th><th>到期</th><th>拦截次数</th><th>创建人 / 时间</th><th>操作</th></tr></thead><tbody>' +
        ((d.list || []).map(x => '<tr><td>' + this._ck('ipbl', x.id) + '</td><td>' + x.id + '</td>' +
          '<td class="db-cell">' + UI.esc(x.pattern) + '</td>' +
          '<td>' + badge(x.pattern.indexOf('/') >= 0 ? '网段' : (x.pattern.indexOf('*') >= 0 ? '通配' : '单 IP'),
            x.pattern.indexOf('/') >= 0 ? '#b45309' : (x.pattern.indexOf('*') >= 0 ? '#6d28d9' : '#dc2626')) + '</td>' +
          '<td><small>' + UI.esc(x.reason || '—') + '</small></td>' +
          '<td><small style="' + (x.expired ? 'color:#dc2626' : '') + '">' + UI.esc(x.expires_str) + (x.expired ? '（已过期）' : '') + '</small></td>' +
          '<td><b>' + (x.hits || 0) + '</b></td>' +
          '<td><small>' + UI.esc(x.created_str) + '</small></td>' +
          '<td><button class="btn sm danger" onclick="Admin.ipblRemove(' + x.id + ',\'' + UI.esc(x.pattern).replace(/'/g, "\\'") + '\')">移出</button></td></tr>').join('') ||
          '<tr><td colspan="9"><div class="empty">暂无封禁规则，所有来源均可访问</div></td></tr>') +
        '</tbody></table></div></section>' +

        '<section class="panel ip-panel"><div class="panel-head"><div><b>当前在线来源</b>' +
        '<p class="db-meta">按 IP 汇总的实时连接，可直接封禁或加入白名单</p></div></div>' +
        ((d.online_ip || []).length ? '<div class="tablewrap"><table><thead><tr><th>来源 IP</th><th>连接数</th><th>在线用户</th><th>操作</th></tr></thead><tbody>' +
          d.online_ip.map(o => '<tr><td class="db-cell">' + UI.esc(o.ip) + '</td><td>' + o.n + '</td>' +
            '<td><small>' + UI.esc(o.users || '未登录') + '</small></td>' +
            '<td><div class="row-actions"><button class="btn sm" onclick="Admin.wlAdd(\'' + UI.esc(o.ip).replace(/'/g, "\\'") + '\')">加入白名单</button>' +
            '<button class="btn sm danger" onclick="Admin.ipblQuick(\'' + UI.esc(o.ip).replace(/'/g, "\\'") + '\')">封禁此 IP</button></div></td></tr>').join('') +
          '</tbody></table></div>' : '<div class="empty">暂无在线 WebSocket 连接</div>') +
        '</section>';
    },

    async saveWhitelist() {
      const v = (document.getElementById('ip-wl').value || '').trim();
      const r = await API.put('/api/admin/settings', { settings: { ip_whitelist: v } });
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) this.render();
    },
    clearWhitelist() {
      const el = document.getElementById('ip-wl');
      if (el) el.value = '';
      this.saveWhitelist();
    },
    wlAdd(ip) {
      const el = document.getElementById('ip-wl');
      if (!el) return;
      const items = el.value.split(',').map(x => x.trim()).filter(Boolean);
      if (items.indexOf(ip) < 0) items.push(ip);
      el.value = items.join(', ');
      UI.toast('已加入白名单输入框，点「保存白名单」生效', 'ok');
    },
    ipblToggle(on) {
      UI.confirm(on ? '启用后黑名单规则立即生效？' : '停用后所有 IP 封禁规则暂时失效（规则保留），确定？', async () => {
        const r = await API.post('/api/admin/ipblacklist/toggle', { on: on });
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      });
    },
    ipblQuick(ip) {
      this._ipblPrefill = ip; this.ipblAddUI();
    },
    ipblAddUI() {
      const pre = this._ipblPrefill || ''; this._ipblPrefill = '';
      UI.modal('加入 IP 黑名单',
        '<label class="field"><span>封禁规则 *</span>' +
        '<input id="ib-pattern" placeholder="192.168.1.23 / 192.168.1.0/24 / 10.20.*" value="' + UI.esc(pre) + '" style="font-family:monospace"></label>' +
        '<label class="field"><span>封禁时长</span><select id="ib-hours">' +
        '<option value="0">永久（需手动移出）</option><option value="1">1 小时</option>' +
        '<option value="8">8 小时</option><option value="24">1 天</option>' +
        '<option value="168">7 天</option><option value="720">30 天</option></select></label>' +
        '<label class="field"><span>原因（可选，≤50 字，写入审计日志与全员通知）</span>' +
        '<input id="ib-reason" maxlength="50" placeholder="如：恶意刷登录、违规外联"></label>' +
        '<p style="font-size:13px;color:var(--text-2);margin:6px 0">⚠️ 封禁网段会影响该网段下<b>全部电脑</b>，' +
        '请确认网段范围；已在线的连接会被立即踢下线，本机地址无法被封。</p>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn danger" onclick="Admin.ipblAdd()">确认封禁</button>', 480);
      setTimeout(() => { const i = document.getElementById('ib-pattern'); if (i) i.focus(); }, 60);
    },
    async ipblAdd() {
      const pattern = UI.val('ib-pattern');
      if (!pattern) return UI.toast('请填写封禁规则', 'err');
      const r = await API.post('/api/admin/ipblacklist', {
        pattern: pattern, reason: UI.val('ib-reason'), hours: parseInt(document.getElementById('ib-hours').value, 10)
      });
      UI.closeModal();
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    ipblRemove(id, pattern) {
      UI.confirm('将 ' + pattern + ' 移出黑名单，该来源立即恢复访问？', async () => {
        const r = await API.post('/api/admin/ipblacklist/' + id + '/remove', {});
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      });
    },

    /* ---------- 日志 ---------- */
    async logs() {
      const f = this.filters;
      const r = await API.get('/api/admin/logs', Object.assign({ page: this.page, page_size: 30 }, f));
      const box = document.getElementById('admin-body');
      box.innerHTML =
        '<div class="toolbar"><input id="lg-kw" placeholder="搜索操作/对象/详情" value="' + UI.esc(f.q || '') + '" onkeydown="if(event.key===\'Enter\')Admin.setFilter(\'q\',this.value)">' +
        '<input id="lg-actor" placeholder="操作人" value="' + UI.esc(f.actor || '') + '" onkeydown="if(event.key===\'Enter\')Admin.setFilter(\'actor\',this.value)">' +
        '<input type="date" id="lg-date" value="' + UI.esc(f.date || '') + '" onchange="Admin.setFilter(\'date\',this.value)">' +
        '<button class="btn" onclick="Admin.setFilter(\'q\',document.getElementById(\'lg-kw\').value);Admin.filters.actor=document.getElementById(\'lg-actor\').value;Admin.render()">查询</button></div>' +
        this._batchbar('logs') +
        '<div class="panel"><table><thead><tr><th class="ckcol"></th><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead><tbody>' +
        ((r.ok ? r.data.list : []) || []).map(l => '<tr><td>' + this._ck('logs', l.id) + '</td><td><small>' + UI.esc(l.time) + '</small></td><td>' + UI.esc(l.actor) + '</td>' +
          '<td><span class="tag gray">' + UI.esc(l.action) + '</span></td><td>' + UI.esc(l.target || '') + '</td>' +
          '<td><span class="ellip" title="' + UI.esc(l.detail || '') + '">' + UI.esc(l.detail || '') + '</span></td><td><small>' + UI.esc(l.ip || '') + '</small></td></tr>').join('') +
        '</tbody></table>' + '</div>' + UI.pager(r.ok ? r.data.total : 0, this.page, 30, 'Admin.goPage');
    },

    /* ---------- 设置 ---------- */
    _sec: 'site',
    async settings() {
      const [s, w, b] = await Promise.all([
        API.get('/api/admin/settings'), API.get('/api/admin/sensitive'), API.get('/api/admin/backups')
      ]);
      const box = document.getElementById('admin-body');
      const st = (s.ok ? s.data.settings : {}) || {};
      const words = (w.ok ? w.data.list : []) || [];
      const bks = (b.ok ? b.data.list : []) || [];
      const row = (k, label, type, opts) => {
        const v = st[k];
        if (type === 'bool') return '<label class="field"><span>' + label + '</span><select id="st-' + k + '">' +
          '<option value="true"' + (v ? ' selected' : '') + '>开启</option><option value="false"' + (!v ? ' selected' : '') + '>关闭</option></select></label>';
        if (type === 'select') return '<label class="field"><span>' + label + '</span><select id="st-' + k + '">' +
          opts.map(o => '<option value="' + o[0] + '"' + (String(v) === o[0] ? ' selected' : '') + '>' + o[1] + '</option>').join('') + '</select></label>';
        return '<label class="field"><span>' + label + '</span><input id="st-' + k + '" value="' + UI.esc(v === undefined || v === null ? '' : v) + '"></label>';
      };
      const sec = (key, title, desc, body) =>
        '<section class="panel settings-sec" data-sec="' + key + '"><div class="panel-head"><div><b>' + title + '</b>' +
        (desc ? '<p class="db-meta">' + desc + '</p>' : '') + '</div></div><div class="panel-body">' + body + '</div></section>';
      const secs = [['site', '站点信息'], ['account', '注册与账号'], ['security', '安全与访问'],
        ['chat', '聊天与文件'], ['feature', '功能开关'], ['maintain', '维护与备份']];

      box.innerHTML =
        '<div class="settings-tabs" id="settings-tabs">' +
        secs.map(x => '<button class="settings-tab" type="button" data-sec="' + x[0] + '" onclick="Admin.setSec(\'' + x[0] + '\')">' + x[1] + '</button>').join('') +
        '</div>' +
        sec('site', '站点信息', '展示在登录页、侧边栏与页面标题中的企业信息',
          '<div class="grid2">' + row('site_name', '系统名称', 'text') + row('site_slogan', '副标题 / 标语', 'text') +
          row('welcome', '登录页欢迎语', 'text') + row('icp_text', '页脚说明', 'text') + '</div>' +
          '<div class="theme-picker-field"><span class="theme-picker-label">主题配色（点选立即生效，全站同步；登录页也会跟着变）</span>' +
          '<div class="theme-picker" id="theme-picker">' +
          [['blue', '云聊蓝', '蓝色主色 · 稳重通用', 'swatch-blue'],
           ['bluepink', '蓝粉', '蓝 → 粉渐变品牌色', 'swatch-bluepink']].map(x =>
            '<button type="button" class="theme-swatch' + ((st.theme_color || 'blue') === x[0] ? ' active' : '') +
            '" data-accent="' + x[0] + '" onclick="Admin.setThemeColor(\'' + x[0] + '\')">' +
            '<span class="swatch-preview ' + x[3] + '"></span><b>' + x[1] + '</b><small>' + x[2] + '</small></button>').join('') +
          '</div><input type="hidden" id="st-theme_color" value="' + UI.esc(st.theme_color || 'blue') + '"></div>') +
        sec('account', '注册与账号', '自助注册、默认角色、密码策略与登录保护',
          '<div class="grid2">' + row('allow_register', '开放自助注册', 'bool') + row('register_need_approval', '注册需审核', 'bool') +
          row('default_role', '注册默认角色', 'select', [['student', '学员'], ['teacher', '讲师']]) +
          row('profile_change_need_approval', '头衔修改需审核（昵称始终可自改）', 'bool') +
          row('password_min_len', '密码最小长度', 'text') + row('password_need_complex', '密码复杂度要求', 'bool') +
          row('max_failed_login', '最大登录失败次数', 'text') + row('lock_minutes', '锁定时长（分钟）', 'text') +
          row('session_hours', '会话有效期（小时）', 'text') + '</div>') +
        sec('security', '安全与访问', '反向代理、域名绑定与 HTTPS 证书（IP 白名单/黑名单已独立到「IP 访问控制」）',
          '<div class="grid2">' + row('trust_real_ip', '信任反向代理 X-Real-IP（仅当部署在 Nginx 等代理之后才开启）', 'bool') + '</div>' +
          '<div class="settings-sub"><b>域名绑定与 HTTPS</b>' +
          '<p class="db-meta">绑定域名后仅名单中的 Host 被接受（留空 = 允许任意 Host）。开启 HTTPS 后除 HTTP 端口外再监听一个加密端口，' +
          '接口与 WebSocket 自动走 https / wss。<b>修改后需重启服务端</b>才生效。</p>' +
          '<div class="grid2">' + row('server_names', '绑定域名（逗号分隔，如 chat.example.com）', 'text') +
          row('https_enabled', '启用 HTTPS', 'bool') +
          row('https_port', 'HTTPS 端口（默认 8443）', 'text') +
          row('cert_file', '证书链文件（fullchain.pem）', 'text') +
          row('key_file', '私钥文件（privkey.key）', 'text') + '</div>' +
          '<div class="settings-actions"><button class="btn" onclick="Admin.useBuiltinCert()">使用内置证书</button>' +
          '<span class="tag ok">内置：chat.example.com · Let\'s Encrypt · 2027-01-25 到期</span>' +
          '<span class="db-meta">相对路径按程序目录解析（安装包内的 cert/ 目录）</span></div></div>') +
        sec('chat', '聊天与文件', '消息长度、撤回时限、上传限制与敏感词',
          '<div class="grid2">' + row('max_msg_len', '单条消息最大字数', 'text') + row('allow_recall_minutes', '允许撤回时限（分钟）', 'text') +
          row('history_page_size', '历史消息每页条数', 'text') + row('max_upload_mb', '单文件上限（MB）', 'text') +
          row('enable_sensitive_filter', '敏感词过滤', 'bool') + row('sensitive_replace', '敏感词替换符', 'text') + '</div>' +
          '<label class="field" style="margin-top:8px"><span>允许上传的扩展名（逗号分隔）</span>' +
          '<textarea id="st-allow_ext" class="settings-area">' + UI.esc(st.allow_ext || '') + '</textarea></label>' +
          '<div class="settings-sub"><b>敏感词（' + words.length + '）</b>' +
          '<div class="settings-actions"><input id="sw-word" placeholder="输入敏感词，回车添加" onkeydown="if(event.key===\'Enter\')Admin.addWord()">' +
          '<button class="btn primary" onclick="Admin.addWord()">添加</button></div>' +
          '<div class="settings-words">' + (words.map(x => '<span class="tag gray wordchip" onclick="Admin.delWord(' + x.id + ')">' +
            UI.esc(x.word) + ' ✕</span>').join('') || '<span class="db-meta">暂无敏感词</span>') + '</div></div>') +
        sec('studycenter', '学习中心 · 题库平台', '把知否刷题平台嵌进学习中心，账号一键登录（不再单独输密码）',
          '<div class="settings-sub"><b>题库平台（知否）</b>' +
          '<p class="db-meta">知否是和云聊同机部署的独立刷题服务（Node，默认 8080 端口）。开启后学生可在' +
          '「学习中心 → 题库刷题」直接进入，云聊会用自己的账号做单点登录，' +
          '<b>题库数据仍留在知否自己的库中</b>，两边不共库。' +
          '共享密钥必须与知否启动时的 <code>ZHIFOU_SSO_SECRET</code> 环境变量完全一致。</p>' +
          '<div class="grid2">' + row('study_center_enabled', '启用题库平台', 'bool') +
          row('study_center_url', '题库平台地址（如 http://127.0.0.1:8080）', 'text') +
          row('study_center_secret', '单点登录共享密钥', 'text') +
          row('study_center_user_prefix', '用户名前缀剥离（可留空）', 'text') + '</div>' +
          '<div class="settings-actions">' +
          '<button class="btn" onclick="Admin.testStudyCenter()">测试连接</button>' +
          '<span id="sc-test-result" class="db-meta">填写后点「测试连接」检查是否可达</span></div>' +
          '<p class="db-meta" style="margin-top:10px">' +
          '· <b>用户名前缀剥离</b>：云聊账号常是 <code>ks20240001</code>，题库登录名是 <code>20240001</code>，' +
          '这里填 <code>ks</code> 即可自动对应；也可在用户资料的「学号」字段里直接填题库登录名，优先级更高。<br>' +
          '· 知否需要用命令 <code>set ZHIFOU_SSO_SECRET=你的密钥 && node server.js</code> 启动，未设置密钥时该功能不可用。<br>' +
          '· 修改后<b>需重启云聊</b>才生效。' +
          '</p></div>') +
        sec('feature', '功能开关', '小游戏与远程协助等高危 / 可选能力，默认关闭',
          '<div class="settings-sub"><b>小游戏</b>' +
          '<p class="db-meta">默认全部关闭；开启总开关并勾选具体游戏后才可访问，「显示入口」决定普通用户能否在左侧导航看到。</p>' +
          '<div class="grid2">' + row('enable_games', '小游戏总开关', 'bool') +
          row('game_show_nav', '在侧边栏显示小游戏入口', 'bool') +
          row('game_fireboy', '森林冰火人（双人联机协作）', 'bool') +
          row('game_chess', '中国象棋（AI / 本地双人）', 'bool') + '</div></div>' +
          '<div class="settings-sub"><b>远程协助（屏幕共享与远程控制）</b>' +
          '<p class="db-meta">高危能力：员工本机确认后才可建立，被控端可随时断开，会话超时自动结束。</p>' +
          '<div class="grid2">' + row('enable_remote_assist', '远程协助总开关', 'bool') +
          row('remote_fps', '画面帧率（1~30）', 'text') + row('remote_quality', '画面质量（10~95）', 'text') +
          row('remote_max_minutes', '单次最长时长（分钟）', 'text') + '</div></div>') +
        sec('maintain', '维护与备份', '数据库热备份、恢复与维护模式',
          '<div class="settings-actions"><button class="btn primary" onclick="Admin.backup()">立即备份数据库</button>' +
          '<button class="btn" onclick="Admin.maintenance(1)">开启维护模式</button>' +
          '<button class="btn" onclick="Admin.maintenance(0)">关闭维护模式</button>' +
          '<span class="db-meta">备份目录：' + UI.esc((b.ok ? b.data.dir : '') || '') + '</span></div>' +
          '<div class="tablewrap"><table><thead><tr><th>备份文件</th><th>大小</th><th>时间</th><th>操作</th></tr></thead><tbody>' +
          (bks.length ? bks.map(x => '<tr><td>' + UI.esc(x.name) + '</td><td>' + UI.esc(x.size_str) + '</td><td>' + UI.esc(x.time) + '</td><td><div class="row-actions">' +
            '<a class="btn sm" href="/api/admin/backup/download?name=' + encodeURIComponent(x.name) + '&token=' + API.token + '">下载</a>' +
            '<button class="btn sm danger" onclick="Admin.restore(\'' + UI.esc(x.name) + '\')">恢复</button></div></td></tr>').join('')
            : '<tr><td colspan="4"><div class="empty">暂无备份</div></td></tr>') +
          '</tbody></table></div>') +
        '<div class="settings-save"><span class="db-meta">修改后点击右侧按钮保存全部设置</span>' +
        '<button class="btn primary" onclick="Admin.saveSettings()">保存全部设置</button></div>';
      this.setSec(this._sec || 'site');
      this.setThemeColor(st.theme_color || 'blue', true);
    },
    setSec(sec) {
      this._sec = sec;
      document.querySelectorAll('.settings-tab').forEach(b => b.classList.toggle('active', b.dataset.sec === sec));
      document.querySelectorAll('.settings-sec').forEach(p => { p.style.display = p.dataset.sec === sec ? '' : 'none'; });
    },
    /* 站点配色：点选立即保存并全站生效（不需要再点保存按钮） */
    async setThemeColor(v, silent) {
      const el = document.getElementById('st-theme_color');
      if (el) el.value = v;
      UI.applyAccent(v);
      document.querySelectorAll('.theme-swatch').forEach(b => b.classList.toggle('active', b.dataset.accent === v));
      if (silent) { this._themeSaved = v; return; }
      const r = await API.put('/api/admin/settings', { settings: { theme_color: v } });
      const got = (r.data && r.data.settings) || {};
      if (r.ok && String(got.theme_color || '') === String(v)) {
        this._themeSaved = v;
        UI.toast('已切换为「' + (v === 'bluepink' ? '蓝粉' : '云聊蓝') + '」配色，全站生效', 'ok');
        try { await Auth.loadMe(); } catch (e) { }
      } else {
        const back = this._themeSaved || 'blue';
        UI.applyAccent(back);
        document.querySelectorAll('.theme-swatch').forEach(b => b.classList.toggle('active', b.dataset.accent === back));
        if (el) el.value = back;
        UI.toast('配色未能保存：服务端版本较旧，请重启服务端后再试', 'err');
      }
    },
    /* 一键填入随包附带的内置证书（chat.example.com） */
    useBuiltinCert() {
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
      set('st-cert_file', 'cert/Nginx/fullchain.pem');
      set('st-key_file', 'cert/Nginx/privkey.key');
      set('st-https_enabled', 'true');
      if (!UI.val('st-https_port')) set('st-https_port', '8443');
      UI.toast('已填入内置证书并开启 HTTPS，保存后重启服务端生效', 'ok');
    },

    /* 题库平台连通性自检：先存一次配置，再让后端去 ping 知否 */
    async testStudyCenter() {
      const el = document.getElementById('sc-test-result');
      const say = (txt, cls) => {
        if (!el) return;
        el.textContent = txt;
        el.className = 'db-meta' + (cls ? ' ' + cls : '');
      };
      say('正在测试…');
      /* 后端读的是已保存的配置，所以先把当前表单里的几项存下来 */
      const data = {};
      ['study_center_enabled', 'study_center_url', 'study_center_secret', 'study_center_user_prefix']
        .forEach(k => {
          const box = document.getElementById('st-' + k);
          if (!box) return;
          let v = box.value;
          if (v === 'true') v = true; else if (v === 'false') v = false;
          data[k] = v;
        });
      const sv = await API.put('/api/admin/settings', { settings: data });
      if (!sv.ok) { say('保存失败：' + (sv.msg || ''), 'err'); return; }
      const r = await API.get('/api/learn/lab/status');
      if (!r.ok) { say('测试失败：' + (r.msg || ''), 'err'); return; }
      const d = r.data || {};
      if (!d.enabled) { say('未启用：请勾选「启用题库平台」并填写地址后保存', 'err'); return; }
      if (!d.reachable) { say('连不上 ' + d.url + '：' + (d.msg || '请确认知否服务已启动'), 'err'); return; }
      if (!d.has_secret) { say('地址可达，但还没填共享密钥，单点登录会用不了', 'err'); return; }
      say('连接正常：' + d.url + '（密钥已配置）', 'ok');
    },

    async saveSettings() {
      const data = {};
      document.querySelectorAll('[id^="st-"]').forEach(el => {
        const k = el.id.slice(3);
        let v = el.value;
        if (v === 'true') v = true; else if (v === 'false') v = false;
        else if (/^(password_min_len|max_failed_login|lock_minutes|session_hours|max_msg_len|allow_recall_minutes|history_page_size|max_upload_mb|remote_fps|remote_quality|remote_max_minutes|https_port)$/.test(k)) v = parseInt(v, 10) || 0;
        data[k] = v;
      });
      const wantTheme = data.theme_color;
      const r = await API.put('/api/admin/settings', { settings: data });
      if (!r.ok) { UI.toast(r.msg || '保存失败', 'err'); return; }
      const got = (r.data && r.data.settings) || {};
      /* 服务端回读校验：老版本服务端会静默丢弃 theme_color，这里明确提示 */
      if (wantTheme && String(got.theme_color || '') !== String(wantTheme)) {
        UI.toast('配色没有保存成功：服务端版本较旧，请重启服务端后再试', 'err');
      } else if (wantTheme) {
        UI.toast('已保存，全站配色：' + (wantTheme === 'bluepink' ? '蓝粉' : '云聊蓝'), 'ok');
      } else {
        UI.toast(r.msg, 'ok');
      }
      await Auth.loadMe();
      App.renderBrand();    },
    async addWord() {
      const w = UI.val('sw-word');
      if (!w) return;
      const r = await API.post('/api/admin/sensitive', { word: w });
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    async delWord(id) {
      const r = await API.del('/api/admin/sensitive/' + id);
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    async backup() {
      const r = await API.post('/api/admin/backup', {});
      UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
    },
    async restore(name) {
      UI.confirm('恢复将覆盖当前全部数据（当前数据会自动备份），确定继续？', async () => {
        const r = await API.post('/api/admin/backup/restore', { name: name });
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      }, true);
    },
    maintenance(on) {
      if (!on) return this._maintenance('', false);
      UI.prompt({ title: '开启系统维护', label: '维护提示语', value: '系统维护中，请稍后访问。',
        okText: '开启维护', onOk: note => this._maintenance(note || '系统维护中，请稍后访问。', true) });
    },
    async _maintenance(note, on) {
      const r = await API.post('/api/admin/maintenance', { enable: on, note: note });
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
    }
  };

  window.Admin = Admin;
})();
