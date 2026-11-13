# 贡献指南

感谢你愿意改进云聊。本文说明参与方式与必须遵守的几条底线。

## 提交前自检

提交前请跑一遍自检脚本，它覆盖了 CI 的全部检查项：

```bash
python tools/selfcheck.py           # 全部检查（会起一个本地服务做冒烟测试）
python tools/selfcheck.py --quick   # 跳过冒烟测试，只做静态检查（更快）
```

它会检查四件事：

| 检查 | 内容 |
|---|---|
| 语法 | 全量 `py_compile` |
| 零依赖约束 | 除 `core/db_sqlite.py` / `core/db_mysql.py` 等少数文件外，禁止直接 `import sqlite3` / `import pymysql` |
| 仓库卫生 | `data/`、`cert/`、`*.db`、`*.pem`、`*.key` 等不应入库的文件 |
| 冒烟测试 | 全新安装 → 登录 → 9 个核心接口 → 校验种子数据 |

失败时脚本会列出具体文件，退出码非 0。**CI 跑的是同一个脚本**，
所以本地通过基本就等于 CI 通过。

## 硬性约束

### 1. 不引入第三方运行时依赖

云聊的核心价值是「零依赖、双击即用」。**后端只允许使用 Python 标准库。**

- 已有例外：`vendor/pymysql`（仅 MySQL 模式使用，且已随包内置）
- 需要新依赖时请先开 Issue 讨论，说明为什么标准库做不到

### 2. 不提交任何真实个人信息

这是本项目最严格的一条。**以下内容一律不得出现在代码、文档、示例数据、测试用例中：**

- 真实姓名、学号、工号、身份证号、手机号、邮箱
- 真实学校/公司/部门名称
- 真实内网 IP、域名、端口
- 任何证书、私钥、`.pfx`、`.pem`、`.key`
- 任何真实数据库文件、上传文件、日志

写示例时请用：`张三/李四`、`ks20240001`、`192.168.1.10`、`example.com`、
`chat.example.com`、`user01`。

### 3. 不提交运行时数据

`data/`、`cert/`、`*.db`、`*.sql`、`*.log` 已在 `.gitignore` 中排除。
如果 `git status` 里出现它们，说明有问题。

### 4. 前端改动必须同时提交构建产物

前端源码在 `web_src/`，但程序实际加载的是构建后的 `web/index.html`。

```bash
# 改完 web_src/ 后
python web_src/build.py
git add web_src/ web/index.html      # 两者一起提交
```

只改 `web_src/` 而不重新构建，改动不会生效；只改 `web/index.html` 而不改源码，
下次构建会被覆盖。**两者必须同步。**

## 代码风格

- 保持现有风格：4 空格缩进，函数与变量 `snake_case`，类 `PascalCase`
- 注释与文档字符串用中文，解释「为什么」而不是「做了什么」
- 数据访问统一走 `core/db.py` 的 API（`query/one/execute/insert/update/delete/scalar/count`），
  **不要**在业务代码里直接写引擎相关的 SQL 或 `import sqlite3 / pymysql`

  ```python
  # 正确
  rows = db.query("SELECT * FROM users WHERE dept=?", (dept,))

  # 错误：绕过数据层，MySQL 模式下会失败
  import sqlite3
  con = sqlite3.connect("data/app.db")
  ```

- SQL 一律用 `?` 占位符。数据层会按引擎自动翻译成 `%s`（MySQL）或原样（SQLite）

## 新增接口

```python
from api.base import route

@route("/api/thing/<id>", "GET")
def get_thing(req, id):
    row = db.one("SELECT * FROM things WHERE id=?", (id,))
    if not row:
        raise util.ApiError("不存在", 404)
    return row
```

- 路径放在 `api/<模块>.py`，在 `api/__init__.py` 里导入以完成注册
- 返回值会被统一包成 `{"ok": true, "data": ...}`
- 需要权限时用现有的权限检查工具，不要自己判断 `role == "admin"`

## 数据库结构变更

表结构定义只有一份：`core/db.py` 的 `SCHEMA`（MySQL 语法）。
SQLite 引擎会在启动时自动翻译，**不要为 SQLite 单独写一套 DDL**。

- 新增表：加到 `SCHEMA` 里
- 给已有表加字段：加到 `SCHEMA`，同时在 `_migrate()` 里加一条 `add(...)`，
  保证老库能自动补齐
- 字段名避开 MySQL 保留字（`key`、`value`、`desc` 等）；确实要用时必须加反引号

## 提交信息

用一句话说清做了什么，中文即可：

```text
修复 MySQL 模式下备份恢复失败的问题
新增课程导出功能
docs: 补充 systemd 部署说明
```

## Pull Request

1. 从 `main` 拉分支：`git checkout -b fix/backup-restore`
2. 一个 PR 只做一件事
3. PR 描述里说明：**改了什么、为什么改、怎么验证的**
4. 涉及界面的改动请附截图
5. 涉及数据库的改动请说明是否需要迁移

## 报告问题

提 Issue 时请附上：

- 操作系统与 Python 版本
- 数据库引擎（`sqlite` / `mysql`）
- 启动命令
- 完整报错信息（控制台或 `data/logs/server.log`）
- 复现步骤

**请先自行隐去日志里的真实姓名、IP、学号等信息再贴出来。**

## 安全问题

发现安全漏洞请**不要**直接开公开 Issue，
通过仓库的 Security 页面私下报告，或发邮件给维护者。
我们会尽快响应，并在修复后公开致谢。
