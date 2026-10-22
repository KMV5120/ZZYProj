/* ============================================================================
 * 前端无浏览器 smoke（jsdom）：加载真实 index.html + app.js，断言接线与渲染。
 * 不是真实浏览器；真实浏览器逐控件点击仍是 G2 人工项。
 * 运行：NODE_PATH=/path/to/node_modules node tools/frontend_smoke.cjs
 * ============================================================================ */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.dirname(__dirname);
let passed = 0;
function check(name, cond, detail = '') {
  if (!cond) throw new Error(`${name} 失败 ${detail}`);
  passed++;
  console.log(`  PASS ${name}`);
}

const now = new Date().toISOString();
const usage = {
  day: '2026-10-22',
  daily: { used: 1, limit: 30, remaining: 29, unlimited: false, resets_at: '2026-10-23T00:00:00+08:00' },
  month: '2026-10',
  monthly: { tokens_used: 144, token_limit: 1000000, remaining: 999856, unlimited: false, resets_at: '2026-11-01T00:00:00+08:00' }
};
const user = {
  id: 'u1', email: 'tester@example.com', nickname: '测试', role: 'admin',
  disabled: false, limits: { daily_messages: null, monthly_tokens: null, daily_messages_default: 30, monthly_tokens_default: 1000000 },
  usage
};

let chatCalls = 0;
let failChat = false;
let chatBody = null;

function jsonRes(status, body) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => null }, json: async () => body };
}
function sseRes(events) {
  const enc = new TextEncoder();
  const chunks = events.map(e => enc.encode(e));
  let i = 0;
  return {
    ok: true, status: 200, headers: { get: () => null },
    body: {
      getReader: () => ({
        read: async () => (i < chunks.length ? { done: false, value: chunks[i++] } : { done: true, value: undefined })
      })
    }
  };
}

const html = fs.readFileSync(path.join(ROOT, 'site/index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(ROOT, 'site/app.js'), 'utf8');
const page = html.replace('<script src="static/app.js"></script>', `<script>${appJs}</script>`);

const dom = new JSDOM(page, {
  url: 'http://127.0.0.1:8100/',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.localStorage.setItem('zzy_token', 'test-token');
    window.localStorage.setItem('zzy_prefs', JSON.stringify({ model: 'deepseek-flash', mode: 'ask' }));
    window.AbortController = global.AbortController;
    window.TextEncoder = global.TextEncoder;
    window.TextDecoder = global.TextDecoder;
    window.fetch = async (url, opts = {}) => {
      const u = String(url);
      const method = (opts.method || 'GET').toUpperCase();
      if (u.endsWith('api/auth/me')) return jsonRes(200, { user });
      if (u.endsWith('api/conversations') && method === 'GET') {
        return jsonRes(200, { conversations: [{ id: 'c1', title: '已有会话', created_at: now, updated_at: now, message_count: 2 }] });
      }
      if (u.includes('api/conversations/c1') && method === 'GET') {
        return jsonRes(200, {
          conversation: { id: 'c1', title: '已有会话', created_at: now, updated_at: now },
          messages: [
            { id: 'm1', role: 'user', content: '旧问题', reasoning: '', model: null, usage: { input: 1, output: 0 }, created_at: now },
            { id: 'm2', role: 'assistant', content: '旧回答', reasoning: '', model: 'deepseek-flash', usage: { input: 1, output: 1 }, created_at: now }
          ]
        });
      }
      if (u.endsWith('api/chat') && method === 'POST') {
        chatCalls++;
        chatBody = JSON.parse(opts.body || '{}');
        if (failChat) {
          return jsonRes(429, { code: 'QUOTA_DAILY', message: '今日额度已用完（1/1 条），明日 00:00 重置。', details: {}, request_id: 'zzy-testrid' });
        }
        return sseRes([
          'event: meta\ndata: {"conversation_id":"c2","title":"新问题","request_id":"zzy-1"}\n\n',
          'event: delta\ndata: {"content":"你"}\n\n',
          'event: delta\ndata: {"content":"好"}\n\n',
          'event: done\ndata: {"conversation_id":"c2","title":"新问题","usage":{"input":10,"output":5},"request_id":"zzy-1"}\n\n'
        ]);
      }
      if (u.endsWith('api/admin/overview')) {
        return jsonRes(200, { users: 3, active_users_today: 2, today: { messages: 5, tokens: 120 }, monthly: { messages: 9, tokens: 300 }, disabled_users: 0, top_users: [] });
      }
      if (u.endsWith('api/admin/users')) {
        return jsonRes(200, { users: [user], count: 1 });
      }
      if (u.endsWith('api/auth/logout')) return jsonRes(200, { success: true });
      return jsonRes(404, { code: 'NOT_FOUND', message: '未 mock 的请求：' + u + ' ' + method });
    };
  }
});

function click(el) {
  el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}
const wait = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const { window } = dom;
  const doc = window.document;
  await wait(120);

  console.log('[1] 启动与账号状态');
  check('账号栏显示邮箱', doc.getElementById('accountText').textContent === 'tester@example.com');
  check('配额条', doc.getElementById('quotaChip').textContent === '今日 1 / 30', doc.getElementById('quotaChip').textContent);
  check('模型名', doc.getElementById('modelName').textContent === 'deepseek-flash');

  console.log('[2] 会话列表与加载');
  const convo = doc.querySelector('.convo');
  check('会话列表渲染', !!convo && convo.textContent.includes('已有会话'), convo && convo.textContent);
  click(convo);
  await wait(80);
  check('打开会话渲染两条消息', doc.querySelectorAll('.msg').length >= 2, String(doc.querySelectorAll('.msg').length));
  check('助手内容', doc.getElementById('chatInner').textContent.includes('旧回答'));

  console.log('[3] 新会话发送与 SSE 解析');
  doc.getElementById('newChatBtn').click();
  const input = doc.getElementById('input');
  input.value = '新问题';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  doc.getElementById('send').click();
  await wait(150);
  check('发出 chat 请求', chatCalls === 1);
  check('mode 由前端传 id', chatBody && chatBody.mode === 'ask', JSON.stringify(chatBody));
  check('conversation_id 为 null（新会话）', chatBody && chatBody.conversation_id === null);
  check('流式内容渲染', doc.getElementById('chatInner').textContent.includes('你好'), doc.getElementById('chatInner').textContent.slice(-80));

  console.log('[4] 失败可见');
  failChat = true;
  input.value = '再问';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  doc.getElementById('send').click();
  await wait(120);
  const err = doc.querySelector('.err-box');
  check('错误卡片出现', !!err);
  check('错误文案可读', err.textContent.includes('今日额度已用完'), err.textContent);
  check('带 request_id', err.textContent.includes('zzy-testrid'));
  check('无 [object Object]', !doc.body.textContent.includes('[object Object]'));

  console.log('[5] 账号弹窗与管理后台');
  click(doc.getElementById('accountPill'));
  await wait(40);
  check('账号弹窗显示用量', doc.getElementById('modal').textContent.includes('本月 token'));
  const pwdBtn = doc.getElementById('changePwdBtn');
  check('修改密码入口可见', !!pwdBtn);
  click(pwdBtn);
  await wait(20);
  check('修改密码表单', !!doc.getElementById('pwdForm') && !!doc.getElementById('pwdOld')
        && !!doc.getElementById('pwdNew2'));
  click(doc.getElementById('pwdCancel'));
  await wait(20);
  const adminBtn = doc.getElementById('openAdminBtn');
  check('管理员入口可见', !!adminBtn);
  click(adminBtn);
  await wait(120);
  check('管理看板渲染', doc.getElementById('modal').textContent.includes('今日活跃'));
  check('用户列表渲染', !!doc.querySelector('.admin-row'));

  console.log(`\n前端 jsdom smoke 通过：${passed} 项`);
  window.close();
})().catch(e => {
  console.error('❌', e.message);
  process.exit(1);
});
