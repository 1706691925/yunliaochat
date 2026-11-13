# -*- coding: utf-8 -*-
"""免安装 MySQL 托管：随程序分发 mysqld，首次启动自动初始化，退出时自动关闭。

设计目标（对齐原项目的「双击即用」交付形态）：
  - 不需要客户安装 MySQL、不需要管理员权限、不需要配置服务
  - 数据目录放在程序的 data/mysql 下，整目录拷贝即可迁移/备份
  - 只监听 127.0.0.1，不对外暴露端口
  - 端口被占用时自动改用一个空闲端口

关于中文路径（重要）：
  Windows 版 mysqld 按系统 ANSI 代码页解析 my.ini 与命令行参数，
  路径里出现中文会变成乱码，报 “Can't create/write to file 'E:\\??\\...'”，
  进而拒绝初始化。而本项目常被放在 `E:\\项目\\yunliao` 这类中文目录下，
  且该卷可能未启用 8.3 短名（取不到短路径）。
  解决办法：在 %LOCALAPPDATA% 下建一个纯 ASCII 的工作目录，
  再用「目录联接（junction）」把 mysql/ 与 data/ 指回项目真实目录。
  mysqld 全程只看到 ASCII 路径，内核负责重定向，数据仍留在项目里。

目录约定：
  <项目>/vendor/mysql/         解压后的 MySQL（只读）
  <项目>/data/mysql/           数据目录（可写，真正要备份的东西）
  %LOCALAPPDATA%/YunliaoMySQL/rt<hash>/
      mysql  -> <项目>/vendor/mysql   （联接）
      data   -> <项目>/data/mysql     （联接）
      my.ini / mysqld.err / mysqld.pid
"""
import os
import socket
import subprocess
import sys
import time

MYSQL_DIRNAME = "mysql"


def _is_windows():
    return os.name == "nt"


def _base_dir():
    """程序所在目录（打包后为 exe 所在目录）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resource_dir():
    """只读资源目录（打包后为 _MEIPASS 临时解压目录）"""
    return getattr(sys, "_MEIPASS", None) or _base_dir()


def mysql_home():
    """定位 vendor/mysql：先找程序目录（可替换升级），再找随包资源目录。"""
    for base in (_base_dir(), _resource_dir()):
        p = os.path.join(base, "vendor", MYSQL_DIRNAME)
        if os.path.isdir(os.path.join(p, "bin")):
            return p
    return os.path.join(_base_dir(), "vendor", MYSQL_DIRNAME)


def mysqld_path():
    exe = "mysqld.exe" if _is_windows() else "mysqld"
    return os.path.join(mysql_home(), "bin", exe)


def mysqladmin_path():
    exe = "mysqladmin.exe" if _is_windows() else "mysqladmin"
    return os.path.join(mysql_home(), "bin", exe)


def available():
    """免安装 MySQL 是否就位"""
    return os.path.isfile(mysqld_path())


def _ascii(p):
    try:
        return all(ord(c) < 128 for c in p)
    except Exception:
        return False


def short_path(path):
    """尽量取得 Windows 8.3 短路径（拿不到就原样返回）。"""
    if os.name != "nt" or not path or _ascii(path):
        return path
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.GetShortPathNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        n = fn(path, buf, 1024)
        if n and buf.value and _ascii(buf.value):
            return buf.value
    except Exception:
        pass
    return path


def work_dir(data_dir):
    """纯 ASCII 的工作目录（联接 + my.ini + 日志都放这里）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or data_dir
    h = 0
    for ch in os.path.abspath(data_dir):
        h = (h * 131 + ord(ch)) % (10 ** 12)
    return os.path.join(base, "YunliaoMySQL", "rt%012d" % h)


def _make_junction(link, target):
    """建立目录联接 link -> target（不需要管理员权限）。已存在且指向正确则跳过。"""
    if os.path.isdir(link):
        try:
            # 已存在：直接判断指向是否正确
            if os.path.realpath(link).lower() == os.path.realpath(target).lower():
                return True
        except Exception:
            pass
        try:
            os.rmdir(link)
        except Exception:
            return False
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if not os.path.isdir(target):
        os.makedirs(target, exist_ok=True)
    try:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           timeout=30)
        return r.returncode == 0 and os.path.isdir(link)
    except Exception:
        return False


def free_port(start=33070, end=33199):
    """从 start 开始找一个空闲端口（只在本机环回上探测）。"""
    for port in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("找不到可用的本地端口（%d~%d 全部被占用）" % (start, end))


def _port_open(port, timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class MySQLServer(object):
    """托管一个随程序启动/关闭的 mysqld 实例。"""

    def __init__(self, data_dir, port=None, database="yunliao",
                 user="yunliao", password="", quiet=True):
        self.data_dir = os.path.abspath(data_dir)              # <项目>/data
        self.datadir_real = os.path.join(self.data_dir, "mysql")   # 真实数据目录
        self.basedir_real = os.path.abspath(mysql_home())
        self.work = work_dir(self.data_dir)
        self.database = database
        self.user = user
        self.password = password or ""
        self.quiet = quiet
        self.port = int(port) if port else 0
        self.proc = None
        self._started_here = False
        self._linked = False          # 是否成功用上联接（决定用哪套路径）
        self._prepare_paths()

    def _log(self, msg):
        if not self.quiet:
            try:
                print("[mysql] %s" % msg)
            except Exception:
                pass

    # ---------------- 路径准备 ----------------
    def _prepare_paths(self):
        """确定 basedir / datadir / runtime_dir 以及要调用的 mysqld.exe 路径。

        关键点（踩坑记录）：不仅 basedir/datadir 要是 ASCII，
        **mysqld.exe 自身的路径也必须是 ASCII**。
        Windows 版 mysqld 用 ANSI 接口解析自己的可执行文件路径，
        路径含中文时会失败并把 basedir 退化成「盘符:\\」，
        于是报 `Failed to set datadir to 'E:\\data\\'`。
        因此联接建好后，一律通过联接路径去调用 exe。
        """
        os.makedirs(self.work, exist_ok=True)
        os.makedirs(self.datadir_real, exist_ok=True)

        if _ascii(self.data_dir) and _ascii(self.basedir_real):
            self.basedir = self.basedir_real
            self.datadir = self.datadir_real
            self.exe = os.path.join(self.basedir, "bin",
                                    "mysqld.exe" if _is_windows() else "mysqld")
            self._linked = True
        else:
            ok1 = _make_junction(os.path.join(self.work, "mysql"), self.basedir_real)
            ok2 = _make_junction(os.path.join(self.work, "data"), self.datadir_real)
            if ok1 and ok2:
                self.basedir = os.path.join(self.work, "mysql")
                self.datadir = os.path.join(self.work, "data")
                self._linked = True
            else:
                self.basedir = short_path(self.basedir_real)
                self.datadir = short_path(self.datadir_real)
                self._linked = _ascii(self.basedir) and _ascii(self.datadir)
                if not self._linked:
                    self._log("警告：无法为中文路径建立 ASCII 联接，"
                              "MySQL 很可能无法启动。建议把程序放到纯英文目录。")
            self.exe = os.path.join(self.basedir, "bin",
                                    "mysqld.exe" if _is_windows() else "mysqld")

        self.runtime_dir = self.work
        self.ini_path = os.path.join(self.runtime_dir, "my.ini")
        self.err_log = os.path.join(self.runtime_dir, "mysqld.err")

    # ---------------- 配置文件 ----------------
    def _write_ini(self):
        os.makedirs(self.runtime_dir, exist_ok=True)

        def fwd(p):
            return p.replace("\\", "/")

        lines = [
            "[mysqld]",
            "basedir=%s" % fwd(self.basedir),
            "datadir=%s" % fwd(self.datadir),
            "port=%d" % self.port,
            "bind-address=127.0.0.1",
            "character-set-server=utf8mb4",
            "collation-server=utf8mb4_unicode_ci",
            # 用 native_password，避免客户端依赖 cryptography 做 RSA 交换
            "default-authentication-plugin=mysql_native_password",
            "max_connections=200",
            "innodb_buffer_pool_size=64M",
            "innodb_log_file_size=32M",
            "innodb_flush_log_at_trx_commit=2",
            "log-error=%s" % fwd(self.err_log),
            "pid-file=%s" % fwd(os.path.join(self.runtime_dir, "mysqld.pid")),
            "tmpdir=%s" % fwd(self.runtime_dir),
            "[client]",
            "port=%d" % self.port,
            "default-character-set=utf8mb4",
        ]
        tmp = self.ini_path + ".tmp"
        # 内容全是 ASCII，按 ascii 写即可（mysqld 不做 UTF-8 解码，也不认 BOM）
        with open(tmp, "w", encoding="ascii", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, self.ini_path)

    def _flags(self):
        if _is_windows():
            return getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return 0

    # ---------------- 初始化 ----------------
    def is_initialized(self):
        return (os.path.isdir(self.datadir_real)
                and os.path.isdir(os.path.join(self.datadir_real, "mysql")))

    def initialize(self):
        """首次运行：mysqld --initialize-insecure 建库（root 空密码）"""
        if self.is_initialized():
            return False
        self._log("首次初始化数据目录：%s" % self.datadir_real)
        os.makedirs(self.datadir_real, exist_ok=True)
        if self.port == 0:
            self.port = free_port()
        self._write_ini()
        r = subprocess.run(
            [self.exe, "--defaults-file=%s" % self.ini_path,
             "--initialize-insecure", "--console"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=600, creationflags=self._flags())
        if r.returncode != 0:
            raise RuntimeError("MySQL 初始化失败（code=%s）：\n%s"
                               % (r.returncode,
                                  r.stdout.decode("utf-8", "replace")[-2500:]))
        self._log("初始化完成")
        return True

    # ---------------- 启动 / 停止 ----------------
    def start(self, wait_seconds=120):
        if self.port == 0:
            self.port = free_port()
        self._write_ini()
        if _port_open(self.port):
            self._log("端口 %d 已被占用，尝试直接连接" % self.port)
            if self._wait_ready(5):
                self._log("复用已运行的实例")
                return False
            self.port = free_port()
            self._write_ini()
        self._log("启动 mysqld（端口 %d）" % self.port)
        self.proc = subprocess.Popen(
            [self.exe, "--defaults-file=%s" % self.ini_path, "--console"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=self._flags())
        self._started_here = True
        if not self._wait_ready(wait_seconds):
            self._dump_err()
            raise RuntimeError("MySQL 启动超时（%d 秒），详见 %s" % (wait_seconds, self.err_log))
        self._log("mysqld 已就绪")
        return True

    def _dump_err(self):
        try:
            if os.path.exists(self.err_log):
                with open(self.err_log, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-2500:]
                self._log("mysqld 错误日志尾部：\n%s" % tail)
        except Exception:
            pass

    def _wait_ready(self, timeout):
        """等到端口可连且能执行 SQL 为止"""
        import pymysql
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_open(self.port):
                try:
                    c = pymysql.connect(host="127.0.0.1", port=self.port, user="root",
                                        password="", connect_timeout=3, charset="utf8mb4")
                    c.close()
                    return True
                except Exception:
                    pass
            if self.proc is not None and self.proc.poll() is not None:
                return False
            time.sleep(0.5)
        return False

    def stop(self, wait_seconds=30):
        """优雅关闭（仅当本进程拉起的实例）。

        不用 mysqladmin.exe：它的可执行文件路径同样含中文，
        会踩到和 mysqld 一样的 ANSI 解析问题。直接发 SQL 关库更稳。
        """
        if not self._started_here or self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                try:
                    import pymysql
                    c = pymysql.connect(host="127.0.0.1", port=self.port, user="root",
                                        password="", connect_timeout=5, charset="utf8mb4",
                                        autocommit=True)
                    cur = c.cursor()
                    cur.execute("SHUTDOWN")
                    c.close()
                except Exception:
                    pass
                self.proc.wait(timeout=wait_seconds)
        except Exception:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
            except Exception:
                pass
        finally:
            self.proc = None
            self._started_here = False

    # ---------------- 建库建用户 ----------------
    def ensure_database(self):
        """确保数据库与业务账号存在（root 空密码连接后执行）"""
        import pymysql
        c = pymysql.connect(host="127.0.0.1", port=self.port, user="root", password="",
                            charset="utf8mb4", autocommit=True)
        try:
            cur = c.cursor()
            # 库名来自本地配置，用反引号包裹（标识符不能用占位符绑定）
            cur.execute("CREATE DATABASE IF NOT EXISTS `" + self.database +
                        "` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            pw = self.password or ""
            for host in ("127.0.0.1", "localhost"):
                cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED WITH "
                            "mysql_native_password BY %s", (self.user, host, pw))
                cur.execute("ALTER USER %s@%s IDENTIFIED WITH mysql_native_password BY %s",
                            (self.user, host, pw))
                cur.execute("GRANT ALL PRIVILEGES ON `" + self.database + "`.* TO %s@%s",
                            (self.user, host))
            cur.execute("FLUSH PRIVILEGES")
        finally:
            c.close()

    # ---------------- 一站式 ----------------
    def ensure_ready(self, wait_seconds=120):
        """初始化 → 启动 → 建库，返回当前端口"""
        if not available():
            raise RuntimeError(
                "未找到免安装 MySQL：%s\n"
                "请先运行 tools/setup_mysql.py 解压 MySQL 到 vendor/mysql。" % mysqld_path())
        self.initialize()
        if self.port == 0:
            self.port = free_port()
            self._write_ini()
        self.start(wait_seconds)
        self.ensure_database()
        return self.port
