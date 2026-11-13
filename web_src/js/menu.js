/* ============ 右键菜单 ============ */
(function () {
  'use strict';

  const ICONS = {
    star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 3 2.6 5.6 6.1.8-4.5 4.2 1.2 6L12 16.9 6.6 19.6l1.2-6L3.3 9.4l6.1-.8L12 3Z"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 6V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h1"/></svg>',
    reply: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 7 4 12l5 5M4 12h9a7 7 0 0 1 7 7"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20h4l10-10-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 9h10a5 5 0 0 1 0 10H8"/><path d="M4 9l4-4M4 9l4 4"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 4v11M7 10l5 5 5-5M4 20h16"/></svg>',
    link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 14a4 4 0 0 1 0-6l2-2a4 4 0 0 1 6 6l-1 1"/><path d="M14 10a4 4 0 0 1 0 6l-2 2a4 4 0 0 1-6-6l1-1"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4.5 5"/></svg>',
    exit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M9 12h11M16 8l4 4-4 4"/></svg>',
    read: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m3 12 5 5L20 6"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 8h.01"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg>',
    bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 9a6 6 0 0 1 12 0c0 4 1.5 6 1.5 6h-15S6 13 6 9Z"/><path d="M10 19a2.2 2.2 0 0 0 4 0"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4"/></svg>',
    theme: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 14.5A7.5 7.5 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 5v14M5 12h14"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 20 6v5.5c0 4.8-3.3 8-8 9.5-4.7-1.5-8-4.7-8-9.5V6l8-3Z"/><path d="m9 12 2 2 4-4"/></svg>'
  };

  const CtxMenu = {
    el: null,
    _onDoc: null,
    isOpen() { return !!this.el; },

    close() {
      if (this.el) { this.el.remove(); this.el = null; }
      if (this._onDoc) {
        document.removeEventListener('mousedown', this._onDoc, true);
        document.removeEventListener('scroll', this._onDoc, true);
        window.removeEventListener('resize', this._onDoc, true);
        window.removeEventListener('blur', this._onDoc, true);
        this._onDoc = null;
      }
    },

    show(ev, items) {
      if (ev && ev.preventDefault) ev.preventDefault();
      this.close();
      items = (items || []).filter(Boolean);
      if (!items.length) return;

      const menu = document.createElement('div');
      menu.className = 'ctxmenu';
      menu.setAttribute('role', 'menu');
      menu.innerHTML = items.map(function (it, i) {
        if (it.sep) return '<div class="ctxsep"></div>';
        const icon = ICONS[it.icon] || '';
        return '<button class="ctxitem' + (it.danger ? ' danger' : '') + '" role="menuitem" data-i="' + i + '"' +
          (it.disabled ? ' disabled' : '') + '>' +
          '<span class="ctxico">' + icon + '</span>' +
          '<span class="ctxlabel">' + UI.esc(it.label) + '</span>' +
          (it.hint ? '<span class="ctxhint">' + UI.esc(it.hint) + '</span>' : '') +
          '</button>';
      }).join('');
      document.body.appendChild(menu);

      const pad = 8;
      const w = menu.offsetWidth, h = menu.offsetHeight;
      let x = (ev && ev.clientX) || 0, y = (ev && ev.clientY) || 0;
      if (x + w + pad > window.innerWidth) x = window.innerWidth - w - pad;
      if (y + h + pad > window.innerHeight) y = window.innerHeight - h - pad;
      menu.style.left = Math.max(pad, x) + 'px';
      menu.style.top = Math.max(pad, y) + 'px';
      const first = menu.querySelector('.ctxitem:not([disabled])');
      if (first) first.focus({ preventScroll: true });

      menu.addEventListener('click', function (e) {
        const b = e.target.closest ? e.target.closest('.ctxitem') : null;
        if (!b || b.disabled) return;
        const it = items[Number(b.dataset.i)];
        CtxMenu.close();
        if (it && it.onClick) setTimeout(function () { it.onClick(); }, 0);
      });

      /* 打开瞬间的豁免期：下面 show() 里对第一个菜单项调用了 focus()，
         某些浏览器会因此派发一次 scroll 事件；而 onDoc 是在捕获阶段监听
         scroll 的，会立刻把刚弹出的菜单关掉（表现为右键"闪一下"或没反应）。
         200ms 内忽略 scroll/blur 触发，之后恢复正常。 */
      const openedAt = Date.now();
      const onDoc = function (e) {
        /* 点在菜单内部时不关闭：交给菜单自己的 click 处理 */
        if (e.type === 'mousedown' && CtxMenu.el && CtxMenu.el.contains(e.target)) return;
        if ((e.type === 'scroll' || e.type === 'blur') && Date.now() - openedAt < 200) return;
        CtxMenu.close();
      };
      this._onDoc = onDoc;
      setTimeout(function () {
        document.addEventListener('mousedown', onDoc, true);
        document.addEventListener('scroll', onDoc, true);
        window.addEventListener('resize', onDoc, true);
        window.addEventListener('blur', onDoc, true);
      }, 0);
      this.el = menu;
    },

    /* 事件委托：容器内的匹配元素右键时，用 builder(el) 生成菜单项 */
    attach(container, selector, builder) {
      if (!container || container.__ctxBound) {
        if (container && container.__ctxBound) container.__ctxBuilder = builder;
        return;
      }
      container.__ctxBound = true;
      container.__ctxBuilder = builder;
      container.addEventListener('contextmenu', function (e) {
        const el = e.target.closest ? e.target.closest(selector) : null;
        if (!el || !container.contains(el)) return;
        const items = container.__ctxBuilder(el, e);
        if (items && items.length) {
          e.stopPropagation();     // 命中专用菜单后不再触发全局菜单
          CtxMenu.show(e, items);
        }
      });
    }
  };

  window.CtxMenu = CtxMenu;
  window.addEventListener('resize', function () { CtxMenu.close(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') CtxMenu.close();
  });
})();
