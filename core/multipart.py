# -*- coding: utf-8 -*-
"""multipart/form-data 解析（自研，替代已废弃的 cgi.parse_multipart）"""
import os
import re
import tempfile

from core import util


class UploadedFile(object):
    def __init__(self, filename, tmp_path, size, field):
        self.filename = filename
        self.tmp_path = tmp_path
        self.size = size
        self.field = field

    def read(self, n=-1):
        with open(self.tmp_path, "rb") as f:
            return f.read(n) if n > 0 else f.read()

    def remove(self):
        try:
            os.remove(self.tmp_path)
        except Exception:
            pass


def parse(rfile, boundary, content_length):
    """解析 multipart，返回 (fields, files)。文件 body 流式写入临时文件（逐块检测 boundary），
    不把大文件整体累积在内存；字段体小，仍走内存缓冲。"""
    fields, files = {}, []
    delim = b"--" + boundary
    end_delim = delim + b"--"
    buf = b""
    read_total = 0

    def feed(n):
        nonlocal buf, read_total
        need = n - len(buf)
        if need > 0:
            # read1：socket 缓冲流有数据即返回（read(n) 会等满 n 字节导致大文件尾部挂起）
            chunk = rfile.read1(min(need, max(content_length - read_total, 0)) if content_length else need)
            read_total += len(chunk)
            buf += chunk
        return buf

    # 跳过分界前的 preamble
    while delim not in buf and read_total < content_length:
        feed(len(buf) + 8192)
    idx = buf.find(delim)
    if idx < 0:
        return fields, files
    buf = buf[idx:]

    while True:
        # 定位当前 part 的头部结束（\r\n\r\n）
        while True:
            if buf.find(b"\r\n\r\n", len(delim)) >= 0 or read_total >= content_length:
                break
            feed(len(buf) + 65536)
        if buf.startswith(end_delim):
            break
        head_end = buf.find(b"\r\n\r\n", len(delim))
        if head_end < 0:
            break
        raw_headers = buf[len(delim) + 2:head_end].decode("utf-8", "ignore")
        body_start = head_end + 4
        fname = None
        field_name = None
        m = re.search(r'name="([^"]*)"', raw_headers)
        if m:
            field_name = m.group(1)
        m2 = re.search(r'filename="([^"]*)"', raw_headers)
        if m2 is not None:
            fname = util.safe_name(m2.group(1))

        if fname:
            # 文件 part：body 流式写入临时文件，内存只保留 head 与当前 64KB 读块
            tmp = tempfile.NamedTemporaryFile(delete=False, prefix="up_", suffix=".bin")
            size = 0
            try:
                with open(tmp.name, "wb") as out:
                    rest = buf[body_start:]
                    bpos = rest.find(delim)
                    if bpos >= 0:
                        # body 与 boundary 之间的 \r\n 属于分隔符，不写入文件
                        body_part = rest[:bpos - 2] if (bpos >= 2 and rest[bpos - 2:bpos] == b"\r\n") else rest[:bpos]
                        out.write(body_part)
                        size += len(body_part)
                        buf = rest[bpos:]
                    else:
                        out.write(rest)
                        size += len(rest)
                        buf = b""
                        while read_total < content_length:
                            chunk = rfile.read1(65536)
                            if not chunk:
                                break
                            read_total += len(chunk)
                            cpos = chunk.find(delim)
                            if cpos >= 0:
                                body_part = chunk[:cpos - 2] if (cpos >= 2 and chunk[cpos - 2:cpos] == b"\r\n") else chunk[:cpos]
                                out.write(body_part)
                                size += len(body_part)
                                buf = chunk[cpos:]
                                break
                            out.write(chunk)
                            size += len(chunk)
            except Exception:
                tmp.close()
                try:
                    os.remove(tmp.name)
                except Exception:
                    pass
                raise
            tmp.close()
            files.append(UploadedFile(fname, tmp.name, size, field_name or "file"))
            if buf.startswith(end_delim):
                break
            continue

        # 字段 part：体小，定位到下一个分界后切片解码
        while True:
            if buf.find(delim, body_start) >= 0 or read_total >= content_length:
                break
            feed(len(buf) + 65536)
        pos = buf.find(delim, body_start)
        if pos < 0:
            break
        fields[field_name or ""] = buf[body_start:pos - 2].decode("utf-8", "ignore")
        buf = buf[pos:]
        if buf.startswith(end_delim):
            break
    return fields, files
