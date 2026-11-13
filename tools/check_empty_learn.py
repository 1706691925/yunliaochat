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
"""全新库上的学习中心空状态检查：所有接口都不应报错。"""
import json
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8940'


def call(method, path, data=None, token=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('X-Token', token)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


import io  # noqa: E402
import os  # noqa: E402

pwd_file = r'E:\项目\yunliao\_fresh_data\初始管理员密码.txt'
with io.open(pwd_file, encoding='utf-8') as f:
    pwd = [ln.split('：', 1)[1].strip() for ln in f if ln.startswith('密码')][0]

r = call('POST', '/api/auth/login', {'username': 'admin', 'password': pwd})
print('login:', r.get('ok'))
tok = r['data']['token']

tests = [
    ('今日待办', '/api/learn/my/todo'),
    ('我的概览', '/api/learn/my/overview'),
    ('课程列表', '/api/learn/courses'),
    ('班级列表', '/api/learn/classes'),
    ('作业列表', '/api/learn/assignments'),
    ('考试列表', '/api/learn/exams'),
    ('资料库', '/api/learn/materials'),
    ('我的考试记录', '/api/learn/my/records'),
    ('打卡', '/api/learn/checkin'),
]
bad = 0
for name, path in tests:
    x = call('GET', path, token=tok)
    ok = bool(x.get('ok'))
    bad += 0 if ok else 1
    print('  %-14s %s  %s' % (name, 'OK  ' if ok else 'FAIL', '' if ok else x.get('msg')))

d = call('GET', '/api/learn/my/todo', token=tok).get('data', {})
print()
print('今日待办明细:')
print('  role_mode      =', d.get('role_mode'))
print('  course_count   =', d.get('course_count'))
print('  pending_hw     =', d.get('pending_homework'))
print('  to_grade_hw    =', d.get('to_grade_homework'))
print('  available_exams=', d.get('available_exams'))
print('  to_grade_exams =', d.get('to_grade_exams'))
print('  streak         =', d.get('streak'))
print('  checked_today  =', d.get('checked_today'))

print()
print('结论:', '空库接口全部正常' if bad == 0 else '%d 个接口异常' % bad)
