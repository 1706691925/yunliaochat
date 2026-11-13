/* ============ 文件中心 ============ */
(function () {
  'use strict';

  const KIND_LABEL = { center: '文件中心', chat: '聊天附件', material: '资料库', avatar: '头像', assignment: '作业', course: '课程' };
  const IMAGE = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico'];
  const DOC = ['doc', 'docx', 'pdf', 'txt', 'md', 'log', 'rtf', 'ppt', 'pptx', 'csv', 'json', 'xml', 'html'];
  const SHEET = ['xls', 'xlsx'];
  const ARCHIVE = ['zip', 'rar', '7z', 'tar', 'gz'];
  const MEDIA = ['mp4', 'mp3', 'wav', 'avi', 'mov', 'mkv', 'flac'];

  function extOf(name) {
    const n = String(name || '');
    return n.indexOf('.') >= 0 ? n.split('.').pop().toLowerCase() : '';
  }
  function groupOf(name) {
    const e = extOf(name);
    if (IMAGE.indexOf(e) >= 0) return 'image';
    if (SHEET.indexOf(e) >= 0) return 'sheet';
    if (DOC.indexOf(e) >= 0) return 'doc';
    if (ARCHIVE.indexOf(e) >= 0) return 'archive';
    if (MEDIA.indexOf(e) >= 0) return 'media';
    return 'other';
  }
  function kindOf(name) {
    const g = groupOf(name);
    if (g === 'image') return 'im';
    if (g === 'sheet') return 'xl';
    if (g === 'archive') return 'zip';
    if (g === 'media') return 'av';
    if (extOf(name) === 'pdf') return 'pdf';
    return 'doc';
  }

  const Files = {
    page: 1, size: 20, kw: '', group: 'all', kind: '',
    all: [], total: 0, users: {}, loaded: false, dragging: false,

    async render() {
      const box = document.getElementById('files-body');
      if (!this.loaded) box.innerHTML = '<div class="empty">加载中…</div>';
      const [fr, ur] = await Promise.all([
        API.get('/api/files/center'),
        this.loaded ? Promise.resolve(null) : API.get('/api/users')
      ]);
      if (!fr.ok) { box.innerHTML = '<div class="empty">' + UI.esc(fr.msg) + '</div>'; return; }
      this.all = fr.data.list || [];
      this.total = fr.data.total || 0;
      this.canPin = !!fr.data.can_pin;
      if (ur && ur.ok) {
        this.users = {};
        (ur.data.list || []).forEach(u => { this.users[u.id] = u; });
      }
      this.loaded = true;
      this.renderShell(box);
      this.renderRows();
    },

    renderShell(box) {
      const sum = this.all.reduce((a, f) => a + (f.size || 0), 0);
      const images = this.all.filter(f => groupOf(f.name) === 'image').length;
      const mats = this.all.filter(f => f.kind === 'material').length;
      box.innerHTML =
        '<section class="summary">' +
          '<div class="sum"><div class="sum-ico">▤</div><div><b id="fc-total">' + this.total + '</b><small>全部文件</small></div></div>' +
          '<div class="sum"><div class="sum-ico pink">▦</div><div><b id="fc-size">' + UI.size(sum) + '</b><small>占用空间</small></div></div>' +
          '<div class="sum"><div class="sum-ico purple">✦</div><div><b id="fc-mats">' + images + ' / ' + mats + '</b><small>图片 / 资料</small></div></div>' +
        '</section>' +
        '<div class="panel file-panel" id="fc-drop">' +
          '<div class="panel-head"><h2>文件列表</h2><span class="muted" id="fc-count"></span></div>' +
          '<div class="ct-tools"><div class="fc-filters">' +
            '<label class="search" for="fc-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg>' +
            '<input id="fc-search" placeholder="搜索文件名、类型或上传者…" autocomplete="off"></label>' +
            '</div>' +
            '<button class="btn primary" type="button" onclick="Files.pick()">＋ 上传文件</button>' +
          '</div>' +
          '<div class="chiprow" id="fc-groups">' +
            [['all', '全部'], ['image', '图片'], ['doc', '文档'], ['sheet', '表格'], ['archive', '压缩包'], ['media', '音视频'], ['other', '其他']]
              .map(g => '<button class="chip' + (this.group === g[0] ? ' active' : '') + '" type="button" data-group="' + g[0] + '">' + g[1] + '</button>').join('') +
          '</div>' +
          '<div class="ct-tablewrap"><table class="filetable"><thead><tr><th>文件</th><th>大小</th><th>上传者</th><th>时间</th><th>下载</th><th>操作</th></tr></thead>' +
          '<tbody id="fc-rows"></tbody></table><div class="empty" id="fc-empty" style="display:none">没有找到文件</div></div>' +
          '<div class="ct-foot"><span id="fc-showing"></span><span id="fc-page"></span></div>' +
          '<div class="filedrop">松开即可上传到文件中心</div>' +
        '</div>' +
        '<input type="file" id="fc-file" multiple style="display:none" onchange="Files.onFile(event)">';

      const srch = document.getElementById('fc-search');
      srch.value = this.kw;
      srch.addEventListener('input', () => { this.kw = srch.value; this.page = 1; this.renderRows(); });
      document.getElementById('fc-groups').addEventListener('click', e => {
        const b = e.target.closest('[data-group]');
        if (!b) return;
        this.group = b.dataset.group; this.page = 1;
        document.querySelectorAll('#fc-groups .chip').forEach(c => c.classList.toggle('active', c === b));
        this.renderRows();
      });
      this.bindDrop();
      CtxMenu.attach(document.getElementById('fc-rows'), 'tr[data-fid]', el => this.menuFor(+el.dataset.fid));
    },

    bindDrop() {
      const zone = document.getElementById('fc-drop');
      if (!zone) return;
      ['dragenter', 'dragover'].forEach(t => zone.addEventListener(t, e => {
        if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') < 0) return;
        e.preventDefault(); zone.classList.add('dragging');
      }));
      ['dragleave', 'dragend'].forEach(t => zone.addEventListener(t, e => {
        if (e.target === zone) zone.classList.remove('dragging');
      }));
      zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('dragging');
        const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
        if (files.length) this.upload(files);
      });
    },

    visible() {
      const kw = (this.kw || '').trim().toLowerCase();
      return this.all.filter(f => {
        if (this.group !== 'all' && groupOf(f.name) !== this.group) return false;
        if (!kw) return true;
        const u = this.users[f.uploader_id] || {};
        const hay = [f.name, KIND_LABEL[f.kind] || f.kind, u.display_name || '', extOf(f.name)].join(' ').toLowerCase();
        return hay.indexOf(kw) >= 0;
      });
    },

    renderRows() {
      const rows = document.getElementById('fc-rows');
      if (!rows) return;
      const list = this.visible();
      const pages = Math.max(1, Math.ceil(list.length / this.size));
      if (this.page > pages) this.page = pages;
      const start = (this.page - 1) * this.size;
      const pageList = list.slice(start, start + this.size);

      rows.innerHTML = pageList.map(f => {
        const u = this.users[f.uploader_id] || {};
        const mine = API.user && f.uploader_id === API.user.id;
        const canDel = mine || (API.user.perms || []).indexOf('material.manage') >= 0;
        const url = f.url + '?token=' + encodeURIComponent(API.token || '');
        const isImg = groupOf(f.name) === 'image';
        return '<tr data-fid="' + f.id + '"' + (f.pinned ? ' class="pinned-row"' : '') + '>' +
          '<td><div class="filecell"><span class="fext ' + kindOf(f.name) + '">' + (extOf(f.name) || 'FILE').slice(0, 4).toUpperCase() + '</span>' +
            '<div><strong title="' + UI.esc(f.name) + '">' + UI.esc(f.name) + '</strong>' +
            '<small>' + ({ image: '图片', doc: '文档', sheet: '表格', archive: '压缩包', media: '音视频', other: '其他' }[groupOf(f.name)]) +
            (isImg ? ' · 可点击预览' : '') +
            (f.pinned ? ' · <span class="pin-tag">📌 置顶</span>' : '') + '</small></div></div></td>' +
          '<td>' + UI.esc(f.size_str) + '</td>' +
          '<td>' + UI.esc(u.display_name || (mine ? '我' : '—')) + '</td>' +
          '<td><small>' + UI.esc((f.created_str || '').slice(0, 16)) + '</small></td>' +
          '<td>' + (f.downloads || 0) + '</td>' +
          '<td><div class="row-actions">' +
            '<a class="btn sm" href="' + url + '" target="_blank" rel="noopener"' + (isImg ? '' : ' download') + '>下载</a>' +
            '<button class="btn sm" type="button" onclick="Files.copyLink(' + f.id + ')">链接</button>' +
            (this.canPin ? '<button class="btn sm" type="button" onclick="Files.togglePin(' + f.id + ',' + (f.pinned ? 0 : 1) + ')">' + (f.pinned ? '取消置顶' : '置顶') + '</button>' : '') +
            (canDel ? '<button class="btn sm danger" type="button" onclick="Files.del(' + f.id + ')">删除</button>' : '') +
          '</div></td></tr>';
      }).join('');

      const empty = document.getElementById('fc-empty');
      if (empty) empty.style.display = list.length ? 'none' : 'block';
      const count = document.getElementById('fc-count');
      if (count) count.textContent = '共 ' + list.length + ' 个' + (this.total > this.all.length ? '（仅显示最近 ' + this.all.length + ' 个）' : '');
      const showing = document.getElementById('fc-showing');
      if (showing) showing.textContent = list.length
        ? '显示第 ' + (start + 1) + ' - ' + (start + pageList.length) + ' 个，共 ' + list.length + ' 个'
        : '暂无文件';
      const pager = document.getElementById('fc-page');
      if (pager) {
        pager.innerHTML = pages > 1
          ? '<button class="pagebtn" type="button" onclick="Files.go(' + (this.page - 1) + ')"' + (this.page <= 1 ? ' disabled' : '') + '>‹</button>' +
            '<span class="pgtext">' + this.page + ' / ' + pages + '</span>' +
            '<button class="pagebtn" type="button" onclick="Files.go(' + (this.page + 1) + ')"' + (this.page >= pages ? ' disabled' : '') + '>›</button>'
          : '';
      }
    },

    go(p) {
      const list = this.visible();
      const pages = Math.max(1, Math.ceil(list.length / this.size));
      this.page = Math.min(pages, Math.max(1, p));
      this.renderRows();
    },

    async togglePin(id, pinned) {
      const r = await API.put('/api/files/' + id + '/pin', { pinned: pinned });
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) { this.loaded = false; this.render(); }
    },
    setKind(k) { this.kind = k; this.page = 1; this.render(); },

    find(id) { return this.all.filter(f => f.id === id)[0]; },

    pick() { const el = document.getElementById('fc-file'); if (el) el.click(); },

    async onFile(e) {
      const files = Array.from(e.target.files || []);
      e.target.value = '';
      if (files.length) await this.upload(files);
    },

    async upload(files) {
      UI.toast('正在上传 ' + files.length + ' 个文件…');
      let ok = 0, fail = 0;
      for (const f of files) {
        const fd = new FormData();
        fd.append('file', f);
        fd.append('kind', 'center');
        const r = await API.upload('/api/files/upload', fd);
        if (r.ok) ok++; else { fail++; UI.toast(f.name + '：' + (r.msg || '上传失败'), 'err'); }
      }
      if (ok) UI.toast('已上传 ' + ok + ' 个文件', 'ok');
      if (ok || fail) { this.loaded = false; this.render(); }
    },

    copyLink(id) {
      const f = this.find(id);
      if (!f) return;
      const url = location.origin + f.url + '?token=' + encodeURIComponent(API.token || '');
      this.copy(url);
    },
    copy(text) {
      const done = () => UI.toast('已复制到剪贴板', 'ok');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => this.copyFallback(text, done));
      } else this.copyFallback(text, done);
    },
    copyFallback(text, done) {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { UI.toast('复制失败，请手动复制', 'err'); }
      ta.remove();
    },

    del(id) {
      const f = this.find(id);
      if (!f) return;
      UI.confirm('删除文件「' + f.name + '」后不可恢复，确定继续？', async () => {
        const r = await API.del('/api/files/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err');
        if (r.ok) { this.loaded = false; this.render(); }
      }, true);
    },

    menuFor(id) {
      const f = this.find(id);
      if (!f) return [];
      const mine = API.user && f.uploader_id === API.user.id;
      const canDel = mine || (API.user.perms || []).indexOf('material.manage') >= 0;
      const url = f.url + '?token=' + encodeURIComponent(API.token || '');
      const isImg = groupOf(f.name) === 'image';
      return [
        { label: isImg ? '预览图片' : '下载文件', icon: 'download', onClick: () => window.open(url, '_blank', 'noopener') },
        { label: '复制下载链接', icon: 'link', onClick: () => this.copyLink(f.id) },
        { label: '复制文件名', icon: 'copy', onClick: () => this.copy(f.name) },
        { sep: true },
        { label: '查看详情', icon: 'info', onClick: () => this.detail(f) },
        this.canPin ? { label: f.pinned ? '取消置顶' : '置顶此文件', icon: 'star', onClick: () => this.togglePin(f.id, f.pinned ? 0 : 1) } : null,
        canDel ? { label: '删除文件', icon: 'trash', danger: true, onClick: () => this.del(f.id) } : null
      ].filter(Boolean);
    },

    detail(f) {
      const u = this.users[f.uploader_id] || {};
      const g = { image: '图片', doc: '文档', sheet: '表格', archive: '压缩包', media: '音视频', other: '其他' }[groupOf(f.name)];
      UI.modal('文件详情',
        '<div class="fdetail"><div class="fdetail-name">' + UI.esc(f.name) + '</div>' +
        '<div class="grid2"><div class="field"><span>类型</span><b>' + g + ' · ' + UI.esc(KIND_LABEL[f.kind] || f.kind) + '</b></div>' +
        '<div class="field"><span>大小</span><b>' + UI.esc(f.size_str) + '</b></div>' +
        '<div class="field"><span>上传者</span><b>' + UI.esc(u.display_name || '—') + '</b></div>' +
        '<div class="field"><span>上传时间</span><b>' + UI.esc(f.created_str) + '</b></div>' +
        '<div class="field"><span>下载次数</span><b>' + (f.downloads || 0) + '</b></div>' +
        '<div class="field"><span>文件 ID</span><b>#' + f.id + '</b></div></div></div>',
        '<button class="btn" onclick="UI.closeModal()">关闭</button>' +
        '<a class="btn primary" href="' + f.url + '?token=' + encodeURIComponent(API.token || '') + '" target="_blank" rel="noopener">下载 / 打开</a>', 520);
    }
  };

  window.Files = Files;
})();
