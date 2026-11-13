# -*- coding: utf-8 -*-
# Yunliao (云聊) - LAN collaboration platform
# Copyright (C) 2026 夜屿
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""把免安装 MySQL 的 ZIP 解压到 vendor/mysql（只需执行一次）。

用法：
    python tools/setup_mysql.py                  # 用 _dl 里的 zip
    python tools/setup_mysql.py --zip 路径.zip    # 指定 zip
    python tools/setup_mysql.py --keep-zip        # 解压后保留 zip

ZIP 从镜像站下载（官方 CDN 在国内常被拒）：
    https://mirrors.aliyun.com/mysql/MySQL-8.0/mysql-8.0.28-winx64.zip

解压后目录结构：
    vendor/mysql/bin/mysqld.exe
    vendor/mysql/share/...
"""
import argparse
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor", "mysql")
DEFAULT_ZIP = os.path.join(os.path.dirname(ROOT), "_dl", "mysql-8.0.28-winx64.zip")


def find_zip(explicit=None):
    if explicit:
        return explicit
    if os.path.isfile(DEFAULT_ZIP):
        return DEFAULT_ZIP
    for cand in ("mysql-8.0.28-winx64.zip", "mysql-8.0.27-winx64.zip"):
        p = os.path.join(os.path.dirname(ROOT), "_dl", cand)
        if os.path.isfile(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=None, help="MySQL ZIP 路径")
    ap.add_argument("--force", action="store_true", help="已存在也重新解压")
    args = ap.parse_args()

    if os.path.isdir(os.path.join(VENDOR, "bin")) and not args.force:
        print("已存在：%s" % VENDOR)
        print("如需重新解压请加 --force")
        return 0

    zp = find_zip(args.zip)
    if not zp or not os.path.isfile(zp):
        print("找不到 MySQL ZIP。请先下载后放到：%s" % DEFAULT_ZIP)
        print("下载地址：https://mirrors.aliyun.com/mysql/MySQL-8.0/mysql-8.0.28-winx64.zip")
        return 2

    size_mb = os.path.getsize(zp) / 1024.0 / 1024.0
    print("解压 %s（%.1f MB）..." % (os.path.basename(zp), size_mb))

    tmp = VENDOR + "_tmp"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)

    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        # ZIP 里通常有一层顶层目录 mysql-8.0.28-winx64/
        top = {n.split("/")[0] for n in names if "/" in n}
        strip = len(top) == 1
        for info in z.infolist():
            parts = info.filename.split("/")
            if strip:
                parts = parts[1:]
            if not parts or parts == [""]:
                continue
            target = os.path.join(tmp, *parts)
            # 防目录穿越
            if not os.path.abspath(target).startswith(os.path.abspath(tmp)):
                continue
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    if os.path.isdir(VENDOR):
        shutil.rmtree(VENDOR, ignore_errors=True)
    os.replace(tmp, VENDOR)

    exe = os.path.join(VENDOR, "bin", "mysqld.exe")
    if not os.path.isfile(exe):
        print("解压完成，但没找到 bin/mysqld.exe，请检查 ZIP 结构")
        return 1
    print("完成：%s" % exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
