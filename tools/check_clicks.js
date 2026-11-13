/* 单文件版前端自检：
 *   1. web/index.html 必须自包含（无外链 css / js）
 *   2. 内联脚本可在沙箱中加载（无语法错误 / 依赖缺失）
 *   3. 页面与脚本模板里所有 onclick 等事件引用的函数真实存在
 * 用法：node tools/check_clicks.js
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const root = path.resolve(__dirname, '..');
const file = path.join(root, 'web/index.html');
const html = fs.readFileSync(file, 'utf8');

let fail = 0;
function bad(msg) { console.log('  ❌ ' + msg); fail++; }
function ok(msg) { console.log('  ✅ ' + msg); }

/* ---------- 1. 自包含检查 ---------- */
console.log('\n[1] 单文件自包含');
if (/<script[^>]+src=/i.test(html)) bad('存在外链脚本 <script src>');
else ok('无外链脚本');
if (/<link[^>]+stylesheet/i.test(html)) bad('存在外链样式 <link rel=stylesheet>');
else ok('无外链样式');
if (/@import\s/i.test(html)) bad('CSS 存在 @import');
else ok('无 CSS @import');
const external = (html.match(/(?:src|href)="(?!data:|#|\/api\/|\/downloads\/|\/games\/)[^"]+\.(?:css|js|woff2?|ttf)"/gi) || []);
if (external.length) bad('仍引用外部资源: ' + external.join(', '));
else ok('无外部 css/js/字体引用');

/* ---------- 2. 提取内联脚本 ---------- */
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
if (scripts.length !== 1) bad('内联脚本数量异常: ' + scripts.length);
else ok('内联脚本 ' + (scripts[0].length / 1024).toFixed(1) + ' KB');
const js = scripts.join('\n');

/* ---------- 3. 沙箱加载 ---------- */
function makeEl(id) {
  const el = {
    id: id || '', style: {}, dataset: {}, value: '', textContent: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [], files: [],
    appendChild(c) { this.children.push(c); return c; }, removeChild() {}, remove() {},
    focus() {}, click() {}, blur() {}, addEventListener() {}, removeEventListener() {},
    querySelector() { return makeEl(); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; },
    setAttribute() {}, getAttribute() { return null; }, closest() { return null; },
    insertAdjacentHTML() {}, scrollTo() {}, scrollIntoView() {},
    scrollTop: 0, scrollHeight: 0, clientHeight: 0, disabled: false, checked: false,
  };
  return el;
}
const store = {};
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  location: { protocol: 'http:', host: '127.0.0.1:8080', href: 'http://127.0.0.1:8080/', hash: '', reload() {} },
  localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => store[k] = String(v), removeItem: k => delete store[k] },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  fetch: () => Promise.resolve({ ok: true, headers: { get: () => 'application/json' }, json: () => Promise.resolve({ ok: true, data: {} }), blob: () => Promise.resolve({}) }),
  WebSocket: function () { this.readyState = 0; this.send = () => {}; this.close = () => {}; },
  URLSearchParams, FormData: function () { this.append = () => {}; },
  navigator: { userAgent: 'node' }, Notification: undefined,
  addEventListener() {}, removeEventListener() {},
  requestAnimationFrame: fn => setTimeout(fn, 0),
  alert() {}, prompt() { return null; }, confirm() { return false; },
  document: {
    getElementById: id => makeEl(id), querySelector: () => makeEl(), querySelectorAll: () => [],
    createElement: () => makeEl(), addEventListener() {}, removeEventListener() {},
    body: makeEl('body'), documentElement: makeEl('html'), title: '',
  },
  Math, Date, JSON, RegExp, Error, Object, Array, String, Number, Boolean, Promise, Map, Set,
  URL, Blob: function () {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

console.log('\n[2] 内联脚本加载');
try {
  vm.runInContext(js, sandbox, { filename: 'index.html#inline', timeout: 15000 });
  ok('脚本加载完成');
} catch (e) {
  bad('脚本加载失败 → ' + e.message);
}

/* ---------- 4. 事件目标校验 ---------- */
console.log('\n[3] 事件目标校验');
const calls = new Set();
const reHtml = /on(?:click|change|input|keyup|keydown|submit|focus|blur|dblclick)="([A-Za-z_$]\w*)\.([A-Za-z_$]\w*)\s*\(/g;
let m;
while ((m = reHtml.exec(js))) { if (m[1] !== 'event') calls.add(m[1] + '.' + m[2]); }
while ((m = reHtml.exec(html))) { if (m[1] !== 'event') calls.add(m[1] + '.' + m[2]); }
const reJs = /onclick=\\?["']([A-Za-z_$]\w*)\.([A-Za-z_$]\w*)\s*\(/g;
while ((m = reJs.exec(js))) { if (m[1] !== 'event') calls.add(m[1] + '.' + m[2]); }

const missing = [], good = [];
[...calls].sort().forEach(c => {
  const [obj, fn] = c.split('.');
  const o = sandbox[obj];
  if (o && typeof o[fn] === 'function') good.push(c);
  else missing.push(c + '   (window.' + obj + ' = ' + (o ? typeof o : 'undefined') + ')');
});
console.log('  引用到的入口：' + calls.size + '，可用：' + good.length + '，失效：' + missing.length);
missing.forEach(x => bad(x));

/* ---------- 5. 关键结构检查 ---------- */
console.log('\n[4] 关键结构');
[['login-page', '登录页'], ['app', '主应用'], ['room-list', '会话列表'], ['msg-list', '消息区'],
 ['contacts-body', '通讯录'], ['files-body', '文件中心'], ['admin-body', '管理后台'],
 ['toast-wrap', '提示层'], ['modal-root', '弹窗层']].forEach(([id, label]) => {
  if (html.indexOf('id="' + id + '"') < 0) bad('缺少 #' + id + '（' + label + '）');
  else ok('#' + id + ' ' + label);
});

console.log('\n' + (fail ? '  ❌ 自检失败：' + fail + ' 项' : '  ✅ 全部自检通过'));
process.exit(fail ? 1 : 0);
