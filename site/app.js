'use strict';

/* =======================================================================
   1. 常量
   ======================================================================= */
const MODES = [
  { id: 'rewrite',  label: '去 AI 味改写', ph: '把要改写的文字贴进来…' },
  { id: 'create',   label: '文本创作',     ph: '说说你想写什么，比如：写条朋友圈，说今天加班到十点' },
  { id: 'ask',      label: '随便问问',     ph: '想问点什么？' },
  { id: 'academic', label: '学术润色',     ph: '贴上需要润色的论文段落…' },
  { id: 'code',     label: '代码',         ph: '需要写、改，还是解释一段代码？' }
];

const MODELS = [
  { id: 'deepseek-flash',  desc: '快，会思考' },
  { id: 'deepseek-v4-pro', desc: '更强，更慢' }
];

const SAMPLES = [
  { icon: 'pen',    title: '把 AI 腔改成人话', desc: '粘贴原文，按你平时的语气重写一遍' },
  { icon: 'bubble', title: '写条朋友圈',      desc: '加班到十点，语气别太丧' },
  { icon: 'doc',    title: '总结重写一遍',    desc: '别写成汇报稿，像在开会时口头讲' },
  { icon: 'bulb',   title: '讲明白一件事',    desc: '用大白话解释模型量化压缩' }
];

const DEMO_TEXT = '大模型这两年跑得实在太快了，快到人有点跟不上。机会一下子多出一大把，可麻烦也跟着一块儿来——说句实话，我们还没完全准备好。';

const TOKEN_KEY = 'zzy_token';
const PREF_KEY = 'zzy_prefs';

/* =======================================================================
   2. 图标
   ======================================================================= */
const ICONS = {
  pen:    '<path d="M2.5 13.5 3 11l7-7 2 2-7 7-2.5.5Z"/><path d="M10.5 3.5 12 2l2 2-1.5 1.5Z"/>',
  bubble: '<path d="M13.5 9.5a2 2 0 0 1-2 2H6l-3 2.2V4a2 2 0 0 1 2-2h6.5a2 2 0 0 1 2 2Z"/>',
  doc:    '<path d="M9 1.8H4.5a1.5 1.5 0 0 0-1.5 1.5v9.4a1.5 1.5 0 0 0 1.5 1.5h7A1.5 1.5 0 0 0 13 12.7V6.3Z"/><path d="M9 1.8v4.5h4"/><path d="M5.5 9h5M5.5 11h3"/>',
  bulb:   '<path d="M6 12h4"/><path d="M5.8 10.2a4 4 0 1 1 4.4 0c-.2.2-.2.5-.2.8H6c0-.3 0-.6-.2-.8Z"/><path d="M6.5 13.6h3"/>',
  copy:   '<rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 5.5V4a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5"/>',
  redo:   '<path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13.2 2.4v3.2h-3.2"/>',
  check:  '<path d="M3 8.4 6.2 11.6 13 4.8"/>',
  chev:   '<path d="M6 4.5 9.5 8 6 11.5"/>',
  trash:  '<path d="M2.8 4.5h10.4"/><path d="M6.2 4.5V3a1 1 0 0 1 1-1h1.6a1 1 0 0 1 1 1v1.5"/><path d="M4 4.5 4.6 13a1 1 0 0 0 1 .9h4.8a1 1 0 0 0 1-.9L12 4.5"/>',
  plus:   '<path d="M8 3v10M3 8h10"/>'
};

function svg(name, size = 14) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" stroke="currentColor"
    stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}

/* =======================================================================
   3. 状态与本地偏好（只存 token / 模型 / 模式，不存对话）
   ======================================================================= */
const state = {
  token: '',
  user: null,
  usage: null,
  model: 'deepseek-flash',
  mode: 'rewrite',
  demo: false,
  convos: [],
  currentId: null,
  busy: false,
  streamingId: null,
  controller: null,
  loadSeq: 0,
  draftSeq: 0,
  codeUntil: 0,
  cooldownTimer: null,
  adminUsers: []
};

const $ = id => document.getElementById(id);
const chatEl = $('chat'), chatInner = $('chatInner'), inputEl = $('input'),
      sendBtn = $('send'), modesEl = $('modes'), convoListEl = $('convoList'),
      convoTitleEl = $('convoTitle'), modelNameEl = $('modelName'),
      modelMenuEl = $('modelMenu'), modelChipEl = $('modelChip'),
      charCountEl = $('charCount'), maskEl = $('mask'), modalEl = $('modal'),
      banner = $('keyBanner'), bannerText = $('bannerText'),
      accountPill = $('accountPill'), accountText = $('accountText'),
      quotaChip = $('quotaChip');

function loadPrefs() {
  try {
    state.token = localStorage.getItem(TOKEN_KEY) || '';
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || '{}');
    if (MODELS.some(m => m.id === p.model)) state.model = p.model;
    if (MODES.some(m => m.id === p.mode)) state.mode = p.mode;
  } catch (e) { /* 隐私模式忽略 */ }
}

function savePrefs() {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify({ model: state.model, mode: state.mode }));
  } catch (e) { /* 忽略 */ }
}

function saveToken(token) {
  state.token = token || '';
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* 忽略 */ }
}

/* =======================================================================
   4. API（相对路径：/ZZYProj/ 下自动命中 /ZZYProj/api/*）
   ======================================================================= */
class ApiError extends Error {
  constructor(message, { status = 0, code = '', requestId = '', details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

function apiUrl(path) {
  return 'api/' + String(path).replace(/^\/+/, '');
}

async function api(path, { method = 'GET', body, signal, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && state.token) headers['Authorization'] = 'Bearer ' + state.token;

  let res;
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('网络异常，请检查连接后重试。');
  }

  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 由下面统一报错 */ }

  if (!res.ok) {
    const err = new ApiError(
      (data && data.message) || `请求失败（HTTP ${res.status}）`,
      {
        status: res.status,
        code: (data && data.code) || '',
        requestId: (data && data.request_id) || res.headers.get('X-Request-ID') || '',
        details: (data && data.details) || null
      }
    );
    if (res.status === 401 && auth) handleUnauthorized();
    throw err;
  }
  return data;
}

/* =======================================================================
   5. 弹窗基础
   ======================================================================= */
function openModal(wide = false) {
  modalEl.className = 'modal' + (wide ? ' wide' : '');
  maskEl.classList.add('show');
}

function closeModal() {
  maskEl.classList.remove('show');
  modalEl.innerHTML = '';
  if (state.cooldownTimer) {
    clearInterval(state.cooldownTimer);
    state.cooldownTimer = null;
  }
}

function usageHtml() {
  const u = state.usage;
  if (!u) return '<p class="usage-line">正在读取用量…</p>';
  const d = u.daily, m = u.monthly;
  const dayText = d.unlimited ? `今日消息：${d.used} 条（不限）` : `今日消息：${d.used} / ${d.limit} 条`;
  const monthText = m.unlimited
    ? `本月 token：${Number(m.tokens_used).toLocaleString()}（不限）`
    : `本月 token：${Number(m.tokens_used).toLocaleString()} / ${Number(m.token_limit).toLocaleString()}`;
  return `<p class="usage-line">${dayText}</p>
    <p class="usage-line">${monthText}</p>
    <p class="usage-line" style="color:var(--ink-3)">额度每日 00:00 / 次月 1 日自动重置</p>`;
}

/* =======================================================================
   6. 登录 / 注册
   ======================================================================= */
function setAuthStatus(text, cls = '') {
  const el = $('authStatus');
  if (!el) return;
  el.className = 'status' + (cls ? ' ' + cls : '');
  el.textContent = text || '';
}

function openAuth(tab = 'login') {
  state.authTab = tab;
  renderAuth();
  maskEl.classList.add('show');
  setTimeout(() => { const el = $('authEmail'); if (el) el.focus(); }, 40);
}

function startCooldown(btn) {
  if (state.cooldownTimer) clearInterval(state.cooldownTimer);
  const tick = () => {
    const left = Math.ceil((state.codeUntil - Date.now()) / 1000);
    if (left > 0) {
      btn.disabled = true;
      btn.textContent = `${left} 秒后重发`;
    } else {
      btn.disabled = false;
      btn.textContent = '发送验证码';
      clearInterval(state.cooldownTimer);
      state.cooldownTimer = null;
    }
  };
  tick();
  state.cooldownTimer = setInterval(tick, 1000);
}

function renderAuth() {
  const isLogin = state.authTab !== 'register';
  openModal();
  modalEl.innerHTML = `
    <h2>${isLogin ? '登录' : '注册'}</h2>
    <p class="desc">${isLogin
      ? '用邮箱和密码登录。对话保存在服务端，换设备可继续。'
      : '注册后即可使用。每日消息与每月 token 有额度，用完自动重置。'}</p>

    <div class="auth-tabs" role="tablist">
      <button type="button" role="tab" data-tab="login" aria-selected="${isLogin}">登录</button>
      <button type="button" role="tab" data-tab="register" aria-selected="${!isLogin}">注册</button>
    </div>

    <form id="authForm" novalidate>
      <label for="authEmail">邮箱</label>
      <input id="authEmail" type="email" autocomplete="email" placeholder="you@example.com" required>
      ${isLogin ? '' : `
        <label for="authNick">昵称（可选）</label>
        <input id="authNick" maxlength="30" placeholder="怎么称呼你">
      `}
      <label for="authPassword">密码</label>
      <input id="authPassword" type="password"
             autocomplete="${isLogin ? 'current-password' : 'new-password'}"
             placeholder="至少 8 位" required>
      ${isLogin ? '' : `
        <label for="authCode">邮箱验证码</label>
        <div class="code-row">
          <input id="authCode" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="6 位数字">
          <button type="button" id="sendCodeBtn">发送验证码</button>
        </div>
      `}
      <div class="status" id="authStatus"></div>
      <div class="row"><button class="primary" type="submit">${isLogin ? '登录' : '注册'}</button></div>
      <div class="row"><button class="ghost" type="button" id="authClose">关闭</button></div>
    </form>
    <p class="desc" style="margin-top:12px;margin-bottom:0">
      不想注册？<a href="#" id="authDemo">用演示模式看看</a>
    </p>`;

  modalEl.querySelectorAll('[data-tab]').forEach(b => {
    b.onclick = () => { state.authTab = b.dataset.tab; renderAuth(); };
  });
  $('authClose').onclick = closeModal;
  $('authDemo').onclick = e => { e.preventDefault(); enterDemo(); };

  const sendBtnEl = $('sendCodeBtn');
  if (sendBtnEl) {
    if (state.codeUntil > Date.now()) startCooldown(sendBtnEl);
    sendBtnEl.onclick = async () => {
      const email = $('authEmail').value.trim();
      const password = $('authPassword').value;
      if (!email) { setAuthStatus('先填邮箱。', 'bad'); return; }
      if (password.length < 8) { setAuthStatus('密码至少 8 位。', 'bad'); return; }
      sendBtnEl.disabled = true;
      setAuthStatus('正在发送…');
      try {
        await api('auth/request-code', {
          method: 'POST', auth: false,
          body: { email, password, nickname: ($('authNick') || {}).value || '' }
        });
        state.codeUntil = Date.now() + 60000;
        startCooldown(sendBtnEl);
        setAuthStatus('验证码已发送，请查收（含垃圾箱）。', 'ok');
        const codeEl = $('authCode');
        if (codeEl) codeEl.focus();
      } catch (e) {
        sendBtnEl.disabled = false;
        setAuthStatus(e.message + (e.requestId ? `（request_id: ${e.requestId}）` : ''), 'bad');
      }
    };
  }

  $('authForm').onsubmit = async e => {
    e.preventDefault();
    const email = $('authEmail').value.trim();
    const password = $('authPassword').value;
    const submit = e.submitter || modalEl.querySelector('button[type="submit"]');
    if (!email) { setAuthStatus('先填邮箱。', 'bad'); return; }
    if (password.length < 8) { setAuthStatus('密码至少 8 位。', 'bad'); return; }

    if (isLogin) {
      if (submit) submit.disabled = true;
      setAuthStatus('正在登录…');
      try {
        const data = await api('auth/login', { method: 'POST', auth: false, body: { email, password } });
        await onAuthSuccess(data);
      } catch (err) {
        setAuthStatus(err.message + (err.requestId ? `（request_id: ${err.requestId}）` : ''), 'bad');
      } finally { if (submit) submit.disabled = false; }
      return;
    }

    const code = ($('authCode').value || '').trim();
    if (!/^\d{6}$/.test(code)) { setAuthStatus('验证码应为 6 位数字。', 'bad'); return; }
    if (submit) submit.disabled = true;
    setAuthStatus('正在注册…');
    try {
      const data = await api('auth/register', {
        method: 'POST', auth: false,
        body: { email, password, code, nickname: ($('authNick') || {}).value || '' }
      });
      await onAuthSuccess(data);
    } catch (err) {
      setAuthStatus(err.message + (err.requestId ? `（request_id: ${err.requestId}）` : ''), 'bad');
    } finally { if (submit) submit.disabled = false; }
  };
}

async function onAuthSuccess(data) {
  saveToken(data.token);
  state.user = data.user;
  state.usage = data.user.usage;
  state.demo = false;
  closeModal();
  renderAccountState();
  try {
    await refreshConvos();
  } catch (e) {
    appendError('登录成功，但读取会话失败：' + e.message, { requestId: e.requestId });
  }
  renderConvoList();
  renderChat();
  toast('欢迎，' + (data.user.nickname || data.user.email));
}

function handleUnauthorized() {
  if (!state.token && !state.user) return;
  saveToken('');
  state.user = null;
  state.usage = null;
  state.convos = [];
  state.currentId = null;
  renderAccountState();
  renderConvoList();
  renderChat();
  openAuth('login');
  toast('登录状态已失效，请重新登录');
}

async function refreshMe() {
  if (!state.token) return;
  try {
    const data = await api('auth/me');
    state.user = data.user;
    state.usage = data.user.usage;
    renderAccountState();
  } catch (e) {
    if (e.status === 401) handleUnauthorized();
  }
}

/* =======================================================================
   7. 账号
   ======================================================================= */
function openAccount() {
  if (!state.user) { openAuth('login'); return; }
  openModal();
  const u = state.user;
  modalEl.innerHTML = `
    <h2>账号</h2>
    <p class="desc">${escapeHtml(u.email)} · ${u.role === 'admin' ? '管理员' : '用户'}</p>
    ${usageHtml()}
    <div class="row">
      <button class="ghost" id="changePwdBtn">修改密码</button>
      <button class="ghost" id="logoutBtn">退出登录</button>
    </div>
    <div class="row"><button class="ghost" id="accountClose">关闭</button></div>
    ${u.role === 'admin' ? '<div class="row"><button class="primary" id="openAdminBtn">管理后台</button></div>' : ''}
    <p class="desc" style="margin-top:14px;margin-bottom:0;color:var(--ink-3)">
      对话内容保存在服务端；管理员只能看到用量，不提供正文读取入口。
    </p>`;
  $('accountClose').onclick = closeModal;
  $('logoutBtn').onclick = logout;
  $('changePwdBtn').onclick = openChangePassword;
  const adminBtn = $('openAdminBtn');
  if (adminBtn) adminBtn.onclick = openAdmin;
}

function openChangePassword() {
  openModal();
  modalEl.innerHTML = `
    <h2>修改密码</h2>
    <p class="desc">需要先输入当前密码；修改后旧密码立即失效。</p>
    <form id="pwdForm" novalidate>
      <label for="pwdOld">当前密码</label>
      <input id="pwdOld" type="password" autocomplete="current-password">
      <label for="pwdNew">新密码</label>
      <input id="pwdNew" type="password" autocomplete="new-password" placeholder="至少 8 位">
      <label for="pwdNew2">确认新密码</label>
      <input id="pwdNew2" type="password" autocomplete="new-password">
      <div class="status" id="pwdStatus"></div>
      <div class="row">
        <button class="primary" type="submit">保存</button>
        <button class="ghost" type="button" id="pwdCancel">取消</button>
      </div>
    </form>`;
  $('pwdCancel').onclick = openAccount;
  $('pwdForm').onsubmit = async e => {
    e.preventDefault();
    const oldP = $('pwdOld').value;
    const newP = $('pwdNew').value;
    const new2 = $('pwdNew2').value;
    const st = $('pwdStatus');
    const set = (t, c = '') => { st.className = 'status' + (c ? ' ' + c : ''); st.textContent = t; };
    if (newP.length < 8) { set('新密码至少 8 位。', 'bad'); return; }
    if (newP !== new2) { set('两次输入的新密码不一致。', 'bad'); return; }
    const btn = e.submitter || modalEl.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    set('正在保存…');
    try {
      await api('auth/change-password', {
        method: 'POST', body: { old_password: oldP, new_password: newP }
      });
      set('已修改。', 'ok');
      setTimeout(() => { if (maskEl.classList.contains('show')) openAccount(); }, 700);
    } catch (err) {
      set(err.message + (err.requestId ? `（request_id: ${err.requestId}）` : ''), 'bad');
    } finally {
      if (btn) btn.disabled = false;
    }
  };
}

async function logout() {
  try { await api('auth/logout', { method: 'POST' }); } catch (e) { /* 无状态令牌，失败也退出 */ }
  saveToken('');
  state.user = null;
  state.usage = null;
  state.demo = false;
  state.convos = [];
  state.currentId = null;
  closeModal();
  renderAccountState();
  renderConvoList();
  renderChat();
  toast('已退出登录');
}

/* =======================================================================
   8. 管理后台
   ======================================================================= */
async function openAdmin() {
  if (!state.user || state.user.role !== 'admin') { toast('需要管理员权限'); return; }
  openModal(true);
  modalEl.innerHTML = '<h2>管理后台</h2><p class="desc">正在加载…</p>';
  try {
    const [ov, us] = await Promise.all([api('admin/overview'), api('admin/users')]);
    state.adminUsers = us.users;
    renderAdmin(ov, us.users);
  } catch (e) {
    modalEl.innerHTML = `<h2>管理后台</h2>
      <p class="status bad">${escapeHtml(e.message)}${e.requestId ? `（request_id: ${e.requestId}）` : ''}</p>
      <div class="row"><button class="ghost" id="adminClose">关闭</button></div>`;
    $('adminClose').onclick = closeModal;
  }
}

function renderAdmin(ov, users) {
  const rows = users.map(u => {
    const d = u.usage.daily;
    const daily = u.limits.daily_messages === null ? '' : String(u.limits.daily_messages);
    const monthly = u.limits.monthly_tokens === null ? '' : String(u.limits.monthly_tokens);
    return `<div class="admin-row" data-id="${u.id}">
      <div class="who" title="${escapeHtml(u.email)}">${escapeHtml(u.email)}${u.role === 'admin' ? ' · 管理员' : ''}${u.disabled ? ' · 已禁用' : ''}</div>
      <div>${d.used}</div>
      <input type="number" min="-1" step="1" data-field="daily" value="${daily}" placeholder="默认 ${u.limits.daily_messages_default}">
      <input type="number" min="-1" step="1" data-field="monthly" value="${monthly}" placeholder="默认 ${u.limits.monthly_tokens_default}">
      <div class="acts">
        <button data-action="save">保存</button>
        <button data-action="toggle" class="${u.disabled ? '' : 'danger'}">${u.disabled ? '启用' : '禁用'}</button>
      </div>
    </div>`;
  }).join('');

  modalEl.innerHTML = `
    <h2>管理后台</h2>
    <p class="desc">只读用量与账号状态；不提供对话正文读取。</p>
    <div class="admin-stats">
      <div class="admin-stat">用户<b>${ov.users}</b></div>
      <div class="admin-stat">今日活跃<b>${ov.active_users_today}</b></div>
      <div class="admin-stat">今日消息<b>${ov.today.messages}</b></div>
      <div class="admin-stat">本月 token<b>${Number(ov.monthly.tokens).toLocaleString()}</b></div>
    </div>
    <div class="admin-head">邮箱 / 今日消息 / 日额度 / 月 token / 操作（留空=默认，-1=不限）</div>
    <div class="admin-list">${rows || '<div class="convo" style="padding:12px">还没有用户</div>'}</div>
    <div class="row"><button class="ghost" id="adminClose">关闭</button></div>`;

  $('adminClose').onclick = closeModal;

  modalEl.querySelectorAll('.admin-row').forEach(row => {
    const id = row.dataset.id;
    const dailyInput = row.querySelector('[data-field="daily"]');
    const monthlyInput = row.querySelector('[data-field="monthly"]');
    row.querySelector('[data-action="save"]').onclick = async () => {
      const parse = v => (v.trim() === '' ? null : Number(v));
      const daily = parse(dailyInput.value);
      const monthly = parse(monthlyInput.value);
      const bad = v => v !== null && v !== -1 && !(v >= 1);
      if (bad(daily) || bad(monthly)) {
        toast('额度只能是 -1（不限）或 >=1；留空表示默认');
        return;
      }
      await adminPatch(id, { daily_limit: daily, monthly_token_limit: monthly });
    };
    row.querySelector('[data-action="toggle"]').onclick = async () => {
      const target = state.adminUsers.find(u => u.id === id);
      if (!target) return;
      await adminPatch(id, { disabled: !target.disabled });
    };
  });
}

async function adminPatch(id, fields) {
  try {
    const data = await api(`admin/users/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: fields
    });
    if (state.user && state.user.id === id) {
      state.user = data.user;
      state.usage = data.user.usage;
      renderAccountState();
    }
    toast('已更新');
    await openAdmin();
  } catch (e) {
    toast('更新失败：' + e.message);
  }
}

/* =======================================================================
   9. 会话
   ======================================================================= */
function uid() {
  return 'm-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function currentConvo() {
  return state.convos.find(c => c.id === state.currentId) || null;
}

function ensureDraft(firstText) {
  let convo = currentConvo();
  if (convo) {
    if (!Array.isArray(convo.messages)) convo.messages = [];
    return convo;
  }
  const text = firstText.replace(/\s+/g, ' ').trim();
  convo = {
    id: 'draft-' + (++state.draftSeq),
    title: text.slice(0, 12) + (text.length > 12 ? '…' : ''),
    createdAt: Date.now(),
    updatedAt: Date.now(),
    message_count: 0,
    tokens: 0,
    messages: []
  };
  state.convos.unshift(convo);
  state.currentId = convo.id;
  return convo;
}

function linkConversation(convo, id, title) {
  if (!id || convo.id === id) return;
  const oldId = convo.id;
  convo.id = id;
  if (title) convo.title = title;
  if (state.currentId === oldId) state.currentId = id;
  if (state.streamingId === oldId) state.streamingId = id;
  // 若列表里已有同一会话（例如刷新过），去重
  state.convos = state.convos.filter(c => c === convo || c.id !== id);
  convoTitleEl.textContent = convo.title;
  renderConvoList();
}

async function refreshConvos() {
  const data = await api('conversations');
  const prev = new Map(state.convos.map(c => [c.id, c]));
  state.convos = (data.conversations || []).map(c => {
    const old = prev.get(c.id);
    return {
      id: c.id,
      title: c.title,
      message_count: c.message_count,
      createdAt: Date.parse(c.created_at) || Date.now(),
      updatedAt: Date.parse(c.updated_at) || Date.now(),
      tokens: old ? old.tokens : 0,
      messages: old ? old.messages : undefined
    };
  });
  if (state.currentId && !state.convos.some(c => c.id === state.currentId)) {
    state.currentId = null;
  }
}

async function openConvo(id) {
  if (state.busy) stop();
  state.currentId = id;
  const convo = currentConvo();
  if (!convo) return;
  convoTitleEl.textContent = convo.title;
  renderConvoList();
  if (Array.isArray(convo.messages)) { renderChat(); }
  else {
    chatInner.innerHTML = '<div class="conv-meta">正在加载对话…</div>';
    const seq = ++state.loadSeq;
    try {
      const data = await api(`conversations/${encodeURIComponent(id)}`);
      if (seq !== state.loadSeq || state.currentId !== id) return;
      convo.title = data.conversation.title;
      convo.messages = data.messages.map(m => ({
        ...m,
        tokens: ((m.usage && m.usage.input) || 0) + ((m.usage && m.usage.output) || 0)
      }));
      convo.tokens = convo.messages.reduce((s, m) => s + (m.tokens || 0), 0);
      convoTitleEl.textContent = convo.title;
      renderChat();
      renderConvoList();
    } catch (e) {
      if (seq !== state.loadSeq) return;
      appendError('读取会话失败：' + e.message, { requestId: e.requestId });
    }
  }
  if (window.innerWidth <= 900) closeSidebar();
}

function newChat() {
  if (state.busy) stop();
  state.loadSeq++;
  state.currentId = null;
  convoTitleEl.textContent = '新的对话';
  renderChat();
  renderConvoList();
  inputEl.value = '';
  autoGrow();
  syncSend();
  updateCharCount();
  inputEl.focus();
}

async function deleteConvo(id, ev) {
  ev.stopPropagation();
  if (state.busy && state.streamingId === id) stop();
  if (!id.startsWith('draft-')) {
    try {
      await api(`conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (e) {
      toast('删除失败：' + e.message);
      return;
    }
  }
  state.convos = state.convos.filter(c => c.id !== id);
  if (state.currentId === id) {
    state.currentId = null;
    convoTitleEl.textContent = '新的对话';
    renderChat();
  }
  renderConvoList();
}

function groupOf(ts) {
  const t = Number(ts);
  if (!Number.isFinite(t)) return '更早';
  const now = Date.now();
  const sameDay = new Date(now).toDateString() === new Date(t).toDateString();
  if (sameDay) return '今天';
  if (now - t < 7 * 864e5) return '最近 7 天';
  return '更早';
}

function renderConvoList() {
  convoListEl.innerHTML = '';
  if (!state.convos.length) {
    const hint = document.createElement('div');
    hint.className = 'group-label';
    hint.textContent = state.user ? '还没有会话' : '登录后显示会话';
    convoListEl.appendChild(hint);
    return;
  }

  const groups = new Map();
  state.convos.slice().sort((a, b) => b.updatedAt - a.updatedAt).forEach(c => {
    const g = groupOf(c.updatedAt);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  });

  groups.forEach((items, name) => {
    const label = document.createElement('div');
    label.className = 'group-label';
    label.textContent = name;
    convoListEl.appendChild(label);

    items.forEach(c => {
      const row = document.createElement('div');
      row.className = 'convo';
      row.setAttribute('role', 'button');
      row.tabIndex = 0;
      if (c.id === state.currentId) row.setAttribute('aria-current', 'true');
      row.onclick = () => openConvo(c.id);
      row.onkeydown = e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openConvo(c.id); }
      };

      const title = document.createElement('span');
      title.className = 'convo-name';
      title.textContent = c.title || '新的对话';

      const badge = document.createElement('span');
      badge.className = 'convo-badge';
      badge.textContent = '进行中';
      badge.hidden = c.id !== state.streamingId;

      const del = document.createElement('button');
      del.className = 'convo-del';
      del.setAttribute('aria-label', '删除会话 ' + (c.title || ''));
      del.innerHTML = svg('trash', 12);
      del.onclick = e => deleteConvo(c.id, e);

      row.append(title, badge, del);
      convoListEl.appendChild(row);
    });
  });
}

/* =======================================================================
   10. 聊天渲染
   ======================================================================= */
function renderChat() {
  chatInner.innerHTML = '';
  const convo = currentConvo();
  if (!convo || !Array.isArray(convo.messages) || !convo.messages.length) {
    chatInner.appendChild(buildEmpty());
    return;
  }
  convo.messages.forEach(m => chatInner.appendChild(buildMessage(m, convo)));
  if (convo.tokens > 0) chatInner.appendChild(buildMeta(convo));
  scrollToBottomSoon();
}

function buildMessage(m, convo) {
  return m.role === 'user' ? buildUserMessage(m) : buildBotMessage(m, convo);
}

function hideEmpty() {
  const el = chatInner.querySelector('.empty');
  if (el) el.remove();
}

function scrollToBottomSoon() {
  requestAnimationFrame(() => { chatEl.scrollTop = chatEl.scrollHeight; });
}

function buildEmpty() {
  const wrap = document.createElement('div');
  wrap.className = 'empty';
  wrap.innerHTML = `<h1>今天想写点什么？</h1><p>贴一段 AI 味的稿子，或者直接说你要什么。</p>`;

  const grid = document.createElement('div');
  grid.className = 'samples';
  SAMPLES.forEach(s => {
    const b = document.createElement('button');
    b.className = 'sample';
    b.innerHTML = `
      <span class="s-title">${svg(s.icon, 14)}${s.title}</span>
      <span class="s-desc">${s.desc}</span>`;
    b.onclick = () => {
      inputEl.value = s.title === '把 AI 腔改成人话'
        ? '随着人工智能技术的飞速发展，我们正迎来前所未有的机遇与挑战。'
        : s.desc;
      autoGrow(); syncSend(); updateCharCount(); inputEl.focus();
    };
    grid.appendChild(b);
  });
  wrap.appendChild(grid);
  return wrap;
}

function buildUserMessage(m) {
  const wrap = document.createElement('div');
  wrap.className = 'msg user';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = m.content;
  wrap.appendChild(bubble);
  return wrap;
}

function buildBotMessage(m, convo) {
  const wrap = document.createElement('div');
  wrap.className = 'msg bot' + (m.superseded ? ' superseded' : '');
  wrap.dataset.mid = m.id;

  if (m.superseded) {
    const tag = document.createElement('span');
    tag.className = 'prev-tag';
    tag.textContent = '上一版';
    wrap.appendChild(tag);
  }
  if (m.reasoning) wrap.appendChild(buildThink(m.reasoning));

  const md = document.createElement('div');
  md.className = 'md';
  md.innerHTML = mdToHtml(m.content || '');
  wrap.appendChild(md);

  const isLast = convo.messages[convo.messages.length - 1] === m;
  wrap.appendChild(buildActions(m, convo, isLast));
  return wrap;
}

function buildThink(text) {
  const d = document.createElement('details');
  d.className = 'think';
  const sum = document.createElement('summary');
  sum.innerHTML = `${svg('chev', 12)}<span>想了想…</span>`;
  const body = document.createElement('div');
  body.className = 'think-body';
  body.textContent = text;
  d.append(sum, body);
  return d;
}

function buildActions(m, convo, isLast) {
  const row = document.createElement('div');
  row.className = 'actions';

  const copy = document.createElement('button');
  copy.innerHTML = `${svg('copy', 12)}复制`;
  copy.onclick = () => {
    navigator.clipboard.writeText(m.content || '').then(() => {
      copy.innerHTML = `${svg('check', 12)}已复制`;
      setTimeout(() => (copy.innerHTML = `${svg('copy', 12)}复制`), 1500);
    }).catch(() => toast('复制失败，请手动选中'));
  };
  row.appendChild(copy);

  if (isLast && !state.demo) {
    const redo = document.createElement('button');
    redo.innerHTML = `${svg('redo', 12)}重新生成`;
    redo.onclick = () => regenerate(m, convo);
    row.appendChild(redo);
  }

  const adopt = document.createElement('button');
  const paint = () => {
    adopt.className = m.accepted ? 'accepted' : '';
    adopt.innerHTML = `${svg('check', 12)}${m.accepted ? '已采纳为定稿' : '采纳为定稿'}`;
  };
  paint();
  adopt.onclick = () => {
    m.accepted = !m.accepted;
    paint();
    toast(m.accepted ? '已标记为定稿（仅本页有效）' : '已取消定稿');
  };
  row.appendChild(adopt);
  return row;
}

function buildMeta(convo) {
  const el = document.createElement('div');
  el.className = 'conv-meta';
  el.textContent = state.demo
    ? '演示模式：回复为内置示例，不会保存'
    : `本次对话 ${Number(convo.tokens || 0).toLocaleString()} tokens · 服务端保存`;
  return el;
}

function appendMetaIfNeeded(convo) {
  const old = chatInner.querySelector('.conv-meta');
  if (old) old.remove();
  if (convo.tokens > 0 || state.demo) chatInner.appendChild(buildMeta(convo));
}

/* =======================================================================
   11. Markdown
   ======================================================================= */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function inlineMd(s) {
  return escapeHtml(s)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
}

function mdToHtml(src) {
  const lines = String(src || '').split('\n');
  const out = [];
  let inCode = false, codeBuf = [], listBuf = [], paraBuf = [];

  const flushPara = () => {
    if (paraBuf.length) { out.push(`<p>${paraBuf.join('<br>')}</p>`); paraBuf = []; }
  };
  const flushList = () => {
    if (listBuf.length) {
      out.push(`<ul>${listBuf.map(x => `<li>${x}</li>`).join('')}</ul>`);
      listBuf = [];
    }
  };
  const flushAll = () => { flushPara(); flushList(); };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');

    if (/^\s*```/.test(line)) {
      if (!inCode) { flushAll(); inCode = true; codeBuf = []; }
      else {
        inCode = false;
        out.push(`<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`);
      }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }

    const t = line.trim();
    if (!t) { flushAll(); continue; }

    const li = t.match(/^[-*•]\s+(.*)$/) || t.match(/^\d+[.、)]\s+(.*)$/);
    if (li) { flushPara(); listBuf.push(inlineMd(li[1])); continue; }

    if (t.startsWith('>')) {
      flushAll();
      out.push(`<blockquote>${inlineMd(t.replace(/^>\s?/, ''))}</blockquote>`);
      continue;
    }
    if (/^#{1,6}\s+/.test(t)) {
      flushAll();
      out.push(`<h4>${inlineMd(t.replace(/^#{1,6}\s+/, ''))}</h4>`);
      continue;
    }

    flushList();
    paraBuf.push(inlineMd(t));
  }
  if (inCode) out.push(`<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`);
  flushAll();
  return out.join('');
}

/* =======================================================================
   12. 发送与流式
   ======================================================================= */
function onSend() {
  if (state.busy) { stop(); return; }
  sendMessage();
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || state.busy) return;
  if (!state.user && !state.demo) { openAuth('login'); return; }

  const convo = ensureDraft(text);
  convoTitleEl.textContent = convo.title;

  inputEl.value = '';
  autoGrow(); syncSend();
  charCountEl.textContent = '0 字';

  const userMsg = { id: uid(), role: 'user', content: text };
  convo.messages.push(userMsg);
  convo.updatedAt = Date.now();
  hideEmpty();
  chatInner.appendChild(buildUserMessage(userMsg));
  scrollToBottomSoon();

  await runBotTurn(convo, { message: text });
}

async function runBotTurn(convo, { regenerate = false, message = '' } = {}) {
  const botMsg = { id: uid(), role: 'assistant', content: '', reasoning: '', tokens: 0 };
  convo.messages.push(botMsg);

  const node = buildBotMessage(botMsg, convo);
  const mdEl = node.querySelector('.md');
  mdEl.innerHTML = '<span class="dots"><span></span><span></span><span></span></span>';
  const actionsEl = node.querySelector('.actions');
  if (actionsEl) actionsEl.remove();
  hideEmpty();
  chatInner.appendChild(node);
  scrollToBottomSoon();

  state.busy = true;
  state.streamingId = convo.id;
  renderConvoList();
  setBusyUI(true);

  const textNode = document.createTextNode('');
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  let thinkEl = null, thinkBody = null;

  const beginStreamingDom = () => {
    mdEl.innerHTML = '';
    mdEl.appendChild(textNode);
    mdEl.appendChild(cursor);
  };

  try {
    if (state.demo) {
      await demoStream(chunk => {
        if (!textNode.parentNode) beginStreamingDom();
        const stick = nearBottom();
        textNode.appendData(chunk);
        botMsg.content += chunk;
        convo.updatedAt = Date.now();
        if (stick) chatEl.scrollTop = chatEl.scrollHeight;
      });
    } else {
      const lastUser = [...convo.messages].reverse().find(m => m.role === 'user');
      const isDraft = String(convo.id).startsWith('draft-');
      const body = {
        conversation_id: isDraft ? null : convo.id,
        message: message || (lastUser ? lastUser.content : ''),
        mode: state.mode,
        model: state.model,
        regenerate
      };

      state.controller = new AbortController();
      await streamChat(body, {
        onMeta(payload) {
          linkConversation(convo, payload.conversation_id, payload.title);
        },
        onReasoning(chunk) {
          if (!thinkEl) {
            thinkEl = buildThink('');
            thinkEl.open = true;
            thinkBody = thinkEl.querySelector('.think-body');
            node.insertBefore(thinkEl, mdEl);
          }
          thinkBody.textContent += chunk;
          botMsg.reasoning += chunk;
        },
        onContent(chunk) {
          if (!textNode.parentNode) beginStreamingDom();
          if (thinkEl && thinkEl.open) thinkEl.open = false;
          const stick = nearBottom();
          textNode.appendData(chunk);
          botMsg.content += chunk;
          if (stick) chatEl.scrollTop = chatEl.scrollHeight;
        },
        onDone(payload) {
          linkConversation(convo, payload.conversation_id, payload.title);
          const u = payload.usage || {};
          botMsg.tokens = (u.input || 0) + (u.output || 0);
          convo.tokens = (convo.tokens || 0) + botMsg.tokens;
          convo.updatedAt = Date.now();
        }
      }, state.controller.signal);
    }

    cursor.remove();
    mdEl.innerHTML = mdToHtml(botMsg.content || '（这次没憋出内容，再试一次吧）');
    node.appendChild(buildActions(botMsg, convo, true));
    convo.updatedAt = Date.now();
    appendMetaIfNeeded(convo);
  } catch (err) {
    if (err && err.name === 'AbortError') {
      cursor.remove();
      if (botMsg.content) {
        mdEl.innerHTML = mdToHtml(botMsg.content);
        node.appendChild(buildActions(botMsg, convo, true));
      } else {
        node.remove();
        convo.messages = convo.messages.filter(m => m.id !== botMsg.id);
      }
    } else {
      node.remove();
      convo.messages = convo.messages.filter(m => m.id !== botMsg.id);
      appendError(err.message || '生成失败，请重试。', {
        auth: !state.user,
        requestId: err.requestId
      });
      if (err.status === 401) handleUnauthorized();
      if (err.status === 429) refreshMe();
    }
  } finally {
    state.busy = false;
    state.streamingId = null;
    state.controller = null;
    setBusyUI(false);
    renderConvoList();
    if (state.token && !state.demo) refreshMe();
    inputEl.focus();
  }
}

async function streamChat(body, handlers, signal) {
  let resp;
  try {
    resp = await fetch(apiUrl('chat'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + state.token
      },
      body: JSON.stringify(body),
      signal
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('网络异常，请检查连接后重试。');
  }

  if (!resp.ok) {
    let data = null;
    try { data = await resp.json(); } catch (e) { /* 忽略 */ }
    const err = new ApiError(
      (data && data.message) || `请求失败（HTTP ${resp.status}）`,
      {
        status: resp.status,
        code: (data && data.code) || '',
        requestId: (data && data.request_id) || resp.headers.get('X-Request-ID') || '',
        details: (data && data.details) || null
      }
    );
    throw err;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  const dispatch = block => {
    let event = 'message';
    const data = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trim());
    }
    if (!data.length) return;
    let payload;
    try { payload = JSON.parse(data.join('\n')); } catch (e) { return; }

    if (event === 'delta') handlers.onContent && handlers.onContent(payload.content || '');
    else if (event === 'reasoning') handlers.onReasoning && handlers.onReasoning(payload.content || '');
    else if (event === 'meta') handlers.onMeta && handlers.onMeta(payload);
    else if (event === 'done') handlers.onDone && handlers.onDone(payload);
    else if (event === 'error') {
      throw new ApiError(payload.message || '生成失败，请重试。', {
        code: payload.code || '',
        requestId: payload.request_id || '',
        details: payload.details || null
      });
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      dispatch(block);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}

async function regenerate(botMsg, convo) {
  if (state.busy || state.demo) return;
  if (!convo.id || String(convo.id).startsWith('draft-')) return;
  const idx = convo.messages.indexOf(botMsg);
  if (idx !== convo.messages.length - 1) { toast('只能重新生成最后一条回复'); return; }
  convo.messages.splice(idx, 1);
  convo.tokens = Math.max(0, (convo.tokens || 0) - (botMsg.tokens || 0));
  renderChat();
  chatEl.scrollTop = chatEl.scrollHeight;
  await runBotTurn(convo, { regenerate: true });
}

function stop() {
  if (state.controller) state.controller.abort();
}

function setBusyUI(busy) {
  sendBtn.classList.toggle('stop', busy);
  sendBtn.textContent = busy ? '■' : '↑';
  sendBtn.setAttribute('aria-label', busy ? '停止生成' : '发送');
  syncSend();
}

function syncSend() {
  sendBtn.disabled = state.busy ? false : !inputEl.value.trim();
}

function updateCharCount() {
  charCountEl.textContent = inputEl.value.trim().length + ' 字';
}

function autoGrow() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 180) + 'px';
}

function nearBottom() {
  return chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < 80;
}

function scrollIfNearBottom(force) {
  if (force || nearBottom()) chatEl.scrollTop = chatEl.scrollHeight;
}

function appendError(msg, { auth = false, requestId = '' } = {}) {
  const text = String(msg) + (requestId ? `\n（request_id: ${requestId}）` : '');
  const wrap = document.createElement('div');
  wrap.className = 'msg err';
  wrap.innerHTML = `<div class="err-box">${escapeHtml(text)}
    <div class="acts">
      ${auth ? '<button data-act="login">登录 / 注册</button>' : ''}
      <button data-act="copy">复制诊断</button>
    </div>
  </div>`;
  wrap.querySelector('[data-act="copy"]').onclick = () => {
    navigator.clipboard.writeText(text)
      .then(() => toast('诊断信息已复制'))
      .catch(() => toast('复制失败，请手动选中'));
  };
  const loginBtn = wrap.querySelector('[data-act="login"]');
  if (loginBtn) loginBtn.onclick = () => { wrap.remove(); openAuth('login'); };
  chatInner.appendChild(wrap);
  scrollIfNearBottom(true);
}

/* 演示模式：不需要登录，不发服务端请求 */
function enterDemo() {
  if (state.user) { closeModal(); toast('已登录，直接使用真实模型'); return; }
  state.demo = true;
  closeModal();
  renderAccountState();
  toast('已进入演示模式');
}

function demoStream(onChunk) {
  return new Promise(resolve => {
    let i = 0;
    const timer = setInterval(() => {
      const step = 1 + Math.floor(Math.random() * 3);
      const chunk = DEMO_TEXT.slice(i, i + step);
      i += step;
      if (chunk) onChunk(chunk);
      if (i >= DEMO_TEXT.length) { clearInterval(timer); resolve(); }
    }, 40);
  });
}

/* =======================================================================
   13. 账号状态 / 配额
   ======================================================================= */
function renderAccountState() {
  const logged = !!state.user;
  accountPill.classList.toggle('warn', !logged);
  accountText.textContent = logged ? state.user.email : (state.demo ? '演示模式' : '未登录');
  accountPill.title = logged ? '账号 / 用量' : '登录 / 注册';

  if (state.demo) {
    banner.hidden = false;
    bannerText.textContent = '演示模式：回复是内置示例，不会保存。登录后使用真实模型。';
    $('bannerKeyBtn').textContent = '登录 / 注册';
    $('bannerDemoBtn').hidden = true;
  } else if (!logged) {
    banner.hidden = false;
    bannerText.textContent = '登录后即可开始；也可以先用演示模式看看。';
    $('bannerKeyBtn').textContent = '登录 / 注册';
    $('bannerDemoBtn').hidden = false;
  } else {
    banner.hidden = true;
  }

  modelNameEl.textContent = state.model;
  renderQuota();
}

function renderQuota() {
  if (!state.usage) {
    quotaChip.textContent = state.user ? '额度 —' : '未登录';
    quotaChip.title = '登录后显示额度';
    return;
  }
  const d = state.usage.daily, m = state.usage.monthly;
  quotaChip.textContent = d.unlimited ? `今日 ${d.used} / 不限` : `今日 ${d.used} / ${d.limit}`;
  quotaChip.title = m.unlimited
    ? `本月 token：${Number(m.tokens_used).toLocaleString()}（不限）`
    : `本月 token：${Number(m.tokens_used).toLocaleString()} / ${Number(m.token_limit).toLocaleString()}`;
}

/* =======================================================================
   14. 模型 / 导出 / 侧栏 / 模式
   ======================================================================= */
function renderModelMenu() {
  modelMenuEl.innerHTML = '';
  MODELS.forEach(m => {
    const b = document.createElement('button');
    b.setAttribute('role', 'menuitemradio');
    b.setAttribute('aria-checked', String(m.id === state.model));
    b.innerHTML = `${m.id}<span>${m.desc}</span>`;
    b.onclick = () => {
      state.model = m.id;
      savePrefs();
      renderAccountState();
      renderModelMenu();
      toggleModelMenu(false);
    };
    modelMenuEl.appendChild(b);
  });
}

function toggleModelMenu(show) {
  const open = show === undefined ? modelMenuEl.hidden : show;
  modelMenuEl.hidden = !open;
  modelChipEl.setAttribute('aria-expanded', String(open));
  if (open) renderModelMenu();
}

function exportConvo() {
  const convo = currentConvo();
  if (!convo || !Array.isArray(convo.messages) || !convo.messages.length) {
    toast('还没有可导出的对话');
    return;
  }
  const lines = [`# ${convo.title}`, ''];
  convo.messages.forEach(m => {
    if (!m.content) return;
    lines.push(m.role === 'user' ? '**你：**' : '**模型：**', '', m.content, '');
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (convo.title || '对话').replace(/[\\/:*?"<>|]/g, '_') + '.md';
  a.click();
  URL.revokeObjectURL(a.href);
  toast('已导出 Markdown');
}

function toast(msg) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => (t.style.opacity = '1'));
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 220); }, 1800);
}

function toggleSidebar() {
  const bar = $('sidebar');
  bar.classList.toggle('collapsed');
  const open = !bar.classList.contains('collapsed');
  if (window.innerWidth <= 900) $('scrim').classList.toggle('show', open);
}
function closeSidebar() {
  $('sidebar').classList.add('collapsed');
  $('scrim').classList.remove('show');
}
function openSidebar() {
  $('sidebar').classList.remove('collapsed');
  if (window.innerWidth <= 900) $('scrim').classList.add('show');
}

function renderModes() {
  modesEl.innerHTML = '';
  MODES.forEach(m => {
    const b = document.createElement('button');
    b.className = 'mode';
    b.textContent = m.label;
    b.setAttribute('role', 'button');
    b.setAttribute('aria-pressed', String(m.id === state.mode));
    b.onclick = () => {
      state.mode = m.id;
      savePrefs();
      renderModes();
      inputEl.placeholder = m.ph;
    };
    modesEl.appendChild(b);
  });
  const active = MODES.find(m => m.id === state.mode);
  if (active) inputEl.placeholder = active.ph;
}

/* =======================================================================
   15. 事件绑定
   ======================================================================= */
inputEl.addEventListener('input', () => {
  autoGrow();
  syncSend();
  updateCharCount();
});
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (!state.busy) sendMessage();
  }
});

sendBtn.addEventListener('click', onSend);
$('newChatBtn').addEventListener('click', newChat);
$('collapseBtn').addEventListener('click', toggleSidebar);
$('menuBtn').addEventListener('click', () => {
  const bar = $('sidebar');
  if (bar.classList.contains('collapsed')) openSidebar(); else closeSidebar();
});
$('scrim').addEventListener('click', closeSidebar);
$('exportBtn').addEventListener('click', exportConvo);
modelChipEl.addEventListener('click', () => toggleModelMenu());
$('bannerKeyBtn').addEventListener('click', () => openAuth('login'));
$('bannerDemoBtn').addEventListener('click', enterDemo);
accountPill.addEventListener('click', () => { state.user ? openAccount() : openAuth('login'); });
$('settingsBtn').addEventListener('click', () => { state.user ? openAccount() : openAuth('login'); });
maskEl.addEventListener('click', e => { if (e.target === maskEl) closeModal(); });

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeModal(); toggleModelMenu(false); }
  if (e.altKey && e.key.toLowerCase() === 'n') {
    e.preventDefault();
    newChat();
  }
});
document.addEventListener('click', e => {
  if (!e.target.closest('.model-wrap')) toggleModelMenu(false);
});

/* =======================================================================
   16. 启动
   ======================================================================= */
async function bootstrap() {
  renderAccountState();
  if (!state.token) return;
  await refreshMe();
  if (!state.user) return;
  try {
    await refreshConvos();
    renderConvoList();
  } catch (e) {
    if (e.status !== 401) appendError('读取会话失败：' + e.message, { requestId: e.requestId });
  }
}

loadPrefs();
$('factSize').textContent = Math.round(new Blob([document.documentElement.outerHTML]).size / 1024) + ' KB';
renderModes();
renderAccountState();
renderConvoList();
renderChat();
autoGrow();
syncSend();
if (window.innerWidth <= 900) closeSidebar();
inputEl.focus();
bootstrap();
