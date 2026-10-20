/* VigieFS — sécurité & utilisateurs : vue d'ensemble, utilisateurs, fiche, analyse des journaux, préparation de l'audit */
'use strict';

const RISK_LABEL = { low: 'Faible', medium: 'Modéré', high: 'Élevé', critical: 'Critique' };
const RISK_TONE = { low: 'neutral', medium: 'warning', high: 'warning', critical: 'critical' };
const ACT_SERIES = [{ key: 'reads', label: 'Lectures' }, { key: 'writes', label: 'Modifications' }, { key: 'deletes', label: 'Suppressions' }];
const ACTION_FILTERS = [['', 'Toutes les actions'], ['read,open', 'Lectures / ouvertures'], ['write,create', 'Modifications / créations'],
  ['delete', 'Suppressions'], ['rename', 'Renommages / déplacements'], ['perm,owner', 'Droits / propriétaire'], ['denied', 'Accès refusés'],
  ['share,logon', 'Connexions'], ['logon_fail,lockout', 'Échecs de connexion / verrouillages'], ['audit_cleared,policy,account,group_add', 'Audit, comptes et groupes']];
const SOURCE_LABEL = { windows: 'Journal Sécurité Windows', samba: 'Audit Samba', smb: 'Sessions et fichiers ouverts SMB', import: 'Journal importé', demo: 'Démonstration', analysis: 'Analyse comportementale (constats)' };
let secLabels = null;
async function secLabelsGet() { if (!secLabels) secLabels = await api('/api/security/labels'); return secLabels; }
const userHref = u => `#/user?u=${encodeURIComponent(u)}`;
const userLink = (u, cls = '') => (String(u).startsWith('poste:') ? `<span class="${cls}">poste ${esc(u.slice(6))}</span>` : `<a href="${userHref(u)}" class="${cls}">${esc(u)}</a>`);
const riskBar = (score, level) => `<span class="risk ${esc(level || 'low')}" data-tip="Score de risque : <b>${fmtNum(Math.round(score || 0))}</b> / 100 (${esc(RISK_LABEL[level] || '')})"><span class="track"><span class="fill" style="width:${Math.min(100, score || 0)}%"></span></span><span class="num">${fmtNum(Math.round(score || 0))}</span></span>`;
const riskBadge = level => `<span class="sev ${RISK_TONE[level] || 'neutral'}">${esc(RISK_LABEL[level] || level)}</span>`;
const actSum = a => (a ? (a.reads || 0) + (a.writes || 0) + (a.creates || 0) + (a.deletes || 0) + (a.renames || 0) : 0);
function actCols(rows, every = 3, hourFmt = true) {
  return rows.map(b => {
    const d = new Date(b.ts * 1000);
    const h = String(d.getHours()).padStart(2, '0');
    return { label: hourFmt ? `${h}h` : d.toLocaleDateString('fr-FR', { weekday: 'short', day: '2-digit' }), tip: `${d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })} ${h}h–${String((d.getHours() + 1) % 24).padStart(2, '0')}h`,
      values: [b.reads || 0, (b.writes || 0) + (b.creates || 0) + (b.renames || 0), b.deletes || 0] };
  });
}
function actTable(rows) {
  return `<div class="table-wrap tall"><table><thead><tr><th>Heure</th>${ACT_SERIES.map(s => `<th class="num">${s.label}</th>`).join('')}<th class="num">Refus</th></tr></thead><tbody>
    ${rows.filter(r => actSum(r) || r.denied).map(r => `<tr><td>${fmtDate(r.ts)}</td><td class="num">${fmtNum(r.reads || 0)}</td><td class="num">${fmtNum((r.writes || 0) + (r.creates || 0) + (r.renames || 0))}</td><td class="num">${fmtNum(r.deletes || 0)}</td><td class="num">${fmtNum(r.denied || 0)}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Aucune activité</td></tr>'}</tbody></table></div>`;
}
function findingRow(f, showUser = true) {
  const d = f.details || {};
  const extra = [d.client && `poste ${esc(d.client)}`, (f.detector === 'new_folder' ? [] : d.folders || []).slice(0, 3).map(x => esc(x.folder) + ' (' + fmtNum(x.count) + ')').join(', '), d.response && `réponse : ${esc(d.response.join(' ; '))}`].filter(Boolean).join(' · ');
  const label = f.label && f.label !== f.title ? ` <span class="pill">${esc(f.label)}</span>` : '';
  return `<div class="finding"><div>${sev(f.severity)}</div><div class="body"><div><span class="strong">${esc(f.title)}</span>${label}
    ${showUser ? ` ${userLink(f.user, 'small')}` : ''}</div>
    <div class="alert-msg">${esc(f.message)}</div>${extra ? `<div class="cell-sub">${extra}</div>` : ''}
    ${(d.samples || []).length ? `<details class="small"><summary>Exemples (${fmtNum(d.samples.length)})</summary><div class="details-box">${d.samples.map(esc).join('<br>')}</div></details>` : ''}</div>
    <div class="nowrap small muted" data-tip="${esc(fmtDate(f.ts))}">${fmtAgo(f.ts)}</div></div>`;
}
async function securityImport(after) {
  modal({ title: 'Importer un journal à analyser', submit: 'Analyser',
    body: `<p>VigieFS rattache chaque ligne à un utilisateur et à un dossier, puis applique ses détecteurs sur toute la période du journal.</p>
      <ul class="small"><li><b>Journal Windows</b> : fichier <code>.evtx</code> (sur un serveur Windows), ou export <b>XML</b> de l'Observateur d'événements (Enregistrer les événements sous… › XML) ou de <code>wevtutil qe Security /f:xml</code>.</li>
      <li><b>Journal d'audit Samba</b> (module full_audit, ou extrait de syslog).</li></ul>
      ${field('Fichier', '<input type="file" name="file" accept=".evtx,.xml,.log,.txt">', '300 Mo au plus.', true)}`,
    onSubmit: async f => {
      const file = $('[name=file]', f).files[0];
      if (!file) throw new Error('Choisissez un fichier.');
      const r = await api(`/api/security/import?filename=${encodeURIComponent(file.name)}`, { method: 'POST', raw: file });
      toast('Analyse du journal lancée');
      const job = await watchJob(r.job_id);
      if (job.status !== 'done') return toast(`Échec de l'analyse : ${job.error || job.status}`, 'error');
      const res = job.result;
      toast(`${res.format} : ${fmtNum(res.kept)} événement(s) retenu(s) sur ${fmtNum(res.read)}, ${fmtNum(res.findings)} anomalie(s)`);
      after && after(res);
    } });
}

// ------------------------------------------------------------------ vue d'ensemble
async function viewSecurity(view) {
  setPage('Sécurité & comportements', 'Sécurité',
    `${can('operator') ? `<button class="btn" data-sact="collect">${icon('refresh')} Collecter maintenant</button><button class="btn" data-sact="analyze">${icon('sparkle')} Analyser 7 jours</button>` : ''}
     ${can('admin') ? `<button class="btn" data-sact="import">${icon('upload')} Importer un journal</button>` : ''}`,
    'Qui fait quoi sur les serveurs de fichiers : activité par utilisateur, comportements anormaux, accès refusés, connexions.');
  rowLinks(view);
  const render = async () => {
    const d = await api('/api/security/overview');
    const day = d.day || {};
    const f7 = d.findings_7d || {};
    const empty = !d.events_total;
    view.innerHTML = `
      ${empty ? `<div class="banner info">${icon('info')}<div><b>Aucune activité utilisateur collectée pour l'instant.</b> ${d.platform === 'windows'
        ? 'Activez l\'audit des accès aux fichiers (stratégie d\'audit, taille du journal, audit des dossiers) : VigieFS lit ensuite le journal Sécurité en continu.'
        : 'Activez le module d\'audit Samba full_audit sur les partages : VigieFS lit ensuite son journal en continu.'} En attendant, les sessions et fichiers ouverts sont échantillonnés, et vous pouvez importer un journal existant.</div><a class="btn sm" href="#/audit">Préparer l'audit →</a></div>`
        : !d.collector.alive ? `<div class="banner warning">${icon('alert')}<span>La collecte n'est pas active (désactivée dans Paramètres › Comportements & journaux, ou VigieFS lancé sans le service).</span></div>` : ''}
      <div class="tiles" style="margin-top:16px">
        ${tile({ label: 'Utilisateurs actifs (24 h)', value: fmtNum(day.users || 0), ic: 'clients', go: '#/users', sub: `${compact(day.total || 0)} opérations` })}
        ${tile({ label: 'Utilisateurs à risque', value: fmtNum(d.at_risk), ic: 'shield', tone: d.at_risk ? 'critical' : 'good', go: '#/users', sub: 'score élevé ou critique' })}
        ${tile({ label: 'Anomalies (7 jours)', value: fmtNum((f7.critical || 0) + (f7.warning || 0) + (f7.info || 0)), ic: 'alert', tone: f7.critical ? 'critical' : f7.warning ? 'warning' : 'good', sub: `${fmtNum(f7.critical || 0)} critique(s) · ${fmtNum(f7.warning || 0)} avertissement(s)`, go: '#/alerts?category=behavior' })}
        ${tile({ label: 'Suppressions (24 h)', value: compact(day.deletes || 0), ic: 'trash', tone: 'aqua', go: '#/logs?action=delete&period=86400' })}
        ${tile({ label: 'Accès refusés (24 h)', value: compact(day.denied || 0), ic: 'lock', tone: day.denied ? 'warning' : 'good', go: '#/logs?action=denied&period=86400' })}
        ${tile({ label: 'Échecs de connexion (24 h)', value: compact(day.logon_fails || 0), ic: 'bell', tone: day.logon_fails ? 'warning' : 'good', go: '#/logs?action=logon_fail,lockout&period=86400' })}
      </div>
      <div class="card">${cardHead('Activité des utilisateurs — 48 heures', 'Opérations sur les fichiers rattachées à un utilisateur', '<button class="btn sm ghost" data-sact="toggle">Voir le tableau</button>')}
        ${chartLegend(ACT_SERIES)}<div id="sec-chart"></div><div id="sec-table" hidden>${actTable(d.timeline)}</div></div>
      <div class="grid cols-2">
        <div class="card">${cardHead('Utilisateurs à risque', 'Score : anomalies récentes pondérées (demi-vie 7 jours)', '<a href="#/users" class="small">Tous les utilisateurs →</a>')}
          ${d.risky.length ? `<table><tbody>${d.risky.map(r => `<tr class="clickable" data-go="${userHref(r.user)}"><td><div class="strong">${esc(r.display || r.user)}${r.watch ? ' <span class="pill blue">surveillé</span>' : ''}</div><div class="cell-sub">${r.last_finding ? esc(r.last_finding.title) + ' · ' + fmtAgo(r.last_finding.ts) : ''}</div></td><td style="width:1%">${riskBadge(r.level)}</td><td style="width:34%">${riskBar(r.risk, r.level)}</td></tr>`).join('')}</tbody></table>`
            : emptyState('shield', 'Aucun utilisateur à risque', 'Aucune anomalie récente.', '', 'good')}</div>
        <div class="card">${cardHead('Dernières anomalies', '', '<a href="#/alerts?category=behavior" class="small">Alertes →</a>')}
          ${d.findings.length ? `<div style="max-height:420px;overflow:auto">${d.findings.slice(0, 12).map(f => findingRow(f)).join('')}</div>` : emptyState('check', 'Rien à signaler', 'Les détecteurs n\'ont relevé aucun comportement anormal.', '', 'good')}</div>
      </div>
      <div class="grid cols-2">
        <div class="card">${cardHead('Les plus actifs (24 h)', 'Opérations sur les fichiers par utilisateur')}${hbars(d.top_active.map(r => ({ name: r.display || r.user, value: r.total || 0, r })), { fmt: v => fmtNum(v), sub: i => `${fmtNum(i.r.reads || 0)} lectures · ${fmtNum((i.r.writes || 0) + (i.r.creates || 0))} modifications · ${fmtNum(i.r.deletes || 0)} suppressions` })}</div>
        <div class="card">${cardHead('Espace occupé par utilisateur', 'Propriétaire des fichiers ou dossier personnel, à la dernière analyse')}${hbars(d.storage_top.map(r => ({ name: r.display || r.user, value: r.size || 0, r })), { sub: i => `${fmtNum(i.r.files)} fichiers · ${i.r.shares} dossier(s)` })}</div>
      </div>
      <div class="card flush">${cardHead('Sources de collecte', d.platform === 'windows' ? 'Journal Sécurité de ce serveur, sessions SMB, journaux importés' : 'Journal d\'audit Samba, sessions SMB, journaux importés', '<a href="#/audit" class="small">Préparer l\'audit →</a>')}
        <div class="table-wrap"><table><thead><tr><th>Source</th><th>Dernière lecture</th><th class="num">Dernier lot</th><th class="num">Total</th><th>État</th></tr></thead><tbody>
        ${d.sources.map(s => `<tr><td class="strong">${esc(SOURCE_LABEL[s.name] || s.name)}</td><td>${s.last_run ? fmtAgo(s.last_run) : '—'}</td><td class="num">${fmtNum(s.last_count || 0)}</td><td class="num">${fmtNum(s.events_total || 0)}</td>
          <td>${s.last_error ? `<span class="sev critical">Erreur</span><div class="cell-sub">${esc(s.last_error)}</div>` : '<span class="sev ok">OK</span>'}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Aucune source lue pour l\'instant.</td></tr>'}</tbody></table></div></div>`;
    state.redraw = [];
    stackedChart($('#sec-chart', view), actCols(d.timeline), ACT_SERIES, { every: 6 });
  };
  const actions = {
    toggle: el => { const t = $('#sec-table', view); const c = $('#sec-chart', view); t.hidden = !t.hidden; c.hidden = !t.hidden; el.textContent = t.hidden ? 'Voir le tableau' : 'Voir le graphique'; },
    collect: async () => { const r = await api('/api/security/collect', { method: 'POST' }); toast(`${fmtNum(r.events)} nouvel(s) événement(s)`); render(); },
    analyze: async () => { const r = await api('/api/security/analyze?days=7', { method: 'POST' }); toast(`${fmtNum(r.findings)} nouvelle(s) anomalie(s)`); render(); },
    import: () => securityImport(() => render()),
  };
  view.addEventListener('click', e => { const el = e.target.closest('[data-sact]'); if (el) guard(actions[el.dataset.sact])(el); });
  $('#page-actions').addEventListener('click', e => { const el = e.target.closest('[data-sact]'); if (el) guard(actions[el.dataset.sact])(el); });
  await render();
  every(30000, guard(render));
}

// ------------------------------------------------------------------ utilisateurs
async function viewUsers(view, query) {
  setPage('Utilisateurs', 'Sécurité', '', 'Activité, espace occupé, capacité et niveau de risque de chaque utilisateur.');
  rowLinks(view);
  view.innerHTML = `<div class="card flush"><div class="filters" style="padding:16px 20px 0">
      <input type="search" name="q" placeholder="Rechercher un utilisateur…" value="${esc(query.get('q') || '')}" style="width:260px">
      ${sel('filter', [['', 'Tous'], ['risk', 'À risque (score ≥ 15)'], ['watch', 'Surveillés'], ['capacity', 'Capacité ≥ 90 %'], ['service', 'Comptes de service (exclus)']], '')}
      <span class="spacer"></span><span class="small muted" id="u-count"></span></div><div id="u-list" class="table-wrap" style="margin-top:14px"></div></div>`;
  let rows = [];
  const draw = () => {
    const q = $('[name=q]', view).value.trim().toLowerCase(); const f = $('[name=filter]', view).value;
    const list = rows.filter(r => (!q || (r.user + ' ' + r.display).toLowerCase().includes(q))
      && (!f || (f === 'risk' && r.risk >= 15) || (f === 'watch' && r.watch) || (f === 'capacity' && r.capacity_pct >= 90) || (f === 'service' && r.service)));
    $('#u-count', view).textContent = `${fmtNum(list.length)} utilisateur(s)`;
    $('#u-list', view).innerHTML = list.length ? `<table><thead><tr><th>Utilisateur</th><th>Risque</th><th class="num">Activité 24 h</th><th class="num">Activité 7 j</th><th>Espace occupé</th><th class="num">Postes</th><th>Dernière activité</th></tr></thead><tbody>
      ${list.map(r => `<tr class="clickable" data-go="${userHref(r.user)}"><td><div class="strong">${esc(r.display)}</div>${String(r.display).toLowerCase() !== r.user ? `<div class="cell-sub mono">${esc(r.user)}</div>` : ''}
          ${r.watch ? '<span class="pill blue">surveillé</span> ' : ''}${r.service ? '<span class="pill">compte de service</span> ' : ''}${r.findings_30d ? `<span class="pill warn">${r.findings_30d} anomalie(s) / 30 j</span>` : ''}</td>
        <td>${riskBar(r.risk, r.level)}</td>
        <td class="num" data-tip="${esc(r.day ? `${fmtNum(r.day.reads)} lectures<br>${fmtNum((r.day.writes || 0) + (r.day.creates || 0))} modifications<br>${fmtNum(r.day.deletes)} suppressions<br>${fmtNum(r.day.denied)} refus` : 'Aucune activité')}">${fmtNum((r.day || {}).total || 0)}</td>
        <td class="num">${fmtNum((r.week || {}).total || 0)}</td>
        <td style="min-width:180px">${r.size ? `<div>${fmtBytes(r.size)} <span class="cell-sub">· ${fmtNum(r.files)} fichiers</span></div>${r.capacity ? meter(r.capacity_pct, r.capacity_pct >= 100 ? 'critical' : r.capacity_pct >= 90 ? 'warning' : '') : ''}` : '<span class="muted">—</span>'}</td>
        <td class="num">${fmtNum(r.clients)}</td><td class="nowrap">${r.last_seen ? fmtAgo(r.last_seen) : '—'}</td></tr>`).join('')}</tbody></table>`
      : emptyState('clients', 'Aucun utilisateur', rows.length ? 'Aucun utilisateur ne correspond à ces critères.' : 'Les utilisateurs apparaissent dès que leur activité est collectée ou après l\'analyse d\'un dossier (propriétaires des fichiers).');
  };
  $('[name=q]', view).addEventListener('input', draw);
  $('[name=filter]', view).addEventListener('change', draw);
  rows = await api('/api/security/users');
  draw();
}

// ------------------------------------------------------------------ fiche utilisateur
async function viewUser(view, query) {
  await secLabelsGet();
  const u = query.get('u') || '';
  const d = await api(`/api/security/user?u=${encodeURIComponent(u)}`);
  const p = d.profile;
  const shares = await api('/api/shares');
  const name = p.display || p.user;
  setPage(name, `<a href="#/users">Utilisateurs</a>`,
    `<a class="btn" href="#/logs?u=${encodeURIComponent(p.user)}">${icon('list')} Son journal</a>
     ${can('operator') ? `<button class="btn" data-uact="watch">${icon('eye')} ${p.watch ? 'Ne plus surveiller' : 'Surveiller'}</button><button class="btn" data-uact="settings">${icon('settings')} Capacité & notes</button>` : ''}`,
    `<span class="mono">${esc(p.user)}</span> · ${riskBadge(p.level)}${p.watch ? ' <span class="pill blue">surveillé : seuils divisés par deux</span>' : ''}${p.service ? ' <span class="pill">compte de service : exclu de l\'analyse</span>' : ''}${p.first_seen ? ` · vu depuis le ${fmtDay(p.first_seen)}` : ''}`);
  const week = d.week.reduce((a, r) => a + (r.total || 0), 0);
  const capTone = d.capacity_pct >= 100 ? 'critical' : d.capacity_pct >= 90 ? 'warning' : 'violet';
  view.innerHTML = `
    ${p.notes ? `<div class="banner info">${icon('info')}<span>${esc(p.notes)}</span></div>` : ''}
    <div class="tiles" style="margin-top:${p.notes ? 16 : 0}px">
      ${tile({ label: 'Score de risque', value: `${fmtNum(Math.round(p.risk || 0))} / 100`, ic: 'shield', tone: p.level === 'critical' ? 'critical' : p.level === 'low' ? 'good' : 'warning', sub: RISK_LABEL[p.level] })}
      ${tile({ label: 'Espace occupé', value: fmtBytes(d.storage_total), ic: 'disk', tone: capTone, sub: d.capacity ? `${fmtPct(d.capacity_pct, 0)} des ${fmtBytes(d.capacity)} alloués` : `${fmtNum(d.storage_files)} fichiers · capacité non définie` })}
      ${tile({ label: 'Activité (7 jours)', value: compact(week), ic: 'activity', tone: 'aqua', sub: 'opérations rattachées' })}
      ${tile({ label: 'Anomalies', value: fmtNum(d.findings.length), ic: 'alert', tone: d.findings.some(f => f.severity === 'critical') ? 'critical' : d.findings.length ? 'warning' : 'good', sub: d.findings[0] ? `dernière ${fmtAgo(d.findings[0].ts)}` : 'aucune' })}
      ${tile({ label: 'Postes utilisés', value: fmtNum(d.clients.length), ic: 'clients', sub: d.clients[0] ? `dernier : ${esc(d.clients[0].client)}` : '' })}
      ${tile({ label: 'Dernière activité', value: p.last_seen ? fmtAgo(p.last_seen) : '—', ic: 'bell', sub: p.last_seen ? fmtDate(p.last_seen) : '' })}
    </div>
    <div id="u-live"></div>
    ${can('operator') && (p.risk >= 15 || d.findings.some(f => f.severity !== 'info')) ? `<div class="card" style="margin-top:16px"><div class="btn-row" style="justify-content:space-between;align-items:center">
      <div><div class="strong">Réagir</div><div class="small muted">Fermer ses sessions coupe l'accès en cours ; bloquer l'accès au partage l'empêche de revenir ; désactiver le compte le coupe de tout le domaine. Tout est réversible et journalisé.</div></div>
      <div class="btn-row"><button class="btn" data-uact="close">${icon('close')} Fermer ses sessions</button>${can('admin') ? `<button class="btn" data-uact="block">${icon('lock')} Bloquer l'accès…</button><button class="btn danger" data-uact="disable">${icon('stop')} Désactiver le compte…</button>` : ''}</div></div></div>`
    : can('operator') ? `<div class="btn-row" style="margin:12px 0 0"><button class="btn sm ghost" data-uact="close">${icon('close')} Fermer ses sessions</button>${can('admin') ? `<button class="btn sm ghost" data-uact="block">${icon('lock')} Bloquer l'accès…</button><button class="btn sm ghost" data-uact="disable">${icon('stop')} Désactiver le compte…</button>` : ''}</div>` : ''}
    <div class="card">${cardHead('Anomalies détectées', 'Constats des détecteurs, du plus récent au plus ancien')}
      ${d.findings.length ? `<div style="max-height:460px;overflow:auto">${d.findings.map(f => findingRow(f, false)).join('')}</div>` : emptyState('check', 'Aucune anomalie', 'Le comportement de cet utilisateur reste dans ses habitudes.', '', 'good')}</div>
    <div class="card">${cardHead('Activité — 7 derniers jours', 'Par heure', '<button class="btn sm ghost" data-uact="toggle">Voir le tableau</button>')}
      ${chartLegend(ACT_SERIES)}<div id="u-chart"></div><div id="u-table" hidden>${actTable(d.week)}</div></div>
    <div class="grid span-2-1">
      <div class="card">${cardHead('Habitudes — 28 derniers jours', 'Volume d\'activité par jour de la semaine et par heure')}${heatmap(d.heatmap, { bh: d.business_hours })}</div>
      <div class="card">${cardHead('Postes et adresses', 'Premier et dernier usage')}
        ${d.clients.length ? `<table><tbody>${d.clients.slice(0, 12).map(c => `<tr><td class="mono">${esc(c.client)}</td><td class="small muted nowrap">depuis ${fmtDay(c.first)}</td><td class="small nowrap">${fmtAgo(c.last)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Aucun poste connu</div>'}</div>
    </div>
    <div class="grid cols-2">
      <div class="card flush">${cardHead('Dossiers utilisés (30 jours)', 'Premier niveau de chaque dossier supervisé')}
        <div class="table-wrap"><table><thead><tr><th>Dossier</th><th class="num">Lect.</th><th class="num">Modif.</th><th class="num">Suppr.</th><th class="num">Refus</th><th>Dernier</th></tr></thead><tbody>
        ${d.folders.map(f => `<tr><td><div class="strong">${esc(f.folder || '(racine)')}</div><div class="cell-sub">${esc(f.share_name || '')}</div></td><td class="num">${fmtNum(f.reads)}</td><td class="num">${fmtNum(f.writes)}</td><td class="num">${fmtNum(f.deletes)}</td><td class="num">${f.denied ? `<span class="pill err">${fmtNum(f.denied)}</span>` : '0'}</td><td class="nowrap small">${fmtAgo(f.last)}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">Aucune activité sur les dossiers supervisés</td></tr>'}</tbody></table></div></div>
      <div class="card flush">${cardHead('Fichiers les plus utilisés (30 jours)')}
        <div class="table-wrap"><table><tbody>${d.files.map(f => `<tr><td class="path small">${fileIcon(f.path || '')}${esc(f.path)}<div class="cell-sub">${esc(f.share_name || '')}</div></td><td class="num">${fmtNum(f.n)}</td><td class="nowrap small">${fmtAgo(f.last)}</td></tr>`).join('') || '<tr><td class="muted">—</td></tr>'}</tbody></table></div></div>
    </div>
    <div class="grid span-2-1">
      <div class="card flush">${cardHead('Espace occupé par dossier supervisé', 'Fichiers dont il est propriétaire, ou rangés dans son dossier personnel')}
        <div class="table-wrap"><table><thead><tr><th>Dossier</th><th class="num">Volume</th><th class="num">Fichiers</th><th class="num">Inactifs</th><th>Principaux sous-dossiers</th></tr></thead><tbody>
        ${d.storage.map(s => `<tr class="clickable" data-go="#/share/${s.share_id}"><td><div class="strong">${esc(s.share_name || s.share_id)}</div><div class="cell-sub">${s.via === 'home' ? 'dossier personnel' : s.via === 'mixte' ? 'propriétaire + dossier personnel' : 'propriétaire des fichiers'}${s.share_total ? ` · ${fmtPct(s.size * 100 / s.share_total, 1)} du dossier` : ''}</div></td>
          <td class="num">${fmtBytes(s.size)}</td><td class="num">${fmtNum(s.files)}</td><td class="num" data-tip="Non modifiés depuis le délai d'inactivité du dossier">${fmtBytes(s.stale_size)}</td>
          <td class="small">${s.folders.slice(0, 4).map(x => `${esc(x.name)} <span class="muted">${fmtBytes(x.size)}</span>`).join(' · ')}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Aucun fichier attribué à cet utilisateur (lancez une analyse des dossiers).</td></tr>'}</tbody></table></div></div>
      <div class="card">${cardHead('Évolution de l\'espace occupé')}<div id="u-growth"></div>
        ${d.storage[0] ? `<div class="small muted" style="margin-top:10px">Types de fichiers : ${d.storage[0].exts.slice(0, 6).map(x => `${esc(x.ext)} ${fmtBytes(x.size)}`).join(' · ')}</div>` : ''}</div>
    </div>
    <div class="card flush">${cardHead('Dernières opérations', '100 plus récentes', `<a class="small" href="#/logs?u=${encodeURIComponent(p.user)}">Analyser son journal →</a>`)}
      <div class="table-wrap tall">${secEventsTable(d.events, false)}</div></div>`;
  rowLinks(view);
  state.redraw = [];
  stackedChart($('#u-chart', view), actCols(d.week), ACT_SERIES, { every: 12, height: 180 });
  lineChart($('#u-growth', view), d.history.map(h => ({ x: new Date(h.day + 'T12:00:00').getTime() / 1000, y: h.size, extra: `${fmtNum(h.files)} fichiers` })), { height: 200 });
  const loadLive = async () => {
    const l = await api(`/api/security/user/live?u=${encodeURIComponent(p.user)}`);
    const q = l.ntfs_quotas || [];
    $('#u-live', view).innerHTML = (l.sessions.length || l.open_files.length || l.blocked.length || q.length) ? `<div class="card">${cardHead('En ce moment', 'Sessions SMB, fichiers ouverts, blocages et quotas NTFS')}
      ${l.blocked.length ? `<div class="banner critical" style="margin-bottom:10px">${icon('lock')}<span>Accès bloqué sur : ${l.blocked.map(esc).join(', ')}</span>${can('admin') ? '<button class="btn sm" data-uact="unblock">Rétablir l\'accès…</button>' : ''}</div>` : ''}
      <div class="small">${l.sessions.map(s => `<span class="pill on">session ${esc(s.client || '')} · ${fmtNum(s.opens || 0)} fichier(s) ouvert(s)</span>`).join(' ')}</div>
      ${l.open_files.length ? `<div class="details-box" style="margin-top:8px">${l.open_files.slice(0, 50).map(f => esc(f.path)).join('<br>')}</div>` : ''}
      ${q.map(x => `<div class="small" style="margin-top:8px">Quota NTFS ${esc(x.volume)} : ${fmtBytes(x.used)}${x.limit ? ` / ${fmtBytes(x.limit)}` : ' (suivi seul)'}</div>`).join('')}</div>` : '';
  };
  guard(loadLive)();
  const reload = () => route();
  const shareChoice = list => `<div class="field full"><label>Dossiers</label>${list.map(s => chk('s_' + s.id, `${s.client_name} › ${s.name}`, false)).join('<br>')}</div>`;
  const actions = {
    toggle: el => { const t = $('#u-table', view); const c = $('#u-chart', view); t.hidden = !t.hidden; c.hidden = !t.hidden; el.textContent = t.hidden ? 'Voir le tableau' : 'Voir le graphique'; },
    watch: async () => { await api(`/api/security/user?u=${encodeURIComponent(p.user)}`, { method: 'PUT', body: { watch: !p.watch } }); toast(p.watch ? 'Surveillance renforcée retirée' : 'Utilisateur surveillé : seuils de détection divisés par deux'); reload(); },
    settings: () => modal({ title: `Capacité et notes — ${name}`, body: `<div class="form-grid">
        ${field('Capacité allouée (Go)', inp('capacity_gb', p.capacity_bytes ? +(p.capacity_bytes / GB).toFixed(2) : '', 'number', 'min="0" step="0.5" placeholder="0 = capacité par défaut"'), 'Alerte à l\'approche puis au dépassement, tous dossiers supervisés confondus.')}
        <div class="field">${chk('service', 'Compte de service (exclu de l\'analyse comportementale)', p.service)}</div>
        ${field('Notes', `<textarea name="notes" placeholder="ex. départ prévu le 30/11, prestataire, compte partagé…">${esc(p.notes || '')}</textarea>`, '', true)}</div>`,
      onSubmit: async f => { const v = formData(f); await api(`/api/security/user?u=${encodeURIComponent(p.user)}`, { method: 'PUT', body: { capacity_gb: v.capacity_gb || 0, service: v.service, notes: v.notes } }); toast('Enregistré'); reload(); } }),
    close: () => confirmBox('Fermer ses sessions ?', `<p>Toutes les sessions SMB de <b>${esc(name)}</b> sont fermées : ses fichiers ouverts sont libérés (les modifications non enregistrées sont perdues). Il peut se reconnecter aussitôt : pour l'en empêcher, bloquez son accès ou désactivez son compte.</p>`,
      async () => { const r = await api(`/api/security/user/action?u=${encodeURIComponent(p.user)}`, { method: 'POST', body: { action: 'close_sessions' } }); toast(r.message); loadLive(); }, 'Fermer les sessions'),
    block: () => modal({ title: `Bloquer l'accès de ${name}`, danger: true, submit: 'Bloquer',
      body: `<p>Un refus d'accès est ajouté au niveau du <b>partage SMB</b> (les droits NTFS ne changent pas) ; réversible depuis cette fiche. Fermez aussi ses sessions pour couper l'accès en cours.</p>${shareChoice(shares)}`,
      onSubmit: async f => { const v = formData(f); const ids = shares.filter(s => v['s_' + s.id]).map(s => s.id); const r = await api(`/api/security/user/action?u=${encodeURIComponent(p.user)}`, { method: 'POST', body: { action: 'block', share_ids: ids } }); toast(r.message); loadLive(); } }),
    unblock: () => modal({ title: `Rétablir l'accès de ${name}`, submit: 'Rétablir', body: shareChoice(shares),
      onSubmit: async f => { const v = formData(f); const ids = shares.filter(s => v['s_' + s.id]).map(s => s.id); const r = await api(`/api/security/user/action?u=${encodeURIComponent(p.user)}`, { method: 'POST', body: { action: 'unblock', share_ids: ids } }); toast(r.message); loadLive(); } }),
    disable: () => modal({ title: `Désactiver le compte de ${name}`, danger: true, submit: 'Désactiver',
      body: `<p>Le compte est désactivé dans Active Directory (ou en local) : plus aucune connexion possible, sur aucun serveur. À réserver à une compromission avérée ou à un départ. Réactivation possible ensuite.</p>${field('Confirmation', inp('confirm', '', 'text', 'placeholder="DÉSACTIVER" autocomplete="off"'), 'Saisissez DÉSACTIVER.', true)}`,
      onSubmit: async f => { const r = await api(`/api/security/user/action?u=${encodeURIComponent(p.user)}`, { method: 'POST', body: { action: 'disable', confirm: formData(f).confirm || '' } }); toast(r.message); } }),
  };
  const handler = e => { const el = e.target.closest('[data-uact]'); if (el) { e.preventDefault(); guard(actions[el.dataset.uact])(el); } };
  view.addEventListener('click', handler);
  $('#page-actions').addEventListener('click', handler);
}

function secEventsTable(rows, showUser = true) {
  const lab = (secLabels || {}).actions || {};
  if (!rows.length) return emptyState('list', 'Aucun événement', 'Rien pour ces critères.');
  return `<table><thead><tr><th>Date</th>${showUser ? '<th>Utilisateur</th>' : ''}<th>Action</th><th>Élément</th><th>Poste</th><th>Source</th></tr></thead><tbody>
    ${rows.map(e => { const det = e.details ? (() => { try { return JSON.parse(e.details); } catch { return {}; } })() : {};
      const fail = e.result === 'fail';
      return `<tr><td class="nowrap small">${fmtDate(e.ts, true)}</td>${showUser ? `<td>${userLink(e.user)}</td>` : ''}
        <td class="nowrap">${fail ? '<span class="pill err">' : '<span class="pill">'}${esc(lab[e.action] || e.action)}</span></td>
        <td class="path small">${e.path ? esc(e.path) : ''}${e.dest_path ? ` → ${esc(e.dest_path)}` : ''}<div class="cell-sub">${esc(e.share_name || '')}${Object.keys(det).length ? (e.share_name ? ' · ' : '') + esc(Object.entries(det).map(([k, v]) => `${k} : ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')) : ''}</div></td>
        <td class="mono small">${esc(e.client || '')}</td><td class="small muted">${esc(SOURCE_LABEL[e.source] || e.source)}${e.event_id ? ` · ${e.event_id}` : ''}</td></tr>`; }).join('')}</tbody></table>`;
}

// ------------------------------------------------------------------ analyse des journaux
async function viewLogs(view, query) {
  await secLabelsGet();
  setPage('Analyse des journaux', 'Sécurité',
    `${can('admin') ? `<button class="btn" data-lact="import">${icon('upload')} Importer un journal</button>` : ''}<a class="btn" id="csv" href="#">${icon('download')} Export CSV</a>`,
    'Recherche dans toute l\'activité collectée et importée : qui, quoi, où, quand, depuis quel poste.');
  const shares = await api('/api/shares');
  const period = query.get('period') || '604800';
  view.innerHTML = `<div class="card flush"><div class="filters" style="padding:16px 20px">
      <input type="search" data-f name="u" placeholder="Utilisateur" value="${esc(query.get('u') || '')}" style="width:180px">
      ${sel('share_id', [['', 'Tous les dossiers'], ...shares.map(s => [s.id, `${s.client_name} › ${s.name}`])], query.get('share_id') || '', 'data-f')}
      ${sel('action', ACTION_FILTERS, query.get('action') || '', 'data-f')}
      ${sel('result', [['', 'Succès et échecs'], ['ok', 'Succès'], ['fail', 'Échecs / refus']], '', 'data-f')}
      ${sel('period', [['3600', 'Dernière heure'], ['86400', '24 heures'], ['604800', '7 jours'], ['2592000', '30 jours'], ['7776000', '90 jours'], ['', 'Tout']], period, 'data-f')}
      ${sel('source', [['', 'Toutes les sources'], ...Object.entries(SOURCE_LABEL).filter(([k]) => k !== 'analysis')], '', 'data-f')}
      <input type="search" data-f name="client" placeholder="Poste / IP" style="width:130px">
      <input type="search" data-f name="q" placeholder="Chemin, fichier, détail…" style="width:220px">
    </div></div>
    <div id="l-stats"></div>
    <div class="card flush">${cardHead('Événements', '', '<span class="small muted" id="l-count"></span>')}<div id="l-rows" class="table-wrap tall"></div>
      <div class="btn-row" style="padding:12px 20px;border-top:1px solid var(--border)"><button class="btn sm" data-lact="more">Charger plus</button></div></div>`;
  let limit = 200;
  const params = () => {
    const p = new URLSearchParams();
    $$('[data-f]', view).forEach(el => { if (el.name !== 'period' && el.value) p.set(el.name, el.value); });
    const per = $('[name=period]', view).value;
    if (per) p.set('since', String(Math.floor(Date.now() / 1000) - Number(per)));
    return p;
  };
  const load = async () => {
    const p = params();
    $('#csv').href = '/api/security/events.csv?' + p;
    const [st, ev] = await Promise.all([api('/api/security/stats?' + p), api(`/api/security/events?${p}&limit=${limit}`)]);
    const lab = secLabels.actions;
    const stepLabel = t => (st.step >= 86400 ? new Date(t * 1000).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }) : new Date(t * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }));
    $('#l-stats', view).innerHTML = `<div class="tiles" style="margin-top:16px">
        ${tile({ label: 'Événements', value: compact(st.total), ic: 'list', sub: st.start ? `du ${fmtDate(st.start)} au ${fmtDate(st.end)}` : '' })}
        ${tile({ label: 'Utilisateurs', value: st.by_user.length >= 15 ? '15 et +' : fmtNum(st.by_user.length), ic: 'clients' })}
        ${tile({ label: 'Échecs / refus', value: compact(st.histogram.reduce((a, h) => a + (h.fails || 0), 0)), ic: 'lock', tone: 'warning' })}
        ${tile({ label: 'Postes', value: st.by_client.length >= 15 ? '15 et +' : fmtNum(st.by_client.length), ic: 'activity', tone: 'aqua' })}</div>
      <div class="card">${cardHead('Répartition dans le temps', `un point = ${st.step >= 86400 ? fmtDuration(st.step) : st.step >= 3600 ? (st.step / 3600) + ' h' : (st.step / 60) + ' min'}`)}<div id="l-hist"></div></div>
      <div class="grid cols-2" style="margin-top:16px">
        <div class="card">${cardHead('Par action')}${hbars(st.by_action.map(r => ({ name: lab[r.k] || r.k, value: r.n })), { fmt: v => fmtNum(v) })}</div>
        <div class="card">${cardHead('Par utilisateur')}${hbars(st.by_user.map(r => ({ name: r.k, value: r.n })), { fmt: v => fmtNum(v) })}</div>
        <div class="card">${cardHead('Par dossier')}${hbars(st.by_share.map(r => ({ name: r.name, value: r.n })), { fmt: v => fmtNum(v) })}</div>
        <div class="card">${cardHead('Par poste')}${hbars(st.by_client.map(r => ({ name: r.k, value: r.n })), { fmt: v => fmtNum(v) })}</div></div>`;
    state.redraw = [];
    if (st.histogram.length) columnChart($('#l-hist', view), st.histogram.map(h => ({ label: stepLabel(h.t), value: h.n, tip: `${fmtDate(h.t)}<br><b>${fmtNum(h.n)}</b> événement(s)${h.fails ? `<br>dont ${fmtNum(h.fails)} échec(s) / refus` : ''}` })), { every: Math.max(1, Math.ceil(st.histogram.length / 12)), height: 170 });
    else $('#l-hist', view).innerHTML = '<div class="empty">Aucun événement</div>';
    $('#l-rows', view).innerHTML = secEventsTable(ev.rows);
    $('#l-count', view).textContent = `${fmtNum(ev.rows.length)} affiché(s) sur ${fmtNum(ev.total)}`;
  };
  $$('[data-f]', view).forEach(el => el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', () => { limit = 200; clearTimeout(view._t); view._t = setTimeout(guard(load), 300); }));
  const actions = { more: () => { limit = Math.min(2000, limit + 300); return load(); }, import: () => securityImport(() => load()) };
  const handler = e => { const el = e.target.closest('[data-lact]'); if (el) { e.preventDefault(); guard(actions[el.dataset.lact])(el); } };
  view.addEventListener('click', handler);
  $('#page-actions').addEventListener('click', handler);
  await load();
}

// ------------------------------------------------------------------ préparation de l'audit
async function viewAuditSetup(view) {
  setPage("Préparer l'audit", 'Sécurité', `<a class="btn" href="/api/security/audit/script.ps1">${icon('download')} Script PowerShell</a>`,
    'Ce que le serveur doit enregistrer pour que VigieFS sache qui lit, modifie, supprime, et qui échoue à se connecter.');
  view.innerHTML = `<div class="card">${emptyState('refresh', 'Contrôle des réglages du serveur…')}</div>`;
  const load = async () => {
    const a = await api('/api/security/audit');
    const groups = {};
    a.items.forEach(i => { (groups[i.group] = groups[i.group] || []).push(i); });
    const missing = a.items.filter(i => i.status !== 'ok');
    const ST = { ok: ['ok', 'Conforme'], missing: ['critical', 'À activer'], partial: ['warning', 'Incomplet'], error: ['critical', 'Erreur'] };
    view.innerHTML = `
      ${a.platform === 'windows' && !a.admin ? `<div class="banner warning">${icon('alert')}<span>VigieFS ne tourne pas avec les droits administrateur : il ne peut ni lire tous les réglages ni les appliquer. Utilisez le script PowerShell, ou le service VigieFS (compte SYSTEM).</span></div>` : ''}
      <div class="banner ${missing.length ? 'warning' : 'ok'}">${icon(missing.length ? 'alert' : 'check')}<span>${missing.length ? `${missing.length} réglage(s) à corriger sur ${a.items.length}.` : 'Tous les réglages nécessaires sont en place.'}
        ${a.platform === 'windows' ? ' Si une stratégie de groupe (GPO) du domaine définit l\'audit, reportez-y ces réglages : elle prime sur ceux du serveur.' : ''}</span></div>
      <div class="card"><p class="small" style="margin-top:0">${a.platform === 'windows'
        ? 'Volume : l\'audit des <b>lectures</b> est le plus bavard. Il est utile pour repérer une copie massive de données ; s\'il est désactivé dans Paramètres › Comportements & journaux, seuls les écritures, suppressions et changements de droits sont audités.'
        : 'Sous Linux / NAS, le module Samba <b>full_audit</b> trace les accès ; VigieFS lit son journal (par défaut /var/log/samba/audit.log, ou syslog).'}</p>
      <form id="a-form">${Object.entries(groups).map(([g, items]) => `<h3 style="margin:18px 0 8px">${esc(g)}</h3><div class="table-wrap"><table><thead><tr>${can('admin') && a.platform === 'windows' ? '<th class="check"></th>' : ''}<th>Réglage</th><th>État</th><th>Actuel</th><th>Attendu</th></tr></thead><tbody>
        ${items.map(i => `<tr class="audit-row">${can('admin') && a.platform === 'windows' ? `<td class="check">${i.status !== 'ok' && i.status !== 'error' ? `<input type="checkbox" name="${esc(i.key)}" checked aria-label="Corriger">` : ''}</td>` : ''}
          <td><div class="strong">${esc(i.label)}</div><div class="cell-sub">${esc(i.detail)}</div><details class="small"><summary>Commande</summary><pre class="snippet">${esc(i.command)}</pre></details></td>
          <td>${badge(ST, i.status)}</td><td class="small">${esc(i.current)}</td><td class="small">${esc(i.expected)}</td></tr>`).join('')}</tbody></table></div>`).join('')}
      ${can('admin') && a.platform === 'windows' && missing.length ? `<div class="btn-row" style="margin-top:16px"><button class="btn primary" type="submit">${icon('check')} Appliquer la sélection</button><span class="small muted">Réversible ; journalisé dans le journal d'audit de VigieFS.</span></div>` : ''}</form></div>
      ${a.snippet ? `<div class="card">${cardHead('Extrait de configuration Samba', 'À adapter puis recharger Samba (smbcontrol all reload-config) et rsyslog')}<pre class="snippet">${esc(a.snippet)}</pre></div>` : ''}`;
    const form = $('#a-form', view);
    form && form.addEventListener('submit', guard(async e => {
      e.preventDefault();
      const keys = Object.entries(formData(form)).filter(([, v]) => v).map(([k]) => k);
      if (!keys.length) return toast('Rien de sélectionné', 'error');
      const r = await api('/api/security/audit/apply', { method: 'POST', body: { keys } });
      const bad = r.results.filter(x => !x.ok);
      toast(bad.length ? `${bad.length} échec(s) : ${bad.map(x => x.error).join(' ; ')}` : `${r.results.length} réglage(s) appliqué(s)`, bad.length ? 'error' : 'ok');
      load();
    }));
  };
  await load();
}
