# 本次改动说明（2026-11-03）

> 面向**教师—学生**教学场景，围绕「让系统从能聊天变成能教学」做的一轮完善。
> 所有改动均通过自动化回归（83 项 e2e + 50 项运维 + 179 个前端事件入口）。

---

## 一、最重要的变化：学习中心回来了

之前的状态是「半成品」——后端 `api/learn.py`（约 40 个接口）完整可用，
但前端被整体下线：`web_src/js/learn.js` 写好了却没有参与构建，
`index.template.html` 里连 `#view-learn` 容器都没有。
结果就是：**有功能，但用户点不到**，数据库里 `exam_records` / `submissions` / `checkins`
全是 0 条——功能从来没被真正用过。

这次把它完整接回：

| 位置 | 改动 |
|---|---|
| `web_src/build.py` | `learn.js` 重新加入 `FILES`，新增 `learn.css` 追加内联 |
| `web_src/index.template.html` | 新增 `#view-learn` 视图 + 左侧导航「学习中心」入口（带红点角标） |
| `web_src/js/app.js` | `go()` 分发到 `Learn.render()`，新增 `updateLearnBadge()` |
| `web_src/learn.css` | 新建，约 200 行，复用现有 CSS 变量，深色主题自动适配 |

前端体积从 394 KB → **447 KB**（+53 KB），仍然是单文件、零外链、断网可用。

---

## 二、新增能力

### 1. 班级（`api/classes.py`，新建）

原来系统里只有 `rooms`（聊天房间）和 `depts`（部门），**没有「教学班」这个概念**。
老师带三个班，没法表达「这份作业只布置给 2026级计算机1班」。

- 表：`classes`（名称/学期/班主任/简介/状态）、`class_members`（多对多）
- 接口 8 个：增删改查、批量加成员（回车或用逗号分隔用户名）、移出成员、学生自助加入
- 删除班级是**归档**而非物理删除，历史教学数据保留

### 2. 今日待办（`GET /api/learn/my/todo`）

登录后第一眼该看到什么？做成四张卡片：

| 角色 | 卡片内容 |
|---|---|
| 学生 | 进行中课程 · **待交作业** · **可参加的考试** · 连续学习天数 |
| 教师 | 进行中课程 · **待批阅作业** · **待阅卷** · 连续学习天数 |

**为什么按角色分流**：老师从不提交作业。如果沿用学生口径，
老师会看到「全部作业都待交」，纯属噪音。这个细节不做，功能就是半成品。

卡片下方还有「近期待办」列表，按截止时间排序，逾期标红，一键跳转处理。
左侧导航的红点数字与卡片数一致（学生=待交+可考；教师=待批阅+待阅卷）。

### 3. 成绩册（`GET /api/learn/classes/<id>/grades`）

教师改完作业最想知道的是「这个班整体怎么样、谁掉队了」，
而不是逐个作业翻提交列表。所以做成 **学生 × (作业 + 考试)** 二维表：

- 横向看每次作业/考试的分，纵向看每个学生的作业均分、考试均分
- **未交/未考显示「—」而不是 0 分**——把「没交」误读成「考了 0 分」会引发家长投诉
- 表头与首列 sticky，深色模式适配，一键导出 CSV（带 BOM，Excel 打开中文不乱码）

### 4. 作业流程补全

原实现有三个真实缺陷：

| 缺陷 | 修复 |
|---|---|
| 重复提交产生多条记录，老师看到一堆重复 | 改为**覆盖式**：同一学生对同一作业只保留一条 |
| 交完就改，可以反复刷分 | 已批阅的**禁止再交**，需老师「退回重做」 |
| 老师批完发现不合格，没法让学生重做 | 新增**退回重做**，带评语，学生收到通知后可重交 |

另外作业状态在界面上清晰区分：
**未提交 / 已提交待批阅 / 已批阅（显示得分）/ 被退回 / 逾期未交**，
学生视角自动把「待处理」的排前面。

### 5. 截止提醒

服务端每 10 分钟检查一次：作业/考试在只剩 **24 小时内**时，
自动提醒**还没交 / 还没考**的人（已交的不打扰）。

用 `assignments.reminded_at` / `exams.reminded_at` 时间戳去重，
保证同一条只提醒一次——否则每 10 分钟一轮会把学生刷屏。

### 6. 教师阅卷入口

教师进「考试」页看到的是**考试列表 + 阅卷按钮**，而不是学生的「开始考试」。
点进去可看每个学生的客观题得分，给主观题手动给分。

---

## 三、顺手修掉的两个 bug

### 1. 撤回的消息仍在侧边栏显示最后一条（`api/chat.py`）

```python
# 修复前：不排除已撤回的消息
last = db.one("SELECT * FROM messages WHERE room_id=? "
              "ORDER BY id DESC LIMIT 1", (row["id"],))
# 修复后
last = db.one("SELECT * FROM messages WHERE room_id=? AND recalled_at=0 "
              "ORDER BY id DESC LIMIT 1", (row["id"],))
```

### 2. 未读数每次都做全表 COUNT（性能）

原来侧边栏刷新要对每个房间 `COUNT(*)` 一遍，10 万条消息时实测 **88 ms**。
改成 `room_members.unread_count` 预存计数 + 增量维护：

| 方案 | 10 万条消息耗时 |
|---|---|
| 原来（每房间 COUNT） | 88.25 ms |
| **现在（预存计数）** | **0.02 ms** |

约 **4400 倍**提升。新增 `core/unread.py`（bump / clear / join / recount / repair_all），
启动时 `repair_all()` 全量校正一次，防止历史数据或异常中断导致计数漂移。

### 3. 房间列表的 N+1 查询

`room_view()` 对**每个房间**各查 3 次（人数 / 最后一条消息 / 未读数），
而 `msg_view()` 又对**每条消息**查 1 次发送者。房间一多就是成百上千条 SQL。

改为批量预取（`_prefetch_rooms()`）：房间列表、人数、最后消息、未读数、
发送者各用一条聚合/`IN (...)` 查询取齐，**SQL 条数与房间数无关**。

`tools/bench_n1.py` 实测（24 房间 / 101 消息，统计 `db.query|one|scalar|execute` 调用次数）：

| 路径 | SQL 条数 |
|---|---|
| 修复前（逐房间逐条查） | 211 |
| **修复后（批量预取）** | **6** |

减少 **205 条（35.2 倍）**，且两种路径返回的数据逐字段完全一致
（bench 内置了一致性断言）。按此结构，房间数到 100 时预计 400 → 6。

历史消息接口（一页 50 条）同样改为一次性取齐发送者。

### 4. 教学相关索引

成绩册、待办、班级成员都是按 `class_id` / `assignment_id` / `exam_id` 过滤，
补了 12 个索引：`idx_msg_last`（局部索引，`WHERE recalled_at=0`，让"每个房间最后一条
未撤回消息"走纯索引扫描）、`idx_sub_asg`、`idx_sub_user`、`idx_exrec_exam`、
`idx_asg_deadline`、`idx_enroll_user`、`idx_checkin_user`、`idx_asg_class`、
`idx_exam_class`、`idx_course_class`、`idx_class_members_user`、`idx_class_members_class`。

> 踩坑记录：索引若建在「迁移里才补的表/列」上（`class_members` 表、`class_id` 列），
> 必须写在 `_migrate()` 里、`add()` 之后；写进 `SCHEMA` 会让**全新库初始化**
> 直接报 `no such table` / `no such column`。已在代码里注释标出。

> 顺带说明：这轮也做过 Node.js 迁移的可行性验证（`node_proto/`），
> 结论是**不建议迁移**——Node 在这个负载下反而更慢（166 ms vs 88 ms），
> 单文件 EXE 从 10–20 MB 膨胀到 89 MB，且 `notify_agent.py`（托盘/弹窗/截屏/协议注册）
> 在 Node 下没有干净替代。详细数据见 `node_proto/VERIFICATION.md`。

---

## 四、文档与仓库卫生

| 问题 | 处理 |
|---|---|
| README 声称「C++ 原生版为主力、端口 8893、`server.exe`」，但 `cpp/`、`server.exe`、`data_cpp/` **全都不存在** | 改为如实描述：当前只有 Python 实现，C++ 版为历史规划 |
| README 引用 `实例页面/`、`offline-wheels/`，两个目录都不存在 | 清理引用；构建脚本改为直接 `pip install pyinstaller` |
| `verify_ops.py` 断言 `/games/fireboy.html`，该文件从未存在（实际是 `fireboy-flash.html`） | 按真实文件名断言；可选资源缺失时记为 SKIP 而非 FAIL |
| 缺 `.gitignore`，`data/`、`__pycache__`、运行时密码文件都可能被提交 | 补 `.gitignore` |
| 残留 `_diag_pwd.py`、`_read_verify.png`、`_srv.log`、`_srv_err.log`、多个 `__pycache__` | 全部清理 |

---

## 五、验证结果

```
tools/e2e_test.py      83 项通过，0 失败   （原 60 项，新增 23 项覆盖班级/成绩册/作业流程）
tools/verify_ops.py    50 项通过，0 失败
tools/check_clicks.js  179 个事件入口全部有效，单文件自包含检查通过
tools/check_fresh_install.py  全新空库 32 张表 / 20 个关键列 / 12 个新增索引 全部就位
tools/bench_n1.py      N+1 实测 211 → 6 条 SQL，且新旧路径数据逐字段一致
```

新增的自动化断言包括：
- 班级创建 / 加成员 / 详情
- 作业挂班级、**重复提交为覆盖而非新增**（断言提交记录只有 1 条）
- **已批阅后拒绝改交**
- 退回重做 → 学生可重交 → **成绩失效待重批**（成绩册回到 `-1`）
- 成绩册学生行、作业列、分数、均分
- 未提交作业的学生在成绩册里**记为空而不是 0**
- 今日待办返回四类计数与角色标记

---

## 六、回滚方式

改动前的完整备份：`E:\项目\yunliao\_backup_20261103_201329`

涉及文件：
```
新增  api/classes.py
      core/unread.py
      web_src/learn.css
      .gitignore
      tools/check_fresh_install.py
      tools/check_empty_learn.py
      tools/bench_n1.py
修改  api/learn.py      今日待办、作业覆盖提交、退回重做、班级归属
      api/chat.py       撤回显隐、未读计数、加入房间初始化、批量预取（N+1）
      api/base.py       （无需改动，已确认 avatar_url 不查库）
      api/__init__.py   注册 classes
      core/db.py        classes/class_members 表、unread_count、class_id、reminded_at、submissions.status、12 个索引
      app.py           启动校正、截止提醒循环
      web_src/build.py learn.js + learn.css 参与构建
      web_src/index.template.html  学习中心视图 + 导航入口
      web_src/js/app.js            go() 分发 + 学习角标
      web_src/js/learn.js          今日页、班级、成绩册、作业状态、阅卷
      tools/e2e_test.py           新增 23 项断言
      tools/verify_ops.py         修正 games 断言 + SKIP 机制
      README.md                   全面校正
      构建EXE.bat                  去掉 offline-wheels 依赖
```

前端改动后需重建：`python web_src/build.py`
