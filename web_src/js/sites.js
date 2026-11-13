/* ============ 常用网站（教学资源导航） ============ */
(function () {
  'use strict';

  const COLORS = ['#3b82f6', '#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#0ea5e9', '#f43f5e'];

  function colorOf(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 9973;
    return COLORS[h % COLORS.length];
  }

  function hostOf(url) {
    try { return new URL(url).host; } catch (e) { return String(url).replace(/^https?:\/\//, '').split('/')[0]; }
  }

  const Sites = {
    data: [], cats: [], cur: '', kw: '',

    async render() {
      const box = document.getElementById('sites-body');
      if (!box) return;
      box.innerHTML = '<div class="empty">加载中…</div>';
      const r = await API.get('/api/sites');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      this.data = r.data.list || [];
      this.cats = r.data.categories || [];
      const stat = document.getElementById('sites-stat');
      if (stat) stat.textContent = this.data.length + ' 个站点 · ' + this.cats.length + ' 个分类';
      this.paint();
    },

    paint() {
      const box = document.getElementById('sites-body');
      if (!this.data.length) {
        box.innerHTML = '<div class="empty">管理员还没有配置常用网站</div>';
        return;
      }
      box.innerHTML =
        '<div class="sites-toolbar">' +
        '<div class="sites-search-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6" stroke-linecap="round"/></svg>' +
        '<input id="sites-kw" placeholder="搜索网站名称 / 地址…" oninput="Sites.setKw(this.value)"></div>' +
        '<div class="sites-tabs" id="sites-tabs">' + this.tabsHtml() + '</div>' +
        '</div>' +
        '<div id="sites-groups">' + this.cats.map(c => this.groupHtml(c)).join('') + '</div>' +
        '<div class="sites-empty" id="sites-empty" style="display:none"><div class="empty">没有匹配的网站</div></div>';
      this.applyFilter();
    },

    tabsHtml() {
      const total = this.data.length;
      const all = '<button class="pill' + (this.cur ? '' : ' active') + '" data-cat="">全部 <b>' + total + '</b></button>';
      return all + this.cats.map(c => {
        const n = this.data.filter(s => (s.category || '常用') === c).length;
        return '<button class="pill' + (this.cur === c ? ' active' : '') + '" data-cat="' + UI.esc(c) + '">' +
          UI.esc(c) + ' <b>' + n + '</b></button>';
      }).join('');
    },

    groupHtml(cat) {
      const list = this.data.filter(s => (s.category || '常用') === cat);
      if (!list.length) return '';
      return '<section class="site-group" data-cat="' + UI.esc(cat) + '">' +
        '<div class="site-group-head"><b>' + UI.esc(cat) + '</b><span class="muted">' + list.length + ' 个</span></div>' +
        '<div class="site-grid">' + list.map(s => {
          const host = hostOf(s.url);
          return '<a class="site-card" href="' + UI.esc(s.url) + '" target="_blank" rel="noopener noreferrer" ' +
            'data-kw="' + UI.esc((s.name + ' ' + s.url + ' ' + host).toLowerCase()) + '" title="' + UI.esc(s.name + ' — ' + s.url) + '">' +
            '<span class="site-ico" style="background:' + colorOf(s.name) + '">' + UI.esc((s.name || '?').slice(0, 1)) + '</span>' +
            '<span class="site-main"><b>' + UI.esc(s.name) + '</b><small>' + UI.esc(host) + '</small></span>' +
            '<span class="site-go">↗</span></a>';
        }).join('') + '</div></section>';
    },

    setKw(kw) {
      this.kw = (kw || '').trim().toLowerCase();
      this.applyFilter();
    },

    pick(cat) {
      this.cur = cat || '';
      document.querySelectorAll('#sites-tabs .pill').forEach(b => b.classList.toggle('active', (b.dataset.cat || '') === this.cur));
      this.applyFilter();
    },

    applyFilter() {
      let shown = 0;
      document.querySelectorAll('#sites-groups .site-group').forEach(g => {
        const catOk = !this.cur || g.dataset.cat === this.cur;
        let n = 0;
        g.querySelectorAll('.site-card').forEach(c => {
          const ok = catOk && (!this.kw || (c.dataset.kw || '').indexOf(this.kw) >= 0);
          c.style.display = ok ? '' : 'none';
          if (ok) n++;
        });
        g.style.display = n ? '' : 'none';
        shown += n;
      });
      const empty = document.getElementById('sites-empty');
      if (empty) empty.style.display = shown ? 'none' : '';
    },

    bind() {
      document.addEventListener('click', e => {
        const b = e.target.closest('#sites-tabs .pill');
        if (b) this.pick(b.dataset.cat || '');
      });
    }
  };

  window.Sites = Sites;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => Sites.bind());
  else Sites.bind();
})();
