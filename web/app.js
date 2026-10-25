/* CROM — editor logic. Vanilla JS, talks to app.py on the same origin.
   Everything the inspector and the timeline show comes from /api/schema (SCHEMA, PRESETS, LANES),
   so a new parameter or lane in engine.py / logo_engine.py appears here without touching this file. */
(function () {
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v);
    else if (k === 'dataset') Object.assign(n.dataset, v);
    else if (v === true) n.setAttribute(k, '');
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat(2)) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
}
function icon(id) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const u = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  u.setAttribute('href', '#i-' + id); s.append(u); return s;
}
const clone = (o) => JSON.parse(JSON.stringify(o));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => { t = null; fn(...a); }, ms); };
  d.cancel = () => { clearTimeout(t); t = null; };
  d.now = (...a) => { clearTimeout(t); t = null; return fn(...a); };
  return d;
}
const pad2 = (n) => String(n).padStart(2, '0');
const trimZeros = (s) => s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s;
function fmtTC(t) { t = Math.max(0, +t || 0); const m = Math.floor(t / 60), s = Math.floor(t % 60), c = Math.floor((t - Math.floor(t)) * 100 + 1e-6); return `${pad2(m)}:${pad2(s)}.${pad2(c)}`; }
const fmtS = (t, d = 2) => trimZeros((+t || 0).toFixed(d)) + ' s';
function decimals(step) { if (!step || step === 'any') return 2; return Math.max(0, Math.ceil(-Math.log10(+step) - 1e-9)); }
const fmtNum = (v, step) => (+v || 0).toFixed(decimals(step));
function fmtBytes(b) {
  if (b == null) return '';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(0) + ' kB';
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
  return (b / 1073741824).toFixed(2) + ' GB';
}
function fmtDate(v) {
  if (!v) return '';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(String(v).replace(' ', 'T'));
  if (isNaN(d)) return String(v);
  return d.toLocaleString('sk-SK', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function fmtEta(sec) { if (sec == null) return ''; sec = Math.max(0, Math.round(sec)); return sec >= 60 ? `${Math.floor(sec / 60)}:${pad2(sec % 60)}` : `${sec} s`; }
const plural = (n, a, b, c) => n === 1 ? a : (n >= 2 && n <= 4 ? b : c);
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
const normRel = (r) => String(r || '').replace(/\\/g, '/');
const encodeRel = (r) => normRel(r).split('/').map(encodeURIComponent).join('/');
const fileUrl = (rel) => '/files/' + encodeURIComponent(S.project || '_') + '/' + encodeRel(rel);
const asciiName = (s) => String(s || 'font.ttf').replace(/[^\x20-\x7e]/g, '_');
function djb2(s) { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; return h.toString(16); }

// ------------------------------------------------------------------ API
const HDR = { 'X-Crom': '1' };
const hdr = (extra) => ({ ...HDR, 'X-Project': S.project || '', ...(extra || {}) });
async function parse(r) {
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await r.json().catch(() => ({})) : await r.text().catch(() => '');
  if (!r.ok) {
    const msg = (data && data.error) || (typeof data === 'string' && data.slice(0, 200)) || ('HTTP ' + r.status);
    throw new Error(msg);
  }
  return data;
}
const api = {
  get: (path) => fetch(path, { cache: 'no-store', headers: hdr() }).then(parse),
  post: (path, body, signal) => fetch(path, { method: 'POST', headers: hdr({ 'Content-Type': 'application/json' }), body: JSON.stringify({ project: S.project, ...(body || {}) }), signal }).then(parse),
  async image(path, body, signal) {
    const r = await fetch(path, { method: 'POST', headers: hdr({ 'Content-Type': 'application/json' }), body: JSON.stringify({ project: S.project, ...(body || {}) }), signal });
    if (!r.ok) await parse(r);
    let info = {};
    try { info = JSON.parse(r.headers.get('X-Info') || '{}'); } catch (e) { info = {}; }
    return { blob: await r.blob(), info };
  },
  upload: (path, file, headers) => fetch(path, { method: 'POST', headers: hdr({ 'Content-Type': 'application/octet-stream', ...(headers || {}) }), body: file }).then(parse),
};

// ------------------------------------------------------------------ state
const LS_KEY = 'crom.ui.v1';
const PHASE_KEYS = { titulky: { in: 't_in', hold: 't_hold', out: 't_out' }, logo: { all: 'dur' } };
const PHASE_META = { titulky: [['in', 'Nástup', 1], ['hold', 'Ustálený', 1], ['out', 'Rozpad', 2]], logo: [['all', 'Intro', 1]] };
const REBUILD_NOTE = {
  layout: 'prepočíta rozloženie textu', shade: 'prepočíta tieňovanie', order: 'prepočíta poradie rozpadu',
  sim: 'spustí novú simuláciu ohňa (asi 50 s, náhľad je dovtedy približný)', grade: 'prepočíta farebnú úpravu loga', canvas: 'prepočíta plátno',
};
const S = {
  project: '', projects: [], projectInfo: null, batch: {},
  module: 'titulky', modules: [], mod: {},
  params: {}, undo: {}, redo: {}, recipe: {}, names: {}, tPer: {},
  t: 0, playing: false, play0: 0, tl: null, tlName: '', tlSlug: '',
  quality: 'auto', tab: 'render', openGroups: {}, openOut: null,
  fonts: [], info: {}, jobs: [], outputs: [], recipes: [], jobStates: {},
  editing: null, lastFrame: null, simPoll: null, drag: null, modalClose: null,
};
const ctl = {};        // param key -> {row, set, spec}
const TL = { W: 0, phases: [], lanes: [] };
let readyResolve; const ready = new Promise((r) => { readyResolve = r; });

const P = () => S.params[S.module];
const M = () => S.mod[S.module];
const specOf = (key) => (M() && M().specs[key]) || null;
const moduleLabel = (id) => { const m = S.modules.find((x) => x.id === id); return m ? m.label : (id || ''); };
function resOf(p) { const m = /^(\d+)x(\d+)$/.exec(String((p || P() || {}).res || '')); return m ? [+m[1], +m[2]] : [1920, 1080]; }
const currentName = () => (S.names[S.module] || '').trim() || S.tlName || '';
function currentHash() { const p = P(); const keys = Object.keys(p).sort(); return djb2(S.module + '|' + JSON.stringify(keys.map((k) => [k, p[k]]))); }

// ------------------------------------------------------------------ params
const optPairs = (spec) => (spec.options || []).map((o) => Array.isArray(o) ? [String(o[0]), String(o.length > 1 ? o[1] : o[0])] : [String(o), String(o)]);
function coerce(spec, v) {
  switch (spec.type) {
    case 'float': { let n = parseFloat(v); if (!isFinite(n)) n = +spec.default; return clamp(n, spec.min, spec.max); }
    case 'int': { let n = Math.round(parseFloat(v)); if (!isFinite(n)) n = +spec.default; return clamp(n, spec.min, spec.max); }
    case 'bool': return !!v;
    case 'color': { const s = String(v ?? '').trim().toLowerCase(); return /^#[0-9a-f]{6}$/.test(s) ? s : spec.default; }
    case 'select': { const s = String(v); return optPairs(spec).some((o) => o[0] === s) ? s : spec.default; }
    case 'font': { const s = String(v ?? ''); return S.fonts.some((f) => f.file === s) ? s : spec.default; }
    default: return String(v ?? '');
  }
}
function roundStep(spec, v) {
  const st = spec.step && spec.step !== 'any' ? +spec.step : (spec.type === 'int' ? 1 : 0);
  if (!st) return v;
  return +(Math.round((v - spec.min) / st) * st + spec.min).toFixed(6);
}
const sameVal = (spec, a, b) => (spec.type === 'float' || spec.type === 'int') ? Math.abs(+a - +b) < 1e-9 : String(a) === String(b);
const isDefault = (spec, v) => sameVal(spec, v, spec.default);
function sanitize(modId, src) {
  const m = S.mod[modId]; const out = {};
  for (const [k, spec] of Object.entries(m.specs)) out[k] = coerce(spec, (src && k in src) ? src[k] : spec.default);
  return out;
}
function setParams(obj, opts = {}) {
  const p = P(); const changed = [];
  for (const [k, raw] of Object.entries(obj || {})) {
    const spec = specOf(k); if (!spec) continue;
    const v = coerce(spec, raw);
    if (!sameVal(spec, p[k], v)) { p[k] = v; changed.push(k); }
  }
  if (changed.length) onParamsChanged(changed, opts);
  return changed;
}
const setParam = (k, v, opts) => setParams({ [k]: v }, opts);
function onParamsChanged(keys, opts = {}) {
  refreshInspector();
  S.tl = localTimeline(S.tl);
  layoutTimeline(); updateTimeUI();
  scheduleTimeline();
  if (keys.includes('res')) fitViewer();
  if (!opts.noFrame) requestFrame({ interactive: !!opts.live });
  refreshStrip();
  updateRecipeBadge(); updateRenderSummary(); save();
}

// undo / redo: one snapshot per gesture (beginEdit on the first slider input, endEdit on change)
function beginEdit() { if (!S.editing) S.editing = { module: S.module, snap: clone(P()) }; }
function endEdit() {
  const e = S.editing; if (!e) return; S.editing = null;
  if (e.module !== S.module) return;
  if (JSON.stringify(e.snap) !== JSON.stringify(P())) pushUndo(e.snap);
}
function edit(fn) { const was = !!S.editing; if (!was) beginEdit(); try { fn(); } finally { if (!was) endEdit(); } }
function pushUndo(snap) {
  const u = S.undo[S.module] || (S.undo[S.module] = []);
  u.push(snap); if (u.length > 120) u.shift();
  S.redo[S.module] = [];
  updateUndoButtons();
}
function undo() {
  const u = S.undo[S.module]; if (!u || !u.length) return;
  (S.redo[S.module] || (S.redo[S.module] = [])).push(clone(P()));
  S.params[S.module] = u.pop();
  onParamsChanged(Object.keys(P()));
  updateUndoButtons();
}
function redo() {
  const r = S.redo[S.module]; if (!r || !r.length) return;
  (S.undo[S.module] || (S.undo[S.module] = [])).push(clone(P()));
  S.params[S.module] = r.pop();
  onParamsChanged(Object.keys(P()));
  updateUndoButtons();
}
function updateUndoButtons() {
  const u = (S.undo[S.module] || []).length, r = (S.redo[S.module] || []).length;
  $('#undo').disabled = !u; $('#redo').disabled = !r;
  $('#undo').title = u ? `Späť (Ctrl+Z) · ${u}` : 'Späť (Ctrl+Z)';
  $('#redo').title = r ? `Znova (Ctrl+Y) · ${r}` : 'Znova (Ctrl+Y)';
}

// ------------------------------------------------------------------ module loading
async function loadModule(id) {
  const d = await api.get('/api/schema?module=' + encodeURIComponent(id));
  const specs = {}; for (const g of d.schema) for (const p of g.params) specs[p.key] = p;
  const presetKeys = new Set(); for (const pr of d.presets || []) for (const k of Object.keys(pr.params || {})) presetKeys.add(k);
  S.mod[d.module] = { id: d.module, label: d.label, desc: d.desc || '', schema: d.schema, presets: d.presets || [], lanes: d.lanes || [], defaults: d.defaults, specs, presetKeys: [...presetKeys] };
  S.batch[d.module] = d.batch || null;
  if (Array.isArray(d.phases) && d.phases.length) {
    PHASE_KEYS[d.module] = {}; PHASE_META[d.module] = [];
    for (const ph of d.phases) { PHASE_KEYS[d.module][ph.id] = ph.key; PHASE_META[d.module].push([ph.id, ph.label, ph.video || 1]); }
  }
  S.modules = d.modules || []; S.fonts = d.fonts || []; S.moduleErrors = d.module_errors || {};
  if (d.project) { S.project = d.project.slug; S.projectInfo = d.project; }
  if (d.projects) S.projects = d.projects;
  S.info = { app: d.app, app_name: d.app_name, engine: d.engine, out_root: d.out_root, cpus: d.cpus, started: d.started };
  S.params[d.module] = sanitize(d.module, S.params[d.module] || d.defaults);
  return S.mod[d.module];
}
async function switchModule(id, first) {
  if (!first && id === S.module) return;
  stop();
  if (!first) S.tPer[S.module] = S.t;
  if (!S.mod[id]) {
    try { await loadModule(id); }
    catch (e) {
      if (!first) throw e;
      // the remembered module is not in this project: fall back to the first one it has
      const d = await api.get('/api/schema'); id = d.module; if (!S.mod[id]) await loadModule(id);
    }
  }
  S.module = id; S.editing = null; S.t = S.tPer[id] || 0;
  renderModuleTabs(); renderProjects();
  buildInspector();
  buildTimeline();
  $('#name').value = S.names[id] || '';
  updateBatchBox();
  S.tl = localTimeline(null); S.tlName = ''; S.tlSlug = '';
  layoutTimeline(); updateTimeUI(); updateRecipeBadge(); updateUndoButtons(); updateRenderSummary(); setStatusLeft();
  fitViewer();
  requestFrame({ exact: true });
  refreshStrip.now();
  save();
  await fetchTimeline();
}
function renderModuleTabs() {
  const nav = $('#modules'); nav.innerHTML = '';
  for (const m of S.modules) nav.append(el('button', { class: m.id === S.module ? 'on' : '', title: m.desc || m.label, onclick: () => switchModule(m.id).catch((e) => toast(e.message, 'err')) }, m.label));
  const errs = Object.entries(S.moduleErrors || {});
  if (errs.length) nav.append(el('button', { class: 'mod-err', type: 'button', title: 'Niektoré moduly sa nenačítali, klikni pre detail', onclick: showModuleErrors }, icon('alert'), String(errs.length)));
  nav.append(el('button', { class: 'mod-reload', type: 'button', title: 'Znovu načítať moduly z priečinka moduly (nový efekt sa objaví bez reštartu)', onclick: reloadModules }, icon('refresh')));
}
function showModuleErrors() {
  const errs = Object.entries(S.moduleErrors || {});
  if (!errs.length) { toast('Všetky moduly sa načítali.', 'ok'); return; }
  const body = el('div', { class: 'mod-errors' }, ...errs.map(([f, e]) => el('div', {}, el('b', {}, f), el('pre', {}, String(e)))));
  openModal({ title: 'Moduly, ktoré sa nenačítali', body, foot: [el('button', { class: 'btn', type: 'button', onclick: closeModal }, 'Zavrieť')] });
}
async function reloadModules() {
  try {
    const d = await api.post('/api/modules/reload', {});
    S.modules = d.modules || S.modules; S.moduleErrors = d.module_errors || {};
    const keep = S.modules.some((m) => m.id === S.module) ? S.module : (S.modules[0] || {}).id;
    S.mod = {};
    await switchModule(keep, true);
    const ne = Object.keys(S.moduleErrors).length;
    toast(`Načítané moduly: ${S.modules.length}` + (ne ? ` · ${ne} s chybou` : ''), ne ? 'warn' : 'ok');
  } catch (e) { toast('Moduly sa nepodarilo načítať: ' + e.message, 'err'); }
}
function updateBatchBox() {
  const b = S.batch[S.module]; const box = $('#batch-box');
  box.classList.toggle('hidden', !b);
  if (!b) return;
  $('#batch-label').textContent = b.label || 'Dávka';
  $('#batch-help').textContent = b.help || 'Každý riadok je jedna úloha s aktuálnymi parametrami.';
}

// ------------------------------------------------------------------ projects (one per band)
function renderProjects() {
  const sel = $('#project'); sel.innerHTML = '';
  for (const p of S.projects) sel.append(el('option', { value: p.slug }, p.name));
  sel.value = S.project;
  const info = S.projectInfo || {};
  const app = S.info.app_name || 'CROM';
  $('#brand-name').textContent = app.toUpperCase();
  $('#brand-sub').textContent = info.name || 'vizuály pre kapely';
  document.title = (info.name ? info.name + ' · ' : '') + app;
}
async function switchProject(slug) {
  if (!slug || slug === S.project) return;
  stop(); save.now();
  S.project = slug;
  S.mod = {}; S.recipe = {}; S.undo = {}; S.redo = {};
  const keep = S.module;
  await switchModule(keep, true);
  pollJobs(); refreshRecipes(); refreshOutputs();
  if (window.AI && window.AI.onProject) window.AI.onProject();
  save();
}
async function newProjectFlow() {
  const r = await askText({ title: 'Nový projekt', label: 'Projekt je jedna kapela alebo klient: vlastné logo, obaly, recepty a výstupy. Názov kapely:', value: '', buttons: [{ label: 'Vytvoriť', action: 'ok', primary: true }] });
  const name = r && r.text; if (!name) return;
  try {
    const d = await api.post('/api/project', { name });
    S.projects = d.projects || S.projects;
    await switchProject(d.project.slug);
    toast(`Projekt „${d.project.name}“ je pripravený. Logo a obaly pridáš tlačidlom so šípkou hore.`, 'ok', { ms: 8000 });
  } catch (e) { toast('Projekt sa nepodarilo vytvoriť: ' + e.message, 'err'); }
}
function assetFlow() {
  const info = S.projectInfo || { assets: {} };
  const body = el('div', { class: 'assets' });
  const kinds = [['logo', 'Logo kapely', 'PNG s priehľadným pozadím. Efekty ho nikdy neprekresľujú.', '.png'],
                 ['obal', 'Obal albumu', 'JPG alebo PNG obalu, pre modul Obal (rozpohybovaný obal).', '.jpg,.jpeg,.png']];
  for (const [kind, label, help, accept] of kinds) {
    const have = (info.assets && info.assets[kind]) || [];
    const input = el('input', { type: 'file', accept, class: 'hidden' });
    input.addEventListener('change', async () => {
      const f = input.files[0]; if (!f) return;
      try {
        const d = await api.upload('/api/asset?kind=' + kind, f, { 'X-Kind': kind, 'X-Filename': asciiName(f.name) });
        S.projectInfo = d.project; S.mod = {}; closeModal();
        await switchModule(S.module, true);
        toast(`Nahraté: ${d.file || f.name}` + ((d.warnings || []).length ? ' · ' + d.warnings.join(' ') : ''), (d.warnings || []).length ? 'warn' : 'ok', { ms: 8000 });
      } catch (e) { toast('Nahrávanie zlyhalo: ' + e.message, 'err'); }
    });
    body.append(el('div', { class: 'asset-row' },
      el('div', {}, el('b', {}, label), el('div', { class: 'hint' }, help),
        el('div', { class: 'hint' }, have.length ? 'V projekte: ' + have.join(', ') : 'Zatiaľ nič.')),
      input, el('button', { class: 'btn', onclick: () => input.click() }, 'Nahrať…')));
  }
  openModal({ title: 'Súbory projektu „' + (info.name || '') + '“', body, foot: [el('button', { class: 'btn', type: 'button', onclick: closeModal }, 'Zavrieť')] });
}

// ------------------------------------------------------------------ timeline data
function localTimeline(prev) {
  const p = P(); const keys = PHASE_KEYS[S.module]; const meta = PHASE_META[S.module];
  if (!keys || !meta || !p) return prev;
  let t = 0, t1 = 0; const phases = [];
  for (const [id, label, video] of meta) {
    const len = +p[keys[id]] || 0;
    phases.push({ id, label, t0: t, t1: t + len, video }); t += len;
    if (video === 1) t1 = t;
  }
  return { total: t, t1, fps: parseInt(p.fps, 10) || 30, phases, markers: prev && prev.markers ? prev.markers : [] };
}
async function fetchTimeline() {
  const mod = S.module;
  try {
    const d = await api.post('/api/timeline', { module: mod, params: P() });
    if (mod !== S.module) return;
    S.tl = d.timeline; S.tlName = d.name || ''; S.tlSlug = d.slug || '';
    layoutTimeline(); updateTimeUI(); updateNamePlaceholder(); updateRenderSummary();
  } catch (e) { console.warn('timeline', e); }
}
const scheduleTimeline = debounce(fetchTimeline, 150);

function laneSpan(lane, tl, p) {
  if (lane.phase === 'end') { const len = +p[lane.len] || 0; return { a: tl.total - len, b: tl.total, t0: tl.total, span: -1 }; }
  const ph = tl.phases.find((x) => x.id === lane.phase);
  let t0, span;
  if (lane.unit === 's') { t0 = 0; span = 1; } else { t0 = ph ? ph.t0 : 0; span = ph ? (ph.t1 - ph.t0) / 100 : 0; }
  const a = lane.from ? t0 + (+p[lane.from] || 0) * span : t0;
  const b = lane.to ? t0 + (+p[lane.to] || 0) * span : (lane.len ? a + (+p[lane.len] || 0) * span : a);
  return { a, b, t0, span };
}
function laneValueText(lane, p, sp) {
  const f = (k) => (k && k in p) ? trimZeros(fmtNum(p[k], (specOf(k) || {}).step)) : '0';
  if (lane.phase === 'end') return `${f(lane.len)} s`;
  if (lane.unit === 's') return lane.shape === 'tick' ? `${f(lane.from)} s` : `${trimZeros(sp.a.toFixed(2))}–${trimZeros(sp.b.toFixed(2))} s`;
  if (lane.len) return `${f(lane.from)} +${f(lane.len)} %`;
  return `${f(lane.from)}–${f(lane.to)} %`;
}
function laneTitle(lane) {
  const L = (k) => k ? ((specOf(k) || {}).label || k) : null;
  const parts = [];
  if (lane.phase === 'end') parts.push(`${L(lane.len)} (sekundy pred koncom)`);
  else {
    if (lane.from) parts.push('začiatok: ' + L(lane.from));
    if (lane.to) parts.push('koniec: ' + L(lane.to));
    if (lane.len) parts.push('dĺžka: ' + L(lane.len));
    parts.push(lane.unit === 's' ? 'v sekundách od začiatku' : `v % fázy „${(S.tl && (S.tl.phases.find((x) => x.id === lane.phase) || {}).label) || lane.phase}“`);
  }
  return 'Potiahni okraj alebo celý pás. ' + parts.join(' · ');
}

// ------------------------------------------------------------------ inspector
function buildInspector() {
  const m = M(); if (!m) return;
  const pr = $('#presets'); pr.innerHTML = '';
  for (const ps of m.presets) pr.append(el('button', { class: 'chip', dataset: { preset: ps.id }, title: presetTitle(ps), onclick: () => applyPreset(ps) }, ps.label));
  pr.classList.toggle('hidden', !m.presets.length);
  const gs = $('#groups'); gs.innerHTML = '';
  for (const k of Object.keys(ctl)) delete ctl[k];
  const og = S.openGroups[S.module] || (S.openGroups[S.module] = {});
  for (const g of m.schema) {
    const det = el('details', { class: 'group', dataset: { group: g.id } });
    det.open = og[g.id] !== false;
    det.addEventListener('toggle', () => { if (!$('#filter').value) { og[g.id] = det.open; save(); } });
    det.append(el('summary', {}, icon('chev'), el('span', {}, g.label), el('span', { class: 'gcount', title: 'počet zmenených parametrov v skupine' })));
    const rows = el('div', { class: 'rows' });
    for (const spec of g.params) rows.append(buildRow(spec, g));
    det.append(rows); gs.append(det);
  }
  refreshInspector();
  applyFilter();
}
function defaultLabel(spec) {
  if (spec.type === 'select') { const o = optPairs(spec).find((x) => x[0] === String(spec.default)); return o ? o[1] : spec.default; }
  if (spec.type === 'font') { const f = S.fonts.find((x) => x.file === spec.default); return f ? f.name : spec.default; }
  if (spec.type === 'textarea') return String(spec.default).replace(/\n/g, ' / ');
  return String(spec.default) + (spec.unit ? ' ' + spec.unit : '');
}
function rowTitle(spec) {
  const lines = [];
  if (spec.hint) lines.push(spec.hint);
  lines.push('Predvolené: ' + defaultLabel(spec));
  if (spec.rebuild && REBUILD_NOTE[spec.rebuild]) lines.push('Zmena ' + REBUILD_NOTE[spec.rebuild] + '.');
  return lines.join('\n');
}
function presetTitle(ps) {
  const e = Object.entries(ps.params || {});
  if (!e.length) return 'Predvolené hodnoty';
  return e.map(([k, v]) => `${(specOf(k) || {}).label || k}: ${v}`).join('\n');
}
function buildRow(spec, group) {
  const key = spec.key;
  const label = el('label', { title: rowTitle(spec) }, el('i', { class: 'dot' }), el('span', {}, spec.label));
  const c = el('div', { class: 'ctl' });
  const rst = el('button', { class: 'rst', type: 'button', title: 'Vrátiť na predvolené: ' + defaultLabel(spec), onclick: () => edit(() => setParam(key, spec.default)) }, icon('reset'));
  const row = el('div', { class: 'row ' + spec.type + (spec.rebuild === 'sim' ? ' heavy' : ''), dataset: { key, search: norm(`${spec.label} ${key} ${group.label} ${spec.hint || ''}`) } }, label, c, rst);
  let set;
  if (spec.type === 'float' || spec.type === 'int') {
    const step = spec.step && spec.step !== 'any' ? spec.step : (spec.type === 'int' ? 1 : 'any');
    const rg = el('input', { type: 'range', min: spec.min, max: spec.max, step });
    const nb = el('input', { type: 'number', min: spec.min, max: spec.max, step, title: `${spec.min} – ${spec.max}` });
    rg.addEventListener('pointerdown', beginEdit);
    rg.addEventListener('input', () => { beginEdit(); setParam(key, rg.value, { live: true }); });
    rg.addEventListener('change', () => { setParam(key, rg.value); endEdit(); });
    rg.addEventListener('wheel', (e) => { e.preventDefault(); const st = step === 'any' ? 0.1 : +step; edit(() => setParam(key, roundStep(spec, (+P()[key]) + (e.deltaY < 0 ? st : -st)))); }, { passive: false });
    nb.addEventListener('change', () => edit(() => setParam(key, nb.value)));
    nb.addEventListener('keydown', (e) => { if (e.key === 'Enter') nb.blur(); });
    c.append(rg, nb, el('span', { class: 'unit', title: spec.unit || '' }, spec.unit || ''));
    set = (v) => { rg.value = v; if (document.activeElement !== nb) nb.value = fmtNum(v, step); };
  } else if (spec.type === 'color') {
    const ci = el('input', { type: 'color' });
    const hx = el('input', { type: 'text', class: 'hex', maxlength: 7, spellcheck: 'false' });
    ci.addEventListener('input', () => { beginEdit(); setParam(key, ci.value, { live: true }); });
    ci.addEventListener('change', () => { setParam(key, ci.value); endEdit(); });
    hx.addEventListener('change', () => {
      let v = hx.value.trim(); if (/^[0-9a-f]{6}$/i.test(v)) v = '#' + v;
      if (/^#[0-9a-f]{6}$/i.test(v)) edit(() => setParam(key, v)); else hx.value = P()[key];
    });
    hx.addEventListener('keydown', (e) => { if (e.key === 'Enter') hx.blur(); });
    c.append(ci, hx);
    set = (v) => { ci.value = v; hx.value = v; };
  } else if (spec.type === 'select') {
    const se = el('select', {}, ...optPairs(spec).map(([v, l]) => el('option', { value: v }, l)));
    se.addEventListener('change', () => edit(() => setParam(key, se.value)));
    c.append(se);
    set = (v) => { se.value = v; };
  } else if (spec.type === 'font') {
    const nm = el('span', {}, '');
    const b = el('button', { class: 'fontpick', type: 'button', title: 'Vybrať písmo alebo nahrať vlastné (.ttf / .otf)' }, nm, icon('chev'));
    b.addEventListener('click', () => openFontPicker(b, key));
    c.append(b);
    set = (v) => { const f = S.fonts.find((x) => x.file === v); nm.textContent = f ? f.name : v; ensureFontFace(v); nm.style.fontFamily = fontFamily(v); };
  } else if (spec.type === 'asset') {
    const se = el('select', {});
    const fill = (v) => {
      const have = ((S.projectInfo && S.projectInfo.assets) || {})[spec.kind] || [];
      const opts = have.map((f) => [f, f]);
      if (v && !have.includes(v)) opts.unshift([v, v + ' (v projekte chýba)']);
      if (!opts.length) opts.push(['', 'Zatiaľ nič, nahraj súbor']);
      se.innerHTML = ''; for (const [val, l] of opts) se.append(el('option', { value: val }, l));
      se.value = v || '';
    };
    se.addEventListener('change', () => edit(() => setParam(key, se.value)));
    const up = el('button', { class: 'ib sm', type: 'button', title: 'Nahrať nový súbor do projektu' }, icon('upload'));
    up.addEventListener('click', assetFlow);
    c.append(se, up);
    set = (v) => fill(v);
  } else {
    const ta = el('textarea', { rows: spec.type === 'textarea' ? 3 : 1, spellcheck: 'false' });
    const live = debounce(() => setParam(key, ta.value), 250);
    ta.addEventListener('focus', beginEdit);
    ta.addEventListener('input', live);
    ta.addEventListener('blur', () => { live.now(); endEdit(); });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Escape') ta.blur(); e.stopPropagation(); });
    c.append(ta);
    set = (v) => { if (document.activeElement !== ta) ta.value = v; };
  }
  ctl[key] = { row, set, spec };
  return row;
}
function refreshInspector() {
  const p = P(); if (!p) return; const counts = {};
  for (const [key, c] of Object.entries(ctl)) {
    const v = p[key]; c.set(v);
    const ch = !isDefault(c.spec, v);
    c.row.classList.toggle('changed', ch);
    if (ch) { const g = c.row.closest('.group'); if (g) counts[g.dataset.group] = (counts[g.dataset.group] || 0) + 1; }
  }
  for (const det of $$('#groups .group')) { const n = counts[det.dataset.group] || 0; det.querySelector('.gcount').textContent = n ? String(n) : ''; }
  const active = activePreset();
  for (const chip of $$('#presets .chip')) chip.classList.toggle('on', chip.dataset.preset === active);
}
function activePreset() {
  const m = M(); const p = P(); if (!m) return null;
  for (const ps of m.presets) {
    let ok = true;
    for (const k of m.presetKeys) {
      const spec = m.specs[k]; if (!spec) continue;
      const want = coerce(spec, (k in (ps.params || {})) ? ps.params[k] : spec.default);
      if (!sameVal(spec, p[k], want)) { ok = false; break; }
    }
    if (ok) return ps.id;
  }
  return null;
}
function applyPreset(ps) {
  const m = M(); const vals = {};
  for (const k of m.presetKeys) { const spec = m.specs[k]; if (spec) vals[k] = (k in (ps.params || {})) ? ps.params[k] : spec.default; }
  edit(() => setParams(vals));
  toast(`Predvoľba „${ps.label}“ použitá. Ctrl+Z ju vráti.`, 'ok', { ms: 2500 });
}
function applyFilter() {
  const q = norm($('#filter').value.trim());
  const og = S.openGroups[S.module] || {};
  for (const det of $$('#groups .group')) {
    let vis = 0;
    for (const row of $$('.row', det)) { const show = !q || row.dataset.search.includes(q); row.classList.toggle('hid', !show); if (show) vis++; }
    det.classList.toggle('hidden', q && !vis);
    if (q) det.open = true; else det.open = og[det.dataset.group] !== false;
  }
}

// fonts: @font-face per file so the picker and the font button show real samples
const faces = new Set();
const fontFamily = (file) => 'ork-' + String(file || '').replace(/[^A-Za-z0-9]/g, '_');
function ensureFontFace(file) {
  if (!file || faces.has(file)) return; faces.add(file);
  let st = $('#fontfaces'); if (!st) { st = el('style', { id: 'fontfaces' }); document.head.append(st); }
  st.textContent += `@font-face{font-family:"${fontFamily(file)}";src:url("/fonts/${encodeURIComponent(file)}");font-display:swap;}\n`;
}
function sampleText() {
  const p = P(); const t = p && typeof p.title === 'string' ? p.title.split('\n')[0].trim() : '';
  return t || 'The One Who Rides';
}
function openFontPicker(anchor, key) {
  const pop = $('#popover'); pop.innerHTML = '';
  const cur = P()[key];
  for (const f of S.fonts) {
    ensureFontFace(f.file);
    pop.append(el('div', { class: 'fopt' + (f.file === cur ? ' on' : ''), onclick: () => { edit(() => setParam(key, f.file)); closePopover(); } },
      el('span', { class: 'nm', title: f.file }, f.name), el('span', { class: 'smp', style: { fontFamily: fontFamily(f.file) } }, sampleText())));
  }
  const inp = el('input', { type: 'file', accept: '.ttf,.otf', hidden: true });
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0]; if (!file) return;
    try {
      const d = await api.upload('/api/font', file, { 'X-Filename': asciiName(file.name) });
      S.fonts = d.fonts || S.fonts;
      edit(() => setParam(key, d.file));
      toast('Písmo pridané: ' + d.file, 'ok');
    } catch (e) { toast('Písmo sa nepodarilo pridať: ' + e.message, 'err'); }
    closePopover();
  });
  pop.append(el('div', { class: 'fup' }, el('span', {}, 'Vlastné písmo (.ttf / .otf)'), el('button', { class: 'btn sm', type: 'button', onclick: () => inp.click() }, icon('upload'), 'Nahrať'), inp));
  pop.classList.remove('hidden');
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8)) + 'px';
  pop.style.top = Math.max(8, Math.min(r.bottom + 4, window.innerHeight - pop.offsetHeight - 8)) + 'px';
  setTimeout(() => document.addEventListener('pointerdown', onDocDown, true), 0);
}
function onDocDown(e) { if (!e.target.closest('#popover')) closePopover(); }
function closePopover() { $('#popover').classList.add('hidden'); document.removeEventListener('pointerdown', onDocDown, true); }

// ------------------------------------------------------------------ timeline DOM
function bindScrub(elm) {
  elm.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('.lbar, .phase-handle')) return;
    e.preventDefault(); stop();
    const r = elm.getBoundingClientRect();
    const seek = (ev) => setTime(clamp((ev.clientX - r.left) / Math.max(1, r.width), 0, 1) * (S.tl ? S.tl.total : 0), { interactive: true });
    seek(e);
    try { elm.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    const up = () => { elm.removeEventListener('pointermove', seek); elm.removeEventListener('pointerup', up); elm.removeEventListener('pointercancel', up); requestFrame({ exact: true }); };
    elm.addEventListener('pointermove', seek); elm.addEventListener('pointerup', up); elm.addEventListener('pointercancel', up);
  });
}
function buildTimeline() {
  const m = M(); if (!m) return;
  const ph = $('#tl-phases'); ph.innerHTML = ''; TL.phases = [];
  const keys = PHASE_KEYS[S.module] || {};
  for (const [id, label, video] of (PHASE_META[S.module] || [])) {
    const seg = el('div', { class: 'phase ' + (id === 'hold' ? 'hold' : 'v' + video), dataset: { phase: id } }, el('span', { class: 'pl' }, label), el('small'));
    ph.append(seg);
    const hd = el('div', { class: 'phase-handle', title: `Potiahni: dĺžka fázy „${label}“ (${(m.specs[keys[id]] || {}).label || keys[id]})` });
    bindDrag(hd, { kind: 'phase', id, key: keys[id] });
    ph.append(hd);
    TL.phases.push({ id, seg, hd });
  }
  const lanes = $('#tl-lanes'); lanes.innerHTML = ''; TL.lanes = [];
  for (const lane of m.lanes) {
    const track = el('div', { class: 'tl-track' }); bindScrub(track);
    const bar = el('div', { class: 'lbar ' + lane.shape + (lane.phase === 'out' ? ' out' : '') });
    const tip = el('span', { class: 'tip' }); bar.append(tip);
    const isEnd = lane.phase === 'end';
    if (lane.shape === 'tick') {
      const mid = el('div', { class: 'mid' }); bindDrag(mid, { kind: 'lane', lane, part: 'from' }); bar.append(mid);
    } else {
      if (lane.from || isEnd) { const h = el('div', { class: 'h l' }); bindDrag(h, { kind: 'lane', lane, part: isEnd ? 'endlen' : 'from' }); bar.append(h); }
      if ((lane.to || lane.len) && !isEnd) { const h = el('div', { class: 'h r' }); bindDrag(h, { kind: 'lane', lane, part: lane.to ? 'to' : 'len' }); bar.append(h); }
      const mid = el('div', { class: 'mid' });
      bindDrag(mid, { kind: 'lane', lane, part: isEnd ? 'endlen' : (lane.from ? (lane.to || lane.len ? 'move' : 'from') : (lane.to ? 'to' : 'len')) });
      bar.append(mid);
    }
    track.append(bar);
    const lab = el('div', { class: 'tl-label', title: laneTitle(lane) }, el('span', {}, lane.label), el('span', { class: 'val' }));
    lanes.append(el('div', { class: 'lane', dataset: { lane: lane.id } }, lab, track));
    TL.lanes.push({ lane, track, bar, tip, val: lab.querySelector('.val') });
  }
}
function drawRuler(W, total) {
  const r = $('#tl-ruler'); r.innerHTML = '';
  let step = 1;
  for (const c of [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60]) { step = c; if (W / (total / c) >= 64) break; }
  const sub = step / 2;
  for (let i = 0; i * sub <= total + 1e-9; i++) {
    const t = i * sub; const major = i % 2 === 0;
    const tk = el('div', { class: 'tick' + (major ? ' major' : ''), style: { left: (t / total * W) + 'px' } });
    if (major) tk.append(el('span', {}, t >= 60 ? `${Math.floor(t / 60)}:${pad2(Math.round(t % 60))}` : trimZeros(t.toFixed(2)) + 's'));
    r.append(tk);
  }
  r.append(el('div', { class: 'ph-now', id: 'ph-now' }));
}
function layoutTimeline() {
  const tl = S.tl; if (!tl) return;
  const track = $('#tl-phases'); const W = track.clientWidth || 1; TL.W = W;
  const total = Math.max(0.001, tl.total); const x = (t) => clamp(t / total, 0, 1) * W;
  const p = P();
  drawRuler(W, total);
  for (const { id, seg, hd } of TL.phases) {
    const ph = tl.phases.find((q) => q.id === id);
    if (!ph) { seg.style.display = 'none'; hd.style.display = 'none'; continue; }
    seg.style.display = ''; hd.style.display = '';
    const a = x(ph.t0), b = x(ph.t1);
    seg.style.left = a + 'px'; seg.style.width = Math.max(0, b - a) + 'px';
    seg.querySelector('small').textContent = fmtS(ph.t1 - ph.t0);
    seg.title = `${ph.label} · ${fmtS(ph.t0)} – ${fmtS(ph.t1)}` + (PHASE_META[S.module] && PHASE_META[S.module].length > 1 ? ` · video ${ph.video}` : '');
    hd.style.left = b + 'px';
  }
  const mk = $('#tl-markers'); mk.innerHTML = ''; let lastX = -1e9, odd = false;
  for (const mr of (tl.markers || [])) {
    const px = x(mr.t); odd = (px - lastX < 64) ? !odd : false; lastX = px;
    mk.append(el('div', { class: 'marker' + (odd ? ' odd' : ''), style: { left: px + 'px' }, title: `${mr.label} · ${fmtS(mr.t)}` }, el('i'), el('span', {}, mr.label)));
  }
  for (const L of TL.lanes) {
    const sp = laneSpan(L.lane, tl, p);
    const a = x(Math.min(sp.a, sp.b)), b = x(Math.max(sp.a, sp.b));
    L.bar.style.left = a + 'px';
    L.bar.style.width = L.lane.shape === 'tick' ? '' : Math.max(3, b - a) + 'px';
    L.val.textContent = laneValueText(L.lane, p, sp);
    L.tip.textContent = L.lane.shape === 'tick' ? fmtS(sp.a) : `${fmtS(sp.a)} → ${fmtS(sp.b)}`;
  }
  positionPlayhead();
}
function bindDrag(handle, cfg) {
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !S.tl) return;
    e.preventDefault(); e.stopPropagation();
    stop(); beginEdit();
    const st = { cfg, x0: e.clientX, total: Math.max(0.001, S.tl.total), W: TL.W || 1, p0: clone(P()), tl0: clone(S.tl) };
    if (cfg.kind === 'lane') st.span0 = laneSpan(cfg.lane, S.tl, P());
    S.drag = st;
    try { handle.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    handle.classList.add('drag'); const bar = handle.closest('.lbar'); if (bar) bar.classList.add('drag');
    const move = (ev) => dragTo(st, (ev.clientX - st.x0) / st.W * st.total);
    const up = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up); handle.removeEventListener('pointercancel', up);
      handle.classList.remove('drag'); if (bar) bar.classList.remove('drag');
      S.drag = null; endEdit(); scheduleTimeline.now(); requestFrame({ exact: true });
    };
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', up); handle.addEventListener('pointercancel', up);
  });
}
function dragTo(st, dt) {
  const { cfg, p0, tl0, span0: u } = st;
  if (cfg.kind === 'phase') {
    const ph = tl0.phases.find((q) => q.id === cfg.id); const spec = specOf(cfg.key); if (!ph || !spec) return;
    const len = roundStep(spec, clamp((ph.t1 - ph.t0) + dt, spec.min, spec.max));
    setParam(cfg.key, len, { live: true, noFrame: true });
    setTime(ph.t0 + len - 0.001, { interactive: true });
    return;
  }
  const lane = cfg.lane; const vals = {}; let follow = null;
  const valueAt = (key, t) => { const spec = specOf(key); return spec ? roundStep(spec, clamp((t - u.t0) / u.span, spec.min, spec.max)) : null; };
  if (cfg.part === 'from') { const v = valueAt(lane.from, u.a + dt); if (v == null) return; vals[lane.from] = v; follow = u.t0 + v * u.span; }
  else if (cfg.part === 'to') { const v = valueAt(lane.to, u.b + dt); if (v == null) return; vals[lane.to] = v; follow = u.t0 + v * u.span; }
  else if (cfg.part === 'len') {
    const spec = specOf(lane.len); if (!spec) return;
    const a = lane.from ? u.t0 + (+p0[lane.from]) * u.span : u.t0;
    const v = roundStep(spec, clamp((u.b + dt - a) / u.span, spec.min, spec.max)); vals[lane.len] = v; follow = a + v * u.span;
  } else if (cfg.part === 'endlen') {
    const spec = specOf(lane.len); if (!spec) return;
    const v = roundStep(spec, clamp(tl0.total - (u.a + dt), spec.min, spec.max)); vals[lane.len] = v; follow = tl0.total - v;
  } else if (cfg.part === 'move') {
    const sf = specOf(lane.from); if (!sf) return;
    const d0 = +p0[lane.from]; const dv = dt / u.span;
    if (lane.to) {
      const stt = specOf(lane.to); const L = (+p0[lane.to]) - d0;
      let nf = clamp(d0 + dv, sf.min, Math.min(sf.max, stt.max - L)); nf = roundStep(sf, nf);
      vals[lane.from] = nf; vals[lane.to] = roundStep(stt, clamp(nf + L, stt.min, stt.max));
    } else {
      const sl = specOf(lane.len); const L = sl ? +p0[lane.len] : 0;
      let hi = sf.max; if (sl && u.span > 0) hi = Math.min(sf.max, (tl0.total - u.t0) / u.span - L);
      vals[lane.from] = roundStep(sf, clamp(d0 + dv, sf.min, Math.max(sf.min, hi)));
    }
    follow = u.t0 + vals[lane.from] * u.span;
  }
  setParams(vals, { live: true, noFrame: true });
  if (follow != null) setTime(follow, { interactive: true });
}

// ------------------------------------------------------------------ time, playhead, playback
function setTime(t, opts = {}) {
  const total = S.tl ? S.tl.total : 0;
  S.t = clamp(+t || 0, 0, total);
  updateTimeUI();
  if (!opts.noFrame) requestFrame({ interactive: !!opts.interactive, exact: !!opts.exact });
}
function phaseLabelAt(t) {
  const tl = S.tl; if (!tl || !tl.phases.length) return '';
  const ph = tl.phases.find((q) => t >= q.t0 && t < q.t1) || tl.phases[tl.phases.length - 1];
  return ph.label + (tl.phases.length > 1 ? ` · video ${ph.video}` : '');
}
function updateTimeUI() {
  const tl = S.tl; const total = tl ? tl.total : 0; if (S.t > total) S.t = total;
  const sc = $('#scrub'); sc.max = total; sc.value = S.t;
  $('#tc').innerHTML = `${fmtTC(S.t)} <small>/ ${fmtTC(total)}</small>`;
  const fps = tl ? tl.fps : 30;
  $('#hud-time').textContent = `${fmtS(S.t)} · #${Math.round(S.t * fps)}`;
  $('#hud-phase').textContent = phaseLabelAt(S.t);
  positionPlayhead();
}
function positionPlayhead() {
  const tl = S.tl; if (!tl) return;
  const track = $('#tl-phases'); const W = track.clientWidth || 1; const f = clamp(S.t / Math.max(0.001, tl.total), 0, 1);
  const tlr = $('#timeline').getBoundingClientRect(); const tr = track.getBoundingClientRect();
  $('#tl-playhead').style.left = (tr.left - tlr.left + f * W) + 'px';
  const now = $('#ph-now'); if (now) { now.style.left = (f * W) + 'px'; now.textContent = fmtS(S.t); }
  $('#strip-head').style.left = (f * 100) + '%';
}
let raf = 0;
function play() {
  if (S.playing || !S.tl || !S.tl.total) return;
  if (S.t >= S.tl.total - 0.02) S.t = 0;
  S.playing = true; S.play0 = performance.now() - S.t * 1000;
  const b = $('#play'); b.innerHTML = ''; b.append(icon('pause')); b.classList.add('on');
  raf = requestAnimationFrame(tick);
}
function tick(now) {
  if (!S.playing) return;
  const total = S.tl.total; let t = (now - S.play0) / 1000;
  if (t >= total) { t = t % total; S.play0 = now - t * 1000; }
  S.t = t; updateTimeUI(); requestFrame({ interactive: true });
  raf = requestAnimationFrame(tick);
}
function stop() {
  if (!S.playing) return;
  S.playing = false; cancelAnimationFrame(raf);
  const b = $('#play'); b.innerHTML = ''; b.append(icon('play')); b.classList.remove('on');
  requestFrame({ exact: true });
}
function markerPoints() {
  const tl = S.tl; if (!tl) return [];
  const pts = new Set([0, +tl.total.toFixed(3)]);
  for (const ph of tl.phases) { pts.add(+ph.t0.toFixed(3)); pts.add(+ph.t1.toFixed(3)); }
  for (const m of tl.markers || []) pts.add(+(+m.t).toFixed(3));
  return [...pts].sort((a, b) => a - b);
}
function gotoMarker(dir) {
  const pts = markerPoints(); if (!pts.length) return;
  const t = dir > 0 ? pts.find((x) => x > S.t + 1e-3) : [...pts].reverse().find((x) => x < S.t - 1e-3);
  if (t != null) setTime(t, { exact: true });
}

// ------------------------------------------------------------------ live preview
const frameReq = { inflight: false, dirty: null, seq: 0 };
const scheduleExact = debounce(() => { if (!S.playing && !S.drag) requestFrame({ exact: true }); }, 220);
function pickHeight(want) {
  const box = $('#frame-box');
  const bh = Math.max(72, Math.round((box.clientHeight || 300) * (window.devicePixelRatio || 1)));
  const maxH = resOf()[1]; const q = S.quality;
  if (want.interactive) return clamp(q === 'fine' ? 450 : q === 'fast' ? 270 : 360, 72, maxH);
  const cands = q === 'fine' ? [270, 360, 450, 540, 720, 900, 1080] : q === 'fast' ? [270, 360, 450] : [270, 360, 450, 540, 720];
  let h = cands[0]; for (const c of cands) if (c <= bh * 1.08) h = c;
  return clamp(h, 72, maxH);
}
function requestFrame(want = {}) {
  if (want.interactive) scheduleExact.cancel();
  if (frameReq.inflight) { const d = frameReq.dirty || {}; frameReq.dirty = { interactive: want.interactive || (d.interactive && !want.exact), exact: want.exact || d.exact }; return; }
  doFrame(want);
}
async function doFrame(want) {
  if (!S.tl) return;
  frameReq.inflight = true;
  const quick = !!want.interactive && !want.exact;
  const mod = S.module; const t = S.t;
  const body = { module: mod, params: P(), t, h: pickHeight(want), quick, q: quick ? 82 : 90 };
  try {
    const { blob, info } = await api.image('/api/frame', body);
    if (mod === S.module) showFrame(blob, info, { t, quick });
  } catch (e) {
    setStatusRight('Náhľad zlyhal: ' + e.message, true);
  }
  frameReq.inflight = false;
  if (frameReq.dirty) { const d = frameReq.dirty; frameReq.dirty = null; doFrame(d); }
  else if (quick && !S.playing) scheduleExact();
}
function showFrame(blob, info, meta) {
  const img = $('#frame'); const url = URL.createObjectURL(blob); const old = img.dataset.url;
  img.onload = () => { if (old) URL.revokeObjectURL(old); $('#frame-empty').classList.add('hidden'); };
  img.src = url; img.dataset.url = url;
  img.classList.toggle('stale', !!meta.quick || !!info.approx);
  S.lastFrame = { t: meta.t, quick: meta.quick, approx: !!info.approx, info };
  $('#hud-size').textContent = info.size ? `${info.size[0]}×${info.size[1]}${meta.quick ? ' rýchly' : ''}` : '';
  $('#hud-ms').textContent = info.frame_ms != null ? `${Math.round(info.frame_ms)} ms` + (info.build_ms ? ` (+${Math.round(info.build_ms)} ms stavba)` : '') : '';
  $('#hud-approx').classList.toggle('hidden', !info.approx);
  updateSim(info.sim);
  const w = $('#warnings'); w.innerHTML = ''; const ws = info.warnings || [];
  w.classList.toggle('hidden', !ws.length); for (const s of ws) w.append(el('div', {}, s));
  setStatusRight(`${info.size ? info.size[0] + '×' + info.size[1] : ''} · ${Math.round(info.frame_ms || 0)} ms${info.particles ? ' · ' + info.particles + ' častíc' : ''}`);
  if (info.approx || (info.sim && info.sim.busy)) startSimPoll();
}
function updateSim(sim) {
  const box = $('#sim');
  if (!sim || (!sim.busy && !(sim.done < sim.total) && !sim.error)) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const pct = sim.total ? Math.round(100 * sim.done / sim.total) : 0;
  $('#sim-fill').style.width = pct + '%';
  $('#sim-text').textContent = sim.error ? ('Simulácia zlyhala: ' + sim.error) : `Simulácia ohňa ${pct} %`;
}
function startSimPoll() {
  if (S.simPoll) return;
  S.simPoll = setInterval(async () => {
    try {
      const d = await api.get('/api/sim'); const sim = d.sim; updateSim(sim);
      const lf = S.lastFrame; const fps = S.tl ? S.tl.fps : 30;
      if (!sim || !sim.busy) {
        stopSimPoll();
        if (lf && lf.approx && !S.playing && !S.drag) requestFrame({ exact: true });
        if (S.module === 'logo') refreshStrip.now();
        return;
      }
      if (lf && lf.approx && sim.done > Math.round(S.t * fps) + 1 && !S.playing && !S.drag && !frameReq.inflight) requestFrame({ exact: true });
    } catch (e) { stopSimPoll(); }
  }, 800);
}
function stopSimPoll() { if (S.simPoll) { clearInterval(S.simPoll); S.simPoll = null; } }

let stripSeq = 0;
const refreshStrip = debounce(async () => {
  if (!S.tl) return;
  const strip = $('#strip'); const W = strip.clientWidth || 800; const H = 64; const [rw, rh] = resOf();
  const n = clamp(Math.round(W / (H * rw / rh)), 2, 40);
  const mod = S.module; const seq = ++stripSeq; const img = $('#strip-img'); img.classList.add('stale');
  try {
    const { blob, info } = await api.image('/api/strip', { module: mod, params: P(), n, th: H });
    if (seq !== stripSeq || mod !== S.module) return;
    const url = URL.createObjectURL(blob); const old = img.dataset.url;
    img.onload = () => { if (old) URL.revokeObjectURL(old); img.classList.remove('stale'); };
    img.src = url; img.dataset.url = url;
    $('#strip-note').classList.toggle('hidden', !info.approx);
    if (info.approx) startSimPoll();
  } catch (e) { if (seq === stripSeq) img.classList.remove('stale'); }
}, 700);

function fitViewer() {
  const v = $('#viewer'); const box = $('#frame-box'); const [rw, rh] = resOf(); const ar = rw / rh;
  const cs = getComputedStyle(v);
  const vw = v.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const vh = v.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  let w = vw, h = w / ar; if (h > vh) { h = vh; w = h * ar; }
  box.style.width = Math.max(64, Math.floor(w)) + 'px'; box.style.height = Math.max(36, Math.floor(h)) + 'px';
}

// ------------------------------------------------------------------ status, toasts, modal
function setStatusLeft() {
  const i = S.info; $('#st-left').textContent = `${i.app_name || 'CROM'} ${i.app || ''} · ${(S.projectInfo && S.projectInfo.name) || ''} · ${moduleLabel(S.module)} · engine v${i.engine || '?'} · ${i.cpus || '?'} jadier`;
}
function setStatusRight(text, err) { const n = $('#st-right'); n.textContent = text || ''; n.style.color = err ? 'var(--err)' : ''; }
function toast(text, kind = 'info', opts = {}) {
  const t = el('div', { class: 'toast ' + kind }, icon(kind === 'ok' ? 'check' : (kind === 'err' || kind === 'warn') ? 'alert' : 'spark'), el('span', { class: 't' }, text));
  if (opts.action) t.append(el('button', { class: 'btn sm', type: 'button', onclick: () => { t.remove(); opts.action.fn(); } }, opts.action.label));
  t.append(el('button', { class: 'ib', type: 'button', title: 'Zavrieť', onclick: () => t.remove() }, icon('x')));
  $('#toasts').append(t);
  setTimeout(() => t.remove(), opts.ms || (kind === 'err' ? 10000 : 4500));
  return t;
}
function openModal({ title, body, foot, onClose }) {
  const m = $('#modal'); $('#modal-title').textContent = title || '';
  const b = $('#modal-body'); b.innerHTML = ''; if (typeof body === 'string') b.innerHTML = body; else if (body) b.append(body);
  const f = $('#modal-foot'); f.innerHTML = ''; for (const x of (foot || [])) f.append(x);
  m.classList.remove('hidden'); S.modalClose = onClose || null;
  const first = b.querySelector('input[type=text], textarea'); if (first) { first.focus(); first.select(); }
}
function closeModal() {
  const m = $('#modal'); if (m.classList.contains('hidden')) return;
  const v = $('video', m); if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
  m.classList.add('hidden'); const f = S.modalClose; S.modalClose = null; if (f) f();
}
function askText({ title, label, value, buttons }) {
  return new Promise((resolve) => {
    let done = false; const finish = (r) => { if (done) return; done = true; closeModal(); resolve(r); };
    const inp = el('input', { type: 'text', value: value || '', placeholder: label || '', spellcheck: 'false' });
    const btns = (buttons || [{ label: 'OK', action: 'ok', primary: true }]).map((bt) => el('button', { class: 'btn' + (bt.primary ? ' primary' : ''), type: 'button', onclick: () => finish({ text: inp.value.trim(), action: bt.action }) }, bt.label));
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); const pb = buttons ? (buttons.find((x) => x.primary) || buttons[0]) : { action: 'ok' }; finish({ text: inp.value.trim(), action: pb.action }); } });
    openModal({ title, body: el('div', {}, label ? el('div', { class: 'hint' }, label) : null, inp), foot: [el('button', { class: 'btn', type: 'button', onclick: () => finish(null) }, 'Zrušiť'), ...btns], onClose: () => finish(null) });
  });
}
function confirmModal(title, text, okLabel, danger) {
  return new Promise((resolve) => {
    let done = false; const finish = (r) => { if (done) return; done = true; closeModal(); resolve(r); };
    openModal({ title, body: el('div', { class: 'hint', style: { fontSize: '12.5px', color: 'var(--fg)' } }, text),
      foot: [el('button', { class: 'btn', type: 'button', onclick: () => finish(false) }, 'Zrušiť'), el('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), type: 'button', onclick: () => finish(true) }, okLabel || 'OK')], onClose: () => finish(false) });
  });
}
function playVideo(rel, title) {
  const v = el('video', { controls: true, autoplay: true, src: fileUrl(rel) });
  openModal({ title: title || normRel(rel).split('/').pop(), body: v, foot: [el('button', { class: 'btn', type: 'button', onclick: () => openFolder(normRel(rel).split('/').slice(0, -1).join('/')) }, icon('folder'), 'Otvoriť priečinok')] });
}
function showImage(rel, title) { showImageSrc(fileUrl(rel), title || normRel(rel).split('/').pop()); }
function showImageSrc(src, title) { openModal({ title: title || '', body: el('img', { class: 'big', src }) }); }
async function openFolder(rel) {
  try { await api.post('/api/open', { rel: rel || '' }); } catch (e) { toast('Priečinok sa nepodarilo otvoriť: ' + e.message, 'err'); }
}

// ------------------------------------------------------------------ name, recipe badge, summary
function updateNamePlaceholder() { $('#name').placeholder = S.tlName || 'Názov úlohy'; $('#recipe-name').placeholder = currentName() || 'Názov receptu'; }
function updateRecipeBadge() {
  const b = $('#recipe-badge'); const cur = S.recipe[S.module];
  if (!cur) { b.classList.add('hidden'); $('#recipe-overwrite').disabled = true; return; }
  const dirty = JSON.stringify(cur.params) !== JSON.stringify(P());
  b.classList.remove('hidden'); b.innerHTML = ''; b.append(icon('doc'), cur.name + (dirty ? ' •' : ''));
  b.title = 'Načítaný recept: ' + cur.name + (dirty ? ' (upravené, neuložené)' : '');
  $('#recipe-overwrite').disabled = false; $('#recipe-overwrite').title = 'Prepísať recept „' + cur.name + '“ aktuálnymi parametrami';
}
function updateRenderSummary() {
  const p = P(); const tl = S.tl; if (!p || !tl) return;
  const [rw, rh] = resOf(); const fps = tl.fps; const frames = Math.round(tl.total * fps);
  const rows = [['Modul', moduleLabel(S.module)], ['Názov', currentName() || '—'], ['Priečinok', (S.tlSlug || '…') + '/vNN'],
    ['Obraz', `${rw}×${rh} · ${fps} fps · CRF ${p.crf}`]];
  if (tl.phases.length > 1) rows.push(['Dĺžka', `video 1: ${fmtS(tl.t1)} · video 2: ${fmtS(tl.total - tl.t1)}`]);
  else rows.push(['Dĺžka', fmtS(tl.total)]);
  rows.push(['Snímky', `${frames} (odhad ${fmtEta(frames / Math.max(1, (S.info.cpus || 4) * 0.9))} pri finále)`]);
  const kv = $('#render-summary'); kv.innerHTML = '';
  for (const [k, v] of rows) kv.append(el('span', {}, k), el('span', { title: v }, v));
}

// ------------------------------------------------------------------ render & jobs
async function render(kind, titles) {
  const body = { module: S.module, params: P(), draft: kind === 'draft', name: currentName(), hash: currentHash() };
  if (titles && titles.length) body.batch = titles;
  try {
    const d = await api.post('/api/render', body); const n = (d.jobs || []).length;
    toast(kind === 'draft' ? 'Rýchly náhľad je vo fronte (960×540).' : (n > 1 ? `${n} úloh je vo fronte.` : 'Finálny render je vo fronte.'), 'ok');
    showTab('render'); pollJobs();
  } catch (e) { toast('Render sa nepodarilo spustiť: ' + e.message, 'err'); }
}
function renderBatch() {
  const lines = $('#batch').value.split('\n').map((s) => s.trim()).filter(Boolean);
  if (!lines.length) { toast('Napíš aspoň jeden titulok, každý na vlastný riadok.', 'warn'); return; }
  render('final', lines);
}
let jobsTimer = null;
async function pollJobs() {
  clearTimeout(jobsTimer);
  try { const d = await api.get('/api/jobs'); S.jobs = d.jobs || []; renderJobs(); } catch (e) { /* server away */ }
  const active = S.jobs.some((j) => j.state === 'queued' || j.state === 'running');
  jobsTimer = setTimeout(pollJobs, active ? 1000 : 5000);
}
const KIND_LABEL = { draft: 'náhľad', final: 'finál' };
const STATE_LABEL = { queued: 'vo fronte', running: 'beží', done: 'hotovo', error: 'chyba', cancelled: 'zrušené' };
function renderJobs() {
  const ul = $('#jobs'); ul.innerHTML = '';
  $('#jobs-empty').classList.toggle('hidden', S.jobs.length > 0);
  $('#jobs-count').textContent = S.jobs.length ? `(${S.jobs.length})` : '';
  const hash = currentHash(); let refreshOut = false;
  for (const j of S.jobs) {
    const prev = S.jobStates[j.id];
    if (prev && prev !== j.state) {
      if (j.state === 'done') {
        const mp4 = (j.files || []).map(normRel).find((f) => f.endsWith('.mp4'));
        toast(`${j.kind === 'draft' ? 'Náhľad' : 'Render'} hotový: ${j.title || j.name}`, 'ok', mp4 ? { action: { label: 'Prehrať', fn: () => playVideo(mp4, j.title || j.name) }, ms: 8000 } : {});
        if (j.kind === 'final') refreshOut = true;
      } else if (j.state === 'error') toast(`Render zlyhal: ${j.title || j.name} · ${j.msg || ''}`, 'err');
    }
    S.jobStates[j.id] = j.state;
    const li = el('li', { class: 'item job ' + j.state });
    const line = el('div', { class: 'line' },
      el('span', { class: 'ttl', title: j.title || j.name }, j.title || j.name || '—'),
      el('span', { class: 'pill ' + (j.kind === 'final' ? 'v1' : '') }, KIND_LABEL[j.kind] || j.kind),
      j.version ? el('span', { class: 'pill' }, j.version) : null,
      j.batch ? el('span', { class: 'pill' }, j.batch) : null,
      (j.hash && j.hash === hash && j.module === S.module) ? el('span', { class: 'pill ok', title: 'Tento render zodpovedá aktuálnym parametrom' }, 'aktuálne') : null);
    const acts = el('span', { class: 'acts' });
    if (j.state === 'queued' || j.state === 'running') acts.append(el('button', { class: 'ib', title: 'Zrušiť', onclick: () => cancelJob(j.id) }, icon('stop')));
    if (j.rel_dir && j.state === 'done') acts.append(el('button', { class: 'ib', title: 'Otvoriť priečinok', onclick: () => openFolder(j.rel_dir) }, icon('folder')));
    line.append(acts); li.append(line);
    const pct = j.total ? Math.round(100 * (j.done || 0) / j.total) : (j.state === 'done' ? 100 : 0);
    li.append(el('div', { class: 'prog' }, el('i', { style: { width: pct + '%' } })));
    const meta = [];
    if (j.res) meta.push(j.res);
    if (j.state === 'running') { meta.push(`${j.done || 0}/${j.total || '?'} snímok`); if (j.rate) meta.push(`${(+j.rate).toFixed(1)} fps`); if (j.eta != null) meta.push('zostáva ' + fmtEta(j.eta)); }
    else if (j.state === 'done') { if (j.sec != null) meta.push(`${Math.round(j.sec)} s`); if (j.rate) meta.push(`${(+j.rate).toFixed(1)} fps`); }
    meta.push(STATE_LABEL[j.state] || j.state);
    if (j.created) meta.push(fmtDate(j.created));
    li.append(el('div', { class: 'meta' }, meta.map((m, i) => el('span', {}, (i ? '· ' : '') + m))));
    if (j.msg && j.state !== 'done') li.append(el('div', { class: 'msg' + (j.state === 'error' ? ' err' : '') }, j.msg));
    if (j.state === 'done' && (j.files || []).length) {
      const files = el('div', { class: 'files' });
      for (const rel0 of j.files) {
        const rel = normRel(rel0); const name = rel.split('/').pop();
        if (rel.endsWith('.mp4')) files.append(el('div', { class: 'file' }, el('button', { class: 'ib', title: 'Prehrať', onclick: () => playVideo(rel, name) }, icon('play')), el('span', { class: 'fn', title: rel }, name)));
        else if (/\.(png|jpg)$/.test(rel)) files.append(el('div', { class: 'file' }, el('img', { class: 'thumb', src: fileUrl(rel), loading: 'lazy', onclick: () => showImage(rel, name) }), el('span', { class: 'fn', title: rel }, name)));
        else if (rel.endsWith('.json')) files.append(el('div', { class: 'file' }, el('button', { class: 'ib', title: 'Načítať recept z tohto renderu', onclick: () => loadRecipeFromFile(rel) }, icon('doc')), el('span', { class: 'fn', title: rel }, name)));
      }
      li.append(files);
    }
    if (j.warnings && j.warnings.length) li.append(el('div', { class: 'msg' }, j.warnings.join(' · ')));
    ul.append(li);
  }
  const act = S.jobs.find((j) => j.state === 'running') || S.jobs.find((j) => j.state === 'queued');
  const sj = $('#st-job'); sj.innerHTML = '';
  if (act) {
    const pct = act.total ? Math.round(100 * (act.done || 0) / act.total) : 0;
    sj.append(el('span', {}, `${act.kind === 'draft' ? 'náhľad' : 'render'}: ${act.title || act.name}`), el('span', { class: 'bar' }, el('i', { style: { width: pct + '%' } })), el('span', {}, act.state === 'running' ? `${pct} %${act.eta != null ? ' · ' + fmtEta(act.eta) : ''}` : 'vo fronte'));
  }
  if (refreshOut) refreshOutputs();
}
async function cancelJob(id) { try { await api.post('/api/cancel', { id }); pollJobs(); } catch (e) { toast(e.message, 'err'); } }
async function clearJobs() { try { const d = await api.post('/api/jobs/clear', {}); S.jobs = d.jobs || []; renderJobs(); } catch (e) { toast(e.message, 'err'); } }

// ------------------------------------------------------------------ outputs
async function refreshOutputs() {
  try { const d = await api.get('/api/outputs'); S.outputs = d.outputs || []; renderOutputs(); } catch (e) { /* ignore */ }
}
function renderOutputs() {
  const ul = $('#outputs'); ul.innerHTML = ''; const list = S.outputs;
  $('#outputs-empty').classList.toggle('hidden', list.length > 0);
  $('#outputs-path').textContent = S.info.out_root || '';
  list.forEach((o, idx) => {
    const open = S.openOut ? S.openOut === o.slug : idx === 0;
    const vers = el('div', { class: 'versions' + (open ? '' : ' hidden') });
    for (const v of o.versions) {
      const vh = el('div', { class: 'vh' }, el('b', {}, v.v), el('span', {}, moduleLabel(v.module)), el('span', { class: 'meta' }, fmtDate(v.mtime)),
        el('span', { class: 'acts' }, el('button', { class: 'ib', title: 'Otvoriť priečinok', onclick: () => openFolder(v.rel) }, icon('folder'))));
      const files = el('div', { class: 'files' });
      for (const f of v.files) files.append(fileRow(f));
      vers.append(el('div', { class: 'version' }, vh, files));
    }
    const head = el('div', { class: 'line' }, icon('chev'), el('span', { class: 'ttl' }, o.slug), el('span', { class: 'meta' }, `${o.versions.length} ${plural(o.versions.length, 'verzia', 'verzie', 'verzií')}`));
    head.addEventListener('click', () => { S.openOut = (open ? '__none__' : o.slug); renderOutputs(); });
    ul.append(el('li', { class: 'item slug' }, head, vers));
  });
}
function fileRow(f) {
  const rel = normRel(f.rel); const row = el('div', { class: 'file' });
  if (f.kind === 'video') row.append(el('button', { class: 'ib', title: 'Prehrať', onclick: () => playVideo(rel, f.name) }, icon('play')), el('span', { class: 'fn', title: f.name }, f.name), f.part ? el('span', { class: 'pill ' + (f.part === 2 ? 'v2' : 'v1') }, 'video ' + f.part) : null, el('span', { class: 'sz' }, fmtBytes(f.size)));
  else if (f.kind === 'image') row.append(el('img', { class: 'thumb', src: fileUrl(rel), loading: 'lazy', onclick: () => showImage(rel, f.name) }), el('span', { class: 'fn', title: f.name }, f.name), el('span', { class: 'sz' }, fmtBytes(f.size)));
  else if (f.kind === 'recipe') row.append(el('button', { class: 'ib', title: 'Načítať recept z tohto renderu', onclick: () => loadRecipeFromFile(rel) }, icon('doc')), el('span', { class: 'fn', title: f.name }, f.name), el('button', { class: 'btn sm', type: 'button', onclick: () => loadRecipeFromFile(rel) }, 'Načítať'));
  else row.append(el('span', { class: 'fn' }, f.name));
  return row;
}

// ------------------------------------------------------------------ recipes
async function refreshRecipes() {
  try { const d = await api.get('/api/recipes'); S.recipes = d.recipes || []; renderRecipes(); } catch (e) { /* ignore */ }
}
function renderRecipes() {
  const ul = $('#recipes'); ul.innerHTML = '';
  $('#recipes-empty').classList.toggle('hidden', S.recipes.length > 0);
  const cur = S.recipe[S.module];
  for (const r of S.recipes) {
    const li = el('li', { class: 'item recipe' + (cur && cur.file === r.file ? ' current' : '') });
    const line = el('div', { class: 'line' },
      r.metal ? el('span', { style: { width: '10px', height: '10px', borderRadius: '50%', background: r.metal, flex: 'none', boxShadow: '0 0 0 1px rgba(255,255,255,.2)' } }) : null,
      el('span', { class: 'ttl', title: r.broken ? 'Súbor sa nedá prečítať' : 'Načítať recept', onclick: () => { if (!r.broken) loadRecipe(r.file); } }, r.name),
      r.module ? el('span', { class: 'pill' }, moduleLabel(r.module)) : el('span', { class: 'pill err' }, 'poškodený'),
      el('span', { class: 'acts' },
        el('button', { class: 'ib', title: 'Načítať', onclick: () => { if (!r.broken) loadRecipe(r.file); } }, icon('download')),
        el('button', { class: 'ib', title: 'Presunúť do koša (recepty/_kos)', onclick: () => trashRecipe(r) }, icon('trash'))));
    li.append(line, el('div', { class: 'meta' }, el('span', {}, r.title || ''), el('span', {}, '· ' + fmtDate(r.saved || r.mtime)), el('span', { title: r.file }, '· ' + r.file)));
    ul.append(li);
  }
}
async function loadRecipe(file) {
  try { const rec = await api.get('/api/recipe?file=' + encodeURIComponent(file)); await applyRecipe(rec); }
  catch (e) { toast('Recept sa nepodarilo načítať: ' + e.message, 'err'); }
}
async function loadRecipeFromFile(rel) {
  try {
    const r = await fetch(fileUrl(rel), { cache: 'no-store' }); if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json(); if (!d || typeof d.params !== 'object') throw new Error('súbor neobsahuje parametre');
    await applyRecipe({ name: d.name || normRel(rel).split('/').pop(), module: d.module || S.module, params: d.params });
  } catch (e) { toast('Recept sa nepodarilo načítať: ' + e.message, 'err'); }
}
async function applyRecipe(rec) {
  const mod = rec.module && S.modules.some((m) => m.id === rec.module) ? rec.module : S.module;
  if (mod !== S.module) await switchModule(mod);
  const full = sanitize(mod, rec.params || {});
  edit(() => setParams(full));
  S.recipe[mod] = rec.file ? { file: rec.file, name: rec.name, params: clone(P()) } : null;
  updateRecipeBadge(); renderRecipes(); save();
  toast(`Recept „${rec.name}“ načítaný${mod !== rec.module ? '' : ''}. Ctrl+Z vráti predchádzajúce nastavenie.`, 'ok');
}
async function saveRecipe(name, overwriteFile) {
  const body = { name: name || currentName() || moduleLabel(S.module), module: S.module, params: P() };
  if (overwriteFile) { body.file = overwriteFile; body.overwrite = true; }
  try {
    const d = await api.post('/api/recipe', body);
    S.recipe[S.module] = { file: d.file, name: d.name, params: clone(P()) };
    updateRecipeBadge(); refreshRecipes(); save();
    toast(`Recept „${d.name}“ uložený (${d.file}).`, 'ok', { action: { label: 'Priečinok', fn: () => openFolder('@recipes') } });
  } catch (e) { toast('Recept sa nepodarilo uložiť: ' + e.message, 'err'); }
}
async function saveRecipeFlow() {
  const cur = S.recipe[S.module];
  const r = await askText({ title: 'Uložiť recept', label: 'Recept je kompletné nastavenie modulu „' + moduleLabel(S.module) + '“, ku ktorému sa dá vrátiť.', value: (cur && cur.name) || currentName(),
    buttons: cur ? [{ label: 'Prepísať „' + cur.name + '“', action: 'overwrite' }, { label: 'Uložiť ako nový', action: 'new', primary: true }] : [{ label: 'Uložiť', action: 'new', primary: true }] });
  if (!r) return;
  await saveRecipe(r.text, r.action === 'overwrite' && cur ? cur.file : null);
}
async function trashRecipe(r) {
  if (!(await confirmModal('Presunúť recept do koša', `Recept „${r.name}“ (${r.file}) sa presunie do priečinka recepty/_kos. Dá sa odtiaľ ručne vrátiť.`, 'Do koša', true))) return;
  try {
    await api.post('/api/recipe/delete', { file: r.file });
    if (S.recipe[S.module] && S.recipe[S.module].file === r.file) { S.recipe[S.module] = null; updateRecipeBadge(); }
    refreshRecipes(); toast('Recept je v koši.', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
function exportRecipe() {
  const name = currentName() || moduleLabel(S.module);
  const doc = { name, module: S.module, params: P(), saved: new Date().toISOString().slice(0, 19).replace('T', ' '), app: S.info.app };
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
  const a = el('a', { href: URL.createObjectURL(blob), download: name.replace(/[^\w\-]+/g, '_').replace(/^_+|_+$/g, '') + '.json' });
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
function importRecipe(file) {
  const rd = new FileReader();
  rd.onload = async () => {
    try {
      const d = JSON.parse(String(rd.result)); if (!d || typeof d.params !== 'object') throw new Error('súbor neobsahuje parametre');
      await applyRecipe({ name: d.name || file.name.replace(/\.json$/i, ''), module: d.module || S.module, params: d.params });
    } catch (e) { toast('Import zlyhal: ' + e.message, 'err'); }
  };
  rd.readAsText(file);
}

// ------------------------------------------------------------------ stills, tabs, persistence
async function saveStill() {
  try {
    const d = await api.post('/api/still', { module: S.module, params: P(), t: S.t });
    const rel = normRel(d.file);
    toast('Snímka uložená: ' + rel.split('/').pop(), 'ok', { action: { label: 'Otvoriť', fn: () => showImage(rel) }, ms: 8000 });
  } catch (e) { toast('Snímka sa nepodarila: ' + e.message, 'err'); }
}
function showTab(id) {
  S.tab = id;
  for (const b of $$('#tabs button')) b.classList.toggle('on', b.dataset.tab === id);
  for (const t of $$('.panel .tab')) t.classList.toggle('hidden', t.id !== 'tab-' + id);
  if (id === 'outputs') refreshOutputs();
  if (id === 'recipes') { refreshRecipes(); updateNamePlaceholder(); }
  if (id === 'ai' && window.AI && window.AI.onShow) window.AI.onShow();
  save();
}
const save = debounce(() => {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ project: S.project, module: S.module, params: S.params, names: S.names, tPer: { ...S.tPer, [S.module]: S.t },
      tab: S.tab, openGroups: S.openGroups, quality: S.quality, recipe: S.recipe }));
  } catch (e) { /* storage may be unavailable */ }
}, 300);
function restore() {
  try {
    const d = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); if (!d || typeof d !== 'object') return;
    if (typeof d.project === 'string') S.project = d.project;
    if (typeof d.module === 'string') S.module = d.module;
    if (d.params && typeof d.params === 'object') S.params = d.params;
    if (d.names && typeof d.names === 'object') S.names = d.names;
    if (d.tPer && typeof d.tPer === 'object') S.tPer = d.tPer;
    if (typeof d.tab === 'string') S.tab = d.tab;
    if (d.openGroups && typeof d.openGroups === 'object') S.openGroups = d.openGroups;
    if (['auto', 'fast', 'fine'].includes(d.quality)) S.quality = d.quality;
    if (d.recipe && typeof d.recipe === 'object') S.recipe = d.recipe;
  } catch (e) { /* ignore */ }
}

// ------------------------------------------------------------------ wiring
function bindUI() {
  $('#filter').addEventListener('input', applyFilter);
  $('#reset-all').addEventListener('click', async () => {
    if (await confirmModal('Predvolené hodnoty', `Všetky parametre modulu „${moduleLabel(S.module)}“ sa vrátia na predvolené hodnoty. Ctrl+Z to vráti.`, 'Vrátiť na predvolené')) { edit(() => setParams(M().defaults)); toast('Parametre sú na predvolených hodnotách.', 'ok'); }
  });
  $('#undo').addEventListener('click', undo); $('#redo').addEventListener('click', redo);
  $('#save-recipe').addEventListener('click', saveRecipeFlow);
  $('#btn-draft').addEventListener('click', () => render('draft')); $('#btn-draft2').addEventListener('click', () => render('draft'));
  $('#btn-final').addEventListener('click', () => render('final')); $('#btn-final2').addEventListener('click', () => render('final'));
  $('#btn-batch').addEventListener('click', renderBatch);
  $('#project').addEventListener('change', () => switchProject($('#project').value).catch((e) => toast(e.message, 'err')));
  $('#project-new').addEventListener('click', newProjectFlow);
  $('#project-assets').addEventListener('click', assetFlow);
  $('#jobs-clear').addEventListener('click', clearJobs);
  $('#name').addEventListener('input', () => { S.names[S.module] = $('#name').value; updateRenderSummary(); save(); });
  $('#name').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#name').blur(); });
  $('#play').addEventListener('click', () => (S.playing ? stop() : play()));
  $('#to-start').addEventListener('click', () => { stop(); setTime(0, { exact: true }); });
  $('#to-end').addEventListener('click', () => { stop(); setTime(S.tl ? S.tl.total : 0, { exact: true }); });
  $('#prev-marker').addEventListener('click', () => { stop(); gotoMarker(-1); });
  $('#next-marker').addEventListener('click', () => { stop(); gotoMarker(1); });
  const sc = $('#scrub');
  sc.addEventListener('input', () => { stop(); setTime(+sc.value, { interactive: true }); });
  sc.addEventListener('change', () => requestFrame({ exact: true }));
  $('#quality').value = S.quality;
  $('#quality').addEventListener('change', () => { S.quality = $('#quality').value; requestFrame({ exact: true }); save(); });
  $('#still').addEventListener('click', saveStill);
  for (const b of $$('#tabs button')) b.addEventListener('click', () => showTab(b.dataset.tab));
  $('#outputs-open').addEventListener('click', () => openFolder('')); $('#outputs-refresh').addEventListener('click', refreshOutputs);
  $('#recipes-open').addEventListener('click', () => openFolder('@recipes')); $('#recipes-refresh').addEventListener('click', refreshRecipes);
  $('#recipe-save').addEventListener('click', () => { saveRecipe($('#recipe-name').value.trim() || currentName()); $('#recipe-name').value = ''; });
  $('#recipe-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#recipe-save').click(); });
  $('#recipe-overwrite').addEventListener('click', () => { const c = S.recipe[S.module]; if (c) saveRecipe(c.name, c.file); });
  $('#recipe-export').addEventListener('click', exportRecipe);
  $('#recipe-import').addEventListener('click', () => $('#recipe-file').click());
  $('#recipe-file').addEventListener('change', () => { const f = $('#recipe-file').files[0]; if (f) importRecipe(f); $('#recipe-file').value = ''; });
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal').addEventListener('pointerdown', (e) => { if (e.target === $('#modal')) closeModal(); });
  for (const id of ['#tl-ruler', '#tl-phases', '#tl-markers', '#strip']) bindScrub($(id));
  document.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b && !b.closest('#popover')) b.blur(); });
  document.addEventListener('keydown', onKey);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.addEventListener('beforeunload', () => save.now());
  const ro = new ResizeObserver(() => { fitViewer(); layoutTimeline(); scheduleExact(); refreshStrip(); });
  ro.observe($('#viewer')); ro.observe($('#tl-phases'));
  showTab(S.tab);
}
function onKey(e) {
  const tgt = e.target; const tag = tgt && tgt.tagName;
  const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (tgt && tgt.isContentEditable);
  if (e.key === 'Escape') { closeModal(); closePopover(); if (typing && tag !== 'SELECT') tgt.blur(); return; }
  if ((e.ctrlKey || e.metaKey) && !e.altKey) {
    const k = e.key.toLowerCase();
    if (k === 's') { e.preventDefault(); saveRecipeFlow(); return; }
    if (typing && tag !== 'SELECT' && tgt.type !== 'range') return;
    if (k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (k === 'y') { e.preventDefault(); redo(); return; }
    return;
  }
  if (typing && tgt.type !== 'range') return;
  if (!$('#modal').classList.contains('hidden')) return;
  const fps = S.tl ? S.tl.fps : 30; const total = S.tl ? S.tl.total : 0;
  switch (e.key) {
    case ' ': e.preventDefault(); if (S.playing) stop(); else play(); break;
    case 'ArrowLeft': if (tgt.type === 'range') return; e.preventDefault(); stop(); setTime(S.t - (e.shiftKey ? 1 : 1 / fps), { exact: true }); break;
    case 'ArrowRight': if (tgt.type === 'range') return; e.preventDefault(); stop(); setTime(S.t + (e.shiftKey ? 1 : 1 / fps), { exact: true }); break;
    case 'Home': e.preventDefault(); stop(); setTime(0, { exact: true }); break;
    case 'End': e.preventDefault(); stop(); setTime(total, { exact: true }); break;
    case ',': stop(); gotoMarker(-1); break;
    case '.': stop(); gotoMarker(1); break;
    default: break;
  }
}

async function applyParams(module, params) {
  if (module && module !== S.module) await switchModule(module);
  let n = 0; edit(() => { n = setParams(params || {}).length; });
  return n;
}

async function boot() {
  restore();
  bindUI();
  try {
    try { await switchModule(S.module, true); }
    catch (e) {
      // remembered project may be gone: start over on the default project
      S.project = ''; S.mod = {};
      await switchModule(S.module, true);
    }
  } catch (e) {
    $('#frame-empty').textContent = 'Server neodpovedá: ' + e.message;
    toast('Nepodarilo sa načítať schému: ' + e.message, 'err', { ms: 20000 });
    return;
  }
  pollJobs(); refreshRecipes(); refreshOutputs();
  readyResolve(true);
}

window.App = { S, api, $, $$, el, icon, toast, openModal, closeModal, confirmModal, askText, fmtDate, fmtBytes, fmtS, P, M, specOf, moduleLabel,
  currentName, applyParams, switchModule, switchProject, showTab, save, playVideo, showImage, showImageSrc, openFolder, ready };
boot();
})();
