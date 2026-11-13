    /* ---------- 仪表盘（对齐 实例页面/admin.html） ---------- */
    async dash() {
      const r = await API.get('/api/admin/dashboard');
      const box = document.getElementById('admin-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data, u = API.user || {};
      const n = v => (v === undefined || v === null || isNaN(v) ? 0 : Number(v));
      const hour = new Date().getHours();
      const greet = hour < 6 ? '凌晨好' : hour < 11 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
      const fmt = v => n(v).toLocaleString();
      const metric = (title, value, foot, icon, cls) =>
        '<div class="metric"><div class="metric-top"><span>' + title + '</span>' +
        '<div class="metric-icon ' + (cls || '') + '">' + icon + '</div></div>' +
        '<div class="metric-value">' + value + '</div><div class="metric-foot">' + foot + '</div></div>';
      const trend = d.trend || [];
      const maxMsg = Math.max(1, ...trend.map(t => n(t.msg)));
      const step = trend.length > 1 ? 600 / (trend.length - 1) : 0;
      const pts = trend.map((t, i) => [35 + i * step, 175 - (n(t.msg) / maxMsg) * 140]);
      const line = pts.length ? pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ') : 'M35 175 L635 175';
      const area = line + ' L' + (pts.length ? pts[pts.length - 1][0].toFixed(1) : '635') + ' 185 L35 185 Z';
      const days = trend.map((t, i) => '<text x="' + (22 + i * step) + '" y="203">' + UI.esc(t.day) + '</text>').join('');
      const logs = (d.recent_logs || []).slice(0, 6);
      const srv = d.server || {};
      box.innerHTML =
        '<div class="titlebar"><div><h1>' + greet + '，' + UI.esc(u.display_name || '管理员') + ' 👋</h1>' +
        '<p>这里是云聊管理中心，查看企业协作平台的运行概况。</p></div>' +
        '<div class="actions"><button class="btn" onclick="Admin.render()">刷新数据</button>' +
        '<button class="btn primary" onclick="Admin.newUser()">＋ 新增成员</button></div></div>' +
        '<section class="metrics">' +
          metric('成员总数', fmt(d.users),
            '<span class="trend-up">待审核 ' + fmt(d.users_pending) + '</span><span>· 今日登录 ' + fmt(d.login_today) + '</span>',
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4.5 5"/></svg>') +
          metric('今日消息', fmt(d.messages_today),
            '<span class="trend-up">累计 ' + fmt(d.messages) + '</span><span>条</span>',
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 11.5a8 8 0 0 1-8 8 9 9 0 0 1-3.5-.7L4 20l1.3-4A8 8 0 1 1 20 11.5Z"/><path d="M8 12h.01M12 12h.01M16 12h.01" stroke-width="2.5"/></svg>', 'purple') +
          metric('在线成员', fmt(d.online),
            '<span style="color:#8795aa">实时状态 · 占成员 ' + (n(d.users) ? Math.round(n(d.online) * 1000 / n(d.users)) / 10 : 0) + '%</span>',
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>', 'green') +
          metric('文件存储', UI.esc(d.files_size_str || '—'),
            '<span style="color:#8795aa">资料 ' + fmt(d.materials) + ' 个 · 聊天附件合计</span>',
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.1l2 2.4h7.9A2.5 2.5 0 0 1 21 9.9v7.6a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-10Z"/><path d="M3 11h18"/></svg>', 'orange') +
        '</section>' +
        '<section class="dashgrid">' +
          '<div class="dpanel"><div class="panelhead"><div><h2>消息活跃趋势</h2><p>近 ' + trend.length + ' 天平台消息发送情况</p></div>' +
          '<span class="statuspill">实时</span></div>' +
          '<div class="chartwrap"><div class="chart-legend"><span><i class="legenddot"></i>消息数量</span>' +
          '<span><i class="legenddot two"></i>新增用户</span></div>' +
          '<svg class="chart" viewBox="0 0 650 210" preserveAspectRatio="none" role="img" aria-label="消息趋势">' +
          '<defs><linearGradient id="areaFill" x1="0" x2="0" y1="0" y2="1">' +
          '<stop offset="0" stop-color="#3987f6" stop-opacity=".19"/><stop offset="1" stop-color="#3987f6" stop-opacity="0"/></linearGradient></defs>' +
          '<path class="gridline" d="M35 25H635M35 65H635M35 105H635M35 145H635M35 185H635"/>' +
          '<text x="2" y="28">' + maxMsg + '</text><text x="2" y="108">' + Math.round(maxMsg / 2) + '</text>' +
          '<text x="15" y="188">0</text>' + days +
          '<path class="area" d="' + area + '"/><path class="line" d="' + line + '"/></svg></div></div>' +
          '<div class="dpanel"><div class="panelhead"><div><h2>系统运行状态</h2><p>核心服务与资源概况</p></div>' +
          '<span class="statuspill">运行正常</span></div><div class="status-list">' +
            '<div class="statusrow"><div class="statusicon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/></svg></div>' +
            '<div class="statuscopy"><b>应用服务</b><small>v' + UI.esc(srv.version || '') + ' · 已运行 ' + fmt(srv.uptime_hours) + ' 小时</small></div><span class="statuspill">正常</span></div>' +
            '<div class="statusrow"><div class="statusicon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg></div>' +
            '<div class="statuscopy"><b>数据库</b><small>' + UI.esc(srv.db_size || '') + ' · 附件 ' + UI.esc(d.files_size_str || '') + '</small></div><span class="statuspill">正常</span></div>' +
            '<div class="statusrow"><div class="statusicon orange"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 12a8 8 0 0 1 16 0M2 12h4M18 12h4M12 4V2M12 22v-4"/><circle cx="12" cy="12" r="3"/></svg></div>' +
            '<div class="statuscopy"><b>实时消息服务</b><small>WebSocket · 在线 ' + fmt(d.online) + ' 人</small></div><span class="statuspill">正常</span></div>' +
            '<div class="statusrow"><div class="statusicon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 20 6v5.5c0 4.8-3.3 8-8 9.5-4.7-1.5-8-4.7-8-9.5V6l8-3Z"/><path d="m9 12 2 2 4-4"/></svg></div>' +
            '<div class="statuscopy"><b>安全状态</b><small>登录保护与访问策略</small></div><span class="statuspill">正常</span></div>' +
          '</div></div>' +
        '</section>' +
        '<section class="tablecard"><div class="panelhead"><div><h2>最近操作日志</h2><p>管理员行为与会话审计</p></div>' +
        '<button class="btn" onclick="Admin.tab(\'logs\')">查看全部</button></div>' +
        '<div class="tablewrap"><table><thead><tr><th>操作</th><th>操作人</th><th>对象</th><th>详情</th><th>时间</th></tr></thead><tbody>' +
        (logs.length ? logs.map(l => '<tr><td><b>' + UI.esc(l.action) + '</b></td><td>' + UI.esc(l.actor || '系统') + '</td>' +
          '<td>' + UI.esc(l.target || '—') + '</td><td>' + UI.esc(l.detail || '—') + '</td><td>' + UI.esc(l.time) + '</td></tr>').join('')
          : '<tr><td colspan="5"><div class="empty">暂无日志</div></td></tr>') +
        '</tbody></table></div><div class="footerline"><span>数据来源：服务端实时统计</span><span>共 ' + logs.length + ' 条最近记录</span></div></section>';
    },
