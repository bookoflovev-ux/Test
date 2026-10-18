/* VigieFS — authentification, squelette, navigation, recherche globale, routeur */
'use strict';

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
    $('#app').innerHTML = `<div class="auth-main" style="min-height:100vh"><div class="card auth-card">${emptyState('alert', 'Serveur injoignable', esc(e.message), '<button class="btn primary" id="retry">Réessayer</button>')}</div></div>`;
    $('#retry').onclick = boot;
  }
}

function authLayout(title, inner) {
  const points = [
    ['shield', 'Supervision temps réel, quotas et détection de rançongiciel'],
    ['broom', 'Nettoyage des fichiers inutiles, doublons et archivage à froid'],
    ['wrench', 'Réparation des documents Word, Excel, PowerPoint et PDF'],
    ['report', 'Rapports par client et intégration au centre de supervision'],
  ];
  return `<div class="auth-screen">
    <aside class="auth-side"><div class="brand" style="padding:0">${LOGO}<div>VigieFS<small>${esc(state.company || '')}</small></div></div>
      <div><h1>La console de pilotage de vos serveurs de fichiers clients.</h1>
      <ul>${points.map(([i, t]) => `<li>${icon(i)}<span>${t}</span></li>`).join('')}</ul></div>
      <div class="small" style="opacity:.6">v${esc(state.version)}</div></aside>
    <main class="auth-main"><form class="auth-card form" novalidate>
      <div class="brand">${LOGO}<div>VigieFS<small>Supervision des serveurs de fichiers</small></div></div>
      <div><h1 style="font-size:22px">${esc(title)}</h1></div>${inner}<div class="error-text" data-err></div></form></main></div>`;
}

function renderLogin() {
  $('#app').innerHTML = authLayout('Connexion', `
    ${field('Identifiant', inp('username', '', 'text', 'autocomplete="username" required'))}
    ${field('Mot de passe', inp('password', '', 'password', 'autocomplete="current-password" required'))}
    <button class="btn primary" type="submit" style="height:40px">Se connecter</button>
    <p class="xs muted">Mot de passe perdu : <code>python -m vigiefs --reset-password &lt;identifiant&gt;</code> sur le serveur.</p>`);
  const form = $('form');
  $('[name=username]', form).focus();
  form.addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/auth/login', { method: 'POST', body: formData(form) }); location.hash = location.hash || '#/'; boot(); } catch (err) { $('[data-err]', form).textContent = err.message; }
  });
}

function renderSetup() {
  $('#app').innerHTML = authLayout('Première installation', `
    <p class="muted small">Créez le compte administrateur principal. Il créera ensuite les comptes des techniciens.</p>
    ${field('Nom complet', inp('full_name', '', 'text', 'autocomplete="name"'))}
    ${field('Identifiant', inp('username', 'admin', 'text', 'autocomplete="username" required'))}
    ${field('Mot de passe', inp('password', '', 'password', 'autocomplete="new-password" required'), '10 caractères minimum, 3 types parmi : minuscules, majuscules, chiffres, symboles.')}
    ${field('Confirmation', inp('confirm', '', 'password', 'autocomplete="new-password" required'))}
    <button class="btn primary" type="submit" style="height:40px">Créer le compte et continuer</button>`);
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
  ['Supervision', [['#/', 'dashboard', 'Tableau de bord'], ['#/alerts', 'bell', 'Alertes', 'alerts'], ['#/events', 'activity', "Journal d'activité"]]],
  ['Gestion', [['#/clients', 'clients', 'Clients'], ['#/shares', 'folder', 'Dossiers supervisés']]],
  ['Optimisation', [['#/optimization', 'sparkle', "Centre d'optimisation"], ['#/duplicates', 'copy', 'Doublons'], ['#/archive', 'archive', 'Archivage à froid'], ['#/recycle', 'trash', 'Corbeille']]],
  ['Outils', [['#/repair', 'wrench', 'Réparation de fichiers'], ['#/jobs', 'tasks', 'Tâches', 'jobs'], ['#/reports', 'report', 'Rapports']]],
  ['Administration', [['#/settings', 'settings', 'Paramètres']]],
];

function renderShell() {
  const u = state.user;
  const initials = (u.full_name || u.username).split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  $('#app').innerHTML = `<div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="brand">${LOGO}<div>VigieFS<small>${esc(state.company || '')}</small></div></div>
      <nav class="nav">${NAV.map(([section, items]) => `<div class="nav-section">${section}</div>${items.map(([href, ic, label, counter]) =>
        `<a href="${href}" data-nav="${href}">${icon(ic)}<span>${label}</span>${counter ? `<span class="count" id="nav-${counter}" hidden></span>` : ''}</a>`).join('')}`).join('')}</nav>
      <div class="user-card"><div class="avatar">${esc(initials)}</div><div class="who"><div class="name ellipsis">${esc(u.full_name || u.username)}</div><div class="role">${esc(ROLE_LABEL[u.role])}</div></div>
        <button class="icon-btn" id="logout-btn" title="Déconnexion" aria-label="Déconnexion">${icon('logout')}</button></div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="icon-btn menu-toggle" id="menu-btn" aria-label="Menu">${icon('menu')}</button>
        <div class="search">${icon('search')}<input type="search" id="gsearch" placeholder="Rechercher un client, un dossier…" autocomplete="off" aria-label="Recherche globale"><kbd>Ctrl K</kbd><div class="search-results" id="gresults" hidden></div></div>
        <div class="spacer"></div>
        <a class="status-pill" id="status-pill" href="#/alerts"><span class="label">Système nominal</span></a>
        <button class="icon-btn" id="theme-btn" title="Basculer clair / sombre" aria-label="Thème">${icon('moon')}</button>
      </header>
      <div class="page"><div class="page-head"><div class="titles"><div class="crumbs" id="crumbs"></div><h1 id="page-title"></h1><div class="subtitle" id="page-sub"></div></div><div class="btn-row" id="page-actions"></div></div>
      <main id="view"></main></div>
    </div></div>`;
  $('#logout-btn').onclick = async () => { await api('/api/auth/logout', { method: 'POST' }).catch(() => {}); state.user = null; boot(); };
  $('#theme-btn').onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('vigiefs-theme', next); } catch { /* stockage indisponible */ }
    state.redraw.forEach(f => f());
  };
  $('#menu-btn').onclick = () => $('#sidebar').classList.toggle('open');
  initSearch();
  refreshIndicators();
  setInterval(refreshIndicators, 20000);
}

async function refreshIndicators() {
  if (!state.user) return;
  try {
    const [alerts, jobs] = await Promise.all([api('/api/alerts?status=open&limit=2000'), api('/api/jobs?status=active&limit=50')]);
    const crit = alerts.filter(a => a.severity === 'critical').length;
    const warn = alerts.filter(a => a.severity === 'warning').length;
    const b = $('#nav-alerts');
    if (b) { b.hidden = !alerts.length; b.textContent = alerts.length; b.classList.toggle('info', !crit); }
    const j = $('#nav-jobs');
    if (j) { j.hidden = !jobs.length; j.textContent = jobs.length; j.classList.add('info'); }
    const pill = $('#status-pill');
    if (pill) {
      pill.className = 'status-pill' + (crit ? ' critical' : warn ? ' warning' : '');
      pill.innerHTML = `<span class="label">${crit ? `${crit} alerte${crit > 1 ? 's' : ''} critique${crit > 1 ? 's' : ''}` : warn ? `${warn} avertissement${warn > 1 ? 's' : ''}` : 'Système nominal'}</span>`;
    }
  } catch { /* silencieux */ }
}

function initSearch() {
  const input = $('#gsearch'); const box = $('#gresults');
  let items = []; let focus = -1;
  const load = async () => {
    const [clients, shares] = await Promise.all([api('/api/clients'), api('/api/shares')]);
    items = [
      ...clients.map(c => ({ label: c.name, sub: `${c.share_count} dossier(s)`, href: `#/shares?client=${c.id}`, kind: 'Client', ic: 'clients' })),
      ...shares.map(s => ({ label: s.name, sub: `${s.client_name} · ${s.path}`, href: `#/share/${s.id}`, kind: 'Dossier', ic: 'folder' })),
      ...NAV.flatMap(([, list]) => list.map(([href, ic, label]) => ({ label, sub: 'Page', href, kind: 'Navigation', ic }))),
    ];
  };
  const render = () => {
    const q = input.value.trim().toLowerCase();
    if (!q) { box.hidden = true; return; }
    const res = items.filter(i => (i.label + ' ' + i.sub).toLowerCase().includes(q)).slice(0, 12);
    focus = res.length ? 0 : -1;
    box.innerHTML = res.length ? res.map((r, i) => `<a href="${esc(r.href)}" class="${i === focus ? 'focus' : ''}">${icon(r.ic, 'file-icon')}<div style="min-width:0"><div class="ellipsis strong">${esc(r.label)}</div><div class="xs muted ellipsis">${esc(r.sub)}</div></div><span class="kind">${r.kind}</span></a>`).join('') : '<div class="empty small">Aucun résultat</div>';
    box.hidden = false;
  };
  input.addEventListener('focus', guard(async () => { await load(); render(); }));
  input.addEventListener('input', render);
  input.addEventListener('keydown', e => {
    const links = $$('a', box);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      focus = (focus + (e.key === 'ArrowDown' ? 1 : -1) + links.length) % Math.max(1, links.length);
      links.forEach((l, i) => l.classList.toggle('focus', i === focus));
    } else if (e.key === 'Enter' && links[focus]) { location.hash = links[focus].getAttribute('href'); input.value = ''; box.hidden = true; input.blur(); }
    else if (e.key === 'Escape') { input.value = ''; box.hidden = true; input.blur(); }
  });
  box.addEventListener('click', () => { input.value = ''; box.hidden = true; });
  document.addEventListener('click', e => { if (!e.target.closest('.search')) box.hidden = true; });
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); input.focus(); } });
}

function setPage(title, crumbs = '', actions = '', subtitle = '') {
  $('#page-title').textContent = title;
  $('#crumbs').innerHTML = crumbs;
  $('#page-actions').innerHTML = actions;
  $('#page-sub').innerHTML = subtitle;
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
  [/^\/optimization$/, viewOptimization],
  [/^\/duplicates(?:\/(\d+))?$/, viewDuplicates],
  [/^\/archive$/, viewArchive],
  [/^\/recycle$/, viewRecycle],
  [/^\/repair$/, viewRepair],
  [/^\/jobs$/, viewJobs],
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
  const fresh = view.cloneNode(false); view.replaceWith(fresh);
  const pa = $('#page-actions'); pa.replaceWith(pa.cloneNode(false));
  $$('[data-nav]').forEach(a => {
    const href = a.dataset.nav.slice(1);
    a.classList.toggle('active', href === '/' ? path === '/' : path.startsWith(href) || (href === '/shares' && path.startsWith('/share/')));
  });
  scrollTo(0, 0);
  for (const [re, fn] of ROUTES) {
    const m = path.match(re);
    if (m) { guard(fn)(fresh, query, ...m.slice(1)); return; }
  }
  setPage('Page introuvable'); fresh.innerHTML = `<div class="card">${emptyState('alert', "Cette page n'existe pas")}</div>`;
}
addEventListener('hashchange', route);

try { const t = localStorage.getItem('vigiefs-theme'); if (t) document.documentElement.dataset.theme = t; } catch { /* stockage indisponible */ }
boot();
