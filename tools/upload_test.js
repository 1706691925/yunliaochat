/* 真实浏览器风格上传测试：使用 FormData/Blob，验证后端 multipart 解析 */
const BASE = process.argv[2] || 'http://127.0.0.1:8932';

async function login() {
  const r = await fetch(BASE + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: process.env.LC_PWD })
  });
  return r.json();
}

(async () => {
  const lg = await login();
  if (!lg.ok) { console.log('登录失败:', lg.msg); process.exit(1); }
  const token = lg.data.token;
  console.log('登录成功');

  const rooms = await (await fetch(BASE + '/api/chat/rooms', { headers: { 'X-Token': token } })).json();
  const rid = rooms.data.list[0].id;
  console.log('房间 id =', rid);

  // 1) 单文件，英文文件名
  let fd = new FormData();
  fd.append('file', new Blob([new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])], { type: 'text/plain' }), 'a.txt');
  fd.append('room_id', String(rid));
  let r = await fetch(BASE + '/api/files/upload', { method: 'POST', headers: { 'X-Token': token }, body: fd });
  let j = await r.json();
  console.log('[1] 英文文件名单文件 →', j.ok ? 'ok' : 'FAIL ' + j.msg, j.ok ? JSON.stringify(j.data.list) : '');

  // 2) 中文文件名
  fd = new FormData();
  fd.append('file', new Blob(['中文内容测试'.repeat(20)], { type: 'text/plain' }), '测试文档.txt');
  fd.append('room_id', String(rid));
  r = await fetch(BASE + '/api/files/upload', { method: 'POST', headers: { 'X-Token': token }, body: fd });
  j = await r.json();
  console.log('[2] 中文文件名 →', j.ok ? 'ok' : 'FAIL ' + j.msg, j.ok ? JSON.stringify(j.data.list) : '');

  // 3) 大一点的文件（约 300KB 二进制，含 \r\n 与 boundary 相似字节）
  const big = new Uint8Array(300 * 1024);
  for (let i = 0; i < big.length; i++) big[i] = (i * 7 + 13) & 0xff;
  fd = new FormData();
  fd.append('file', new Blob([big], { type: 'application/octet-stream' }), 'big.bin');
  fd.append('room_id', String(rid));
  r = await fetch(BASE + '/api/files/upload', { method: 'POST', headers: { 'X-Token': token }, body: fd });
  j = await r.json();
  console.log('[3] 300KB 二进制 →', j.ok ? 'ok' : 'FAIL ' + j.msg, j.ok ? JSON.stringify(j.data.list) : '');

  // 4) 多文件一次上传
  fd = new FormData();
  fd.append('file', new Blob(['m1'], { type: 'text/plain' }), 'm1.txt');
  fd.append('file', new Blob(['m2'], { type: 'text/plain' }), 'm2.txt');
  fd.append('room_id', String(rid));
  r = await fetch(BASE + '/api/files/upload', { method: 'POST', headers: { 'X-Token': token }, body: fd });
  j = await r.json();
  console.log('[4] 多文件 →', j.ok ? 'ok' : 'FAIL ' + j.msg, j.ok ? JSON.stringify(j.data.list) : '');

  // 5) 带附件发消息 + 回读
  if (j.ok && j.data.list.length) {
    const ids = j.data.list.map(f => f.id);
    const mr = await (await fetch(BASE + '/api/chat/rooms/' + rid + '/messages', {
      method: 'POST', headers: { 'X-Token': token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: '带附件消息', attachments: ids, reply_to: 0 })
    })).json();
    console.log('[5] 带附件发消息 →', mr.ok ? 'ok' : 'FAIL ' + mr.msg);
    if (mr.ok) console.log('    attachments =', JSON.stringify(mr.data.attachments));
  }
  console.log('上传测试结束');
})();
