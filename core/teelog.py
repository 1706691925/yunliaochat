# -*- coding: utf-8 -*-
# Copyright (c) 2026 夜屿
# Yunliao (云聊) is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
"""控制台日志分流。

单独成模块的原因：launcher.py 会 `import app`，如果 app.py 反过来
`from launcher import Tee` 就形成循环导入（实际表现为服务启动不了、
日志也是空的）。把 Tee 放在这里，两边都能安全引用。
"""
import queue
import sys

LOG_QUEUE = queue.Queue()


class Tee(object):
    """把 print 输出同时送到控制台和日志文件。

    第一个参数是真实输出流，第二个是**日志文件路径**（不是文件对象）。
    """

    def __init__(self, real, log_path):
        self._real = real
        self._lf = None
        try:
            self._lf = open(log_path, "a", encoding="utf-8")
        except Exception:
            pass

    def write(self, s):
        try:
            self._real.write(s)
            self._real.flush()
        except Exception:
            pass
        LOG_QUEUE.put(s)
        if self._lf:
            try:
                self._lf.write(s)
                self._lf.flush()
            except Exception:
                pass

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass
        if self._lf:
            try:
                self._lf.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return self._real.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._real.fileno()
