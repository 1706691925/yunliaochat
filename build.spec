# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置
环境变量：
  LC_ONEFILE=1/0   单文件模式（默认 1）；0 为目录模式（启动更快）
  LC_CONSOLE=1/0   是否显示控制台窗口（默认 1，建议服务器保留以便查看日志）
  LC_APP_NAME      输出的 exe 名称（默认 EnterpriseLearnChat）
  LC_UPX=1         启用 UPX 压缩（需本机安装 upx.exe）
"""
import os

APP_NAME = os.environ.get('LC_APP_NAME', 'EnterpriseLearnChat')
ONEFILE = os.environ.get('LC_ONEFILE', '1') == '1'
CONSOLE = os.environ.get('LC_CONSOLE', '1') == '1'
UPX = os.environ.get('LC_UPX', '0') == '1'

a = Analysis(
    ['app.py'],
    pathex=[os.path.abspath(SPECPATH), os.path.join(os.path.abspath(SPECPATH), 'vendor')],
    binaries=[],
    # web/cert 是随包只读资源；vendor 里放 PyMySQL（纯 Python）
    # 免安装 MySQL 本体不塞进 EXE（200MB+ 会拖慢启动），
    # 而是作为同目录 vendor/mysql 分发，由 core.mysql_server 托管启动。
    datas=[('web', 'web'), ('cert', 'cert')],
    hiddenimports=[
        'pymysql', 'pymysql.connections', 'pymysql.cursors',
        'pymysql.constants', 'pymysql.converters', 'pymysql.charset',
        'sqlite3', '_sqlite3', 'json', 'email', 'email.message',
        'http.server', 'socketserver', 'urllib.parse', 'urllib.request',
        'hashlib', 'hmac', 'secrets', 'csv', 'io', 'mimetypes', 'webbrowser',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'lib2to3', 'distutils',
        'doctest', 'asyncio', 'test', 'idlelib', 'turtledemo',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=UPX,
        runtime_tmpdir=None,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=UPX,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False,
        upx=UPX,
        name=APP_NAME,
    )
