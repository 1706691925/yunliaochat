# -*- mode: python ; coding: utf-8 -*-
"""云聊 · 桌面提醒助手 —— PyInstaller 打包配置

构建：pyinstaller Notify.spec --noconfirm
产物：dist/云聊提醒.exe（单文件、无控制台窗口）
"""
import os

block_cipher = None

a = Analysis(
    ['notify_agent.py'],
    pathex=[],
    binaries=[],
    datas=[('app.ico', '.'), ('assets/notify_icon_76.png', 'assets'), ('assets/notify_icon_152.png', 'assets')],
    hiddenimports=['tkinter', 'winsound', 'winreg'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['PIL', 'numpy', 'pandas', 'PyQt5', 'PySide6'],
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
    name='云聊提醒',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口
    disable_windowed_traceback=False,
    icon='assets/notify.ico',
)
