/* VigieFS — gestion : clients, dossiers supervisés, fiche dossier, explorateur */
'use strict';

function clientForm(c = {}) {
  return `<div class="form-grid">
    ${field('Nom du client', inp('name', c.name, 'text', 'required maxlength="120"'), '', true)}
    ${field('Contact', inp('contact_name', c.contact_name))}
    ${field('E-mail du contact', inp('contact_email', c.contact_email, 'email'))}
    ${field('Notes', `<textarea name="notes">${esc(c.notes || '')}</textarea>`, 'Contrat, SLA, référence Datto, particularités…', true)}
  </div>`;
}

async function viewClients(view) {
  setPage('Clients', 'Gestion', can('operator') ? `<button class="btn primary" data-new>${icon('plus')} Nouveau client</button>` : '',
    'Vos clients et leurs serveurs de fichiers.');
  const render = async () => {
    const rows = await api('/api/clients');
    view._rows = rows;
    view.innerHTML = `<div class="card flush"><div class="filters"><input type="search" placeholder="Rechercher un client…" id="q"></div>
      <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Client</th><th>Contact</th><th class="num">Dossiers</th><th class="num">Volume</th><th class="num">Fichiers</th><th>État</th><th></th></tr></thead>
      <tbody>${rows.length ? rows.map(c => `<tr class="clickable" data-go="#/shares?client=${c.id}" data-search="${esc((c.name + ' ' + c.contact_name + ' ' + c.contact_email).toLowerCase())}">
        <td><div class="cell-title">${esc(c.name)}</div>${c.notes ? `<div class="cell-sub ellipsis" style="max-width:360px">${esc(c.notes)}</div>` : ''}</td>
        <td>${esc(c.contact_name || '—')}${c.contact_email ? `<div class="small"><a href="mailto:${esc(c.contact_email)}">${esc(c.contact_email)}</a></div>` : ''}</td>
        <td class="num">${c.share_count}</td><td class="num">${fmtBytes(c.total_size)}</td><td class="num">${fmtNum(c.total_files)}</td>
        <td>${c.critical_alerts ? sev('critical') : c.open_alerts ? `<span class="sev warning">${c.open_alerts} alerte(s)</span>` : sev('ok')}</td>
        <td class="nowrap">${can('operator') ? `<button class="btn sm" data-act="edit" data-id="${c.id}">Modifier</button>` : ''}
        ${can('admin') ? ` <button class="btn sm ghost danger" data-act="del" data-id="${c.id}">Supprimer</button>` : ''}</td></tr>`).join('')
        : `<tr><td colspan="7">${emptyState('clients', 'Aucun client', can('operator') ? 'Commencez par créer votre premier client.' : '')}</td></tr>`}</tbody></table></div></div>`;
    $('#q', view).addEventListener('input', e => { const q = e.target.value.toLowerCase(); $$('tr[data-search]', view).forEach(tr => { tr.hidden = !tr.dataset.search.includes(q); }); });
  };
  rowLinks(view);
  bindActions(view, {
    edit: el => { const c = view._rows.find(r => r.id == el.dataset.id); modal({ title: `Modifier « ${c.name} »`, body: clientForm(c), onSubmit: async f => { await api(`/api/clients/${c.id}`, { method: 'PUT', body: formData(f) }); toast('Client mis à jour'); render(); } }); },
    del: el => { const c = view._rows.find(r => r.id == el.dataset.id); confirmBox('Supprimer le client', `Supprimer <b>${esc(c.name)}</b> et retirer ses ${c.share_count} dossier(s) de la supervision ?<br><br><span class="muted">Les fichiers sur disque ne sont <b>pas</b> supprimés.</span>`, async () => { await api(`/api/clients/${c.id}`, { method: 'DELETE' }); toast('Client supprimé'); render(); }, 'Supprimer'); },
  });
  const nb = $('[data-new]'); if (nb) nb.onclick = () => modal({ title: 'Nouveau client', body: clientForm(), submit: 'Créer', onSubmit: async f => { await api('/api/clients', { method: 'POST', body: formData(f) }); toast('Client créé'); render(); } });
  await render();
}

// ------------------------------------------------------------------ dossiers supervisés
const INTERVALS = [[0, 'Désactivée (manuelle)'], [60, 'Toutes les heures'], [180, 'Toutes les 3 h'], [360, 'Toutes les 6 h'], [720, 'Toutes les 12 h'], [1440, 'Quotidienne'], [10080, 'Hebdomadaire']];
const SHARE_DEFAULTS = { quota_warn_pct: 80, quota_crit_pct: 95, scan_interval_min: 360, stale_days: 365, disk_free_warn_pct: 10, monitor_enabled: 1, screening_enabled: 1, screening_action: 'alert', quota_bytes: 0, archive_enabled: 0, archive_age_days: 730, archive_min_size_kb: 0, archive_extensions: '', archive_exclude: '', archive_criterion: 'mtime', archive_stub: 1, archive_interval_days: 30, access_path: '', acl_depth: 4, acl_interval_h: 24, matrix_depth: 2 };

function shareForm(s, clients) {
  s = Object.assign({}, SHARE_DEFAULTS, s || {});
  const intervals = INTERVALS.some(([v]) => v == s.scan_interval_min) ? INTERVALS : INTERVALS.concat([[s.scan_interval_min, `Toutes les ${s.scan_interval_min} min`]]);
  return `<div class="form">
    <div class="form-grid">
      ${field('Client', sel('client_id', clients.map(c => [c.id, c.name]), s.client_id, 'required'))}
      ${field('Nom du dossier', inp('name', s.name, 'text', 'required placeholder="ex. Comptabilité"'))}
      ${field('Chemin', inp('path', s.path, 'text', 'required placeholder="D:\\Partages\\Client\\Compta  ou  \\\\SRV-FS01\\Compta"'), 'Chemin local ou UNC accessible par le compte de service VigieFS.', true)}
      ${field('Description', inp('description', s.description), '', true)}
    </div>
    <fieldset><legend>Quota</legend><div class="form-grid">
      ${field('Quota (Go)', inp('quota_gb', s.quota_bytes ? +(s.quota_bytes / GB).toFixed(2) : '', 'number', 'min="0" step="0.1" placeholder="0 = illimité"'), 'Quota logiciel : alertes et prévisions (sans blocage d\'écriture).')}
      ${field('Seuils d\'alerte (%)', `<div class="btn-row"><input type="number" name="quota_warn_pct" value="${s.quota_warn_pct}" min="1" max="100" style="width:90px" aria-label="avertissement"> <span class="muted small">avert.</span> <input type="number" name="quota_crit_pct" value="${s.quota_crit_pct}" min="1" max="200" style="width:90px" aria-label="critique"> <span class="muted small">critique</span></div>`)}
    </div></fieldset>
    <fieldset><legend>Supervision</legend><div class="form-grid">
      ${field('Analyse automatique', sel('scan_interval_min', intervals, s.scan_interval_min))}
      ${field('Données inactives après (jours)', inp('stale_days', s.stale_days, 'number', 'min="0"'), 'Fichiers non modifiés depuis — candidats à l\'archivage.')}
      ${field('Alerte si espace libre du volume < (%)', inp('disk_free_warn_pct', s.disk_free_warn_pct, 'number', 'min="0" max="99"'), '0 = désactivé.')}
      <div class="field" style="justify-content:flex-end">${chk('monitor_enabled', 'Surveillance temps réel (journal + anti-rançongiciel)', s.monitor_enabled)}</div>
    </div></fieldset>
    <fieldset><legend>Filtrage de fichiers</legend><div class="form-grid">
      <div class="field">${chk('screening_enabled', 'Activer le filtrage', s.screening_enabled)}</div>
      ${field('Action', sel('screening_action', [['alert', 'Alerter uniquement'], ['quarantine', 'Alerter et mettre en quarantaine']], s.screening_action), 'La quarantaine nécessite un dossier configuré (Paramètres › Sécurité).')}
      ${field('Extensions bloquées', `<textarea name="blocked_extensions" placeholder="Vide = liste par défaut">${esc(s.blocked_extensions || '')}</textarea>`, 'Séparées par des virgules, ex. .exe, .mp4, .iso', true)}
    </div></fieldset>
    <fieldset><legend>Droits et chemins</legend><div class="form-grid">
      ${field('Chemin utilisé par les utilisateurs', inp('access_path', s.access_path, 'text', 'placeholder="ex. \\\\SRV-FS01\\Compta ou P:\\"'), 'Sert à calculer la longueur réelle des chemins (lecteur réseau, partage UNC). Vide = chemin local.', true)}
      ${field('Profondeur d\'analyse des droits', inp('acl_depth', s.acl_depth, 'number', 'min="0" max="8"'), 'Niveaux de dossiers dont les ACL sont lues.')}
      ${field('Analyse des droits (heures)', inp('acl_interval_h', s.acl_interval_h, 'number', 'min="0"'), '0 = manuelle. Détecte les modifications de droits.')}
      ${field('Profondeur de la matrice', inp('matrix_depth', s.matrix_depth, 'number', 'min="0" max="6"'), 'Bonne pratique : droits définis sur 2 niveaux, héritage en dessous.')}
    </div></fieldset>
    <fieldset><legend>Archivage à froid automatique</legend>${archivePolicyFields(s)}</fieldset></div>`;
}
function archivePolicyFields(s, wide = false) {
  return `<div class="form-grid${wide ? ' cols-3' : ''}">
    <div class="field full">${chk('archive_enabled', 'Archiver automatiquement les données froides de ce dossier', s.archive_enabled)}</div>
    ${field('Fichiers non utilisés depuis (jours)', inp('archive_age_days', s.archive_age_days, 'number', 'min="1"'), '730 = 2 ans.')}
    ${field('Fréquence (jours)', inp('archive_interval_days', s.archive_interval_days, 'number', 'min="1"'))}
    ${field('Critère', sel('archive_criterion', [['mtime', 'Date de dernière modification (recommandé)'], ['atime', 'Date de dernier accès']], s.archive_criterion), 'Le dernier accès n\'est fiable que si NTFS le met à jour.')}
    ${field('Taille minimale (Ko)', inp('archive_min_size_kb', s.archive_min_size_kb, 'number', 'min="0"'), '0 = tous les fichiers.')}
    ${field('Uniquement ces extensions', inp('archive_extensions', s.archive_extensions, 'text', 'placeholder="Vide = toutes (ex. .pdf, .mp4, .zip)"'))}
    ${field('Exclusions', inp('archive_exclude', s.archive_exclude, 'text', 'placeholder="ex. Modèles/*, *.lnk"'), 'Motifs de chemins ou de noms, séparés par des virgules.')}
    <div class="field full">${chk('archive_stub', 'Laisser un raccourci « [archivé] » à la place de chaque fichier déplacé', s.archive_stub)}</div></div>`;
}
function shareBody(f, base = {}) {
  const d = Object.assign({}, SHARE_DEFAULTS, base, formData(f));
  d.client_id = Number(d.client_id);
  if ('quota_gb' in d) { d.quota_bytes = Math.round((d.quota_gb || 0) * GB); delete d.quota_gb; }
  d.scan_interval_min = Number(d.scan_interval_min);
  for (const k of ['monitor_enabled', 'screening_enabled', 'archive_enabled', 'archive_stub']) d[k] = !!d[k];
  for (const k of ['quota_warn_pct', 'quota_crit_pct', 'stale_days', 'disk_free_warn_pct', 'archive_min_size_kb', 'acl_depth', 'acl_interval_h', 'matrix_depth']) d[k] = d[k] ?? 0;
  const keep = ['client_id', 'name', 'path', 'description', 'quota_bytes', 'quota_warn_pct', 'quota_crit_pct', 'screening_enabled', 'screening_action', 'blocked_extensions', 'monitor_enabled', 'scan_interval_min', 'stale_days', 'disk_free_warn_pct', 'archive_enabled', 'archive_age_days', 'archive_min_size_kb', 'archive_extensions', 'archive_exclude', 'archive_criterion', 'archive_stub', 'archive_interval_days', 'access_path', 'acl_depth', 'acl_interval_h', 'matrix_depth'];
  return Object.fromEntries(keep.map(k => [k, d[k] ?? '']));
}
async function openShareForm(share, onDone, presetClient) {
  const clients = await api('/api/clients');
  if (!clients.length) { toast("Créez d'abord un client.", 'error'); location.hash = '#/clients'; return; }
  modal({
    title: share ? `Modifier « ${share.name} »` : 'Ajouter un dossier supervisé', body: shareForm(share || { client_id: presetClient }, clients), submit: share ? 'Enregistrer' : 'Ajouter',
    onSubmit: async f => {
      const body = shareBody(f, share || {});
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
  if (s.scan_running) return `<span class="nowrap"><span class="dot run"></span>Analyse… ${compact(s.scan_progress?.file_count || 0)}</span>`;
  const bad = s.last_scan_status && s.last_scan_status !== 'ok';
  return `<span class="nowrap">${fmtAgo(s.last_scan_at)}</span>${bad ? ` <span class="pill err">${esc(s.last_scan_status)}</span>` : ''}`;
}

async function viewShares(view, query) {
  const clientFilter = query.get('client') || '';
  setPage('Dossiers supervisés', 'Gestion', can('operator') ? `<button class="btn primary" data-new>${icon('plus')} Ajouter un dossier</button>` : '',
    'Partages et dossiers surveillés, avec quotas, analyses et état temps réel.');
  const clients = await api('/api/clients');
  view.innerHTML = `<div class="card flush"><div class="filters">
      ${sel('client', [['', 'Tous les clients'], ...clients.map(c => [c.id, c.name])], clientFilter, 'id="f-client"')}
      <input type="search" id="q" placeholder="Rechercher (nom, chemin)…">
    </div><div id="list" style="margin-top:14px"></div></div>`;
  const render = async () => {
    const cid = $('#f-client', view).value;
    const rows = await api('/api/shares' + (cid ? `?client_id=${cid}` : ''));
    const q = $('#q', view).value.toLowerCase();
    $('#list', view).innerHTML = `<div class="table-wrap"><table><thead><tr><th>Dossier</th><th>Client</th><th class="num">Volume</th><th>Quota</th><th>Surveillance</th><th>Dernière analyse</th><th>État</th><th></th></tr></thead><tbody>
      ${rows.length ? rows.map(s => { const hay = (s.name + ' ' + s.path + ' ' + s.client_name).toLowerCase(); return `<tr class="clickable" data-go="#/share/${s.id}" data-search="${esc(hay)}" ${q && !hay.includes(q) ? 'hidden' : ''}>
        <td><div class="cell-title">${icon('folder', 'file-icon dir')}${esc(s.name)}${s.path_available ? '' : ' <span class="sev critical">Inaccessible</span>'}</div><div class="cell-sub mono share-path">${esc(s.path)}</div></td>
        <td>${esc(s.client_name)}</td>
        <td class="num">${fmtBytes(s.last_size)}<div class="cell-sub">${compact(s.last_file_count)} fichiers</div></td>
        <td style="min-width:170px">${meter(s.quota_pct, s.quota_level)}</td>
        <td>${monitorCell(s)}</td><td>${scanCell(s)}</td>
        <td>${s.open_alerts ? `<span class="sev warning">${s.open_alerts} alerte(s)</span>` : sev('ok')}</td>
        <td class="nowrap">${can('operator') ? (s.scan_running ? `<button class="btn sm" data-act="cancel" data-id="${s.id}">${icon('stop')} Arrêter</button>` : `<button class="btn sm" data-act="scan" data-id="${s.id}" ${s.path_available ? '' : 'disabled'}>${icon('play')} Analyser</button>`) : ''}</td>
      </tr>`; }).join('') : `<tr><td colspan="8">${emptyState('folder', 'Aucun dossier supervisé', can('operator') ? 'Ajoutez un dossier (chemin local ou partage UNC) pour démarrer la supervision.' : '', can('operator') ? '<button class="btn primary" data-act="new">Ajouter un dossier</button>' : '')}</td></tr>`}
      </tbody></table></div>`;
  };
  $('#f-client', view).addEventListener('change', () => { history.replaceState(null, '', '#/shares' + ($('#f-client', view).value ? `?client=${$('#f-client', view).value}` : '')); render(); });
  $('#q', view).addEventListener('input', e => { const q = e.target.value.toLowerCase(); $$('tr[data-search]', view).forEach(tr => { tr.hidden = !tr.dataset.search.includes(q); }); });
  rowLinks(view);
  bindActions(view, {
    scan: async el => { await api(`/api/shares/${el.dataset.id}/scan`, { method: 'POST' }); toast('Analyse lancée'); render(); },
    cancel: async el => { await api(`/api/shares/${el.dataset.id}/scan/cancel`, { method: 'POST' }); toast('Arrêt demandé'); render(); },
    new: () => openShareForm(null, render, clientFilter),
  });
  const nb = $('[data-new]'); if (nb) nb.onclick = () => openShareForm(null, render, clientFilter);
  await render();
  every(5000, guard(render));
}

// ------------------------------------------------------------------ fiche d'un dossier
const SHARE_TABS = [['overview', "Vue d'ensemble", 'dashboard'], ['report', 'Stockage', 'report'], ['architecture', 'Architecture', 'list'], ['rights', 'Droits', 'lock'], ['quotas', 'Quotas', 'disk'], ['cleanup', 'Nettoyage', 'broom'], ['duplicates', 'Doublons', 'copy'], ['archive', 'Archivage', 'archive'], ['integrity', 'Intégrité', 'shield'], ['explorer', 'Explorateur', 'folder'], ['activity', 'Activité', 'activity'], ['alerts', 'Alertes', 'bell']];

async function viewShare(view, query, id, tab = 'overview') {
  let s = await api(`/api/shares/${id}`);
  const actions = () => (can('operator') ? (s.scan_running ? `<button class="btn" data-hact="cancel">${icon('stop')} Arrêter l'analyse</button>` : `<button class="btn primary" data-hact="scan" ${s.path_available ? '' : 'disabled'}>${icon('play')} Analyser maintenant</button>`) + `<button class="btn" data-hact="edit">Modifier</button>` : '') + (can('admin') ? `<button class="btn ghost danger" data-hact="del">Retirer</button>` : '');
  const header = () => setPage(s.name, `<a href="#/shares">Dossiers</a> ${icon('chevron', 'file-icon')} <a href="#/shares?client=${s.client_id}">${esc(s.client_name)}</a>`, actions(),
    `<span class="mono small">${esc(s.path)}</span> · ${monitorCell(s)}${s.path_available ? '' : ' · <span class="sev critical">Inaccessible</span>'}`);
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
  view.innerHTML = `<nav class="tabs">${SHARE_TABS.map(([k, l, ic]) => `<a href="#/share/${id}/${k}" class="${k === tab ? 'active' : ''}">${icon(ic)}${l}</a>`).join('')}</nav><div id="tab"></div>`;
  const pane = $('#tab', view);
  const fn = { overview: shareOverview, report: shareReport, architecture: shareArchitecture, rights: shareRights, quotas: shareQuotas, cleanup: shareCleanup, duplicates: shareDuplicates, archive: shareArchive, integrity: shareIntegrity, explorer: shareExplorer, activity: shareActivity, alerts: shareAlerts }[tab] || shareOverview;
  await fn(pane, s, query);
  every(4000, guard(async () => {
    const was = s.scan_running;
    s = await api(`/api/shares/${id}`);
    if (was !== s.scan_running) { header(); if (!s.scan_running && ['overview', 'report', 'cleanup'].includes(tab)) fn(pane, s, query); }
    const prog = $('#scan-progress', view);
    if (prog) prog.innerHTML = s.scan_running ? `<span class="dot run"></span>Analyse en cours… ${fmtNum(s.scan_progress?.file_count || 0)} fichiers, ${fmtBytes(s.scan_progress?.total_size || 0)}` : '';
  }));
}

async function shareOverview(pane, s) {
  const growth = s.growth_per_day;
  const hist = s.history.filter(h => h.status === 'done').reverse();
  const free = s.disk ? (s.disk.free * 100 / s.disk.total) : null;
  pane.innerHTML = `
    <div class="small muted" id="scan-progress" style="margin-bottom:8px"></div>
    ${s.path_available ? '' : `<div class="banner critical" style="margin-bottom:16px">${icon('alert')}Le chemin est actuellement inaccessible. Vérifiez le volume ou le partage réseau.</div>`}
    <div class="tiles">
      ${tile({ label: 'Volume', value: fmtBytes(s.last_size), ic: 'disk', sub: s.quota_bytes ? 'sur ' + fmtBytes(s.quota_bytes) + ' de quota' : 'sans quota' })}
      ${tile({ label: 'Fichiers', value: fmtNum(s.last_file_count), ic: 'file', tone: 'violet', sub: `analysé ${fmtAgo(s.last_scan_at)}` })}
      ${tile({ label: 'Croissance (30 j)', value: growth == null ? '—' : (growth >= 0 ? '+' : '') + fmtBytes(growth), ic: 'activity', tone: 'aqua', sub: 'par jour' })}
      ${tile({ label: 'Saturation prévue', value: s.forecast_days == null ? '—' : s.forecast_days < 1 ? 'Atteinte' : Math.round(s.forecast_days) + ' j', ic: 'alert', tone: s.forecast_days != null && s.forecast_days < 30 ? 'warning' : 'good', sub: s.quota_bytes ? (s.forecast_days == null ? 'pas de croissance mesurée' : 'au rythme actuel') : 'définir un quota' })}
      ${tile({ label: 'Espace libre du volume', value: s.disk ? fmtBytes(s.disk.free) : '—', ic: 'disk', tone: free != null && free < (s.disk_free_warn_pct || 0) ? 'critical' : 'good', sub: free == null ? '' : fmtPct(free) + ' de ' + fmtBytes(s.disk.total) })}
    </div>
    <div class="grid span-2-1">
      <div class="card">${cardHead('Évolution du volume', 'Taille du dossier à chaque analyse')}<div id="growth"></div></div>
      <div class="card">${cardHead('Configuration')}<div style="margin:2px 0 16px">${meter(s.quota_pct, s.quota_level)}</div>
        <dl class="kv">
          <dt>Analyse auto</dt><dd>${esc((INTERVALS.find(([v]) => v == s.scan_interval_min) || [0, s.scan_interval_min + ' min'])[1])}</dd>
          <dt>Filtrage</dt><dd>${s.screening_enabled ? (s.screening_action === 'quarantine' ? 'Alerte + quarantaine' : 'Alerte') : 'Désactivé'}</dd>
          <dt>Extensions</dt><dd class="small mono">${esc(s.blocked_extensions || 'liste par défaut')}</dd>
          <dt>Données froides</dt><dd>${s.stale_days ? 'non modifiées depuis ' + fmtAge(s.stale_days) : '—'}</dd>
          <dt>Archivage auto</dt><dd>${s.archive_enabled ? `après ${fmtAge(s.archive_age_days)}, tous les ${s.archive_interval_days} j` : 'désactivé'}</dd>
          <dt>Seuils quota</dt><dd>${s.quota_warn_pct} % / ${s.quota_crit_pct} %</dd>
          ${s.description ? `<dt>Description</dt><dd>${esc(s.description)}</dd>` : ''}
        </dl></div>
    </div>
    <div class="card flush">${cardHead('Historique des analyses')}<div class="table-wrap tall"><table><thead><tr><th>Début</th><th>Durée</th><th>Statut</th><th class="num">Volume</th><th class="num">Fichiers</th><th class="num">Erreurs</th><th></th></tr></thead><tbody>
      ${s.history.length ? s.history.map(h => `<tr><td>${fmtDate(h.started_at)}</td><td>${h.finished_at ? fmtDuration(h.finished_at - h.started_at) : '—'}</td>
        <td>${{ done: '<span class="sev ok">Terminée</span>', running: '<span class="sev info">En cours</span>', failed: '<span class="sev critical">Échec</span>', cancelled: '<span class="sev neutral">Annulée</span>' }[h.status] || esc(h.status)}</td>
        <td class="num">${fmtBytes(h.total_size)}</td><td class="num">${fmtNum(h.file_count)}</td><td class="num">${fmtNum(h.error_count)}</td>
        <td>${h.status === 'done' ? `<a href="#/share/${s.id}/report?scan=${h.id}">Rapport →</a>` : ''}</td></tr>`).join('') : `<tr><td colspan="7" class="empty">Aucune analyse pour le moment.</td></tr>`}
    </tbody></table></div></div>`;
  lineChart($('#growth', pane), hist.map(h => ({ x: h.finished_at, y: h.total_size, extra: `${fmtNum(h.file_count)} fichiers` })));
}

function fileTable(files, { empty = 'Aucun fichier', cols = ['size', 'mtime'] } = {}) {
  if (!files || !files.length) return `<div class="empty">${empty}</div>`;
  return `<div class="table-wrap tall"><table><thead><tr><th>Chemin</th>${cols.includes('size') ? '<th class="num">Taille</th>' : ''}${cols.includes('mtime') ? '<th>Modifié</th>' : ''}${cols.includes('kind') ? '<th>Type</th>' : ''}</tr></thead><tbody>
    ${files.map(f => `<tr><td class="path">${fileIcon(f.path)}${esc(f.path)}</td>${cols.includes('size') ? `<td class="num">${fmtBytes(f.size)}</td>` : ''}${cols.includes('mtime') ? `<td class="nowrap">${fmtDay(f.mtime)}</td>` : ''}${cols.includes('kind') ? `<td>${f.kind === 'note' ? 'Note de rançon' : 'Extension chiffrée'}</td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}

async function shareReport(pane, s, query) {
  const scanId = query.get('scan');
  const { scan } = await api(`/api/shares/${s.id}/report` + (scanId ? `?scan_id=${scanId}` : ''));
  if (!scan) { pane.innerHTML = `<div class="card">${emptyState('report', 'Aucun rapport disponible', can('operator') ? 'Lancez une analyse avec le bouton « Analyser maintenant ».' : '')}<div class="small muted" id="scan-progress"></div></div>`; return; }
  const r = scan.report;
  const done = s.history.filter(h => h.status === 'done');
  pane.innerHTML = `
    <div class="filters" style="margin-bottom:16px"><span class="label-text">Analyse du</span>${sel('scan', done.map(h => [h.id, fmtDate(h.started_at)]), scan.id, 'id="scan-sel"')}
      <span class="small muted" id="scan-progress"></span></div>
    ${r.ransomware.length ? `<div class="banner critical" style="margin-bottom:16px">${icon('alert')}${r.ransomware.length} fichier(s) suspects de rançongiciel détectés — voir la section dédiée ci-dessous.</div>` : ''}
    <div class="tiles">
      ${tile({ label: 'Volume analysé', value: fmtBytes(r.total_size), ic: 'disk', sub: `en ${fmtDuration(scan.finished_at - scan.started_at)}` })}
      ${tile({ label: 'Fichiers / dossiers', value: compact(r.file_count), ic: 'file', tone: 'violet', sub: `${fmtNum(r.dir_count)} dossiers` })}
      ${tile({ label: 'Données froides', value: fmtBytes(r.stale_size), ic: 'archive', tone: 'aqua', sub: `${fmtNum(r.stale_count)} fichiers > ${fmtAge(s.stale_days)}${r.total_size ? ' · ' + fmtPct(r.stale_size * 100 / r.total_size, 0) : ''}` })}
      ${tile({ label: 'Fichiers inutiles', value: fmtBytes((r.junk_total || {}).size || 0), ic: 'broom', tone: 'warning', go: `#/share/${s.id}/cleanup`, sub: `${fmtNum((r.junk_total || {}).count || 0)} fichiers · ${fmtNum((r.empty_dirs || {}).count || 0)} dossiers vides` })}
      ${tile({ label: 'Fichiers interdits', value: fmtNum(r.screened.length), ic: 'shield', tone: r.screened.length ? 'warning' : 'good', sub: `${fmtNum(r.error_count)} élément(s) illisible(s)` })}
    </div>
    <div class="grid cols-2">
      <div class="card">${cardHead('Volume par type de fichier')}${hbars(r.by_extension.map(e => ({ name: e.ext, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div class="card">${cardHead('Volume par sous-dossier')}${hbars(r.top_folders.slice(0, 15).map(e => ({ name: e.name, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div class="card">${cardHead('Ancienneté des données', 'Selon la date de dernière modification')}${hbars(r.ages.map(e => ({ name: e.label, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div class="card flush">${cardHead('Les plus gros fichiers')}${fileTable(r.largest)}</div>
    </div>
    <div class="card flush">${cardHead('Fichiers froids les plus volumineux', 'Candidats à l\'archivage à froid', `<a class="btn sm" href="#/share/${s.id}/archive">${icon('archive')} Archiver</a>`)}${fileTable(r.stale_largest, { empty: 'Aucun fichier inactif' })}</div>
    <div class="grid cols-2">
      <div class="card flush">${cardHead('Fichiers interdits par le filtrage')}${fileTable(r.screened, { empty: 'Aucun fichier interdit ✓' })}</div>
      <div class="card flush">${cardHead('Suspects rançongiciel')}${fileTable(r.ransomware, { empty: 'Aucune trace détectée ✓', cols: ['size', 'mtime', 'kind'] })}</div>
    </div>
    ${r.errors.length ? `<div class="card flush">${cardHead("Erreurs d'accès (échantillon)")}<div class="table-wrap"><table><thead><tr><th>Chemin</th><th>Erreur</th></tr></thead><tbody>${r.errors.map(e => `<tr><td class="path">${esc(e.path)}</td><td>${esc(e.error)}</td></tr>`).join('')}</tbody></table></div></div>` : ''}`;
  rowLinks(pane);
  $('#scan-sel', pane).addEventListener('change', e => { location.hash = `#/share/${s.id}/report?scan=${e.target.value}`; });
}

async function shareExplorer(pane, s, query) {
  let cwd = query.get('path') || '';
  pane.innerHTML = `<div class="explorer"><div class="card flush"><div class="card-head" style="padding:12px 16px;margin:0;border-bottom:1px solid var(--border)"><div class="breadcrumb" id="bc"></div><div class="spacer"></div>
      <div class="btn-row">${can('operator') ? `<button class="btn sm" data-act="mkdir">${icon('plus')} Nouveau dossier</button>` : ''}<button class="btn sm" data-act="reload" title="Actualiser">${icon('refresh')}</button></div></div>
      <div id="listing"></div></div>
    <div class="card">${cardHead('Permissions')}<div class="small muted" id="perm-target"></div><div id="perms"></div></div></div>`;
  const loadPerms = async rel => {
    $('#perm-target', pane).innerHTML = `<span class="mono">${esc(rel || '(racine)')}</span>`;
    $('#perms', pane).innerHTML = '<div class="muted small">Chargement…</div>';
    try {
      const p = await api(`/api/shares/${s.id}/permissions?path=${encodeURIComponent(rel)}`);
      if (p.error) { $('#perms', pane).innerHTML = `<div class="error-text">${esc(p.error)}</div>`; return; }
      $('#perms', pane).innerHTML = (p.mode ? `<div class="small" style="margin:6px 0"><code>${esc(p.mode)}</code> · ${esc(p.owner)}:${esc(p.group)}</div>` : '') +
        p.entries.map(e => `<div class="acl-entry ${e.deny ? 'deny' : ''}"><div class="who">${e.deny ? '⛔ Refus — ' : ''}${esc(e.principal)}</div><div>${esc(e.rights)}</div>
          <div class="cell-sub">${e.inherited ? 'Hérité' : 'Explicite'}${e.flags && e.flags.length ? ' · ' + esc(e.flags.join(', ')) : ''}</div>${e.warning ? `<div class="sev warning" style="margin-top:4px">${esc(e.warning)}</div>` : ''}</div>`).join('');
    } catch (err) { $('#perms', pane).innerHTML = `<div class="error-text">${esc(err.message)}</div>`; }
  };
  const load = async () => {
    const d = await api(`/api/shares/${s.id}/browse?path=${encodeURIComponent(cwd)}`);
    cwd = d.path;
    history.replaceState(null, '', `#/share/${s.id}/explorer${cwd ? '?path=' + encodeURIComponent(cwd) : ''}`);
    const parts = cwd ? cwd.split('/') : [];
    $('#bc', pane).innerHTML = `<a href="#" data-act="cd" data-path="">${icon('folder', 'file-icon dir')}${esc(s.name)}</a>` + parts.map((p, i) => `${icon('chevron', 'file-icon')}<a href="#" data-act="cd" data-path="${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</a>`).join('');
    const join = n => (cwd ? cwd + '/' : '') + n;
    $('#listing', pane).innerHTML = `<div class="table-wrap tall"><table><thead><tr><th>Nom</th><th class="num">Taille</th><th>Modifié</th><th></th></tr></thead><tbody>
      ${cwd ? `<tr class="clickable" data-act="cd" data-path="${esc(parts.slice(0, -1).join('/'))}"><td colspan="4">${icon('folder', 'file-icon dir')}..</td></tr>` : ''}
      ${d.entries.length ? d.entries.map(e => { const p = join(e.name); const doc = !e.is_dir && DOC_FAMILY[extOf(e.name)]; return `<tr class="clickable" data-act="${e.is_dir ? 'cd' : 'select'}" data-path="${esc(p)}">
        <td>${e.is_dir ? icon('folder', 'file-icon dir') : fileIcon(e.name)}${esc(e.name)}${e.link ? ' <span class="pill">lien</span>' : ''}${e.error ? ' <span class="pill err">illisible</span>' : ''}</td>
        <td class="num">${e.is_dir ? '' : fmtBytes(e.size)}</td><td class="nowrap">${fmtDate(e.mtime)}</td>
        <td class="nowrap" style="text-align:right">${e.is_dir ? `<button class="btn sm ghost" data-act="perm" data-path="${esc(p)}" title="Permissions">${icon('lock')}</button>` : ''}
          ${doc ? `<button class="btn sm ghost" data-act="diag" data-path="${esc(p)}" title="Diagnostiquer / réparer">${icon('wrench')}</button>` : ''}
          ${!e.is_dir && can('operator') ? `<a class="btn sm ghost" href="/api/shares/${s.id}/download?path=${encodeURIComponent(p)}" title="Télécharger">${icon('download')}</a>` : ''}
          ${can('operator') ? `<button class="btn sm ghost" data-act="rename" data-path="${esc(p)}" data-name="${esc(e.name)}">Renommer</button>` : ''}
          ${can('operator') ? `<button class="btn sm ghost danger" data-act="del" data-path="${esc(p)}" data-name="${esc(e.name)}" data-dir="${e.is_dir ? 1 : 0}" title="Envoyer à la corbeille">${icon('trash')}</button>` : ''}</td></tr>`; }).join('') : `<tr><td colspan="4" class="empty">Dossier vide</td></tr>`}
      </tbody></table></div>${d.truncated ? '<div class="small muted" style="padding:8px 16px">Affichage limité aux 5 000 premiers éléments.</div>' : ''}`;
    loadPerms(cwd);
  };
  bindActions(pane, {
    cd: el => { cwd = el.dataset.path; return load(); },
    select: el => { $$('tr.selected', pane).forEach(t => t.classList.remove('selected')); el.classList.add('selected'); loadPerms(el.dataset.path); },
    perm: el => loadPerms(el.dataset.path),
    reload: load,
    diag: el => openDiagnosis(s, el.dataset.path, load),
    mkdir: () => modal({ title: 'Nouveau dossier', size: 'sm', submit: 'Créer', body: field('Nom', inp('name', '', 'text', 'required')), onSubmit: async f => { await api(`/api/shares/${s.id}/mkdir`, { method: 'POST', body: { path: cwd, name: formData(f).name } }); toast('Dossier créé'); load(); } }),
    rename: el => modal({ title: 'Renommer', size: 'sm', submit: 'Renommer', body: field('Nouveau nom', inp('new_name', el.dataset.name, 'text', 'required')), onSubmit: async f => { await api(`/api/shares/${s.id}/rename`, { method: 'POST', body: { path: el.dataset.path, new_name: formData(f).new_name } }); toast('Élément renommé'); load(); } }),
    del: el => confirmBox('Envoyer à la corbeille', `Déplacer ${el.dataset.dir === '1' ? 'le dossier' : 'le fichier'} <b class="mono">${esc(el.dataset.path)}</b>${el.dataset.dir === '1' ? ' <b>et tout son contenu</b>' : ''} vers la corbeille VigieFS ?<br><br><span class="muted">Restaurable depuis Optimisation › Corbeille pendant la durée de rétention.</span>`, async () => { await api(`/api/shares/${s.id}/delete`, { method: 'POST', body: { path: el.dataset.path } }); toast('Élément envoyé à la corbeille'); load(); }, 'Envoyer à la corbeille'),
  });
  if (!s.path_available) { $('#listing', pane).innerHTML = emptyState('alert', 'Chemin inaccessible'); return; }
  await load();
}

async function shareActivity(pane, s) {
  pane.innerHTML = `<div class="card flush">${eventFilters()}<div id="ev" class="table-wrap tall" style="margin-top:14px"></div></div>`;
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

async function shareAlerts(pane, s) {
  const load = async () => {
    const rows = await api(`/api/alerts?share_id=${s.id}`);
    pane.innerHTML = `<div class="card flush table-wrap">${alertsTable(rows, { selectable: false, showShare: false })}</div>`;
  };
  bindActions(pane, alertActions(pane, load));
  await load();
}
