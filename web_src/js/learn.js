/* ============ 学习中心 ============ */
(function () {
  'use strict';
  const Learn = {
    cur: 'today', page: 1, kw: '', cid: 0,
    exam: null, recId: 0, answers: {}, deadline: 0, timer: null,

    async render() {
      const box = document.getElementById('learn-body');
      if (!box) return;
      document.querySelectorAll('[data-ltab]').forEach(b => b.classList.toggle('active', b.dataset.ltab === this.cur));
      if (this.cid) return this.courseDetail(this.cid);
      box.innerHTML = '<div class="empty">加载中…</div>';
      if (this.cur === 'today') return this.today();
      if (this.cur === 'courses') return this.courses();
      if (this.cur === 'classes') return this.classes();
      if (this.cur === 'materials') return this.materials();
      if (this.cur === 'lab') return this.lab();
      if (this.cur === 'exams') return this.exams();
      if (this.cur === 'homework') return this.homework();
      if (this.cur === 'mine') return this.mine();
    },

    /* ---------- 今日（登录后的第一屏）----------
       四张待办卡片。学生看「待交作业 / 可考考试」，
       教师看「待批阅 / 待阅卷」——同一个接口，后端按角色给不同口径。 */
    async today() {
      const box = document.getElementById('learn-body');
      const r = await API.get('/api/learn/my/todo');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      const isTeacher = d.role_mode === 'teacher';

      const card = (cls, icon, title, value, sub, onclick) =>
        '<div class="todo-card ' + cls + '"' + (onclick ? ' onclick="' + onclick + '"' : '') + '>' +
        '<div class="tc-head"><span class="tc-ico">' + icon + '</span><b>' + title + '</b></div>' +
        '<div class="tc-value">' + value + '</div>' +
        '<div class="tc-sub">' + sub + '</div></div>';

      const courseSub = d.course_count
        ? (d.courses[0] ? UI.esc(d.courses[0].title) + ' · ' + (d.courses[0].progress || 0) + '%' : '进行中')
        : '暂无进行中的课程';

      // 第二张卡：学生=待交作业，教师=待批阅
      let hwCard;
      if (isTeacher) {
        const n = d.to_grade_homework || 0;
        hwCard = card(n ? 'warn' : '', '📝', '待批阅作业', n + ' 份',
          n ? ('最早：' + UI.esc(d.homework[0] ? d.homework[0].title : '')) : '全部批阅完成',
          "Learn.tab('homework')");
      } else {
        const n = d.pending_homework || 0;
        const od = d.overdue_homework || 0;
        hwCard = card(od ? 'urgent' : (n ? 'warn' : ''), '📝', '待交作业', n + ' 份',
          n ? ('最早截止：' + UI.esc(d.homework[0] ? d.homework[0].deadline_str : '')
               + (od ? '　<span class="tc-warn">' + od + ' 项已逾期</span>' : ''))
            : (od ? '<span class="tc-warn">' + od + ' 项已逾期未交</span>' : '全部已完成'),
          "Learn.tab('homework')");
      }

      // 第三张卡：学生=可考考试，教师=待阅卷
      let examCard;
      if (isTeacher) {
        const n = d.to_grade_exams || 0;
        examCard = card(n ? 'warn' : '', '🎯', '待阅卷', n + ' 份',
          n ? '主观题需人工评分' : '暂无待阅卷',
          "Learn.tab('exams')");
      } else {
        const n = d.available_exams || 0;
        examCard = card(n ? 'warn' : '', '🎯', '可参加的考试', n + ' 场',
          n ? ('最近：' + UI.esc(d.exams[0] ? d.exams[0].title : '')) : '暂无待考',
          "Learn.tab('exams')");
      }

      let html = '<div class="todo-grid">' +
        card('', '📚', '进行中的课程', d.course_count + ' 门', courseSub, "Learn.tab('courses')") +
        hwCard + examCard +
        card('', '🔥', '连续学习', d.streak + ' 天',
          d.checked_today ? '<span class="tc-ok">今日已打卡 · 积分 ' + d.points + '</span>'
            : '<button class="btn sm primary" onclick="event.stopPropagation();Learn.checkin()">立即打卡</button>',
          '') +
        '</div>';

      // 待办明细
      const rows = [];
      (d.homework || []).slice(0, 3).forEach(h => {
        const tag = isTeacher
          ? '<span class="hw-state warn">' + (h.to_grade || 0) + ' 份待批</span>'
          : (h.overdue ? '<span class="tag danger">已逾期</span>'
            : (h.left_days >= 0 && h.left_days <= 2 ? '<span class="tag warn">即将截止</span>' : ''));
        rows.push('<div class="list-item"><div>📝</div><div class="li-main"><b>' + UI.esc(h.title) +
          '</b><small>' + UI.esc(h.course_title || '未关联课程') + ' · 截止 ' + UI.esc(h.deadline_str) +
          '</small></div>' + tag +
          '<button class="btn sm" onclick="event.stopPropagation();Learn.tab(\'homework\')">' +
          (isTeacher ? '去批阅' : '去提交') + '</button></div>');
      });
      (d.exams || []).slice(0, 3).forEach(e => {
        if (isTeacher) {
          rows.push('<div class="list-item"><div>🎯</div><div class="li-main"><b>' + UI.esc(e.title) +
            '</b><small>' + (e.to_grade || 0) + ' 份待阅卷</small></div>' +
            '<button class="btn sm primary" onclick="event.stopPropagation();Learn.tab(\'exams\')">去阅卷</button></div>');
        } else {
          rows.push('<div class="list-item"><div>🎯</div><div class="li-main"><b>' + UI.esc(e.title) +
            '</b><small>' + e.duration + ' 分钟 · 截止 ' + UI.esc(e.end_str) + '</small></div>' +
            '<button class="btn sm primary" onclick="event.stopPropagation();Learn.startExam(' + e.id + ')">开始</button></div>');
        }
      });

      if (rows.length) {
        html += '<div class="panel"><div class="panel-head"><b>近期待办</b>' +
          '<span class="muted">按截止时间排序</span></div><div>' + rows.join('') + '</div></div>';
      }
      box.innerHTML = html;
    },

    /* ---------- 课程 ---------- */
    async courses() {
      const r = await API.get('/api/learn/courses', { q: this.kw, page: this.page, page_size: 12 });
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const list = r.data.list || [];
      const canManage = API.user.perms.indexOf('course.manage') >= 0;
      box.innerHTML =
        '<div class="toolbar"><input id="lq-kw" placeholder="搜索课程" value="' + UI.esc(this.kw) + '" onkeydown="if(event.key===\'Enter\')Learn.search(this.value)">' +
        '<button class="btn" onclick="Learn.search(document.getElementById(\'lq-kw\').value)">搜索</button>' +
        (canManage ? '<button class="btn primary" onclick="Learn.newCourse()" style="margin-left:auto">＋ 新建课程</button>' : '') + '</div>' +
        (list.length ? '<div class="cards">' + list.map(c =>
          '<div class="card"><div class="card-cover" style="background:linear-gradient(135deg,' + (c.cover_color || '#2f6fed') + ',#12a67a)">' +
          '<b>' + UI.esc(c.title) + '</b><small>' + UI.esc(c.subtitle || c.category) + '</small></div>' +
          '<div class="card-body"><p>' + UI.esc(c.desc || '暂无简介') + '</p>' +
          '<div style="display:flex;gap:10px;font-size:12px;color:var(--text-2);flex-wrap:wrap">' +
          '<span>讲师：' + UI.esc(c.teacher_name) + '</span><span>' + c.lesson_count + ' 课时</span>' +
          '<span>' + c.material_count + ' 资料</span><span>' + c.enrolled_count + ' 人报名</span></div>' +
          (c.enrolled ? '<div style="margin-top:8px"><div class="progress"><i style="width:' + c.progress + '%"></i></div>' +
            '<small style="color:var(--text-3)">学习进度 ' + c.progress + '%</small></div>' : '') +
          '</div><div class="card-foot">' +
          '<button class="btn sm" onclick="Learn.openCourse(' + c.id + ')">进入学习</button>' +
          (c.enrolled ? '<span class="tag ok">已报名</span>' : '<button class="btn sm primary" onclick="Learn.enroll(' + c.id + ')">报名</button>') +
          (c.required ? '<span class="tag danger" style="margin-left:auto">必修</span>' : '') + '</div></div>').join('') + '</div>'
          : '<div class="empty">暂无课程</div>') +
        UI.pager(r.data.total || 0, this.page, 12, 'Learn.goPage');
    },
    search(kw) { this.kw = kw; this.page = 1; this.render(); },
    goPage(p) { this.page = p; this.render(); },

    async openCourse(id) { this.cid = id; document.getElementById('view-sub').textContent = '课程详情'; this.render(); },

    async courseDetail(id) {
      const r = await API.get('/api/learn/courses/' + id);
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const c = r.data;
      const finishMap = {};
      (c.finished || []).forEach(x => finishMap[x] = 1);
      const lessons = (c.lessons || []).map(l =>
        '<div class="list-item"><div style="width:28px;text-align:center">' + (finishMap[l.id] ? '✅' : '📖') + '</div>' +
        '<div class="li-main"><b>' + UI.esc(l.title) + '</b><small>' + (l.minutes || 15) + ' 分钟</small>' +
        '<div style="margin-top:6px;color:var(--text-2);font-size:13px;white-space:pre-wrap">' + UI.nl2br(l.content || '') + '</div></div>' +
        '<button class="btn sm ' + (finishMap[l.id] ? '' : 'primary') + '" onclick="Learn.finishLesson(' + l.id + ',' + id + ')">' +
        (finishMap[l.id] ? '已完成' : '标记完成') + '</button></div>').join('');
      const mats = (c.materials || []).map(m =>
        '<div class="list-item"><div>📄</div><div class="li-main"><b>' + UI.esc(m.title) + '</b>' +
        '<small>' + UI.esc(m.category) + ' · ' + UI.time(m.created_at) + '</small></div>' +
        (m.file_id ? '<a class="btn sm" href="/api/files/' + m.file_id + '/download?token=' + API.token + '" download>下载</a>' : '') + '</div>').join('');
      const exams = (c.exams || []).map(e =>
        '<div class="list-item"><div>📝</div><div class="li-main"><b>' + UI.esc(e.title) + '</b>' +
        '<small>满分 ' + e.total_score + ' · 及格 ' + e.pass_score + ' · ' + e.duration + ' 分钟</small></div>' +
        '<button class="btn sm primary" onclick="Learn.startExam(' + e.id + ')">开始考试</button></div>').join('');
      box.innerHTML =
        '<div class="panel"><div class="panel-head"><b>' + UI.esc(c.title) + '</b>' +
        '<span><button class="btn sm" onclick="Learn.back()">返回列表</button>' +
        (c.enrolled ? '<span class="tag ok">已报名</span>' : '<button class="btn sm primary" onclick="Learn.enroll(' + c.id + ')">报名课程</button>') +
        '</span></div><div class="panel-body">' +
        '<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">' +
        '<div style="flex:1;min-width:220px"><p style="color:var(--text-2)">' + UI.esc(c.desc || '') + '</p>' +
        '<small style="color:var(--text-3)">讲师：' + UI.esc(c.teacher_name) + ' · 分类：' + UI.esc(c.category) +
        ' · 已报名 ' + c.enrolled_count + ' 人</small></div>' +
        '<div style="width:180px"><div class="progress"><i style="width:' + (c.progress || 0) + '%"></i></div>' +
        '<small style="color:var(--text-3)">进度 ' + (c.progress || 0) + '%</small></div></div></div></div>' +
        '<div class="panel"><div class="panel-head"><b>课时（' + (c.lessons || []).length + '）</b>' +
        (c.can_manage ? '<button class="btn sm" onclick="Learn.newLesson(' + c.id + ')">＋ 添加课时</button>' : '') + '</div>' +
        '<div>' + (lessons || '<div class="empty">暂无课时</div>') + '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>课程资料（' + (c.materials || []).length + '）</b></div>' +
        '<div>' + (mats || '<div class="empty">暂无资料</div>') + '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>课程考试</b></div><div>' +
        (exams || '<div class="empty">暂无考试</div>') + '</div></div>';
    },
    back() { this.cid = 0; document.getElementById('view-sub').textContent = ''; this.render(); },

    async enroll(id) {
      const r = await API.post('/api/learn/courses/' + id + '/enroll', {});
      UI.toast(r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    async finishLesson(lid, cid) {
      const r = await API.post('/api/learn/lessons/' + lid + '/finish', {});
      if (r.ok) { UI.toast('学习进度 ' + r.data.progress + '%', 'ok'); API.post('/api/learn/study-log', { course_id: cid, minutes: 15, action: 'lesson' }); }
      else UI.toast(r.msg, 'err');
      this.render();
    },
    newCourse() {
      API.get('/api/learn/classes', {}).then(r => {
        const cls = (r.ok && r.data.list) || [];
        const opts = '<option value="0">不关联班级</option>' +
          cls.map(c => '<option value="' + c.id + '">' + UI.esc(c.name) + '</option>').join('');
        UI.modal('新建课程',
          '<label class="field"><span>课程名称</span><input id="nc-title"></label>' +
          '<label class="field"><span>副标题</span><input id="nc-sub"></label>' +
          '<label class="field"><span>分类</span><input id="nc-cat" value="通用"></label>' +
          '<label class="field"><span>所属班级</span><select id="nc-class">' + opts + '</select></label>' +
          '<label class="field"><span>简介</span><textarea id="nc-desc" style="width:100%;height:90px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>',
          '<button class="btn" onclick="UI.closeModal()">取消</button>' +
          '<button class="btn primary" onclick="Learn.doNewCourse()">创建</button>');
      });
    },
    async doNewCourse() {
      const r = await API.post('/api/learn/courses', {
        title: UI.val('nc-title'), subtitle: UI.val('nc-sub'), category: UI.val('nc-cat'),
        desc: UI.val('nc-desc'),
        class_id: parseInt((document.getElementById('nc-class') || {}).value || '0', 10) || 0
      });
      UI.closeModal();
      UI.toast(r.ok ? '课程已创建' : r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    newLesson(cid) {
      UI.modal('添加课时',
        '<label class="field"><span>课时标题</span><input id="nl-title"></label>' +
        '<label class="field"><span>学习内容</span><textarea id="nl-content" style="width:100%;height:160px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>' +
        '<label class="field"><span>建议时长（分钟）</span><input id="nl-min" value="15"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Learn.doNewLesson(' + cid + ')">添加</button>');
    },
    async doNewLesson(cid) {
      const r = await API.post('/api/learn/courses/' + cid + '/lessons', {
        title: UI.val('nl-title'), content: document.getElementById('nl-content').value, minutes: UI.val('nl-min')
      });
      UI.closeModal();
      UI.toast(r.ok ? '已添加' : r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },

    /* ---------- 题库刷题（嵌入知否平台）----------
       两个进程同机部署，云聊负责"你是谁"，知否负责自己的会话。
       同源时用 iframe 内嵌；跨端口时浏览器仍允许 iframe 显示，
       但写 localStorage 会被同源策略挡住，因此退化为新标签页打开。 */
    async lab() {
      const box = document.getElementById('learn-body');
      box.innerHTML = '<div class="empty">正在连接题库平台…</div>';
      const r = await API.get('/api/learn/lab/status');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data || {};
      if (!d.enabled) {
        const can = API.user.perms.indexOf('admin.access') >= 0;
        box.innerHTML = '<div class="panel"><div class="empty">题库平台未启用' +
          (can ? '<div style="margin-top:10px"><button class="btn primary" onclick="App.go(\'admin\')">去后台开启</button></div>' : '') +
          '</div></div>';
        return;
      }
      const sameOrigin = (function () {
        try { return new URL(d.url).origin === location.origin; } catch (e) { return false; }
      })();
      box.innerHTML =
        '<div class="lab-bar">' +
          '<span class="lab-dot ' + (d.reachable ? 'on' : 'off') + '"></span>' +
          '<b>知否刷题平台</b>' +
          '<span class="lab-url">' + UI.esc(d.url) + '</span>' +
          (d.reachable ? '' : '<span class="lab-warn">' + UI.esc(d.msg) + '</span>') +
          '<button class="btn sm" style="margin-left:auto" onclick="Learn.labOpen()">新窗口打开</button>' +
          '<button class="btn sm" onclick="Learn.lab()">刷新</button>' +
        '</div>' +
        '<div id="lab-frame-wrap" class="lab-frame-wrap">' +
          '<div class="empty">点「进入题库」开始刷题（共用云聊账号，无需再登录）</div>' +
        '</div>';
      this._labInfo = d;
      const wrap = document.getElementById('lab-frame-wrap');
      if (wrap) {
        wrap.innerHTML = '<div class="empty" style="padding:40px 0">' +
          '<button class="btn primary" onclick="Learn.labEnter()">进入题库</button>' +
          '<div style="margin-top:10px;font-size:12px;color:var(--text-3)">' +
          '将自动使用当前云聊账号登录，无需再输入密码</div></div>';
      }
    },

    /* 进入题库：先换票据，再把票据注入知否的 localStorage */
    async labEnter() {
      const wrap = document.getElementById('lab-frame-wrap');
      if (!wrap) return;
      wrap.innerHTML = '<div class="empty">正在单点登录…</div>';
      const r = await API.post('/api/learn/lab/sso', {});
      if (!r.ok) {
        wrap.innerHTML = '<div class="empty">' + UI.esc(r.msg) +
          '<div style="margin-top:10px"><button class="btn sm" onclick="Learn.labOpen()">改为新窗口打开</button></div></div>';
        return;
      }
      const d = r.data || {};
      let sameOrigin = false;
      try { sameOrigin = new URL(d.url).origin === location.origin; } catch (e) { }
      if (!sameOrigin) {
        wrap.innerHTML = '<div class="empty">题库平台与云聊不同源（' + UI.esc(d.url) +
          '），浏览器不允许跨站写入登录态。<br>请用「新窗口打开」进入。' +
          '<div style="margin-top:12px"><button class="btn primary" onclick="Learn.labOpen()">新窗口打开</button></div></div>';
        return;
      }
      wrap.innerHTML = '<div class="empty">正在加载题库…</div>';
      const f = document.createElement('iframe');
      f.className = 'lab-frame';
      f.src = d.url.replace(/\/$/, '') + '/';
      wrap.innerHTML = '';
      wrap.appendChild(f);
      /* 知否前端的 token 键名是 exam_token（见其 assets/app.js 的 TOKEN_KEY） */
      setTimeout(function () {
        try {
          f.contentWindow.localStorage.setItem('exam_token', d.token);
          f.contentWindow.location.reload();
        } catch (e) { /* 跨源或未加载完，忽略 */ }
      }, 1200);
    },

    /* 新窗口打开：把票据拼在 URL 上，由知否前端自己接收 */
    async labOpen() {
      const info = this._labInfo || {};
      const r = await API.post('/api/learn/lab/sso', {});
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      const d = r.data || {};
      /* 带 token 打开一个中转页：先写 localStorage 再跳首页。
         直接放 hash 里知否不认识，用 data: 或 about:blank 又会被拦，
         所以由云聊自己出一个极小的跳转页。 */
      const target = (d.url || info.url || '').replace(/\/$/, '');
      const bridge = location.origin + '/sso-bridge?to=' + encodeURIComponent(target) +
        '&token=' + encodeURIComponent(d.token || '');
      window.open(bridge, '_blank', 'noopener');
    },

    /* ---------- 资料库 ---------- */
    async materials() {
      const r = await API.get('/api/learn/materials', { q: this.kw, page: this.page, page_size: 20 });
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const can = API.user.perms.indexOf('material.manage') >= 0;
      box.innerHTML =
        '<div class="toolbar"><input id="mq" placeholder="搜索资料" value="' + UI.esc(this.kw) + '" onkeydown="if(event.key===\'Enter\')Learn.search(this.value)">' +
        '<button class="btn" onclick="Learn.search(document.getElementById(\'mq\').value)">搜索</button>' +
        (can ? '<button class="btn primary" style="margin-left:auto" onclick="Learn.newMaterial()">＋ 上传资料</button>' : '') + '</div>' +
        ((r.data.list || []).length ? '<div class="panel"><div>' + r.data.list.map(m =>
          '<div class="list-item"><div style="font-size:18px">' + (m.file_id ? '📎' : '📝') + '</div>' +
          '<div class="li-main"><b>' + UI.esc(m.title) + '</b><small>' + UI.esc(m.category) + ' · 上传者 ' +
          UI.esc(m.uploader || '—') + ' · ' + UI.time(m.created_at) + '</small>' +
          (m.content ? '<div style="margin-top:4px;color:var(--text-2);font-size:13px">' + UI.nl2br(m.content.slice(0, 120)) + '</div>' : '') + '</div>' +
          (m.file_id ? '<a class="btn sm" href="/api/files/' + m.file_id + '/download?token=' + API.token + '" download>下载 ' + UI.size(m.file_size || 0) + '</a>' : '') +
          (can ? '<button class="btn sm danger" onclick="Learn.delMaterial(' + m.id + ')">删除</button>' : '') + '</div>').join('') + '</div></div>'
          : '<div class="empty">暂无资料</div>') +
        UI.pager(r.data.total || 0, this.page, 20, 'Learn.goPage');
    },
    newMaterial() {
      UI.modal('上传资料',
        '<label class="field"><span>标题</span><input id="nm-title"></label>' +
        '<label class="field"><span>分类</span><input id="nm-cat" value="通用"></label>' +
        '<label class="field"><span>文件（可选）</span><input type="file" id="nm-file"></label>' +
        '<label class="field"><span>文字内容（可选）</span><textarea id="nm-content" style="width:100%;height:100px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Learn.doNewMaterial()">上传</button>');
    },
    async doNewMaterial() {
      const fd = new FormData();
      fd.append('title', UI.val('nm-title'));
      fd.append('category', UI.val('nm-cat'));
      fd.append('content', (document.getElementById('nm-content').value || '').trim());
      const f = document.getElementById('nm-file').files[0];
      if (f) fd.append('file', f);
      const r = await API.upload('/api/learn/materials', fd);
      UI.closeModal();
      UI.toast(r.ok ? '已上传' : r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    delMaterial(id) {
      UI.confirm('确定删除该资料？', async () => {
        const r = await API.del('/api/learn/materials/' + id);
        UI.toast(r.msg, r.ok ? 'ok' : 'err'); this.render();
      }, true);
    },

    /* ---------- 考试 ---------- */
    async exams() {
      const r = await API.get('/api/learn/exams');
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const canGrade = API.user.perms.indexOf('exam.grade') >= 0;
      const list = r.data.list || [];

      box.innerHTML =
        '<div class="panel"><div class="panel-head"><b>' + (canGrade ? '考试列表' : '可参加的考试') + '</b></div><div>' +
        (list.length ? list.map(e =>
          '<div class="list-item"><div style="font-size:18px">📝</div><div class="li-main"><b>' + UI.esc(e.title) + '</b>' +
          '<small>' + UI.esc(e.course_title || '综合') + ' · ' + e.question_count + ' 题 · ' + e.duration + ' 分钟 · 及格 ' +
          e.pass_score + ' 分 · 可考 ' + e.max_attempts + ' 次（已考 ' + e.attempted + ' 次）' +
          (e.best_score ? ' · 最高 ' + UI.esc(String(e.best_score)) + ' 分' : '') + '</small></div>' +
          (canGrade
            ? '<button class="btn sm" onclick="Learn.viewExamRecords(' + e.id + ')">阅卷</button>'
            : (e.can_take
              ? '<button class="btn sm primary" onclick="Learn.startExam(' + e.id + ')">开始考试</button>'
              : '<span class="tag gray">不可参加</span>')) +
          '</div>').join('') : '<div class="empty">暂无考试</div>') + '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>' + (canGrade ? '我出卷的考试记录' : '我的考试记录') + '</b></div>' +
        '<div id="my-records">加载中…</div></div>';
      if (canGrade) {
        document.getElementById('my-records').innerHTML =
          '<div class="empty">点击上方考试的「阅卷」查看学生答卷</div>';
        return;
      }
      const r2 = await API.get('/api/learn/my/records');
      const el = document.getElementById('my-records');
      if (r2.ok && (r2.data.list || []).length) {
        el.innerHTML = r2.data.list.map(x =>
          '<div class="list-item"><div class="li-main"><b>' + UI.esc(x.exam_title) + '</b><small>' +
          UI.time(x.submitted_at, 'full') + ' · 用时 ' + x.duration_used + ' 分钟</small></div>' +
          '<span class="tag ' + (x.passed ? 'ok' : 'danger') + '">' + x.total_score + ' 分 · ' + (x.passed ? '通过' : '未通过') + '</span>' +
          '<button class="btn sm" onclick="Learn.result(' + x.exam_id + ',' + x.id + ')">查看</button></div>').join('');
      } else el.innerHTML = '<div class="empty">暂无记录</div>';
    },

    /* 教师阅卷：列出某场考试的所有答卷，主观题可手动给分 */
    async viewExamRecords(id) {
      const r = await API.get('/api/learn/exams/' + id + '/records');
      if (!r.ok) return UI.toast(r.msg, 'err');
      const rows = r.data.list || [];
      const body = rows.length ? rows.map(x => {
        const pending = x.status !== 'graded';
        return '<div class="list-item"><div class="li-main"><b>' + UI.esc(x.display_name) +
          '</b><small>' + UI.esc(x.username) + ' · 交卷 ' + UI.esc(x.submitted_str) +
          ' · 客观题 ' + (x.obj_score || 0) + ' 分</small></div>' +
          '<span class="hw-state ' + (pending ? 'warn' : 'ok') + '">' +
          (pending ? '待阅卷' : '已阅 ' + x.total_score + ' 分') + '</span>' +
          (pending ? '<input id="ex-' + x.id + '" type="number" min="0" placeholder="主观题分" ' +
            'style="width:96px;padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:var(--panel);color:var(--text)">' +
            '<button class="btn sm primary" onclick="Learn.doGradeRecord(' + x.id + ',' + id + ')">给分</button>' : '') +
          '</div>';
      }).join('') : '<div class="empty">暂无学生作答</div>';
      UI.modal('阅卷（' + rows.length + ' 份）', body, '<button class="btn" onclick="UI.closeModal()">关闭</button>', 760);
    },
    async doGradeRecord(rid, examId) {
      const el = document.getElementById('ex-' + rid);
      const sub = el ? el.value : 0;
      const r = await API.post('/api/learn/records/' + rid + '/grade', { sub_score: sub });
      UI.toast(r.msg || (r.ok ? '已给分' : '失败'), r.ok ? 'ok' : 'err');
      if (r.ok) this.viewExamRecords(examId);
    },

    async startExam(id) {
      const r = await API.post('/api/learn/exams/' + id + '/start', {});
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      this.exam = r.data; this.recId = r.data.record_id; this.answers = {};
      this.deadline = r.data.deadline;
      this.renderExam();
    },

    renderExam() {
      const e = this.exam;
      const box = document.getElementById('learn-body');
      const qs = e.questions.map((q, i) => {
        let opts = '';
        if (q.qtype === 'single' || q.qtype === 'judge' || q.qtype === 'multi') {
          opts = (q.options || []).map((o, j) => {
            const letter = String.fromCharCode(65 + j);
            return '<div class="opt" id="opt-' + q.id + '-' + letter + '" onclick="Learn.choose(' + q.id + ',\'' +
              letter + '\',\'' + q.qtype + '\')">' + UI.esc(o) + '</div>';
          }).join('');
        } else if (q.qtype === 'blank') {
          opts = '<input class="field" id="ans-' + q.id + '" placeholder="请输入答案" oninput="Learn.setAns(' + q.id + ',this.value)">';
        } else {
          opts = '<textarea id="ans-' + q.id + '" style="width:100%;height:100px;padding:10px;border:1px solid var(--border);border-radius:8px" oninput="Learn.setAns(' + q.id + ',this.value)"></textarea>';
        }
        return '<div class="exam-q"><div class="q-stem">' + (i + 1) + '. ' + UI.esc(q.stem) +
          ' <span class="tag gray">' + q.qtype_name + ' · ' + q.score + '分</span></div>' + opts + '</div>';
      }).join('');
      box.innerHTML =
        '<div class="exam-timer"><b>' + UI.esc(e.exam.title) + '</b>' +
        '<span>剩余时间 <b id="exam-left">--:--</b> <button class="btn sm primary" onclick="Learn.submitExam(' + e.exam.id + ')">交卷</button></span></div>' +
        '<div style="max-width:820px;margin:0 auto">' + qs + '</div>';
      clearInterval(this.timer);
      this.timer = setInterval(() => {
        const left = this.deadline - Math.floor(Date.now() / 1000);
        const el = document.getElementById('exam-left');
        if (!el) return clearInterval(this.timer);
        if (left <= 0) { el.textContent = '00:00'; clearInterval(this.timer); this.submitExam(e.exam.id); return; }
        el.textContent = String(Math.floor(left / 60)).padStart(2, '0') + ':' + String(left % 60).padStart(2, '0');
      }, 1000);
    },

    choose(qid, letter, qtype) {
      if (qtype === 'multi') {
        this.answers[qid] = this.answers[qid] || '';
        let cur = this.answers[qid] ? this.answers[qid].split(',') : [];
        const i = cur.indexOf(letter);
        if (i >= 0) cur.splice(i, 1); else cur.push(letter);
        cur.sort();
        this.answers[qid] = cur.join(',');
        cur.forEach(l => document.getElementById('opt-' + qid + '-' + l).classList.add('sel'));
        (this.exam.questions.find(q => q.id === qid).options || []).forEach((o, j) => {
          const lt = String.fromCharCode(65 + j);
          const el = document.getElementById('opt-' + qid + '-' + lt);
          if (el && cur.indexOf(lt) < 0) el.classList.remove('sel');
        });
      } else {
        this.answers[qid] = letter;
        (this.exam.questions.find(q => q.id === qid).options || []).forEach((o, j) => {
          const lt = String.fromCharCode(65 + j);
          const el = document.getElementById('opt-' + qid + '-' + lt);
          if (el) el.classList.toggle('sel', lt === letter);
        });
      }
    },
    setAns(qid, v) { this.answers[qid] = v; },

    submitExam(id) {
      UI.confirm('确定交卷？交卷后不可修改。', () => this._doSubmit(id), true);
    },
    async _doSubmit(id) {
      clearInterval(this.timer);
      const r = await API.post('/api/learn/exams/' + id + '/submit', { record_id: this.recId, answers: this.answers });
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      UI.toast('提交成功，得分 ' + r.data.score + (r.data.status === 'submitted' ? '（含主观题待阅卷）' : ''), 'ok');
      this.exam = null;
      this.result(id, this.recId);
    },

    async result(examId, recId) {
      const r = await API.get('/api/learn/exams/' + examId + '/result/' + recId);
      if (!r.ok) { UI.toast(r.msg, 'err'); return; }
      const d = r.data, rec = d.record;
      const box = document.getElementById('learn-body');
      box.innerHTML = '<div class="panel"><div class="panel-head"><b>' + UI.esc(d.exam.title || '考试结果') + '</b>' +
        '<span class="tag ' + (rec.passed ? 'ok' : 'danger') + '">' + rec.total_score + ' 分 · ' +
        (rec.passed ? '通过' : '未通过') + '</span></div><div class="panel-body">' +
        '<div style="display:flex;gap:24px;flex-wrap:wrap;color:var(--text-2);font-size:13px">' +
        '<span>客观题：' + rec.obj_score + ' 分</span><span>主观题：' + rec.sub_score + ' 分</span>' +
        '<span>及格线：' + (d.exam.pass_score || '-') + ' 分</span><span>提交时间：' + UI.time(rec.submitted_at, 'full') + '</span></div>' +
        (d.show_answer ? '' : '<p style="color:var(--text-3);font-size:12px">本次考试不显示答案与解析</p>') +
        '</div></div>' +
        '<div style="max-width:820px;margin:0 auto">' + (d.questions || []).map((q, i) => {
          let opts = '';
          (q.options || []).forEach((o, j) => {
            const letter = String.fromCharCode(65 + j);
            const isMy = String(q.my_answer || '').indexOf(letter) >= 0;
            const isStd = String(q.answer || '').indexOf(letter) >= 0;
            let cls = '';
            if (d.show_answer) cls = isStd ? 'right' : (isMy ? 'wrong' : '');
            opts += '<div class="opt ' + cls + '">' + UI.esc(o) + '</div>';
          });
          const myAns = q.qtype === 'short' || q.qtype === 'blank' ?
            '<div style="margin-top:6px"><small>你的答案：</small><div style="border:1px solid var(--border);padding:8px;border-radius:6px">' +
            UI.nl2br(q.my_answer || '（未作答）') + '</div></div>' : '';
          return '<div class="exam-q"><div class="q-stem">' + (i + 1) + '. ' + UI.esc(q.stem) +
            ' <span class="tag ' + (q.correct ? 'ok' : 'danger') + '">得 ' + q.got + '/' + q.score + ' 分</span></div>' + opts + myAns +
            (d.show_answer ? '<div style="margin-top:8px;color:var(--text-2);font-size:13px">正确答案：' + UI.esc(q.answer || '') +
              (q.analysis ? '<br>解析：' + UI.nl2br(q.analysis) : '') + '</div>' : '') + '</div>';
        }).join('') + '</div>' +
        '<div style="text-align:center;margin:20px"><button class="btn" onclick="Learn.tab(\'exams\')">返回考试列表</button></div>';
    },

    /* ---------- 作业 ---------- */
    /* 状态区分：未交 / 已交待批 / 已批阅 / 被退回重做 */
    hwStateTag(a) {
      if (a.returned) return '<span class="hw-state danger">被退回</span>';
      if (a.graded) return '<span class="hw-state ok">已批阅 ' + a.my_score + ' 分</span>';
      if (a.submitted) return '<span class="hw-state warn">已提交 · 待批阅</span>';
      if (a.expired) return '<span class="hw-state danger">逾期未交</span>';
      return '<span class="hw-state gray">未提交</span>';
    },
    async homework() {
      const r = await API.get('/api/learn/assignments');
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const can = API.user.perms.indexOf('assignment.manage') >= 0;
      const list = r.data.list || [];
      // 学生视角：未交且未截止的排最前，其次被退回，再次已交，最后已批阅
      const rank = a => a.returned ? 0 : (!a.submitted && !a.expired ? 1 : (a.submitted && !a.graded ? 2 : 3));
      if (!can) list.sort((x, y) => rank(x) - rank(y));

      box.innerHTML = '<div class="toolbar">' +
        (can ? '<button class="btn primary" onclick="Learn.newHomework()">＋ 布置作业</button>' : '') +
        '</div>' +
        (list.length ? '<div class="panel"><div>' + list.map(a => {
          const sub = a.returned
            ? '<button class="btn sm primary" onclick="Learn.submitHomework(' + a.id + ')">重新提交</button>'
            : (a.submitted
              ? (a.graded ? '' : '<button class="btn sm" onclick="Learn.submitHomework(' + a.id + ')">修改</button>')
              : (a.expired ? '<span class="muted">已截止</span>'
                : '<button class="btn sm primary" onclick="Learn.submitHomework(' + a.id + ')">提交作业</button>'));
          const fb = a.feedback
            ? '<div style="margin-top:6px;padding:8px 10px;background:var(--panel-2);border-radius:8px;font-size:12.5px;color:var(--text-2)">' +
              '老师评语：' + UI.esc(a.feedback) + '</div>' : '';
          return '<div class="list-item"><div style="font-size:18px">🗂</div><div class="li-main"><b>' +
            UI.esc(a.title) + '</b><small>' + UI.esc(a.course_title || '综合') + ' · 截止 ' + UI.time(a.deadline, 'full') +
            ' · 已提交 ' + a.submit_count + ' 份</small>' +
            '<div style="margin-top:4px;color:var(--text-2);font-size:13px">' + UI.nl2br((a.content || '').slice(0, 150)) + '</div>' +
            fb + '</div>' + this.hwStateTag(a) + sub +
            (can ? '<button class="btn sm" onclick="Learn.viewSubmissions(' + a.id + ')">批阅</button>' : '') + '</div>';
        }).join('') + '</div></div>'
          : '<div class="empty">暂无作业</div>');
    },
    newHomework() {
      // 班级下拉：一次拉取，避免二次交互
      API.get('/api/learn/classes', {}).then(r => {
        const cls = (r.ok && r.data.list) || [];
        const opts = '<option value="0">不分班级（全员可见）</option>' +
          cls.map(c => '<option value="' + c.id + '">' + UI.esc(c.name) + '</option>').join('');
        UI.modal('布置作业',
          '<label class="field"><span>标题</span><input id="nh-title" placeholder="如：第4周读书笔记"></label>' +
          '<label class="field"><span>布置给</span><select id="nh-class">' + opts + '</select></label>' +
          '<label class="field"><span>要求</span><textarea id="nh-content" style="width:100%;height:110px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>' +
          '<label class="field"><span>截止时间（YYYY-MM-DD HH:MM，留空表示不限）</span>' +
          '<input id="nh-dl" placeholder="2026-12-31 18:00"></label>',
          '<button class="btn" onclick="UI.closeModal()">取消</button>' +
          '<button class="btn primary" onclick="Learn.doNewHomework()">发布</button>');
      });
    },
    async doNewHomework() {
      const r = await API.post('/api/learn/assignments', {
        title: UI.val('nh-title'), content: document.getElementById('nh-content').value,
        deadline_str: UI.val('nh-dl'),
        class_id: parseInt((document.getElementById('nh-class') || {}).value || '0', 10) || 0
      });
      UI.closeModal();
      UI.toast(r.ok ? '已发布' : r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    submitHomework(id) {
      UI.modal('提交作业',
        '<label class="field"><span>作业内容</span><textarea id="sh-content" style="width:100%;height:180px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>' +
        '<label class="field"><span>附件（可选）</span><input type="file" id="sh-file"></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Learn.doSubmitHomework(' + id + ')">提交</button>');
    },
    async doSubmitHomework(id) {
      const fd = new FormData();
      fd.append('content', document.getElementById('sh-content').value);
      const f = document.getElementById('sh-file').files[0];
      if (f) fd.append('file', f);
      const r = await API.upload('/api/learn/assignments/' + id + '/submit', fd);
      UI.closeModal();
      UI.toast(r.ok ? '已提交' : r.msg, r.ok ? 'ok' : 'err');
      this.render();
    },
    async viewSubmissions(id) {
      const r = await API.get('/api/learn/assignments/' + id + '/submissions');
      if (!r.ok) return UI.toast(r.msg, 'err');
      UI.modal('作业提交情况（' + (r.data.list || []).length + '）',
        (r.data.list || []).map(s => '<div class="list-item">' + UI.avatar(s.display_name, '#2f6fed') +
          '<div class="li-main"><b>' + UI.esc(s.display_name) + '</b><small>' + UI.time(s.submitted_at, 'full') +
          (s.late ? ' · 迟交' : '') + '</small><div style="margin-top:4px;color:var(--text-2);font-size:13px">' +
          UI.nl2br((s.content || '').slice(0, 200)) + '</div>' +
          (s.file_id ? '<a class="link" href="/api/files/' + s.file_id + '/download?token=' + API.token + '" download>下载附件</a>' : '') +
          '</div><div><input id="sc-' + s.id + '" style="width:70px;padding:4px 6px;border:1px solid var(--border);border-radius:6px" value="' + (s.score || '') + '" placeholder="分数">' +
          '<button class="btn sm primary" onclick="Learn.grade(' + s.id + ',' + id + ')">批阅</button></div></div>').join('') ||
        '<div class="empty">暂无提交</div>', '', 720);
    },
    grade(sid, aid) {
      const score = document.getElementById('sc-' + sid).value;
      UI.prompt({ title: '批阅', label: '评语（可留空）', placeholder: '给学生留下评语…', okText: '提交批阅',
        onOk: async fb => {
          const r = await API.post('/api/learn/submissions/' + sid + '/grade', { score: score, feedback: fb });
          UI.toast(r.msg, r.ok ? 'ok' : 'err');
          if (r.ok && aid) this.viewSubmissions(aid);   // 弹窗已被批阅界面替换，重新打开列表
        } });
    },

    /* ---------- 我的班级 ---------- */
    async classes() {
      const box = document.getElementById('learn-body');
      const r = await API.get('/api/learn/classes', { mine: 0 });
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const can = API.user.perms.indexOf('course.manage') >= 0;
      const list = r.data.list || [];
      box.innerHTML = '<div class="toolbar">' +
        (can ? '<button class="btn primary" onclick="Learn.newClass()">＋ 新建班级</button>' : '') +
        '</div>' +
        (list.length ? '<div class="learn-cards">' + list.map(c =>
          '<div class="learn-card"><div class="lc-cover" style="background:linear-gradient(135deg,#3988ff,#2878f0)">' +
          '<b>' + UI.esc(c.name) + '</b><small>' + UI.esc(c.term || '未设学期') + '</small></div>' +
          '<div class="lc-body"><div class="lc-desc">' + UI.esc(c.desc || '暂无简介') + '</div>' +
          '<div class="lc-meta"><span>教师：' + UI.esc(c.teacher_name) + '</span><span>' + c.member_count + ' 人</span></div></div>' +
          '<div class="lc-foot"><button class="btn sm" onclick="Learn.openClass(' + c.id + ')">查看</button>' +
          (c.joined ? '<span class="tag ok">已加入</span>'
            : '<button class="btn sm primary" onclick="Learn.joinClass(' + c.id + ')">加入</button>') +
          '</div></div>').join('') + '</div>'
          : '<div class="empty">暂无班级。教师可新建班级，或联系管理员创建。</div>');
    },
    newClass() {
      UI.modal('新建班级',
        '<label class="field"><span>班级名称</span><input id="nc-name" placeholder="如：2026级计算机1班"></label>' +
        '<label class="field"><span>学期</span><input id="nc-term" placeholder="如：2026-2027-1"></label>' +
        '<label class="field"><span>简介</span><textarea id="nc-cdesc" style="width:100%;height:80px;padding:10px;border:1px solid var(--border);border-radius:8px"></textarea></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button><button class="btn primary" onclick="Learn.doNewClass()">创建</button>');
    },
    async doNewClass() {
      const r = await API.post('/api/learn/classes', {
        name: UI.val('nc-name'), term: UI.val('nc-term'), desc: UI.val('nc-cdesc')
      });
      UI.closeModal();
      UI.toast(r.ok ? '班级已创建' : r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) this.render();
    },
    async joinClass(id) {
      const r = await API.post('/api/learn/classes/' + id + '/join', {});
      UI.toast(r.msg || (r.ok ? '已加入' : '失败'), r.ok ? 'ok' : 'err');
      this.render();
    },
    async openClass(id) {
      const r = await API.get('/api/learn/classes/' + id);
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const c = r.data;
      const members = (c.members || []).map(m =>
        '<div class="list-item"><div class="avatar">' + UI.esc((m.display_name || '?').slice(0, 1)) + '</div>' +
        '<div class="li-main"><b>' + UI.esc(m.display_name) + '</b><small>' + UI.esc(m.username) +
        (m.dept ? ' · ' + UI.esc(m.dept) : '') + '</small></div>' +
        (c.can_manage ? '<button class="btn sm danger" onclick="Learn.removeClassMember(' + c.id + ',' + m.id + ')">移出</button>' : '') +
        '</div>').join('') || '<div class="empty">暂无成员</div>';

      box.innerHTML =
        '<div class="toolbar"><button class="btn" onclick="Learn.classes()">← 返回班级列表</button>' +
        (c.can_manage ? '<button class="btn primary" onclick="Learn.addClassMembers(' + c.id + ')">＋ 添加学生</button>' : '') +
        '</div>' +
        '<div class="panel"><div class="panel-head"><b>' + UI.esc(c.name) + '</b>' +
        '<span class="tag">' + c.member_count + ' 人 · ' + UI.esc(c.term || '未设学期') + '</span></div>' +
        '<div class="panel-body"><p style="color:var(--text-2)">' + UI.esc(c.desc || '暂无简介') + '</p>' +
        '<small style="color:var(--text-3)">班主任：' + UI.esc(c.teacher_name) + '</small>' +
        (c.can_manage ? '<div style="margin-top:12px"><button class="btn" onclick="Learn.classGrades(' + c.id + ')">查看成绩册</button></div>' : '') +
        '</div></div>' +
        '<div class="panel"><div class="panel-head"><b>班级成员（' + (c.members || []).length + '）</b></div><div>' + members + '</div></div>';
    },
    addClassMembers(id) {
      UI.modal('添加学生',
        '<label class="field"><span>用户名列表（每行一个，或用逗号分隔）</span>' +
        '<textarea id="ac-users" style="width:100%;height:140px;padding:10px;border:1px solid var(--border);border-radius:8px" ' +
        'placeholder="student01&#10;student02&#10;student"></textarea></label>',
        '<button class="btn" onclick="UI.closeModal()">取消</button>' +
        '<button class="btn primary" onclick="Learn.doAddClassMembers(' + id + ')">添加</button>');
    },
    async doAddClassMembers(id) {
      const raw = document.getElementById('ac-users').value || '';
      const r = await API.post('/api/learn/classes/' + id + '/members', { usernames: raw });
      UI.closeModal();
      UI.toast(r.ok ? (r.data.msg || '已添加') : r.msg, r.ok ? 'ok' : 'err');
      this.openClass(id);
    },
    removeClassMember(cid, uid) {
      UI.confirm('确定将该学生移出班级？', async () => {
        const r = await API.del('/api/learn/classes/' + cid + '/members/' + uid);
        UI.toast(r.msg || '已移出', r.ok ? 'ok' : 'err');
        this.openClass(cid);
      }, true);
    },

    /* ---------- 班级成绩册 ---------- */
    /* 学生 × (作业 + 考试) 二维表；-1 表示无成绩，显示「—」而不是 0，
       避免把「没交」误读成「考了 0 分」。 */
    async classGrades(cid) {
      const box = document.getElementById('learn-body');
      const r = await API.get('/api/learn/classes/' + cid + '/grades');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      this._grades = d;

      const cell = (v, extra) => {
        if (v === -1 || v === undefined) return '<td class="gb-none">—</td>';
        const cls = extra === 'fail' ? 'gb-fail' : (extra === 'pass' ? 'gb-pass' : '');
        return '<td class="' + cls + '">' + (Math.round(v * 10) / 10) + '</td>';
      };

      const head = '<tr><th class="gb-name">学生</th>' +
        d.assignments.map(a => '<th title="' + UI.esc(a.title) + '">' + UI.esc((a.title || '').slice(0, 8)) + '</th>').join('') +
        d.exams.map(e => '<th title="' + UI.esc(e.title) + '">考·' + UI.esc((e.title || '').slice(0, 6)) + '</th>').join('') +
        '<th>作业均分</th><th>考试均分</th></tr>';

      const body = d.rows.map(row =>
        '<tr><td class="gb-name"><b>' + UI.esc(row.name) + '</b><small>' + UI.esc(row.username) + '</small></td>' +
        row.hw.map(c => cell(c.score)).join('') +
        row.exam.map(c => cell(c.score, c.passed ? 'pass' : (c.score >= 0 ? 'fail' : ''))).join('') +
        cell(row.hw_avg) + cell(row.exam_avg) + '</tr>').join('');

      box.innerHTML =
        '<div class="toolbar">' +
        '<button class="btn" onclick="Learn.openClass(' + cid + ')">← 返回班级</button>' +
        '<button class="btn" onclick="Learn.exportGrades(' + cid + ')">导出 CSV</button>' +
        '<span class="muted" style="margin-left:auto">' + d.student_count + ' 名学生 · ' +
        d.assignments.length + ' 份作业 · ' + d.exams.length + ' 场考试</span></div>' +
        (d.rows.length
          ? '<div class="panel gb-wrap"><table class="gb-table"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'
          : '<div class="empty">该班级暂无学生或暂无成绩数据</div>');
    },

    exportGrades(cid) {
      const d = this._grades;
      if (!d) return UI.toast('请先打开成绩册', 'err');
      const esc = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
      const lines = [];
      lines.push(['学生', '账号']
        .concat(d.assignments.map(a => a.title))
        .concat(d.exams.map(e => '考试:' + e.title))
        .concat(['作业均分', '考试均分']).map(esc).join(','));
      d.rows.forEach(r => {
        lines.push([r.name, r.username]
          .concat(r.hw.map(c => (c.score === -1 ? '' : c.score)))
          .concat(r.exam.map(c => (c.score === -1 ? '' : c.score)))
          .concat([r.hw_avg === -1 ? '' : r.hw_avg, r.exam_avg === -1 ? '' : r.exam_avg])
          .map(esc).join(','));
      });
      // 加 BOM，Excel 打开中文不乱码
      const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (d.class.name || '班级') + '-成绩册.csv';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
      UI.toast('已导出成绩册', 'ok');
    },

    /* ---------- 我的学习 ---------- */
    async mine() {
      const r = await API.get('/api/learn/my/overview');
      const c = await API.get('/api/learn/checkin');
      const box = document.getElementById('learn-body');
      if (!r.ok) { box.innerHTML = '<div class="empty">' + UI.esc(r.msg) + '</div>'; return; }
      const d = r.data;
      const stat = (v, l) => '<div class="stat"><b>' + v + '</b><span>' + l + '</span></div>';
      box.innerHTML =
        '<div class="stat-grid">' + stat(d.courses, '报名课程') + stat(d.finished_courses, '已完成课程') +
        stat(d.exams, '参加考试') + stat(d.passed, '通过考试') + stat(d.avg_score, '平均分') +
        stat(d.learn_minutes, '学习时长(分钟)') + stat(d.checkin_days, '打卡天数') + stat(d.points, '学习积分') + '</div>' +
        '<div class="panel"><div class="panel-head"><b>今日打卡</b>' +
        '<span>' + (c.ok && c.data.today ? '<span class="tag ok">已打卡</span>' :
          '<button class="btn sm primary" onclick="Learn.checkin()">立即打卡</button>') + '</span></div>' +
        '<div class="panel-body"><small style="color:var(--text-3)">累计打卡 ' + (c.ok ? c.data.total : 0) + ' 天，连续学习可获得积分奖励。</small></div></div>' +
        '<div class="panel"><div class="panel-head"><b>最近学习动态</b></div><div>' +
        ((d.logs || []).length ? d.logs.map(l => '<div class="list-item"><div class="li-main"><b>' +
          ({ enroll: '报名课程', lesson: '完成课时', exam: '参加考试', study: '学习记录' }[l.action] || l.action) +
          '</b><small>' + UI.esc(l.detail || '') + ' · ' + (l.minutes || 0) + ' 分钟</small></div>' +
          '<small style="color:var(--text-3)">' + UI.time(l.created_at, 'full') + '</small></div>').join('')
          : '<div class="empty">暂无学习记录</div>') + '</div></div>';
    },
    checkin() {
      UI.prompt({ title: '今日打卡', label: '打卡备注（可留空）', placeholder: '记录今天的学习内容…', okText: '打卡',
        onOk: async note => {
          const r = await API.post('/api/learn/checkin', { note: note });
          UI.toast(r.msg, r.ok ? 'ok' : 'err');
          this.render();
        } });
    },

    tab(t) { this.cur = t; this.page = 1; this.kw = ''; this.cid = 0; this.render(); }
  };

  window.Learn = Learn;
})();
