'use strict';

const $ = (id) => document.getElementById(id);
const MAX_EDGE = 1568;

const state = { day: null, today: null, busy: false };

class AuthError extends Error {}

/* ---------- 工具 ---------- */

function toast(message, ms = 2600) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function formatDay(day) {
  const [y, m, d] = day.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return `${m} 月 ${d} 日 · ${WEEKDAYS[date.getDay()]}`;
}

function formatTime(ts) {
  const t = (ts || '').slice(11, 16);
  return t || '';
}

function shiftDay(day, delta) {
  const [y, m, d] = day.split('-').map(Number);
  const date = new Date(y, m - 1, d + delta);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const TOKEN_KEY = 'journal.token';

function savedToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (err) { return ''; }
}

function saveToken(token) {
  try { localStorage.setItem(TOKEN_KEY, token); } catch (err) { /* 无痕模式，算了吧 */ }
}

function forgetToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch (err) { /* 同上 */ }
}

async function api(path, options = {}) {
  const { silentAuth = false, retried = false, ...init } = options;

  let res;
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch (err) {
    throw new Error('网络不通，检查一下连上了没有。');
  }

  // cookie 没了（清了数据、或者过期），但这台设备自己记着口令 ——
  // 静默补登一次再重试原请求。登录过一次就不该再让人输。
  if (res.status === 401 && !retried && path !== '/api/login') {
    if (await trySilentLogin()) {
      return api(path, { ...options, retried: true });
    }
  }

  if (res.status === 401) {
    if (!silentAuth) showLogin();
    throw new AuthError('未登录');
  }

  let body = {};
  try { body = await res.json(); } catch (err) { body = {}; }
  if (!res.ok) throw new Error(body.detail || `出错了（${res.status}）`);
  return body;
}

async function trySilentLogin() {
  const token = savedToken();
  if (!token) return false;
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    return res.ok;
  } catch (err) {
    return false;
  }
}

/* ---------- 遮罩：登录 / 引导访谈 ---------- */

function showOverlay(html) {
  $('panel').innerHTML = html;
  $('overlay').hidden = false;
}

function hideOverlay() {
  $('overlay').hidden = true;
}

function showLogin(message) {
  const known = savedToken();
  showOverlay(`
    <h1>日记</h1>
    <p>${message || (known ? '这张设备上的口令不对了，重新输一次。' : '输入口令进来。')}</p>
    <input id="token" type="password" placeholder="口令" autocomplete="current-password">
    <p class="err" id="loginerr"></p>
    <button id="loginbtn">进去</button>
    ${known ? '<button class="link" id="forgetbtn">换一个口令</button>' : ''}
  `);
  const submit = async (given) => {
    const token = given || $('token').value.trim();
    if (!token) return;
    try {
      await api('/api/login', {
        method: 'POST',
        body: JSON.stringify({ token }),
        silentAuth: true,
      });
      saveToken(token);
      hideOverlay();
      await boot();
    } catch (err) {
      $('loginerr').textContent = err.message;
    }
  };
  $('loginbtn').onclick = () => submit();
  const forget = $('forgetbtn');
  if (forget) forget.onclick = () => { forgetToken(); showLogin('好，输一个新的。'); };
  $('token').onkeydown = (e) => { if (e.key === 'Enter') submit(); };
  $('token').focus();
}

function showOnboarding(onboarding) {
  const q = onboarding.question;
  if (!q) return false;
  showOverlay(`
    <div class="progress">第 ${q.index + 1} / ${q.total} 个问题</div>
    <h1>先让我认识你一下</h1>
    <p>${q.text}</p>
    <textarea id="answer" rows="3" placeholder="随便说，给个大概就行"></textarea>
    <button id="answerbtn">说完了</button>
    <button class="link" id="skipbtn">跳过这个问题</button>
    <p class="err" id="oberr"></p>
  `);

  const send = async (skipped) => {
    const answer = skipped ? null : $('answer').value.trim();
    if (!skipped && !answer) { $('oberr').textContent = '写点什么，或者跳过。'; return; }
    try {
      const next = await api('/api/onboarding/answer', {
        method: 'POST',
        body: JSON.stringify({ key: q.key, answer, skipped }),
      });
      if (next.done) {
        hideOverlay();
        await load(state.day, true);
        toast('好，我记住了。');
      } else {
        showOnboarding(next);
      }
    } catch (err) {
      if (err instanceof AuthError) return;
      $('oberr').textContent = err.message;
    }
  };

  $('answerbtn').onclick = () => send(false);
  $('skipbtn').onclick = () => send(true);
  $('answer').focus();
  return true;
}

/* ---------- 渲染 ---------- */

// 心情条：-2..2 映射成 5 格。
// 单独一个光条没人看得懂 —— 实测被当成加载进度。所以必须带标题。
function moodBar(mood) {
  const level = mood + 2;
  let html = '<div class="moodbar">';
  for (let i = 0; i < 5; i += 1) {
    const on = i <= level;
    html += `<i class="${on ? 'on' : ''} ${on && level >= 3 ? 'deep' : ''}"></i>`;
  }
  return html + '</div>';
}

function cardHtml(card, day) {
  if (!card) return '';

  const hasSummary = typeof card.mood === 'number';
  const hasContent = (card.entry_count || 0) > 0;

  // 空的一天不需要卡片。以前这里会渲染出一张「0 段」的空卡，
  // 而且对已经过去的那天写「今天还没说」。
  if (!hasSummary && !hasContent) return '';

  const parts = [`${card.entry_count} 段`];
  if (card.image_count) parts.push(`${card.image_count} 张图`);

  let body;
  if (hasSummary) {
    body = `
      <div class="moodrow">
        <span class="moodcaption">状态</span>
        ${moodBar(card.mood)}
      </div>`
      + (card.mood_label ? `<div class="headline">「${escapeHtml(card.mood_label)}」</div>` : '')
      + (card.headline ? `<div class="keywords">${escapeHtml(card.headline)}</div>` : '')
      + summaryHtml(card.summary)
      + ((card.keywords && card.keywords.length)
        ? `<div class="keywords">${card.keywords.map((k) => `<span>${escapeHtml(k)}</span>`).join('')}</div>`
        : '');
  } else {
    // 白天打开时总结还没跑。说清楚，不要把「没总结」说成「你没说话」。
    body = '<div class="keywords">今天的总结要等晚上跑，这里会填上。</div>';
  }

  return `
    <div class="card">
      <h2><span>${day === state.today ? '今天的你' : '那天的你'}</span>
          <span class="count">${parts.join(' · ')}</span></h2>
      ${body}
    </div>`;
}

function summaryHtml(summary) {
  if (!summary) return '';
  const section = (title, value, cls = '') => value
    ? `<div class="summary-section ${cls}"><b>${title}</b><div>${escapeHtml(value)}</div></div>`
    : '';
  const list = (title, values, cls = '') => Array.isArray(values) && values.length
    ? `<div class="summary-section ${cls}"><b>${title}</b><ul>${values.map(v => `<li>${escapeHtml(v)}</li>`).join('')}</ul></div>`
    : '';
  return `<div class="summary-detail">
    ${section('今天的主线', summary.overview, 'overview')}
    ${list('发生了什么', summary.facts)}
    ${section('卡点', summary.tension, 'tension')}
    ${section('暂时判断', summary.hypothesis, 'hypothesis')}
    ${section('下一步', summary.next_step, 'next-step')}
    ${list('还没解决', summary.open_loop, 'open-loop')}
  </div>`;
}

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// 模型回答允许一小部分 Markdown，但绝不把模型输出直接当 HTML。
// 先完整转义，再只把安全的格式标记转换成标签。
function renderMarkdown(text) {
  const source = String(text == null ? '' : text).replace(/\r\n?/g, '\n');
  const lines = source.split('\n');
  const blocks = [];
  let list = null;
  const closeList = () => {
    if (list) { blocks.push(`<ul>${list.join('')}</ul>`); list = null; }
  };
  const inline = (value) => {
    let out = escapeHtml(value);
    out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s).,!?:;])/g, '$1<em>$2</em>');
    out = out.replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,!?:;])/g, '$1<em>$2</em>');
    return out;
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { closeList(); continue; }
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (!list) list = [];
      list.push(`<li>${inline(bullet[1])}</li>`);
    } else if (heading) {
      closeList(); blocks.push(`<h3>${inline(heading[1])}</h3>`);
    } else {
      closeList(); blocks.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return blocks.join('');
}

function renderStream(data, scrollToBottom) {
  const items = [];
  for (const entry of data.entries) {
    items.push({ ts: entry.ts, kind: 'mine', entry });
  }
  for (const said of data.utterances) {
    items.push({ ts: said.ts, kind: 'theirs', said });
  }
  items.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));

  let html = cardHtml(data.card, data.day);

  if (!items.length) {
    html += `<div class="empty">${
      data.day === data.today ? '今天还没有什么。说一句吧。' : '这一天是空的。'
    }</div>`;
  }

  for (const item of items) {
    if (item.kind === 'mine') {
      const e = item.entry;
      // 注意：这里不能换行。.bubble 是 pre-wrap，模板里的换行会被真的渲染成空行，
      // 看起来就是「时间上面空了一大块」。
      const inner = e.kind === 'image'
        ? `<img class="zoomable" src="/media/${encodeURI(e.image_path || '')}" alt="图片" loading="lazy">`
          + (e.content ? escapeHtml(e.content) : '')
        : escapeHtml(e.content);
      html += `<div class="row mine"><div class="bubble">${inner}<span class="stamp">${formatTime(e.ts)}</span></div></div>`;
    } else {
      const s = item.said;
      const marker = s.trigger && s.trigger !== 'ask' && s.trigger !== 'onboarding';
      html += `<div class="row theirs"><div class="bubble markdown ${marker ? 'marker' : ''}">${renderMarkdown(s.content)}<span class="stamp">${formatTime(s.ts)}</span></div></div>`;
    }
  }

  const stream = $('stream');
  stream.innerHTML = html;
  // 打开时停在顶部 —— 先看到「今天的你」那张卡片，那是这个产品对你的承诺。
  // 只有刚说完话才滚到底看最新的。
  if (scrollToBottom) {
    stream.scrollTop = stream.scrollHeight;
  }
}

function renderHeader(day, today) {
  const isToday = day === today;
  $('datelabel').innerHTML = isToday
    ? `今天<span class="sub">${formatDay(day)}</span>`
    : formatDay(day);

  $('next').disabled = isToday;
  $('backtotoday').hidden = isToday;

  // 原生日期选择器跟着当前天走，并且不让选到未来。
  const picker = $('datepicker');
  picker.max = today;
  if (picker.value !== day) picker.value = day;
}

/* ---------- 数据 ---------- */

async function load(day, scrollToBottom = false) {
  const query = day ? `?day=${day}` : '';
  let data;
  try {
    data = await api(`/api/state${query}`);
  } catch (err) {
    if (err instanceof AuthError) return;
    toast(err.message);
    return;
  }

  state.day = data.day;
  state.today = data.today;
  $('app').hidden = false;
  hideOverlay();
  renderHeader(data.day, data.today);
  renderStream(data, scrollToBottom);

  if (!data.configured) {
    toast('服务器还没配好模型密钥。', 5000);
  }

  if (data.onboarding && !data.onboarding.done) {
    showOnboarding(data.onboarding);
  }
}

async function boot() {
  // 1) cookie 还在就直接进去
  try {
    const me = await api('/api/me', { silentAuth: true });
    if (me.ok) {
      await load(null);
      return;
    }
  } catch (err) {
    if (!(err instanceof AuthError)) {
      showLogin('连不上服务器。');
      return;
    }
  }

  // 2) cookie 没了，用这台设备自己记着的口令补一次
  if (await trySilentLogin()) {
    await load(null);
    return;
  }

  // 3) 真的不行了，才让人输
  showLogin();
}

/* ---------- 动作 ---------- */

function setBusy(busy, label) {
  state.busy = busy;
  $('save').disabled = busy;
  $('askbtn').disabled = busy;
  $('askbtn').textContent = busy ? label || '等一下…' : '问它';
}

// 模型要十几秒才回。这期间屏幕不能一动不动，否则看起来就是卡死了。
function pushLocalBubble(text) {
  const stream = $('stream');
  const row = document.createElement('div');
  row.className = 'row mine';
  row.innerHTML = '<div class="bubble"></div>';
  const bubble = row.firstChild;
  bubble.textContent = text;
  const stamp = document.createElement('span');
  stamp.className = 'stamp';
  stamp.textContent = '刚发出';
  bubble.appendChild(stamp);
  stream.appendChild(row);
  stream.scrollTop = stream.scrollHeight;
}

function showThinking() {
  if ($('thinking')) return;
  const stream = $('stream');
  const row = document.createElement('div');
  row.className = 'row theirs';
  row.id = 'thinking';
  row.innerHTML = '<div class="bubble thinking"><span></span><span></span><span></span></div>';
  stream.appendChild(row);
  stream.scrollTop = stream.scrollHeight;
}

function hideThinking() {
  const el = $('thinking');
  if (el) el.remove();
}

async function remember() {
  const text = $('input').value.trim();
  if (!text) { toast('还没写什么。'); return; }
  setBusy(true);
  try {
    const result = await api('/api/capture', { method: 'POST', body: JSON.stringify({ text }) });
    $('input').value = '';
    autosize($('input'));
    await load(state.day, true);
    if (result.intent === 'question' && result.reply) {
      toast('我把它当成问题回答了。');
    } else if (result.error) {
      toast(`已经记下了，但回答失败：${result.error}`, 5000);
    } else {
      toast('记下了。');
    }
  } catch (err) {
    if (!(err instanceof AuthError)) toast(err.message);
  } finally {
    setBusy(false);
  }
}

async function askIt() {
  const text = $('input').value.trim();
  if (!text) { toast('想问什么？先写一句。'); return; }

  $('input').value = '';
  autosize($('input'));
  // 先把你说的话、和「它在想」都显示出来。
  // 不这么做的话，模型思考的这十几秒里屏幕什么都不动，看起来像卡死。
  pushLocalBubble(text);
  showThinking();
  setBusy(true, '它正在想…');

  try {
    await api('/api/entries', { method: 'POST', body: JSON.stringify({ text }) });
    const result = await api('/api/ask', { method: 'POST', body: JSON.stringify({ text }) });
    hideThinking();
    await load(state.day, true);
    if (!result.reply) toast('它没说什么。');
  } catch (err) {
    hideThinking();
    if (!(err instanceof AuthError)) {
      // 问题已经记下了，只是回答失败 —— 说清楚，别让人以为白写了
      toast(`${err.message}（你的问题已经记下了）`, 4500);
      await load(state.day, true);
    }
  } finally {
    setBusy(false);
  }
}

/* ---------- 图片 ---------- */

function compress(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('读不了这个文件。'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('这个文件不是图片。'));
      img.onload = () => {
        const { width, height } = img;
        const longest = Math.max(width, height);
        const scale = longest > MAX_EDGE ? MAX_EDGE / longest : 1;
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

async function sendImage(file) {
  setBusy(true);
  try {
    const dataUrl = await compress(file);
    const caption = $('input').value.trim();
    await api('/api/entries', {
      method: 'POST',
      body: JSON.stringify({ caption, image: dataUrl }),
    });
    $('input').value = '';
    autosize($('input'));
    await load(state.day, true);
  } catch (err) {
    if (!(err instanceof AuthError)) toast(err.message);
  } finally {
    setBusy(false);
  }
}

/* ---------- 图片放大 ---------- */

function openLightbox(src) {
  const box = $('lightbox');
  $('lightbox-img').src = src;
  box.hidden = false;
}

function closeLightbox() {
  $('lightbox').hidden = true;
  $('lightbox-img').src = '';
}

/* ---------- 输入框自适应 ---------- */

function autosize(el) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 130)}px`;
}

/* ---------- 绑定 ---------- */

$('prev').onclick = () => load(shiftDay(state.day, -1));
$('next').onclick = () => { if (state.day !== state.today) load(shiftDay(state.day, 1)); };
$('backtotoday').onclick = () => { if (state.today) load(state.today); };
$('datepicker').onchange = (e) => {
  if (e.target.value) load(e.target.value);
};
$('save').onclick = remember;
$('askbtn').onclick = askIt;
$('camera').onclick = () => $('file').click();
$('file').onchange = (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = '';
  if (file) sendImage(file);
};
$('input').addEventListener('input', (e) => autosize(e.target));

// 点图片全屏看。用事件委托，因为流是 innerHTML 重建的。
$('stream').addEventListener('click', (e) => {
  const img = e.target.closest('img.zoomable');
  if (img && img.src) openLightbox(img.src);
});

$('lightbox').onclick = closeLightbox;
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('lightbox').hidden) closeLightbox();
});
$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    remember();
  }
});

boot();
