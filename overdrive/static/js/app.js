/* ===== Overdrive — interface ===== */
'use strict';

// ---------- Utilitaires ----------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fmtBytes(n, d = 1) {
  if (n == null || isNaN(n)) return '—';
  const u = ['o', 'Ko', 'Mo', 'Go', 'To'];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i ? n.toFixed(d) : Math.round(n)} ${u[i]}`;
}
const fmtRate = (n) => (n == null ? '—' : fmtBytes(n) + '/s');
const fmtPct = (n) => (n == null ? '—' : `${Math.round(n)} %`);
const fmtTemp = (n) => (n == null ? '—' : `${Math.round(n)} °C`);
function fmtDur(s) {
  s = Math.floor(s || 0);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} j ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : 'jamais');
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

// Icônes (style trait, 24x24)
const P = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  game: '<path d="M6 11h4M8 9v4M15 12h.01M18 10h.01"/><path d="M17.3 5H6.7a4 4 0 0 0-4 3.6l-.7 6.2A3 3 0 0 0 5 18c1 0 1.6-.5 2.1-1.1L8.5 15h7l1.4 1.9c.5.6 1.1 1.1 2.1 1.1a3 3 0 0 0 3-3.2l-.7-6.2A4 4 0 0 0 17.3 5z"/>',
  zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  ram: '<rect x="2" y="7" width="20" height="10" rx="1.5"/><path d="M6 11v2M10 11v2M14 11v2M18 11v2M6 17v3M18 17v3"/>',
  gpu: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="9" cy="12" r="3"/><path d="M15 10h3M15 14h3"/>',
  wifi: '<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><circle cx="12" cy="19.5" r=".8"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M4 19h16"/>',
  shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  refresh: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M3 21v-5h5"/>',
  power: '<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1zM12 15l-3-3a22 22 0 0 1 2-4A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22.4 22.4 0 0 1-4 2z"/><path d="M9 12H4s.6-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.6 4-2c1.1-1.6 0-5 0-5"/>',
  broom: '<path d="m19 5-7 7M9.5 12.5l2 2M4 20c0-3 1.5-6.5 5.5-7.5l2 2C10.5 18.5 7 20 4 20z"/><path d="M14 21c1-2 1.5-4 1-6"/>',
  disk: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/>',
  thermo: '<path d="M14 14.8V4a2 2 0 0 0-4 0v10.8a4 4 0 1 0 4 0z"/>',
  battery: '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 11v2"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  remote: '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
  play: '<path d="m6 4 14 8-14 8z"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>',
  server: '<rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M6 7h.01M6 17h.01"/>',
};
const icon = (n) => `<svg viewBox="0 0 24 24">${P[n] || P.info}</svg>`;

// ---------- API ----------
let TOKEN = '';
try {
  const q = new URLSearchParams(location.search).get('token');
  if (q) { localStorage.setItem('od_token', q); history.replaceState(null, '', location.pathname + location.hash); }
  TOKEN = localStorage.getItem('od_token') || '';
} catch (e) { /* stockage indisponible */ }

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(TOKEN ? { 'X-Token': TOKEN } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* réponse vide */ }
  if (res.status === 401 && data?.auth) { askToken(); throw new Error('Jeton requis'); }
  if (!res.ok) throw new Error(data?.error || `Erreur ${res.status}`);
  return data;
}
const post = (p, body = {}) => api(p, { method: 'POST', body });

async function runJob(jobId, onUpdate) {
  for (;;) {
    const j = await api(`/api/jobs/${jobId}`);
    onUpdate && onUpdate(j);
    if (j.status === 'done') return j.result;
    if (j.status === 'error') throw new Error(j.error);
    await sleep(700);
  }
}

function progressBox(el, label = 'En cours…') {
  el.innerHTML = `<div class="stack" style="gap:8px"><div class="row between small"><span class="pmsg muted">${esc(label)}</span><span class="ppct faint"></span></div>
    <div class="progress indeterminate"><div style="width:0%"></div></div></div>`;
  return (j) => {
    const bar = $('.progress', el); if (!bar) return;
    if (j.message) $('.pmsg', el).textContent = j.message;
    if (j.progress > 0) { bar.classList.remove('indeterminate'); bar.firstElementChild.style.width = `${Math.round(j.progress * 100)}%`; $('.ppct', el).textContent = `${Math.round(j.progress * 100)} %`; }
  };
}

async function startJob(path, body, el, label) {
  const upd = progressBox(el, label);
  const { job } = await post(path, body);
  return runJob(job, upd);
}

// ---------- UI : toasts, modal, actions ----------
function toast(msg, type = 'info') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `${icon(type === 'ok' ? 'check' : type === 'bad' ? 'alert' : 'info')}<div>${esc(msg)}</div>`;
  $('#toasts').appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 300); }, type === 'bad' ? 6500 : 4000);
}

function modal(html) {
  const m = $('#modal');
  $('#modalCard').innerHTML = html;
  m.hidden = false;
  return m;
}
function closeModal() { $('#modal').hidden = true; }
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });

function confirmBox(title, text, ok = 'Confirmer', danger = false) {
  return new Promise((resolve) => {
    modal(`<h3>${esc(title)}</h3><p>${esc(text)}</p><div class="row" style="justify-content:flex-end">
      <button class="btn btn-ghost" data-r="0">Annuler</button><button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-r="1">${esc(ok)}</button></div>`);
    $$('#modalCard [data-r]').forEach((b) => b.onclick = () => { closeModal(); resolve(b.dataset.r === '1'); });
  });
}

function askToken() {
  if (!$('#modal').hidden) return;
  modal(`<h3>Accès à distance</h3><p>Saisissez le jeton d'accès affiché dans Overdrive sur le PC (Paramètres → Accès à distance).</p>
    <input type="password" id="tokIn" placeholder="Jeton" style="width:100%"><div class="row mt" style="justify-content:flex-end">
    <button class="btn btn-primary" id="tokOk">Se connecter</button></div>`);
  $('#tokOk').onclick = () => { TOKEN = $('#tokIn').value.trim(); try { localStorage.setItem('od_token', TOKEN); } catch (e) {} closeModal(); boot(); };
}

/** Exécute une action en désactivant le bouton, avec toasts de succès/erreur. */
async function act(btn, fn, okMsg) {
  const orig = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = `${icon('refresh')}<span>Patientez…</span>`; }
  try {
    const r = await fn();
    if (okMsg) toast(typeof okMsg === 'function' ? okMsg(r) : okMsg, 'ok');
    return r;
  } catch (e) {
    toast(e.message, 'bad');
    throw e;
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
  }
}
const safe = (p) => p.catch(() => {});

function copyText(text) {
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(
    () => toast('Copié dans le presse-papiers', 'ok'),
    () => { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); toast('Copié', 'ok'); });
}

// ---------- Composants ----------
const C = 2 * Math.PI * 40;
function gaugeHTML(id) {
  return `<div class="gauge" id="${id}"><svg viewBox="0 0 92 92"><defs><linearGradient id="${id}-g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>
    <circle class="track" cx="46" cy="46" r="40" fill="none"/><circle class="bar" cx="46" cy="46" r="40" fill="none" stroke="url(#${id}-g)"
    stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}"/></svg><div class="val">—</div></div>`;
}
function setGauge(id, pct, label) {
  const g = document.getElementById(id); if (!g) return;
  const bar = $('.bar', g);
  const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  bar.style.strokeDashoffset = C * (1 - v / 100);
  bar.setAttribute('stroke', v >= 90 ? 'var(--bad)' : v >= 75 ? 'var(--warn)' : `url(#${id}-g)`);
  $('.val', g).innerHTML = label ?? (pct == null ? '—' : `${Math.round(pct)}<small>%</small>`);
}

function sparkline(canvas, series, { max, color = '#22d3ee', color2, fill = true } = {}) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const draw = (data, col, doFill) => {
    const pts = data.map((v) => v ?? 0);
    if (pts.length < 2) return;
    const m = max ?? Math.max(...pts, 1) * 1.15;
    const step = w / (120 - 1);
    const off = w - (pts.length - 1) * step;
    ctx.beginPath();
    pts.forEach((v, i) => { const x = off + i * step, y = h - 2 - (v / m) * (h - 6); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = col; ctx.lineWidth = 1.8; ctx.lineJoin = 'round'; ctx.stroke();
    if (doFill) {
      ctx.lineTo(w, h); ctx.lineTo(off, h); ctx.closePath();
      const gr = ctx.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, col + '55'); gr.addColorStop(1, col + '00');
      ctx.fillStyle = gr; ctx.fill();
    }
  };
  if (Array.isArray(series[0]) || series[0] === undefined && Array.isArray(series[1])) {
    draw(series[0] || [], color, fill); draw(series[1] || [], color2 || '#8b5cf6', false);
  } else draw(series, color, fill);
}

const sw = (checked, attrs = '') => `<label class="switch"><input type="checkbox" ${checked ? 'checked' : ''} ${attrs}><span></span></label>`;
const chip = (t, cls = '') => `<span class="chip ${cls}">${esc(t)}</span>`;
const card = (title, body, { ico, sub, extra = '', cls = '' } = {}) => `<div class="card ${cls}">
  ${title ? `<div class="card-head">${ico ? `<div class="card-icon">${icon(ico)}</div>` : ''}<h3>${esc(title)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</h3>${extra}</div>` : ''}${body}</div>`;
const notice = (text, type = '') => `<div class="notice ${type}">${icon(type === 'info' ? 'info' : 'alert')}<div>${text}</div></div>`;
const empty = (text, ico = 'info') => `<div class="empty">${icon(ico)}<div>${esc(text)}</div></div>`;
const tabs = (items, active) => `<div class="tabs">${items.map(([id, l]) => `<button data-tab="${id}" class="${id === active ? 'active' : ''}">${esc(l)}</button>`).join('')}</div>`;
function bindTabs(root, handler) {
  $$('.tabs button', root).forEach((b) => b.onclick = () => {
    $$('.tabs button', root).forEach((x) => x.classList.toggle('active', x === b));
    handler(b.dataset.tab);
  });
}
const winOnly = () => (INFO && !INFO.windows ? notice('Mode démonstration : Overdrive tourne sur un système non-Windows. Les actions Windows sont désactivées, le monitoring fonctionne.', 'info') : '');

// ---------- État global ----------
let INFO = null;
let STATS = null;
const HIST = { cpu: [], ram: [], gpu: [], net_down: [], net_up: [], cpu_temp: [], gpu_temp: [] };
let current = null;
let pageTimers = [];
const every = (ms, fn) => pageTimers.push(setInterval(fn, ms));

// ---------- Pages ----------
const PAGES = [
  { id: 'dashboard', title: 'Tableau de bord', sub: 'Vue d\'ensemble de votre PC en temps réel', ico: 'dashboard', section: 'Pilotage' },
  { id: 'gaming', title: 'Mode Jeu · CS2', sub: 'Profil compétitif, Game Boost et configuration Counter-Strike 2', ico: 'game' },
  { id: 'tweaks', title: 'Optimisations', sub: 'Réglages Windows pour les performances — tous réversibles', ico: 'zap' },
  { id: 'clean', title: 'Nettoyage', sub: 'Fichiers temporaires, caches et résidus', ico: 'broom', section: 'Entretien' },
  { id: 'files', title: 'Fichiers inutiles', sub: 'Gros fichiers, doublons, vieux téléchargements', ico: 'folder' },
  { id: 'processes', title: 'Processus & démarrage', sub: 'Applications en arrière-plan, démarrage et services', ico: 'cpu' },
  { id: 'network', title: 'Réseau', sub: 'Latence, DNS et pile TCP/IP', ico: 'wifi' },
  { id: 'updates', title: 'Mises à jour', sub: 'Windows, pilotes ASUS / NVIDIA et applications', ico: 'download', section: 'Système' },
  { id: 'backup', title: 'Sauvegarde & maintenance', sub: 'Points de restauration, copies de dossiers, réparation', ico: 'shield' },
  { id: 'supervision', title: 'Supervision · Nexus', sub: 'Export des métriques, alertes et journal', ico: 'activity' },
  { id: 'settings', title: 'Paramètres & accès distant', sub: 'Pilotage depuis le téléphone, automatisations', ico: 'settings' },
];
const R = {};

function buildNav() {
  $('#nav').innerHTML = PAGES.map((p) => `${p.section ? `<div class="nav-section">${p.section}</div>` : ''}
    <a href="#/${p.id}" data-page="${p.id}">${icon(p.ico)}<span>${esc(p.title)}</span></a>`).join('');
}

async function route() {
  const id = (location.hash.replace('#/', '') || 'dashboard').split('?')[0];
  const page = PAGES.find((p) => p.id === id) || PAGES[0];
  pageTimers.forEach(clearInterval); pageTimers = [];
  current = page;
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.page === page.id));
  $('#pageTitle').textContent = page.title;
  $('#pageSub').textContent = page.sub;
  $('#sidebar').classList.remove('open'); $('#backdrop').classList.remove('open');
  const view = $('#view');
  view.innerHTML = '<div class="grid g3"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
  window.scrollTo(0, 0);
  try { await R[page.id](view); } catch (e) { view.innerHTML = card('', empty(`Erreur : ${e.message}`, 'alert')); }
}

// ===== Tableau de bord =====
R.dashboard = async (v) => {
  const st = INFO.static || {};
  v.innerHTML = `${winOnly()}<div class="grid g4" style="margin-top:${INFO.windows ? 0 : 16}px">
    <div class="card glow span2"><div class="health"><div class="health-ring" id="healthRing">
      <svg viewBox="0 0 132 132"><defs><linearGradient id="hg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>
      <circle cx="66" cy="66" r="56" fill="none" stroke="var(--surface-3)" stroke-width="11"/>
      <circle id="hbar" cx="66" cy="66" r="56" fill="none" stroke="url(#hg)" stroke-width="11" stroke-linecap="round" stroke-dasharray="${2 * Math.PI * 56}" stroke-dashoffset="${2 * Math.PI * 56}" style="transition:stroke-dashoffset .8s"/></svg>
      <div class="val"><b id="hscore">—</b><span>Santé</span></div></div>
      <div class="grow"><div class="stat-label">${esc(st.model || st.hostname || 'Mon PC')}</div><h2 style="font-size:22px;margin:4px 0 2px" id="htitle">Analyse…</h2>
      <div class="muted small">${esc(st.cpu_name || '')} · ${fmtBytes(st.ram_total, 0)} RAM · ${esc(st.os || '')}</div>
      <ul class="issues" id="hissues"></ul></div></div>
      <div class="grid g4 mini mt" style="gap:10px;margin-top:18px">
        <div class="tip"><div class="stat-label">Allumé depuis</div><b id="mUp" style="margin:4px 0 0">—</b></div>
        <div class="tip"><div class="stat-label">Processus</div><b id="mProcs" style="margin:4px 0 0">—</b></div>
        <div class="tip"><div class="stat-label">Températures</div><b id="mTemps" style="margin:4px 0 0">—</b></div>
        <div class="tip"><div class="stat-label">Alimentation</div><b id="mPower" style="margin:4px 0 0">—</b></div>
      </div></div>
    <div class="card span2"><div class="card-head"><div class="card-icon">${icon('rocket')}</div><h3>Actions rapides<span class="sub">Un clic, résultat immédiat</span></h3></div>
      <div class="quick">
        <button data-q="quick"><div class="qi">${icon('zap')}</div><b>Optimisation express</b><span>Nettoyage + RAM + DNS</span></button>
        <button data-q="boost"><div class="qi">${icon('game')}</div><b>Game Boost</b><span>Avant une partie de CS2</span></button>
        <button data-q="ram"><div class="qi">${icon('ram')}</div><b>Libérer la RAM</b><span>Purge standby + working sets</span></button>
        <button data-q="clean"><div class="qi">${icon('broom')}</div><b>Nettoyer</b><span>Analyser les fichiers inutiles</span></button>
        <button data-q="updates"><div class="qi">${icon('download')}</div><b>Mises à jour</b><span>Windows, pilotes, applis</span></button>
        <button data-q="restore"><div class="qi">${icon('shield')}</div><b>Point de restauration</b><span>Sécuriser avant les réglages</span></button>
      </div><div id="quickOut" class="mt" hidden></div></div>

    <div class="card"><div class="gauge-card">${gaugeHTML('gCpu')}<div class="gauge-info"><div class="stat-label">Processeur</div>
      <div class="name" id="cpuFreq">—</div><div class="small muted">${st.cores ?? '—'} cœurs · ${st.threads ?? '—'} threads</div></div></div>
      <div class="mt"><div class="kv"><span>Température</span><span id="cpuTemp">—</span></div></div>
      <canvas class="spark" id="sCpu"></canvas><div class="cores" id="cores"></div></div>
    <div class="card"><div class="gauge-card">${gaugeHTML('gRam')}<div class="gauge-info"><div class="stat-label">Mémoire vive</div>
      <div class="name" id="ramUsed">—</div><div class="small muted" id="ramTotal">—</div></div></div>
      <div class="mt"><div class="kv"><span>Disponible</span><span id="ramFree">—</span></div><div class="kv"><span>Fichier d'échange</span><span id="ramSwap">—</span></div></div>
      <canvas class="spark" id="sRam"></canvas></div>
    <div class="card"><div class="gauge-card">${gaugeHTML('gGpu')}<div class="gauge-info"><div class="stat-label">Carte graphique</div>
      <div class="name ellipsis" id="gpuName">—</div><div class="small muted" id="gpuTemp">—</div></div></div>
      <div class="mt"><div class="kv"><span>VRAM</span><span id="gpuMem">—</span></div><div class="kv"><span>Consommation</span><span id="gpuPow">—</span></div></div>
      <canvas class="spark" id="sGpu"></canvas></div>
    <div class="card"><div class="card-head"><div class="card-icon">${icon('wifi')}</div><h3>Réseau</h3></div>
      <div class="row between"><div><div class="stat-label">Réception</div><div class="stat-value" style="font-size:22px" id="netDown">—</div></div>
      <div style="text-align:right"><div class="stat-label">Envoi</div><div class="stat-value" style="font-size:22px;color:var(--accent-2)" id="netUp">—</div></div></div>
      <canvas class="spark" id="sNet" style="height:78px"></canvas></div>

    <div class="card span2"><div class="card-head"><div class="card-icon">${icon('disk')}</div><h3>Stockage</h3><span class="chip" id="diskIo">—</span></div><div id="disks" class="stack" style="gap:14px"></div></div>
    <div class="card span2"><div class="card-head"><div class="card-icon">${icon('cpu')}</div><h3>Processus les plus gourmands</h3>
      <a class="btn btn-sm btn-ghost" href="#/processes">Tout voir</a></div><div id="topProcs"></div></div>
  </div>`;

  $$('[data-q]', v).forEach((b) => b.onclick = () => quickAction(b.dataset.q, b));
  const h = await api('/api/history');
  Object.keys(HIST).forEach((k) => { HIST[k] = h[k] || []; });
  R.dashboard.onStats = updateDashboard;
  if (STATS?.ts) updateDashboard(STATS);
};

function updateDashboard(s) {
  if (!$('#gCpu')) return;
  const hs = s.health || { score: 100, issues: [] };
  const HC = 2 * Math.PI * 56;
  $('#hbar').style.strokeDashoffset = HC * (1 - hs.score / 100);
  $('#hscore').textContent = hs.score;
  $('#htitle').textContent = hs.score >= 90 ? 'Votre PC est en pleine forme' : hs.score >= 70 ? 'Quelques points à améliorer' : 'Votre PC a besoin d\'attention';
  $('#hissues').innerHTML = hs.issues.length ? hs.issues.map((i) => `<li>${icon('alert')}<span>${esc(i)}</span></li>`).join('')
    : `<li style="color:var(--ok)">${icon('check').replace('<svg', '<svg style="color:var(--ok)"')}<span>Aucun problème détecté</span></li>`;

  $('#mUp').textContent = fmtDur(s.uptime);
  $('#mProcs').textContent = s.processes?.count ?? '—';
  $('#mTemps').textContent = s.cpu.temp == null && s.gpu.temp == null ? 'non dispo.' : `${fmtTemp(s.cpu.temp)} · ${fmtTemp(s.gpu.temp)}`;
  $('#mPower').textContent = s.battery ? `${s.battery.plugged ? 'Secteur' : 'Batterie'} · ${Math.round(s.battery.percent)} %` : 'Secteur';
  setGauge('gCpu', s.cpu.percent);
  $('#cpuFreq').textContent = s.cpu.freq ? `${(s.cpu.freq / 1000).toFixed(2)} GHz` : '—';
  $('#cpuTemp').textContent = fmtTemp(s.cpu.temp);
  $('#cores').innerHTML = (s.cpu.per_core || []).map((c) => `<div class="core" title="${Math.round(c)} %"><div style="height:${c}%"></div></div>`).join('');
  setGauge('gRam', s.ram.percent);
  $('#ramUsed').textContent = `${fmtBytes(s.ram.used)} utilisés`;
  $('#ramTotal').textContent = `sur ${fmtBytes(s.ram.total, 0)}`;
  $('#ramFree').textContent = fmtBytes(s.ram.available);
  $('#ramSwap').textContent = fmtPct(s.ram.swap_percent);
  const g = (s.gpus || [])[0];
  setGauge('gGpu', s.gpu.percent);
  $('#gpuName').textContent = (s.gpu.name || 'Non détectée').replace(/NVIDIA GeForce |Laptop GPU/g, '').trim();
  $('#gpuName').title = s.gpu.name || '';
  $('#gpuTemp').textContent = s.gpu.temp != null ? `${fmtTemp(s.gpu.temp)}` : (g?.driver ? `Pilote ${g.driver}` : 'Température indisponible');
  $('#gpuMem').textContent = g && g.mem_total ? `${fmtBytes(g.mem_used)} / ${fmtBytes(g.mem_total, 0)}` : '—';
  $('#gpuPow').textContent = g && g.power ? `${Math.round(g.power)} W` : '—';
  $('#netDown').textContent = fmtRate(s.net.down);
  $('#netUp').textContent = fmtRate(s.net.up);
  $('#diskIo').textContent = `Lecture ${fmtRate(s.disk_io.read)} · Écriture ${fmtRate(s.disk_io.write)}`;
  $('#disks').innerHTML = (s.disks || []).map((d) => `<div><div class="row between small"><b>${esc(d.mount)}</b>
    <span class="muted">${fmtBytes(d.free)} libres sur ${fmtBytes(d.total, 0)}</span></div>
    <div class="bar-line mt" style="margin-top:6px"><div style="width:${d.percent}%;${d.percent > 90 ? 'background:var(--bad)' : ''}"></div></div></div>`).join('') || empty('Aucun disque');
  const procs = s.processes?.by_cpu || [];
  $('#topProcs').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Processus</th><th class="num">CPU</th><th class="num">RAM</th></tr></thead><tbody>
    ${procs.slice(0, 6).map((p) => `<tr><td class="ellipsis" style="max-width:260px">${esc(p.name)}</td><td class="num">${p.cpu.toFixed(1)} %</td><td class="num">${fmtBytes(p.mem)}</td></tr>`).join('')}
    </tbody></table></div>`;
  sparkline($('#sCpu'), HIST.cpu, { max: 100 });
  sparkline($('#sRam'), HIST.ram, { max: 100, color: '#8b5cf6' });
  sparkline($('#sGpu'), HIST.gpu, { max: 100, color: '#34d399' });
  sparkline($('#sNet'), [HIST.net_down, HIST.net_up], { color: '#22d3ee', color2: '#8b5cf6' });
}

async function quickAction(q, btn) {
  const out = $('#quickOut');
  if (q === 'clean') return (location.hash = '#/clean');
  if (q === 'updates') return (location.hash = '#/updates');
  if (q === 'restore') {
    out.hidden = false;
    return safe(act(btn, () => startJob('/api/backup/restore-point', { description: 'Overdrive - point manuel' }, out, 'Création du point de restauration…'),
      'Point de restauration créé').finally(() => { out.hidden = true; }));
  }
  if (q === 'ram') return safe(act(btn, () => post('/api/ram/optimize'), (r) => `${fmtBytes(r.freed)} de RAM libérés`));
  if (q === 'boost') return safe(act(btn, () => post('/api/gaming/boost'), (r) => `Game Boost : ${r.killed.length} appli(s) fermée(s)${r.ram?.freed ? `, ${fmtBytes(r.ram.freed)} libérés` : ''}`));
  if (q === 'quick') {
    out.hidden = false;
    return safe(act(btn, async () => {
      const r = await startJob('/api/quick-optimize', {}, out, 'Optimisation express…');
      out.innerHTML = `<div class="row"><span class="chip ok">${icon('check')} Terminé</span><span class="small muted">${fmtBytes(r.clean.freed)} de fichiers supprimés${r.ram?.freed != null ? ` · ${fmtBytes(r.ram.freed)} de RAM libérés` : ''}</span></div>`;
      return r;
    }, 'Optimisation express terminée'));
  }
}

// ===== Mode Jeu / CS2 =====
R.gaming = async (v) => {
  const [st, inf, auto] = await Promise.all([api('/api/gaming'), api('/api/gaming/info'), api('/api/gaming/autoexec')]);
  const cs2 = st.cs2 || {};
  const active = st.profile_active;
  v.innerHTML = `${winOnly()}<div class="stack" style="margin-top:${INFO.windows ? 0 : 16}px">
    <div class="hero"><div class="hero-badge">CS2</div>
      <div class="row" style="gap:8px;margin-bottom:12px">${cs2.installed ? chip('CS2 détecté', 'ok') : chip('CS2 non trouvé', 'warn')}
        ${st.running ? chip('En cours d\'exécution', 'acc') : ''}${active ? chip('Profil compétitif actif', 'ok') : chip('Profil inactif')}
        ${st.priority_set ? chip('Priorité haute permanente', 'ok') : ''}${st.gpu_pref_set ? chip('GPU dédié forcé', 'ok') : ''}</div>
      <h2>Counter-Strike 2 — Mode compétitif</h2>
      <p>Le profil applique en un clic : plan Performances ultimes, Mode Jeu, capture Game DVR coupée, priorité multimédia des jeux,
      bridage réseau désactivé, souris 1:1, HAGS, CS2 en priorité CPU haute et forcé sur le GPU NVIDIA. Tout est annulable.</p>
      ${st.on_battery ? notice('Votre portable est <b>sur batterie</b> : branchez le secteur, sinon le GPU est bridé et les FPS chutent fortement.') + '<div style="height:14px"></div>' : ''}
      <div class="row">
        ${active ? `<button class="btn btn-lg" id="profOff">${icon('undo')}Désactiver le profil</button>` : `<button class="btn btn-primary btn-lg" id="profOn">${icon('rocket')}Activer le profil CS2</button>`}
        <button class="btn btn-lg" id="boostBtn">${icon('zap')}Game Boost maintenant</button>
      </div>
      ${cs2.installed ? `<div class="tiny faint mt">${esc(cs2.game_dir)}</div>` : ''}
    </div>

    <div class="grid g2">
      ${card('Options de lancement Steam', `<div class="codebox"><code>${esc(inf.launch_options)}</code><button class="btn btn-sm" id="copyLaunch">${icon('copy')}Copier</button></div>
        <div class="small muted mt">Steam → Bibliothèque → clic droit sur CS2 → Propriétés → Options de lancement.</div>
        <ul class="issues">${inf.launch_notes.map((n) => `<li>${icon('info')}<span>${esc(n)}</span></li>`).join('')}</ul>`, { ico: 'play' })}
      ${card('Game Boost — applications fermées', `<p class="small muted" style="margin-top:0">Ces programmes sont fermés avant de jouer (un par ligne). Ils libèrent CPU, RAM, disque et réseau.</p>
        <textarea id="killList" style="min-height:150px">${esc(inf.kill_list.join('\n'))}</textarea>
        <div class="row mt"><button class="btn" id="saveKill">${icon('save')}Enregistrer la liste</button></div>`, { ico: 'x' })}
    </div>

    ${card('autoexec.cfg', `<textarea id="autoexec" spellcheck="false">${esc(auto.content)}</textarea>
      <div class="row mt"><button class="btn btn-primary" id="saveAuto" ${cs2.installed ? '' : 'disabled'}>${icon('save')}Enregistrer dans CS2</button>
      <button class="btn" id="backupCfg" ${cs2.installed ? '' : 'disabled'}>${icon('shield')}Sauvegarder ma config CS2</button>
      <span class="small muted grow">${auto.path ? esc(auto.path) : 'CS2 introuvable : installez-le pour enregistrer.'}${auto.exists ? '' : ' (nouveau fichier)'}</span></div>`,
      { ico: 'save', sub: 'Configuration chargée à chaque lancement (avec +exec autoexec). Une copie .bak est créée à chaque enregistrement.' })}

    <div class="grid g2">
      ${card('Réglages vidéo recommandés (FPS)', `<div class="table-wrap"><table><thead><tr><th>Paramètre</th><th>Valeur</th><th class="hide-sm">Pourquoi</th></tr></thead><tbody>
        ${inf.video_settings.map((r) => `<tr><td>${esc(r.setting)}</td><td class="strong">${esc(r.value)}</td><td class="muted small hide-sm">${esc(r.why)}</td></tr>`).join('')}</tbody></table></div>`, { ico: 'gpu' })}
      ${card('Spécial ASUS TUF', `<div class="stack" style="gap:10px">${inf.tuf_tips.map((t) => `<div class="tip"><b>${esc(t.title)}</b><p>${esc(t.text)}</p></div>`).join('')}</div>`, { ico: 'rocket', sub: 'Les plus gros gains de FPS sur portable' })}
    </div></div>`;

  const on = $('#profOn'), off = $('#profOff');
  if (on) on.onclick = () => safe(act(on, () => post('/api/gaming/profile'), 'Profil CS2 activé — redémarrez le PC pour HAGS').then(() => route()));
  if (off) off.onclick = () => safe(act(off, () => api('/api/gaming/profile', { method: 'DELETE' }), 'Profil désactivé, réglages d\'origine restaurés').then(() => route()));
  $('#boostBtn').onclick = (e) => safe(act(e.currentTarget, () => post('/api/gaming/boost'),
    (r) => `Game Boost : ${r.killed.length ? r.killed.join(', ') + ' fermé(s)' : 'aucune appli à fermer'}${r.ram?.freed ? ` · ${fmtBytes(r.ram.freed)} RAM libérés` : ''}`));
  $('#copyLaunch').onclick = () => copyText(inf.launch_options);
  $('#saveKill').onclick = (e) => safe(act(e.currentTarget, () => api('/api/gaming/kill-list', { method: 'PUT', body: { kill_list: $('#killList').value.split(/\n|,/) } }), 'Liste enregistrée'));
  $('#saveAuto').onclick = (e) => safe(act(e.currentTarget, () => post('/api/gaming/autoexec', { content: $('#autoexec').value }), 'autoexec.cfg enregistré'));
  $('#backupCfg').onclick = (e) => safe(act(e.currentTarget, () => post('/api/gaming/backup-config'), (r) => `Sauvegarde créée : ${r.path}`));
};

// ===== Optimisations =====
R.tweaks = async (v) => {
  const [list, plans] = await Promise.all([api('/api/tweaks'), api('/api/power-plans')]);
  const cats = [...new Set(list.map((t) => t.category))];
  const applied = list.filter((t) => t.applied).length;
  v.innerHTML = `${winOnly()}<div class="stack" style="margin-top:${INFO.windows ? 0 : 16}px">
    <div class="grid g3">
      <div class="card glow"><div class="stat-label">Optimisations actives</div><div class="big-number">${applied} / ${list.length}</div>
        <div class="small muted">Chaque réglage mémorise la valeur d'origine.</div></div>
      <div class="card span2"><div class="card-head"><div class="card-icon">${icon('shield')}</div><h3>Appliquer en toute sécurité<span class="sub">Créez un point de restauration, puis appliquez les réglages recommandés.</span></h3></div>
        <div class="row"><button class="btn" id="rp">${icon('shield')}Point de restauration</button>
        <button class="btn btn-primary" id="rec">${icon('zap')}Appliquer les recommandés</button>
        <button class="btn btn-danger" id="revAll">${icon('undo')}Tout restaurer</button></div><div id="rpOut" class="mt" hidden></div></div>
    </div>
    ${plans.length ? card('Plan d\'alimentation', `<div class="row"><select id="plan" class="grow" style="max-width:420px">${plans.map((p) => `<option value="${p.guid}" ${p.active ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
      <button class="btn" id="setPlan">Activer</button></div>`, { ico: 'power', sub: 'Sur portable ASUS, combinez avec le mode Turbo d\'Armoury Crate / G-Helper.' }) : ''}
    ${cats.map((c) => card(c, `<div class="list">${list.filter((t) => t.category === c).map((t) => `<div class="item">
      <div class="item-body"><div class="item-title">${esc(t.name)} ${t.recommended ? chip('Recommandé', 'acc') : ''}
        ${chip(t.impact)} ${t.risk === 'moyen' ? chip('à tester', 'warn') : ''} ${t.reboot ? chip('redémarrage', 'warn') : ''}</div>
        <div class="item-desc">${esc(t.description)}</div></div>
      ${sw(t.applied, `data-tw="${t.id}" ${INFO.windows ? '' : 'disabled'}`)}</div>`).join('')}</div>`, { ico: c === 'Jeu' ? 'game' : c === 'Réseau' ? 'wifi' : c === 'Performance' ? 'zap' : 'settings' })).join('')}
  </div>`;
  $$('[data-tw]', v).forEach((inp) => inp.onchange = async () => {
    const id = inp.dataset.tw, on = inp.checked;
    inp.disabled = true;
    try {
      const r = await post(`/api/tweaks/${id}/${on ? 'apply' : 'revert'}`);
      toast(`${on ? 'Appliqué' : 'Annulé'}${r.reboot ? ' — redémarrage nécessaire' : ''}`, 'ok');
    } catch (e) { inp.checked = !on; toast(e.message, 'bad'); }
    inp.disabled = false;
  });
  $('#rec').onclick = async (e) => {
    if (!await confirmBox('Appliquer les optimisations recommandées ?', 'Les réglages marqués « Recommandé » seront appliqués. Vous pourrez tout annuler à tout moment.', 'Appliquer')) return;
    safe(act(e.currentTarget, () => post('/api/tweaks/recommended'), (r) => `${r.filter((x) => !x.error).length} optimisation(s) appliquée(s)`).then(() => route()));
  };
  $('#revAll').onclick = async (e) => {
    if (!await confirmBox('Tout restaurer ?', 'Toutes les modifications faites par Overdrive (registre, services, plan d\'alimentation) seront remises à leur valeur d\'origine.', 'Restaurer', true)) return;
    safe(act(e.currentTarget, () => post('/api/tweaks/revert-all'), (r) => `${r.reverted} réglage(s) restauré(s)`).then(() => route()));
  };
  $('#rp').onclick = (e) => { const o = $('#rpOut'); o.hidden = false; safe(act(e.currentTarget, () => startJob('/api/backup/restore-point', { description: 'Overdrive - avant optimisations' }, o, 'Création du point de restauration…'), 'Point de restauration créé').finally(() => { o.hidden = true; })); };
  if ($('#setPlan')) $('#setPlan').onclick = (e) => safe(act(e.currentTarget, () => post('/api/power-plans', { guid: $('#plan').value }), (r) => `Plan actif : ${r.name}`));
};

// ===== Nettoyage =====
R.clean = async (v) => {
  const cats = await api('/api/clean/categories');
  v.innerHTML = `<div class="grid g3">
    <div class="card glow"><div class="stat-label">Espace récupérable</div><div class="big-number" id="cTotal">—</div>
      <div class="small muted" id="cSub">Lancez une analyse pour estimer l'espace libérable.</div>
      <div class="row mt"><button class="btn btn-primary" id="cScan">${icon('search')}Analyser</button><button class="btn" id="cRun" disabled>${icon('broom')}Nettoyer la sélection</button></div>
      <div id="cProg" class="mt"></div>
      ${INFO.windows ? `<button class="btn btn-ghost btn-sm mt" id="cleanmgr">${icon('link')}Nettoyage de disque Windows (fichiers système)</button>` : ''}</div>
    <div class="card span2"><div class="card-head"><div class="card-icon">${icon('broom')}</div><h3>Catégories<span class="sub">Seuls des fichiers sans valeur sont concernés : vos documents, mots de passe et historiques ne sont jamais touchés.</span></h3>
      <button class="btn btn-sm btn-ghost" id="cAll">Tout cocher</button></div>
      <div class="list">${cats.map((c) => `<div class="item"><label class="check"><input type="checkbox" data-c="${c.id}" ${c.default ? 'checked' : ''}></label>
        <div class="item-body"><div class="item-title">${esc(c.label)} ${c.warning ? chip('attention', 'warn') : ''}</div>
        <div class="item-desc">${esc(c.description)}${c.warning ? ` <span style="color:var(--warn)">${esc(c.warning)}</span>` : ''}</div></div>
        <div class="strong" data-size="${c.id}" style="font-variant-numeric:tabular-nums">—</div></div>`).join('')}</div></div></div>`;
  const sel = () => $$('[data-c]:checked', v).map((i) => i.dataset.c);
  let scanned = null;
  const refreshTotal = () => {
    if (!scanned) return;
    const tot = scanned.categories.filter((c) => sel().includes(c.id)).reduce((a, c) => a + c.size, 0);
    $('#cTotal').textContent = fmtBytes(tot);
    $('#cSub').textContent = `Sélection : ${sel().length} catégorie(s) · total analysé ${fmtBytes(scanned.total)}`;
  };
  $$('[data-c]', v).forEach((i) => i.onchange = refreshTotal);
  $('#cAll').onclick = () => { $$('[data-c]', v).forEach((i) => { i.checked = true; }); refreshTotal(); };
  $('#cScan').onclick = (e) => safe(act(e.currentTarget, async () => {
    scanned = await startJob('/api/clean/scan', {}, $('#cProg'), 'Analyse…');
    $('#cProg').innerHTML = '';
    scanned.categories.forEach((c) => { const el = $(`[data-size="${c.id}"]`); if (el) el.textContent = c.size ? fmtBytes(c.size) : '0'; });
    $('#cRun').disabled = false;
    refreshTotal();
  }));
  $('#cRun').onclick = async (e) => {
    const ids = sel();
    if (!ids.length) return toast('Sélectionnez au moins une catégorie', 'warn');
    if (!await confirmBox('Nettoyer ?', `${ids.length} catégorie(s) seront nettoyées. Les fichiers en cours d'utilisation sont ignorés.`, 'Nettoyer')) return;
    safe(act(e.currentTarget, async () => {
      const r = await startJob('/api/clean/run', { ids }, $('#cProg'), 'Nettoyage…');
      $('#cProg').innerHTML = `<span class="chip ok">${icon('check')} ${fmtBytes(r.freed)} libérés · ${r.deleted} fichiers</span>`;
      $('#cTotal').textContent = fmtBytes(r.freed); $('#cSub').textContent = 'Espace libéré lors de ce nettoyage.';
      $$('[data-size]', v).forEach((el) => { if (ids.includes(el.dataset.size)) el.textContent = '✓'; });
      return r;
    }, (r) => `${fmtBytes(r.freed)} libérés`));
  };
  if ($('#cleanmgr')) $('#cleanmgr').onclick = () => safe(act(null, () => post('/api/open/cleanmgr')));
};

// ===== Fichiers inutiles =====
R.files = async (v) => {
  const roots = await api('/api/files/roots');
  v.innerHTML = `${tabs([['large', 'Gros fichiers'], ['duplicates', 'Doublons'], ['downloads', 'Vieux téléchargements'], ['empty', 'Dossiers vides']], 'large')}<div id="fBody"></div>`;
  const show = (kind) => {
    const opts = {
      large: { label: 'Taille minimale (Mo)', key: 'min_mb', def: 500, desc: 'Repérez les fichiers volumineux oubliés (vidéos, ISO, archives, installateurs).' },
      duplicates: { label: 'Taille minimale (Mo)', key: 'min_mb', def: 1, desc: 'Fichiers au contenu strictement identique (comparaison par empreinte). Le plus ancien de chaque groupe est conservé.' },
      downloads: { label: 'Non ouverts depuis (jours)', key: 'days', def: 90, desc: 'Fichiers du dossier Téléchargements ni modifiés ni ouverts depuis longtemps.' },
      empty: { desc: 'Dossiers vides laissés par des désinstallations.' },
    }[kind];
    $('#fBody').innerHTML = card('', `<p class="muted" style="margin-top:0">${esc(opts.desc)}</p>
      <div class="row">${kind !== 'downloads' ? `<label class="field grow" style="max-width:520px">Dossiers à analyser (séparés par ;)<input type="text" id="fRoots" value="${esc((kind === 'large' ? roots : roots.slice(0, 1)).join(';'))}"></label>` : ''}
      ${opts.key ? `<label class="field" style="width:200px">${esc(opts.label)}<input type="number" id="fParam" value="${opts.def}" min="0"></label>` : ''}
      <button class="btn btn-primary" id="fScan" style="align-self:flex-end">${icon('search')}Analyser</button></div>
      <div id="fProg" class="mt"></div><div id="fRes" class="mt"></div>`);
    $('#fScan').onclick = (e) => safe(act(e.currentTarget, async () => {
      const body = { roots: ($('#fRoots')?.value || '').split(';').map((s) => s.trim()).filter(Boolean) };
      if (opts.key) body[opts.key] = Number($('#fParam').value);
      const r = await startJob(`/api/files/${kind}`, body, $('#fProg'), 'Analyse des fichiers…');
      $('#fProg').innerHTML = '';
      renderFiles(kind, r);
    }));
  };
  bindTabs(v, show);
  show('large');
};

function renderFiles(kind, r) {
  const box = $('#fRes');
  let rows = [];
  if (kind === 'duplicates') {
    if (!r.groups.length) return (box.innerHTML = empty('Aucun doublon trouvé', 'check'));
    box.innerHTML = `<div class="row between mb"><b>${r.groups.length} groupe(s) · ${fmtBytes(r.wasted)} gaspillés</b><button class="btn btn-danger" id="fTrash">${icon('trash')}Envoyer la sélection à la corbeille</button></div>
      <div class="stack scroll-y" style="gap:10px">${r.groups.map((g) => `<div class="tip"><b>${fmtBytes(g.size)} × ${g.paths.length}</b>
      ${g.paths.map((p, i) => `<label class="check small" style="display:flex;margin-top:4px"><input type="checkbox" data-p="${esc(p)}" ${i ? 'checked' : ''}><span class="ellipsis">${esc(p)}</span>${i ? '' : chip('conservé (plus ancien)', 'ok')}</label>`).join('')}</div>`).join('')}</div>`;
  } else if (kind === 'empty') {
    rows = r.folders;
    if (!rows.length) return (box.innerHTML = empty('Aucun dossier vide', 'check'));
    box.innerHTML = `<div class="row between mb"><b>${rows.length} dossier(s) vide(s)</b><button class="btn btn-danger" id="fTrash">${icon('trash')}Supprimer la sélection</button></div>
      <div class="table-wrap scroll-y"><table><tbody>${rows.map((p) => `<tr><td style="width:30px"><input type="checkbox" data-p="${esc(p)}" checked></td><td class="small">${esc(p)}</td></tr>`).join('')}</tbody></table></div>`;
  } else {
    rows = r.files;
    if (!rows.length) return (box.innerHTML = empty('Rien trouvé avec ces critères', 'check'));
    box.innerHTML = `<div class="row between mb"><b>${rows.length} fichier(s) · ${fmtBytes(r.total)}</b><button class="btn btn-danger" id="fTrash">${icon('trash')}Envoyer la sélection à la corbeille</button></div>
      <div class="table-wrap scroll-y"><table><thead><tr><th></th><th>Fichier</th><th class="num">Taille</th><th class="num hide-sm">Dernière ouverture</th></tr></thead><tbody>
      ${rows.map((f) => `<tr><td style="width:30px"><input type="checkbox" data-p="${esc(f.path)}"></td><td class="small" style="word-break:break-all">${esc(f.path)}</td>
      <td class="num strong">${fmtBytes(f.size)}</td><td class="num small muted hide-sm">${fmtDate(Math.max(f.atime, f.mtime))}</td></tr>`).join('')}</tbody></table></div>`;
  }
  $('#fTrash').onclick = async (e) => {
    const paths = $$('[data-p]:checked', box).map((i) => i.dataset.p);
    if (!paths.length) return toast('Rien de sélectionné', 'warn');
    if (!await confirmBox('Envoyer à la corbeille ?', `${paths.length} élément(s). Vous pourrez les récupérer depuis la corbeille.`, 'Confirmer', true)) return;
    safe(act(e.currentTarget, async () => {
      const res = await post('/api/files/trash', { paths });
      res.trashed.forEach((p) => { const i = $$('[data-p]', box).find((x) => x.dataset.p === p); if (i) i.closest('tr, label').remove(); });
      return res;
    }, (res) => `${res.trashed.length} élément(s) à la corbeille (${fmtBytes(res.size)})${res.errors.length ? ` · ${res.errors.length} erreur(s)` : ''}`));
  };
}

// ===== Processus, démarrage, services =====
R.processes = async (v) => {
  v.innerHTML = `${winOnly()}<div style="margin-top:${INFO.windows ? 0 : 16}px">${tabs([['procs', 'Processus'], ['startup', 'Démarrage'], ['services', 'Services']], 'procs')}</div><div id="pBody"></div>`;
  const show = (t) => { pageTimers.forEach(clearInterval); pageTimers = []; ({ procs: procTab, startup: startupTab, services: servicesTab })[t]($('#pBody')); };
  bindTabs(v, show);
  show('procs');
};

let procSort = { key: 'cpu', dir: -1 };
async function procTab(el) {
  el.innerHTML = `<div class="stack"><div class="grid g3" id="bloatBox"></div>
    <div class="card"><div class="row mb"><input type="search" id="pSearch" placeholder="Rechercher un processus…" class="grow" style="max-width:360px">
      <label class="check small"><input type="checkbox" id="pBloat"> Seulement les applis inutiles</label><span class="grow"></span>
      <button class="btn" id="pRam">${icon('ram')}Libérer la RAM</button></div>
      <div class="table-wrap scroll-y" style="max-height:620px"><table><thead><tr>
        <th class="sort" data-k="name">Nom</th><th class="sort num" data-k="pid">PID</th><th class="sort num" data-k="cpu">CPU</th><th class="sort num" data-k="mem">RAM</th>
        <th class="hide-sm">Priorité</th><th></th></tr></thead><tbody id="pRows"></tbody></table></div></div></div>`;
  let data = [];
  const draw = () => {
    if (document.activeElement && document.activeElement.tagName === 'SELECT' && el.contains(document.activeElement)) return;
    const q = $('#pSearch').value.toLowerCase(), onlyBloat = $('#pBloat').checked;
    const rows = data.filter((p) => (!q || p.name.toLowerCase().includes(q) || String(p.pid).includes(q)) && (!onlyBloat || p.bloat))
      .sort((a, b) => (a[procSort.key] > b[procSort.key] ? 1 : a[procSort.key] < b[procSort.key] ? -1 : 0) * procSort.dir).slice(0, 250);
    $('#pRows').innerHTML = rows.map((p) => `<tr><td><div class="row" style="gap:6px;flex-wrap:nowrap"><span class="ellipsis" style="max-width:280px" title="${esc(p.exe)}">${esc(p.name)}</span>
      ${p.critical ? chip('système') : ''}${p.bloat ? `<span class="chip warn" title="${esc(p.bloat)}">inutile en jeu</span>` : ''}</div></td>
      <td class="num muted">${p.pid}</td><td class="num">${p.cpu.toFixed(1)} %</td><td class="num">${fmtBytes(p.mem)}</td>
      <td class="hide-sm"><select data-prio="${p.pid}" ${p.critical ? 'disabled' : ''} style="padding:4px 8px;font-size:12px">${['idle', 'below', 'normal', 'above', 'high'].map((l) => `<option value="${l}" ${p.priority === l ? 'selected' : ''}>${{ idle: 'Basse', below: 'Inférieure', normal: 'Normale', above: 'Supérieure', high: 'Haute' }[l]}</option>`).join('')}</select></td>
      <td class="num">${p.critical ? '' : `<button class="btn btn-sm btn-danger" data-kill="${p.pid}" data-name="${esc(p.name)}">Fermer</button>`}</td></tr>`).join('');
    const bloat = Object.values(data.filter((p) => p.bloat).reduce((a, p) => { (a[p.name] ||= { ...p, mem: 0, n: 0 }).mem += p.mem; a[p.name].n++; return a; }, {}));
    $('#bloatBox').hidden = !bloat.length;
    $('#bloatBox').innerHTML = bloat.length ? `<div class="card span3"><div class="card-head"><div class="card-icon">${icon('alert')}</div><h3>${bloat.length} application(s) inutile(s) en arrière-plan<span class="sub">Elles consomment des ressources pendant vos parties.</span></h3>
      <button class="btn btn-primary btn-sm" id="killBloat">Tout fermer</button></div><div class="row">${bloat.map((b) => `<span class="chip">${esc(b.bloat)} · ${fmtBytes(b.mem)}</span>`).join('')}</div></div>` : '';
    if ($('#killBloat')) $('#killBloat').onclick = (e) => safe(act(e.currentTarget, () => post('/api/gaming/boost', { kill: true }), (r) => `${r.killed.length} appli(s) fermée(s)`).then(load));
  };
  const load = async () => { try { data = await api('/api/processes'); draw(); } catch (e) { /* réessai au prochain tick */ } };
  $('#pSearch').oninput = draw; $('#pBloat').onchange = draw;
  $$('th.sort', el).forEach((th) => th.onclick = () => { const k = th.dataset.k; procSort = { key: k, dir: procSort.key === k ? -procSort.dir : (k === 'name' ? 1 : -1) }; draw(); });
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-kill]'); if (!b) return;
    if (!await confirmBox(`Fermer ${b.dataset.name} ?`, 'Les données non enregistrées de cette application seront perdues.', 'Fermer', true)) return;
    safe(act(b, () => post(`/api/processes/${b.dataset.kill}/kill`), `${b.dataset.name} fermé`).then(load));
  });
  el.addEventListener('change', (e) => {
    const s = e.target.closest('[data-prio]'); if (!s) return;
    safe(act(null, () => post(`/api/processes/${s.dataset.prio}/priority`, { level: s.value }), 'Priorité modifiée'));
    s.blur();
  });
  $('#pRam').onclick = (e) => safe(act(e.currentTarget, () => post('/api/ram/optimize'), (r) => `${fmtBytes(r.freed)} de RAM libérés${r.note ? ' — ' + r.note : ''}`));
  await load();
  every(3000, load);
}

async function startupTab(el) {
  const items = await api('/api/startup');
  el.innerHTML = card('Programmes lancés au démarrage', items.length ? `<div class="list">${items.map((i) => `<div class="item">
    <div class="item-body"><div class="item-title">${esc(i.name)} ${chip(i.scope)}</div><div class="item-desc ellipsis" title="${esc(i.command)}">${esc(i.command)}</div></div>
    ${sw(i.enabled, `data-st="${esc(i.id)}"`)}</div>`).join('')}</div>` : empty(INFO.windows ? 'Aucun programme au démarrage' : 'Disponible sous Windows'),
  { ico: 'power', sub: 'Désactiver un programme accélère le démarrage de Windows. Même méthode que le Gestionnaire des tâches : réactivable à tout moment.' });
  $$('[data-st]', el).forEach((inp) => inp.onchange = async () => {
    try { await post('/api/startup', { id: inp.dataset.st, enabled: inp.checked }); toast(inp.checked ? 'Activé au démarrage' : 'Désactivé au démarrage', 'ok'); } catch (e) { inp.checked = !inp.checked; toast(e.message, 'bad'); }
  });
}

async function servicesTab(el) {
  const list = await api('/api/services');
  const lbl = { Automatic: 'Automatique', Manual: 'Manuel', Disabled: 'Désactivé' };
  el.innerHTML = card('Services Windows non essentiels', list.length ? `<div class="list">${list.map((s) => `<div class="item">
    <div class="item-body"><div class="item-title">${esc(s.label)} <span class="faint small mono">${esc(s.name)}</span>
      ${chip(s.status === 'Running' ? 'en cours' : 'arrêté', s.status === 'Running' ? 'acc' : '')} ${chip(lbl[s.start_type] || s.start_type)} ${s.risk === 'moyen' ? chip('selon usage', 'warn') : ''}</div>
      <div class="item-desc">${esc(s.description)} Conseillé : <b>${lbl[s.advised]}</b>.</div></div>
    ${s.revertible ? `<button class="btn btn-sm" data-sv="${s.name}" data-a="restore">${icon('undo')}Restaurer</button>` : ''}
    ${s.optimized ? chip('optimisé', 'ok') : `<button class="btn btn-sm btn-primary" data-sv="${s.name}" data-a="optimize">Optimiser</button>`}</div>`).join('')}</div>` : empty('Disponible sous Windows'),
  { ico: 'server', sub: 'Liste prudente : seuls des services sans impact sur la stabilité sont proposés. L\'état d\'origine est sauvegardé.' });
  $$('[data-sv]', el).forEach((b) => b.onclick = () => safe(act(b, () => post(`/api/services/${b.dataset.sv}/${b.dataset.a}`), 'Service mis à jour').then(() => servicesTab(el))));
}

// ===== Réseau =====
R.network = async (v) => {
  const n = await api('/api/network');
  const current = (n.dns[0]?.servers || []).join(', ');
  const provider = Object.entries(n.providers).find(([, p]) => p.servers[0] === n.dns[0]?.servers?.[0])?.[0];
  v.innerHTML = `${winOnly()}<div class="stack" style="margin-top:${INFO.windows ? 0 : 16}px"><div class="grid g3">
    <div class="card span2"><div class="card-head"><div class="card-icon">${icon('activity')}</div><h3>Test de latence<span class="sub">Ping, gigue et perte de paquets vers les services de jeu</span></h3>
      <button class="btn btn-primary" id="lat">${icon('play')}Lancer le test</button></div><div id="latOut">${empty('Lancez un test pour mesurer la qualité de votre connexion', 'wifi')}</div></div>
    <div class="card"><div class="card-head"><div class="card-icon">${icon('wifi')}</div><h3>Débit en direct</h3></div>
      <div class="row between"><div><div class="stat-label">Réception</div><div class="stat-value" style="font-size:22px" id="nDown">—</div></div>
      <div style="text-align:right"><div class="stat-label">Envoi</div><div class="stat-value" style="font-size:22px;color:var(--accent-2)" id="nUp">—</div></div></div>
      <canvas class="spark" id="nSpark" style="height:80px"></canvas></div></div>
    <div class="grid g2">
      ${card('Serveurs DNS', `<p class="small muted" style="margin-top:0">Actuel : <b>${esc(current || 'inconnu')}</b>${n.dns[0] ? ` (${esc(n.dns[0].interface)})` : ''}. Un DNS rapide accélère la navigation et la connexion aux services Steam (n'influe pas sur le ping en jeu).</p>
        <div class="stack" style="gap:8px">${Object.entries(n.providers).map(([id, p]) => `<div class="row between tip" style="padding:10px 12px"><div><b style="display:inline">${esc(p.label)}</b> <span class="faint small mono">${p.servers.join(' · ')}</span></div>
        ${provider === id ? chip('actif', 'ok') : `<button class="btn btn-sm" data-dns="${id}">Utiliser</button>`}</div>`).join('')}
        <div class="row"><button class="btn btn-sm btn-ghost" data-dns="auto">${icon('undo')}Revenir au DNS automatique (box)</button></div></div>`, { ico: 'server' })}
      ${card('Outils réseau', `<div class="stack" style="gap:10px">
        <div class="row between tip"><div><b>Vider le cache DNS</b><p>Résout les sites inaccessibles après un changement de serveur.</p></div><button class="btn btn-sm" id="flush">Vider</button></div>
        <div class="row between tip"><div><b>Optimiser TCP/IP</b><p>Auto-tuning normal, RSS actif, heuristiques Windows désactivées.</p></div><button class="btn btn-sm" id="tcp">Appliquer</button></div>
        <div class="row between tip"><div><b>Réinitialiser la pile réseau</b><p>Winsock + TCP/IP : en dernier recours si la connexion est instable. Redémarrage requis.</p></div><button class="btn btn-sm btn-danger" id="reset">Réinitialiser</button></div>
        ${notice('Pour CS2 : privilégiez le <b>câble Ethernet</b>. Les réglages réseau « miracles » (Nagle, etc.) n\'affectent pas CS2, qui utilise UDP ; le ping dépend surtout de la connexion et du Wi-Fi.', 'info')}</div>`, { ico: 'settings' })}
    </div>
    ${card('Cartes réseau', `<div class="grid g3">${n.adapters.map((a) => `<div class="tip"><div class="row between"><b style="margin:0">${esc(a.name)}</b>${chip(a.up ? 'connectée' : 'déconnectée', a.up ? 'ok' : '')}</div>
      <p class="mt" style="margin-top:6px">${esc(a.kind)}${a.speed ? ` · ${a.speed} Mb/s` : ''} · MTU ${a.mtu}<br>${esc(a.ipv4.join(', ') || 'pas d\'IPv4')}</p></div>`).join('')}</div>`, { ico: 'wifi' })}
  </div>`;
  R.network.onStats = (s) => { if (!$('#nDown')) return; $('#nDown').textContent = fmtRate(s.net.down); $('#nUp').textContent = fmtRate(s.net.up); sparkline($('#nSpark'), [HIST.net_down, HIST.net_up], { color: '#22d3ee', color2: '#8b5cf6' }); };
  $('#lat').onclick = (e) => safe(act(e.currentTarget, async () => {
    const r = await startJob('/api/network/latency', {}, $('#latOut'), 'Mesure de la latence…');
    const col = (ms) => (ms == null ? 'var(--bad)' : ms < 30 ? 'var(--ok)' : ms < 70 ? 'var(--warn)' : 'var(--bad)');
    $('#latOut').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Cible</th><th class="num">Moyenne</th><th class="num">Min / Max</th><th class="num">Gigue</th><th class="num">Perte</th></tr></thead><tbody>
      ${r.results.map((x) => `<tr><td><b>${esc(x.label)}</b> <span class="faint small">${esc(x.host)}</span></td><td class="num strong" style="color:${col(x.avg)}">${x.avg ?? '—'} ms</td>
      <td class="num muted">${x.min ?? '—'} / ${x.max ?? '—'}</td><td class="num">${x.jitter} ms</td><td class="num" style="color:${x.loss ? 'var(--bad)' : 'inherit'}">${x.loss} %</td></tr>`).join('')}
      </tbody></table></div><div class="mt">${notice(esc(r.verdict), 'info')}</div>`;
  }));
  $$('[data-dns]', v).forEach((b) => b.onclick = () => safe(act(b, () => post('/api/network/dns', { provider: b.dataset.dns }), 'DNS mis à jour').then(() => route())));
  $('#flush').onclick = (e) => safe(act(e.currentTarget, () => post('/api/network/flush-dns'), 'Cache DNS vidé'));
  $('#tcp').onclick = (e) => safe(act(e.currentTarget, () => post('/api/network/tcp'), 'Paramètres TCP optimisés'));
  $('#reset').onclick = async (e) => {
    if (!await confirmBox('Réinitialiser la pile réseau ?', 'Les paramètres réseau (dont les IP fixes et VPN) seront réinitialisés. Un redémarrage sera nécessaire.', 'Réinitialiser', true)) return;
    safe(act(e.currentTarget, () => post('/api/network/reset'), 'Pile réseau réinitialisée — redémarrez le PC'));
  };
};

// ===== Mises à jour =====
R.updates = async (v) => {
  v.innerHTML = `${winOnly()}<div style="margin-top:${INFO.windows ? 0 : 16}px">${tabs([['windows', 'Windows Update'], ['drivers', 'Pilotes'], ['apps', 'Applications'], ['vendor', 'ASUS & constructeur']], 'windows')}</div><div id="uBody"></div>`;
  const show = (t) => ({ windows: updWindows, drivers: updDrivers, apps: updApps, vendor: updVendor })[t]($('#uBody'));
  bindTabs(v, show);
  show('windows');
};

function updWindows(el) {
  el.innerHTML = card('Windows Update', `<p class="muted" style="margin-top:0">Mises à jour de sécurité, cumulatives et pilotes certifiés (dont ceux fournis par ASUS via Windows Update).</p>
    <div class="row"><button class="btn btn-primary" id="wScan">${icon('search')}Rechercher</button>
    <button class="btn" id="wAll" disabled>${icon('download')}Tout installer</button><button class="btn" id="wDrv" disabled>${icon('gpu')}Installer les pilotes uniquement</button>
    <button class="btn btn-ghost" id="wOpen">${icon('link')}Ouvrir Windows Update</button></div><div id="wOut" class="mt"></div>`, { ico: 'download' });
  $('#wScan').onclick = (e) => safe(act(e.currentTarget, async () => {
    const r = await startJob('/api/updates/windows-scan', { kind: 'all' }, $('#wOut'), 'Recherche…');
    $('#wOut').innerHTML = r.updates.length ? `<div class="table-wrap"><table><thead><tr><th>Mise à jour</th><th>Type</th><th class="num">Taille</th></tr></thead><tbody>
      ${r.updates.map((u) => `<tr><td>${esc(u.title)}</td><td>${u.driver ? chip('pilote', 'acc') : chip(u.severity || 'système')}</td><td class="num">${u.size ? fmtBytes(u.size) : '—'}</td></tr>`).join('')}</tbody></table></div>`
      : `<span class="chip ok">${icon('check')} Windows est à jour</span>`;
    $('#wAll').disabled = !r.updates.length; $('#wDrv').disabled = !r.updates.some((u) => u.driver);
  }));
  const install = (kind) => async (e) => {
    if (!await confirmBox('Installer les mises à jour ?', 'L\'installation peut durer longtemps et nécessiter un redémarrage. Gardez le PC branché.', 'Installer')) return;
    safe(act(e.currentTarget, async () => {
      const r = await startJob('/api/updates/windows-install', { kind }, $('#wOut'), 'Installation…');
      $('#wOut').innerHTML = `<span class="chip ${r.result <= 3 ? 'ok' : 'bad'}">${r.installed} installée(s) — ${esc(r.status)}</span>${r.reboot ? ' ' + chip('redémarrage requis', 'warn') : ''}`;
      return r;
    }, 'Installation terminée'));
  };
  $('#wAll').onclick = install('all'); $('#wDrv').onclick = install('driver');
  $('#wOpen').onclick = () => safe(act(null, () => post('/api/open/windows_update')));
}

async function updDrivers(el) {
  el.innerHTML = card('Pilotes installés', '<div class="skeleton"></div>', { ico: 'gpu' });
  let list = [];
  try { list = await api('/api/updates/drivers'); } catch (e) { el.innerHTML = card('Pilotes installés', empty(e.message), { ico: 'gpu' }); return; }
  const year = new Date().getFullYear();
  el.innerHTML = card('Pilotes installés', `<div class="row mb"><button class="btn btn-primary" id="dScan">${icon('search')}Chercher des pilotes plus récents (Windows Update)</button>
    <button class="btn btn-ghost" id="dDev">${icon('link')}Gestionnaire de périphériques</button></div><div id="dOut" class="mb"></div>
    ${notice('Pour la carte graphique, le pilote le plus récent vient de <b>NVIDIA</b> (application NVIDIA) ; pour le BIOS, le chipset et les pilotes spécifiques TUF, utilisez <b>MyASUS</b> (onglet « ASUS & constructeur »).', 'info')}
    <div class="table-wrap scroll-y mt"><table><thead><tr><th>Périphérique</th><th>Catégorie</th><th>Version</th><th class="num">Date</th></tr></thead><tbody>
    ${list.map((d) => { const old = d.date && year - Number(d.date.slice(0, 4)) >= 3; return `<tr><td>${esc(d.name)}<div class="tiny faint">${esc(d.vendor || '')}</div></td><td>${chip(d.class)}</td><td class="mono">${esc(d.version)}</td>
      <td class="num ${old ? '' : 'muted'}" style="${old ? 'color:var(--warn)' : ''}">${esc(d.date || '—')}</td></tr>`; }).join('')}</tbody></table></div>`, { ico: 'gpu', sub: `${list.length} pilotes · en orange : plus de 3 ans` });
  $('#dScan').onclick = (e) => safe(act(e.currentTarget, async () => {
    const r = await startJob('/api/updates/windows-scan', { kind: 'driver' }, $('#dOut'), 'Recherche de pilotes…');
    $('#dOut').innerHTML = r.updates.length ? `<div class="stack" style="gap:6px">${r.updates.map((u) => `<div class="tip small">${esc(u.title)}</div>`).join('')}
      <button class="btn btn-primary" id="dInst">${icon('download')}Installer ces ${r.updates.length} pilote(s)</button></div>` : `<span class="chip ok">${icon('check')} Aucun nouveau pilote sur Windows Update</span>`;
    if ($('#dInst')) $('#dInst').onclick = (ev) => safe(act(ev.currentTarget, () => startJob('/api/updates/windows-install', { kind: 'driver' }, $('#dOut'), 'Installation des pilotes…'), 'Pilotes installés'));
  }));
  $('#dDev').onclick = () => safe(act(null, () => post('/api/open/devmgmt')));
}

function updApps(el) {
  el.innerHTML = card('Applications (winget)', `<p class="muted" style="margin-top:0">Met à jour Chrome, Discord, Steam, 7-Zip, VLC… via le gestionnaire de paquets officiel de Windows.</p>
    <div class="row"><button class="btn btn-primary" id="aScan">${icon('search')}Rechercher</button><button class="btn" id="aSel" disabled>${icon('download')}Mettre à jour la sélection</button>
    <button class="btn" id="aAll" disabled>Tout mettre à jour</button></div><div id="aOut" class="mt"></div>`, { ico: 'download' });
  $('#aScan').onclick = (e) => safe(act(e.currentTarget, async () => {
    const r = await startJob('/api/updates/apps-scan', {}, $('#aOut'), 'Recherche…');
    $('#aOut').innerHTML = r.apps.length ? `<div class="table-wrap"><table><thead><tr><th></th><th>Application</th><th>Installée</th><th>Disponible</th></tr></thead><tbody>
      ${r.apps.map((a) => `<tr><td style="width:30px"><input type="checkbox" data-app="${esc(a.id)}" checked></td><td>${esc(a.name)}<div class="tiny faint mono">${esc(a.id)}</div></td>
      <td class="mono">${esc(a.version)}</td><td class="mono" style="color:var(--ok)">${esc(a.available)}</td></tr>`).join('')}</tbody></table></div>`
      : `<span class="chip ok">${icon('check')} Toutes vos applications sont à jour</span>`;
    $('#aSel').disabled = $('#aAll').disabled = !r.apps.length;
  }));
  const up = (all) => (e) => safe(act(e.currentTarget, async () => {
    const ids = all ? null : $$('[data-app]:checked', el).map((i) => i.dataset.app);
    const r = await startJob('/api/updates/apps-upgrade', { ids }, $('#aOut'), 'Mise à jour en cours…');
    $('#aOut').innerHTML = r.results ? r.results.map((x) => chip(`${x.id} ${x.ok ? '✓' : '✗'}`, x.ok ? 'ok' : 'bad')).join(' ') : `<pre class="out">${esc(r.output)}</pre>`;
  }, 'Mises à jour terminées'));
  $('#aSel').onclick = up(false); $('#aAll').onclick = up(true);
}

async function updVendor(el) {
  const vd = await api('/api/updates/vendor');
  el.innerHTML = `<div class="grid g2">
    ${card(vd.model || 'Mon PC', `<div class="kv"><span>Fabricant</span><span>${esc(vd.manufacturer || '—')}</span></div><div class="kv"><span>Modèle</span><span>${esc(vd.model || '—')}</span></div>
      <div class="kv"><span>BIOS</span><span class="mono">${esc(vd.bios || '—')}</span></div><div class="kv"><span>Pilote GPU</span><span class="mono">${esc(vd.gpu_driver || '—')}</span></div>
      <div class="mt">${vd.is_asus ? notice('PC ASUS détecté. Mettez à jour le <b>BIOS</b> et les pilotes spécifiques (clavier, ventilateurs, MUX) via <b>MyASUS</b> → Mises à jour système, et les modes de performance via <b>Armoury Crate</b> ou <b>G-Helper</b>.', 'info') : ''}</div>`, { ico: 'cpu', sub: 'Informations constructeur' })}
    ${card('Outils installés', vd.apps.length ? `<div class="stack" style="gap:8px">${vd.apps.map((a) => `<div class="row between tip" style="padding:10px 12px"><b style="margin:0">${esc(a.name)}</b>
      <button class="btn btn-sm" data-app="${esc(a.app_id)}">${icon('play')}Ouvrir</button></div>`).join('')}</div>` : empty(INFO.windows ? 'Aucun outil constructeur détecté (MyASUS, Armoury Crate, NVIDIA…)' : 'Disponible sous Windows'), { ico: 'rocket' })}
    ${card('Liens officiels', `<div class="stack" style="gap:8px">${vd.links.map((l) => `<a class="row between tip" style="padding:10px 12px;text-decoration:none;color:inherit" href="${esc(l.url)}" target="_blank" rel="noopener"><span>${esc(l.label)}</span>${icon('link')}</a>`).join('')}</div>`, { ico: 'link', cls: 'span2' })}
  </div>`;
  $$('[data-app]', el).forEach((b) => b.onclick = () => safe(act(b, () => post('/api/updates/launch', { app_id: b.dataset.app }), 'Application ouverte sur le PC')));
}

// ===== Sauvegarde & maintenance =====
R.backup = async (v) => {
  const [b, tools] = await Promise.all([api('/api/backup'), api('/api/maintenance')]);
  const sources = [...new Set([...(b.config.sources || []), ...b.suggested])];
  v.innerHTML = `${winOnly()}<div class="stack" style="margin-top:${INFO.windows ? 0 : 16}px"><div class="grid g2">
    ${card('Points de restauration', `<div class="row mb"><input type="text" id="rpDesc" placeholder="Description" value="Overdrive - manuel" class="grow">
      <button class="btn btn-primary" id="rpNew">${icon('shield')}Créer</button><button class="btn" id="rpOpen">${icon('undo')}Restaurer…</button></div><div id="rpOut"></div>
      ${b.restore_points.length ? `<div class="table-wrap scroll-y" style="max-height:260px"><table><tbody>${b.restore_points.map((p) => `<tr><td>${esc(p.description)}</td><td class="num muted small">${esc(p.date)}</td></tr>`).join('')}</tbody></table></div>`
        : empty(b.error || (INFO.windows ? 'Aucun point de restauration' : 'Disponible sous Windows'), 'shield')}`, { ico: 'shield', sub: 'Permet d\'annuler n\'importe quelle modification système' })}
    ${card('Sauvegarde des dossiers', `<div class="stack" style="gap:6px;max-height:210px;overflow:auto" id="srcList">${sources.map((s) => `<label class="check small"><input type="checkbox" data-src="${esc(s)}" ${(b.config.sources || []).includes(s) || !b.config.sources.length ? 'checked' : ''}><span class="ellipsis">${esc(s)}</span></label>`).join('')}</div>
      <div class="row mt"><input type="text" id="srcAdd" placeholder="Ajouter un dossier (ex : D:\\Projets)" class="grow"><button class="btn btn-sm" id="srcAddBtn">Ajouter</button></div>
      <label class="field mt">Destination (disque externe, clé USB, NAS…)<input type="text" id="dest" placeholder="E:\\ ou \\\\NAS\\Sauvegardes" value="${esc(b.config.destination)}"></label>
      <div class="row mt"><button class="btn btn-primary" id="bkRun">${icon('save')}Lancer la sauvegarde</button><span class="small muted">Dernière : ${fmtDate(b.last_backup)}</span></div>
      <div id="bkOut" class="mt"></div><div class="tiny faint mt">Copie incrémentale : seuls les fichiers nouveaux ou modifiés sont copiés, rien n'est supprimé de la destination.</div>`, { ico: 'save', sub: 'Documents, bureau, images, configs CS2…' })}
    </div>
    ${card('Maintenance & réparation de Windows', `<div class="grid g3">${tools.map((t) => `<div class="tip"><b>${esc(t.label)}</b><div class="row mt"><button class="btn btn-sm" data-tool="${t.id}">${icon('play')}Lancer</button></div></div>`).join('')}</div>
      <div id="toolOut" class="mt"></div>`, { ico: 'settings', sub: 'Outils intégrés à Windows, exécutés en arrière-plan' })}
  </div>`;
  $('#rpNew').onclick = (e) => safe(act(e.currentTarget, () => startJob('/api/backup/restore-point', { description: $('#rpDesc').value }, $('#rpOut'), 'Création…'), 'Point de restauration créé').then(() => route()));
  $('#rpOpen').onclick = () => safe(act(null, () => post('/api/backup/open-restore')));
  $('#srcAddBtn').onclick = () => {
    const p = $('#srcAdd').value.trim(); if (!p) return;
    $('#srcList').insertAdjacentHTML('beforeend', `<label class="check small"><input type="checkbox" data-src="${esc(p)}" checked><span class="ellipsis">${esc(p)}</span></label>`);
    $('#srcAdd').value = '';
  };
  $('#bkRun').onclick = (e) => safe(act(e.currentTarget, async () => {
    const srcs = $$('[data-src]:checked', v).map((i) => i.dataset.src);
    const r = await startJob('/api/backup/run', { sources: srcs, destination: $('#dest').value.trim() }, $('#bkOut'), 'Préparation…');
    $('#bkOut').innerHTML = `<span class="chip ok">${icon('check')} ${r.copied} copiés (${fmtBytes(r.size)}) · ${r.skipped} inchangés${r.errors ? ` · ${r.errors} erreurs` : ''}</span><div class="tiny faint mt">${esc(r.destination)}</div>`;
  }, 'Sauvegarde terminée'));
  $$('[data-tool]', v).forEach((btn) => btn.onclick = () => safe(act(btn, async () => {
    const r = await startJob(`/api/maintenance/${btn.dataset.tool}`, {}, $('#toolOut'), 'Démarrage…');
    $('#toolOut').innerHTML = `<pre class="out">${esc(r.output || 'Terminé.')}</pre>`;
  }, 'Outil terminé')));
};

// ===== Supervision =====
R.supervision = async (v) => {
  const [s, logs] = await Promise.all([api('/api/supervision'), api('/api/journal?limit=80')]);
  const n = s.config, a = s.alerts_config;
  const base = INFO.local && INFO.lan_ips?.length ? `http://${INFO.lan_ips[0]}:${INFO.port}` : `${location.protocol}//${location.host}`;
  const tok = INFO.token || TOKEN;
  v.innerHTML = `<div class="stack"><div class="grid g2">
    ${card('Connexion à Nexus', `<div class="row between mb"><span class="small muted">Envoi automatique des métriques et alertes (JSON, POST)</span>${sw(n.enabled, 'id="nxOn"')}</div>
      <label class="field">URL de réception (webhook / API d'ingestion)<input type="url" id="nxUrl" value="${esc(n.url)}" placeholder="https://nexus.mondomaine.fr/api/ingest"></label>
      <div class="grid g2 mt"><label class="field">Clé API (facultatif)<input type="password" id="nxKey" value="${esc(n.api_key)}" placeholder="envoyée en Bearer + X-API-Key"></label>
      <label class="field">Intervalle (secondes)<input type="number" id="nxInt" value="${n.interval}" min="5"></label></div>
      <label class="check small mt"><input type="checkbox" id="nxAl" ${n.send_alerts ? 'checked' : ''}> Envoyer aussi les alertes (déclenchées / résolues)</label>
      <div class="row mt"><button class="btn btn-primary" id="nxSave">${icon('save')}Enregistrer</button><button class="btn" id="nxTest">${icon('play')}Envoyer un test</button>
      <span class="small ${s.last_push.ok === false ? '' : 'muted'}" style="${s.last_push.ok === false ? 'color:var(--bad)' : ''}">${s.last_push.ts ? `Dernier envoi ${fmtDate(s.last_push.ts)} : ${s.last_push.ok ? 'OK' : esc(s.last_push.error)}` : 'Aucun envoi'}</span></div>`, { ico: 'activity', sub: 'Mode « push » : Overdrive envoie ses données à votre serveur' })}
    ${card('Points d\'accès pour la supervision', `<p class="small muted" style="margin-top:0">Mode « pull » : Nexus, Prometheus, Grafana, Uptime Kuma, Home Assistant… peuvent interroger ces URL (remplacez l'adresse par l'IP du PC).</p>
      <div class="stack" style="gap:8px">${[['JSON complet (Nexus)', '/api/nexus'], ['Prometheus', '/metrics'], ['Santé (uptime)', '/api/health']].map(([l, p]) => `<div><div class="tiny faint">${l}</div>
      <div class="codebox"><code>${esc(`${base}${p}?token=${tok}`)}</code><button class="btn btn-sm" data-copy="${esc(`${base}${p}?token=${tok}`)}">${icon('copy')}</button></div></div>`).join('')}
      <div class="tiny faint">Le jeton peut aussi être passé en en-tête <code>Authorization: Bearer …</code> ou <code>X-Token</code>.</div></div>`, { ico: 'server' })}
    </div>
    <div class="grid g2">
    ${card('Seuils d\'alerte', `<div class="grid g2">${[['cpu_percent', 'CPU (%)'], ['ram_percent', 'RAM (%)'], ['cpu_temp', 'Temp. CPU (°C)'], ['gpu_temp', 'Temp. GPU (°C)'], ['disk_percent', 'Disque plein (%)']].map(([k, l]) => `<label class="field">${l}<input type="number" data-al="${k}" value="${a[k]}"></label>`).join('')}</div>
      <div class="row mt"><button class="btn" id="alSave">${icon('save')}Enregistrer les seuils</button></div>
      <div class="mt">${s.alerts.length ? s.alerts.map((x) => `<div class="notice" style="margin-top:8px">${icon('alert')}<div><b>${esc(x.label)}</b> — ${x.value}${esc(x.unit)} (seuil ${x.limit}${esc(x.unit)}) depuis ${fmtTime(x.since)}</div></div>`).join('') : `<span class="chip ok">${icon('check')} Aucune alerte active</span>`}</div>`, { ico: 'alert' })}
    ${card('Journal d\'activité', `<div class="scroll-y" style="max-height:420px">${logs.length ? logs.map((l) => `<div class="log"><span class="lvl ${l.level}"></span><time>${fmtDate(l.ts)}</time><span class="grow">${esc(l.message)}</span></div>`).join('') : empty('Journal vide')}</div>`, { ico: 'activity' })}
    </div></div>`;
  $$('[data-copy]', v).forEach((b) => b.onclick = () => copyText(b.dataset.copy));
  const saveNx = () => api('/api/config', { method: 'PUT', body: { nexus: { enabled: $('#nxOn').checked, url: $('#nxUrl').value.trim(), api_key: $('#nxKey').value, interval: Number($('#nxInt').value) || 30, send_alerts: $('#nxAl').checked } } });
  $('#nxSave').onclick = (e) => safe(act(e.currentTarget, saveNx, 'Configuration Nexus enregistrée'));
  $('#nxTest').onclick = (e) => safe(act(e.currentTarget, async () => { await saveNx(); return post('/api/supervision/test'); }, (r) => `Test envoyé (HTTP ${r.status})`));
  $('#alSave').onclick = (e) => safe(act(e.currentTarget, () => api('/api/config', { method: 'PUT', body: { alerts: Object.fromEntries($$('[data-al]', v).map((i) => [i.dataset.al, Number(i.value)])) } }), 'Seuils enregistrés'));
};

// ===== Paramètres =====
R.settings = async (v) => {
  const cfg = await api('/api/config');
  const s = cfg.scheduler;
  const urls = (INFO.lan_ips || []).map((ip) => `http://${ip}:${INFO.port}/?token=${INFO.token || '…'}`);
  v.innerHTML = `${winOnly()}<div class="grid g2" style="margin-top:${INFO.windows ? 0 : 16}px">
    ${card('Accès depuis le téléphone ou un autre PC', `<div class="row between mb"><span class="small">Autoriser l'accès réseau (avec jeton)</span>${sw(cfg.allow_remote, 'id="remoteOn"')}</div>
      ${INFO.local ? `<div class="stack" style="gap:8px">${urls.map((u) => `<div class="codebox"><code>${esc(u)}</code><button class="btn btn-sm" data-copy="${esc(u)}">${icon('copy')}</button></div>`).join('') || empty('Aucune adresse réseau')}</div>
      <p class="small muted">Ouvrez ce lien sur votre téléphone (même Wi-Fi), puis « Ajouter à l'écran d'accueil » : Overdrive s'utilise comme une appli.</p>
      ${notice('<b>Depuis n\'importe où (4G, autre réseau)</b> : installez <a href="https://tailscale.com/download" target="_blank" rel="noopener">Tailscale</a> (gratuit) sur le PC et le téléphone, puis utilisez l\'adresse Tailscale du PC (100.x.x.x) avec le même lien. Aucune ouverture de port nécessaire, connexion chiffrée.', 'info')}
      <div class="row mt"><button class="btn btn-danger btn-sm" id="regen">${icon('refresh')}Régénérer le jeton</button><span class="tiny faint">Les appareils connectés devront saisir le nouveau lien.</span></div>`
      : notice('Vous êtes connecté à distance. Les liens d\'accès ne sont visibles que sur le PC.', 'info')}`, { ico: 'remote', sub: 'Interface gratuite, rien à installer sur le téléphone' })}
    ${card('Pilotage à distance du PC', `<div class="grid g2">
      <button class="btn" data-pw="lock">${icon('lock')}Verrouiller</button><button class="btn" data-pw="sleep">${icon('moon')}Mettre en veille</button>
      <button class="btn" data-pw="restart">${icon('refresh')}Redémarrer</button><button class="btn btn-danger" data-pw="shutdown">${icon('power')}Éteindre</button></div>
      <button class="btn btn-ghost btn-sm mt" data-pw="cancel">${icon('x')}Annuler un arrêt / redémarrage programmé</button>
      <div class="grid g2 mt">${[['taskmgr', 'Gestionnaire des tâches'], ['display', 'Paramètres d\'affichage'], ['graphics', 'Graphismes (GPU par appli)'], ['control_power', 'Options d\'alimentation']].map(([k, l]) => `<button class="btn btn-sm" data-open="${k}">${esc(l)}</button>`).join('')}</div>`,
      { ico: 'power', sub: 'Arrêt et redémarrage après un délai de 30 s (annulable)' })}
    ${card('Automatisations', `<div class="list">
      <div class="item"><div class="item-body"><div class="item-title">Nettoyage automatique</div><div class="item-desc">Catégories sûres (temporaires, dumps, miniatures).</div></div>
        <input type="number" id="cleanH" value="${s.clean_every_hours}" min="1" style="width:80px"><span class="small muted">h</span>${sw(s.auto_clean, 'id="autoClean"')}</div>
      <div class="item"><div class="item-body"><div class="item-title">Libération automatique de la RAM</div><div class="item-desc">Quand l'utilisation dépasse le seuil (10 min minimum entre deux purges).</div></div>
        <input type="number" id="ramTh" value="${s.ram_threshold}" min="50" max="99" style="width:80px"><span class="small muted">%</span>${sw(s.auto_ram, 'id="autoRam"')}</div>
      <div class="item"><div class="item-body"><div class="item-title">Sauvegarde automatique</div><div class="item-desc">Utilise les dossiers et la destination de la page Sauvegarde.</div></div>
        <input type="number" id="bkH" value="${s.backup_every_hours}" min="1" style="width:80px"><span class="small muted">h</span>${sw(s.auto_backup, 'id="autoBk"')}</div>
      </div><div class="row mt"><button class="btn btn-primary" id="schSave">${icon('save')}Enregistrer</button></div>`, { ico: 'refresh', sub: 'Overdrive s\'en occupe en arrière-plan' })}
    ${card('À propos', `<div class="kv"><span>Version</span><span>${esc(INFO.version)}</span></div><div class="kv"><span>Droits administrateur</span><span>${INFO.admin ? 'oui' : 'non — lancez start.bat'}</span></div>
      <div class="kv"><span>Machine</span><span>${esc(INFO.static?.hostname)}</span></div><div class="kv"><span>Système</span><span>${esc(INFO.static?.os)} ${esc(INFO.static?.os_version || '')}</span></div>
      <div class="kv"><span>Températures</span><span>${STATS?.cpu?.temp != null ? 'disponibles' : 'lancez LibreHardwareMonitor (serveur web)'}</span></div>
      <div class="row mt"><a class="btn btn-sm" href="/api/docs" target="_blank">${icon('link')}Documentation de l'API</a></div>`, { ico: 'info' })}
  </div>`;
  $$('[data-copy]', v).forEach((b) => b.onclick = () => copyText(b.dataset.copy));
  $('#remoteOn').onchange = (e) => safe(act(null, () => api('/api/config', { method: 'PUT', body: { allow_remote: e.target.checked } }), 'Enregistré — redémarrez Overdrive pour appliquer'));
  if ($('#regen')) $('#regen').onclick = async (e) => {
    if (!await confirmBox('Régénérer le jeton ?', 'Les téléphones et outils de supervision déjà connectés perdront l\'accès.', 'Régénérer', true)) return;
    safe(act(e.currentTarget, async () => { const c = await api('/api/config', { method: 'PUT', body: { regenerate_token: true } }); TOKEN = c.token; INFO.token = c.token; try { localStorage.setItem('od_token', c.token); } catch (x) {} route(); }, 'Nouveau jeton généré'));
  };
  $$('[data-pw]', v).forEach((b) => b.onclick = async () => {
    const a = b.dataset.pw;
    if (['shutdown', 'restart'].includes(a) && !await confirmBox(a === 'shutdown' ? 'Éteindre le PC ?' : 'Redémarrer le PC ?', 'L\'action aura lieu dans 30 secondes. Enregistrez votre travail.', 'Confirmer', true)) return;
    safe(act(b, () => post(`/api/power/${a}`), (r) => r.message));
  });
  $$('[data-open]', v).forEach((b) => b.onclick = () => safe(act(b, () => post(`/api/open/${b.dataset.open}`), 'Ouvert sur le PC')));
  $('#schSave').onclick = (e) => safe(act(e.currentTarget, () => api('/api/config', { method: 'PUT', body: { scheduler: {
    auto_clean: $('#autoClean').checked, clean_every_hours: Number($('#cleanH').value) || 24,
    auto_ram: $('#autoRam').checked, ram_threshold: Number($('#ramTh').value) || 85,
    auto_backup: $('#autoBk').checked, backup_every_hours: Number($('#bkH').value) || 24 } } }), 'Automatisations enregistrées'));
};

// ---------- Boucle temps réel ----------
function updateChrome(s) {
  const chips = [];
  if (INFO && !INFO.admin && INFO.windows) chips.push(`<span class="chip warn" title="Relancez via start.bat">${icon('alert')} Sans admin</span>`);
  if (s.cpu?.temp != null) chips.push(`<span class="chip ${s.cpu.temp > 90 ? 'bad' : ''}">${icon('thermo')} CPU ${Math.round(s.cpu.temp)}°</span>`);
  if (s.gpu?.temp != null) chips.push(`<span class="chip ${s.gpu.temp > 85 ? 'bad' : ''}">${icon('thermo')} GPU ${Math.round(s.gpu.temp)}°</span>`);
  if (s.battery) chips.push(`<span class="chip ${s.battery.plugged ? '' : 'warn'}">${icon('battery')} ${Math.round(s.battery.percent)} %${s.battery.plugged ? '' : ' batterie'}</span>`);
  if (s.alerts?.length) chips.push(`<a class="chip bad" href="#/supervision">${icon('alert')} ${s.alerts.length} alerte(s)</a>`);
  $('#chips').innerHTML = chips.join('');
}

async function tick() {
  try {
    const s = await api('/api/stats');
    if (s && s.ts) {
      STATS = s;
      const push = (k, val) => { HIST[k].push(val); if (HIST[k].length > 120) HIST[k].shift(); };
      push('cpu', s.cpu.percent); push('ram', s.ram.percent); push('gpu', s.gpu.percent || 0);
      push('net_down', s.net.down); push('net_up', s.net.up);
      updateChrome(s);
      const h = current && R[current.id].onStats;
      if (h) h(s);
    }
  } catch (e) { /* serveur momentanément indisponible */ }
}

// ---------- Démarrage ----------
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $('#themeBtn').innerHTML = icon(t === 'dark' ? 'sun' : 'moon');
  try { localStorage.setItem('od_theme', t); } catch (e) {}
}

let booted = false;
async function boot() {
  try { INFO = await api('/api/info'); } catch (e) { if (!e.message.includes('Jeton')) $('#view').innerHTML = card('', empty(`Serveur Overdrive injoignable : ${e.message}`, 'alert')); return; }
  if (INFO.token && !TOKEN) TOKEN = INFO.token;
  const st = INFO.static || {};
  $('#sidebarFoot').innerHTML = `<div class="row" style="gap:8px">${INFO.admin ? chip('Administrateur', 'ok') : chip('Sans admin', 'warn')}${INFO.local ? '' : chip('Distant', 'acc')}</div>
    <div class="ellipsis"><b style="color:var(--text)">${esc(st.hostname || '')}</b><br>${esc(st.os || '')}</div><div class="tiny faint">Overdrive v${esc(INFO.version)}</div>`;
  if (!booted) {
    booted = true;
    window.addEventListener('hashchange', route);
    setInterval(tick, 1000);
  }
  await tick();
  route();
}

buildNav();
let theme = 'dark';
try { theme = localStorage.getItem('od_theme') || 'dark'; } catch (e) {}
applyTheme(theme);
$('#themeBtn').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
$('#menuBtn').onclick = () => { $('#sidebar').classList.toggle('open'); $('#backdrop').classList.toggle('open'); };
$('#backdrop').onclick = () => { $('#sidebar').classList.remove('open'); $('#backdrop').classList.remove('open'); };
$('#quickBtn').onclick = async (e) => {
  const btn = e.currentTarget;
  safe(act(btn, async () => {
    const { job } = await post('/api/quick-optimize');
    const r = await runJob(job);
    return r;
  }, (r) => `Optimisation express : ${fmtBytes(r.clean.freed)} nettoyés${r.ram?.freed != null ? `, ${fmtBytes(r.ram.freed)} de RAM libérés` : ''}`));
};
boot();
