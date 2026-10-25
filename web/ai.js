/* CROM — the assistant panel (Claude). Talks to /api/ai/* in app.py and uses window.App from app.js.
   The key is never shown or logged here: it goes to the server once, which stores it in _settings.json. */
(function () {
'use strict';
const A = window.App;
if (!A) return;
const { $, $$, el, toast, t, tp } = A;
const LS = 'crom.ai.v1';
const POLL_MS = 700;
const KIND = { prompt: 'you', info: 'info', text: 'Claude', tool: 'tool', error: 'error' };

const st = {
  status: null, session: null, view: null, since: 0, timer: null, running: false,
  autoOpened: false, applied: null, lastStatusAt: 0,
  prefs: { allow: false, auto: true, cont: true, session: null },
};

function loadPrefs() { try { Object.assign(st.prefs, JSON.parse(localStorage.getItem(LS) || '{}')); } catch (e) { /* no storage */ } }
function savePrefs() { try { localStorage.setItem(LS, JSON.stringify(st.prefs)); } catch (e) { /* no storage */ } }
const pill = (text, kind) => el('span', { class: 'pill' + (kind ? ' ' + kind : '') }, text);
const fmtUsd = (v) => (v == null ? '' : (+v).toFixed(4) + ' USD');

// ------------------------------------------------------------------ status and settings
async function loadStatus() {
  try {
    st.status = await A.api.get('/api/ai/status');
    st.lastStatusAt = Date.now();
  } catch (e) {
    st.status = null;
    $('#ai-status').textContent = t('Could not get the assistant status: ') + e.message;
    $('#ai-send').disabled = true;
    return null;
  }
  renderStatus(st.status);
  const s = st.status;
  if (s.running && s.running !== st.session) attach(s.running);
  else if (s.running && !st.running) startPoll();
  return s;
}

function renderStatus(s) {
  const box = $('#ai-status'); box.innerHTML = '';
  box.append(s.sdk ? pill(t('anthropic library') + ' ' + (s.sdk_version || ''), 'ok') : pill(t('anthropic library missing'), 'err'));
  box.append(s.key ? pill(t('key: ') + t(s.key_source || 'set'), 'ok') : pill(t('no key'), 'warn'));
  const m = (s.models || []).find((x) => x.id === s.model);
  box.append(pill((m ? m.label : s.model) + ' · ' + s.effort));
  if (s.busy) box.append(pill(t('working'), 'warn'));
  box.append(el('span', { class: 'hint', style: { marginLeft: 'auto' } }, t('limits: {steps} steps · {images} img. · {minutes} min', { steps: s.limits.iterations, images: s.limits.images, minutes: s.limits.minutes })));

  const hint = $('#ai-sdk-hint'); hint.innerHTML = '';
  if (!s.sdk) {
    hint.append(t('The anthropic library is not installed. This command installs it (it downloads the package into the app folder; restart the app afterwards):'));
    hint.append(el('code', { style: { display: 'block', userSelect: 'all', margin: '4px 0', wordBreak: 'break-all', color: 'var(--fg)' } }, s.sdk_hint || ''));
    if (s.sdk_error) hint.append(el('div', {}, t('Import error: ') + s.sdk_error));
    hint.classList.remove('hidden');
  } else hint.classList.add('hidden');

  const ms = $('#ai-model'); ms.innerHTML = '';
  for (const md of s.models || []) ms.append(el('option', { value: md.id, selected: md.id === s.model }, md.label));
  const es = $('#ai-effort'); es.innerHTML = '';
  for (const ef of s.efforts || []) es.append(el('option', { value: ef, selected: ef === s.effort }, ef));
  $('#ai-model-note').textContent = m && m.note ? `${m.label}: ${t(m.note)}. ${t('Every request is billed to your Anthropic account.')}` : '';

  const ready = s.sdk && s.key;
  $('#ai-send').disabled = !ready || s.busy;
  $('#ai-send').title = !s.sdk ? t('Install the anthropic library first (the command is in the settings)')
    : !s.key ? t('Enter an API key in the settings first') : s.busy ? t('The assistant is still working') : 'Ctrl+Enter';
  if (!ready && !st.autoOpened) { st.autoOpened = true; showSettings(true); }
}

function showSettings(on) {
  const box = $('#ai-settings');
  const show = on == null ? box.classList.contains('hidden') : !!on;
  box.classList.toggle('hidden', !show);
  $('#ai-settings-toggle').classList.toggle('on', show);
  if (show && !$('#ai-key').value && st.status && !st.status.key) $('#ai-key').focus();
}

async function postSettings(body, okText) {
  try {
    st.status = await A.api.post('/api/ai/settings', body);
    renderStatus(st.status);
    if (okText) toast(okText, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}

async function saveKey() {
  const inp = $('#ai-key');
  const k = inp.value;
  if (!k.trim()) { toast(t('Paste the API key into the field.'), 'warn'); inp.focus(); return; }
  if (await postSettings({ key: k }, t('Key saved to _settings.json (on this computer only).'))) inp.value = '';
}

async function testKey() {
  const b = $('#ai-key-test'); b.disabled = true;
  try {
    const r = await A.api.post('/api/ai/test', {});
    if (r.ok) toast(t('The key works (source: {src}).', { src: t(r.key_source || '?') + (r.sample_model ? ', model ' + r.sample_model : '') }), 'ok');
    else toast(t('The key does not work: ') + t(r.error || 'unknown error'), 'err', { ms: 12000 });
  } catch (e) { toast(e.message, 'err', { ms: 12000 }); }
  finally { b.disabled = false; }
}

async function clearKey() {
  if (!await A.confirmModal(t('Delete key'), t('The stored API key is removed from _settings.json. A key in the ANTHROPIC_API_KEY environment variable is not affected.'), t('Delete'), true)) return;
  await postSettings({ clear_key: true }, t('Key deleted.'));
}

// ------------------------------------------------------------------ conversation
function specLabel(module, key) {
  const m = A.S.mod[module];
  const sp = m && m.specs ? m.specs[key] : null;
  return sp ? t(sp.label) : key;
}
function fmtVal(module, key, v) {
  const m = A.S.mod[module];
  const sp = m && m.specs ? m.specs[key] : null;
  if (v == null) return '—';
  if (sp && sp.type === 'select') { const o = (sp.options || []).find((x) => String(x[0]) === String(v)); return o ? t(o[1]) : String(v); }
  if (sp && sp.type === 'color') return el('span', {}, el('i', { style: { display: 'inline-block', width: '9px', height: '9px', borderRadius: '2px', background: String(v), marginRight: '4px', verticalAlign: '-1px' } }), String(v));
  if (sp && sp.type === 'textarea') return JSON.stringify(String(v));
  if (typeof v === 'number') return String(Math.round(v * 1000) / 1000) + (sp && sp.unit ? ' ' + t(sp.unit) : '');
  return String(v);
}

function stepNode(s) {
  const n = el('div', { class: 'step ' + s.kind + (s.kind === 'tool' && s.error ? ' err' : ''), dataset: { i: s.i } });
  n.append(el('span', { class: 'k', dataset: { kind: s.kind } }, t(KIND[s.kind] || s.kind)));
  const b = el('div', { class: 'b' });
  if (s.kind === 'tool') {
    b.append(el('b', {}, s.text));
    if (s.args && s.args !== '{}') b.append(el('code', {}, s.args));
    if (s.result) b.append(el('div', { class: 'res' }, t(s.result)));
    if (s.thumb) {
      const src = 'data:image/jpeg;base64,' + s.thumb;
      b.append(el('img', { class: 'thumb', src, alt: s.text, onclick: () => A.showImageSrc(src, s.text) }));
    }
  } else b.append(s.kind === 'text' || s.kind === 'prompt' ? (s.text || '') : t(s.text || ''));
  n.append(b);
  if (s.at != null) n.append(el('span', { class: 'at' }, A.fmtS(s.at, 1)));
  return n;
}

function appendSteps(steps) {
  const log = $('#ai-log');
  const live = $('#ai-live', log);
  for (const s of steps) {
    if (s.i < st.since) continue;
    if (live) log.insertBefore(stepNode(s), live); else log.append(stepNode(s));
  }
}

function renderLive(text) {
  const log = $('#ai-log');
  let live = $('#ai-live', log);
  if (!text) { if (live) live.remove(); return; }
  if (!live) { live = el('div', { class: 'step text live', id: 'ai-live' }, el('span', { class: 'k', dataset: { kind: 'text' } }, 'Claude'), el('div', { class: 'b' })); log.append(live); }
  $('.b', live).textContent = text;
}

function renderMeta(v) {
  const parts = [];
  if (v.n_steps != null) parts.push(tp(v.n_steps, '{n} step', '{n} steps'));
  if (v.images) parts.push(t('{n} img.', { n: v.images }));
  if (v.sec != null) parts.push(A.fmtS(v.sec, 0));
  if (v.cost_usd != null) parts.push(fmtUsd(v.cost_usd));
  if (v.model) parts.push((v.model.split('-')[1] || v.model) + ' · ' + v.effort);
  $('#ai-meta').textContent = parts.join(' · ');
}

function setRunning(on) {
  st.running = on;
  $('#ai-cancel').classList.toggle('hidden', !on);
  $('#ai-send').disabled = on || !(st.status && st.status.sdk && st.status.key);
  $('#ai-new').disabled = on;
  $('#ai-prompt').disabled = false;
}

function ingest(v) {
  st.view = v;
  $('#ai-empty').classList.add('hidden');
  if (v.steps && v.steps.length) appendSteps(v.steps);
  st.since = Math.max(st.since, v.n_steps || 0);
  renderLive(v.status === 'running' ? v.live : '');
  renderMeta(v);
  const log = $('#ai-log');
  log.scrollTop = log.scrollHeight;
  if (v.status === 'queued' || v.status === 'running') { if (!st.running) setRunning(true); }
  else finish(v);
}

async function poll() {
  st.timer = null;
  if (!st.session || !st.running) return;
  try {
    const v = await A.api.get(`/api/ai/job?id=${encodeURIComponent(st.session)}&since=${st.since}`);
    if (v.id !== st.session) return;
    ingest(v);
  } catch (e) {
    toast(t('The connection to the assistant failed: ') + e.message, 'err');
    setRunning(false);
    return;
  }
  if (st.running) st.timer = setTimeout(poll, POLL_MS);
}

function startPoll() { setRunning(true); clearTimeout(st.timer); st.timer = setTimeout(poll, 50); }

function attach(id) {
  // a run the server knows (after a reload, or started from another tab)
  st.session = id; st.since = 0; st.prefs.session = id; savePrefs();
  $('#ai-log').innerHTML = ''; $('#ai-result').classList.add('hidden');
  startPoll();
}

async function restoreSession(id) {
  try {
    const v = await A.api.get(`/api/ai/job?id=${encodeURIComponent(id)}&since=0`);
    st.session = v.id; st.since = 0;
    $('#ai-log').innerHTML = '';
    st.applied = v.id + ':' + v.n_steps; // an old result is not re-applied on reload
    ingest(v);
  } catch (e) { st.prefs.session = null; savePrefs(); }
}

function renderResult(v) {
  const txt = $('#ai-text'); const ch = $('#ai-changes');
  txt.innerHTML = ''; ch.innerHTML = '';
  const keys = Object.keys(v.changed || {});
  const hasChanges = keys.length > 0 || v.module_changed;
  if (v.status === 'error') txt.append(el('span', { style: { color: 'var(--err)' } }, t('Error: ') + t(v.error || 'unknown')));
  else if (v.status === 'cancelled') txt.append(el('span', { style: { color: 'var(--fg3)' } }, t('Cancelled.') + (v.text ? ' ' + v.text : '')));
  else txt.append(v.text || (hasChanges ? t('Done.') : t('Done, no parameter changes.')));
  if (v.module_changed) ch.append(el('span', {}, t('module')), el('span', {}, el('span', { class: 'new' }, A.moduleLabel(v.module) + ' ' + t('(every parameter of the module)'))));
  for (const k of keys) {
    const [o, n] = v.changed[k];
    ch.append(el('span', { title: k }, specLabel(v.module, k)),
      el('span', {}, el('span', { class: 'old' }, fmtVal(v.module, k, o)), el('span', { class: 'new' }, fmtVal(v.module, k, n))));
  }
  if (st.applied === v.id + ':' + v.n_steps && st.appliedNote) ch.append(el('span', {}, ''), el('span', { style: { color: 'var(--ok)' } }, t(st.appliedNote)));
  return hasChanges;
}

function finish(v) {
  const was = st.running;
  setRunning(false);
  renderLive('');
  const box = $('#ai-result');
  st.appliedNote = null;
  const hasChanges = renderResult(v);
  const applyable = hasChanges && v.status !== 'error';
  const tag = v.id + ':' + v.n_steps;
  const auto = applyable && st.prefs.auto && was && st.applied !== tag;
  $('#ai-apply').classList.toggle('hidden', !applyable || auto);
  $('#ai-discard').classList.toggle('hidden', !applyable || auto);
  box.classList.remove('hidden');
  if (auto) applyResult(v, true);
  if (was && v.status !== 'running') {
    const kind = v.status === 'done' ? 'ok' : v.status === 'error' ? 'err' : 'warn';
    const msg = v.status === 'done' ? t('The assistant finished.') : v.status === 'error' ? t('The assistant failed: ') + t(v.error || '') : t('The assistant was cancelled.');
    if (A.S.tab !== 'ai') toast(msg, kind, { action: { label: t('Show'), fn: () => A.showTab('ai') } });
  }
  loadStatus();
}

async function applyResult(v, auto) {
  if (!v) return;
  try {
    const n = await A.applyParams(v.module, v.params);
    st.applied = v.id + ':' + v.n_steps;
    $('#ai-apply').classList.add('hidden'); $('#ai-discard').classList.add('hidden');
    st.appliedNote = auto ? 'applied automatically · Ctrl+Z undoes it' : 'applied · Ctrl+Z undoes it';
    const note = $('#ai-changes');
    note.append(el('span', {}, ''), el('span', { style: { color: 'var(--ok)' } }, t(st.appliedNote)));
    toast(t('Changes applied ({what}). Ctrl+Z undoes them.', { what: tp(n, '{n} parameter', '{n} parameters') }), 'ok');
  } catch (e) { toast(t('Could not apply the changes: ') + e.message, 'err'); }
}

async function send() {
  const inp = $('#ai-prompt');
  const prompt = inp.value.trim();
  if (!prompt) { toast(t('Write what the assistant should do.'), 'warn'); inp.focus(); return; }
  if (st.running) { toast(t('The assistant is still working.'), 'warn'); return; }
  const body = {
    prompt, module: A.S.module, params: A.P(), allow_render: !!st.prefs.allow, lang: window.I18N.lang(),
    session: st.prefs.cont ? st.session : null,
    model: $('#ai-model').value || undefined, effort: $('#ai-effort').value || undefined,
  };
  $('#ai-send').disabled = true;
  let v;
  try { v = await A.api.post('/api/ai/chat', body); }
  catch (e) { toast(e.message, 'err', { ms: 12000 }); $('#ai-send').disabled = false; loadStatus(); return; }
  if (v.id !== st.session) { st.session = v.id; st.since = 0; $('#ai-log').innerHTML = ''; }
  st.prefs.session = st.session; savePrefs();
  $('#ai-result').classList.add('hidden');
  inp.value = '';
  setRunning(true);
  ingest(v);
  if (st.running) { clearTimeout(st.timer); st.timer = setTimeout(poll, POLL_MS); }
}

async function cancel() {
  if (!st.session) return;
  try { const v = await A.api.post('/api/ai/cancel', { id: st.session }); toast(t('Cancelling the assistant…'), 'warn'); if (v && v.status && v.status !== 'running' && v.status !== 'queued') ingest(v); }
  catch (e) { toast(e.message, 'err'); }
}

function newConversation() {
  if (st.running) { toast(t('The assistant is still working. Cancel it first.'), 'warn'); return; }
  st.session = null; st.since = 0; st.view = null; st.prefs.session = null; savePrefs();
  $('#ai-log').innerHTML = '';
  $('#ai-result').classList.add('hidden');
  $('#ai-empty').classList.remove('hidden');
  $('#ai-meta').textContent = '';
  $('#ai-prompt').focus();
}

// ------------------------------------------------------------------ init
function bind() {
  $('#ai-settings-toggle').addEventListener('click', () => showSettings());
  $('#ai-key-save').addEventListener('click', saveKey);
  $('#ai-key').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveKey(); } });
  $('#ai-key-test').addEventListener('click', testKey);
  $('#ai-key-clear').addEventListener('click', clearKey);
  $('#ai-model').addEventListener('change', (e) => postSettings({ model: e.target.value }, t('Model saved.')));
  $('#ai-effort').addEventListener('change', (e) => postSettings({ effort: e.target.value }, t('Effort saved.')));
  for (const [id, key] of [['#ai-allow-render', 'allow'], ['#ai-auto-apply', 'auto'], ['#ai-continue', 'cont']]) {
    const c = $(id); c.checked = !!st.prefs[key];
    c.addEventListener('change', () => { st.prefs[key] = c.checked; savePrefs(); });
  }
  $('#ai-send').addEventListener('click', send);
  $('#ai-prompt').addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); send(); } });
  $('#ai-cancel').addEventListener('click', cancel);
  $('#ai-new').addEventListener('click', newConversation);
  $('#ai-apply').addEventListener('click', () => applyResult(st.view, false));
  $('#ai-discard').addEventListener('click', () => { $('#ai-apply').classList.add('hidden'); $('#ai-discard').classList.add('hidden'); toast(t('Suggestion discarded, the parameters stay as they were.'), 'info'); });
}

async function init() {
  loadPrefs();
  bind();
  const s = await loadStatus();
  if (s && !s.running && st.prefs.session) await restoreSession(st.prefs.session);
}

function onShow() {
  if (Date.now() - st.lastStatusAt > 8000) loadStatus();
}

function onLang() {
  if (st.status) renderStatus(st.status);
  for (const k of $$('#ai-log .step .k')) { const kind = k.dataset.kind; if (kind) k.textContent = t(KIND[kind] || kind); }
  if (st.view) renderMeta(st.view);
  if (st.view && !$('#ai-result').classList.contains('hidden')) renderResult(st.view);
}

window.AI = { init, onShow, onLang, state: st };
A.ready.then(init);
})();
