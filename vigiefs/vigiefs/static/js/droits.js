/* VigieFS — droits d'accès : vue globale, onglet Droits d'un dossier, éditeur de matrice */
'use strict';

const LEVELS = ['none', 'list', 'read', 'write', 'modify', 'full'];
const LEVEL_LABEL = { none: 'Aucun', list: 'Parcours / liste', read: 'Lecture', write: 'Écriture', modify: 'Modification', full: 'Contrôle total' };
const LEVEL_SHORT = { none: '—', list: 'L', read: 'R', write: 'W', modify: 'M', full: 'CT' };
const LEVEL_HELP = { list: 'Voir le dossier et le traverser (ce dossier seulement)', read: 'Lire et ouvrir les fichiers', write: 'Créer et modifier sans supprimer', modify: 'Lire, créer, modifier et supprimer', full: 'Tout, y compris modifier les droits' };
const PTYPE_ICON = { user: '👤', group: '👥', wellknown: '⚙', orphan: '⚠', unknown: '?' };
const SEV_ORDER = { critical: 0, warning: 1, info: 2 };
const lvBadge = lv => `<span class="lv lv-${lv}" title="${esc(LEVEL_LABEL[lv] || lv)}">${esc(LEVEL_SHORT[lv] || lv)}</span>`;
const pName = p => (p === 'other' ? 'Autres (tout le monde)' : p.startsWith('user:') ? p.slice(5) : p.startsWith('group:') ? p.slice(6) : p);
const scoreTone = s => (s == null ? '' : s >= 80 ? 'good' : s >= 60 ? 'warning' : 'critical');

function principalPicker(name, principals, value = '', placeholder = 'DOMAINE\\Groupe ou group:nom') {
  const id = 'dl-' + Math.random().toString(36).slice(2);
  return `<input type="text" name="${name}" value="${esc(value)}" list="${id}" placeholder="${esc(placeholder)}" autocomplete="off" required>
    <datalist id="${id}">${principals.map(p => `<option value="${esc(p.name)}">${esc((PTYPE_ICON[p.type] || '') + ' ' + (p.label || pName(p.name)))}</option>`).join('')}</datalist>`;
}

// ------------------------------------------------------------------ vue globale
async function viewRights(view) {
  setPage("Droits d'accès", 'Droits & conformité', '', 'Audit des droits NTFS / POSIX, anomalies, matrices et accès effectifs par identité.');
  const [ov, principals] = await Promise.all([api('/api/acl/overview'), api('/api/acl/principals')]);
  const done = ov.filter(s => s.summary);
  const sum = k => done.reduce((a, s) => a + ((s.summary.by_code || {})[k] || 0), 0);
  const crit = done.reduce((a, s) => a + ((s.summary.by_severity || {}).critical || 0), 0);
  const avg = done.length ? Math.round(done.reduce((a, s) => a + s.summary.score, 0) / done.length) : null;
  view.innerHTML = `
    <div class="tiles">
      ${tile({ label: 'Score moyen des droits', value: avg == null ? '—' : avg + ' / 100', ic: 'shield', hero: true, sub: `${done.length} / ${ov.length} dossier(s) analysé(s)` })}
      ${tile({ label: 'Problèmes critiques', value: fmtNum(crit), ic: 'alert', tone: crit ? 'critical' : 'good', sub: 'accès ouverts en écriture, SYSTÈME absent…' })}
      ${tile({ label: 'Accès ouverts à tous', value: fmtNum(sum('open_write') + sum('open_read')), ic: 'eye', tone: sum('open_write') ? 'critical' : 'warning', sub: `${sum('open_write')} en écriture` })}
      ${tile({ label: 'Droits nominatifs', value: fmtNum(sum('direct_user')), ic: 'clients', tone: 'warning', sub: 'à remplacer par des groupes' })}
      ${tile({ label: 'SID orphelins', value: fmtNum(sum('orphan')), ic: 'lock', tone: 'violet', sub: 'comptes supprimés' })}
    </div>
    <div class="card flush">${cardHead('Dossiers supervisés', 'Dernière analyse des droits et matrice associée')}
      <div class="table-wrap"><table><thead><tr><th>Dossier</th><th>Analyse</th><th style="width:180px">Score</th><th>Anomalies</th><th class="num">Listes d'accès</th><th>Matrice</th><th></th></tr></thead><tbody>
      ${ov.map(s => { const sm = s.summary; const bs = (sm && sm.by_severity) || {}; return `<tr class="clickable" data-go="#/share/${s.id}/rights">
        <td><div class="cell-title">${esc(s.name)}</div><div class="cell-sub">${esc(s.client_name)}</div></td>
        <td>${s.scanned_at ? fmtAgo(s.scanned_at) : '<span class="muted">jamais</span>'}</td>
        <td>${sm ? scoreMeter(sm.score) : '—'}</td>
        <td>${sm ? `${bs.critical ? `<span class="sev critical">${bs.critical} critique(s)</span> ` : ''}${bs.warning ? `<span class="sev warning">${bs.warning}</span> ` : ''}${!bs.critical && !bs.warning ? sev('ok') : ''}` : ''}</td>
        <td class="num">${sm ? fmtNum(sm.unique_acls) : ''}</td>
        <td>${s.matrix ? `<a href="#/matrix/${s.matrix.id}">${esc(s.matrix.name)}</a> ${badge(MATRIX_STATUS, s.matrix.status)}` : '<span class="muted small">aucune</span>'}</td>
        <td class="nowrap">${can('operator') ? `<button class="btn sm" data-act="scan" data-id="${s.id}">${icon('refresh')} Analyser</button>` : ''}</td></tr>`; }).join('') || `<tr><td colspan="7">${emptyState('folder', 'Aucun dossier supervisé')}</td></tr>`}
      </tbody></table></div></div>
    <form class="card" id="who">${cardHead('Qui a accès à quoi ?', 'Accès effectifs d\'un utilisateur ou d\'un groupe sur tous les dossiers supervisés (appartenance aux groupes résolue lorsque l\'annuaire est accessible).')}
      <div class="filters">${principalPicker('principal', principals, '', 'Utilisateur ou groupe (ex. CORP\\jdupont, user:alice)')}<button class="btn primary" type="submit">${icon('search')} Rechercher</button></div>
      <div id="who-res"></div></form>`;
  rowLinks(view);
  bindActions(view, { scan: async el => { await api(`/api/shares/${el.dataset.id}/acl/scan`, { method: 'POST' }); toast('Analyse des droits lancée'); setTimeout(route, 1500); } });
  $('#who', view).addEventListener('submit', guard(async e => {
    e.preventDefault();
    const p = formData(e.target).principal.trim(); if (!p) return;
    $('#who-res', view).innerHTML = '<div class="muted small">Recherche…</div>';
    const r = await api('/api/acl/who?principal=' + encodeURIComponent(p));
    $('#who-res', view).innerHTML = `${r.groups.length ? `<p class="small muted">Groupes : ${r.groups.map(g => `<span class="pill">${esc(pName(g))}</span>`).join(' ')}</p>` : ''}
      ${r.shares.length ? r.shares.map(s => `<h3>${esc(s.share)}</h3>${accessTable(s.access)}`).join('') : emptyState('lock', 'Aucun accès trouvé', 'Cette identité n\'apparaît dans aucune liste d\'accès analysée (hors groupes non résolus).')}`;
  }));
}
const MATRIX_STATUS = { draft: ['neutral', 'Brouillon'], applied: ['ok', 'Appliquée'], modified: ['warning', 'Modifiée depuis application'], superseded: ['neutral', 'Remplacée'] };
function scoreMeter(score) {
  const level = score >= 80 ? '' : score >= 60 ? 'warning' : 'critical';
  return `<div class="meter ${level}" data-tip="Score : <b>${score} / 100</b>"><div class="track"><div class="fill" style="width:${score}%"></div></div><span class="pct">${score}</span></div>`;
}
function accessTable(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>Dossier</th><th>Accès</th><th>Obtenu via</th></tr></thead><tbody>${rows.map(r => `<tr><td class="path" style="padding-left:${12 + r.depth * 16}px">${icon('folder', 'file-icon dir')}${esc(r.path || '(racine)')}</td><td>${lvBadge(r.level)} ${esc(LEVEL_LABEL[r.level])}</td><td class="small">${r.via.map(v => esc(pName(v))).join(', ')}</td></tr>`).join('')}</tbody></table></div>`;
}

// ------------------------------------------------------------------ onglet Droits d'un dossier
async function shareRights(pane, s) {
  const render = async () => {
    const [d, matrices, caps] = await Promise.all([api(`/api/shares/${s.id}/acl`), api(`/api/shares/${s.id}/matrices`), api('/api/acl/capabilities')]);
    const running = (await api(`/api/jobs?share_id=${s.id}&type=acl_scan&status=active`))[0];
    const actions = `${can('operator') ? `<button class="btn primary" data-act="scan" ${running ? 'disabled' : ''}>${icon('refresh')} Analyser les droits</button>` : ''}
      ${d.snapshot ? `<a class="btn" href="/api/shares/${s.id}/acl/export.csv">${icon('download')} Export CSV</a><button class="btn" data-act="backups">${icon('restore')} Sauvegardes</button>` : ''}`;
    if (!d.snapshot) {
      pane.innerHTML = `<div id="job-slot"></div><div class="card">${emptyState('lock', 'Droits non analysés', `Analyse des ${s.acl_depth || 4} premiers niveaux de dossiers : propriétaires, héritage, entrées explicites, anomalies.`, `<div class="btn-row" style="justify-content:center">${actions}</div>`)}</div>`;
    } else {
      const sm = d.summary; const bs = sm.by_severity || {};
      const findings = d.findings.slice().sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
      pane.innerHTML = `<div id="job-slot"></div>
        <div class="filters" style="margin-bottom:16px">${actions}<span class="spacer"></span><span class="small muted">Analyse du ${fmtDate(d.snapshot.created_at)} · ${d.snapshot.platform === 'windows' ? 'NTFS' : 'POSIX / ACL'} · profondeur ${d.snapshot.depth}</span></div>
        <div class="tiles">
          ${tile({ label: 'Score des droits', value: sm.score + ' / 100', ic: 'shield', tone: scoreTone(sm.score) })}
          ${tile({ label: 'Dossiers analysés', value: fmtNum(sm.folders), ic: 'folder', sub: sm.errors ? `${sm.errors} illisible(s)` : '' })}
          ${tile({ label: 'Problèmes', value: fmtNum((bs.critical || 0) + (bs.warning || 0)), ic: 'alert', tone: bs.critical ? 'critical' : bs.warning ? 'warning' : 'good', sub: `${bs.critical || 0} critique(s) · ${bs.info || 0} info` })}
          ${tile({ label: "Listes d'accès distinctes", value: fmtNum(sm.unique_acls), ic: 'list', tone: 'violet', sub: `${fmtNum(sm.explicit_aces)} entrées explicites` })}
          ${tile({ label: "Ruptures d'héritage", value: sm.protected == null ? '—' : fmtNum(sm.protected), ic: 'lock', tone: 'aqua', sub: sm.changes ? `${sm.changes} changement(s) depuis l'analyse précédente` : '' })}
        </div>
        <div class="explorer" style="grid-template-columns:minmax(0,1fr) minmax(0,1.3fr)">
          <div class="card flush">${cardHead('Arborescence', '🔒 héritage désactivé · ● anomalies')}<div class="table-wrap tall" id="tree">${rightsTree(d.nodes)}</div></div>
          <div class="card" id="node">${emptyState('folder', 'Sélectionnez un dossier', 'Liste d\'accès, droits effectifs et actions.')}</div>
        </div>
        <div class="card flush">${cardHead('Matrices de droits', 'Décrivez les droits cibles sur les premiers niveaux, comparez à l\'existant, générez le script ou appliquez.',
          can('operator') ? `<div class="btn-row"><button class="btn primary" data-act="newmx" data-kind="proposal">${icon('sparkle')} Proposition AGDLP</button><button class="btn" data-act="newmx" data-kind="current">À partir de l'existant</button><button class="btn ghost" data-act="newmx" data-kind="empty">Vierge</button></div>` : '')}
          ${matrices.length ? `<table><tbody>${matrices.map(m => `<tr class="clickable" data-go="#/matrix/${m.id}"><td class="cell-title">${esc(m.name)}</td><td>${badge(MATRIX_STATUS, m.status)}</td><td class="small muted">modifiée ${fmtAgo(m.updated_at)} par ${esc(m.created_by)}${m.applied_at ? ` · appliquée ${fmtAgo(m.applied_at)} par ${esc(m.applied_by)}` : ''}</td><td><a class="btn sm" href="#/matrix/${m.id}">Ouvrir →</a></td></tr>`).join('')}</tbody></table>`
            : `<div class="empty small">Aucune matrice. La proposition AGDLP crée des groupes « lecture » et « modification » par dossier métier et le droit de parcours sur les dossiers parents.</div>`}</div>
        <div class="card flush">${cardHead('Anomalies', `${findings.length} constat(s)`, `${sel('fsev', [['', 'Toutes'], ['critical', 'Critiques'], ['warning', 'Avertissements'], ['info', 'Informations']], '', 'id="fsev" style="width:auto"')}`)}
          <div class="table-wrap tall" id="findings"></div></div>
        <div class="grid cols-2">
          <form class="card" id="eff">${cardHead('Accès effectifs d\'une identité', 'Sur ce dossier supervisé')}<div class="filters">${principalPicker('principal', d.principals)}<button class="btn" type="submit">${icon('search')} Calculer</button></div><div id="eff-res"></div></form>
          <div class="card" id="diff">${cardHead('Changements récents', 'Entre les deux dernières analyses')}<div class="muted small">Chargement…</div></div>
        </div>`;
      const renderFindings = () => {
        const f = $('#fsev', pane).value;
        const rows = findings.filter(x => !f || x.severity === f);
        $('#findings', pane).innerHTML = rows.length ? `<table><thead><tr><th>Gravité</th><th>Constat</th><th>Dossier</th><th>Identité</th><th>Recommandation</th></tr></thead><tbody>${rows.slice(0, 1000).map(x => `<tr class="clickable" data-node="${esc(x.path)}"><td>${sev(x.severity)}</td><td><span class="strong">${esc(x.title)}</span>${x.detail ? `<div class="cell-sub">${esc(x.detail)}</div>` : ''}</td><td class="path">${esc(x.path || '(racine)')}</td><td>${x.principal ? esc(pName(x.principal)) : ''}</td><td class="small">${esc(x.recommendation)}</td></tr>`).join('')}</tbody></table>` : emptyState('check', 'Aucune anomalie', '', '', 'good');
      };
      renderFindings();
      $('#fsev', pane).addEventListener('change', renderFindings);
      api(`/api/shares/${s.id}/acl/diff`).then(df => {
        $('#diff', pane).innerHTML = cardHead('Changements récents', df.from ? `Du ${fmtDate(df.from)} au ${fmtDate(df.to)}` : 'Une seule analyse disponible') +
          (df.changes.length ? `<div class="table-wrap tall"><table><tbody>${df.changes.slice(0, 300).map(c => `<tr><td class="path">${esc(c.path || '(racine)')}</td><td>${{ added: '<span class="pill on">ajout</span>', removed: '<span class="pill err">retrait</span>', inheritance: '<span class="pill warn">héritage</span>', owner: '<span class="pill blue">propriétaire</span>' }[c.type]}</td><td class="small">${c.principal ? esc(pName(c.principal)) + ' — ' : ''}${esc(c.detail)}</td></tr>`).join('')}</tbody></table></div>`
            : `<div class="empty small">Aucun changement de droits explicites.${df.created_folders ? ` ${df.created_folders} dossier(s) créé(s).` : ''}</div>`);
      }).catch(() => {});
      $('#eff', pane).addEventListener('submit', guard(async e => {
        e.preventDefault();
        const p = formData(e.target).principal.trim(); if (!p) return;
        const r = await api(`/api/shares/${s.id}/acl/principal?name=${encodeURIComponent(p)}`);
        $('#eff-res', pane).innerHTML = (r.groups.length ? `<p class="small muted">Groupes : ${r.groups.map(g => esc(pName(g))).join(', ')}</p>` : '') + (r.access.length ? accessTable(r.access) : '<div class="empty small">Aucun accès.</div>');
      }));
      pane._data = d; pane._caps = caps;
    }
    if (running) {
      const job = await watchJob(running.id, j => { const slot = $('#job-slot', pane); if (slot) slot.innerHTML = `<div style="margin-bottom:16px">${jobLine(j, 'Analyse des droits en cours')}</div>`; });
      if (job) render();
    }
  };
  const showNode = async path => {
    $$('#tree tr', pane).forEach(t => t.classList.toggle('selected', t.dataset.node === path));
    const n = await api(`/api/shares/${s.id}/acl/node?path=${encodeURIComponent(path)}`);
    const win = pane._data.platform === 'windows';
    $('#node', pane).innerHTML = `${cardHead(esc(path || '(racine)'), `Propriétaire : ${esc(pName(n.owner || '—'))}${n.protected == null ? '' : n.protected ? ' · 🔒 héritage désactivé' : ' · hérite du parent'}`,
      can('admin') ? `<div class="btn-row"><button class="btn sm primary" data-act="grant" data-path="${esc(path)}">${icon('plus')} Accorder</button>${win && path ? `<button class="btn sm" data-act="${n.protected ? 'inherit' : 'protect'}" data-path="${esc(path)}">${n.protected ? 'Réactiver l\'héritage' : 'Désactiver l\'héritage'}</button>` : ''}<button class="btn sm ghost" data-act="owner" data-path="${esc(path)}">Propriétaire</button></div>` : '')}
      ${n.findings.length ? `<div style="margin-bottom:12px">${n.findings.map(f => `<div class="small" style="margin:3px 0">${sev(f.severity)} ${esc(f.title)}${f.principal ? ' — ' + esc(pName(f.principal)) : ''}</div>`).join('')}</div>` : ''}
      <h3 style="margin-top:4px">Liste d'accès</h3>
      <div class="table-wrap"><table><thead><tr><th>Identité</th><th>Type</th><th>Droit</th><th>Portée</th><th>Origine</th><th></th></tr></thead><tbody>
      ${n.aces.map(a => `<tr><td><span class="strong">${esc(a.label)}</span>${a.broad ? ' <span class="pill err">tout le monde</span>' : ''}${a.admin ? ' <span class="pill">admin</span>' : ''}</td>
        <td class="small">${PTYPE_ICON[a.ptype] || ''} ${esc(a.ptype_label || '')}</td>
        <td>${a.allow ? '' : '<span class="pill err">Refus</span> '}${lvBadge(a.level)} ${esc(a.level_label || '')}${a.special ? ` <span class="pill" title="${esc(a.raw)}">spéciaux</span>` : ''}</td>
        <td class="small">${esc(a.scope_label || '')}</td><td class="small">${a.inherited ? 'Hérité' : '<b>Explicite</b>'}</td>
        <td>${can('admin') && !a.inherited && !a.role ? `<button class="btn sm ghost danger" data-act="revoke" data-path="${esc(path)}" data-principal="${esc(a.principal)}" data-deny="${a.allow ? 0 : 1}" title="Retirer">${icon('close')}</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div>
      <h3>Droits effectifs</h3><div class="btn-row">${n.effective.map(e => `<span class="pill" style="display:inline-flex;gap:6px;align-items:center">${lvBadge(e.level)} ${esc(e.label)}</span>`).join('')}</div>`;
  };
  const opJob = async (ops, label) => {
    const r = await api(`/api/shares/${s.id}/acl/ops`, { method: 'POST', body: { ops } });
    const job = await watchJob(r.job_id, j => { const slot = $('#job-slot', pane); if (slot) slot.innerHTML = `<div style="margin-bottom:16px">${jobLine(j, label)}</div>`; });
    const res = job.result || {};
    toast(job.status === 'done' && !res.failed ? `${label} : effectué (sauvegarde des droits créée)` : `${label} : ${res.results ? res.results.filter(x => !x.ok).map(x => x.error).join(' ; ') : job.error}`, job.status === 'done' && !res.failed ? 'ok' : 'error');
    await render();
  };
  pane.addEventListener('click', e => { const tr = e.target.closest('[data-node]'); if (tr && !e.target.closest('button,a')) guard(showNode)(tr.dataset.node); });
  rowLinks(pane);
  bindActions(pane, {
    scan: async () => { await api(`/api/shares/${s.id}/acl/scan`, { method: 'POST' }); toast('Analyse des droits lancée'); render(); },
    newmx: async el => { const r = await api(`/api/shares/${s.id}/matrices`, { method: 'POST', body: { kind: el.dataset.kind } }); location.hash = `#/matrix/${r.id}`; },
    grant: el => modal({ title: `Accorder un droit — ${el.dataset.path || '(racine)'}`, submit: 'Accorder', body: `<div class="form-grid">
        ${field('Identité', principalPicker('principal', pane._data.principals), 'Groupe de préférence (modèle AGDLP).', true)}
        ${field('Niveau', sel('level', LEVELS.slice(1).map(l => [l, `${LEVEL_LABEL[l]} — ${LEVEL_HELP[l]}`]), 'modify'), '', true)}</div>
        <p class="small muted">Une sauvegarde complète des droits est réalisée avant la modification. Les sous-dossiers héritent du droit (sauf « parcours »).</p>`,
      onSubmit: f => { const d = formData(f); opJob([{ op: 'grant', path: el.dataset.path, principal: d.principal.trim(), level: d.level, recursive: pane._data.platform === 'posix' && d.level !== 'list' }], 'Droit accordé'); } }),
    revoke: el => confirmBox('Retirer le droit', `Retirer l'entrée ${el.dataset.deny === '1' ? 'de <b>refus</b>' : ''} de <b>${esc(pName(el.dataset.principal))}</b> sur <span class="mono">${esc(el.dataset.path || '(racine)')}</span> ?`,
      () => { opJob([{ op: el.dataset.deny === '1' ? 'remove_deny' : 'revoke', path: el.dataset.path, principal: el.dataset.principal }], 'Droit retiré'); }, 'Retirer'),
    protect: el => confirmBox('Désactiver l\'héritage', 'Les droits hérités seront <b>copiés</b> en droits explicites : rien n\'est perdu, mais le dossier ne suivra plus les changements de son parent.',
      () => { opJob([{ op: 'protect', path: el.dataset.path, strip: false }], 'Héritage désactivé'); }, 'Désactiver', false),
    inherit: el => confirmBox('Réactiver l\'héritage', 'Le dossier recevra à nouveau les droits de son parent (les droits explicites sont conservés).',
      () => { opJob([{ op: 'inherit', path: el.dataset.path }], 'Héritage réactivé'); }, 'Réactiver', false),
    owner: el => modal({ title: 'Changer le propriétaire', size: 'sm', submit: 'Appliquer', body: field('Nouveau propriétaire', principalPicker('principal', pane._data.principals, '', 'BUILTIN\\Administrators')),
      onSubmit: f => { opJob([{ op: 'owner', path: el.dataset.path, principal: formData(f).principal.trim() }], 'Propriétaire modifié'); } }),
    backups: async () => {
      const list = await api(`/api/shares/${s.id}/acl/backups`);
      modal({ title: 'Sauvegardes des droits', size: 'lg', noFooter: true, body: list.length ? `<p class="small muted">Une sauvegarde complète est prise avant chaque modification de droits ou application de matrice.</p><table><tbody>${list.map(b => `<tr><td class="mono small">${esc(b.name)}</td><td>${fmtDate(b.created_at)}</td><td class="num">${fmtBytes(b.size)}</td><td>${can('admin') ? `<button class="btn sm" data-restore="${esc(b.name)}">${icon('restore')} Restaurer</button>` : ''}</td></tr>`).join('')}</tbody></table>` : emptyState('restore', 'Aucune sauvegarde'),
        onOpen: form => form.addEventListener('click', guard(async e => {
          const b = e.target.closest('[data-restore]'); if (!b) return;
          if (!confirm(`Restaurer les droits depuis ${b.dataset.restore} ?`)) return;
          $('#modal-root').innerHTML = '';
          const r = await api(`/api/shares/${s.id}/acl/restore`, { method: 'POST', body: { name: b.dataset.restore } });
          await watchJob(r.job_id); toast('Droits restaurés'); render();
        })) });
    },
  });
  await render();
}
function rightsTree(nodes) {
  // Ordre arborescent : chaque dossier suit son parent (le séparateur passe avant tout autre caractère).
  const sortKey = p => p.split('/').join('\u0001').toLowerCase();
  const sorted = nodes.slice().sort((a, b) => (sortKey(a.path) < sortKey(b.path) ? -1 : sortKey(a.path) > sortKey(b.path) ? 1 : 0));
  return `<table><tbody>${sorted.map(n => `<tr class="clickable" data-node="${esc(n.path)}"><td style="padding-left:${12 + n.depth * 18}px" class="nowrap">${icon('folder', 'file-icon dir')}${esc(n.path ? n.path.split('/').pop() : '(racine)')}
    ${n.protected ? ' <span title="Héritage désactivé">🔒</span>' : ''}${n.error ? ' <span class="pill err">illisible</span>' : ''}</td>
    <td class="num small muted">${n.explicit ? `${n.explicit} expl.` : ''}</td>
    <td style="width:1%">${n.critical ? `<span class="sev critical">${n.issues}</span>` : n.issues ? `<span class="sev warning">${n.issues}</span>` : ''}</td></tr>`).join('')}</tbody></table>`;
}

// ------------------------------------------------------------------ éditeur de matrice
async function viewMatrix(view, query, id) {
  let m = await api(`/api/matrices/${id}`);
  const share = await api(`/api/shares/${m.share_id}`);
  let data = m.data; let dirty = false;
  const isWin = data.platform === 'windows';
  const header = () => setPage(m.name, `<a href="#/rights">Droits</a> ${icon('chevron', 'file-icon')} <a href="#/share/${share.id}/rights">${esc(share.name)}</a> ${icon('chevron', 'file-icon')} Matrice`,
    `${can('operator') ? `<button class="btn primary" data-mact="save">${icon('check')} Enregistrer</button>` : ''}
     <a class="btn" href="/api/matrices/${id}/script">${icon('download')} Script ${isWin ? 'PowerShell' : 'shell'}</a>
     <a class="btn" href="/api/matrices/${id}/export.xlsx">${icon('download')} Excel</a>
     ${can('operator') ? `<button class="btn" data-mact="import">${icon('upload')} Importer CSV</button>` : ''}
     ${can('admin') ? `<button class="btn danger" data-mact="apply">${icon('play')} Appliquer</button>` : ''}`,
    `${badge(MATRIX_STATUS, m.status)} · ${data.rows.length} dossier(s) × ${data.columns.length} identité(s) · droits réels analysés ${fmtAgo(m.snapshot_at)}`);
  header();
  const key = (p, c) => `${p}\t${c}`;
  const parentOf = p => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : (p ? '' : null));
  const cellOf = (p, c) => data.cells[key(p, c)] || 'none';
  const inheritedOf = (p, c) => { const par = parentOf(p); if (par == null) return 'none'; const lv = cellOf(par, c); return lv === 'list' ? 'none' : lv; };
  const render = () => {
    const rows = data.rows.slice().sort((a, b) => (a.path === '' ? -1 : b.path === '' ? 1 : a.path.localeCompare(b.path)));
    const plan = m.plan || { ops: [], warnings: [], summary: {} };
    view.innerHTML = `<div id="mx-job"></div>
      ${dirty ? `<div class="banner warning" style="margin-bottom:16px">${icon('info')}Modifications non enregistrées : le plan d'écart sera recalculé à l'enregistrement.</div>` : ''}
      <div class="card flush">${cardHead('Matrice', 'Cliquez une case pour choisir le niveau. Les cases pâles sont héritées du dossier parent.',
        can('operator') ? `<div class="btn-row"><button class="btn sm" data-act="addcol">${icon('plus')} Identité</button><button class="btn sm" data-act="addrow">${icon('plus')} Dossier</button></div>` : '')}
        <div class="matrix-wrap"><table class="matrix"><thead><tr><th class="mx-folder">Dossier</th><th class="mx-inh">Héritage</th>
          ${data.columns.map((c, i) => `<th class="mx-col ${c.system ? 'sys' : ''}" title="${esc(c.principal)}"><div>${esc(PTYPE_ICON[c.ptype] || '')} ${esc(pName(c.principal))}</div>${c.new ? '<span class="pill blue">à créer</span>' : ''}${can('operator') ? `<button class="mx-x" data-act="delcol" data-i="${i}" title="Retirer la colonne">×</button>` : ''}</th>`).join('')}</tr></thead>
          <tbody>${rows.map(r => `<tr><td class="mx-folder" style="padding-left:${10 + (r.path ? r.path.split('/').length : 0) * 16}px">${icon('folder', 'file-icon dir')}<span title="${esc(r.path)}">${esc(r.path ? r.path.split('/').pop() : '(racine)')}</span>${r.create ? ' <span class="pill blue">à créer</span>' : ''}${can('operator') && r.path ? ` <button class="mx-x" data-act="delrow" data-path="${esc(r.path)}" title="Retirer la ligne">×</button>` : ''}</td>
            <td class="mx-inh">${r.path ? `<button class="chip-btn ${r.inherit ? '' : 'on'}" data-act="inh" data-path="${esc(r.path)}" ${can('operator') ? '' : 'disabled'}>${r.inherit ? 'Hérite' : '🔒 Propres'}</button>` : '<span class="small muted">racine</span>'}</td>
            ${data.columns.map(c => { const lv = cellOf(r.path, c.principal); const inh = r.inherit && r.path && lv !== 'none' && lv === inheritedOf(r.path, c.principal); return `<td class="mx-cell"><button class="lv lv-${lv}${inh ? ' inh' : ''}" data-act="cell" data-path="${esc(r.path)}" data-p="${esc(c.principal)}" title="${esc(pName(c.principal))} — ${esc(LEVEL_LABEL[lv])}${inh ? ' (hérité)' : ''}" ${can('operator') ? '' : 'disabled'}>${LEVEL_SHORT[lv]}</button></td>`; }).join('')}</tr>`).join('')}</tbody></table></div>
        <div class="legend" style="padding:12px 20px">${LEVELS.map(l => `<span>${lvBadge(l)} ${esc(LEVEL_LABEL[l])}</span>`).join('')}<span>🔒 Propres : héritage désactivé</span></div></div>
      <div class="grid cols-2">
        <div class="card">${cardHead('Écarts avec les droits actuels', `${plan.ops.length} opération(s) à appliquer`)}
          ${plan.warnings.length ? plan.warnings.map(w => `<div class="banner warning" style="margin-bottom:8px">${icon('alert')}<span><span class="mono small">${esc(w.path || '(racine)')}</span> — ${esc(w.message)}</span></div>`).join('') : ''}
          ${plan.ops.length ? `<div class="table-wrap tall"><table><tbody>${plan.ops.map(o => `<tr><td style="width:1%"><span class="pill ${o.op === 'revoke' || o.op === 'protect' ? 'err' : o.op === 'grant' ? 'on' : 'blue'}">${esc(OP_LABEL[o.op] || o.op)}</span></td><td><div class="path">${esc(o.path || '(racine)')}</div><div class="cell-sub">${o.principal ? esc(pName(o.principal)) : ''}${o.level ? ' ' + lvBadge(o.level) : ''}</div></td><td class="small muted" style="width:32%">${esc(o.reason || '')}</td></tr>`).join('')}</tbody></table></div>`
            : emptyState('check', 'Conforme', 'Les droits réels correspondent à la matrice.', '', 'good')}</div>
        <form class="card form" id="mx-opts" style="align-content:start">${cardHead('Options d\'application')}
          ${chk('remove_unlisted', 'Retirer les entrées explicites absentes de la matrice', data.options.remove_unlisted)}
          ${chk('remove_denies', 'Supprimer les refus explicites', data.options.remove_denies)}
          ${isWin ? chk('reset_below', 'Remettre en héritage les sous-dossiers plus profonds que la matrice', data.options.reset_below) : ''}
          ${chk('create_groups', 'Créer les groupes et y ajouter les membres proposés lors de l\'application', data.options.create_groups)}
          ${isWin ? field('Unité d\'organisation des groupes (AD)', inp('groups_ou', data.options.groups_ou || '', 'text', 'placeholder="OU=Groupes,OU=Fichiers,DC=client,DC=local"')) : ''}
          <p class="small muted">Avant toute application, VigieFS sauvegarde l'intégralité des droits (${isWin ? 'icacls /save' : 'getfacl -R'}) : restauration possible depuis l'onglet Droits.</p></form>
      </div>
      ${data.groups_plan && data.groups_plan.length ? `<div class="card flush">${cardHead('Groupes proposés (AGDLP)', 'Groupes de domaine local portant les droits ; y imbriquer les groupes globaux métier ou les utilisateurs.')}
        <div class="table-wrap"><table><thead><tr><th>Créer</th><th>Groupe</th><th>Accès</th><th>Dossier</th><th>Membres proposés</th></tr></thead><tbody>
        ${data.groups_plan.map((g, i) => `<tr><td class="check"><input type="checkbox" data-gcreate="${i}" ${g.create !== false ? 'checked' : ''} ${can('operator') ? '' : 'disabled'}></td><td class="mono small strong">${esc(pName(g.group))}</td><td>${esc(g.access)}</td><td class="path">${esc(g.path || '(racine)')}</td>
          <td><input type="text" data-gmembers="${i}" value="${esc(g.members.join(', '))}" ${can('operator') ? '' : 'disabled'}>${g.review && g.review.length ? `<div class="cell-sub" style="color:var(--warning-ink)">⚠ accès actuel à reprendre manuellement : ${g.review.map(r => esc(pName(r))).join(', ')}</div>` : ''}</td></tr>`).join('')}
        </tbody></table></div></div>` : ''}`;
  };
  const markDirty = () => { dirty = true; render(); };
  const save = async () => {
    const o = formData($('#mx-opts', view));
    data.options = { ...data.options, ...o };
    $$('[data-gcreate]', view).forEach(c => { data.groups_plan[c.dataset.gcreate].create = c.checked; });
    $$('[data-gmembers]', view).forEach(c => { data.groups_plan[c.dataset.gmembers].members = c.value.split(',').map(x => x.trim()).filter(Boolean); });
    await api(`/api/matrices/${id}`, { method: 'PUT', body: { data } });
    m = await api(`/api/matrices/${id}`); data = m.data; dirty = false; header(); render(); toast('Matrice enregistrée');
  };
  const popover = (el) => {
    $$('.lv-pop').forEach(p => p.remove());
    const pop = document.createElement('div');
    pop.className = 'lv-pop';
    pop.innerHTML = LEVELS.map(l => `<button type="button" data-lv="${l}">${lvBadge(l)} ${esc(LEVEL_LABEL[l])}</button>`).join('');
    document.body.appendChild(pop);
    const r = el.getBoundingClientRect();
    pop.style.left = Math.min(innerWidth - 230, r.left) + 'px'; pop.style.top = (r.bottom + 4 + scrollY) + 'px';
    pop.addEventListener('click', e => {
      const b = e.target.closest('[data-lv]'); if (!b) return;
      const k = key(el.dataset.path, el.dataset.p);
      if (b.dataset.lv === 'none') delete data.cells[k]; else data.cells[k] = b.dataset.lv;
      pop.remove(); markDirty();
    });
    setTimeout(() => document.addEventListener('click', function close(ev) { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('click', close); } }), 0);
  };
  bindActions(view, {
    cell: el => popover(el),
    inh: el => { const r = data.rows.find(x => x.path === el.dataset.path); r.inherit = !r.inherit; markDirty(); },
    delcol: el => { const c = data.columns[el.dataset.i]; data.columns.splice(el.dataset.i, 1); Object.keys(data.cells).forEach(k => { if (k.endsWith('\t' + c.principal)) delete data.cells[k]; }); markDirty(); },
    delrow: el => { data.rows = data.rows.filter(r => r.path !== el.dataset.path); Object.keys(data.cells).forEach(k => { if (k.startsWith(el.dataset.path + '\t')) delete data.cells[k]; }); markDirty(); },
    addcol: () => modal({ title: 'Ajouter une identité', submit: 'Ajouter', body: `<div class="form-grid">${field('Identité', principalPicker('principal', m.principals, '', isWin ? 'CORP\\GDL_Compta_RW' : 'group:compta'), 'Existante (liste) ou nouveau groupe à créer.', true)}
        ${field('Type', sel('ptype', [['group', 'Groupe'], ['user', 'Utilisateur'], ['wellknown', 'Identité système']], 'group'))}${field('Niveau initial sur la racine', sel('level', LEVELS.map(l => [l, LEVEL_LABEL[l]]), 'list'))}</div>`,
      onSubmit: f => { const d = formData(f); const p = d.principal.trim(); if (!p) throw new Error('Identité requise'); if (data.columns.some(c => c.principal === p)) throw new Error('Déjà présente'); const known = m.principals.some(x => x.name === p); data.columns.push({ principal: p, ptype: d.ptype, system: false, broad: false, new: !known }); if (d.level !== 'none') data.cells[key('', p)] = d.level; markDirty(); } }),
    addrow: () => modal({ title: 'Ajouter un dossier', submit: 'Ajouter', body: `${field('Chemin relatif', inp('path', '', 'text', 'required placeholder="Compta/Fournisseurs"'), 'Dossier existant plus profond, ou nouveau dossier (créé à l\'application).')}${chk('create', 'Créer ce dossier s\'il n\'existe pas', true)}`,
      onSubmit: f => { const d = formData(f); const p = d.path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, ''); if (!p) throw new Error('Chemin requis'); if (data.rows.some(r => r.path === p)) throw new Error('Déjà présent'); data.rows.push({ path: p, inherit: true, create: d.create, exists: false }); markDirty(); } }),
  });
  $('#page-actions').addEventListener('click', guard(async e => {
    const b = e.target.closest('[data-mact]'); if (!b) return;
    if (b.dataset.mact === 'save') return save();
    if (b.dataset.mact === 'import') {
      const input = document.createElement('input'); input.type = 'file'; input.accept = '.csv,text/csv';
      input.onchange = guard(async () => { const f = input.files[0]; if (!f) return; const r = await api(`/api/matrices/${id}/import`, { method: 'POST', raw: f }); toast(`Matrice importée : ${r.rows} dossiers × ${r.columns} identités`); m = await api(`/api/matrices/${id}`); data = m.data; render(); header(); });
      input.click(); return;
    }
    if (b.dataset.mact === 'apply') {
      if (dirty) await save();
      const p = m.plan;
      confirmBox('Appliquer la matrice', `<p><b>${p.ops.length} opération(s)</b> vont modifier les droits réels de <b>${esc(share.name)}</b>${data.options.create_groups ? ' (avec création des groupes)' : ''}.</p>
        <ul class="list-check"><li>Sauvegarde complète des droits avant modification</li><li>Nouvelle analyse et contrôle de conformité après application</li><li>Restauration possible depuis l'onglet Droits</li></ul>
        ${p.warnings.length ? `<p class="small" style="color:var(--warning-ink)">⚠ ${p.warnings.length} avertissement(s) : relisez le plan avant de continuer.</p>` : ''}
        <p class="small muted">Conseil : téléchargez et relisez d'abord le script.</p>`, async () => {
        const r = await api(`/api/matrices/${id}/apply`, { method: 'POST', body: { confirm: true } });
        const job = await watchJob(r.job_id, j => { const slot = $('#mx-job', view); if (slot) slot.innerHTML = `<div style="margin-bottom:16px">${jobLine(j, 'Application de la matrice — ' + (j.progress.phase || ''))}</div>`; });
        const res = job.result || {};
        modal({ title: 'Résultat de l\'application', noFooter: true, size: 'lg', body: job.status !== 'done' ? `<div class="error-text">${esc(job.error || job.status)}</div>` : `
          <div class="tiles">${tile({ label: 'Opérations réussies', value: fmtNum(res.ok), ic: 'check', tone: 'good' })}${tile({ label: 'Échecs', value: fmtNum(res.failed), ic: 'alert', tone: res.failed ? 'critical' : 'good' })}${tile({ label: 'Écarts restants', value: fmtNum(res.remaining_gaps || 0), ic: 'shield', tone: res.remaining_gaps ? 'warning' : 'good' })}</div>
          ${res.groups_created && res.groups_created.length ? `<p class="small">Groupes créés : ${res.groups_created.map(g => esc(pName(g))).join(', ')}</p>` : ''}
          ${res.failed ? `<h3>Échecs</h3><table><tbody>${res.results.filter(x => !x.ok).map(x => `<tr><td class="path">${esc(x.path || '(racine)')}</td><td>${esc(OP_LABEL[x.op] || x.op)} ${x.principal ? esc(pName(x.principal)) : ''}</td><td class="small">${esc(x.error)}</td></tr>`).join('')}</tbody></table>` : ''}
          <p class="small muted">Sauvegarde : ${esc((res.backup || '').split(/[\\/]/).pop())}</p>` });
        m = await api(`/api/matrices/${id}`); data = m.data; header(); render();
      }, 'Appliquer');
    }
  }));
  render();
}
const OP_LABEL = { grant: 'Accorder', revoke: 'Retirer', protect: "Désactiver l'héritage", inherit: "Réactiver l'héritage", remove_deny: 'Supprimer un refus', reset_children: 'Réinitialiser les sous-dossiers', mkdir: 'Créer le dossier', owner: 'Propriétaire', deny_write: 'Verrouiller' };
