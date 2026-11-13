# -*- coding: utf-8 -*-
"""全局应用上下文（避免循环引用）"""
import threading

APP = None
_LOCK = threading.RLock()
