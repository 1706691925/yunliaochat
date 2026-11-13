# -*- mode: python ; coding: utf-8 -*-
"""云聊提醒 · 安装包 PyInstaller 配置

先构建绿色版：python -m PyInstaller Notify.spec --noconfirm
再构建安装包：python -m PyInstaller Installer.spec --noconfirm
产物：dist/云聊提醒-安装包.exe
"""
import os

block_cipher = None

a = Analysis(
    ['installer_app.py'],
    pathex=[],
    binaries=[],
    datas=[('dist/云聊提醒.exe', '.'), ('app.ico', '.'), ('assets/notify_icon_152.png', 'assets')],
    hiddenimports=['tkinter', 'tkinter.ttk', 'winreg', 'shutil', 'subprocess'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['PIL', 'numpy', 'pandas', 'PyQt5', 'PySide6', 'sqlite3'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='云聊提醒-安装包',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon='assets/notify.ico',
)
