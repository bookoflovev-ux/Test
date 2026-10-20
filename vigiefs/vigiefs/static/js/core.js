/* VigieFS — noyau de l'interface : utilitaires, API, icônes, graphiques, modales, tâches */
'use strict';

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = new Intl.NumberFormat('fr-FR');
const fmtNum = n => (n == null ? '—' : nf.format(n));
const GB = 1024 ** 3;

function fmtBytes(n, digits = 1) {
  if (n == null) return '—';
  const units = ['o', 'Ko', 'Mo', 'Go', 'To', 'Po'];
  let i = 0; let v = Math.abs(n);
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (n < 0 ? '-' : '') + (i === 0 ? v.toFixed(0) : v.toLocaleString('fr-FR', { maximumFractionDigits: digits, minimumFractionDigits: v < 10 ? digits : 0 })) + ' ' + units[i];
}
function compact(n) {
  if (n == null) return '—';
  if (Math.abs(n) >= 1e6) return (n / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 1 }) + ' M';
  if (Math.abs(n) >= 1e4) return (n / 1e3).toLocaleString('fr-FR', { maximumFractionDigits: 1 }) + ' k';
  return nf.format(n);
}
const fmtPct = (v, d = 1) => v.toLocaleString('fr-FR', { maximumFractionDigits: d, minimumFractionDigits: d }) + ' %';
function fmtDate(ts, withSec = false) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('fr-FR') + ' ' + d.toLocaleTimeString('fr-FR', withSec ? {} : { hour: '2-digit', minute: '2-digit' });
}
const fmtDay = ts => (ts ? new Date(ts * 1000).toLocaleDateString('fr-FR') : '—');
function fmtAgo(ts) {
  if (!ts) return 'jamais';
  const s = Date.now() / 1000 - ts;
  if (s < 0) return 'dans ' + fmtDuration(-s);
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
  if (s < 86400 * 30) return `il y a ${Math.floor(s / 86400)} j`;
  return fmtDay(ts);
}
function fmtDuration(sec) {
  if (sec == null) return '—';
  if (sec < 60) return `${sec.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min`;
  return `${Math.floor(sec / 86400)} j ${Math.floor((sec % 86400) / 3600)} h`;
}
function fmtAge(days) {
  if (days >= 365) { const y = days / 365; return `${y.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} an${y >= 2 ? 's' : ''}`; }
  if (days >= 30) return `${Math.round(days / 30)} mois`;
  return `${days} jour${days > 1 ? 's' : ''}`;
}
const plural = (n, one, many) => `${fmtNum(n)} ${n > 1 ? many : one}`;

// ------------------------------------------------------------------ icônes (24×24, remplissage)
const ICONS = {
  dashboard: 'M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z',
  clients: 'M16 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13zm8 0c-.29 0-.62.02-.97.05A4.22 4.22 0 0 1 17 16.5V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
  folder: 'M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm0 7V3.5L19.5 9z',
  bell: 'M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4a1.5 1.5 0 0 0-3 0v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1z',
  list: 'M3 13h2v-2H3zm0 4h2v-2H3zm0-8h2V7H3zm4 4h14v-2H7zm0 4h14v-2H7zM7 7v2h14V7z',
  report: 'M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zM9 17H7v-7h2zm4 0h-2V7h2zm4 0h-2v-4h2z',
  settings: 'M19.14 12.94a7.07 7.07 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.61l-1.92-3.32a.5.5 0 0 0-.59-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.48.41l-.36 2.54a7.3 7.3 0 0 0-1.62.94l-2.39-.96a.48.48 0 0 0-.59.22L2.74 8.87a.48.48 0 0 0 .12.61l2.03 1.58a7.3 7.3 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.04.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.48.48 0 0 0-.12-.61zM12 15.6A3.6 3.6 0 1 1 15.6 12 3.6 3.6 0 0 1 12 15.6z',
  play: 'M8 5v14l11-7z',
  stop: 'M6 6h12v12H6z',
  plus: 'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z',
  menu: 'M3 18h18v-2H3zm0-5h18v-2H3zm0-7v2h18V6z',
  refresh: 'M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z',
  download: 'M5 20h14v-2H5zm14-9h-4V3H9v8H5l7 7z',
  upload: 'M5 20h14v-2H5zm4-4h6v-6h4l-7-7-7 7h4z',
  moon: 'M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.39 5.39 0 0 1-4.4 2.26 5.4 5.4 0 0 1-3.14-9.8A9.3 9.3 0 0 0 12 3z',
  logout: 'M10.09 15.59 11.5 17l5-5-5-5-1.41 1.41L12.67 11H3v2h9.67zM19 3H5a2 2 0 0 0-2 2v4h2V5h14v14H5v-4H3v4a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2z',
  search: 'M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19zm-6 0a4.5 4.5 0 1 1 4.5-4.5A4.49 4.49 0 0 1 9.5 14z',
  broom: 'M19.36 2.72 20.78 4.14 15.06 9.85c1.07 1.54 1.22 3.39.32 4.59L9.06 8.12c1.2-.9 3.05-.75 4.59.32zM5.93 17.57c-2.01-2.01-3.24-4.41-3.58-6.65l4.88-2.09 7.44 7.44-2.09 4.88c-2.24-.34-4.64-1.57-6.65-3.58z',
  copy: 'M16 1H4a2 2 0 0 0-2 2v14h2V3h12zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11z',
  archive: 'M20.54 5.23 19.15 3.55A1.45 1.45 0 0 0 18 3H6a1.49 1.49 0 0 0-1.16.55L3.46 5.23A1.97 1.97 0 0 0 3 6.5V19a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6.5a1.97 1.97 0 0 0-.46-1.27zM12 17.5 6.5 12H10v-2h4v2h3.5zM5.12 5l.81-1h12l.94 1z',
  trash: 'M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z',
  restore: 'M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8z',
  wrench: 'M22.7 19 13.6 9.9c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z',
  shield: 'M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5zm-2 16-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9z',
  tasks: 'M22 7h-9v2h9zm0 8h-9v2h9zM5.54 11 2 7.46l1.41-1.41 2.12 2.12 4.24-4.24 1.41 1.41zm0 8L2 15.46l1.41-1.41 2.12 2.12 4.24-4.24 1.41 1.41z',
  check: 'M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z',
  alert: 'M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z',
  info: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 15h-2v-6h2zm0-8h-2V7h2z',
  sparkle: 'M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25z',
  disk: 'M20 13H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1zM7 19a2 2 0 1 1 2-2 2 2 0 0 1-2 2zM20 3H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1zM7 9a2 2 0 1 1 2-2 2 2 0 0 1-2 2z',
  lock: 'M18 8h-1V6A5 5 0 0 0 7 6v2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2zm-6 9a2 2 0 1 1 2-2 2 2 0 0 1-2 2zm3.1-9H8.9V6a3.1 3.1 0 0 1 6.2 0z',
  chevron: 'M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z',
  close: 'M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z',
  activity: 'M3.5 18.49 9.5 12.48l4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z',
  eye: 'M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17a5 5 0 1 1 5-5 5 5 0 0 1-5 5zm0-8a3 3 0 1 0 3 3 3 3 0 0 0-3-3z',
};
const icon = (name, cls = '') => `<svg viewBox="0 0 24 24" fill="currentColor" class="${cls}" aria-hidden="true"><path d="${ICONS[name] || ''}"/></svg>`;
const LOGO = '<span class="brand-mark"><svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H10l2 2h7.5A1.5 1.5 0 0 1 21 8.5v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z" fill="#fff"/><circle cx="12" cy="13.5" r="3.4" fill="#2a78d6"/><circle cx="12" cy="13.5" r="1.5" fill="#fff"/></svg></span>';
const DOC_FAMILY = { docx: 'word', docm: 'word', dotx: 'word', dotm: 'word', doc: 'word', dot: 'word', xlsx: 'excel', xlsm: 'excel', xltx: 'excel', xltm: 'excel', xls: 'excel', xlt: 'excel', pptx: 'powerpoint', pptm: 'powerpoint', potx: 'powerpoint', ppsx: 'powerpoint', ppt: 'powerpoint', pps: 'powerpoint', pdf: 'pdf' };
const extOf = name => (name.includes('.') ? name.split('.').pop().toLowerCase() : '');
const fileIcon = name => { const fam = DOC_FAMILY[extOf(name)]; return icon('file', 'file-icon' + (fam ? ' ' + fam : '')); };

const SEV_LABEL = { critical: 'Critique', warning: 'Avertissement', info: 'Info', ok: 'OK' };
const STATUS_LABEL = { open: 'Ouverte', acknowledged: 'Acquittée', resolved: 'Résolue' };
const CAT_LABEL = { quota: 'Quota', forecast: 'Prévision', screening: 'Filtrage', ransomware: 'Rançongiciel', path: 'Disponibilité', disk: 'Espace disque', scan: 'Analyse', system: 'Système', permissions: "Droits d'accès", paths: 'Chemins et nommage', sensitive: 'Données sensibles', behavior: 'Comportement utilisateur', security: 'Sécurité des comptes', service: 'Service VigieFS', virtualization: 'Virtualisation' };
const EVENT_LABEL = { created: 'Création', modified: 'Modification', deleted: 'Suppression', moved: 'Déplacement/renommage', dossier_created: 'Dossier créé', dossier_deleted: 'Dossier supprimé', dossier_moved: 'Dossier déplacé' };
const ROLE_LABEL = { viewer: 'Lecture seule', operator: 'Opérateur', admin: 'Administrateur' };
const JOB_STATUS = { queued: ['neutral', 'En attente'], running: ['info', 'En cours'], done: ['ok', 'Terminée'], failed: ['critical', 'Échec'], cancelled: ['neutral', 'Annulée'], interrupted: ['warning', 'Interrompue'] };
const DOC_STATUS = { ok: ['ok', 'Sain'], warning: ['warning', 'Anomalie mineure'], corrupt: ['critical', 'Corrompu'], irrecoverable: ['critical', 'Irrécupérable'], protected: ['violet', 'Protégé'], unsupported: ['neutral', 'Non pris en charge'] };
const REPAIR_STATUS = { repaired: ['ok', 'Réparé'], partial: ['warning', 'Réparation partielle'], text_salvaged: ['warning', 'Texte récupéré'], not_needed: ['neutral', 'Aucune réparation nécessaire'], failed: ['critical', 'Irréparable'], converted: ['ok', 'Converti'] };
const sev = s => `<span class="sev ${esc(s)}">${esc(SEV_LABEL[s] || s)}</span>`;
const badge = (map, key) => { const [cls, label] = map[key] || ['neutral', key]; return `<span class="sev ${cls}">${esc(label)}</span>`; };

// ------------------------------------------------------------------ état & API
const state = { user: null, company: '', version: '', timers: [], redraw: [], cache: {} };
const ROLE_LEVEL = { viewer: 0, operator: 1, admin: 2 };
const can = role => state.user && ROLE_LEVEL[state.user.role] >= ROLE_LEVEL[role];

async function api(path, { method = 'GET', body, raw } = {}) {
  const opts = { method, headers: { 'X-VigieFS': '1' }, credentials: 'same-origin' };
  if (raw !== undefined) { opts.body = raw; opts.headers['Content-Type'] = 'application/octet-stream'; } else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (res.status === 401 && !path.startsWith('/api/auth/')) { state.user = null; boot(); throw new Error('Session expirée'); }
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `Erreur ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map(d => `${(d.loc || []).slice(-1)[0]} : ${d.msg}`).join(' ; ');
    throw new Error(msg);
  }
  return data;
}

function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast' + (type === 'error' ? ' error' : '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), type === 'error' ? 7000 : 3500);
}
const guard = fn => async (...args) => { try { return await fn(...args); } catch (e) { toast(e.message, 'error'); } };

function every(ms, fn) { const id = setInterval(() => { if (!document.hidden) fn(); }, ms); state.timers.push(id); return id; }
function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; state.redraw = []; }

/** Délégation : <button data-act="nom"> → actions.nom(el, event) */
function bindActions(root, actions) {
  root.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (!el || !root.contains(el)) return;
    const link = e.target.closest('a[href]');
    if (link && !link.dataset.act && el.contains(link)) return;
    const fn = actions[el.dataset.act];
    if (fn) { e.preventDefault(); e.stopPropagation(); guard(fn)(el, e); }
  });
}
function rowLinks(root) {
  root.addEventListener('click', e => {
    if (e.target.closest('button,a,input,select,label')) return;
    const g = e.target.closest('[data-go]');
    if (g && root.contains(g)) location.hash = g.dataset.go;
  });
}

// ------------------------------------------------------------------ infobulle
const tip = $('#tooltip');
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14; let top = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > innerHeight - 8) top = y - r.height - 14;
  tip.style.left = left + 'px'; tip.style.top = top + 'px';
}
const hideTip = () => { tip.hidden = true; };
document.addEventListener('mousemove', e => {
  const el = e.target.closest && e.target.closest('[data-tip]');
  if (el) showTip(el.dataset.tip, e.clientX, e.clientY);
  else if (!e.target.closest || !e.target.closest('.chart-interactive')) hideTip();
});

// ------------------------------------------------------------------ graphiques (une série, règles dataviz)
function niceTicks(max, count = 4) {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}
function niceBytesTicks(max, count = 4) {
  const units = [1, 1024, 1024 ** 2, 1024 ** 3, 1024 ** 4];
  let u = units[0];
  for (const x of units) if (max >= x) u = x;
  return niceTicks(max / u, count).map(t => t * u);
}
function colPath(x, y, w, h, r = 4) {
  if (h <= 0) return '';
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function columnChart(el, data, { height = 200, fmt = compact, every: labelEvery = 1 } = {}) {
  const draw = () => {
    const W = Math.max(el.clientWidth, 280); const H = height;
    const m = { t: 10, r: 6, b: 24, l: 40 };
    const iw = W - m.l - m.r; const ih = H - m.t - m.b;
    const max = Math.max(...data.map(d => d.value), 0);
    const ticks = niceTicks(max || 1);
    const top = ticks[ticks.length - 1];
    const band = iw / data.length;
    const bw = Math.min(24, band - 2);
    let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img">`;
    ticks.forEach(t => {
      const y = m.t + ih - (t / top) * ih;
      svg += `<line class="${t === 0 ? 'baseline' : 'grid-line'}" x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}"/>`;
      svg += `<text class="tick" x="${m.l - 6}" y="${y + 4}" text-anchor="end">${esc(fmt(t))}</text>`;
    });
    data.forEach((d, i) => {
      const h = (d.value / top) * ih;
      const x = m.l + i * band + (band - bw) / 2;
      svg += `<g><rect class="bar-hit" x="${m.l + i * band}" y="${m.t}" width="${band}" height="${ih}" data-tip="${esc(d.tip || `${d.label} : <b>${fmt(d.value)}</b>`)}"/>`;
      svg += `<path class="bar" d="${colPath(x, m.t + ih - h, bw, h)}" pointer-events="none"/></g>`;
      if (i % labelEvery === 0) svg += `<text class="tick" x="${m.l + i * band + band / 2}" y="${H - 6}" text-anchor="middle">${esc(d.label)}</text>`;
    });
    el.innerHTML = svg + '</svg>';
  };
  el.classList.add('chart'); draw(); state.redraw.push(draw);
}
function lineChart(el, points, { height = 220, fmtY = v => fmtBytes(v), bytes = true } = {}) {
  if (points.length < 2) { el.innerHTML = emptyState('activity', 'Pas encore de tendance', 'Au moins deux analyses sont nécessaires pour tracer l\'évolution du volume.'); return; }
  const draw = () => {
    const W = Math.max(el.clientWidth, 280); const H = height;
    const m = { t: 12, r: 16, b: 24, l: 66 };
    const iw = W - m.l - m.r; const ih = H - m.t - m.b;
    const xs = points.map(p => p.x); const x0 = Math.min(...xs); const x1 = Math.max(...xs);
    const maxY = Math.max(...points.map(p => p.y));
    const ticks = bytes ? niceBytesTicks(maxY || 1) : niceTicks(maxY || 1);
    const top = ticks[ticks.length - 1] || 1;
    const sx = x => m.l + (x1 === x0 ? iw / 2 : ((x - x0) / (x1 - x0)) * iw);
    const sy = y => m.t + ih - (y / top) * ih;
    let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img">`;
    ticks.forEach(t => {
      svg += `<line class="${t === 0 ? 'baseline' : 'grid-line'}" x1="${m.l}" x2="${W - m.r}" y1="${sy(t)}" y2="${sy(t)}"/>`;
      svg += `<text class="tick" x="${m.l - 6}" y="${sy(t) + 4}" text-anchor="end">${esc(fmtY(t))}</text>`;
    });
    const n = Math.min(5, points.length);
    for (let i = 0; i < n; i++) {
      const x = x0 + ((x1 - x0) * i) / Math.max(1, n - 1);
      svg += `<text class="tick" x="${sx(x)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">${new Date(x * 1000).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })}</text>`;
    }
    const line = points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
    svg += `<path class="area" d="${line}L${sx(x1)},${sy(0)}L${sx(x0)},${sy(0)}Z"/><path class="line" d="${line}"/>`;
    const last = points[points.length - 1];
    svg += `<line class="crosshair" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" visibility="hidden"/><circle class="marker hover-dot" r="5" visibility="hidden"/>`;
    svg += `<circle class="marker" cx="${sx(last.x)}" cy="${sy(last.y)}" r="4.5"/><rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" class="hit"/>`;
    el.innerHTML = svg + '</svg>';
    el.classList.add('chart-interactive');
    const s = $('svg', el); const cross = $('.crosshair', s); const dot = $('.hover-dot', s);
    $('.hit', s).addEventListener('mousemove', e => {
      const r = s.getBoundingClientRect();
      const mx = ((e.clientX - r.left) / r.width) * W;
      let best = points[0];
      for (const p of points) if (Math.abs(sx(p.x) - mx) < Math.abs(sx(best.x) - mx)) best = p;
      cross.setAttribute('x1', sx(best.x)); cross.setAttribute('x2', sx(best.x)); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', sx(best.x)); dot.setAttribute('cy', sy(best.y)); dot.setAttribute('visibility', 'visible');
      showTip(`${fmtDate(best.x)}<br><b>${esc(fmtY(best.y))}</b>${best.extra ? '<br>' + esc(best.extra) : ''}`, e.clientX, e.clientY);
    });
    $('.hit', s).addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); hideTip(); });
  };
  el.classList.add('chart'); draw(); state.redraw.push(draw);
}
/** Colonnes empilées (≤ 3 séries, couleurs --series-1..3) : légende au-dessus, infobulle par colonne. */
function stackedChart(el, data, series, { height = 200, fmt = compact, every: labelEvery = 1 } = {}) {
  const draw = () => {
    const W = Math.max(el.clientWidth, 280); const H = height;
    const m = { t: 10, r: 6, b: 24, l: 40 };
    const iw = W - m.l - m.r; const ih = H - m.t - m.b;
    const totals = data.map(d => d.values.reduce((a, b) => a + b, 0));
    const ticks = niceTicks(Math.max(...totals, 0) || 1);
    const top = ticks[ticks.length - 1];
    const band = iw / data.length;
    const bw = Math.max(1, Math.min(24, band - 2));
    let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img">`;
    ticks.forEach(t => {
      const y = m.t + ih - (t / top) * ih;
      svg += `<line class="${t === 0 ? 'baseline' : 'grid-line'}" x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}"/>`;
      svg += `<text class="tick" x="${m.l - 6}" y="${y + 4}" text-anchor="end">${esc(fmt(t))}</text>`;
    });
    data.forEach((d, i) => {
      const x = m.l + i * band + (band - bw) / 2;
      let y = m.t + ih;
      const last = d.values.reduce((k, v, j) => (v > 0 ? j : k), -1);
      const tipRows = series.map((s, j) => `<span class="sw s${j + 1}"></span>${esc(s.label)} : <b>${esc(fmt(d.values[j]))}</b>`).join('<br>');
      svg += `<g><rect class="bar-hit" x="${m.l + i * band}" y="${m.t}" width="${band}" height="${ih}" data-tip="${esc(`${d.tip || d.label}<br>${tipRows}<br>Total : <b>${fmt(totals[i])}</b>`)}"/>`;
      d.values.forEach((v, j) => {
        if (v <= 0) return;
        const h = (v / top) * ih;
        const gap = j === last ? 0 : Math.min(2, h / 2);
        y -= h;
        svg += j === last ? `<path class="bar s${j + 1}" d="${colPath(x, y, bw, h)}" pointer-events="none"/>`
          : `<rect class="bar s${j + 1}" x="${x}" y="${y + gap}" width="${bw}" height="${Math.max(0, h - gap)}" pointer-events="none"/>`;
      });
      svg += '</g>';
      if (i % labelEvery === 0) svg += `<text class="tick" x="${m.l + i * band + band / 2}" y="${H - 6}" text-anchor="middle">${esc(d.label)}</text>`;
    });
    el.innerHTML = svg + '</svg>';
  };
  el.classList.add('chart'); draw(); state.redraw.push(draw);
}
const chartLegend = series => `<div class="legend">${series.map((s, j) => `<span><i class="sw s${j + 1}"></i>${esc(s.label)}</span>`).join('')}</div>`;
/** Carte horaire 7 jours × 24 h (une teinte, du clair au foncé) ; `bh` = horaires de travail encadrés. */
function heatmap(matrix, { fmt = fmtNum, bh = null } = {}) {
  const days = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
  const max = Math.max(1, ...matrix.flat());
  const level = v => (v <= 0 ? 0 : Math.min(5, 1 + Math.floor((v / max) * 4.999)));
  const inBh = (d, h) => bh && (bh.days || []).includes(d) && h >= bh.start && h < bh.end;
  return `<div class="heat"><div></div>${Array.from({ length: 24 }, (_, h) => `<div class="hx">${h % 3 === 0 ? h + 'h' : ''}</div>`).join('')}
    ${matrix.map((row, d) => `<div class="hy">${days[d].slice(0, 3)}</div>${row.map((v, h) => `<div class="cell l${level(v)}${inBh(d, h) ? ' bh' : ''}" data-tip="${esc(`${days[d]} ${h}h–${h + 1}h<br><b>${fmt(v)}</b> opération(s)${inBh(d, h) ? '' : '<br>hors horaires de travail'}`)}"></div>`).join('')}`).join('')}</div>
    <div class="heat-legend small muted">Moins ${[0, 1, 2, 3, 4, 5].map(l => `<span class="cell l${l}"></span>`).join('')} Plus${bh ? ' · <span class="cell bh l0"></span> horaires de travail' : ''}</div>`;
}
function hbars(items, { fmt = v => fmtBytes(v), sub } = {}) {
  if (!items.length) return '<div class="empty">Aucune donnée</div>';
  const max = Math.max(...items.map(i => i.value), 1);
  return `<div class="hbars">${items.map(i => `
    <div class="hbar" data-tip="${esc(`${esc(i.name)}<br><b>${esc(fmt(i.value))}</b>${sub ? '<br>' + esc(sub(i)) : ''}`)}">
      <span class="name" title="${esc(i.name)}">${esc(i.name)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(0.5, (i.value / max) * 100).toFixed(2)}%"></span></span>
      <span class="val">${esc(fmt(i.value))}</span></div>`).join('')}</div>`;
}
function meter(pct, level) {
  if (pct == null) return '<span class="muted small">Sans quota</span>';
  const label = level === 'critical' ? 'critique' : level === 'warning' ? 'avertissement' : 'normal';
  return `<div class="meter ${esc(level || '')}" data-tip="Utilisation du quota : <b>${fmtPct(pct)}</b> (${label})">
    <div class="track"><div class="fill" style="width:${Math.min(100, pct).toFixed(1)}%"></div></div><span class="pct">${pct.toFixed(0)} %</span></div>`;
}
function progressBar(done, total) {
  if (!total) return '<div class="progress indeterminate"><div></div></div>';
  return `<div class="progress"><div style="width:${Math.min(100, (done / total) * 100).toFixed(1)}%"></div></div>`;
}
let resizeT;
addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => state.redraw.forEach(f => f()), 150); });

// ------------------------------------------------------------------ blocs d'interface
const tile = ({ label, value, sub = '', ic = 'info', tone = '', go = '', hero = false }) =>
  `<div class="tile${go ? ' link' : ''}${hero ? ' hero-tile' : ''}"${go ? ` data-go="${esc(go)}"` : ''}><div class="chip ${tone}">${icon(ic)}</div>
    <div class="body"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div></div>`;
const emptyState = (ic, title, text = '', cta = '', tone = '') =>
  `<div class="empty-state"><div class="ic ${tone}">${icon(ic)}</div><h2>${esc(title)}</h2>${text ? `<p>${text}</p>` : ''}${cta}</div>`;
const cardHead = (title, desc = '', right = '') =>
  `<div class="card-head"><div><h2>${title}</h2>${desc ? `<div class="desc">${desc}</div>` : ''}</div><div class="spacer"></div>${right}</div>`;

// ------------------------------------------------------------------ modales & formulaires
function modal({ title, body, submit = 'Enregistrer', size = '', danger = false, onSubmit, noFooter = false, onOpen }) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><form class="modal ${size}" novalidate>
    <div class="modal-head"><h2>${esc(title)}</h2><button type="button" class="icon-btn" data-close aria-label="Fermer">${icon('close')}</button></div>
    <div class="modal-body">${body}<div class="error-text" data-err></div></div>
    ${noFooter ? '' : `<div class="modal-foot"><button type="button" class="btn" data-close>Annuler</button>
    <button type="submit" class="btn ${danger ? 'danger solid' : 'primary'}">${esc(submit)}</button></div>`}
  </form></div>`;
  const form = $('form', root);
  const close = () => { root.innerHTML = ''; };
  $$('[data-close]', root).forEach(b => b.addEventListener('click', close));
  $('.modal-backdrop', root).addEventListener('mousedown', e => { if (e.target.classList.contains('modal-backdrop')) close(); });
  form.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (!onSubmit) return close();
    const btn = $('button[type=submit]', form); if (btn) btn.disabled = true;
    try { const r = await onSubmit(form); if (r !== false) close(); } catch (err) { $('[data-err]', form).textContent = err.message; } finally { if (btn) btn.disabled = false; }
  });
  setTimeout(() => { const f = $('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea', form); if (f) f.focus(); }, 30);
  if (onOpen) onOpen(form);
  return form;
}
const confirmBox = (title, text, onOk, submit = 'Confirmer', danger = true) =>
  modal({ title, size: 'sm', danger, submit, body: `<div>${text}</div>`, onSubmit: onOk });

function formData(form) {
  const out = {};
  $$('[name]', form).forEach(el => {
    if (el.type === 'radio') { if (el.checked) out[el.name] = el.value; return; }
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'number') out[el.name] = el.value === '' ? null : Number(el.value);
    else out[el.name] = el.value;
  });
  return out;
}
const field = (label, input, hint = '', full = false) =>
  `<div class="field${full ? ' full' : ''}"><label>${esc(label)}</label>${input}${hint ? `<span class="hint">${hint}</span>` : ''}</div>`;
const inp = (name, value = '', type = 'text', extra = '') => `<input type="${type}" name="${name}" value="${esc(value)}" ${extra}>`;
const sel = (name, options, value, extra = '') =>
  `<select name="${name}" ${extra}>${options.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
const chk = (name, label, checked, extra = '') => `<label class="check"><input type="checkbox" name="${name}"${checked ? ' checked' : ''} ${extra}> ${esc(label)}</label>`;
const seg = (name, options, value) => `<div class="seg">${options.map(([v, l]) => `<label><input type="radio" name="${name}" value="${esc(v)}"${v === value ? ' checked' : ''}>${esc(l)}</label>`).join('')}</div>`;

// ------------------------------------------------------------------ tâches de fond
/** Suit une tâche jusqu'à la fin ; onTick(job) à chaque progression, renvoie la tâche finale. */
function watchJob(jobId, onTick) {
  return new Promise(resolve => {
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      try {
        const job = await api(`/api/jobs/${jobId}`);
        onTick && onTick(job);
        if (!['queued', 'running'].includes(job.status)) { stopped = true; clearInterval(id); resolve(job); }
      } catch { /* réessai au prochain tick */ }
    };
    const id = every(1000, poll);
    poll();
  });
}
function jobLine(job, label) {
  const p = job.progress || {};
  const total = p.total || p.to_hash || 0;
  const done = p.done ?? p.hashed ?? 0;
  const bits = [];
  if (p.phase) bits.push(p.phase);
  if (p.scanned) bits.push(`${compact(p.scanned)} fichiers parcourus`);
  if (p.checked) bits.push(`${compact(p.checked)} documents vérifiés`);
  if (p.candidates) bits.push(`${compact(p.candidates)} candidats · ${fmtBytes(p.size)}`);
  if (total) bits.push(`${compact(done)} / ${compact(total)}`);
  if (p.moved) bits.push(`${compact(p.moved)} déplacés`);
  return `<div class="banner info"><span class="dot run"></span><div style="flex:1;min-width:0"><div>${esc(label || job.title)}</div>
    <div class="small" style="font-weight:450">${esc(bits.join(' · ') || 'Démarrage…')}</div><div style="margin-top:6px">${progressBar(done, total)}</div></div>
    ${can('operator') ? `<button class="btn sm" data-cancel-job="${job.id}">Annuler</button>` : ''}</div>`;
}
document.addEventListener('click', guard(async e => {
  const b = e.target.closest('[data-cancel-job]');
  if (!b) return;
  await api(`/api/jobs/${b.dataset.cancelJob}/cancel`, { method: 'POST' });
  toast('Annulation demandée');
}));
