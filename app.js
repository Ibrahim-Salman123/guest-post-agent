/* Guest Posting Agent UI - vanilla ES6, talks to the FastAPI backend with fetch. */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S = {sites: [], camps: [], arts: [], camp: null};

async function api(path, opt = {}) {
  const r = await fetch('/api' + path, {headers: {'Content-Type': 'application/json'}, ...opt,
    body: opt.body ? JSON.stringify(opt.body) : undefined});
  let d = {}; try { d = await r.json(); } catch {}
  if (!r.ok) {
    // FRIENDLY ERROR: never throw raw backend error to UI
    let msg = 'Something went wrong. Please try again.';
    if (typeof d.detail === 'string') {
      // Map technical errors to friendly messages
      if (d.detail.includes('401') || d.detail.toLowerCase().includes('not allowed')) {
        msg = 'Website permission needed. Please verify the site has admin access.';
      } else if (d.detail.includes('429') || d.detail.toLowerCase().includes('rate')) {
        msg = 'High traffic. Please wait a moment and retry.';
      } else if (d.detail.toLowerCase().includes('url')) {
        msg = 'Please check the URL and try again.';
      } else {
        msg = d.detail;
      }
    }
    throw new Error(msg);
  }
  return d;
}
function toast(msg, type = '') {
  const t = document.createElement('div'); t.className = 'toast ' + type; t.textContent = msg;
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), type === 'err' ? 9000 : 4500);
}
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, 'err'); } };
const badge = s => `<span class="badge b-${esc(s.split(' ')[0])}">${esc(s === 'Waiting for Approval' ? 'Waiting Approval' : s)}</span>`;
const link = (u, t) => u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>` : '-';

function actionCell(a) {
  const retryable = ['Failed', 'Rejected'].includes(a.status);
  const viewable  = ['Draft Created', 'Waiting for Approval', 'Published', 'Failed', 'Rejected'].includes(a.status);
  const btns = [];
  if (retryable) btns.push(`<button class="btn sm" data-retry="${a.id}">Retry</button>`);
  if (viewable)  btns.push(`<button class="btn sm ghost" data-open="${a.id}">${a.status === 'Failed' || a.status === 'Rejected' ? 'View' : 'Review'}</button>`);
  return `<td>${btns.join(' ')}</td>`;
}

// SOFT STATUS CELL — no scary red errors, only gentle notes
function statusCell(a) {
  const errHtml = a.error
    ? `<br><small class="soft-note" title="${esc(a.error)}">• ${esc(shortMsg(a.error))}</small>`
    : '';
  const warnHtml = (!a.error && a.warnings)
    ? `<br><small class="soft-note" title="${esc(a.warnings)}">• ${esc(shortMsg(a.warnings))}</small>`
    : '';
  const tag = a.is_custom ? ' <small style="color:#0EA5E9">[custom]</small>' : '';
  return `<td>${badge(a.status)}${tag}${errHtml}${warnHtml}</td>`;
}

function shortMsg(msg) {
  if (!msg) return '';
  msg = String(msg);
  if (msg.length <= 50) return msg;
  return msg.slice(0, 47) + '…';
}

document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.view').forEach(v => v.hidden = v.id !== 'v-' + b.dataset.view);
});

const toggleSidebar = () => {
  document.querySelector('.side').classList.toggle('collapsed');
  document.body.classList.toggle('sidebar-collapsed');
};
document.getElementById('sideToggle').onclick = toggleSidebar;

async function refresh() {
  try {
    const [d, sites, camps] = await Promise.all([api('/dashboard'), api('/websites'), api('/campaigns')]);
    S.sites = sites; S.camps = camps;
    $('#m_active').textContent = d.active_campaigns; $('#m_sites').textContent = d.total_sites;
    $('#m_pending').textContent = d.pending_approval; $('#m_pub').textContent = d.published;
    $('#qCount').textContent = d.pending_approval;
    const sel = $('#campSel'), cur = S.camp || sel.value;
    sel.innerHTML = camps.map(c => `<option value="${c.id}">${esc(c.name)} (${c.published}/${c.total})</option>`).join('') || '<option value="">No campaigns yet</option>';
    if (camps.some(c => String(c.id) === String(cur))) sel.value = cur;
    S.camp = sel.value || null;
    S.arts = await api('/articles');
    renderAll();
  } catch (e) { /* silent, no scary errors */ }
}

function renderAll() {
  const camp = S.camps.find(c => String(c.id) === String(S.camp));
  $('#runBtn').disabled = !camp || camp.running; $('#runBtn').textContent = camp && camp.running ? 'Running…' : 'Run campaign';
  $('#pauseBtn').textContent = camp && camp.status === 'Paused' ? 'Resume' : 'Pause'; $('#pauseBtn').disabled = !camp;
  $('#approveAllBtn').disabled = !camp;

  const rows = S.arts.filter(a => String(a.campaign_id) === String(S.camp)).sort((a, b) => a.article_number - b.article_number);
  $('#progBody').innerHTML = rows.map(a => `<tr><td>${a.article_number}</td><td>${esc(a.website)}</td><td>${esc(a.keyword)}</td>
   <td>${link(a.target_url, a.target_url ? 'Link' : '-')}</td>${statusCell(a)}
   <td>${a.seo_score ?? '-'}</td><td>${link(a.draft_url, 'Draft')}</td><td>${link(a.live_url, 'Live')}</td><td>${(a.created_at || '').slice(0, 10)}</td>
   ${actionCell(a)}</tr>`).join('') || '<tr><td colspan="10">Create a campaign to see progress here.</td></tr>';

  $('#siteBody').innerHTML = S.sites.map(s => `<tr><td>${esc(s.name)}</td><td>${link(s.url, esc(s.url))}</td><td>${esc(s.niche)}</td><td>${s.article_limit}</td><td>${s.daily_limit || 0}</td><td>${s.published_count}</td><td>${s.remaining}</td>
   <td><button class="btn sm ghost" data-edit="${s.id}">Edit</button> <button class="btn sm ghost" data-del="${s.id}">Delete</button></td></tr>`).join('') || '<tr><td colspan="8">Add your first guest posting website above.</td></tr>';

  const q = S.arts.filter(a => ['Draft Created', 'Waiting for Approval'].includes(a.status));
  $('#qBody').innerHTML = q.map(a => `<tr><td>${esc(a.website)}</td><td>${esc(a.keyword)}</td><td>${esc(a.title || '-')}</td><td>${a.seo_score ?? '-'}</td><td>${a.words ?? '-'}</td><td><button class="btn sm ghost" data-open="${a.id}">Review</button></td></tr>`).join('') || '<tr><td colspan="6">No drafts waiting. Run a campaign to generate some.</td></tr>';

  $('#anCamp').innerHTML = S.camps.map(c => { const p = c.total ? Math.round(c.published / c.total * 100) : 0;
    const canDelete = c.published === 0;
    return `<tr><td>${esc(c.name)}</td><td>${badge(c.status)}</td><td><div class="prog"><i style="width:${p}%"></i></div></td><td>${c.published} / ${c.total}</td>
    <td>${canDelete ? `<button class="btn sm ghost" data-delcamp="${c.id}">Delete</button>` : '<small style="color:#94A3B8">Published</small>'}</td></tr>`; }).join('') || '<tr><td colspan="5">No campaigns yet.</td></tr>';

  $('#anSites').innerHTML = S.sites.map(s => { const used = s.article_limit - s.remaining;
    return `<div class="cap"><div><b>${esc(s.name)}</b><span>${used} assigned of ${s.article_limit} (${s.published_count} published)</span></div><div class="prog"><i style="width:${Math.round(used / s.article_limit * 100)}%"></i></div></div>`; }).join('') || '<p class="hint">No websites yet.</p>';

  const pool = $('#sitePool');
  const hash = S.sites.map(s => `${s.id}:${s.remaining}`).join('|');
  if (pool.dataset.hash !== hash) { pool.dataset.hash = hash;
    pool.innerHTML = S.sites.map(s => `<label><input type="checkbox" value="${s.id}">${esc(s.name)} (${s.remaining} left)</label>`).join('') || '<span class="hint">Add websites first.</span>'; }

  document.querySelectorAll('#pairs select.pw').forEach(sel => {
    const v = sel.value;
    sel.innerHTML = '<option value="">Auto-assign</option>' + S.sites.map(s => `<option value="${s.id}">${esc(s.name)} (${s.remaining} left)</option>`).join('');
    sel.value = v;
  });

  const caSite = $('#caSite');
  if (caSite) {
    const cur = caSite.value;
    caSite.innerHTML = '<option value="">— pick a website —</option>' + S.sites.map(s => `<option value="${s.id}">${esc(s.name)} (${s.remaining} left)</option>`).join('');
    if (cur) caSite.value = cur;
  }
}

$('#campSel').onchange = e => { S.camp = e.target.value; renderAll(); };
$('#runBtn').onclick = guard(async () => { const r = await api(`/campaigns/${S.camp}/execute`, {method: 'POST'}); toast(`Started ${r.started} article(s)`, 'ok'); await refresh(); });
$('#pauseBtn').onclick = guard(async () => { await api(`/campaigns/${S.camp}/toggle`, {method: 'POST'}); await refresh(); });
$('#approveAllBtn').onclick = guard(async () => {
  if (!S.camp) return;
  if (!confirm('Approve & publish ALL drafts in this campaign?')) return;
  toast('Publishing all drafts... please wait', 'ok');
  const r = await api(`/campaigns/${S.camp}/approve-all`, {method: 'POST'});
  toast(`Published ${r.published}, failed ${r.failed}`, r.failed ? 'err' : 'ok');
  refresh();
});

document.body.addEventListener('click', guard(async e => {
  const t = e.target;
  if (t.dataset.retry) { await api(`/articles/${t.dataset.retry}/retry`, {method: 'POST'}); toast('Retry started', 'ok'); refresh(); }
  if (t.dataset.open) openModal(t.dataset.open);

  if (t.dataset.edit) {
    const s = S.sites.find(x => x.id == t.dataset.edit), f = $('#siteForm');
    Object.keys(s).forEach(k => { if (f.elements[k] && k !== 'password') f.elements[k].value = s[k] ?? ''; });
    f.elements.password.value = '';
    $('#wfTitle').textContent = 'Edit ' + s.name;
    document.querySelector('[data-view=websites]').click();
    scrollTo(0, 0);
  }

  if (t.dataset.delcamp) {
    if (confirm('Delete this campaign and all its articles?')) {
      await api('/campaigns/' + t.dataset.delcamp, {method: 'DELETE'});
      toast('Campaign deleted', 'ok');
      refresh();
    }
  }

  if (t.dataset.del) {
    const wid = t.dataset.del;
    const site = S.sites.find(s => String(s.id) === String(wid));
    const siteName = site ? site.name : 'this website';
    try {
      await api('/websites/' + wid, {method: 'DELETE'});
      toast('Website deleted', 'ok');
      refresh();
    } catch (err) {
      if (err.message && err.message.toLowerCase().includes('article')) {
        if (confirm(`"${siteName}" has articles linked to it. Delete anyway?`)) {
          await api('/websites/' + wid + '?force=true', {method: 'DELETE'});
          toast('Website and articles deleted', 'ok');
          refresh();
        }
      } else { throw err; }
    }
  }
}));

const resetSite = () => { $('#siteForm').reset(); $('#siteForm').elements.id.value = ''; $('#wfTitle').textContent = 'Add a website'; };
$('#wfReset').onclick = resetSite;
$('#wfTest').onclick = guard(async () => {
  const f = $('#siteForm');
  const body = f.elements.id.value
    ? {id: +f.elements.id.value}
    : {url: f.elements.url.value.trim(), username: f.elements.username.value.trim(), password: f.elements.password.value};
  if (!body.id && (!body.url || !body.username || !body.password)) throw new Error('Enter URL, username and password first');
  const r = await api('/websites/test-connection', {method: 'POST', body});
  toast(r.message || 'Connection OK', 'ok');
});

$('#siteForm').onsubmit = guard(async e => {
  e.preventDefault(); const o = Object.fromEntries(new FormData(e.target));
  o.id = o.id ? +o.id : null;
  o.article_limit = +o.article_limit;
  o.daily_limit = +o.daily_limit;
  o.min_words = +o.min_words;
  o.max_words = +o.max_words;
  if (!o.password) delete o.password;
  await api('/websites', {method: 'POST', body: o}); toast('Website saved', 'ok'); resetSite(); refresh();
});

// BULK WEBSITES
$('#bulkAdd').onclick = guard(async () => {
  const txt = $('#bulkSites').value.trim();
  if (!txt) throw new Error('Paste CSV data first');
  const lines = txt.split('\n').map(l => l.trim()).filter(Boolean);
  const websites = [];
  for (const line of lines) {
    const parts = line.split(',').map(p => p.trim());
    if (parts.length < 4) continue;
    websites.push({
      name: parts[0], url: parts[1], username: parts[2], password: parts[3],
      niche: parts[4] || '', article_limit: 1000, daily_limit: 50,
      min_words: 900, max_words: 1200, cms_type: 'wordpress',
    });
  }
  if (!websites.length) throw new Error('No valid rows found');
  const r = await api('/websites/bulk', {method: 'POST', body: {websites}});
  toast(`Added ${r.created} websites (${r.failed} skipped)`, r.failed ? '' : 'ok');
  $('#bulkSites').value = '';
  refresh();
});

// BULK KEYWORDS
$('#bulkAddPairs').onclick = () => {
  const txt = $('#bulkPairs').value.trim();
  if (!txt) { toast('Paste CSV first', 'err'); return; }
  const lines = txt.split('\n').map(l => l.trim()).filter(Boolean);
  let count = 0;
  for (const line of lines) {
    const parts = line.split(',').map(p => p.trim());
    const kw = parts[0] || '';
    const url = parts[1] || '';
    const qty = parseInt(parts[2]) || 1;
    if (!kw) continue;
    addPairWithValues(kw, url, qty);
    count++;
  }
  toast(`Added ${count} keyword rows`, 'ok');
  $('#bulkPairs').value = '';
  renderAll();
};

function _pairRowInner(keyword = '', url = '', qty = 1) {
  return `<input placeholder="Keyword" class="pk" value="${esc(keyword)}">
    <input placeholder="Target URL (optional)" class="pu" type="text" value="${esc(url)}">
    <input class="pq" type="number" min="1" max="500" value="${qty}" title="Quantity">
    <select class="pw"></select>
    <select class="plang">
      <option value="">Language (default)</option>
      <option value="English">English</option>
      <option value="Urdu">Urdu</option>
      <option value="Hindi">Hindi</option>
      <option value="Arabic">Arabic</option>
      <option value="Spanish">Spanish</option>
      <option value="French">French</option>
      <option value="German">German</option>
    </select>
    <select class="pimg">
      <option value="">Image (default)</option>
      <option value="nature">Nature</option>
      <option value="technology">Technology</option>
      <option value="business">Business</option>
      <option value="health fitness">Health</option>
      <option value="food">Food</option>
      <option value="travel">Travel</option>
      <option value="sports">Sports</option>
      <option value="fashion">Fashion</option>
    </select>
    <button type="button" class="btn ghost sm">Remove</button>`;
}

function addPair() {
  const d = document.createElement('div'); d.className = 'prow';
  d.innerHTML = _pairRowInner();
  d.querySelector('button').onclick = () => d.remove();
  $('#pairs').appendChild(d); renderAll();
}
function addPairWithValues(keyword, url, qty = 1) {
  const d = document.createElement('div'); d.className = 'prow';
  d.innerHTML = _pairRowInner(keyword, url, qty);
  d.querySelector('button').onclick = () => d.remove();
  $('#pairs').appendChild(d);
}
$('#addPair').onclick = addPair; addPair();

// SAVE CAMPAIGN
$('#saveCamp').onclick = guard(async () => {
  const campLang = ($('#cLang') && $('#cLang').value) || 'English';
  const campImg = ($('#cImgCat') && $('#cImgCat').value) || '';
  const campQty = parseInt($('#cQty') && $('#cQty').value) || 1;
  const pairs = [...document.querySelectorAll('.prow')].map(r => ({
    keyword: r.querySelector('.pk').value.trim(),
    target_url: r.querySelector('.pu').value.trim(),
    quantity: parseInt(r.querySelector('.pq').value) || 1,
    website_id: r.querySelector('.pw').value ? +r.querySelector('.pw').value : null,
    language: r.querySelector('.plang') ? r.querySelector('.plang').value : '',
    image_category: r.querySelector('.pimg') ? r.querySelector('.pimg').value : '',
  })).filter(p => p.keyword);
  if (!pairs.length) throw new Error('Add at least one keyword');
  const ids = [...document.querySelectorAll('#sitePool input:checked')].map(i => +i.value);
  const r = await api('/campaigns', {method: 'POST', body: {
    name: $('#cName').value.trim(),
    pairs,
    website_ids: ids.length ? ids : null,
    language: campLang,
    image_category: campImg,
    quantity: campQty,
  }});
  r.alerts.forEach(a => toast('Skipped: ' + a, ''));
  toast(`Campaign created with ${r.created} article(s)`, 'ok');
  S.camp = String(r.campaign_id);
  $('#cName').value = ''; $('#pairs').innerHTML = ''; addPair();
  await refresh(); document.querySelector('[data-view=dashboard]').click();
});

// BULK CUSTOM ARTICLES
$('#bulkCustomAdd').onclick = guard(async () => {
  const txt = $('#bulkCustom').value.trim();
  if (!txt) throw new Error('Paste JSON data first');
  let arr;
  try { arr = JSON.parse(txt); } catch (e) { throw new Error('Invalid JSON: ' + e.message); }
  if (!Array.isArray(arr) || !arr.length) throw new Error('Expected a non-empty JSON array');

  const sites = [...document.querySelectorAll('#sitePool input:checked')].map(i => +i.value);
  const siteIds = sites.length ? sites : S.sites.map(s => s.id);
  if (!siteIds.length) throw new Error('No websites available. Add websites first.');

  let count = 0, errors = [];
  for (const item of arr) {
    if (!item.keyword || !item.title || !item.content) {
      errors.push('Missing keyword/title/content'); continue;
    }
    try {
      await api('/custom-articles', {method: 'POST', body: {
        website_id: siteIds[count % siteIds.length],
        keyword: item.keyword,
        title: item.title,
        content: item.content,
        target_url: item.target_url || '',
        language: item.language || 'English',
        is_spinning: false,
      }});
      count++;
    } catch (e) { errors.push(item.keyword + ': ' + e.message); }
  }
  toast(`Uploaded ${count} articles${errors.length ? ' (' + errors.length + ' skipped)' : ''}`, errors.length ? '' : 'ok');
  $('#bulkCustom').value = '';
  refresh();
});

// CUSTOM ARTICLE
$('#caSave').onclick = guard(async () => {
  const siteId = parseInt($('#caSite').value);
  const keyword = $('#caKeyword').value.trim();
  const title = $('#caTitle').value.trim();
  const body = $('#caBody').value;
  if (!siteId || !keyword || !title || !body) throw new Error('Website, keyword, title, and body required');
  const payload = {
    website_id: siteId,
    keyword,
    title,
    content: body,
    target_url: $('#caUrl').value.trim(),
    language: $('#caLang').value,
    is_spinning: $('#caSpin').checked,
  };
  const r = await api('/custom-articles', {method: 'POST', body: payload});
  toast('Custom article submitted — publishing soon', 'ok');
  $('#caKeyword').value = ''; $('#caTitle').value = ''; $('#caBody').value = ''; $('#caUrl').value = '';
  $('#caSpin').checked = false;
  refresh();
});

// MODAL
let cur = null;
function setIframeContent(html) {
  const old = document.getElementById('mPrev');
  if (!old) return;
  const fresh = document.createElement('iframe');
  fresh.id = 'mPrev';
  fresh.setAttribute('sandbox', '');
  fresh.setAttribute('title', 'Article preview');
  fresh.srcdoc = html;
  old.parentNode.replaceChild(fresh, old);
}
async function openModal(id) {
  try { cur = await api('/articles/' + id); } catch (e) { toast(e.message, 'err'); return; }
  $('#mTitle').textContent = cur.title || cur.keyword;
  $('#mMeta').textContent = `${cur.website} | Keyword: ${cur.keyword} | Target: ${cur.target_url || '—'} | Language: ${cur.language || 'English'} | Image: ${cur.image_category || 'auto'} | SEO ${cur.seo_score ?? '-'}`;
  if (cur.image_alt) $('#mMeta').textContent += ` | Image alt: ${cur.image_alt}`;
  if (cur.is_custom) $('#mMeta').textContent += ' | [CUSTOM]';
  if (cur.is_spinning) $('#mMeta').textContent += ' [SPINNING]';

  // SOFT WARNINGS — never scary red
  const warnEl = $('#mWarn');
  const msgs = [];
  if (cur.error) msgs.push('• ' + cur.error);
  if (cur.warnings && !cur.error) msgs.push('• ' + cur.warnings);
  if (msgs.length) {
    warnEl.hidden = false; warnEl.textContent = msgs.join('\n');
    warnEl.className = 'warn';   // soft yellow, not red
  } else { warnEl.hidden = true; warnEl.textContent = ''; warnEl.className = 'warn'; }

  const content = cur.content || '<p style="color:#999;font-style:italic">No content available.</p>';
  const preview = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:20px;line-height:1.7;color:#0F172A;max-width:800px;margin:0 auto}
      h2{color:#0EA5E9;margin-top:28px;margin-bottom:12px;font-size:1.35em}
      h3{color:#0EA5E9;margin-top:20px;margin-bottom:8px;font-size:1.1em}
      p{margin:12px 0} a{color:#0EA5E9} ul,ol{margin:12px 0;padding-left:24px}
      img{max-width:100%;height:auto;border-radius:8px;margin:12px 0}
    </style></head><body>${content}</body></html>`;
  setIframeContent(preview);

  if (cur.draft_url) {
    $('#mDraft').href = cur.draft_url; $('#mDraft').style.display = '';
    $('#mDraft').textContent = 'Open draft in website';
  } else { $('#mDraft').style.display = 'none'; }

  const status = cur.status;
  const canApprove = ['Draft Created', 'Waiting for Approval'].includes(status);
  const canReject  = ['Draft Created', 'Waiting for Approval', 'Failed', 'Rejected'].includes(status);
  const canDelete  = status !== 'Published';
  const liveLabel = $('#mLive').closest('label');
  if (liveLabel) liveLabel.hidden = !canApprove;
  $('#mApprove').hidden = !canApprove;
  $('#mReject').hidden  = !canReject;
  $('#mDelete').hidden  = !canDelete;
  $('#mAct').hidden = status === 'Published';
  $('#mLive').value = '';
  $('#modal').hidden = false;
  if (status === 'Published') $('#mMeta').textContent += ` | Live: ${cur.live_url}`;
}

$('#mClose').onclick = () => $('#modal').hidden = true;
$('#mApprove').onclick = guard(async () => {
  const manualUrl = $('#mLive').value.trim();
  if (!confirm(manualUrl
      ? `Publish this article now? Live URL will be set to: ${manualUrl}`
      : 'Publish this article on your real website now?')) return;
  toast('Publishing... please wait', 'ok');
  try {
    const body = manualUrl ? {live_url: manualUrl} : {};
    const r = await api(`/articles/${cur.id}/approve`, {method: 'POST', body});
    $('#modal').hidden = true;
    toast('Published successfully!', 'ok');
    refresh();
  } catch (e) { toast(e.message, 'err'); }
});
$('#mReject').onclick = guard(async () => {
  if (!confirm('Reject this draft and regenerate a fresh version?')) return;
  await api(`/articles/${cur.id}/reject?regenerate=true`, {method: 'POST'});
  $('#modal').hidden = true; toast('Regeneration started', 'ok'); refresh();
});
$('#mDelete').onclick = guard(async () => {
  if (!confirm('Delete this article permanently?')) return;
  await api(`/articles/${cur.id}`, {method: 'DELETE'});
  $('#modal').hidden = true; toast('Article deleted', 'ok'); refresh();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').hidden = true; });

refresh().catch(() => {});
setInterval(() => { if (document.hidden || !$('#modal').hidden) return; refresh().catch(() => {}); }, 3000);

// ---------- LOGIN (STRICTLY LOCKED) ----------
const LOGIN_EMAIL = "ibrahim444salman@gmail.com";
const LOGIN_PASSWORD = "00Ibr@siddiquE";

const hideLogin = () => document.getElementById('loginScreen').classList.add('hidden');

document.getElementById('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target;
  const email = (f.elements.email.value || "").trim().toLowerCase();
  const password = f.elements.password.value || "";

  if (email !== LOGIN_EMAIL || password !== LOGIN_PASSWORD) {
    toast('Access denied. Only the authorized account can use this agent.', 'err');
    return;
  }
  try {
    const r = await api('/login', {method: 'POST', body: {email, password}});
    if (r && r.ok) { hideLogin(); toast('Welcome back!', 'ok'); }
    else { toast('Login failed. Check credentials.', 'err'); }
  } catch (err) {
    toast('Access denied. ' + (err.message || ''), 'err');
  }
});
