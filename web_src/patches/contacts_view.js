    /* ---------- 通讯录（对齐 实例页面/yunliao-contacts.html） ---------- */
    async contactsView() {
      const box = document.getElementById('contacts-body');
      box.innerHTML = '<div class="empty">加载中…</div>';
      const r = await API.get('/api/users');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      this.contacts = r.data.list || [];
      const groups = {};
      this.contacts.forEach(u => { const d = u.dept || '未分配'; (groups[d] = groups[d] || []).push(u); });
      this._ctDepts = Object.keys(groups).sort();
      if (!this._ctDept || (this._ctDept !== '全部联系人' && this._ctDepts.indexOf(this._ctDept) < 0)) this._ctDept = '全部联系人';
      const online = this.contacts.filter(u => u.online).length;
      box.innerHTML =
        '<section class="summary">' +
          '<div class="sum"><div class="sum-ico">♧</div><div><b>' + this.contacts.length + '</b><small>全部联系人</small></div></div>' +
          '<div class="sum"><div class="sum-ico pink">✦</div><div><b>' + online + '</b><small>当前在线</small></div></div>' +
          '<div class="sum"><div class="sum-ico purple">▦</div><div><b>' + this._ctDepts.length + '</b><small>组织部门</small></div></div>' +
        '</section>' +
        '<div class="ct-grid">' +
          '<section class="panel ct-dept"><div class="panel-head"><h2>组织架构</h2><span class="muted">按部门查看</span></div>' +
            '<div class="departments" id="ct-depts">' +
              '<button class="dept active" type="button" data-dept="全部联系人"><span class="left"><span class="glyph">◉</span>全部联系人</span><span class="count">' + this.contacts.length + '</span></button>' +
              this._ctDepts.map(d => '<button class="dept" type="button" data-dept="' + UI.esc(d) + '"><span class="left"><span class="glyph">✧</span>' + UI.esc(d) + '</span><span class="count">' + groups[d].length + '</span></button>').join('') +
            '</div></section>' +
          '<section class="panel ct-list"><div class="panel-head"><h2 id="ct-title">' + UI.esc(this._ctDept) + '</h2><span class="muted">成员列表</span></div>' +
            '<div class="ct-tools"><label class="search" for="ct-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg>' +
            '<input id="ct-search" placeholder="搜索姓名、部门或职位…" autocomplete="off"></label>' +
            '<button class="btn primary" type="button" onclick="App.newContactHint()">＋ 添加联系人</button></div>' +
            '<div class="ct-tablewrap"><table><thead><tr><th>联系人</th><th>部门</th><th>职位</th><th>状态</th><th>操作</th></tr></thead><tbody id="ct-rows"></tbody></table>' +
            '<div class="empty" id="ct-empty" style="display:none">没有找到匹配的联系人</div></div>' +
            '<div class="ct-foot"><span id="ct-showing"></span><span>云聊通讯录 · 按部门查看成员</span></div>' +
          '</section>' +
        '</div>';
      const srch = document.getElementById('ct-search');
      srch.addEventListener('input', () => this.renderContacts());
      document.getElementById('ct-depts').addEventListener('click', e => {
        const b = e.target.closest('[data-dept]');
        if (!b) return;
        this._ctDept = b.dataset.dept;
        document.querySelectorAll('#ct-depts .dept').forEach(x => x.classList.toggle('active', x === b));
        document.getElementById('ct-title').textContent = this._ctDept;
        this.renderContacts();
      });
      this.renderContacts();
      CtxMenu.attach(box, 'tr[data-uid]', el => this.contactMenu(+el.dataset.uid));
    },
    newContactHint() { UI.toast('添加联系人请在「管理后台 → 用户管理 → 新建用户」中创建', 'err'); },
    renderContacts() {
      const box = document.getElementById('ct-rows');
      if (!box) return;
      const kw = (document.getElementById('ct-search').value || '').trim().toLowerCase();
      const dept = this._ctDept || '全部联系人';
      let list = (this.contacts || []).filter(u => dept === '全部联系人' || (u.dept || '未分配') === dept);
      if (kw) list = list.filter(u => [u.display_name, u.username, u.dept, u.position, u.email]
        .some(x => String(x || '').toLowerCase().indexOf(kw) >= 0));
      box.innerHTML = list.map(u =>
        '<tr data-uid="' + u.id + '"><td><div class="person">' + UI.avatar(u.display_name, u.color, 0, u.avatar) +
          '<div><strong>' + UI.esc(u.display_name) + '</strong><small>@' + UI.esc(u.username) + '</small></div></div></td>' +
        '<td>' + UI.esc(u.dept || '未分配') + '</td>' +
        '<td>' + UI.esc(u.position || '—') + '</td>' +
        '<td><span class="pstate ' + (u.online ? '' : 'off') + '">' + (u.online ? '在线' : '离线') + '</span></td>' +
        '<td><button class="ct-action" type="button" onclick="Chat.startDM(' + u.id + ')">发消息 ↗</button></td></tr>').join('');
      const em = document.getElementById('ct-empty');
      if (em) em.style.display = list.length ? 'none' : 'block';
      const sh = document.getElementById('ct-showing');
      if (sh) sh.textContent = '显示 ' + list.length + ' 位联系人';
    },
    filterContacts() { this.renderContacts(); },
    /* 联系人右键菜单 */
    contactMenu(id) {
      const u = (this.contacts || []).filter(x => x.id === id)[0];
      if (!u) return [];
      const copy = t => { this.copyText(t); };
      return [
        { label: '发消息', icon: 'open', onClick: () => Chat.startDM(u.id) },
        { label: '查看组织信息', icon: 'users', onClick: () => UI.modal(UI.esc(u.display_name),
            '<div class="grid2">' +
            '<div class="field"><span>账号</span><b>@' + UI.esc(u.username) + '</b></div>' +
            '<div class="field"><span>部门</span><b>' + UI.esc(u.dept || '未分配') + '</b></div>' +
            '<div class="field"><span>岗位</span><b>' + UI.esc(u.position || '—') + '</b></div>' +
            '<div class="field"><span>状态</span><b>' + (u.online ? '在线' : '离线') + '</b></div>' +
            (u.email ? '<div class="field"><span>邮箱</span><b>' + UI.esc(u.email) + '</b></div>' : '') +
            (u.phone ? '<div class="field"><span>手机</span><b>' + UI.esc(u.phone) + '</b></div>' : '') +
            '</div>', '<button class="btn" onclick="UI.closeModal()">关闭</button>', 460) },
        { sep: true },
        { label: '复制账号', icon: 'copy', onClick: () => copy(u.username) },
        { label: '复制姓名', icon: 'copy', onClick: () => copy(u.display_name) },
        u.email ? { label: '复制邮箱', icon: 'copy', onClick: () => copy(u.email) } : null
      ].filter(Boolean);
    },
    copyText(t) {
      const done = () => UI.toast('已复制：' + t, 'ok');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(String(t)).then(done).catch(() => this.copyTextFallback(t, done));
        return;
      }
      this.copyTextFallback(t, done);
    },
    copyTextFallback(t, done) {
      const ta = document.createElement('textarea');
      ta.value = String(t); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { UI.toast('复制失败，请手动复制', 'err'); }
      ta.remove();
    },
