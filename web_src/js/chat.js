/* ============ 聊天模块 ============ */
(function () {
  'use strict';
  /* 表情分 8 组，共约 170 个 */
  const EMOJI_GROUPS = [
    { name: '常用', list: ['😀', '😄', '😁', '😂', '🤣', '😊', '😍', '🥰', '😘', '😎', '🤔', '😅', '😭', '😡', '🥳',
      '😴', '🤗', '😇', '🙃', '😉', '😌', '😋', '🤩', '🥺', '😤', '😱', '🤯', '🥶', '🤒', '🤕', '🫡', '😷'] },
    { name: '手势', list: ['👍', '👎', '👏', '🙏', '🤝', '💪', '👌', '✌️', '🤞', '🤟', '👋', '🤙', '☝️', '👆', '👇',
      '👈', '👉', '✊', '👊', '🫶', '🤲', '🤷', '🤦', '🙌'] },
    { name: '爱心', list: ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💔', '💕', '💞', '💓', '💗', '💖', '💘', '💝'] },
    { name: '表情', list: ['😐', '😑', '😶', '🙄', '😬', '🤨', '😏', '😒', '😞', '😔', '😟', '😕', '🙁', '☹️', '😣',
      '😖', '😫', '😩', '😢', '🥲', '😮', '😲', '😳', '🥵', '😨', '😰', '😥', '😓', '🤐', '🤫', '🤭', '🫢'] },
    { name: '动物', list: ['🐶', '🐱', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐨', '🐯', '🦁', '🐮', '🐷', '🐸', '🐵',
      '🐔', '🐧', '🦄', '🐝', '🦋', '🐢', '🐬', '🐳', '🦉'] },
    { name: '美食', list: ['☕', '🍵', '🍺', '🍻', '🍎', '🍊', '🍋', '🍌', '🍉', '🍇', '🍓', '🍒', '🍑', '🥝', '🍅',
      '🥑', '🍞', '🍰', '🎂', '🍭', '🍬', '🍫', '🍜', '🍚'] },
    { name: '活动', list: ['⚽', '🏀', '🏐', '🎾', '🏓', '🏸', '🎯', '🎮', '🎲', '🎉', '🎊', '🎁', '🏆', '🥇', '🎓',
      '📚', '✈️', '🚀', '🎵', '🎧', '🎤', '📷', '💡', '🌈'] },
    { name: '符号', list: ['✅', '❌', '❗', '❓', '⚠️', '💯', '🔥', '⭐', '🌟', '✨', '💤', '💬', '🔔', '📌', '📍',
      '🎈', '🎀', '🔒', '🔑', '⏰', '📅', '📎', '💻', '📱', '📄', '📊'] }
  ];

  const Chat = {
    rooms: [], current: 0, room: null, msgs: [], members: [],
    attach: [], replyTo: 0, oldest: 0, contacts: [], ids: {}, muted: false, canManage: false,

    /* 统一入列并按 id 去重：HTTP 返回值与 WebSocket 推送会各到达一次 */
    pushMsg(m) {
      if (!m || !m.id) return false;
      if (this.ids[m.id]) return false;
      this.ids[m.id] = 1;
      this.msgs.push(m);
      return true;
    },

    reindex() {
      this.ids = {};
      const me = this.ids;
      this.msgs.forEach(function (m) { if (m && m.id) me[m.id] = 1; });
    },

    init() {
      /* 粘贴图片：Win+Shift+S / 截图工具后直接 Ctrl+V 发送 */
      document.addEventListener('paste', e => this.onPaste(e));
      WS.on('msg', d => this.onMsg(d));
      WS.on('typing', d => this.onTyping(d));
      WS.on('typing_end', () => { document.getElementById('typing-bar').textContent = ''; });
      WS.on('recall', d => this.onRecall(d));
      WS.on('edit', d => this.onEdit(d));
      WS.on('unread', d => App.updateUnread(d));
      WS.on('sys', d => {
        if (d.room_id === this.current) { this.loadMessages(); this.loadMembers(); }
        else this.loadRooms();
      });
      WS.on('presence', () => { if (this.current) this.loadMembers(); });
      /* 禁言：被禁言者本人收到 —— 更新输入区 + 提示 */
      WS.on('muted', d => {
        const name = (this.rooms.find(x => x.id === d.room_id) || {}).name || ('房间' + d.room_id);
        UI.toast(d.minutes > 0 ? ('您在「' + name + '」被禁言 ' + d.minutes + ' 分钟，' + (d.until_str || '') + ' 解禁')
                               : ('您在「' + name + '」的禁言已解除'), d.minutes > 0 ? 'err' : 'ok');
        if (d.room_id === this.current) {
          this.muted = d.minutes > 0;
          this.updateComposer();
          this.loadMembers();
          if (document.getElementById('modal-root').innerHTML) this.showMembers();
        }
      });
      /* 禁言/解禁状态变化：房间内的管理员刷新成员标签 */
      WS.on('mute_update', d => {
        if (d.room_id === this.current) {
          this.loadMembers();
          if (document.getElementById('modal-root').innerHTML) this.showMembers();
        }
      });
      /* 成员变化（邀请/移除）：列表刷新 + 被移除者剔除房间 */
      WS.on('member_update', d => {
        if (d.room_id === this.current) this.loadMembers();
        this.loadRooms();
      });
      WS.on('kicked', d => {
        UI.toast('你已被移出房间「' + (d.name || '') + '」', 'err');
        if (d.room_id === this.current) { this.current = 0; this.msgs = []; this.ids = {}; }
        this.loadRooms();
      });
      /* 新房间/新私聊：刷新房间列表 */
      WS.on('room_update', () => this.loadRooms());
      /* 账号被封禁：强制退出 */
      WS.on('banned', d => {
        UI.toast(d.msg || '账号已被停用', 'err');
        setTimeout(() => Auth.logout(true), 1200);
      });
      /* 全员广播提示（禁言/封禁等管理动作） */
      WS.on('sys_global', d => { if (d && d.content) UI.toast(d.content, 'err'); });
      /* 他人资料变化（头衔等）：刷新成员与在线列表 */
      WS.on('user_update', () => { if (this.current) this.loadMembers(); });
      WS.on('notice', () => { });
      /* 隐身巡查入口：仅 admin 账号（patrol）可见 */
      if (API.user && API.user.patrol) {
        const pb = document.getElementById('patrol-btn');
        if (pb) pb.style.display = '';
      }
      /* 右键菜单：会话 / 消息 */
      if (window.CtxMenu) {
        CtxMenu.attach(document.getElementById('room-list'), '.room-item', el => this.roomMenu(+el.dataset.id));
        CtxMenu.attach(document.getElementById('msg-list'), '.msg', el => this.msgMenu(+String(el.id).replace('msg-', '')));
      }
      this.loadRooms();
    },

    /* 隐身巡查：一键静默加入全部房间（不显示进入、不在成员列表/在线列表，只读） */
    async patrolJoinAll() {
      const r = await API.post('/api/chat/rooms/patrol-join-all', {});
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      UI.toast(r.msg || '已静默加入全部房间', 'ok');
      this.loadRooms();
      if (this.current) this.loadMessages();
    },

    /* 房间标签三合一：已加入（含私聊）与未加入的公开房合并为一个列表 */
    async loadRooms() {
      const [mine, pub] = await Promise.all([
        API.get('/api/chat/rooms'),
        API.get('/api/chat/rooms/public')
      ]);
      if (!mine.ok || !pub.ok) return;
      const joined = (mine.data.list || []).map(r => { r.joined = true; return r; });
      const joinedIds = {};
      joined.forEach(r => { joinedIds[r.id] = 1; });
      const open = ((pub.data.list || [])).filter(r => !joinedIds[r.id]);
      this.rooms = joined.concat(open);
      this.renderRooms();
    },

    renderRooms() {
      const box = document.getElementById('room-list');
      if (!this.rooms.length) { box.innerHTML = '<div class="empty">暂无房间</div>'; return; }
      const icon = t => ({ public: '💬', private: '🔒', dept: '🏢', course: '🎓', dm: '👤', announce: '📢' }[t] || '💬');
      const fm = this._filterMode || 'all';
      const list = this.rooms.filter(r => fm === 'all' || (fm === 'joined' ? r.joined !== false : r.joined === false));
      const rc = document.getElementById('room-count');
      if (rc) rc.textContent = list.length + ' 个会话';
      box.innerHTML = list.map(r => {
        const joined = r.joined !== false;
        const last = r.last_message;
        const preview = last ? (last.type === 'file' ? '[文件] ' + ((last.attachments[0] || {}).name || '') : last.content) : (r.topic || (joined ? '暂无消息' : '点击加入'));
        const un = r.unread > 0 ? '<span class="room-unread">' + r.unread + '</span>' : '';
        const action = joined ? '' : '<button class="btn sm" onclick="event.stopPropagation();Chat.joinRoom(' + r.id + ')">＋加入</button>';
        return '<div class="room-item ' + (r.id === this.current ? 'active' : '') + '" data-id="' + r.id + '" onclick="Chat.openRoom(' + r.id + ')">' +
          '<div class="room-ic">' + icon(r.type) + '</div>' +
          '<div class="room-meta"><b>' + UI.esc(r.name) + '</b><small>' + UI.esc(preview || '') + '</small></div>' + un + action + '</div>';
      }).join('');
    },

    /* 会话搜索框实时过滤（不触发重排） */
    filterRooms(kw) {
      const q = (kw || '').toLowerCase();
      const items = document.querySelectorAll('#room-list .room-item');
      items.forEach(el => {
        const hit = !q || el.textContent.toLowerCase().indexOf(q) >= 0;
        el.style.display = hit ? '' : 'none';
      });
    },

    /* 会话筛选：全部 / 已加入 / 未加入 */
    setFilter(f, btn) {
      this._filterMode = f;
      document.querySelectorAll('.filterrow .pill').forEach(p => p.classList.toggle('active', p === btn));
      this.renderRooms();
    },

    /* 合并列表里加入未加入的公开房 */
    async joinRoom(id) {
      const r = await API.post('/api/chat/rooms/' + id + '/join', {});
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      this.loadRooms();
      this.openRoom(id);
    },

    async openRoom(id) {
      this.current = id;
      this.msgs = []; this.ids = {}; this.oldest = 0; this.replyTo = 0;
      const r = await API.get('/api/chat/rooms/' + id);
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      this.room = r.data;
      this.muted = !!r.data.muted;
      this.canManage = !!r.data.can_manage;
      document.getElementById('chat-room-name').textContent = r.data.name;
      const _topic = String(r.data.topic || '').trim();
      document.getElementById('chat-room-topic').textContent =
        (_topic ? _topic + ' · ' : '') + r.data.member_count + '人' + (this.muted ? ' · 您已被禁言' : '');
      document.getElementById('view-sub').textContent = r.data.name;
      this.updateComposer();
      this.renderRooms();
      await this.loadMessages();
      await this.loadMembers();
      WS.send('sub', { room_id: id });
      const lastId = this.msgs.length ? this.msgs[this.msgs.length - 1].id : 0;
      WS.send('read', { room_id: id, msg_id: lastId });
      API.post('/api/chat/rooms/' + id + '/read', { msg_id: lastId });
    },

    /* 禁言/公告频道/隐身巡查时禁用输入区 */
    updateComposer() {
      const ta = document.getElementById('msg-input');
      const sendBtn = document.querySelector('.composer-send .btn');
      const hint = document.getElementById('composer-hint');
      const silent = !!(this.room && this.room.silent);
      const locked = this.muted || silent || (this.room && this.room.type === 'announce' &&
        API.user.perms.indexOf('announce.manage') < 0);
      ta.disabled = locked;
      if (sendBtn) sendBtn.disabled = locked;
      ta.placeholder = this.muted ? '您已被禁言，暂不能发言'
        : silent ? '隐身巡查中：该房间仅可查看，不可发言'
        : (this.room && this.room.type === 'announce' && API.user.perms.indexOf('announce.manage') < 0)
          ? '公告频道仅管理员可发言'
          : '输入消息，Enter 发送，Shift+Enter 换行';
      if (locked && hint && !this.replyTo) hint.textContent = this.muted ? '🔇 禁言中' : silent ? '👁 隐身巡查 · 只读' : '📢 只读频道';
      else if (hint && !this.replyTo) hint.textContent = '';
    },

    async loadMessages(before) {
      const id = this.current;
      if (!id) return;
      const r = await API.get('/api/chat/rooms/' + id + '/messages', { before: before || 0, size: 60 });
      if (!r.ok) return;
      const list = r.data.list || [];
      const box = document.getElementById('msg-list');
      if (before) {
        this.msgs = list.concat(this.msgs);
      } else {
        this.msgs = list;
        box.innerHTML = '';
      }
      this.reindex();
      this.renderMessages(before);
      if (!before) box.scrollTop = box.scrollHeight;
    },

    renderMessages(keepScroll) {
      const box = document.getElementById('msg-list');
      const me = API.user.id;
      /* 渲染前按 id 去重并排序：HTTP 返回与 WebSocket 推送各到达一次也不会出现重复气泡 */
      const seen = {}, list = [];
      this.msgs.forEach(m => { if (m && m.id && !seen[m.id]) { seen[m.id] = 1; list.push(m); } });
      list.sort((a, b) => a.id - b.id);
      if (list.length !== this.msgs.length) { this.msgs = list; this.reindex(); }
      let html = '', lastDay = '', lastUser = 0, lastMetaTs = 0;
      list.forEach(m => {
        const day = UI.time(m.created_at, 'full').split(' ')[0];
        if (day !== lastDay) { html += '<div class="msg-day">' + day + '</div>'; lastDay = day; lastUser = 0; lastMetaTs = 0; }
        const mine = !!(m.user && m.user.id === me);
        const who = m.user ? (m.user.name || '') : '系统';
        /* 系统消息 / 撤回提示：居中细线，不参与气泡布局 */
        if (m.type === 'system') {
          html += '<div class="sysline">' + UI.esc(m.content) + '</div>';
          lastUser = 0; lastMetaTs = 0;
          return;
        }
        if (m.recalled) {
          html += '<div class="sysline muted">' + (mine ? '你' : UI.esc(who)) + ' 撤回了一条消息</div>';
          lastUser = 0; lastMetaTs = 0;
          return;
        }
        /* 同一人 5 分钟内的连续消息：不重复头像与昵称，让对话更紧凑 */
        const ts = m.created_at || 0;
        const cont = !!(m.user && m.user.id === lastUser && ts - lastMetaTs < 300);
        lastUser = m.user ? m.user.id : 0;
        lastMetaTs = ts;

        let body;
        let att = '';
        (m.attachments || []).forEach(f => {
          const isImg = /\.(png|jpe?g|gif|bmp|svg|webp)$/i.test(f.name);
          const tk = '?token=' + encodeURIComponent(API.token || '');
          att += '<div class="att-item">📎 <a href="' + f.url + tk + '" target="_blank" ' +
            (isImg ? '' : 'download') + '>' + UI.esc(f.name) + '</a> <small style="color:var(--text-3)">' +
            UI.size(f.size) + '</small>' + (isImg ? '<img src="' + f.url + tk + '" alt="" loading="lazy">' : '') + '</div>';
        });
        const edited = m.edited ? '<small class="edited-mark">（已编辑）</small>' : '';
        const quote = m.reply ? ('<div class="quote' + (m.reply.recalled ? ' recalled' : '') + '" onclick="Chat.jumpTo(' +
          m.reply.id + ')" title="点击查看原消息">↩ ' + UI.esc(m.reply.name) + '：' +
          UI.esc(m.reply.recalled ? '[已撤回]' : (m.reply.content || '[附件]')) + '</div>') : '';
        body = '<div class="bubble">' + quote + (m.content ? UI.nl2br(m.content) : '') + edited +
          (att ? '<div class="att">' + att + '</div>' : '') + '</div>';

        const acts = '<span class="link" onclick="Chat.reply(' + m.id + ')">回复</span>' +
          (mine ? '<span class="link" onclick="Chat.recall(' + m.id + ')">撤回</span>' +
            '<span class="link" onclick="Chat.edit(' + m.id + ')">编辑</span>' : '');
        const titleTag = (m.user && m.user.title) ? '<span class="title-tag">' + UI.esc(m.user.title) + '</span>' : '';
        /* 已读回执：只对"我发的"消息显示，标注这条被几个人读了。
           后端直接给 read_others / read_peers（已去掉发送者自己），
           这里不再自己减 1，避免两边算法不一致导致「详情说有 1 人已读、角标却显示 0/1」。 */
        let readTag = '';
        if (mine && m.type !== 'system' && !m.recalled && typeof m.read_peers === 'number') {
          const peers = m.read_peers;
          const read = Math.min(peers, m.read_others || 0);
          readTag = '<span class="read-tag' + (peers > 0 && read >= peers ? ' all' : '') +
            '" title="已读 ' + read + ' / ' + peers + ' 人" onclick="Chat.showRead(' + m.id + ')">' +
            (peers <= 0 ? '已读' : (read >= peers ? '全部已读' : '已读 ' + read + '/' + peers)) + '</span>';
        }
        const avatar = cont
          ? '<div class="avatar-spacer"></div>'
          : UI.avatar(m.user ? m.user.name : '?', m.user ? m.user.color : '#8794a8', 0, m.user ? m.user.avatar : '');
        html += '<div class="msg ' + (mine ? 'mine ' : '') + (cont ? 'cont' : 'has-meta') + '" id="msg-' + m.id + '">' +
          avatar + '<div class="msg-body">' +
          (cont ? '' :
            '<div class="msg-head"><b>' + UI.esc(who) + '</b>' + titleTag +
            '<span>' + UI.time(m.created_at) + '</span>' + acts + readTag + '</div>') +
          body + '</div></div>';
      });
      const old = box.scrollHeight - box.scrollTop;
      box.innerHTML = html || '<div class="empty">还没有消息，来说两句吧</div>';
      if (keepScroll) box.scrollTop = box.scrollHeight - old;
      document.getElementById('typing-bar').textContent = '';
    },

    async send() {
      if (this._sending) return;          // 防连击：回车+点击/快速双击只会真正发送一次
      if (this.muted) { UI.toast('您已被禁言，无法发言', 'err'); return; }
      const el = document.getElementById('msg-input');
      if (el.disabled) { UI.toast('当前房间为只读，无法发言', 'err'); return; }
      const content = el.value.trim();
      if (!content && !this.attach.length) return;
      if (!this.current) { UI.toast('请先选择一个房间', 'err'); return; }
      /* 自带 nonce，服务端回显时带回，用于忽略自己那条 WebSocket 推送 */
      const nonce = 'n' + Date.now() + Math.random().toString(36).slice(2, 8);
      this._own = this._own || {};
      this._own[nonce] = 1;
      this._sending = true;
      try {
        const r = await API.post('/api/chat/rooms/' + this.current + '/messages', {
          content: content, attachments: this.attach.map(a => a.id), reply_to: this.replyTo, nonce: nonce
        });
        if (!r.ok) {
          UI.toast(r.msg, 'err');
          if (r.status === 403 && /禁言/.test(r.msg || '')) {      // 服务端判定已禁言：同步 UI 状态
            this.muted = true; this.updateComposer();
          }
          return;
        }
        el.value = ''; this.attach = []; this.replyTo = 0;
        document.getElementById('attach-preview').textContent = '';
        this.updateComposer();
        this.pushMsg(r.data);
        this.renderMessages();
        const box = document.getElementById('msg-list');
        box.scrollTop = box.scrollHeight;
        WS.send('read', { room_id: this.current, msg_id: r.data.id });
      } finally {
        this._sending = false;
      }
    },

    onMsg(d) {
      if (d.nonce && this._own && this._own[d.nonce]) { delete this._own[d.nonce]; return; }
      if (d.room_id === this.current) {
        const box = document.getElementById('msg-list');
        const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
        if (!this.pushMsg(d)) return;          // 已渲染过（HTTP 返回或重复推送）则忽略
        this.renderMessages();
        if (nearBottom) box.scrollTop = box.scrollHeight;
        WS.send('read', { room_id: d.room_id, msg_id: d.id });
      } else {
        this.loadRooms();
        App.loadNotify(true);
        // 标签页黄色闪烁：@我 或 私聊（dm）消息且不在前台
        if (document.hidden) {
          const my = API.user ? API.user.id : null;
          const mentions = d && Array.isArray(d.mentions) ? d.mentions : [];
          const room = (this.rooms || []).find(x => x.id === d.room_id);
          if (my && (mentions.indexOf(my) >= 0 || (room && room.type === 'dm'))) {
            const isMent = mentions.indexOf(my) >= 0;
            UI.notifyTitle(isMent ? '有人@你' : '私聊新消息',
              { body: (d.sender_name ? d.sender_name + '：' : '') + (isMent ? '有人在消息中@了你' : '给你发来一条私聊消息') });
          }
        }
      }
    },

    onTyping(d) {
      if (d.room_id !== this.current) return;
      const bar = document.getElementById('typing-bar');
      bar.textContent = d.name + ' 正在输入…';
      clearTimeout(this._t);
      this._t = setTimeout(() => { bar.textContent = ''; }, 2500);
    },

    onRecall(d) {
      const m = this.msgs.find(x => x.id === d.id);
      if (m) { m.recalled = true; this.renderMessages(true); }
    },

    onEdit(d) {
      const m = this.msgs.find(x => x.id === d.id);
      if (m) { Object.assign(m, d); this.renderMessages(true); }
    },

    onKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
      else if (this.current && !e.target.disabled) {
        clearTimeout(this._tt);
        if (!this._tt) WS.send('typing', { room_id: this.current });
        this._tt = setTimeout(() => { this._tt = null; }, 3000);
      }
    },
    onInput() { },

    async onFile(e) {
      const files = Array.from(e.target.files || []);
      for (const f of files) {
        const fd = new FormData();
        fd.append('file', f);
        fd.append('room_id', this.current);
        const r = await API.upload('/api/files/upload', fd);
        if (r.ok) this.attach = this.attach.concat(r.data.list);
        else UI.toast(r.msg, 'err');
      }
      this.renderAttach();
      e.target.value = '';
    },
    renderAttach() {
      const el = document.getElementById('attach-preview');
      if (el) el.textContent = this.attach.length ? '待发送附件：' + this.attach.map(a => a.name).join('、') : '';
    },

    /* ---------- 截图发送 ---------- */
    canShot() {
      return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);
    },
    async screenshot() {
      if (!this.current) { UI.toast('请先在左侧选择一个会话', 'err'); return; }
      if (!this.canShot()) {
        UI.modal('截图发送',
          '<p style="line-height:1.8">浏览器只允许 <b>HTTPS</b> 或 <b>localhost</b> 页面截屏，当前页面不是安全环境。可以这样发截图：</p>' +
          '<ol style="line-height:2;padding-left:20px"><li>按 <b>Win + Shift + S</b> 框选截图（或 <b>PrintScreen</b>）</li>' +
          '<li>回到这里，直接按 <b>Ctrl + V</b> 粘贴发送</li></ol>' +
          '<p class="muted" style="margin-top:8px">管理员在「系统设置 → 安全与访问」开启 HTTPS 后，本按钮即可直接截屏。</p>',
          '<button class="btn primary" onclick="UI.closeModal()">知道了</button>', 500);
        return;
      }
      let stream = null;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 8 }, audio: false });
        const video = document.createElement('video');
        video.srcObject = stream;
        video.muted = true;
        video.playsInline = true;
        await video.play();
        await new Promise(r => setTimeout(r, 200));
        const w = video.videoWidth || window.screen.width;
        const h = video.videoHeight || window.screen.height;
        const cv = document.createElement('canvas');
        cv.width = w;
        cv.height = h;
        cv.getContext('2d').drawImage(video, 0, 0, w, h);
        stream.getTracks().forEach(t => t.stop());
        stream = null;
        this.shotSelect(cv.toDataURL('image/png'), w, h);
      } catch (err) {
        if (stream) stream.getTracks().forEach(t => t.stop());
        if (err && err.name === 'NotAllowedError') UI.toast('已取消截屏', '');
        else UI.toast('无法截取屏幕：' + ((err && err.message) || err), 'err');
      }
    },
    shotSelect(dataUrl, imgW, imgH) {
      this.closeShot();
      const vw = window.innerWidth, vh = window.innerHeight;
      const scale = Math.min(vw / imgW, vh / imgH);
      const dw = Math.round(imgW * scale), dh = Math.round(imgH * scale);
      const ox = Math.round((vw - dw) / 2), oy = Math.round((vh - dh) / 2);
      const el = document.createElement('div');
      el.id = 'shot-overlay';
      el.innerHTML =
        '<img id="shot-img" src="' + dataUrl + '" style="left:' + ox + 'px;top:' + oy + 'px;width:' + dw + 'px;height:' + dh + 'px">' +
        '<div id="shot-sel"><span class="size"></span></div>' +
        '<div id="shot-bar"><span class="hint">拖动选择区域 · Enter 确认 · Esc 取消</span>' +
        '<button class="btn" type="button" id="shot-full">全屏</button>' +
        '<button class="btn" type="button" id="shot-cancel">取消</button>' +
        '<button class="btn primary" type="button" id="shot-ok">截图发送</button></div>';
      document.body.appendChild(el);
      const sel = el.querySelector('#shot-sel');
      const size = sel.querySelector('.size');
      let start = null, rect = null;
      const clamp = (x, y) => [Math.min(Math.max(x, ox), ox + dw), Math.min(Math.max(y, oy), oy + dh)];
      const draw = () => {
        if (!rect || rect.w < 1 || rect.h < 1) { sel.style.display = 'none'; return; }
        sel.style.display = 'block';
        sel.style.left = rect.x + 'px';
        sel.style.top = rect.y + 'px';
        sel.style.width = rect.w + 'px';
        sel.style.height = rect.h + 'px';
        size.textContent = Math.round(rect.w / scale) + ' × ' + Math.round(rect.h / scale);
      };
      el.addEventListener('mousedown', e => {
        if (e.target.closest && e.target.closest('#shot-bar')) return;
        if (e.button !== 0) return;
        const p = clamp(e.clientX, e.clientY);
        start = p;
        rect = { x: p[0], y: p[1], w: 0, h: 0 };
        draw();
      });
      el.addEventListener('mousemove', e => {
        if (!start) return;
        const p = clamp(e.clientX, e.clientY);
        rect = { x: Math.min(start[0], p[0]), y: Math.min(start[1], p[1]),
                 w: Math.abs(p[0] - start[0]), h: Math.abs(p[1] - start[1]) };
        draw();
      });
      el.addEventListener('mouseup', () => { start = null; });
      const finish = (full) => {
        const img = el.querySelector('#shot-img');
        const cv = document.createElement('canvas');
        let sx = 0, sy = 0, sw = imgW, sh = imgH;
        if (!full && rect && rect.w > 4 && rect.h > 4) {
          sx = Math.round((rect.x - ox) / scale);
          sy = Math.round((rect.y - oy) / scale);
          sw = Math.max(1, Math.round(rect.w / scale));
          sh = Math.max(1, Math.round(rect.h / scale));
          sx = Math.min(sx, imgW - 1); sy = Math.min(sy, imgH - 1);
          sw = Math.min(sw, imgW - sx); sh = Math.min(sh, imgH - sy);
        }
        cv.width = sw; cv.height = sh;
        cv.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
        this.closeShot();
        cv.toBlob(blob => { if (blob) this.shotUpload(blob); }, 'image/png');
      };
      el.querySelector('#shot-ok').addEventListener('click', () => finish(false));
      el.querySelector('#shot-full').addEventListener('click', () => finish(true));
      el.querySelector('#shot-cancel').addEventListener('click', () => this.closeShot());
      this._shotKey = e => {
        if (e.key === 'Escape') { e.preventDefault(); this.closeShot(); }
        else if (e.key === 'Enter') { e.preventDefault(); finish(false); }
      };
      document.addEventListener('keydown', this._shotKey, true);
    },
    closeShot() {
      const el = document.getElementById('shot-overlay');
      if (el) el.remove();
      if (this._shotKey) { document.removeEventListener('keydown', this._shotKey, true); this._shotKey = null; }
    },
    async shotUpload(blob) {
      if (!this.current) { UI.toast('请先选择一个会话', 'err'); return; }
      const name = '截图_' + new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14) + '.png';
      const f = (blob instanceof File) ? blob : new File([blob], name, { type: 'image/png' });
      const fd = new FormData();
      fd.append('file', f);
      fd.append('room_id', this.current);
      UI.toast('正在发送截图…', '');
      const r = await API.upload('/api/files/upload', fd);
      if (!r.ok) { UI.toast(r.msg || '截图上传失败', 'err'); return; }
      this.attach = this.attach.concat(r.data.list);
      this.renderAttach();
      const input = document.getElementById('msg-input');
      if (input && !input.value.trim()) this.send();
      else UI.toast('截图已添加为附件，可补充文字后发送', 'ok');
    },
    onPaste(e) {
      if (!this.current) return;
      if (window.App && App.view !== 'chat') return;
      if (document.getElementById('modal-root').innerHTML.trim()) return;
      const cd = e.clipboardData || window.clipboardData;
      if (!cd || !cd.items) return;
      for (const it of cd.items) {
        if (it.kind === 'file' && it.type && it.type.indexOf('image/') === 0) {
          const f = it.getAsFile();
          if (f) {
            e.preventDefault();
            this.shotUpload(f);
            return;
          }
        }
      }
    },

    emoji(el) {
      const btn = el || (window.event && event.target) || document.getElementById('emoji-btn');
      if (!btn) return;
      const rect = btn.getBoundingClientRect();
      let pop = document.getElementById('emoji-pop');
      if (pop) { pop.remove(); return; }
      pop = document.createElement('div');
      pop.id = 'emoji-pop';
      pop.className = 'emoji-pop';
      pop.innerHTML = this.emojiPanel(0);
      document.body.appendChild(pop);

      /* 面板较宽较高：先插入再按视口夹紧位置 */
      const w = pop.offsetWidth, h = pop.offsetHeight;
      let left = rect.left - 20;
      if (left + w > window.innerWidth - 10) left = window.innerWidth - w - 10;
      let top = rect.top - h - 8;
      if (top < 10) top = Math.min(window.innerHeight - h - 10, rect.bottom + 8);
      pop.style.left = Math.max(10, left) + 'px';
      pop.style.top = Math.max(10, top) + 'px';

      pop.addEventListener('click', e => {
        const tab = e.target.closest ? e.target.closest('.emoji-tab') : null;
        if (!tab) return;
        e.stopPropagation();
        pop.querySelectorAll('.emoji-tab').forEach(b => b.classList.toggle('active', b === tab));
        pop.querySelector('.emoji-grid').innerHTML = this.emojiGrid(+tab.dataset.g);
      });
      setTimeout(() => document.addEventListener('click', function h() { pop.remove(); document.removeEventListener('click', h); }), 50);
    },
    emojiPanel(gi) {
      return '<div class="emoji-tabs">' + EMOJI_GROUPS.map((g, i) =>
        '<button class="emoji-tab' + (i === gi ? ' active' : '') + '" type="button" data-g="' + i + '">' + g.name + '</button>').join('') + '</div>' +
        '<div class="emoji-scroll"><div class="emoji-grid">' + this.emojiGrid(gi) + '</div></div>';
    },
    emojiGrid(gi) {
      const g = EMOJI_GROUPS[gi] || EMOJI_GROUPS[0];
      return g.list.map(e => '<span title="' + e + '" onclick="Chat.pickEmoji(\'' + e + '\')">' + e + '</span>').join('');
    },
    pickEmoji(e) {
      const el = document.getElementById('msg-input');
      el.value += e;
      const pop = document.getElementById('emoji-pop');
      if (pop) pop.remove();
      el.focus();
    },

    async mention() {
      if (!this.contacts.length) {
        const r = await API.get('/api/users');
        if (r.ok) this.contacts = r.data.list;
      }
      const el = document.getElementById('msg-input');
      el.value += '@';
      el.focus();
      const rect = el.getBoundingClientRect();
      let pop = document.getElementById('mention-pop');
      if (pop) pop.remove();
      pop = document.createElement('div');
      pop.id = 'mention-pop';
      pop.className = 'mention-pop';
      pop.style.left = rect.left + 'px';
      pop.style.top = (rect.top - 210) + 'px';
      pop.innerHTML = this.contacts.map((u, i) =>
        '<div class="mention-item" data-i="' + i + '">' +
        UI.esc(u.display_name) + ' <small style="color:var(--text-3)">' + UI.esc(u.dept) + '</small></div>').join('');
      pop.addEventListener('mousedown', e => {
        const it = e.target.closest ? e.target.closest('.mention-item') : null;
        if (!it) return;
        e.preventDefault();
        this.pickMention(this.contacts[+it.dataset.i]);
      });
      document.body.appendChild(pop);
      setTimeout(() => document.addEventListener('click', function h() { pop.remove(); document.removeEventListener('click', h); }), 50);
    },
    pickMention(u) {
      if (!u) return;
      const el = document.getElementById('msg-input');
      el.value = el.value.replace(/@$/, '@' + u.display_name + ' ');
      document.getElementById('mention-pop').remove();
      el.focus();
    },

    reply(id) {
      const m = this.msgs.find(x => x.id === id);
      if (!m) return;
      this.replyTo = id;
      document.getElementById('composer-hint').innerHTML =
        '<span class="reply-bar">回复 ' + UI.esc(m.user.name) + '：' + UI.esc((m.content || '').slice(0, 40)) +
        ' <button class="link" onclick="Chat.cancelReply()">取消</button></span>';
    },
    cancelReply() { this.replyTo = 0; this.updateComposer(); },

    /* 点击回复引用：滚动定位到原消息并高亮 */
    jumpTo(id) {
      const el = document.getElementById('msg-' + id);
      if (!el) { UI.toast('原消息不在当前加载范围内', 'err'); return; }
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    },

    recall(id) {
      UI.confirm('确定撤回这条消息？', async () => {
        const r = await API.post('/api/chat/messages/' + id + '/recall', {});
        if (r.ok) UI.toast('已撤回', 'ok'); else UI.toast(r.msg, 'err');
      });
    },
    edit(id) {
      const m = this.msgs.find(x => x.id === id);
      if (!m) return;
      UI.modal('编辑消息', '<textarea id="edit-input" style="width:100%;height:120px;padding:10px;border:1px solid var(--border);border-radius:8px">' +
        UI.esc(m.content) + '</textarea>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Chat.doEdit(' + id + ')">保存</button>', 520);
    },
    async doEdit(id) {
      const v = document.getElementById('edit-input').value.trim();
      const r = await API.post('/api/chat/messages/' + id + '/edit', { content: v });
      UI.closeModal();
      UI.toast(r.ok ? '已保存' : r.msg, r.ok ? 'ok' : 'err');
    },

    async loadMembers() {
      if (!this.current) return;
      const r = await API.get('/api/chat/rooms/' + this.current + '/members');
      if (r.ok) this.members = r.data.list;
    },
    renderMembers() { if (this.current && document.getElementById('modal-root').innerHTML) this.showMembers(); },

    showMembers() {
      const canManage = this.canManage;
      const rows = this.members.map(m =>
        '<div class="list-item">' + UI.avatar(m.display_name, m.color, 0, m.avatar) +
        '<div class="li-main"><b>' + UI.esc(m.display_name) +
        (m.title ? '<span class="title-tag">' + UI.esc(m.title) + '</span>' : '') +
        (m.online ? ' <span class="tag ok">在线</span>' : '') +
        (m.muted ? ' <span class="tag danger" title="' + UI.esc(m.muted_until_str || '') + '">禁言中' + (m.muted_until_str ? '·' + m.muted_until_str.slice(11, 16) : '') + '</span>' : '') +
        '<small>' + UI.esc(m.dept || '—') + ' · ' + UI.esc(m.position || m.user_role) + '</small></div>' +
        (canManage ? '<div class="row-actions">' +
          (m.muted ? '<button class="btn sm" onclick="Chat.mute(' + m.user_id + ',0,\'\')">解除禁言</button>' :
            '<button class="btn sm danger" onclick="Chat.muteUI(' + m.user_id + ')">禁言</button>') +
          '<button class="btn sm danger" onclick="Chat.kick(' + m.user_id + ')">移除</button></div>' : '') + '</div>').join('');
      UI.modal('房间成员（' + this.members.length + '）',
        (canManage ? '<div class="toolbar"><button class="btn primary" onclick="Chat.inviteUI()">＋ 邀请成员</button></div>' : '') +
        (rows || '<div class="empty">暂无成员</div>'), '', 560);
    },
    async mute(uid, min, reason) {
      const r = await API.post('/api/chat/rooms/' + this.current + '/mute',
        { user_id: uid, minutes: min, reason: reason || '' });
      UI.toast(r.ok ? (r.data.msg || '操作成功') : r.msg, r.ok ? 'ok' : 'err');
      this.loadMembers(); this.showMembers();
    },
    /* 禁言窗口：内置弹窗（原生 prompt 在受限/内嵌浏览器会被拦截，导致窗口打不开） */
    muteUI(uid) {
      const m = this.members.find(x => x.user_id === uid);
      const name = m ? m.display_name : ('用户' + uid);
      const rm = this.room ? this.room.name : '';
      const opts = [[5, '5 分钟'], [30, '30 分钟'], [60, '1 小时'], [240, '4 小时'], [1440, '1 天'], [43200, '30 天']];
      UI.modal('禁言 · ' + UI.esc(name),
        '<p style="color:var(--text-2);font-size:13px;margin-bottom:10px">将在房间「' + UI.esc(rm) + '」内禁言该成员。</p>' +
        '<div class="mute-chips">' + opts.map(o =>
          '<button class="chip-btn" data-min="' + o[0] + '" onclick="Chat._mutePick(this,' + o[0] + ')">' +
          o[1] + '</button>').join('') + '</div>' +
        '<label class="field" style="margin-top:10px"><span>自定义时长（分钟，0 表示解除禁言）</span>' +
        '<input type="number" id="mu-min" min="0" max="525600" step="1" value="30"></label>' +
        '<label class="field"><span>禁言原因（可选，会写入系统消息与通知）</span>' +
        '<input id="mu-reason" maxlength="50" placeholder="如：发布违规内容"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn danger" onclick="Chat._muteOk(' + uid + ')">确定禁言</button>', 480);
      this._muteSel = 30;
      // 默认选中 30 分钟档
      const d = document.querySelector('.mute-chips [data-min="30"]');
      if (d) d.classList.add('on');
    },
    _mutePick(el, min) {
      document.querySelectorAll('.mute-chips .chip-btn').forEach(b => b.classList.remove('on'));
      el.classList.add('on');
      this._muteSel = min;
      const inp = document.getElementById('mu-min');
      if (inp) inp.value = min;
    },
    _muteOk(uid) {
      const min = parseInt(document.getElementById('mu-min').value, 10);
      if (isNaN(min) || min < 0 || min > 525600) return UI.toast('请输入 0 ~ 525600 之间的分钟数', 'err');
      const reason = UI.val('mu-reason');
      UI.closeModal();
      this.mute(uid, min, reason);
    },
    async kick(uid) {
      UI.confirm('确定移除该成员？其将被实时通知并移出房间。', async () => {
        const r = await API.del('/api/chat/rooms/' + this.current + '/members/' + uid);
        UI.toast(r.ok ? (r.msg || '已移除') : r.msg, r.ok ? 'ok' : 'err');
        this.loadMembers(); this.showMembers();
        this.loadRooms();
      }, true);
    },

    /* ---------- 邀请成员 ---------- */
    async inviteUI() {
      if (!this.contacts.length) {
        const r = await API.get('/api/users');
        if (r.ok) this.contacts = r.data.list;
      }
      const inRoom = new Set(this.members.map(m => m.user_id));
      const list = this.contacts.filter(u => !inRoom.has(u.id));
      UI.modal('邀请成员加入「' + UI.esc((this.room || {}).name || '') + '」',
        '<input id="inv-kw" placeholder="搜索姓名/部门" oninput="Chat.filterInvite(this.value)" style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:8px;margin-bottom:10px">' +
        '<div id="inv-list" style="max-height:320px;overflow:auto">' +
        (list.length ? list.map(u =>
          '<label class="list-item" style="cursor:pointer"><input type="checkbox" class="inv-chk" value="' + u.id + '" data-n="' + UI.esc(u.display_name) + '">' +
          UI.avatar(u.display_name, UI.colorOf(u.username), 0, u.avatar) +
          '<div class="li-main"><b>' + UI.esc(u.display_name) + (u.title ? '<span class="title-tag">' + UI.esc(u.title) + '</span>' : '') + '</b>' +
          '<small>' + UI.esc(u.dept || '—') + ' · ' + UI.esc(u.position || '') + '</small></div>' +
          (u.online ? '<span class="tag ok">在线</span>' : '<span class="tag gray">离线</span>') + '</label>').join('')
          : '<div class="empty">没有可邀请的用户</div>') + '</div>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Chat.doInvite()">邀请所选</button>', 560);
    },
    filterInvite(kw) {
      kw = (kw || '').toLowerCase();
      document.querySelectorAll('#inv-list .list-item').forEach(el => {
        el.style.display = el.textContent.toLowerCase().indexOf(kw) >= 0 ? '' : 'none';
      });
    },
    async doInvite() {
      const chks = Array.from(document.querySelectorAll('.inv-chk:checked'));
      if (!chks.length) return UI.toast('请至少勾选一名用户', 'err');
      for (const c of chks) {
        const r = await API.post('/api/chat/rooms/' + this.current + '/members', { user_id: parseInt(c.value, 10) });
        if (!r.ok) UI.toast((c.dataset.n || '') + '：' + r.msg, 'err');
      }
      UI.closeModal();
      UI.toast('邀请完成', 'ok');
      this.loadMembers(); this.loadRooms();
      setTimeout(() => this.showMembers(), 250);   // 邀请弹窗会覆盖成员列表，这里重新打开
    },

    /* ---------- 创建房间 ---------- */
    /* 注意：UI.modal 是单层覆盖式弹窗，若在“创建房间”里再开一个选人弹窗，
       外层表单会被整体替换掉，回填时目标输入框已不存在，按钮就会“点了没反应”。
       因此选人改为在同一个弹窗内切换面板，始终保留外层表单。 */
    newRoom() {
      UI.modal('创建房间',
        '<div id="nr-panel-main">' +
        '<label class="field"><span>房间名称</span><input id="nr-name" placeholder="如：项目组讨论"></label>' +
        '<label class="field"><span>主题/简介</span><input id="nr-topic" placeholder="一句话说明用途"></label>' +
        '<label class="field"><span>类型</span><select id="nr-type" onchange="Chat.typeHint()">' +
        '<option value="public">公开房间（全员可直接加入）</option>' +
        '<option value="private">私密房间（仅限邀请成员）</option>' +
        '<option value="dept">部门房间（本部门全员自动加入）</option></select></label>' +
        '<label class="field"><span>成员（用户名/姓名，逗号分隔）</span>' +
        '<input id="nr-members" placeholder="如：zhangsan,李四"></label>' +
        '<div class="field"><button class="btn sm" onclick="Chat.pickRoomMembers()">从通讯录选择 ▾</button>' +
        '<small id="nr-type-hint" style="display:block;color:var(--text-3);margin-top:6px">私密房间必须至少邀请 1 名成员</small></div>' +
        '</div>' +
        '<div id="nr-panel-pick" style="display:none">' +
        '<input id="nr-pick-kw" placeholder="搜索姓名/部门" oninput="Chat.filterPickList(this.value)" ' +
        'style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:8px">' +
        '<div id="nr-pick-list" style="max-height:320px;overflow:auto;margin-top:8px"></div>' +
        '<div style="display:flex;align-items:center;gap:10px;margin-top:10px">' +
        '<button class="btn" onclick="Chat.backToRoomForm()">← 返回房间信息</button>' +
        '<span style="color:var(--text-3);font-size:12px" id="nr-pick-count"></span></div>' +
        '</div>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Chat.doNewRoom()">创建</button>');
      this.typeHint();
    },
    typeHint() {
      const t = document.getElementById('nr-type').value;
      document.getElementById('nr-type-hint').textContent =
        t === 'private' ? '私密房间必须至少邀请 1 名成员，其他人无法搜索和加入'
          : t === 'dept' ? '部门房间将自动拉入你所在部门（' + (API.user.dept || '未设置') + '）的全部成员'
            : '公开房间全员可见，可自行加入';
    },
    async pickRoomMembers() {
      if (!document.getElementById('nr-panel-pick')) {
        return UI.toast('创建房间窗口已关闭，请重新打开', 'err');
      }
      if (!this.contacts.length) {
        const r = await API.get('/api/users');
        if (!r.ok) return UI.toast(r.msg, 'err');
        this.contacts = r.data.list;
      }
      const picked = this._pickSet();
      document.getElementById('nr-pick-list').innerHTML =
        this.contacts.filter(u => u.id !== API.user.id).map(u =>
          '<label class="list-item" style="cursor:pointer">' +
          '<input type="checkbox" class="nr-pick-chk" value="' + UI.esc(u.username) + '"' +
          (picked[u.username] ? ' checked' : '') + ' onchange="Chat.updatePickCount()">' +
          UI.avatar(u.display_name, UI.colorOf(u.username), 0, u.avatar) +
          '<div class="li-main"><b>' + UI.esc(u.display_name) +
          (u.title ? '<span class="title-tag">' + UI.esc(u.title) + '</span>' : '') +
          '</b><small>' + UI.esc(u.dept || '—') + ' · ' + UI.esc(u.username) + '</small></div>' +
          (u.online ? '<span class="tag ok">在线</span>' : '<span class="tag gray">离线</span>') +
          '</label>').join('') || '<div class="empty">通讯录暂无可选成员</div>';
      document.getElementById('nr-pick-kw').value = '';
      document.getElementById('nr-panel-main').style.display = 'none';
      document.getElementById('nr-panel-pick').style.display = '';
      this.updatePickCount();
      const kw = document.getElementById('nr-pick-kw');
      if (kw) kw.focus();
    },
    /* 以“成员”输入框为准做双向同步，重开面板时已选中的仍然保持勾选 */
    _pickSet() {
      const set = {};
      const v = UI.val('nr-members');
      v.split(/[,，;；\s]+/).filter(Boolean).forEach(x => { set[x] = 1; });
      return set;
    },
    backToRoomForm() {
      const names = Array.from(document.querySelectorAll('.nr-pick-chk:checked')).map(c => c.value);
      const box = document.getElementById('nr-members');
      if (box) box.value = names.join(',');
      const pick = document.getElementById('nr-panel-pick');
      const main = document.getElementById('nr-panel-main');
      if (pick) pick.style.display = 'none';
      if (main) main.style.display = '';
    },
    updatePickCount() {
      const el = document.getElementById('nr-pick-count');
      if (el) el.textContent = '已选 ' + document.querySelectorAll('.nr-pick-chk:checked').length + ' 人';
    },
    filterPickList(kw) {
      kw = (kw || '').toLowerCase();
      document.querySelectorAll('#nr-pick-list .list-item').forEach(el => {
        el.style.display = el.textContent.toLowerCase().indexOf(kw) >= 0 ? '' : 'none';
      });
    },
    async doNewRoom() {
      const pick = document.getElementById('nr-panel-pick');
      if (pick && pick.style.display !== 'none') {        // 正在选人：先回填并返回，避免误创建
        this.backToRoomForm();
        return UI.toast('已记录所选成员，请确认后点“创建”', 'ok');
      }
      if (!document.getElementById('nr-name')) {
        return UI.toast('创建房间窗口已关闭，请重新打开', 'err');
      }
      const r = await API.post('/api/chat/rooms', {
        name: UI.val('nr-name'), topic: UI.val('nr-topic'),
        type: document.getElementById('nr-type').value, members: UI.val('nr-members')
      });
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }   // 失败保留弹窗便于修改
      UI.closeModal();
      UI.toast(r.data.msg || '房间已创建', 'ok');
      this.loadRooms();
      setTimeout(() => this.openRoom(r.data.id), 300);
    },

    async startDM(uid) {
      const r = await API.post('/api/chat/dm', { user_id: uid });
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      App.go('chat');
      this.loadRooms();
      setTimeout(() => this.openRoom(r.data.id), 300);
    },

    searchInRoom() {
      UI.prompt({ title: '房间消息搜索', label: '关键词', placeholder: '输入要搜索的内容',
        okText: '搜索', onOk: kw => { if (kw) this.doSearch(kw); } });
    },
    async doSearch(kw) {
      const r = await API.get('/api/chat/search', { q: kw, room_id: this.current });
      if (!r.ok) return UI.toast(r.msg, 'err');
      const list = r.data.list || [];
      UI.modal('搜索结果（' + list.length + '）',
        list.map(m => '<div class="list-item"><div class="li-main"><b>' + UI.esc(m.user.name) +
          '</b><small>' + UI.time(m.created_at, 'full') + ' · ' + UI.esc(m.room_name || '') +
          '</small><div style="margin-top:4px">' + UI.nl2br(m.content) + '</div></div>' +
          '<button class="btn sm" onclick="UI.closeModal();Chat.openRoom(' + m.room_id + ')">跳转</button></div>').join('') ||
        '<div class="empty">没有找到相关消息</div>', '', 620);
    },
    refresh() { this.loadMessages(); this.loadRooms(); },

    /* ---------- 右键菜单 ---------- */
    roomMenu(id) {
      const r = (this.rooms || []).filter(x => x.id === id)[0];
      if (!r) return [];
      const joined = r.joined !== false;
      return [
        { label: '打开会话', icon: 'open', onClick: () => this.openRoom(r.id) },
        (joined && r.unread > 0) ? { label: '标记已读', icon: 'read', hint: r.unread + ' 条', onClick: () => this.markRead(r.id) } : null,
        joined ? { label: '查看成员', icon: 'users', onClick: () => { this.openRoom(r.id); setTimeout(() => this.showMembers(), 400); } } : null,
        !joined ? { label: '加入房间', icon: 'exit', onClick: () => this.joinRoom(r.id) } : null,
        { sep: true },
        { label: '复制房间名', icon: 'copy', onClick: () => App.copyText(r.name) },
        (joined && r.type !== 'dm' && r.type !== 'announce')
          ? { label: '退出房间', icon: 'exit', danger: true, onClick: () => this.confirmLeave(r.id, r.name) } : null
      ].filter(Boolean);
    },

    msgMenu(id) {
      const m = (this.msgs || []).filter(x => x.id === id)[0];
      if (!m) return [];
      const mine = !!(m.user && API.user && m.user.id === API.user.id);
      const canDel = (API.user.perms || []).indexOf('message.moderate') >= 0;
      const sys = m.type === 'system' || m.recalled;
      return [
        m.content ? { label: '复制文字', icon: 'copy', onClick: () => App.copyText(m.content) } : null,
        (m.user && m.user.name) ? { label: '复制「发送人：内容」', icon: 'copy', onClick: () => App.copyText((m.user.name || '') + '：' + (m.content || '')) } : null,
        !sys ? { label: '引用回复', icon: 'reply', onClick: () => this.reply(m.id) } : null,
        m.reply ? { label: '查看原消息', icon: 'open', onClick: () => this.jumpTo(m.reply.id) } : null,
        (mine && !sys) ? { sep: true } : null,
        (mine && !sys) ? { label: '编辑消息', icon: 'edit', onClick: () => this.edit(m.id) } : null,
        (mine && !sys) ? { label: '撤回消息', icon: 'undo', onClick: () => this.recall(m.id) } : null,
        (canDel && !m.recalled) ? { sep: true } : null,
        (canDel && !m.recalled) ? { label: '删除消息（管理员）', icon: 'trash', danger: true, onClick: () => this.adminDelete(m.id) } : null
      ].filter(Boolean);
    },

    markRead(id) {
      API.post('/api/chat/rooms/' + id + '/read', { msg_id: 0 }).then(r => {
        if (r.ok) this.loadRooms();
      });
    },

    /* 已读回执详情：谁读了这条消息（只对本人发送的消息开放） */
    async showRead(mid) {
      const m = (this.msgs || []).filter(x => x.id === mid)[0];
      if (!m || !this.current) return;
      const r = await API.get('/api/chat/rooms/' + this.current + '/readers', { msg_id: mid });
      if (!r.ok) { UI.toast(r.msg || '读取已读详情失败', 'err'); return; }
      const d = r.data || {};
      const readList = d.readers || [];
      const unreadList = d.unread || [];
      const row = (u, ok) => '<div class="list-item"><div class="li-main"><b>' +
        UI.esc(u.name || u.username || ('用户' + u.id)) + '</b><small>' +
        UI.esc(u.dept || '') + '</small></div><span class="tag ' +
        (ok ? 'ok' : '') + '">' + (ok ? '已读' : '未读') + '</span></div>';
      const html = (readList.length
        ? '<div class="sec-title">已读（' + readList.length + '）</div>' + readList.map(u => row(u, true)).join('')
        : '<div class="empty">还没有人读这条消息</div>') +
        (unreadList.length
          ? '<div class="sec-title" style="margin-top:12px">未读（' + unreadList.length + '）</div>' +
            unreadList.map(u => row(u, false)).join('')
          : '');
      UI.modal('消息已读详情', html, '', 420);
    },

    confirmLeave(id, name) {
      UI.confirm('退出房间「' + name + '」后将不再接收该房间消息，确定继续？', async () => {
        const r = await API.post('/api/chat/rooms/' + id + '/leave', {});
        UI.toast(r.msg, r.ok ? 'ok' : 'err');
        if (r.ok && this.current === id) {
          this.current = 0; this.room = null; this.msgs = []; this.ids = {};
          const box = document.getElementById('msg-list');
          if (box) box.innerHTML = '<div class="empty">还没有消息，来说两句吧</div>';
          const nameEl = document.getElementById('chat-room-name');
          if (nameEl) nameEl.textContent = '请选择房间';
          const topicEl = document.getElementById('chat-room-topic');
          if (topicEl) topicEl.textContent = '';
        }
        this.loadRooms();
      }, true);
    },

    adminDelete(id) {
      UI.confirm('删除这条消息？删除后所有成员都将看不到。', async () => {
        const r = await API.del('/api/chat/messages/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err');
        if (r.ok) {
          this.msgs = this.msgs.filter(x => x.id !== id);
          this.renderMessages();
        }
      }, true);
    }
  };

  window.Chat = Chat;
})();
