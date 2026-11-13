# Node.js 迁移可行性验证报告

> 验证日期：2026-11-03
> 验证方式：用 Node 24 写一个可运行的原型，直接接管现有 `data/app.db`，与 Python 版做同条件对比。

---

## 一、验证结论（先说答案）

| 问题 | 结论 |
|---|---|
| Node 能否直连现有数据库？ | ✅ **能**。`node:sqlite` 内置，零第三方依赖 |
| 现有用户密码能否继续用？ | ✅ **能**。PBKDF2 哈希跨语言完全兼容，用户无需重置密码 |
| WebSocket 能否实现？ | ✅ **能**。自研 160 行 vs Python 版 154 行，但代码更清晰 |
| 能否打包成单 EXE？ | ⚠️ **能，但代价大**：89.4 MB（Python 版约 10~20 MB），且需手写资源加载器 |
| 迁移后性能提升明显吗？ | ❌ **不明显**。语言不是瓶颈，见第三节 |
| 建议迁移吗？ | ❌ **不建议**。理由见第五节 |

---

## 二、已验证可用的部分

### 2.1 密码哈希跨语言兼容（关键成果）

Python 侧：
```python
hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 120000)
# 存储格式：pbkdf2$120000$<salt>$<base64>
```

Node 侧：
```javascript
crypto.pbkdf2Sync(pwd, salt, 120000, 32, 'sha256')
```

**实测结果**：Python 生成的哈希 `pbkdf2$120000$b8d01544...$1ms8CQjN9qEl...` 在 Node 中验证返回 `true`，反向亦然。

这意味着迁移**不需要重置任何用户密码**——这一点比预期好。

### 2.2 数据库直接可用

`node:sqlite`（Node 22.5+ 内置）直接打开现有 270 KB 的 `app.db`，WAL 模式正常。

现有代码零改动即可读表、读密码哈希、写审计日志。

### 2.3 业务逻辑可平移

原型的 920 行代码覆盖了：
- 登录/登出/会话（含锁定、失败计数、审计日志）
- 房间列表（含未读数）
- 历史消息
- WebSocket 认证与推送

接口返回格式与 Python 版**逐字段一致**（已用真实数据比对）。

---

## 三、关键性能发现（这次验证最重要的产出）

### 3.1 语言切换带来的差异很小

小数据量（481 条消息）下的实测：

| 接口 | Python | Node | 差异 |
|---|---|---|---|
| `GET /api/chat/rooms` | 1.01 ms | 2.36 ms | Node 更慢 |
| `GET /rooms/1/messages` | 2.76 ms | 1.52 ms | Node 快 1.8x |
| `POST /api/auth/login` | 93.04 ms | 51.11 ms | Node 快 1.8x |

**登录快是因为 Node 的 PBKDF2 实现更快**（OpenSSL vs Python hashlib），这属于密码学库差异，不是语言本身。

### 3.2 真正的瓶颈是"未读数用 COUNT 实时计算"

在 **10 万条消息**规模下的三方案对比：

| 方案 | 耗时 | 说明 |
|---|---|---|
| A. Python 版写法（逐房间 COUNT） | **88.25 ms** | 8 房间 × 4 次查询 |
| B. Node 聚合写法（仍 COUNT） | **166.22 ms** | 固定 5 条 SQL，但仍是 COUNT |
| C. 预存计数（O(1)） | **0.02 ms** | 读 `room_members.unread_count` |

- 语言迁移（A→B）：**0.53x（反而更慢）**
- 设计改造（B→C）：**约 6780x**
- 合计（A→C）：**约 3600x**

> **核心结论**：把 Python 换成 Node 只解决一小部分问题。
> 真正的优化点是「未读数不要再实时 COUNT」，这一条改动**在 Python 里做，收益完全相同**。

### 3.3 索引的影响（意外发现）

`MAX(id) GROUP BY` 在 10 万条上耗时 5~10 ms。加 `(room_id, id)` 索引后改善有限——
因为 SQLite 对该查询用的是 **COVERING INDEX 全索引扫描**，而非 seek。

这提示：**索引不是万能药，查询写法比索引更重要**。

---

## 四、Node SEA 单 EXE 打包实测

### 4.1 过程与坑

| 步骤 | 结果 |
|---|---|
| 生成 blob（仅入口文件） | 153 字节 — **不含任何依赖模块** |
| 注入 node.exe | 89.4 MB |
| 直接运行 | ❌ `ERR_UNKNOWN_BUILTIN_MODULE: ./src/server` |
| 改用 `assets` 内嵌全部源文件 | ✅ blob 38 KB |
| 手写 `require` 加载器（vm 编译） | ✅ 成功运行 |
| 修正 `__dirname` 路径问题 | ✅ 完整跑通 |

**SEA 的限制**：`main` 字段只内嵌入口文件，**不会自动追踪 require 依赖树**。
多文件项目必须：
1. 在 `sea-config.json` 的 `assets` 里逐一声明每个源文件
2. 自己实现 require 拦截器，用 `vm.runInThisContext` 编译内嵌源码

这比 PyInstaller 的自动依赖分析**麻烦得多**。

### 4.2 体积对比

| 产物 | 体积 |
|---|---|
| **Node SEA 单文件** | **89.4 MB** |
| Python 单 EXE（现有） | 约 10~20 MB |

**体积膨胀约 5~9 倍**，因为 Node 运行时本身就有 89 MB。

---

## 五、最终建议

### 不建议现在迁移

理由按重要性排序：

#### 1. 迁移解决不了实际问题

对比数据已经证明：性能瓶颈是**查询设计**（未读数 COUNT），不是语言。
- 换 Node：`88ms → 166ms`（更差）
- 改设计：`166ms → 0.02ms`（无论用什么语言）

**同一个索引 + 同一个 `unread_count` 列，在 Python 版里改完，性能与 Node 版完全相同。**

#### 2. 交付形态严重降级

"双击 EXE、免安装、断网可用"是这个项目最核心的卖点。
Node SEA 让单文件从 ~10 MB 变成 **89 MB**，还要手写资源加载器。
而且 SEA 仍标注为实验性特性。

#### 3. 桌面助手 1656 行无处安放

`notify_agent.py`（1191 行）+ `client.py`（465 行）依赖 Windows 原生能力：
托盘、Toast 通知、截图框选、`yunliao://` 协议、开机自启。

Node 做这些只能用 Electron（100~150 MB）或 Tauri（需要 Rust 工具链）。
**换成 Electron 后，你只是把「Python + PyInstaller」变成「Node + Electron」，复杂度没降反升。**

#### 4. 失去"零依赖"定位

现在 `requirements.txt` 只有注释，运行时零第三方包，甲方安全审计容易通过。
Node 中等项目会有 200~400 个 `node_modules` 包，`npm audit` 常年有告警。

---

## 六、如果仍然要迁移，正确路径是什么

若确有理由（如团队只会 JS），建议：

### 分三阶段，不停服

```
阶段 1（1 周）：Node 只做 /api/auth + /api/chat
               与 Python 版并行跑，共读同一个 app.db
               用同一个 token 表（sessions）——已验证可行

阶段 2（1 周）：迁 /api/learn + /api/admin + /api/files
               Node 成为主服务，Python 退为备用

阶段 3（3~5 天）：补打包（SEA 或保留 Node 运行时）+ 桌面端方案定夺
```

### 关键前置决策

**先回答一个问题：桌面助手怎么办？**

- 若**保留 Python 桌面端** → 项目变成「Node 服务端 + Python 桌面端」，README 必须如实说明
- 若**接受 Electron** → 体积从 10 MB 涨到 150 MB，需重新评估客户接受度
- 若**砍掉桌面助手** → 失去消息实时提醒，这是教学场景的高频需求

**这个问题不解决，任何迁移计划都不可执行。**

---

## 七、关于 MySQL

本次未做 MySQL 实测，基于分析给出结论：**不建议换**。

| 维度 | SQLite（现状） | MySQL |
|---|---|---|
| 部署 | 零安装 | 需装服务、建库、配账号 |
| 备份 | 复制 `data/` 一个目录 | 库备份 + 文件目录，两套策略 |
| 热恢复 | 现有代码支持，无需重启 | 必须停服务 drop database |
| 离线安装 | 无需 | 需带 MySQL 离线包 + VC++ 运行库 |
| 你的数据量 | 10 万条消息查询 < 100ms | 优势在这个量级**体现不出来** |

只有将来出现**多校共用一套系统**或**要求主从分离**时，MySQL 才有意义。

---

## 八、本次验证的其它收获

验证过程中发现 **Python 版真实存在的一个 bug**：

`api/chat.py:141` 的 `room_view()`：
```python
last = db.one("SELECT * FROM messages WHERE room_id=? ORDER BY id DESC LIMIT 1")
```
**没有过滤 `recalled_at`（已撤回消息）**，导致撤回后侧边栏仍显示撤回的消息内容。

Node 版已修正（`WHERE recalled_at=0`）。

---

## 附：原型文件清单

```
node_proto/
├── src/
│   ├── db.js          72 行  数据层（node:sqlite，零依赖）
│   ├── security.js   120 行  密码/令牌/权限（PBKDF2 兼容）
│   ├── http.js        94 行  路由与请求上下文
│   ├── session.js     83 行  会话管理
│   ├── ws.js         160 行  WebSocket（自研帧解析）
│   ├── api/auth.js    72 行  登录/登出
│   ├── api/chat.js   121 行  房间/消息（已做 N+1 优化）
│   └── server.js     198 行  服务端主程序
├── sea_main.js                SEA 自定义加载器
├── build/yunliao-node.exe     89.4 MB 单可执行文件（已验证可运行）
└── tools/
    ├── seed_bench.js          压测数据生成
    ├── bench.js               双版本接口对比
    ├── bench_index.js         索引影响
    ├── profile_deep.js        逐条 SQL 定位瓶颈
    └── bench_final.js         三方案终极对比
```

启动方式：
```bash
node src/server.js --port 8901
```
