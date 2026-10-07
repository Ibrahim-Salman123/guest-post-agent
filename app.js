/* Guest Posting Agent UI - vanilla ES6, talks to the FastAPI backend with fetch. */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S = {sites: [], camps: [], arts: [], camp: null};

async function api(path, opt = {}) {
  const r = await fetch('/api' + path, {headers: {'Content-Type': 'application/json'}, ...opt,
    body: opt.body ? JSON.stringify(opt.body) : undefined});
  let d = {}; try { d = await r.json(); } catch {}
  if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : (d.detail || []).map(e => (e.loc || []).slice(-1) + ': ' + e.msg).join('; ') || r.statusText);
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

function statusCell(a) {
  const errHtml = a.error
    ? `<br><small class="err-msg" title="${esc(a.error)}">⚠ ${esc(a.error.slice(0, 80))}</small>`
    : '';
  const warnHtml = (!a.error && a.warnings)
    ? `<br><small class="warn-msg" title="${esc(a.warnings)}">⚠ ${esc(a.warnings.split('\n')[0].slice(0, 60))}</small>`
    : '';
  return `<td>${badge(a.status)}${errHtml}${warnHtml}</td>`;
}

// Sidebar navigation
document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.view').forEach(v => v.hidden = v.id !== 'v-' + b.dataset.view);
});

// Sidebar toggle
const toggleSidebar = () => {
  document.querySelector('.side').classList.toggle('collapsed');
  document.body.classList.toggle('sidebar-collapsed');
};
document.getElementById('sideToggle').onclick = toggleSidebar;

async function refresh() {
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
}

function renderAll() {
  const camp = S.camps.find(c => String(c.id) === String(S.camp));
  $('#runBtn').disabled = !camp || camp.running; $('#runBtn').textContent = camp && camp.running ? 'Running…' : 'Run campaign';
  $('#pauseBtn').textContent = camp && camp.status === 'Paused' ? 'Resume' : 'Pause'; $('#pauseBtn').disabled = !camp;

  const rows = S.arts.filter(a => String(a.campaign_id) === String(S.camp)).sort((a, b) => a.article_number - b.article_number);
  $('#progBody').innerHTML = rows.map(a => `<tr><td>${a.article_number}</td><td>${esc(a.website)}</td><td>${esc(a.keyword)}</td>
   <td>${link(a.target_url, 'Link')}</td>${statusCell(a)}
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

  document.querySelectorAll('#pairs select').forEach(sel => { const v = sel.value;
    sel.innerHTML = '<option value="">Auto-assign</option>' + S.sites.map(s => `<option value="${s.id}">${esc(s.name)} (${s.remaining} left)</option>`).join(''); sel.value = v; });
}

$('#campSel').onchange = e => { S.camp = e.target.value; renderAll(); };
$('#runBtn').onclick = guard(async () => { const r = await api(`/campaigns/${S.camp}/execute`, {method: 'POST'}); toast(`Started ${r.started} article(s)`, 'ok'); await refresh(); });
$('#pauseBtn').onclick = guard(async () => { await api(`/campaigns/${S.camp}/toggle`, {method: 'POST'}); await refresh(); });

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
        if (confirm(`"${siteName}" has articles linked to it.\n\nDeleting will also remove:\n• All articles under this website\n• All campaign references\n\nAre you sure you want to delete?`)) {
          await api('/websites/' + wid + '?force=true', {method: 'DELETE'});
          toast('Website and articles deleted', 'ok');
          refresh();
        }
      } else {
        throw err;
      }
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

function addPair() {
  const d = document.createElement('div'); d.className = 'prow';
  d.innerHTML = `<input placeholder="Keyword" class="pk"><input placeholder="https://target-url.com/page" class="pu" type="url"><select class="pw"></select><button type="button" class="btn ghost sm">Remove</button>`;
  d.querySelector('button').onclick = () => d.remove(); $('#pairs').appendChild(d); renderAll();
}
$('#addPair').onclick = addPair; addPair();

$('#saveCamp').onclick = guard(async () => {
  const pairs = [...document.querySelectorAll('.prow')].map(r => ({keyword: r.querySelector('.pk').value.trim(), target_url: r.querySelector('.pu').value.trim(), website_id: r.querySelector('.pw').value ? +r.querySelector('.pw').value : null})).filter(p => p.keyword || p.target_url);
  const ids = [...document.querySelectorAll('#sitePool input:checked')].map(i => +i.value);
  const r = await api('/campaigns', {method: 'POST', body: {name: $('#cName').value.trim(), pairs, website_ids: ids.length ? ids : null}});
  r.alerts.forEach(a => toast('Skipped: ' + a, 'err'));
  toast(`Campaign created with ${r.created} article(s)`, 'ok');
  S.camp = String(r.campaign_id); $('#cName').value = ''; $('#pairs').innerHTML = ''; addPair();
  await refresh(); document.querySelector('[data-view=dashboard]').click();
});

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
  cur = await api('/articles/' + id);
  $('#mTitle').textContent = cur.title || cur.keyword;
  $('#mMeta').textContent = `${cur.website} | Keyword: ${cur.keyword} | Target: ${cur.target_url} | SEO ${cur.seo_score ?? '-'} | Meta: ${cur.meta_description || '-'}`;
  if (cur.image_alt) $('#mMeta').textContent += ` | Image alt: ${cur.image_alt}`;

  const warnEl = $('#mWarn');
  const msgs = [];
  if (cur.error) msgs.push('❌ ' + cur.error);
  if (cur.warnings) msgs.push('⚠ ' + cur.warnings);
  if (msgs.length) {
    warnEl.hidden = false;
    warnEl.textContent = msgs.join('\n');
    warnEl.className = cur.error ? 'warn warn-err' : 'warn';
  } else {
    warnEl.hidden = true;
    warnEl.textContent = '';
    warnEl.className = 'warn';
  }

  const content = cur.content || '<p style="color:#999;font-style:italic">No content available.</p>';
  const preview = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
           padding:20px;line-height:1.7;color:#0F172A;max-width:800px;margin:0 auto}
      h2{color:#0EA5E9;margin-top:28px;margin-bottom:12px;font-size:1.35em}
      h3{color:#0EA5E9;margin-top:20px;margin-bottom:8px;font-size:1.1em}
      p{margin:12px 0}
      a{color:#0EA5E9}
      strong{color:#0F172A}
      ul,ol{margin:12px 0;padding-left:24px}
      li{margin:6px 0}
      img{max-width:100%;height:auto;border-radius:8px;margin:12px 0}
    </style></head><body>${content}</body></html>`;
  setIframeContent(preview);

  if (cur.draft_url) {
    $('#mDraft').href = cur.draft_url;
    $('#mDraft').style.display = '';
    $('#mDraft').textContent = 'Open draft in website';
  } else {
    $('#mDraft').style.display = 'none';
  }

  const status = cur.status;
  const canApprove = ['Draft Created', 'Waiting for Approval'].includes(status);
  const canReject  = ['Draft Created', 'Waiting for Approval', 'Failed', 'Rejected'].includes(status);
  const canDelete  = status !== 'Published';

  // Show/hide Approve button + Live URL field based on status
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

// ✅ Approve & Publish — auto-publish on real website, optional manual URL
$('#mApprove').onclick = guard(async () => {
  const manualUrl = $('#mLive').value.trim();
  if (!confirm(manualUrl
      ? `Publish this article now? Live URL will be set to: ${manualUrl}`
      : 'Publish this article on your real website now? (Live URL will be auto-detected)')) return;

  toast('Publishing... please wait', 'ok');
  try {
    const body = manualUrl ? {live_url: manualUrl} : {};
    const r = await api(`/articles/${cur.id}/approve`, {method: 'POST', body});
    $('#modal').hidden = true;
    toast(r.live_url ? 'Published successfully! Live URL saved.' : 'Published successfully!', 'ok');
    refresh();
  } catch (e) {
    toast(e.message, 'err');
  }
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

refresh().catch(e => toast(e.message, 'err'));
setInterval(() => { if (document.hidden || !$('#modal').hidden) return; refresh().catch(() => {}); }, 3000);

// Login screen
const hideLogin = () => document.getElementById('loginScreen').classList.add('hidden');
document.getElementById('loginForm').addEventListener('submit', e => {
  e.preventDefault();
  hideLogin();
});
document.getElementById('googleSignIn').addEventListener('click', hideLogin);
