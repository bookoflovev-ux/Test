/* VigieFS — conformité : architecture, quotas, partages & fichiers ouverts, santé du serveur, RGPD */
'use strict';

const GRADE_TONE = { A: 'good', B: 'good', C: 'warning', D: 'critical', E: 'critical' };
const gradeBadge = g => `<span class="grade grade-${esc(g)}">${esc(g)}</span>`;
const NAMING_LABEL = { invalid_char: 'Caractère interdit', edge_space: 'Espace en début/fin', trailing_dot: 'Point final', reserved: 'Nom réservé Windows', cloud_blocked: 'Bloqué OneDrive/SharePoint', name_too_long: 'Nom > 255 caractères', case_conflict: 'Conflit de casse' };

// ------------------------------------------------------------------ architecture
async function viewArchitecture(view) {
  setPage('Architecture des dossiers', 'Droits & conformité', '', 'Structure, longueur des chemins, nommage, droits et hygiène : un score et des recommandations par dossier supervisé.');
  const rows = await api('/api/architecture');
  const scanned = rows.filter(r => r.scanned);
  const tot = k => scanned.reduce((a, r) => a + (r.metrics[k] || 0), 0);
  view.innerHTML = `
    <div class="tiles">
      ${tile({ label: 'Score moyen', value: scanned.length ? Math.round(scanned.reduce((a, r) => a + r.score, 0) / scanned.length) + ' / 100' : '—', ic: 'report', hero: true, sub: `${scanned.length} dossier(s) analysé(s)` })}
      ${tile({ label: 'Chemins > 260 caractères', value: fmtNum(tot('over_crit')), ic: 'alert', tone: tot('over_crit') ? 'critical' : 'good', sub: `${fmtNum(tot('over_warn'))} au-delà du seuil d'alerte` })}
      ${tile({ label: 'Noms non conformes', value: fmtNum(tot('naming_issues')), ic: 'file', tone: tot('naming_issues') ? 'warning' : 'good', sub: 'Windows / OneDrive / SharePoint' })}
      ${tile({ label: 'Profondeur maximale', value: fmtNum(Math.max(0, ...scanned.map(r => r.metrics.max_depth))), ic: 'folder', tone: 'violet', sub: 'niveaux' })}
    </div>
    <div class="card flush">${cardHead('Dossiers supervisés')}<div class="table-wrap"><table><thead><tr><th>Dossier</th><th>Note</th><th style="width:170px">Score</th><th>Structure</th><th>Chemins</th><th>Nommage</th><th>Droits</th><th>Hygiène</th><th>Priorité</th></tr></thead><tbody>
    ${rows.map(r => `<tr class="clickable" data-go="#/share/${r.id}/architecture"><td><div class="cell-title">${esc(r.name)}</div><div class="cell-sub">${esc(r.client_name)}</div></td>
      <td>${r.scanned ? gradeBadge(r.grade) : '—'}</td><td>${r.scanned ? scoreMeter(r.score) : '<span class="muted small">non analysé</span>'}</td>
      ${r.categories.map(c => `<td class="num">${c.score == null ? '<span class="muted">—</span>' : `<span class="pill ${c.score >= 80 ? 'on' : c.score >= 60 ? 'warn' : 'err'}">${c.score}</span>`}</td>`).join('')}
      <td class="small" style="max-width:300px">${r.top[0] ? `${sev(r.top[0].severity)} ${esc(r.top[0].title)}` : ''}</td></tr>`).join('') || `<tr><td colspan="9">${emptyState('folder', 'Aucun dossier supervisé')}</td></tr>`}
    </tbody></table></div></div>`;
  rowLinks(view);
}

async function shareArchitecture(pane, s) {
  const a = await api(`/api/shares/${s.id}/architecture`);
  if (!a.scanned) { pane.innerHTML = `<div class="card">${emptyState('report', 'Aucune analyse', 'Lancez une analyse du dossier pour évaluer son architecture.')}</div>`; return; }
  const m = a.metrics; const r = a.rules;
  const reco = a.recommendations;
  pane.innerHTML = `
    <div class="tiles">
      <div class="tile hero-tile"><div class="chip">${icon('report')}</div><div class="body"><div class="label">Note d'architecture</div><div class="value">${a.grade} · ${a.score}/100</div><div class="sub">${reco.length} recommandation(s)</div></div></div>
      ${tile({ label: `Chemins > ${r.crit_len} car.`, value: fmtNum(m.over_crit), ic: 'alert', tone: m.over_crit ? 'critical' : 'good', sub: `${fmtNum(m.over_warn)} > ${r.warn_len} · ${fmtNum(m.over_cloud)} > ${r.cloud_len} (SharePoint)` })}
      ${tile({ label: 'Profondeur maximale', value: m.max_depth + ' niveaux', ic: 'folder', tone: m.max_depth > r.max_depth ? 'warning' : 'good', sub: `${fmtNum(m.deep_items)} élément(s) au-delà de ${r.max_depth}` })}
      ${tile({ label: 'Noms non conformes', value: fmtNum(m.naming_issues), ic: 'file', tone: m.naming_issues ? 'warning' : 'good' })}
      ${tile({ label: 'Fichiers à la racine', value: fmtNum(m.root_files), ic: 'list', tone: m.root_files > 20 ? 'warning' : 'good', sub: `${m.level1} dossier(s) au 1er niveau` })}
    </div>
    <div class="grid span-1-2">
      <div class="card">${cardHead('Notes par catégorie', 'Sur 100 — pondérées dans la note globale')}
        ${hbars(a.categories.filter(c => c.score != null).map(c => ({ name: c.label, value: c.score, w: c.weight })), { fmt: v => v + ' / 100', sub: i => `poids ${i.w} %` })}
        ${a.categories.some(c => c.score == null) ? '<p class="small muted" style="margin-top:10px">Droits non analysés : lancez l\'analyse des droits pour compléter la note.</p>' : ''}</div>
      <div class="card">${cardHead('Recommandations', 'Par ordre de priorité')}
        ${reco.length ? reco.map(x => `<div class="reco"><div>${sev(x.severity)}</div><div><div class="strong">${esc(x.title)}</div><div class="small ink-2">${esc(x.detail)}</div>
          ${x.link ? `<a class="small" href="#/share/${s.id}/${x.link === 'matrix' ? 'rights' : x.link}">${esc(x.action)} →</a>` : ''}</div></div>`).join('') : emptyState('check', 'Architecture saine', '', '', 'good')}</div>
    </div>
    <div class="grid cols-2">
      <div class="card">${cardHead('Fichiers par niveau de profondeur', 'Niveau 0 = racine du partage')}<div id="depth-chart"></div></div>
      <div class="card flush">${cardHead('Chemins les plus longs', `Longueur vue par les utilisateurs (préfixe : ${esc(a.prefix || '')})`, `<a class="btn sm" href="/api/shares/${s.id}/paths.csv">${icon('download')} CSV</a>`)}
        ${a.longest.length ? `<div class="table-wrap tall"><table><tbody>${a.longest.map(l => `<tr><td class="num"><span class="pill ${l.length > r.crit_len ? 'err' : 'warn'}">${l.length}</span></td><td class="path">${esc(l.path)}</td><td class="small muted">${esc(l.kind)}</td></tr>`).join('')}</tbody></table></div>`
          : emptyState('check', `Aucun chemin au-delà de ${r.warn_len} caractères`, '', '', 'good')}</div>
    </div>
    <div class="card flush">${cardHead('Noms à corriger', 'Incompatibles avec Windows ou avec une migration OneDrive / SharePoint')}
      ${a.naming.samples.length ? `<div class="table-wrap tall"><table><thead><tr><th>Problème</th><th>Élément</th><th>Nature</th></tr></thead><tbody>${a.naming.samples.map(n => `<tr><td><span class="pill warn">${esc(NAMING_LABEL[n.code] || n.code)}</span></td><td class="path">${esc(n.path)}</td><td class="small">${esc(n.kind)}</td></tr>`).join('')}</tbody></table></div>`
        : emptyState('check', 'Tous les noms sont conformes', '', '', 'good')}</div>
    <div id="rs-panel"></div>
    <div class="card flush">${cardHead('Arborescence annotée', `Niveaux 0 à ${r.arch_depth} · taille, fichiers, chemin le plus long, héritage`)}
      <div class="table-wrap tall"><table><thead><tr><th>Dossier</th><th class="num">Taille</th><th style="width:140px"></th><th class="num">Fichiers</th><th class="num">Sous-dossiers</th><th class="num">Chemin max</th><th>Droits</th><th>Dernière modif.</th></tr></thead><tbody>
      ${(() => { const max = Math.max(1, ...a.tree.map(t => t.size)); return a.tree.map(t => `<tr><td class="nowrap" style="padding-left:${12 + t.depth * 18}px">${icon('folder', 'file-icon dir')}${esc(t.rel_path ? t.rel_path.split('/').pop() : '(racine)')}</td>
        <td class="num">${fmtBytes(t.size)}</td><td><div class="progress"><div style="width:${(t.size * 100 / max).toFixed(1)}%"></div></div></td><td class="num">${fmtNum(t.files)}</td><td class="num">${fmtNum(t.subdirs)}</td>
        <td class="num">${t.long ? `<span class="pill ${t.max_path_len > r.crit_len ? 'err' : 'warn'}">${t.max_path_len}</span>` : t.max_path_len}</td>
        <td class="nowrap">${t.protected ? '🔒 ' : ''}${t.explicit ? `<span class="small muted">${t.explicit} expl.</span>` : ''}${t.acl_issues ? ` <span class="sev warning">${t.acl_issues}</span>` : ''}</td>
        <td class="nowrap small">${fmtDay(t.newest_mtime)}</td></tr>`).join(''); })()}</tbody></table></div></div>`;
  // Niveaux sans fichier affichés à 0 pour garder une échelle continue.
  const dfMax = Math.max(-1, ...Object.keys(m.depth_files || {}).map(Number));
  const df = Array.from({ length: dfMax + 1 }, (_, k) => { const v = (m.depth_files || {})[k] || 0; return { label: String(k), value: v, tip: `Niveau ${k}<br><b>${fmtNum(v)}</b> fichier(s)` }; });
  if (df.length) columnChart($('#depth-chart', pane), df, { height: 200 }); else $('#depth-chart', pane).innerHTML = '<div class="empty">Aucune donnée</div>';
  restructurePanel($('#rs-panel', pane), s).catch(e => { $('#rs-panel', pane).innerHTML = `<div class="banner critical">${esc(e.message)}</div>`; });
}

// ------------------------------------------------------------------ quotas
const QUOTA_LEVEL = { ok: ['ok', 'Normal'], warning: ['warning', 'Avertissement'], critical: ['critical', 'Critique'], exceeded: ['critical', 'Dépassé'], unknown: ['neutral', 'Non contrôlé'] };
const ENFORCE_LABEL = { fsrm: 'FSRM (Windows)', vigiefs: 'Verrouillage VigieFS', none: 'Alerte seulement' };

async function viewQuotas(view, query) {
  setPage('Quotas', 'Droits & conformité', can('operator') ? `<button class="btn primary" data-new>${icon('plus')} Nouveau quota</button>` : '',
    'Quotas par dossier ou sous-dossier, souples (alerte) ou stricts (blocage des écritures), modèles et quotas automatiques par sous-dossier.');
  await quotasPanel(view, null);
}
async function shareQuotas(pane, s) { await quotasPanel(pane, s); }

async function quotasPanel(root, share) {
  const shares = share ? [share] : await api('/api/shares');
  const render = async () => {
    const d = await api('/api/quotas' + (share ? `?share_id=${share.id}` : ''));
    const q = d.quotas;
    const locked = q.filter(x => x.locks.length).length;
    root.innerHTML = `
      <div class="banner info" style="margin-bottom:16px">${icon('info')}<span>${d.fsrm ? 'FSRM est disponible : les quotas stricts sont appliqués par Windows (blocage immédiat).' : `Quotas stricts appliqués par <b>verrouillage VigieFS</b> : dès le dépassement, l'écriture est refusée aux comptes non administrateurs (${d.platform === 'windows' ? 'ils peuvent encore supprimer pour libérer de la place' : 'masque ACL en lecture seule'}), puis rétablie automatiquement sous ${d.settings.unlock_below_pct} %. Contrôle toutes les ${d.settings.check_interval_min} min.`}</span></div>
      ${share ? `<div class="filters" style="margin-bottom:16px">${can('operator') ? `<button class="btn primary" data-act="new">${icon('plus')} Nouveau quota</button>` : ''}<span class="small muted">Le quota global du dossier (Modifier › Quota) reste un quota souple d'alerte.</span></div>` : ''}
      <div class="tiles" style="margin-bottom:16px">
        ${tile({ label: 'Quotas définis', value: fmtNum(q.length), ic: 'disk', sub: `${q.filter(x => x.mode === 'hard').length} strict(s)` })}
        ${tile({ label: 'Dépassés', value: fmtNum(q.filter(x => (x.state || {}).level === 'exceeded').length), ic: 'alert', tone: 'critical' })}
        ${tile({ label: 'Au-delà du seuil', value: fmtNum(q.filter(x => ['warning', 'critical'].includes((x.state || {}).level)).length), ic: 'bell', tone: 'warning' })}
        ${tile({ label: 'Dossiers verrouillés', value: fmtNum(locked), ic: 'lock', tone: locked ? 'critical' : 'good' })}
      </div>
      <div class="card flush">${cardHead('Quotas')}<div class="table-wrap"><table><thead><tr><th>Dossier</th><th class="num">Limite</th><th>Type</th><th style="width:200px">Occupation</th><th>État</th><th>Contrôle</th><th></th></tr></thead><tbody>
      ${q.length ? q.map(x => { const st = x.state || {}; const items = st.items || []; return `<tr>
        <td><div class="cell-title">${esc(x.share_name)} / ${esc(x.rel_path || '(racine)')}</div><div class="cell-sub">${[x.auto_apply ? 'Quota automatique : chaque sous-dossier' : '', x.template ? `modèle « ${esc(x.template)} »` : '', x.notes ? esc(x.notes) : ''].filter(Boolean).join(' · ')}</div></td>
        <td class="num">${fmtBytes(x.limit_bytes)}<div class="cell-sub">${x.warn_pct} / ${x.crit_pct} %</div></td>
        <td>${x.mode === 'hard' ? '<span class="sev critical">Strict</span>' : '<span class="sev info">Souple</span>'}<div class="cell-sub">${esc(ENFORCE_LABEL[x.effective_enforcement] || x.effective_enforcement)}</div></td>
        <td>${st.pct != null ? meter(st.pct, ['critical', 'exceeded'].includes(st.level) ? 'critical' : st.level === 'warning' ? 'warning' : '') + `<div class="cell-sub">${fmtBytes(st.usage)}${x.auto_apply ? ` (le plus rempli sur ${items.length})` : ''}</div>` : '<span class="muted small">—</span>'}</td>
        <td>${badge(QUOTA_LEVEL, st.level || 'unknown')}${x.locks.length ? `<div class="cell-sub" style="color:var(--critical-ink)">🔒 ${x.locks.length} verrou(s)</div>` : ''}</td>
        <td class="small">${fmtAgo(st.checked_at)}</td>
        <td class="nowrap">${can('operator') ? `<button class="btn sm" data-act="check" data-id="${x.id}">Vérifier</button> <button class="btn sm ghost" data-act="edit" data-id="${x.id}">Modifier</button>` : ''}
          ${x.locks.length && can('admin') ? ` <button class="btn sm" data-act="unlock" data-id="${x.id}">Déverrouiller</button>` : ''}
          ${x.effective_enforcement === 'fsrm' || d.platform === 'windows' ? ` <button class="btn sm ghost" data-act="script" data-id="${x.id}">FSRM</button>` : ''}
          ${can('operator') ? ` <button class="btn sm ghost danger" data-act="del" data-id="${x.id}">${icon('trash')}</button>` : ''}</td></tr>
        ${x.auto_apply && items.length ? items.map(i => `<tr class="sub-row"><td class="path" style="padding-left:36px">↳ ${esc(i.path)}</td><td></td><td></td><td>${i.pct != null ? meter(i.pct, ['critical', 'exceeded'].includes(i.level) ? 'critical' : i.level === 'warning' ? 'warning' : '') : ''}</td><td>${badge(QUOTA_LEVEL, i.level)}${i.locked ? ' 🔒' : ''}</td><td colspan="2" class="small muted">${fmtBytes(i.usage)}</td></tr>`).join('') : ''}`; }).join('')
        : `<tr><td colspan="7">${emptyState('disk', 'Aucun quota avancé', 'Créez des quotas sur des sous-dossiers (services, dossiers personnels) avec blocage des écritures si besoin.')}</td></tr>`}
      </tbody></table></div></div>`;
    root._d = d;
  };
  const form = (x = {}, d) => {
    const tpl = d.settings.templates || [];
    return `<div class="form-grid">
      ${share ? `<input type="hidden" name="share_id" value="${share.id}">` : field('Dossier supervisé', sel('share_id', shares.map(s => [s.id, `${s.client_name || ''} › ${s.name}`]), x.share_id))}
      ${field('Sous-dossier (chemin relatif)', inp('rel_path', x.rel_path || '', 'text', 'placeholder="Vide = racine · ex. Utilisateurs ou Projets/2024"'), '', !share)}
      ${field('Modèle', sel('template', [['', '— Personnalisé —'], ...tpl.map(t => [t.name, `${t.name} (${t.mode === 'hard' ? 'strict' : 'souple'})`])], x.template || ''), 'Remplit la limite, le type et les seuils.', true)}
      ${field('Limite (Go)', inp('limit_gb', x.limit_bytes ? +(x.limit_bytes / GB).toFixed(3) : '', 'number', 'min="0.001" step="0.1" required'))}
      ${field('Type', seg('mode', [['soft', 'Souple (alerte)'], ['hard', 'Strict (blocage)']], x.mode || 'hard'))}
      ${field('Seuil d\'avertissement (%)', inp('warn_pct', x.warn_pct || 85, 'number', 'min="1" max="100"'))}
      ${field('Seuil critique (%)', inp('crit_pct', x.crit_pct || 95, 'number', 'min="1" max="100"'))}
      ${field('Application du blocage', sel('enforcement', [['auto', 'Automatique (FSRM si disponible, sinon VigieFS)'], ['fsrm', 'FSRM (Windows)'], ['vigiefs', 'Verrouillage VigieFS'], ['none', 'Aucune (alerte seulement)']], x.enforcement || 'auto'), '', true)}
      <div class="field full">${chk('auto_apply', 'Quota automatique : appliquer la limite à CHAQUE sous-dossier direct (dossiers personnels, projets…)', x.auto_apply)}</div>
      ${field('Notes', inp('notes', x.notes || ''), '', true)}</div>`;
  };
  const body = f => { const d = formData(f); return { share_id: Number(d.share_id), rel_path: d.rel_path || '', limit_bytes: Math.round((d.limit_gb || 0) * GB), warn_pct: d.warn_pct, crit_pct: d.crit_pct, mode: d.mode || 'hard', enforcement: d.enforcement, auto_apply: !!d.auto_apply, template: d.template, notes: d.notes || '' }; };
  const open = (x) => modal({ title: x ? 'Modifier le quota' : 'Nouveau quota', body: form(x || {}, root._d), submit: x ? 'Enregistrer' : 'Créer',
    onOpen: f => $('[name=template]', f).addEventListener('change', e => { const t = (root._d.settings.templates || []).find(t => t.name === e.target.value); if (!t) return; $('[name=limit_gb]', f).value = t.limit_gb; $('[name=warn_pct]', f).value = t.warn_pct; $('[name=crit_pct]', f).value = t.crit_pct; $$('[name=mode]', f).forEach(r => { r.checked = r.value === t.mode; }); }),
    onSubmit: async f => { const r = await api(x ? `/api/quotas/${x.id}` : '/api/quotas', { method: x ? 'PUT' : 'POST', body: body(f) }); const st = r.state || {}; toast(st.error ? `Quota enregistré, FSRM : ${st.error}` : `Quota enregistré — ${QUOTA_LEVEL[st.level] ? QUOTA_LEVEL[st.level][1] : ''}${st.locked ? ' — dossier verrouillé' : ''}`, st.error ? 'error' : 'ok'); render(); } });
  bindActions(root, {
    new: () => open(null),
    edit: el => open(root._d.quotas.find(x => x.id == el.dataset.id)),
    check: async el => { const st = await api(`/api/quotas/${el.dataset.id}/check`, { method: 'POST' }); toast(`Contrôle : ${QUOTA_LEVEL[st.level][1]}${st.locked ? ' — verrouillé' : ''}`); render(); },
    unlock: el => confirmBox('Déverrouiller', 'Rétablir l\'écriture immédiatement, même si le quota est toujours dépassé ? (le prochain contrôle reverrouillera si nécessaire)', async () => { await api(`/api/quotas/${el.dataset.id}/unlock`, { method: 'POST' }); toast('Dossier déverrouillé'); render(); }, 'Déverrouiller', false),
    script: async el => { const t = await (await fetch(`/api/quotas/${el.dataset.id}/fsrm-script`, { headers: { 'X-VigieFS': '1' } })).text(); modal({ title: 'Commandes FSRM équivalentes', size: 'lg', noFooter: true, body: `<p class="small muted">À exécuter dans PowerShell administrateur sur le serveur de fichiers (rôle FSRM installé).</p><div class="details-box" style="max-height:420px">${esc(t)}</div>` }); },
    del: el => confirmBox('Supprimer le quota', 'Le quota (et son équivalent FSRM) sera supprimé et les verrous éventuels levés.', async () => { await api(`/api/quotas/${el.dataset.id}`, { method: 'DELETE' }); toast('Quota supprimé'); render(); }, 'Supprimer'),
  });
  const nb = $('[data-new]'); if (nb && !share) nb.onclick = () => open(null);
  await render();
  every(30000, guard(render));
}

// ------------------------------------------------------------------ partages & fichiers ouverts
async function viewSmb(view) {
  setPage('Partages & fichiers ouverts', 'Droits & conformité', `<button class="btn" data-refresh>${icon('refresh')} Actualiser</button>`, 'Partages SMB du serveur, fichiers ouverts (verrouillés) et sessions des utilisateurs.');
  const render = async () => {
    const [sh, op] = await Promise.all([api('/api/smb/shares'), api('/api/smb/open-files')]);
    const files = op.files; const sess = op.sessions;
    const q = ($('#of-q', view) || {}).value || '';
    view.innerHTML = `
      ${!sh.available ? `<div class="banner warning" style="margin-bottom:16px">${icon('info')}<span>Partages indisponibles : ${esc(sh.error || 'cmdlets SmbShare (Windows) ou Samba requis')}.</span></div>` : ''}
      <div class="tiles" style="margin-bottom:16px">
        ${tile({ label: 'Partages', value: fmtNum(sh.items.length), ic: 'folder', sub: `${sh.items.filter(s => s.abe).length} avec énumération basée sur l'accès` })}
        ${tile({ label: 'Fichiers ouverts', value: files.available ? fmtNum(files.items.length) : '—', ic: 'file', tone: 'violet' })}
        ${tile({ label: 'Sessions', value: sess.available ? fmtNum(sess.items.length) : '—', ic: 'clients', tone: 'aqua' })}
      </div>
      <div class="card flush">${cardHead('Fichiers ouverts', 'Fermez un fichier resté verrouillé par un utilisateur (« le fichier est utilisé par… »).', `<input type="search" id="of-q" placeholder="Filtrer (fichier, utilisateur)…" value="${esc(q)}" style="width:260px">`)}
        ${files.available ? (files.items.length ? `<div class="table-wrap tall"><table><thead><tr><th>Fichier</th><th>Utilisateur</th><th>Poste</th><th class="num">Verrous</th><th></th></tr></thead><tbody>
          ${files.items.map(f => `<tr data-search="${esc((f.path + ' ' + f.user + ' ' + f.client).toLowerCase())}"><td class="path">${fileIcon(f.path || '')}${esc(f.path)}</td><td>${esc(f.user || '')}</td><td>${esc(f.client || '')}</td><td class="num">${f.locks || ''}</td>
            <td>${can('operator') ? `<button class="btn sm" data-act="closefile" data-id="${esc(f.id)}" data-path="${esc(f.path)}">Fermer</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : emptyState('check', 'Aucun fichier ouvert', '', '', 'good'))
          : `<div class="empty small">${esc(files.error || 'Non disponible')}</div>`}</div>
      <div class="card flush">${cardHead('Sessions SMB')}
        ${sess.available ? (sess.items.length ? `<div class="table-wrap"><table><thead><tr><th>Utilisateur</th><th>Poste</th><th class="num">Fichiers</th><th>Connecté depuis</th><th>Inactif</th><th>Protocole</th><th></th></tr></thead><tbody>
          ${sess.items.map(x => `<tr><td class="strong">${esc(x.user || '')}</td><td>${esc(x.client || '')}</td><td class="num">${x.opens ?? ''}</td><td>${x.duration != null ? fmtDuration(x.duration) : ''}</td><td>${x.idle != null ? fmtDuration(x.idle) : ''}</td><td class="small">${esc(x.dialect || '')}</td>
            <td>${can('admin') ? `<button class="btn sm ghost danger" data-act="closesess" data-id="${esc(x.id)}" data-user="${esc(x.user || '')}">Déconnecter</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty small">Aucune session.</div>')
          : `<div class="empty small">${esc(sess.error || 'Non disponible')}</div>`}</div>
      <div class="card flush">${cardHead('Partages', 'Bonne pratique : droits de partage larges, contrôle fin par les droits NTFS, énumération basée sur l\'accès activée.')}
        ${sh.items.length ? `<div class="table-wrap"><table><thead><tr><th>Partage</th><th>Énumération (ABE)</th><th>Chiffrement</th><th>Droits de partage</th><th>Supervision</th><th>Remarques</th></tr></thead><tbody>
          ${sh.items.map(x => `<tr><td><div class="cell-title">${esc(x.name)}</div><div class="cell-sub mono">${esc(x.path)}</div></td>
            <td>${x.abe ? '<span class="sev ok">Activée</span>' : '<span class="sev neutral">Désactivée</span>'}${can('admin') ? ` <button class="btn sm ghost" data-act="abe" data-name="${esc(x.name)}" data-on="${x.abe ? 0 : 1}">${x.abe ? 'Désactiver' : 'Activer'}</button>` : ''}</td>
            <td>${x.encrypt ? '<span class="sev ok">Oui</span>' : '<span class="sev neutral">Non</span>'}</td>
            <td class="small">${(x.access || []).map(a => `${esc(a.account)} : ${esc(a.right)}${a.type && a.type !== 'Allow' ? ' (refus)' : ''}`).join('<br>')}</td>
            <td>${x.supervised.length ? x.supervised.map(s => `<a href="#/share/${s.id}">${esc(s.name)}</a>`).join(', ') : '<span class="muted small">non supervisé</span>'}</td>
            <td class="small">${x.findings.map(f => esc(f.title)).join('<br>')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty small">Aucun partage détecté.</div>'}</div>`;
    const of = $('#of-q', view);
    of.addEventListener('input', () => { const v = of.value.toLowerCase(); $$('tr[data-search]', view).forEach(tr => { tr.hidden = !tr.dataset.search.includes(v); }); });
    if (q) of.dispatchEvent(new Event('input'));
  };
  bindActions(view, {
    closefile: el => confirmBox('Fermer le fichier', `Fermer <span class="mono">${esc(el.dataset.path)}</span> ? Les modifications non enregistrées de l'utilisateur seront perdues.`, async () => { await api(`/api/smb/open-files/${encodeURIComponent(el.dataset.id)}/close`, { method: 'POST' }); toast('Fichier fermé'); render(); }, 'Fermer'),
    closesess: el => confirmBox('Déconnecter la session', `Déconnecter <b>${esc(el.dataset.user)}</b> ? Tous ses fichiers ouverts seront fermés.`, async () => { await api(`/api/smb/sessions/${encodeURIComponent(el.dataset.id)}/close`, { method: 'POST' }); toast('Session fermée'); render(); }, 'Déconnecter'),
    abe: async el => { await api(`/api/smb/shares/${encodeURIComponent(el.dataset.name)}/abe`, { method: 'POST', body: { enabled: el.dataset.on === '1' } }); toast('Partage mis à jour'); render(); },
  });
  $('[data-refresh]').onclick = guard(render);
  await render();
  every(15000, guard(render));
}

// ------------------------------------------------------------------ santé du serveur
const CHECK_STATUS = { ok: ['ok', 'Conforme'], info: ['info', 'Conseil'], warning: ['warning', 'À corriger'], critical: ['critical', 'Critique'], unknown: ['neutral', 'Inconnu'] };
async function viewHealth(view) {
  setPage('Santé du serveur', 'Droits & conformité', `<button class="btn" data-refresh>${icon('refresh')} Relancer les contrôles</button>`, 'Sécurité SMB, chemins longs, clichés instantanés, audit, quotas, sauvegarde et volumes.');
  const render = async (refresh = false) => {
    view.innerHTML = '<div class="banner info"><span class="dot run"></span>Contrôles en cours…</div>';
    const h = await api('/api/health/server' + (refresh ? '?refresh=true' : ''));
    const groups = {};
    h.checks.forEach(c => { (groups[c.group] = groups[c.group] || []).push(c); });
    const c = h.counts;
    view.innerHTML = `<div class="tiles">
        ${tile({ label: 'Conformes', value: fmtNum(c.ok), ic: 'check', tone: 'good' })}
        ${tile({ label: 'Critiques', value: fmtNum(c.critical), ic: 'alert', tone: c.critical ? 'critical' : 'good' })}
        ${tile({ label: 'À corriger', value: fmtNum(c.warning), ic: 'bell', tone: c.warning ? 'warning' : 'good' })}
        ${tile({ label: 'Conseils', value: fmtNum(c.info), ic: 'info', sub: `${h.platform === 'windows' ? 'Windows Server' : 'Linux / Samba'} · ${fmtDate(h.checked_at)}` })}
      </div>
      <div class="grid cols-2">${Object.entries(groups).map(([g, list]) => `<div class="card flush">${cardHead(esc(g))}<table><tbody>
        ${list.map(x => `<tr><td style="width:1%">${badge(CHECK_STATUS, x.status)}</td><td><div class="strong">${esc(x.label)}</div><div class="cell-sub">${esc(x.value)}</div>
          ${x.status !== 'ok' && x.recommendation ? `<div class="small ink-2" style="margin-top:4px">${esc(x.recommendation)}</div>` : ''}
          ${x.status !== 'ok' && x.fix ? `<div class="details-box" style="margin-top:6px;max-height:none">${esc(x.fix)}</div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>`).join('')}</div>`;
  };
  $('[data-refresh]').onclick = guard(() => render(true));
  await render();
}

// ------------------------------------------------------------------ données sensibles (RGPD)
const RISK = { high: ['critical', 'Élevé'], medium: ['warning', 'Moyen'], low: ['info', 'Faible'] };
async function viewSensitive(view, query) {
  setPage('Données sensibles (RGPD)', 'Droits & conformité', '', 'Repérage des NIR, IBAN, cartes bancaires, identifiants en clair et données de santé, croisé avec les droits d\'accès.');
  const ov = await api('/api/sensitive');
  const sid = query.get('share') || (ov.shares[0] && ov.shares[0].id);
  view.innerHTML = `
    <div class="card flush">${cardHead('Vue d\'ensemble', 'Dernière recherche par dossier supervisé')}<div class="table-wrap"><table><thead><tr><th>Dossier</th><th>Recherche</th><th class="num">Fichiers lus</th><th class="num">Signalés</th><th class="num">Risque élevé</th><th class="num">Exposés à tous</th><th></th></tr></thead><tbody>
    ${ov.shares.map(s => `<tr class="clickable ${String(s.id) === String(sid) ? 'selected' : ''}" data-go="#/sensitive?share=${s.id}"><td class="cell-title">${esc(s.name)}</td><td>${s.scanned_at ? fmtAgo(s.scanned_at) : '<span class="muted">jamais</span>'}</td>
      <td class="num">${fmtNum(s.scanned)}</td><td class="num">${fmtNum(s.flagged)}</td><td class="num">${s.high ? `<span class="sev critical">${s.high}</span>` : fmtNum(s.high)}</td><td class="num">${s.exposed_high ? `<span class="sev critical">${s.exposed_high}</span>` : fmtNum(s.exposed_high)}</td>
      <td>${can('operator') ? `<button class="btn sm" data-act="run" data-id="${s.id}">${icon('search')} Rechercher</button>` : ''}</td></tr>`).join('') || `<tr><td colspan="7">${emptyState('folder', 'Aucun dossier')}</td></tr>`}
    </tbody></table></div></div><div id="detail"></div>`;
  rowLinks(view);
  const detail = async () => {
    if (!sid) return;
    const d = await api(`/api/shares/${sid}/sensitive`);
    const box = $('#detail', view);
    if (d.running) {
      const job = await watchJob(d.running.id, j => { box.innerHTML = `<div style="margin-top:16px">${jobLine(j, 'Recherche de données sensibles')}</div>`; });
      if (job) detail();
      return;
    }
    if (!d.last) { box.innerHTML = `<div class="card">${emptyState('shield', 'Aucune recherche', 'Lancez une recherche : les documents texte, Word, Excel et PowerPoint sont lus et les numéros sont validés (clé NIR, IBAN, Luhn).')}</div>`; return; }
    const r = d.last.result; const labels = r.labels;
    box.innerHTML = `
      ${r.exposed_high ? `<div class="banner critical" style="margin-top:16px">${icon('alert')}<span><b>${r.exposed_high} fichier(s) sensibles sont lisibles par tout le monde.</b> Restreignez les droits (matrice) ou déplacez ces fichiers.</span></div>` : ''}
      ${!r.acl_snapshot ? `<div class="banner info" style="margin-top:16px">${icon('info')}Droits non analysés : l'exposition n'a pas pu être évaluée.</div>` : ''}
      <div class="tiles" style="margin-top:16px">${Object.entries(r.totals).map(([k, v]) => tile({ label: labels[k], value: fmtNum(v.files) + ' fichier(s)', ic: 'shield', tone: ['nir', 'card', 'credentials', 'health'].includes(k) ? 'critical' : 'warning', sub: `${fmtNum(v.hits)} occurrence(s)` })).join('') || tile({ label: 'Aucune donnée sensible', value: '0', ic: 'check', tone: 'good' })}</div>
      <div class="card flush">${cardHead('Fichiers signalés', `${fmtNum(r.flagged)} sur ${fmtNum(r.scanned)} fichiers lus · ${fmtDate(d.last.finished_at)} · valeurs masquées`)}
        ${r.results.length ? `<div class="table-wrap tall"><table><thead><tr><th>Risque</th><th>Fichier</th><th>Données détectées</th><th>Exposition</th><th>Modifié</th></tr></thead><tbody>
        ${r.results.map(x => `<tr><td>${badge(RISK, x.level)}</td><td class="path">${fileIcon(x.path)}${esc(x.path)}</td>
          <td>${Object.entries(x.found).map(([k, n]) => `<span class="pill ${['nir', 'card', 'credentials', 'health'].includes(k) ? 'err' : 'warn'}" data-tip="${esc(esc((x.samples[k] || []).join(' · ')))}">${esc(labels[k])} × ${n}</span>`).join(' ')}</td>
          <td>${x.exposed_to.length ? `<span class="sev critical">${x.exposed_to.map(p => esc(pName(p))).join(', ')}</span>` : '<span class="small muted">restreint</span>'}</td><td class="nowrap small">${fmtDay(x.mtime)}</td></tr>`).join('')}</tbody></table></div>`
          : emptyState('check', 'Aucune donnée sensible détectée', '', '', 'good')}</div>`;
  };
  bindActions(view, { run: async el => { await api(`/api/shares/${el.dataset.id}/sensitive`, { method: 'POST' }); toast('Recherche lancée'); location.hash = `#/sensitive?share=${el.dataset.id}`; if (String(el.dataset.id) === String(sid)) detail(); } });
  await detail();
}
