# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
更早的开发期记录见 [CHANGELOG-20261103.md](CHANGELOG-20261103.md)。

## [1.1.1] - 2026-11-13

首个开源版本。

### 新增

- **双数据库引擎**：`db_engine` 配置项可在 SQLite（默认）与内置免安装 MySQL 8.0 之间切换，
  业务代码无需改动（`core/db.py` 为门面，`core/db_sqlite.py` / `core/db_mysql.py` 为实现）
- `core/mysql_server.py`：托管内置 mysqld 的初始化、启停、建库建权，
  自动避让端口，只监听 `127.0.0.1`
- `tools/setup_mysql.py`：解压 MySQL ZIP 到 `vendor/mysql`
- `tools/migrate_sqlite_to_mysql.py`：SQLite 数据迁移到 MySQL
- 退出时优雅关闭 mysqld
- 支持**邮箱登录**（登录框原先提示「账号或邮箱」但后端只认用户名）
- 命令行新增 `--https` / `--cert` / `--key`

### 修复

- **房间列表无法滚动**：flex 子项缺 `min-height:0`，列表不收缩、被父容器裁掉
- **右键菜单不可见**：入场动画 `@keyframes` 含 `opacity:0`，动画不推进时菜单永久透明
  （DOM 存在、能点击，但看不见）；改为动画只动 `transform`，
  并补 `prefers-reduced-motion` 兜底
- 消息区 `.messages` 同类滚动隐患
- 右键菜单打开瞬间可能被自身 `focus()` 触发的 scroll 事件关闭
- `/api/admin/db/tables` 在 SQLite 引擎下返回 500（硬编码查询 `information_schema`）
- 首页数据库引擎标签硬编码为 MySQL
- `config.json` 带 UTF-8 BOM 时解析失败且**静默回退默认值**，导致用户配置丢失且无提示；
  改为 `utf-8-sig` 读取并打印警告
- 管理员后台修改/新建用户、CSV 导入时未校验邮箱唯一性
- 备份恢复在持有元数据锁时长时间阻塞；改为引擎无关实现（清表 + 回填），
  SQLite 与 MySQL 均可往返恢复

### 变更

- 默认 `https_enabled` 改为 `false`（仓库不附带证书，证书与私钥不入库）
- 备份文件格式统一为引擎无关的 `.sql`，可跨引擎恢复
- 演示种子数据全部替换为**虚构账号**（`admin` / `teacher` / `student` / `teacher02` /
  `leader` / `user01`–`user03`），示例部门与房间为通用名称
- 移除 `node_proto/`、构建产物、旧版前端等无关目录，仓库体积显著下降

### 安全

- 清除仓库内全部真实个人信息（姓名、学号、学校）、内网 IP、域名与 TLS 私钥
- `vendor/mysql`（864 MB）不入库，使用者自行准备

### 文档

- 重写 `README.md`（功能介绍、双数据库对比、快速开始、架构）
- 新增 `docs/部署指南.md`（源码/EXE 部署、两种数据库、HTTPS、开机自启、
  反向代理、备份恢复、升级回滚、安全清单、常见问题）
- 新增 `CONTRIBUTING.md`、`SECURITY.md`、`LICENSE`（MIT）
- 新增 `docs/Node迁移可行性验证.md`（技术选型记录）

[1.1.1]: https://github.com/1706691925/yunliaochat/releases/tag/v1.1.1
