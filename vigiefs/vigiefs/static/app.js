/* VigieFS — interface d'administration (JavaScript natif, sans dépendance) */
'use strict';

// ------------------------------------------------------------------ utilitaires
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = new Intl.NumberFormat('fr-FR');
const fmtNum = n => (n == null ? '—' : nf.format(n));

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
  if (Math.abs(n) >= 1e3) return (n / 1e3).toLocaleString('fr-FR', { maximumFractionDigits: 1 }) + ' k';
  return nf.format(n);
}
function fmtDate(ts, withSec = false) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('fr-FR') + ' ' + d.toLocaleTimeString('fr-FR', withSec ? {} : { hour: '2-digit', minute: '2-digit' });
}
function fmtAgo(ts) {
  if (!ts) return 'jamais';
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
  if (s < 86400 * 30) return `il y a ${Math.floor(s / 86400)} j`;
  return fmtDate(ts);
}
function fmtDuration(sec) {
  if (sec == null) return '—';
  if (sec < 60) return `${sec.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min`;
  return `${Math.floor(sec / 86400)} j ${Math.floor((sec % 86400) / 3600)} h`;
}
const GB = 1024 ** 3;
const fmtPct = (v, d = 1) => v.toLocaleString('fr-FR', { maximumFractionDigits: d, minimumFractionDigits: d }) + ' %';

const ICONS = {
  dashboard: '<path d="M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z"/>',
  clients: '<path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>',
  folder: '<path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm0 7V3.5L19.5 9z"/>',
  bell: '<path d="M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4a1.5 1.5 0 0 0-3 0v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1z"/>',
  list: '<path d="M3 13h2v-2H3zm0 4h2v-2H3zm0-8h2V7H3zm4 4h14v-2H7zm0 4h14v-2H7zM7 7v2h14V7z"/>',
  report: '<path d="M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zM9 17H7v-7h2zm4 0h-2V7h2zm4 0h-2v-4h2z"/>',
  settings: '<path d="M19.14 12.94a7.07 7.07 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.61l-1.92-3.32a.5.5 0 0 0-.59-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.48.41l-.36 2.54a7.3 7.3 0 0 0-1.62.94l-2.39-.96a.48.48 0 0 0-.59.22L2.74 8.87a.48.48 0 0 0 .12.61l2.03 1.58a7.3 7.3 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.04.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.48.48 0 0 0-.12-.61zM12 15.6A3.6 3.6 0 1 1 15.6 12 3.6 3.6 0 0 1 12 15.6z"/>',
  play: '<path d="M8 5v14l11-7z"/>',
  stop: '<path d="M6 6h12v12H6z"/>',
  plus: '<path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/>',
  menu: '<path d="M3 18h18v-2H3zm0-5h18v-2H3zm0-7v2h18V6z"/>',
  refresh: '<path d="M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z"/>',
  download: '<path d="M5 20h14v-2H5zm14-9h-4V3H9v8H5l7 7z"/>',
  moon: '<path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.39 5.39 0 0 1-4.4 2.26 5.4 5.4 0 0 1-3.14-9.8A9.3 9.3 0 0 0 12 3z"/>',
};
const icon = (name, cls = '') => `<svg viewBox="0 0 24 24" fill="currentColor" class="${cls}" aria-hidden="true">${ICONS[name] || ''}</svg>`;
const LOGO = '<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#2a78d6"/><path d="M7 10h7l2 2h9v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1z" fill="#fff"/><circle cx="16" cy="18" r="3.2" fill="#2a78d6"/><circle cx="16" cy="18" r="1.4" fill="#fff"/></svg>';

const SEV_LABEL = { critical: 'Critique', warning: 'Avertissement', info: 'Info', ok: 'OK' };
const STATUS_LABEL = { open: 'Ouverte', acknowledged: 'Acquittée', resolved: 'Résolue' };
const CAT_LABEL = { quota: 'Quota', forecast: 'Prévision', screening: 'Filtrage', ransomware: 'Rançongiciel', path: 'Disponibilité', disk: 'Espace disque', scan: 'Analyse', system: 'Système' };
const EVENT_LABEL = { created: 'Création', modified: 'Modification', deleted: 'Suppression', moved: 'Déplacement/renommage', dossier_created: 'Dossier créé', dossier_deleted: 'Dossier supprimé', dossier_moved: 'Dossier déplacé' };
const ROLE_LABEL = { viewer: 'Lecture seule', operator: 'Opérateur', admin: 'Administrateur' };
const sev = s => `<span class="sev ${esc(s)}">${esc(SEV_LABEL[s] || s)}</span>`;

// ------------------------------------------------------------------ état & API
const state = { user: null, company: '', version: '', timers: [], redraw: [], openAlerts: 0 };
const ROLE_LEVEL = { viewer: 0, operator: 1, admin: 2 };
const can = role => state.user && ROLE_LEVEL[state.user.role] >= ROLE_LEVEL[role];

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: { 'X-VigieFS': '1' }, credentials: 'same-origin' };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
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
const guard = fn => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message, 'error'); } };

function every(ms, fn) { const id = setInterval(() => { if (!document.hidden) fn(); }, ms); state.timers.push(id); }
function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; state.redraw = []; }

/** Délégation d'événements : <button data-act="nom" data-id="3"> → actions.nom(el, event) */
function bindActions(root, actions) {
  root.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (!el || !root.contains(el)) return;
    const link = e.target.closest('a[href]');
    if (link && !link.dataset.act && el.contains(link)) return; // lien natif (ex. téléchargement)
    const fn = actions[el.dataset.act];
    if (fn) { e.preventDefault(); e.stopPropagation(); guard(fn)(el, e); }
  });
}

// ------------------------------------------------------------------ infobulle globale
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

// ------------------------------------------------------------------ graphiques
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

/** Histogramme vertical, une série. data = [{label, value, tip}] */
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

/** Courbe temporelle, une série, avec réticule. points = [{x: ts, y}] */
function lineChart(el, points, { height = 220, fmtY = v => fmtBytes(v), bytes = true } = {}) {
  if (points.length < 2) { el.innerHTML = '<div class="empty">Au moins deux analyses sont nécessaires pour tracer l\'évolution.</div>'; return; }
  const draw = () => {
    const W = Math.max(el.clientWidth, 280); const H = height;
    const m = { t: 12, r: 16, b: 24, l: 64 };
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
    const nLabels = Math.min(5, points.length);
    for (let i = 0; i < nLabels; i++) {
      const x = x0 + ((x1 - x0) * i) / Math.max(1, nLabels - 1);
      const d = new Date(x * 1000);
      svg += `<text class="tick" x="${sx(x)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === nLabels - 1 ? 'end' : 'middle'}">${d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })}</text>`;
    }
    const line = points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
    svg += `<path class="area" d="${line}L${sx(x1)},${sy(0)}L${sx(x0)},${sy(0)}Z"/>`;
    svg += `<path class="line" d="${line}"/>`;
    const last = points[points.length - 1];
    svg += `<line class="crosshair" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" visibility="hidden"/>`;
    svg += `<circle class="marker hover-dot" r="5" visibility="hidden"/>`;
    svg += `<circle class="marker" cx="${sx(last.x)}" cy="${sy(last.y)}" r="4.5"/>`;
    svg += `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" class="hit"/>`;
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

/** Barres horizontales étiquetées (valeur en bout de barre). */
function hbars(items, { fmt = v => fmtBytes(v), sub } = {}) {
  if (!items.length) return '<div class="empty">Aucune donnée</div>';
  const max = Math.max(...items.map(i => i.value), 1);
  return `<div class="hbars">${items.map(i => `
    <div class="hbar" data-tip="${esc(`${esc(i.name)}<br><b>${esc(fmt(i.value))}</b>${sub ? '<br>' + esc(sub(i)) : ''}`)}">
      <span class="name" title="${esc(i.name)}">${esc(i.name)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(0.5, (i.value / max) * 100).toFixed(2)}%;display:block"></span></span>
      <span class="val">${esc(fmt(i.value))}</span>
    </div>`).join('')}</div>`;
}

function meter(pct, level) {
  if (pct == null) return '<span class="muted small">Sans quota</span>';
  const w = Math.min(100, pct);
  const label = level === 'critical' ? 'critique' : level === 'warning' ? 'avertissement' : 'normal';
  return `<div class="meter ${esc(level || '')}" data-tip="Utilisation du quota : <b>${fmtPct(pct)}</b> (${label})">
    <div class="track"><div class="fill" style="width:${w.toFixed(1)}%"></div></div><span class="pct">${pct.toFixed(0)} %</span></div>`;
}

let resizeT;
addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => state.redraw.forEach(f => f()), 150); });

// ------------------------------------------------------------------ modales
function modal({ title, body, submit = 'Enregistrer', size = '', danger = false, onSubmit, noFooter = false }) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><form class="modal ${size}" novalidate>
    <div class="modal-head"><h2>${esc(title)}</h2><button type="button" class="btn ghost sm" data-close aria-label="Fermer">✕</button></div>
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
    const btn = $('button[type=submit]', form); if (btn) btn.disabled = true;
    try { const r = await onSubmit(form); if (r !== false) close(); } catch (err) { $('[data-err]', form).textContent = err.message; } finally { if (btn) btn.disabled = false; }
  });
  setTimeout(() => { const f = $('input:not([type=hidden]),select,textarea', form); if (f) f.focus(); }, 30);
  return form;
}
const confirmBox = (title, text, onOk, submit = 'Confirmer') =>
  modal({ title, size: 'sm', danger: true, submit, body: `<p>${text}</p>`, onSubmit: onOk });

function formData(form) {
  const out = {};
  $$('[name]', form).forEach(el => {
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
const chk = (name, label, checked) => `<label class="check"><input type="checkbox" name="${name}"${checked ? ' checked' : ''}> ${esc(label)}</label>`;

// ------------------------------------------------------------------ authentification
async function boot() {
  clearTimers();
  try {
    const st = await api('/api/auth/status');
    state.company = st.company; state.version = st.version;
    if (st.setup_required) return renderSetup();
    if (!st.user) return renderLogin();
    state.user = st.user;
    renderShell();
    route();
  } catch (e) {
    $('#app').innerHTML = `<div class="auth-screen"><div class="auth-card"><h2>Serveur injoignable</h2><p class="muted">${esc(e.message)}</p><button class="btn primary" id="retry">Réessayer</button></div></div>`;
    $('#retry').onclick = boot;
  }
}

function authCard(title, inner) {
  return `<div class="auth-screen"><form class="auth-card form" novalidate>
    <div class="brand">${LOGO}<div>VigieFS<small>Supervision des serveurs de fichiers</small></div></div>
    <h2>${esc(title)}</h2>${inner}<div class="error-text" data-err></div></form></div>`;
}

function renderLogin() {
  $('#app').innerHTML = authCard('Connexion', `
    ${field('Identifiant', inp('username', '', 'text', 'autocomplete="username" required'))}
    ${field('Mot de passe', inp('password', '', 'password', 'autocomplete="current-password" required'))}
    <button class="btn primary" type="submit">Se connecter</button>
    <p class="small muted">Mot de passe perdu : <code>python -m vigiefs --reset-password &lt;identifiant&gt;</code> sur le serveur.</p>`);
  const form = $('form');
  $('[name=username]', form).focus();
  form.addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/auth/login', { method: 'POST', body: formData(form) }); location.hash = location.hash || '#/'; boot(); } catch (err) { $('[data-err]', form).textContent = err.message; }
  });
}

function renderSetup() {
  $('#app').innerHTML = authCard('Première installation', `
    <p class="muted small">Créez le compte administrateur principal. Il pourra ensuite créer les comptes des techniciens.</p>
    ${field('Nom complet', inp('full_name', '', 'text', 'autocomplete="name"'))}
    ${field('Identifiant', inp('username', 'admin', 'text', 'autocomplete="username" required'))}
    ${field('Mot de passe', inp('password', '', 'password', 'autocomplete="new-password" required'), '10 caractères minimum, 3 types parmi : minuscules, majuscules, chiffres, symboles.')}
    ${field('Confirmation', inp('confirm', '', 'password', 'autocomplete="new-password" required'))}
    <button class="btn primary" type="submit">Créer le compte et continuer</button>`);
  const form = $('form');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const d = formData(form);
    if (d.password !== d.confirm) { $('[data-err]', form).textContent = 'Les mots de passe ne correspondent pas.'; return; }
    try {
      await api('/api/auth/setup', { method: 'POST', body: { username: d.username, password: d.password, full_name: d.full_name } });
      await api('/api/auth/login', { method: 'POST', body: { username: d.username, password: d.password } });
      location.hash = '#/clients'; boot();
    } catch (err) { $('[data-err]', form).textContent = err.message; }
  });
}

// ------------------------------------------------------------------ squelette
const NAV = [
  ['#/', 'dashboard', 'Tableau de bord'],
  ['#/clients', 'clients', 'Clients'],
  ['#/shares', 'folder', 'Dossiers supervisés'],
  ['#/alerts', 'bell', 'Alertes'],
  ['#/events', 'list', "Journal d'activité"],
  ['#/reports', 'report', 'Rapports'],
  ['#/settings', 'settings', 'Paramètres'],
];

function renderShell() {
  const u = state.user;
  $('#app').innerHTML = `<div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="brand">${LOGO}<div>VigieFS<small>${esc(state.company || '')}</small></div></div>
      <ul class="nav">${NAV.map(([href, ic, label]) => `<li><a href="${href}" data-nav="${href}">${icon(ic)}<span>${label}</span>${href === '#/alerts' ? '<span class="badge-count" id="nav-alerts" hidden></span>' : ''}</a></li>`).join('')}</ul>
      <div class="sidebar-foot">
        <div class="user">${esc(u.full_name || u.username)}</div><div>${esc(ROLE_LABEL[u.role])}</div>
        <div class="btn-row"><button class="btn sm" id="theme-btn" title="Basculer le thème">${icon('moon')} Thème</button>
        <button class="btn sm" id="logout-btn">Déconnexion</button></div>
        <div class="small" style="margin-top:8px;opacity:.6">v${esc(state.version)}</div>
      </div>
    </aside>
    <div class="main">
      <header class="topbar"><button class="btn sm menu-toggle" id="menu-btn" aria-label="Menu">${icon('menu')}</button>
        <div><div class="crumbs" id="crumbs"></div><h1 id="page-title"></h1></div><div class="spacer"></div><div class="btn-row" id="page-actions"></div></header>
      <main class="content" id="view"></main>
    </div></div>`;
  $('#logout-btn').onclick = async () => { await api('/api/auth/logout', { method: 'POST' }).catch(() => {}); state.user = null; boot(); };
  $('#theme-btn').onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('vigiefs-theme', next); } catch { /* stockage indisponible */ }
  };
  $('#menu-btn').onclick = () => $('#sidebar').classList.toggle('open');
  refreshAlertBadge();
  setInterval(refreshAlertBadge, 30000);
}

async function refreshAlertBadge() {
  if (!state.user) return;
  try {
    const d = await api('/api/alerts?status=open&limit=2000');
    const b = $('#nav-alerts'); if (!b) return;
    const crit = d.filter(a => a.severity === 'critical').length;
    b.hidden = d.length === 0; b.textContent = d.length;
    b.style.background = crit ? 'var(--critical)' : 'var(--accent)';
  } catch { /* silencieux */ }
}

function setPage(title, crumbs = '', actions = '') {
  $('#page-title').textContent = title;
  $('#crumbs').innerHTML = crumbs;
  $('#page-actions').innerHTML = actions;
  document.title = `${title} — VigieFS`;
}

// ------------------------------------------------------------------ routeur
const ROUTES = [
  [/^\/?$/, viewDashboard],
  [/^\/clients$/, viewClients],
  [/^\/shares$/, viewShares],
  [/^\/share\/(\d+)(?:\/(\w+))?$/, viewShare],
  [/^\/alerts$/, viewAlerts],
  [/^\/events$/, viewEvents],
  [/^\/reports$/, viewReports],
  [/^\/settings(?:\/(\w+))?$/, viewSettings],
];
function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path, query: new URLSearchParams(qs || '') };
}
function route() {
  if (!state.user) return;
  clearTimers(); hideTip();
  $('#modal-root').innerHTML = '';
  $('#sidebar').classList.remove('open');
  const { path, query } = parseHash();
  const view = $('#view');
  const fresh = view.cloneNode(false); view.replaceWith(fresh); // purge des écouteurs précédents
  const pa = $('#page-actions'); pa.replaceWith(pa.cloneNode(false));
  $$('[data-nav]').forEach(a => {
    const href = a.dataset.nav.slice(1);
    a.classList.toggle('active', href === '/' ? path === '/' : path.startsWith(href) || (href === '/shares' && path.startsWith('/share/')));
  });
  for (const [re, fn] of ROUTES) {
    const m = path.match(re);
    if (m) { guard(fn)(fresh, query, ...m.slice(1)); return; }
  }
  setPage('Page introuvable'); fresh.innerHTML = '<div class="card empty">Cette page n\'existe pas.</div>';
}
addEventListener('hashchange', route);

// ------------------------------------------------------------------ tableau de bord
async function viewDashboard(view) {
  setPage('Tableau de bord', esc(state.company || ''), `<button class="btn" data-refresh>${icon('refresh')} Actualiser</button>`);
  const render = async () => {
    const d = await api('/api/dashboard');
    const c = d.counts; const a = d.alerts;
    view.innerHTML = `
      ${a.critical ? `<a class="banner critical" href="#/alerts?severity=critical">⚠ ${a.critical} alerte(s) critique(s) en cours — cliquez pour traiter</a>`
        : `<div class="banner ok">✓ Aucune alerte critique. ${c.unavailable ? '' : 'Tous les dossiers supervisés sont accessibles.'}</div>`}
      <div class="tiles">
        <div class="tile link" data-go="#/clients"><div class="label">Clients</div><div class="value">${fmtNum(c.clients)}</div></div>
        <div class="tile link" data-go="#/shares"><div class="label">Dossiers supervisés</div><div class="value">${fmtNum(c.shares)}</div><div class="sub">${c.monitored} surveillé(s) en temps réel</div></div>
        <div class="tile"><div class="label">Volume total</div><div class="value">${fmtBytes(c.total_size)}</div><div class="sub">${compact(c.total_files)} fichiers</div></div>
        <div class="tile link" data-go="#/alerts?severity=critical"><div class="label">Alertes critiques</div><div class="value">${fmtNum(a.critical)}</div><div class="sub">${a.critical ? sev('critical') : 'aucune'}</div></div>
        <div class="tile link" data-go="#/alerts?severity=warning"><div class="label">Avertissements</div><div class="value">${fmtNum(a.warning)}</div><div class="sub">${a.warning ? sev('warning') : 'aucun'}</div></div>
        <div class="tile link" data-go="#/events"><div class="label">Événements fichiers (24 h)</div><div class="value">${compact(c.events_24h)}</div><div class="sub">${c.scans_running} analyse(s) en cours</div></div>
        <div class="tile link" data-go="#/shares"><div class="label">Dossiers inaccessibles</div><div class="value">${fmtNum(c.unavailable)}</div><div class="sub">${c.unavailable ? sev('critical') : sev('ok')}</div></div>
      </div>
      <div class="grid span-2-1">
        <div class="card"><div class="card-head"><h2>Activité fichiers — 24 dernières heures</h2><div class="spacer"></div><button class="btn sm ghost view-toggle" data-act="toggleTable">Voir le tableau</button></div>
          <div id="act-chart"></div><div id="act-table" hidden></div></div>
        <div class="card"><div class="card-head"><h2>Alertes en cours</h2><div class="spacer"></div><a href="#/alerts" class="small">Tout voir →</a></div>
          ${d.recent_alerts.length ? `<table><tbody>${d.recent_alerts.map(r => `<tr class="clickable" data-go="#/alerts"><td>${sev(r.severity)}</td><td><div>${esc(r.title)}</div><div class="small muted">${esc(r.client_name || '')}${r.share_name ? ' · ' + esc(r.share_name) : ''} · ${fmtAgo(r.last_seen)}</div></td></tr>`).join('')}</tbody></table>` : '<div class="empty">Aucune alerte active 🎉</div>'}
        </div>
      </div>
      <div class="grid cols-2" style="margin-top:16px">
        <div class="card"><h2>Quotas à surveiller</h2>
          ${d.quota_watch.length ? `<table><tbody>${d.quota_watch.map(s => `<tr class="clickable" data-go="#/share/${s.id}"><td><div>${esc(s.name)}</div><div class="small muted">${fmtBytes(s.last_size)} / ${fmtBytes(s.quota_bytes)}</div></td><td style="width:45%">${meter(s.quota_pct, s.quota_level)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Aucun quota défini. Définissez des quotas sur les dossiers pour être alerté avant saturation.</div>'}
        </div>
        <div class="card"><h2>Volume par client</h2>${hbars(d.clients.map(x => ({ name: x.name, value: x.size, files: x.files, shares: x.shares })), { sub: i => `${fmtNum(i.files)} fichiers · ${i.shares} dossier(s)` })}</div>
      </div>`;
    state.redraw = [];
    columnChart($('#act-chart', view), d.activity.map(b => {
      const h = new Date(b.ts * 1000).getHours();
      return { label: `${String(h).padStart(2, '0')}h`, value: b.count, tip: `${String(h).padStart(2, '0')}h00 – ${String((h + 1) % 24).padStart(2, '0')}h00<br><b>${fmtNum(b.count)}</b> événement(s)` };
    }), { every: 3 });
    $('#act-table', view).innerHTML = `<div class="table-wrap tall"><table><thead><tr><th>Heure</th><th class="num">Événements</th></tr></thead><tbody>${d.activity.map(b => `<tr><td>${fmtDate(b.ts)}</td><td class="num">${fmtNum(b.count)}</td></tr>`).join('')}</tbody></table></div>`;
  };
  view.addEventListener('click', e => { const g = e.target.closest('[data-go]'); if (g) location.hash = g.dataset.go; });
  bindActions(view, { toggleTable: el => { const t = $('#act-table', view); const c = $('#act-chart', view); t.hidden = !t.hidden; c.hidden = !t.hidden; el.textContent = t.hidden ? 'Voir le tableau' : 'Voir le graphique'; } });
  $('[data-refresh]').onclick = guard(render);
  await render();
  every(15000, guard(render));
}

// ------------------------------------------------------------------ clients
function clientForm(c = {}) {
  return `<div class="form-grid">
    ${field('Nom du client', inp('name', c.name, 'text', 'required maxlength="120"'), '', true)}
    ${field('Contact', inp('contact_name', c.contact_name))}
    ${field('E-mail du contact', inp('contact_email', c.contact_email, 'email'))}
    ${field('Notes', `<textarea name="notes">${esc(c.notes || '')}</textarea>`, 'Contrat, SLA, référence Datto, particularités…', true)}
  </div>`;
}

async function viewClients(view) {
  setPage('Clients', 'Gestion', can('operator') ? `<button class="btn primary" data-new>${icon('plus')} Nouveau client</button>` : '');
  const render = async () => {
    const rows = await api('/api/clients');
    view.innerHTML = `<div class="card"><div class="filters"><input type="search" placeholder="Rechercher un client…" id="q"></div>
      <div class="table-wrap"><table><thead><tr><th>Client</th><th>Contact</th><th class="num">Dossiers</th><th class="num">Volume</th><th class="num">Fichiers</th><th>Alertes</th><th></th></tr></thead>
      <tbody>${rows.length ? rows.map(c => `<tr class="clickable" data-go="#/shares?client=${c.id}" data-search="${esc((c.name + ' ' + c.contact_name + ' ' + c.contact_email).toLowerCase())}">
        <td><b>${esc(c.name)}</b>${c.notes ? `<div class="small muted">${esc(c.notes.slice(0, 80))}</div>` : ''}</td>
        <td>${esc(c.contact_name || '—')}${c.contact_email ? `<div class="small"><a href="mailto:${esc(c.contact_email)}">${esc(c.contact_email)}</a></div>` : ''}</td>
        <td class="num">${c.share_count}</td><td class="num">${fmtBytes(c.total_size)}</td><td class="num">${fmtNum(c.total_files)}</td>
        <td>${c.critical_alerts ? sev('critical') + ' ' : ''}${c.open_alerts ? `<span class="pill">${c.open_alerts} ouverte(s)</span>` : sev('ok')}</td>
        <td class="nowrap">${can('operator') ? `<button class="btn sm" data-act="edit" data-id="${c.id}">Modifier</button>` : ''}
        ${can('admin') ? `<button class="btn sm danger" data-act="del" data-id="${c.id}">Supprimer</button>` : ''}</td></tr>`).join('')
        : `<tr><td colspan="7" class="empty">Aucun client. ${can('operator') ? 'Commencez par créer votre premier client.' : ''}</td></tr>`}</tbody></table></div></div>`;
    $('#q', view).addEventListener('input', e => { const q = e.target.value.toLowerCase(); $$('tr[data-search]', view).forEach(tr => { tr.hidden = !tr.dataset.search.includes(q); }); });
    view._rows = rows;
  };
  view.addEventListener('click', e => { if (e.target.closest('button,a')) return; const g = e.target.closest('[data-go]'); if (g) location.hash = g.dataset.go; });
  bindActions(view, {
    edit: el => { const c = view._rows.find(r => r.id == el.dataset.id); modal({ title: `Modifier « ${c.name} »`, body: clientForm(c), onSubmit: async f => { await api(`/api/clients/${c.id}`, { method: 'PUT', body: formData(f) }); toast('Client mis à jour'); render(); } }); },
    del: el => { const c = view._rows.find(r => r.id == el.dataset.id); confirmBox('Supprimer le client', `Supprimer <b>${esc(c.name)}</b> et retirer ses ${c.share_count} dossier(s) de la supervision ? <br><br><span class="muted">Les fichiers sur disque ne sont <b>pas</b> supprimés.</span>`, async () => { await api(`/api/clients/${c.id}`, { method: 'DELETE' }); toast('Client supprimé'); render(); }, 'Supprimer'); },
  });
  const nb = $('[data-new]'); if (nb) nb.onclick = () => modal({ title: 'Nouveau client', body: clientForm(), submit: 'Créer', onSubmit: async f => { await api('/api/clients', { method: 'POST', body: formData(f) }); toast('Client créé'); render(); } });
  await render();
}

// ------------------------------------------------------------------ dossiers supervisés
const INTERVALS = [[0, 'Désactivée (manuelle)'], [60, 'Toutes les heures'], [180, 'Toutes les 3 h'], [360, 'Toutes les 6 h'], [720, 'Toutes les 12 h'], [1440, 'Quotidienne'], [10080, 'Hebdomadaire']];

function shareForm(s, clients) {
  s = Object.assign({ quota_warn_pct: 80, quota_crit_pct: 95, scan_interval_min: 360, stale_days: 365, disk_free_warn_pct: 10, monitor_enabled: 1, screening_enabled: 1, screening_action: 'alert', quota_bytes: 0 }, s || {});
  const intervals = INTERVALS.some(([v]) => v == s.scan_interval_min) ? INTERVALS : INTERVALS.concat([[s.scan_interval_min, `Toutes les ${s.scan_interval_min} min`]]);
  return `<div class="form">
    <div class="form-grid">
      ${field('Client', sel('client_id', clients.map(c => [c.id, c.name]), s.client_id, 'required'))}
      ${field('Nom du dossier', inp('name', s.name, 'text', 'required placeholder="ex. Comptabilité"'))}
      ${field('Chemin', inp('path', s.path, 'text', 'required placeholder="D:\\Partages\\Client\\Compta  ou  \\\\SRV-FS01\\Compta"'), 'Chemin local ou UNC accessible par le compte de service VigieFS.', true)}
      ${field('Description', inp('description', s.description), '', true)}
    </div>
    <fieldset><legend>Quota</legend><div class="form-grid">
      ${field('Quota (Go)', inp('quota_gb', s.quota_bytes ? +(s.quota_bytes / GB).toFixed(2) : '', 'number', 'min="0" step="0.1" placeholder="0 = illimité"'), 'Quota logiciel : alertes et prévisions (pas de blocage d\'écriture).')}
      ${field('Seuils d\'alerte (%)', `<div class="btn-row"><input type="number" name="quota_warn_pct" value="${s.quota_warn_pct}" min="1" max="100" style="width:90px" aria-label="avertissement"> <span class="muted">avert.</span> <input type="number" name="quota_crit_pct" value="${s.quota_crit_pct}" min="1" max="200" style="width:90px" aria-label="critique"> <span class="muted">critique</span></div>`)}
    </div></fieldset>
    <fieldset><legend>Supervision</legend><div class="form-grid">
      ${field('Analyse automatique', sel('scan_interval_min', intervals, s.scan_interval_min))}
      ${field('Données inactives après (jours)', inp('stale_days', s.stale_days, 'number', 'min="0"'), 'Fichiers non modifiés depuis — candidats à l\'archivage.')}
      ${field('Alerte si espace libre du volume < (%)', inp('disk_free_warn_pct', s.disk_free_warn_pct, 'number', 'min="0" max="99"'), '0 = désactivé.')}
      <div class="field" style="justify-content:flex-end">${chk('monitor_enabled', 'Surveillance temps réel (journal + détection rançongiciel)', s.monitor_enabled)}</div>
    </div></fieldset>
    <fieldset><legend>Filtrage de fichiers</legend><div class="form-grid">
      <div class="field">${chk('screening_enabled', 'Activer le filtrage', s.screening_enabled)}</div>
      ${field('Action', sel('screening_action', [['alert', 'Alerter uniquement'], ['quarantine', 'Alerter et mettre en quarantaine']], s.screening_action), 'La quarantaine nécessite un dossier configuré dans Paramètres › Sécurité.')}
      ${field('Extensions bloquées', `<textarea name="blocked_extensions" placeholder="Vide = liste par défaut (Paramètres)">${esc(s.blocked_extensions || '')}</textarea>`, 'Séparées par des virgules, ex. .exe, .mp4, .iso', true)}
    </div></fieldset></div>`;
}
function shareBody(f) {
  const d = formData(f);
  d.client_id = Number(d.client_id);
  d.quota_bytes = Math.round((d.quota_gb || 0) * GB); delete d.quota_gb;
  d.scan_interval_min = Number(d.scan_interval_min);
  for (const k of ['quota_warn_pct', 'quota_crit_pct', 'stale_days', 'disk_free_warn_pct']) d[k] = d[k] ?? 0;
  return d;
}
async function openShareForm(share, onDone, presetClient) {
  const clients = await api('/api/clients');
  if (!clients.length) { toast("Créez d'abord un client.", 'error'); location.hash = '#/clients'; return; }
  modal({
    title: share ? `Modifier « ${share.name} »` : 'Ajouter un dossier supervisé', body: shareForm(share || { client_id: presetClient }, clients), submit: share ? 'Enregistrer' : 'Ajouter',
    onSubmit: async f => {
      const body = shareBody(f);
      if (share) await api(`/api/shares/${share.id}`, { method: 'PUT', body });
      else await api('/api/shares', { method: 'POST', body });
      toast(share ? 'Dossier mis à jour' : 'Dossier ajouté — première analyse lancée');
      onDone && onDone();
    },
  });
}

function monitorCell(s) {
  const st = s.monitor_status || '';
  const cls = st === 'active' ? 'on' : st.startsWith('erreur') ? 'err' : '';
  const label = st === 'active' ? 'Temps réel' : st === 'désactivée' ? 'Désactivée' : st.startsWith('erreur') ? 'Erreur' : st;
  return `<span class="nowrap" data-tip="${esc(esc(st))}"><span class="dot ${cls}"></span>${esc(label)}</span>`;
}
function scanCell(s) {
  if (s.scan_running) return `<span class="nowrap"><span class="dot run"></span>En cours… ${compact(s.scan_progress?.file_count || 0)} fichiers</span>`;
  const bad = s.last_scan_status && s.last_scan_status !== 'ok';
  return `<span class="nowrap">${fmtAgo(s.last_scan_at)}</span>${bad ? ` <span class="pill err">${esc(s.last_scan_status)}</span>` : ''}`;
}

async function viewShares(view, query) {
  const clientFilter = query.get('client') || '';
  setPage('Dossiers supervisés', 'Gestion', can('operator') ? `<button class="btn primary" data-new>${icon('plus')} Ajouter un dossier</button>` : '');
  const clients = await api('/api/clients');
  view.innerHTML = `<div class="card"><div class="filters">
      ${sel('client', [['', 'Tous les clients'], ...clients.map(c => [c.id, c.name])], clientFilter, 'id="f-client"')}
      <input type="search" id="q" placeholder="Rechercher (nom, chemin)…">
    </div><div id="list"></div></div>`;
  const render = async () => {
    const cid = $('#f-client', view).value;
    const rows = await api('/api/shares' + (cid ? `?client_id=${cid}` : ''));
    const q = $('#q', view).value.toLowerCase();
    $('#list', view).innerHTML = `<div class="table-wrap"><table><thead><tr><th>Client</th><th>Dossier</th><th class="num">Volume</th><th>Quota</th><th>Surveillance</th><th>Dernière analyse</th><th>Alertes</th><th></th></tr></thead><tbody>
      ${rows.length ? rows.map(s => `<tr class="clickable" data-go="#/share/${s.id}" data-search="${esc((s.name + ' ' + s.path + ' ' + s.client_name).toLowerCase())}" ${q && !(s.name + ' ' + s.path + ' ' + s.client_name).toLowerCase().includes(q) ? 'hidden' : ''}>
        <td>${esc(s.client_name)}</td>
        <td><b>${esc(s.name)}</b>${s.path_available ? '' : ' <span class="sev critical">Inaccessible</span>'}<div class="small muted mono share-path">${esc(s.path)}</div></td>
        <td class="num">${fmtBytes(s.last_size)}<div class="small muted">${compact(s.last_file_count)} fichiers</div></td>
        <td style="min-width:170px">${meter(s.quota_pct, s.quota_level)}</td>
        <td>${monitorCell(s)}</td><td>${scanCell(s)}</td>
        <td>${s.open_alerts ? `<span class="pill err">${s.open_alerts} ouverte(s)</span>` : sev('ok')}</td>
        <td class="nowrap">${can('operator') ? (s.scan_running ? `<button class="btn sm" data-act="cancel" data-id="${s.id}">${icon('stop')} Arrêter</button>` : `<button class="btn sm" data-act="scan" data-id="${s.id}" ${s.path_available ? '' : 'disabled'}>${icon('play')} Analyser</button>`) : ''}</td>
      </tr>`).join('') : `<tr><td colspan="8" class="empty">Aucun dossier supervisé. ${can('operator') ? 'Ajoutez un dossier (chemin local ou partage UNC) pour démarrer la supervision.' : ''}</td></tr>`}
      </tbody></table></div>`;
    return rows.some(r => r.scan_running);
  };
  $('#f-client', view).addEventListener('change', () => { history.replaceState(null, '', '#/shares' + ($('#f-client', view).value ? `?client=${$('#f-client', view).value}` : '')); render(); });
  $('#q', view).addEventListener('input', e => { const q = e.target.value.toLowerCase(); $$('tr[data-search]', view).forEach(tr => { tr.hidden = !tr.dataset.search.includes(q); }); });
  view.addEventListener('click', e => { if (e.target.closest('button,a,input,select')) return; const g = e.target.closest('[data-go]'); if (g) location.hash = g.dataset.go; });
  bindActions(view, {
    scan: async el => { await api(`/api/shares/${el.dataset.id}/scan`, { method: 'POST' }); toast('Analyse lancée'); render(); },
    cancel: async el => { await api(`/api/shares/${el.dataset.id}/scan/cancel`, { method: 'POST' }); toast('Arrêt demandé'); render(); },
  });
  const nb = $('[data-new]'); if (nb) nb.onclick = () => openShareForm(null, render, clientFilter);
  await render();
  every(5000, guard(render));
}

// ------------------------------------------------------------------ détail d'un dossier
const SHARE_TABS = [['overview', "Vue d'ensemble"], ['report', 'Rapport de stockage'], ['explorer', 'Explorateur'], ['activity', 'Activité'], ['alerts', 'Alertes'], ['duplicates', 'Doublons']];

async function viewShare(view, query, id, tab = 'overview') {
  let s = await api(`/api/shares/${id}`);
  const actions = () => (can('operator') ? (s.scan_running ? `<button class="btn" data-hact="cancel">${icon('stop')} Arrêter l'analyse</button>` : `<button class="btn primary" data-hact="scan" ${s.path_available ? '' : 'disabled'}>${icon('play')} Analyser maintenant</button>`) + `<button class="btn" data-hact="edit">Modifier</button>` : '') + (can('admin') ? `<button class="btn danger" data-hact="del">Retirer</button>` : '');
  const header = () => setPage(s.name, `<a href="#/shares">Dossiers</a> › <a href="#/shares?client=${s.client_id}">${esc(s.client_name)}</a> › <span class="mono">${esc(s.path)}</span>`, actions());
  header();
  $('#page-actions').addEventListener('click', guard(async e => {
    const b = e.target.closest('[data-hact]'); if (!b) return;
    const act = b.dataset.hact;
    if (act === 'scan') { await api(`/api/shares/${id}/scan`, { method: 'POST' }); toast('Analyse lancée'); }
    if (act === 'cancel') { await api(`/api/shares/${id}/scan/cancel`, { method: 'POST' }); toast('Arrêt demandé'); }
    if (act === 'edit') return openShareForm(s, route);
    if (act === 'del') return confirmBox('Retirer de la supervision', `Retirer <b>${esc(s.name)}</b> de la supervision ? L'historique sera effacé.<br><br><span class="muted">Les fichiers ne sont <b>pas</b> supprimés.</span>`, async () => { await api(`/api/shares/${id}`, { method: 'DELETE' }); toast('Dossier retiré'); location.hash = '#/shares'; }, 'Retirer');
    s = await api(`/api/shares/${id}`); header();
  }));
  view.innerHTML = `<nav class="tabs">${SHARE_TABS.map(([k, l]) => `<a href="#/share/${id}/${k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</nav><div id="tab"></div>`;
  const pane = $('#tab', view);
  const fn = { overview: shareOverview, report: shareReport, explorer: shareExplorer, activity: shareActivity, alerts: shareAlerts, duplicates: shareDuplicates }[tab] || shareOverview;
  await fn(pane, s, query);
  every(4000, guard(async () => {
    const was = s.scan_running;
    s = await api(`/api/shares/${id}`);
    if (was !== s.scan_running) { header(); if (!s.scan_running && (tab === 'overview' || tab === 'report')) fn(pane, s, query); }
    const prog = $('#scan-progress', view);
    if (prog) prog.textContent = s.scan_running ? `Analyse en cours… ${fmtNum(s.scan_progress?.file_count || 0)} fichiers, ${fmtBytes(s.scan_progress?.total_size || 0)}` : '';
  }));
}

async function shareOverview(pane, s) {
  const growth = s.growth_per_day;
  const hist = s.history.filter(h => h.status === 'done').reverse();
  const free = s.disk ? (s.disk.free * 100 / s.disk.total) : null;
  pane.innerHTML = `
    <div class="small muted" id="scan-progress" style="margin-bottom:8px">${s.scan_running ? 'Analyse en cours…' : ''}</div>
    ${s.path_available ? '' : '<div class="banner critical">⚠ Le chemin est actuellement inaccessible. Vérifiez le volume ou le partage réseau.</div>'}
    <div class="tiles">
      <div class="tile"><div class="label">Volume</div><div class="value">${fmtBytes(s.last_size)}</div><div class="sub">${s.quota_bytes ? 'sur ' + fmtBytes(s.quota_bytes) + ' de quota' : 'sans quota'}</div></div>
      <div class="tile"><div class="label">Fichiers</div><div class="value">${fmtNum(s.last_file_count)}</div><div class="sub">analysé ${fmtAgo(s.last_scan_at)}</div></div>
      <div class="tile"><div class="label">Croissance (30 j)</div><div class="value">${growth == null ? '—' : (growth >= 0 ? '+' : '') + fmtBytes(growth)}</div><div class="sub">par jour</div></div>
      <div class="tile"><div class="label">Saturation prévue</div><div class="value">${s.forecast_days == null ? '—' : s.forecast_days < 1 ? 'Atteinte' : Math.round(s.forecast_days) + ' j'}</div><div class="sub">${s.quota_bytes ? (s.forecast_days == null ? 'pas de croissance mesurée' : 'au rythme actuel') : 'définir un quota'}</div></div>
      <div class="tile"><div class="label">Espace libre du volume</div><div class="value">${s.disk ? fmtBytes(s.disk.free) : '—'}</div><div class="sub">${free == null ? '' : fmtPct(free) + ' de ' + fmtBytes(s.disk.total)}</div></div>
    </div>
    <div class="grid span-2-1">
      <div class="card"><h2>Évolution du volume</h2><div id="growth"></div></div>
      <div class="card"><h2>Quota</h2><div style="margin:6px 0 16px">${meter(s.quota_pct, s.quota_level)}</div>
        <dl class="kv">
          <dt>Surveillance</dt><dd>${monitorCell(s)}</dd>
          <dt>Analyse auto</dt><dd>${esc((INTERVALS.find(([v]) => v == s.scan_interval_min) || [0, s.scan_interval_min + ' min'])[1])}</dd>
          <dt>Filtrage</dt><dd>${s.screening_enabled ? (s.screening_action === 'quarantine' ? 'Alerte + quarantaine' : 'Alerte') : 'Désactivé'}</dd>
          <dt>Extensions</dt><dd class="small mono">${esc(s.blocked_extensions || 'liste par défaut')}</dd>
          <dt>Inactivité</dt><dd>${s.stale_days ? s.stale_days + ' jours' : '—'}</dd>
          <dt>Seuils quota</dt><dd>${s.quota_warn_pct} % / ${s.quota_crit_pct} %</dd>
          ${s.description ? `<dt>Description</dt><dd>${esc(s.description)}</dd>` : ''}
        </dl></div>
    </div>
    <div class="card"><h2>Historique des analyses</h2><div class="table-wrap tall"><table><thead><tr><th>Début</th><th>Durée</th><th>Statut</th><th class="num">Volume</th><th class="num">Fichiers</th><th class="num">Erreurs</th><th></th></tr></thead><tbody>
      ${s.history.length ? s.history.map(h => `<tr><td>${fmtDate(h.started_at)}</td><td>${h.finished_at ? fmtDuration(h.finished_at - h.started_at) : '—'}</td>
        <td>${{ done: sev('ok').replace('OK', 'Terminée'), running: '<span class="dot run"></span>En cours', failed: '<span class="sev critical">Échec</span>', cancelled: '<span class="pill">Annulée</span>' }[h.status] || esc(h.status)}</td>
        <td class="num">${fmtBytes(h.total_size)}</td><td class="num">${fmtNum(h.file_count)}</td><td class="num">${fmtNum(h.error_count)}</td>
        <td>${h.status === 'done' ? `<a href="#/share/${s.id}/report?scan=${h.id}">Rapport</a>` : ''}</td></tr>`).join('') : '<tr><td colspan="7" class="empty">Aucune analyse pour le moment.</td></tr>'}
    </tbody></table></div></div>`;
  lineChart($('#growth', pane), hist.map(h => ({ x: h.finished_at, y: h.total_size, extra: `${fmtNum(h.file_count)} fichiers` })));
}

function fileTable(files, { empty = 'Aucun fichier', cols = ['size', 'mtime'] } = {}) {
  if (!files || !files.length) return `<div class="empty">${empty}</div>`;
  return `<div class="table-wrap tall"><table><thead><tr><th>Chemin</th>${cols.includes('size') ? '<th class="num">Taille</th>' : ''}${cols.includes('mtime') ? '<th>Modifié</th>' : ''}${cols.includes('kind') ? '<th>Type</th>' : ''}</tr></thead><tbody>
    ${files.map(f => `<tr><td class="path">${esc(f.path)}</td>${cols.includes('size') ? `<td class="num">${fmtBytes(f.size)}</td>` : ''}${cols.includes('mtime') ? `<td class="nowrap">${fmtDate(f.mtime)}</td>` : ''}${cols.includes('kind') ? `<td>${f.kind === 'note' ? 'Note de rançon' : 'Extension chiffrée'}</td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}

async function shareReport(pane, s, query) {
  const scanId = query.get('scan');
  const { scan } = await api(`/api/shares/${s.id}/report` + (scanId ? `?scan_id=${scanId}` : ''));
  if (!scan) { pane.innerHTML = `<div class="card empty">Aucun rapport disponible. ${can('operator') ? 'Lancez une analyse avec le bouton « Analyser maintenant ».' : ''}</div><div class="small muted" id="scan-progress"></div>`; return; }
  const r = scan.report;
  const done = s.history.filter(h => h.status === 'done');
  pane.innerHTML = `
    <div class="filters"><span class="label-text">Analyse du</span>${sel('scan', done.map(h => [h.id, fmtDate(h.started_at)]), scan.id, 'id="scan-sel" style="width:auto"')}
      <span class="small muted" id="scan-progress"></span></div>
    ${r.ransomware.length ? `<div class="banner critical">⚠ ${r.ransomware.length} fichier(s) suspects de rançongiciel détectés — voir la section dédiée ci-dessous.</div>` : ''}
    <div class="tiles">
      <div class="tile"><div class="label">Volume analysé</div><div class="value">${fmtBytes(r.total_size)}</div><div class="sub">en ${fmtDuration(scan.finished_at - scan.started_at)}</div></div>
      <div class="tile"><div class="label">Fichiers / dossiers</div><div class="value">${compact(r.file_count)}</div><div class="sub">${fmtNum(r.dir_count)} dossiers</div></div>
      <div class="tile"><div class="label">Données inactives</div><div class="value">${fmtBytes(r.stale_size)}</div><div class="sub">${fmtNum(r.stale_count)} fichiers non modifiés depuis ${s.stale_days} j${r.total_size ? ' · ' + fmtPct(r.stale_size * 100 / r.total_size, 0) : ''}</div></div>
      <div class="tile"><div class="label">Fichiers interdits</div><div class="value">${fmtNum(r.screened.length)}</div><div class="sub">${r.screened.length ? sev('warning') : sev('ok')}</div></div>
      <div class="tile"><div class="label">Éléments illisibles</div><div class="value">${fmtNum(r.error_count)}</div><div class="sub">droits d'accès</div></div>
    </div>
    <div class="grid cols-2">
      <div class="card"><h2>Volume par type de fichier</h2>${hbars(r.by_extension.map(e => ({ name: e.ext, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div class="card"><h2>Volume par sous-dossier</h2>${hbars(r.top_folders.slice(0, 15).map(e => ({ name: e.name, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div class="card"><h2>Ancienneté des données (dernière modification)</h2>${hbars(r.ages.map(e => ({ name: e.label, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div class="card"><h2>Les plus gros fichiers</h2>${fileTable(r.largest)}</div>
    </div>
    <div class="card"><h2>Fichiers inactifs les plus volumineux</h2><p class="small muted">Candidats à l'archivage ou à la purge (après validation avec le client).</p>${fileTable(r.stale_largest, { empty: 'Aucun fichier inactif' })}</div>
    <div class="card"><h2>Fichiers interdits par le filtrage</h2>${fileTable(r.screened, { empty: 'Aucun fichier interdit ✓' })}</div>
    <div class="card"><h2>Suspects rançongiciel</h2>${fileTable(r.ransomware, { empty: 'Aucune trace détectée ✓', cols: ['size', 'mtime', 'kind'] })}</div>
    ${r.errors.length ? `<div class="card"><h2>Erreurs d'accès (échantillon)</h2><div class="table-wrap"><table><thead><tr><th>Chemin</th><th>Erreur</th></tr></thead><tbody>${r.errors.map(e => `<tr><td class="path">${esc(e.path)}</td><td>${esc(e.error)}</td></tr>`).join('')}</tbody></table></div></div>` : ''}`;
  $('#scan-sel', pane).addEventListener('change', e => { location.hash = `#/share/${s.id}/report?scan=${e.target.value}`; });
}

async function shareExplorer(pane, s, query) {
  let cwd = query.get('path') || '';
  let selected = null;
  pane.innerHTML = `<div class="explorer"><div class="card"><div class="card-head"><div class="breadcrumb" id="bc"></div><div class="spacer"></div>
      <div class="btn-row">${can('operator') ? `<button class="btn sm" data-act="mkdir">${icon('plus')} Nouveau dossier</button>` : ''}<button class="btn sm" data-act="reload">${icon('refresh')}</button></div></div>
      <div id="listing"></div></div>
    <div class="card"><h2>Permissions</h2><div class="small muted" id="perm-target"></div><div id="perms"></div></div></div>`;
  const loadPerms = async rel => {
    $('#perm-target', pane).innerHTML = `<span class="mono">${esc(rel || '(racine)')}</span>`;
    $('#perms', pane).innerHTML = '<div class="muted small">Chargement…</div>';
    try {
      const p = await api(`/api/shares/${s.id}/permissions?path=${encodeURIComponent(rel)}`);
      if (p.error) { $('#perms', pane).innerHTML = `<div class="error-text">${esc(p.error)}</div>`; return; }
      $('#perms', pane).innerHTML = (p.mode ? `<div class="small" style="margin:6px 0"><code>${esc(p.mode)}</code> · ${esc(p.owner)}:${esc(p.group)}</div>` : '') +
        p.entries.map(e => `<div class="acl-entry ${e.deny ? 'deny' : ''}"><div class="who">${e.deny ? '⛔ Refus — ' : ''}${esc(e.principal)}</div><div>${esc(e.rights)}</div>
          <div class="small muted">${e.inherited ? 'Hérité' : 'Explicite'}${e.flags && e.flags.length ? ' · ' + esc(e.flags.join(', ')) : ''}</div>${e.warning ? `<div class="sev warning" style="margin-top:4px">${esc(e.warning)}</div>` : ''}</div>`).join('');
    } catch (err) { $('#perms', pane).innerHTML = `<div class="error-text">${esc(err.message)}</div>`; }
  };
  const load = async () => {
    const d = await api(`/api/shares/${s.id}/browse?path=${encodeURIComponent(cwd)}`);
    cwd = d.path;
    history.replaceState(null, '', `#/share/${s.id}/explorer${cwd ? '?path=' + encodeURIComponent(cwd) : ''}`);
    const parts = cwd ? cwd.split('/') : [];
    $('#bc', pane).innerHTML = `<a href="#" data-act="cd" data-path="">${icon('folder', 'file-icon dir')}${esc(s.name)}</a>` + parts.map((p, i) => ` › <a href="#" data-act="cd" data-path="${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</a>`).join('');
    const join = n => (cwd ? cwd + '/' : '') + n;
    $('#listing', pane).innerHTML = `<div class="table-wrap tall"><table><thead><tr><th>Nom</th><th class="num">Taille</th><th>Modifié</th><th></th></tr></thead><tbody>
      ${cwd ? `<tr class="clickable" data-act="cd" data-path="${esc(parts.slice(0, -1).join('/'))}"><td colspan="4">${icon('folder', 'file-icon dir')}..</td></tr>` : ''}
      ${d.entries.length ? d.entries.map(e => `<tr class="clickable" data-act="${e.is_dir ? 'cd' : 'select'}" data-path="${esc(join(e.name))}">
        <td>${icon(e.is_dir ? 'folder' : 'file', 'file-icon' + (e.is_dir ? ' dir' : ''))}${esc(e.name)}${e.link ? ' <span class="pill">lien</span>' : ''}${e.error ? ' <span class="pill err">illisible</span>' : ''}</td>
        <td class="num">${e.is_dir ? '' : fmtBytes(e.size)}</td><td class="nowrap">${fmtDate(e.mtime)}</td>
        <td class="nowrap">${e.is_dir ? `<button class="btn sm ghost" data-act="perm" data-path="${esc(join(e.name))}" title="Permissions">🔒</button>` : ''}
          ${!e.is_dir && can('operator') ? `<a class="btn sm ghost" href="/api/shares/${s.id}/download?path=${encodeURIComponent(join(e.name))}" title="Télécharger">${icon('download')}</a>` : ''}
          ${can('operator') ? `<button class="btn sm ghost" data-act="rename" data-path="${esc(join(e.name))}" data-name="${esc(e.name)}">Renommer</button>` : ''}
          ${can('admin') ? `<button class="btn sm ghost danger" data-act="del" data-path="${esc(join(e.name))}" data-name="${esc(e.name)}" data-dir="${e.is_dir ? 1 : 0}">Supprimer</button>` : ''}</td></tr>`).join('') : '<tr><td colspan="4" class="empty">Dossier vide</td></tr>'}
      </tbody></table></div>${d.truncated ? '<div class="small muted">Affichage limité aux 5 000 premiers éléments.</div>' : ''}`;
    selected = null;
    loadPerms(cwd);
  };
  bindActions(pane, {
    cd: el => { cwd = el.dataset.path; return load(); },
    select: el => { $$('tr.selected', pane).forEach(t => t.classList.remove('selected')); el.classList.add('selected'); selected = el.dataset.path; loadPerms(selected); },
    perm: el => loadPerms(el.dataset.path),
    reload: load,
    mkdir: () => modal({ title: 'Nouveau dossier', size: 'sm', submit: 'Créer', body: field('Nom', inp('name', '', 'text', 'required')), onSubmit: async f => { await api(`/api/shares/${s.id}/mkdir`, { method: 'POST', body: { path: cwd, name: formData(f).name } }); toast('Dossier créé'); load(); } }),
    rename: el => modal({ title: 'Renommer', size: 'sm', submit: 'Renommer', body: field('Nouveau nom', inp('new_name', el.dataset.name, 'text', 'required')), onSubmit: async f => { await api(`/api/shares/${s.id}/rename`, { method: 'POST', body: { path: el.dataset.path, new_name: formData(f).new_name } }); toast('Élément renommé'); load(); } }),
    del: el => confirmBox('Supprimer définitivement', `Supprimer ${el.dataset.dir === '1' ? 'le dossier' : 'le fichier'} <b class="mono">${esc(el.dataset.path)}</b>${el.dataset.dir === '1' ? ' <b>et tout son contenu</b>' : ''} ?<br><br><span class="muted">Action irréversible et journalisée. Assurez-vous qu'une sauvegarde Datto récente existe.</span>`, async () => { await api(`/api/shares/${s.id}/delete`, { method: 'POST', body: { path: el.dataset.path } }); toast('Élément supprimé'); load(); }, 'Supprimer'),
  });
  if (!s.path_available) { $('#listing', pane).innerHTML = '<div class="empty">Chemin inaccessible.</div>'; return; }
  await load();
}

function eventsTable(rows, showShare = true) {
  if (!rows.length) return '<div class="empty">Aucun événement sur cette période.</div>';
  return `<table><thead><tr><th>Date</th>${showShare ? '<th>Client / dossier</th>' : ''}<th>Type</th><th>Chemin</th><th class="num">Taille</th></tr></thead><tbody>
    ${rows.map(e => `<tr><td class="nowrap">${fmtDate(e.ts, true)}</td>${showShare ? `<td class="nowrap">${esc(e.client_name)}<div class="small muted">${esc(e.share_name)}</div></td>` : ''}
      <td><span class="pill ${e.type.includes('deleted') ? 'err' : e.type.includes('created') ? 'on' : ''}">${esc(EVENT_LABEL[e.type] || e.type)}</span></td>
      <td class="path">${esc(e.path)}${e.dest_path ? ` <span class="muted">→</span> ${esc(e.dest_path)}` : ''}</td><td class="num">${e.size == null ? '' : fmtBytes(e.size)}</td></tr>`).join('')}</tbody></table>`;
}

const PERIODS = [[3600, 'Dernière heure'], [86400, '24 heures'], [7 * 86400, '7 jours'], [30 * 86400, '30 jours'], [0, 'Tout']];
function eventFilters(extra = '') {
  return `<div class="filters">${extra}
    ${sel('type', [['', 'Tous les types'], ...Object.entries(EVENT_LABEL)], '', 'data-f')}
    ${sel('period', PERIODS, 86400, 'data-f')}
    <input type="search" name="q" placeholder="Filtrer par chemin…" data-f>
    <label class="check small"><input type="checkbox" name="live" checked> Temps réel</label>
    <span class="spacer"></span><a class="btn sm" data-export href="#">${icon('download')} Export CSV</a></div>`;
}
function eventParams(root, base = {}) {
  const p = new URLSearchParams();
  Object.entries(base).forEach(([k, v]) => v && p.set(k, v));
  const type = $('[name=type]', root).value; if (type) p.set('type', type);
  const per = Number($('[name=period]', root).value); if (per) p.set('since', Math.floor(Date.now() / 1000 - per));
  const q = $('[name=q]', root).value.trim(); if (q) p.set('q', q);
  return p;
}

async function shareActivity(pane, s) {
  pane.innerHTML = `<div class="card">${eventFilters()}<div id="ev" class="table-wrap tall"></div></div>`;
  const load = async () => {
    const p = eventParams(pane, { share_id: s.id });
    $('[data-export]', pane).href = '/api/events/export.csv?' + p;
    p.set('limit', 500);
    $('#ev', pane).innerHTML = eventsTable(await api('/api/events?' + p), false);
  };
  $$('[data-f]', pane).forEach(el => el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', guard(load)));
  await load();
  every(5000, () => { if ($('[name=live]', pane).checked) guard(load)(); });
}

function alertsTable(rows, { selectable = true, showShare = true } = {}) {
  if (!rows.length) return '<div class="empty">Aucune alerte ✓</div>';
  return `<table><thead><tr>${selectable && can('operator') ? '<th><input type="checkbox" data-all aria-label="Tout sélectionner"></th>' : ''}<th>Gravité</th><th>Alerte</th>${showShare ? '<th>Client / dossier</th>' : ''}<th class="num">Occur.</th><th>Dernière</th><th>Statut</th><th></th></tr></thead><tbody>
    ${rows.map(a => `<tr>${selectable && can('operator') ? `<td>${a.status === 'open' ? `<input type="checkbox" data-sel value="${a.id}" aria-label="Sélectionner">` : ''}</td>` : ''}
      <td>${sev(a.severity)}</td>
      <td><div><b>${esc(a.title)}</b> <span class="pill">${esc(CAT_LABEL[a.category] || a.category)}</span></div><div class="alert-msg">${esc(a.message)}</div>
        <div class="details-box" id="det-${a.id}" hidden>${esc(JSON.stringify(a.details, null, 2))}</div></td>
      ${showShare ? `<td>${a.client_name ? esc(a.client_name) : '—'}${a.share_id ? `<div class="small"><a href="#/share/${a.share_id}">${esc(a.share_name)}</a></div>` : ''}</td>` : ''}
      <td class="num">${a.count}</td>
      <td class="nowrap" data-tip="Première : ${esc(fmtDate(a.first_seen))}<br>Dernière : ${esc(fmtDate(a.last_seen))}">${fmtAgo(a.last_seen)}</td>
      <td><span class="pill">${STATUS_LABEL[a.status]}</span>${a.ack_by ? `<div class="small muted">${esc(a.ack_by)}</div>` : ''}</td>
      <td class="nowrap"><button class="btn sm ghost" data-act="details" data-id="${a.id}">Détails</button>
        ${can('operator') && a.status === 'open' ? `<button class="btn sm" data-act="ack" data-id="${a.id}">Acquitter</button>` : ''}
        ${can('operator') && a.status !== 'resolved' ? `<button class="btn sm ghost" data-act="close" data-id="${a.id}">Clore</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
}
function alertActions(root, reload) {
  return {
    details: el => { const d = $(`#det-${el.dataset.id}`, root); d.hidden = !d.hidden; },
    ack: async el => { await api(`/api/alerts/${el.dataset.id}/ack`, { method: 'POST' }); toast('Alerte acquittée'); reload(); refreshAlertBadge(); },
    close: async el => { await api(`/api/alerts/${el.dataset.id}/close`, { method: 'POST' }); toast('Alerte close'); reload(); refreshAlertBadge(); },
  };
}

async function shareAlerts(pane, s) {
  const load = async () => {
    const rows = await api(`/api/alerts?share_id=${s.id}`);
    pane.innerHTML = `<div class="card table-wrap">${alertsTable(rows, { selectable: false, showShare: false })}</div>`;
  };
  bindActions(pane, alertActions(pane, load));
  await load();
}

async function shareDuplicates(pane, s) {
  const render = async () => {
    const job = await api(`/api/shares/${s.id}/duplicates`);
    const running = job.status === 'running';
    let body = '';
    if (job.status === 'none') body = '<div class="empty">Aucune recherche effectuée. La recherche compare le contenu (SHA-256) des fichiers de même taille.</div>';
    else if (running) body = `<div class="empty"><span class="dot run"></span>Recherche en cours depuis ${fmtDuration(Date.now() / 1000 - job.started)}…</div>`;
    else if (job.status === 'failed') body = `<div class="error-text">Échec : ${esc(job.error)}</div>`;
    else if (job.result) {
      const g = job.result.groups;
      body = `<div class="tiles"><div class="tile"><div class="label">Espace récupérable</div><div class="value">${fmtBytes(job.result.wasted_total)}</div></div><div class="tile"><div class="label">Groupes de doublons</div><div class="value">${fmtNum(g.length)}</div></div></div>
        ${g.length ? `<div class="table-wrap tall"><table><thead><tr><th class="num">Taille</th><th class="num">Copies</th><th class="num">Récupérable</th><th>Fichiers</th></tr></thead><tbody>${g.map(x => `<tr><td class="num">${fmtBytes(x.size)}</td><td class="num">${x.count}</td><td class="num">${fmtBytes(x.wasted)}</td><td class="path">${x.files.map(esc).join('<br>')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Aucun doublon ✓</div>'}`;
    }
    pane.innerHTML = `<div class="card"><div class="card-head"><h2>Fichiers en double</h2><div class="spacer"></div>
      ${can('operator') ? `<label class="small">Taille min. (Mo) <input type="number" id="minsize" value="1" min="0" step="0.5" style="width:80px"></label><button class="btn primary" data-act="run" ${running || !s.path_available ? 'disabled' : ''}>${icon('play')} Lancer la recherche</button>` : ''}</div>${body}
      ${job.finished ? `<div class="small muted">Dernière recherche : ${fmtDate(job.finished)}</div>` : ''}</div>`;
    return running;
  };
  bindActions(pane, { run: async () => { await api(`/api/shares/${s.id}/duplicates?min_size_mb=${$('#minsize', pane).value || 1}`, { method: 'POST' }); render(); } });
  await render();
  every(3000, async () => { if ($('[data-act=run][disabled]', pane)) guard(render)(); });
}

// ------------------------------------------------------------------ alertes (global)
async function viewAlerts(view, query) {
  setPage('Alertes', 'Supervision', `<a class="btn" href="/api/reports/alerts.csv">${icon('download')} Export CSV</a>`);
  const clients = await api('/api/clients');
  view.innerHTML = `<div class="card"><div class="filters">
      ${sel('status', [['active', 'Actives'], ['open', 'Ouvertes'], ['acknowledged', 'Acquittées'], ['resolved', 'Résolues'], ['', 'Toutes']], query.get('status') || 'active', 'data-f')}
      ${sel('severity', [['', 'Toutes gravités'], ['critical', 'Critique'], ['warning', 'Avertissement'], ['info', 'Info']], query.get('severity') || '', 'data-f')}
      ${sel('client_id', [['', 'Tous les clients'], ...clients.map(c => [c.id, c.name])], query.get('client') || '', 'data-f')}
      ${sel('category', [['', 'Toutes catégories'], ...Object.entries(CAT_LABEL)], '', 'data-f')}
      <span class="spacer"></span>${can('operator') ? '<button class="btn" data-act="bulk" disabled>Acquitter la sélection</button>' : ''}
    </div><div id="list" class="table-wrap"></div></div>`;
  const load = async () => {
    const p = new URLSearchParams();
    $$('[data-f]', view).forEach(el => { if (el.value) p.set(el.name, el.value); });
    const rows = await api('/api/alerts?' + p);
    const keep = new Set($$('[data-sel]:checked', view).map(c => c.value));
    const open = new Set($$('.details-box:not([hidden])', view).map(d => d.id));
    $('#list', view).innerHTML = alertsTable(rows);
    $$('[data-sel]', view).forEach(c => { c.checked = keep.has(c.value); });
    open.forEach(id => { const d = $('#' + id, view); if (d) d.hidden = false; });
    syncBulk();
  };
  const syncBulk = () => { const b = $('[data-act=bulk]', view); if (b) b.disabled = !$$('[data-sel]:checked', view).length; };
  view.addEventListener('change', e => {
    if (e.target.matches('[data-all]')) $$('[data-sel]', view).forEach(c => { c.checked = e.target.checked; });
    if (e.target.matches('[data-f]')) load();
    syncBulk();
  });
  bindActions(view, {
    ...alertActions(view, load),
    bulk: async () => { const ids = $$('[data-sel]:checked', view).map(c => Number(c.value)); await api('/api/alerts/bulk-ack', { method: 'POST', body: { ids } }); toast(`${ids.length} alerte(s) acquittée(s)`); load(); refreshAlertBadge(); },
  });
  await load();
  every(15000, guard(load));
}

// ------------------------------------------------------------------ journal global
async function viewEvents(view) {
  setPage("Journal d'activité", 'Supervision');
  const [clients, shares] = await Promise.all([api('/api/clients'), api('/api/shares')]);
  view.innerHTML = `<div class="card">${eventFilters(
    sel('client_id', [['', 'Tous les clients'], ...clients.map(c => [c.id, c.name])], '', 'data-f') +
    sel('share_id', [['', 'Tous les dossiers'], ...shares.map(s => [s.id, `${s.client_name} › ${s.name}`])], '', 'data-f'))}
    <div id="ev" class="table-wrap tall"></div><div class="btn-row" style="margin-top:10px"><button class="btn sm" data-act="more">Charger plus</button><span class="small muted" id="count"></span></div></div>`;
  let limit = 200;
  const load = async () => {
    const p = eventParams(view, { client_id: $('[name=client_id]', view).value, share_id: $('[name=share_id]', view).value });
    $('[data-export]', view).href = '/api/events/export.csv?' + p;
    p.set('limit', limit);
    const rows = await api('/api/events?' + p);
    $('#ev', view).innerHTML = eventsTable(rows);
    $('#count', view).textContent = `${fmtNum(rows.length)} événement(s) affiché(s)`;
  };
  $$('[data-f]', view).forEach(el => el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', () => { limit = 200; guard(load)(); }));
  bindActions(view, { more: () => { limit = Math.min(2000, limit + 300); return load(); } });
  await load();
  every(5000, () => { if ($('[name=live]', view).checked) guard(load)(); });
}

// ------------------------------------------------------------------ rapports
async function viewReports(view) {
  setPage('Rapports', 'Analyse');
  const [clients, shares] = await Promise.all([api('/api/clients'), api('/api/shares')]);
  const totalSize = clients.reduce((a, c) => a + c.total_size, 0);
  view.innerHTML = `
    <div class="grid cols-3">
      <div class="card"><h2>Consommation par client</h2><p class="muted small">Volume et nombre de fichiers par client et par dossier, utilisation des quotas. Idéal pour la facturation du stockage.</p><a class="btn primary" href="/api/reports/usage.csv">${icon('download')} Télécharger (CSV)</a></div>
      <div class="card"><h2>Historique des alertes</h2><p class="muted small">Toutes les alertes avec statut, occurrences et technicien ayant acquitté. Pour les revues mensuelles / comités clients.</p><a class="btn primary" href="/api/reports/alerts.csv">${icon('download')} Télécharger (CSV)</a></div>
      <div class="card"><h2>Journal d'activité</h2><p class="muted small">Créations, modifications, suppressions et renommages, filtrables par client, dossier et période.</p><a class="btn" href="#/events">Ouvrir le journal →</a></div>
    </div>
    <div class="card"><h2>Synthèse par client</h2><div class="table-wrap"><table><thead><tr><th>Client</th><th class="num">Dossiers</th><th class="num">Volume</th><th class="num">Part</th><th class="num">Fichiers</th><th>Alertes ouvertes</th></tr></thead><tbody>
      ${clients.map(c => `<tr><td><a href="#/shares?client=${c.id}">${esc(c.name)}</a></td><td class="num">${c.share_count}</td><td class="num">${fmtBytes(c.total_size)}</td><td class="num">${totalSize ? fmtPct(c.total_size * 100 / totalSize) : '—'}</td><td class="num">${fmtNum(c.total_files)}</td><td>${c.open_alerts ? `<span class="pill err">${c.open_alerts}</span>` : sev('ok')}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">Aucun client</td></tr>'}
    </tbody></table></div></div>
    <div class="card"><h2>Dossiers sans analyse récente (> 48 h)</h2>${(() => {
      const stale = shares.filter(s => !s.last_scan_at || Date.now() / 1000 - s.last_scan_at > 172800);
      return stale.length ? `<table><tbody>${stale.map(s => `<tr><td><a href="#/share/${s.id}">${esc(s.client_name)} › ${esc(s.name)}</a></td><td class="mono small">${esc(s.path)}</td><td>${fmtAgo(s.last_scan_at)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Tous les dossiers ont été analysés récemment ✓</div>';
    })()}</div>`;
}

// ------------------------------------------------------------------ paramètres
const SETTINGS_TABS = [['account', 'Mon compte', 'viewer'], ['general', 'Général', 'admin'], ['notifications', 'Notifications', 'admin'], ['security', 'Sécurité & filtrage', 'admin'], ['retention', 'Prévisions & rétention', 'admin'], ['users', 'Utilisateurs', 'admin'], ['apikeys', "Clés d'API", 'admin'], ['audit', "Journal d'audit", 'admin'], ['integration', 'Intégration & système', 'viewer']];
const listToText = l => (Array.isArray(l) ? l.join(', ') : l || '');
const textToList = t => t.split(/[,;\n]/).map(x => x.trim()).filter(Boolean);

async function viewSettings(view, query, tab) {
  const tabs = SETTINGS_TABS.filter(t => can(t[2]));
  tab = tab && tabs.some(t => t[0] === tab) ? tab : (can('admin') ? 'general' : 'account');
  setPage('Paramètres', 'Administration');
  view.innerHTML = `<nav class="tabs">${tabs.map(([k, l]) => `<a href="#/settings/${k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</nav><div id="pane"></div>`;
  const pane = $('#pane', view);
  const st = can('admin') ? await api('/api/settings') : null;
  const save = async (key, body, msg = 'Paramètres enregistrés') => { await api(`/api/settings/${key}`, { method: 'PUT', body }); toast(msg); };
  const formCard = (title, inner, id, extra = '') => `<form class="card form" id="${id}"><h2>${title}</h2>${inner}<div class="btn-row"><button class="btn primary" type="submit">Enregistrer</button>${extra}</div></form>`;
  const onSubmit = (id, fn) => $('#' + id, pane).addEventListener('submit', guard(async e => { e.preventDefault(); await fn(formData(e.target)); }));

  if (tab === 'account') {
    pane.innerHTML = formCard('Changer mon mot de passe', `<div class="form-grid">
      ${field('Mot de passe actuel', inp('current_password', '', 'password', 'autocomplete="current-password"'), '', true)}
      ${field('Nouveau mot de passe', inp('new_password', '', 'password', 'autocomplete="new-password"'), '10 caractères min., 3 types de caractères.')}
      ${field('Confirmation', inp('confirm', '', 'password', 'autocomplete="new-password"'))}</div>`, 'f-pwd');
    onSubmit('f-pwd', async d => { if (d.new_password !== d.confirm) throw new Error('Les mots de passe ne correspondent pas.'); await api('/api/auth/password', { method: 'POST', body: d }); toast('Mot de passe modifié'); $('#f-pwd', pane).reset(); });
  }
  if (tab === 'general') {
    const g = st.general;
    pane.innerHTML = formCard('Général', `<div class="form-grid">
      ${field("Nom de l'entreprise", inp('company_name', g.company_name), 'Affiché dans l\'interface et les notifications.')}
      ${field('Durée de session (heures)', inp('session_hours', g.session_hours, 'number', 'min="1" max="720"'))}
      ${field('Fréquence du planificateur (s)', inp('scheduler_tick_sec', g.scheduler_tick_sec, 'number', 'min="5" max="3600"'), 'Contrôle de disponibilité des dossiers et déclenchement des analyses.')}</div>`, 'f-gen');
    onSubmit('f-gen', async d => { await save('general', d); state.company = d.company_name; });
  }
  if (tab === 'notifications') {
    const s = st.smtp; const w = st.webhook;
    const sevOpts = [['info', 'Info et plus'], ['warning', 'Avertissement et plus'], ['critical', 'Critique uniquement']];
    pane.innerHTML = formCard('E-mail (SMTP)', `${chk('enabled', 'Activer les notifications par e-mail', s.enabled)}<div class="form-grid">
      ${field('Serveur SMTP', inp('host', s.host, 'text', 'placeholder="smtp.office365.com"'))}
      ${field('Port', inp('port', s.port, 'number'), '587 (STARTTLS) ou 465 (SSL)')}
      ${field('Utilisateur', inp('username', s.username, 'text', 'autocomplete="off"'))}
      ${field('Mot de passe', inp('password', s.password, 'password', 'autocomplete="new-password"'))}
      ${field('Expéditeur', inp('sender', s.sender, 'email'))}
      ${field('Gravité minimale', sel('min_severity', sevOpts, s.min_severity))}
      ${field('Destinataires', inp('recipients', listToText(s.recipients)), 'Séparés par des virgules (ex. hotline@, astreinte@).', true)}
      <div class="field">${chk('starttls', 'STARTTLS', s.starttls)}</div></div>`, 'f-smtp', '<button class="btn" type="button" data-test="email">Envoyer un test</button>') +
      formCard('Webhook (centre de supervision, Teams, Slack, PSA…)', `${chk('enabled', 'Activer le webhook', w.enabled)}<div class="form-grid">
      ${field('URL', inp('url', w.url, 'url', 'placeholder="https://supervision.exemple.fr/hooks/vigiefs"'), 'Reçoit un POST JSON pour chaque nouvelle alerte ou escalade.', true)}
      ${field('Secret de signature', inp('secret', w.secret, 'password', 'autocomplete="new-password"'), 'En-tête X-VigieFS-Signature: sha256=HMAC(corps).')}
      ${field('Gravité minimale', sel('min_severity', sevOpts, w.min_severity))}</div>`, 'f-hook', '<button class="btn" type="button" data-test="webhook">Envoyer un test</button>');
    onSubmit('f-smtp', d => save('smtp', { ...d, recipients: textToList(d.recipients) }));
    onSubmit('f-hook', d => save('webhook', d));
    pane.addEventListener('click', guard(async e => { const b = e.target.closest('[data-test]'); if (!b) return; await api(`/api/settings/test-${b.dataset.test}`, { method: 'POST' }); toast('Test envoyé avec succès'); }));
  }
  if (tab === 'security') {
    const r = st.ransomware; const sc = st.screening;
    pane.innerHTML = formCard('Détection de rançongiciel', `${chk('enabled', 'Activer la détection (temps réel + analyses)', r.enabled)}<div class="form-grid">
      ${field("Seuil d'activité massive", inp('burst_threshold', r.burst_threshold, 'number', 'min="10"'), 'Nombre de modifications / renommages / suppressions…')}
      ${field('…dans une fenêtre de (secondes)', inp('burst_window_sec', r.burst_window_sec, 'number', 'min="5"'))}
      ${field('Extensions de fichiers chiffrés', `<textarea name="extensions">${esc(listToText(r.extensions))}</textarea>`, '', true)}
      ${field('Motifs de notes de rançon', `<textarea name="note_patterns">${esc(listToText(r.note_patterns))}</textarea>`, 'Joker * accepté, insensible à la casse.', true)}</div>`, 'f-rs') +
      formCard('Filtrage de fichiers', `<div class="form-grid">
      ${field('Extensions bloquées par défaut', `<textarea name="default_blocked_extensions">${esc(listToText(sc.default_blocked_extensions))}</textarea>`, 'Appliquées aux dossiers dont la liste personnalisée est vide.', true)}
      ${field('Dossier de quarantaine', inp('quarantine_dir', sc.quarantine_dir, 'text', 'placeholder="E:\\VigieFS\\Quarantaine"'), 'Doit être <b>en dehors</b> des dossiers supervisés. Vide = quarantaine désactivée.', true)}</div>`, 'f-sc');
    onSubmit('f-rs', d => save('ransomware', { ...d, extensions: textToList(d.extensions), note_patterns: textToList(d.note_patterns) }));
    onSubmit('f-sc', d => save('screening', { ...d, default_blocked_extensions: textToList(d.default_blocked_extensions) }));
  }
  if (tab === 'retention') {
    const f = st.forecast; const r = st.retention;
    pane.innerHTML = formCard('Prévision de saturation des quotas', `${chk('enabled', 'Alerter avant saturation (tendance sur 30 jours)', f.enabled)}<div class="form-grid">
      ${field('Avertissement si saturation dans moins de (jours)', inp('warn_days', f.warn_days, 'number', 'min="1"'))}
      ${field('Critique si saturation dans moins de (jours)', inp('crit_days', f.crit_days, 'number', 'min="1"'))}</div>`, 'f-fc') +
      formCard('Rétention des données', `<div class="form-grid">
      ${field("Journal d'activité (jours)", inp('events_days', r.events_days, 'number', 'min="1"'))}
      ${field('Analyses conservées par dossier', inp('scans_per_share', r.scans_per_share, 'number', 'min="5"'))}
      ${field('Alertes résolues (jours)', inp('resolved_alerts_days', r.resolved_alerts_days, 'number', 'min="1"'))}</div>`, 'f-ret');
    onSubmit('f-fc', d => save('forecast', d));
    onSubmit('f-ret', d => save('retention', d));
  }
  if (tab === 'users') {
    const load = async () => {
      const users = await api('/api/users');
      pane.innerHTML = `<div class="card"><div class="card-head"><h2>Utilisateurs</h2><div class="spacer"></div><button class="btn primary" data-act="new">${icon('plus')} Nouvel utilisateur</button></div>
        <p class="small muted"><b>Lecture seule</b> : consultation. <b>Opérateur</b> : clients, dossiers, analyses, acquittement, renommage. <b>Administrateur</b> : tout, dont suppression de fichiers et paramètres.</p>
        <table><thead><tr><th>Identifiant</th><th>Nom</th><th>Rôle</th><th>Dernière connexion</th><th></th></tr></thead><tbody>
        ${users.map(u => `<tr><td><b>${esc(u.username)}</b></td><td>${esc(u.full_name)}</td><td>${ROLE_LABEL[u.role]}</td><td>${fmtAgo(u.last_login)}</td>
          <td class="nowrap"><button class="btn sm" data-act="edit" data-id="${u.id}">Modifier</button>${u.id !== state.user.id ? ` <button class="btn sm danger" data-act="del" data-id="${u.id}">Supprimer</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
      pane._users = users;
    };
    const userForm = (u = {}) => `<div class="form-grid">
      ${field('Identifiant', inp('username', u.username, 'text', u.id ? 'readonly' : 'required'))}
      ${field('Nom complet', inp('full_name', u.full_name))}
      ${field('Rôle', sel('role', Object.entries(ROLE_LABEL), u.role || 'operator'))}
      ${field(u.id ? 'Nouveau mot de passe (optionnel)' : 'Mot de passe', inp('password', '', 'password', 'autocomplete="new-password"'), '10 caractères min., 3 types.')}</div>`;
    bindActions(pane, {
      new: () => modal({ title: 'Nouvel utilisateur', body: userForm(), submit: 'Créer', onSubmit: async f => { await api('/api/users', { method: 'POST', body: formData(f) }); toast('Utilisateur créé'); load(); } }),
      edit: el => { const u = pane._users.find(x => x.id == el.dataset.id); modal({ title: `Modifier ${u.username}`, body: userForm(u), onSubmit: async f => { const d = formData(f); if (!d.password) d.password = null; await api(`/api/users/${u.id}`, { method: 'PUT', body: d }); toast('Utilisateur mis à jour'); load(); } }); },
      del: el => { const u = pane._users.find(x => x.id == el.dataset.id); confirmBox('Supprimer le compte', `Supprimer le compte <b>${esc(u.username)}</b> ?`, async () => { await api(`/api/users/${u.id}`, { method: 'DELETE' }); toast('Compte supprimé'); load(); }, 'Supprimer'); },
    });
    await load();
  }
  if (tab === 'apikeys') {
    const load = async () => {
      const keys = await api('/api/apikeys');
      pane.innerHTML = `<div class="card"><div class="card-head"><h2>Clés d'API</h2><div class="spacer"></div><button class="btn primary" data-act="new">${icon('plus')} Nouvelle clé</button></div>
        <p class="small muted">Pour le centre de supervision, Datto RMM, Prometheus ou vos scripts. Transmettez la clé dans l'en-tête <code>X-API-Key</code> ou <code>Authorization: Bearer</code>. Privilégiez le rôle « Lecture seule ».</p>
        <table><thead><tr><th>Nom</th><th>Préfixe</th><th>Rôle</th><th>Créée</th><th>Dernière utilisation</th><th></th></tr></thead><tbody>
        ${keys.length ? keys.map(k => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.prefix)}…</td><td>${ROLE_LABEL[k.role]}</td><td>${fmtDate(k.created_at)}</td><td>${fmtAgo(k.last_used)}</td><td><button class="btn sm danger" data-act="del" data-id="${k.id}">Révoquer</button></td></tr>`).join('') : '<tr><td colspan="6" class="empty">Aucune clé</td></tr>'}</tbody></table></div>`;
    };
    bindActions(pane, {
      new: () => modal({ title: "Nouvelle clé d'API", body: `<div class="form-grid">${field('Nom', inp('name', '', 'text', 'required placeholder="Datto RMM - monitor"'))}${field('Rôle', sel('role', Object.entries(ROLE_LABEL), 'viewer'))}</div>`, submit: 'Générer',
        onSubmit: async f => { const r = await api('/api/apikeys', { method: 'POST', body: formData(f) }); load(); modal({ title: 'Clé générée', noFooter: true, body: `<p>Copiez cette clé maintenant, elle ne sera <b>plus jamais affichée</b> :</p><div class="secret-box">${esc(r.key)}</div>` }); return false; } }),
      del: el => confirmBox('Révoquer la clé', 'Les intégrations utilisant cette clé cesseront de fonctionner immédiatement.', async () => { await api(`/api/apikeys/${el.dataset.id}`, { method: 'DELETE' }); toast('Clé révoquée'); load(); }, 'Révoquer'),
    });
    await load();
  }
  if (tab === 'audit') {
    pane.innerHTML = `<div class="card"><div class="filters"><input type="search" id="aq" placeholder="Filtrer (utilisateur, action, cible)…"></div><div id="al" class="table-wrap tall"></div></div>`;
    const load = async () => {
      const q = $('#aq', pane).value.trim();
      const rows = await api('/api/audit?limit=1000' + (q ? '&q=' + encodeURIComponent(q) : ''));
      $('#al', pane).innerHTML = `<table><thead><tr><th>Date</th><th>Acteur</th><th>Action</th><th>Cible</th><th>Détails</th></tr></thead><tbody>${rows.map(r => `<tr><td class="nowrap">${fmtDate(r.ts, true)}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td class="path">${esc(r.target)}</td><td class="small">${esc(r.details)}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Vide</td></tr>'}</tbody></table>`;
    };
    $('#aq', pane).addEventListener('input', guard(load));
    await load();
  }
  if (tab === 'integration') {
    const sys = await api('/api/system');
    const base = location.origin;
    pane.innerHTML = `<div class="grid cols-2"><div class="card"><h2>Système</h2><dl class="kv">
        <dt>Version</dt><dd>${esc(sys.version)}</dd><dt>Serveur</dt><dd>${esc(sys.hostname)}</dd><dt>Plateforme</dt><dd>${esc(sys.platform)} · Python ${esc(sys.python)}</dd>
        ${sys.data_dir ? `<dt>Données</dt><dd class="mono small">${esc(sys.data_dir)}</dd>` : ''}
        <dt>Démarré depuis</dt><dd>${fmtDuration(sys.uptime_sec)}</dd><dt>Événements enregistrés</dt><dd>${fmtNum(sys.events_recorded)} (file d'attente : ${sys.queue})</dd>
        <dt>Analyses en cours</dt><dd>${sys.scans_running}</dd></dl></div>
      <div class="card"><h2>Points d'intégration</h2><dl class="kv">
        <dt>Statut global</dt><dd class="mono small">GET ${esc(base)}/api/v1/status</dd>
        <dt>Prometheus</dt><dd class="mono small">GET ${esc(base)}/metrics</dd>
        <dt>Documentation API</dt><dd><a href="/api/docs" target="_blank" rel="noopener">${esc(base)}/api/docs</a></dd>
        <dt>Webhook sortant</dt><dd>Paramètres › Notifications</dd></dl>
        <h3>Datto RMM</h3><p class="small">Le script <code>integrations/datto-rmm/VigieFS-Monitor.ps1</code> s'importe comme composant « Monitor » : il interroge <code>/api/v1/status</code> et lève une alerte Datto si VigieFS signale un problème.</p>
        <h3>Exemple</h3><div class="secret-box small">curl -H "X-API-Key: vfs_…" ${esc(base)}/api/v1/status</div></div></div>`;
  }
}

// ------------------------------------------------------------------ démarrage
try { const t = localStorage.getItem('vigiefs-theme'); if (t) document.documentElement.dataset.theme = t; } catch { /* stockage indisponible */ }
boot();
