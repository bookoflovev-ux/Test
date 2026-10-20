/* VigieFS — réorganisation d'arborescence : architecture optimisée, appliquée en 2 étapes sans perte de fichier. */
'use strict';

const RS_STATUS = {
  draft: ['neutral', 'Brouillon'], prepared: ['info', 'Étape 1 faite : dossiers créés'], executing: ['info', 'Déplacement en cours'],
  done: ['ok', 'Appliqué'], partial: ['warning', 'Appliqué partiellement'], rolled_back: ['neutral', 'Annulé (retour arrière)'],
};
const RS_OP_STATUS = { done: ['ok', 'Fait'], partial: ['warning', 'Partiel'], skipped: ['neutral', 'Ignoré'], failed: ['critical', 'Échec'] };
const RS_RULE_HINT = {
  naming: 'Caractères interdits, espaces ou point final, noms réservés (CON, AUX…), noms bloqués par OneDrive, doublons de casse.',
  length: 'Noms de dossiers ou de fichiers raccourcis sur les branches qui dépassent la limite de chemin.',
  intermediate: 'Niveau qui ne contient qu\'un seul sous-dossier du même nom ou au nom générique (« Nouveau dossier ») : son contenu remonte d\'un niveau.',
  empty: 'Dossiers ne contenant aucun fichier, sous-dossiers compris.',
  crowded: 'Dossiers contenant trop de fichiers : rangement par année de modification.',
  root: 'Fichiers déposés à la racine du partage, rangés dans un dossier « _A classer ».',
  cold: 'Dossiers sans modification depuis plusieurs années, déplacés dans « _Archives » (chemin d\'origine conservé).',
  template: 'Premier niveau rangé dans un modèle numéroté (01 - Direction, 02 - Finances…) : à valider avec le client.',
};

/** Carte « Réorganisation » de l'onglet Architecture d'un dossier. */
async function restructurePanel(box, s) {
  const plans = await api(`/api/shares/${s.id}/restructure`);
  box.innerHTML = `<div class="card flush">${cardHead('Réorganisation de l\'arborescence',
    'Architecture optimisée proposée à partir de l\'existant, à appliquer sur sélection en deux étapes sécurisées : création des dossiers, puis déplacement vérifié par robocopy.',
    can('operator') ? `<button class="btn primary" data-act="rs-propose" ${s.path_available === false ? 'disabled' : ''}>${icon('sparkle')} Proposer une architecture optimisée</button>` : '')}
    <div id="rs-job" style="padding:0 20px"></div>
    ${plans.length ? `<div class="table-wrap"><table><thead><tr><th>Plan</th><th>État</th><th class="num">Opérations</th><th>Chemins trop longs</th><th></th></tr></thead><tbody>
      ${plans.map(p => `<tr class="clickable" data-go="#/restructure/${p.id}"><td><div class="strong">${esc(p.name)}</div><div class="cell-sub">${esc(p.created_by || '')} · ${fmtAgo(p.created_at)}</div></td>
        <td>${badge(RS_STATUS, p.status)}</td><td class="num">${fmtNum(p.selected)} / ${fmtNum(p.ops)}</td>
        <td>${p.metrics ? `${fmtNum(p.metrics.before.long_paths)} → <b>${fmtNum(p.metrics.after.long_paths)}</b>` : '—'}</td><td>${icon('chevron', 'file-icon')}</td></tr>`).join('')}</tbody></table></div>`
      : `<div style="padding:0 20px 16px">${emptyState('sparkle', 'Aucune proposition', 'VigieFS parcourt l\'arborescence et propose les corrections : noms, chemins trop longs, niveaux inutiles, dossiers vides, données froides…')}</div>`}</div>`;
  rowLinks(box);
  bindActions(box, {
    'rs-propose': async () => {
      const r = await api(`/api/shares/${s.id}/restructure/propose`, { method: 'POST' });
      const job = await watchJob(r.job_id, j => { const slot = $('#rs-job', box); if (slot) slot.innerHTML = jobLine(j); });
      if (job.status === 'done') location.hash = `#/restructure/${job.result.plan_id}`;
      else toast(job.error || 'Proposition impossible', 'error');
    },
  });
}

function rsTarget(o, plan) {
  const arch = plan.archive_folder || '_Archives';
  switch (o.type) {
    case 'rename_dir': case 'rename_file':
      return can('operator') && ['draft', 'prepared'].includes(plan._status)
        ? `<input type="text" class="mono small" data-rsname="${esc(o.id)}" value="${esc(o.name)}" style="width:100%">` : `<span class="mono">${esc(o.name)}</span>`;
    case 'move_dir': return `→ <span class="mono">${esc(o.dest.replace('{archive}', arch).replace('/{parent}', o.src.includes('/') ? '/' + o.src.slice(0, o.src.lastIndexOf('/')) : '').replace('{parent}', ''))}/</span>`;
    case 'merge_up': return '<span class="small">contenu remonté d\'un niveau</span>';
    case 'move_files': return `→ <span class="mono">${esc(o.dest_dir || (o.src ? o.src + '/' : '') + o.dest_sub)}/</span> <span class="cell-sub">${fmtNum(o.files)} fichier(s)</span>`;
    case 'remove_empty': return '<span class="small">supprimé (vide)</span>';
    default: return '';
  }
}

function rsTree(nodes) {
  if (!nodes.length) return emptyState('folder', 'Arborescence vide');
  return `<div class="table-wrap tall"><table><tbody>${nodes.map(n => `<tr${n.changed ? ' class="selected"' : ''}><td style="padding-left:${10 + (n.depth - 1) * 16}px" class="nowrap">${icon('folder', 'file-icon dir')}${esc(n.path.split('/').pop())}${n.changed ? ' <span class="pill blue">modifié</span>' : ''}</td><td class="num small muted">${fmtNum(n.files)}</td></tr>`).join('')}</tbody></table></div>`;
}

// ------------------------------------------------------------------ plan de réorganisation
async function viewRestructure(view, query, id) {
  let p = await api(`/api/restructure/${id}`);
  let q = '';
  const showAll = new Set();
  const editable = () => ['draft', 'prepared'].includes(p.status);
  const header = () => setPage(p.name, `<a href="#/share/${p.share_id}/architecture">${esc(p.share_name)}</a> ${icon('chevron', 'file-icon')} Réorganisation`,
    `<a class="btn" href="/api/restructure/${id}/script">${icon('download')} Script PowerShell</a>
     ${can('admin') && editable() ? `<button class="btn ${p.status === 'draft' ? 'primary' : ''}" data-sact="prepare">${icon('folder')} Étape 1 : créer les dossiers</button>` : ''}
     ${can('admin') && p.status === 'prepared' ? `<button class="btn" data-sact="unprepare">Annuler l'étape 1</button><button class="btn primary" data-sact="execute">${icon('play')} Étape 2 : déplacer</button>` : ''}
     ${can('admin') && ['done', 'partial'].includes(p.status) ? `<button class="btn danger" data-sact="rollback">${icon('restore')} Retour arrière</button>` : ''}
     ${can('operator') && ['draft', 'rolled_back'].includes(p.status) ? `<button class="btn danger" data-sact="delete">${icon('trash')}</button>` : ''}`,
    `${badge(RS_STATUS, p.status)} · ${fmtNum(p.selected)} opération(s) retenue(s) sur ${fmtNum(p.ops)} · proposé ${fmtAgo(p.created_at)} par ${esc(p.created_by || '')}`);

  const render = async () => {
    const d = p.data; d._status = p.status;
    const mb = (d.metrics || {}).before || {}, ma = (d.metrics || {}).after || {};
    const errors = (d.problems || []).filter(x => x.level === 'error'), warns = (d.problems || []).filter(x => x.level === 'warning');
    const ex = (p.result || {}).execute, prep = (p.result || {}).prepare;
    const opRes = (ex || {}).ops || {};
    const byRule = {};
    d.ops.forEach(o => { (byRule[o.rule] = byRule[o.rule] || []).push(o); });
    const step = p.status === 'draft' ? 0 : p.status === 'prepared' ? 1 : 2;
    const metric = (label, a, b, ic) => tile({ label, value: `${fmtNum(a)} → ${fmtNum(b)}`, ic, tone: b < a ? 'good' : b > a ? 'warning' : '', sub: b < a ? `−${fmtNum(a - b)}` : b > a ? `+${fmtNum(b - a)}` : 'inchangé' });
    view.innerHTML = `
      <ol class="stepper"><li class="step ${step > 0 ? 'done' : 'current'}"><span class="dot">${step > 0 ? '✓' : 1}</span><span>Sélection des changements</span></li>
        <li class="step ${step > 1 ? 'done' : step === 1 ? 'current' : ''}"><span class="dot">${step > 1 ? '✓' : 2}</span><span>Étape 1 : création des dossiers</span></li>
        <li class="step ${['done', 'partial'].includes(p.status) ? 'done' : step === 2 ? 'current' : ''}"><span class="dot">${['done', 'partial'].includes(p.status) ? '✓' : 3}</span><span>Étape 2 : déplacement vérifié</span></li></ol>
      <div class="banner info" style="margin-bottom:16px">${icon('shield')}<div><b>Aucun fichier ne peut être perdu.</b> L'étape 1 crée seulement les dossiers cibles. L'étape 2 copie chaque fichier
        avec robocopy sans jamais écraser (/XC /XN /XO), vérifie la copie (taille, date${d.options.verify_hash ? ', empreinte SHA-256' : ''}) et ne supprime que les sources vérifiées ;
        le reste demeure en place et est listé. Cliché instantané, sauvegarde des droits, inventaire avant/après et retour arrière exact (manifeste).</div></div>
      ${d.protected && (d.protected.dirs || d.protected.files) ? `<div class="banner info" style="margin-bottom:16px">${icon('disk')}<span>Machines virtuelles : ${fmtNum(d.protected.dirs)} dossier(s) et ${fmtNum(d.protected.files)} fichier(s) laissés en place (disques virtuels, configurations) : les déplacer casserait les VM.</span></div>` : ''}
      <div id="rs-job"></div>
      <div class="tiles">
        ${tile({ label: 'Opérations retenues', value: `${fmtNum(p.selected)} / ${fmtNum(p.ops)}`, ic: 'tasks', hero: true, sub: `${fmtNum(d.ops.filter(o => o.selected).reduce((a, o) => a + (o.files || 0), 0))} fichier(s) concernés` })}
        ${metric('Chemins trop longs', mb.long_paths || 0, ma.long_paths || 0, 'alert')}
        ${metric('Profondeur maximale', mb.max_depth || 0, ma.max_depth || 0, 'folder')}
        ${metric('Fichiers à la racine', mb.root_files || 0, ma.root_files || 0, 'list')}
        ${metric('Dossiers', mb.dirs || 0, ma.dirs || 0, 'folder')}
      </div>
      ${errors.length ? `<div class="banner critical" style="margin-bottom:12px">${icon('alert')}<div><b>À corriger avant l'étape 1 :</b><ul style="margin:6px 0 0 18px">${errors.map(x => `<li>${esc(x.message)}</li>`).join('')}</ul></div></div>` : ''}
      ${warns.length ? `<div class="banner warning" style="margin-bottom:12px">${icon('info')}<div><b>Points d'attention</b><ul style="margin:6px 0 0 18px">${warns.slice(0, 12).map(x => `<li>${esc(x.message)}</li>`).join('')}${warns.length > 12 ? `<li>… ${warns.length - 12} autre(s)</li>` : ''}</ul></div></div>` : ''}
      ${prep && prep.problems && prep.problems.length && p.status !== 'draft' ? `<div class="banner warning" style="margin-bottom:12px">${icon('info')}<div><b>Contrôles de l'étape 1</b><ul style="margin:6px 0 0 18px">${prep.problems.slice(0, 12).map(x => `<li>${esc(x.message)}</li>`).join('')}</ul></div></div>` : ''}
      ${ex ? `<div class="card" style="margin-bottom:16px">${cardHead('Résultat du déplacement', `${fmtDate(ex.started_at)} → ${fmtDate(ex.finished_at)}`)}
        <div class="tiles">${tile({ label: 'Fichiers déplacés et vérifiés', value: fmtNum((ex.totals || {}).moved), ic: 'check', tone: 'good' })}
          ${tile({ label: 'Laissés en place', value: fmtNum((ex.totals || {}).kept), ic: 'info', tone: (ex.totals || {}).kept ? 'warning' : 'good', sub: 'conflits, fichiers ouverts…' })}
          ${tile({ label: 'Inventaire', value: ex.inventory_ok ? 'identique' : 'différent', ic: 'shield', tone: ex.inventory_ok ? 'good' : 'warning', sub: ex.inventory_before ? `${fmtNum(ex.inventory_before.files)} → ${fmtNum((ex.inventory_after || {}).files)} fichiers` : '' })}
          ${tile({ label: 'Sauvegardes préalables', value: ex.vss && ex.vss.ok ? 'cliché + ACL' : 'ACL', ic: 'lock', sub: ex.vss ? (ex.vss.ok ? `cliché ${esc(ex.vss.id || '')}` : esc(ex.vss.skipped || ex.vss.error || '')) : 'cliché non demandé' })}</div>
        ${ex.error ? `<div class="banner critical" style="margin-top:12px">${icon('alert')}${esc(ex.error)}</div>` : ''}</div>` : ''}
      <div class="grid span-2-1">
        <div>
          <div class="card" style="margin-bottom:12px"><input type="search" id="rs-q" placeholder="Filtrer les opérations par chemin…" value="${esc(q)}" style="width:100%"></div>
          ${Object.keys(p.rules).filter(r => byRule[r]).map(rule => {
            const ops = byRule[rule].filter(o => !q || o.src.toLowerCase().includes(q.toLowerCase()) || (o.name || '').toLowerCase().includes(q.toLowerCase()));
            const sel = byRule[rule].filter(o => o.selected).length;
            const shown = showAll.has(rule) ? ops : ops.slice(0, 150);
            return `<div class="card flush" style="margin-bottom:12px">${cardHead(`${editable() && can('operator') ? `<input type="checkbox" data-rsrule="${rule}" ${sel === byRule[rule].length ? 'checked' : ''} ${sel && sel < byRule[rule].length ? 'data-partial' : ''}> ` : ''}${esc(p.rules[rule])}`,
              `${esc(RS_RULE_HINT[rule] || '')}`, `<span class="pill ${sel ? 'on' : ''}">${fmtNum(sel)} / ${fmtNum(byRule[rule].length)}</span>`)}
              <div class="table-wrap${ops.length > 12 ? ' tall' : ''}"><table><tbody>${shown.map(o => `<tr>
                <td class="check">${editable() && can('operator') ? `<input type="checkbox" data-rsop="${esc(o.id)}" ${o.selected ? 'checked' : ''}>` : (o.selected ? '✓' : '')}</td>
                <td style="width:22%"><div class="small strong">${esc(p.op_labels[o.type] || o.type)}</div>${opRes[o.id] ? badge(RS_OP_STATUS, opRes[o.id].status) : ''}</td>
                <td><div class="path small">${esc(o.src || '(racine)')}</div><div class="cell-sub">${esc(o.reason || '')}${opRes[o.id] && opRes[o.id].error ? ` — ${esc(opRes[o.id].error)}` : ''}${opRes[o.id] && (opRes[o.id].kept || []).length ? ` — ${opRes[o.id].kept.length} laissé(s) en place : ${esc(opRes[o.id].kept[0].path)} (${esc(opRes[o.id].kept[0].reason)})` : ''}</div></td>
                <td style="width:34%">${rsTarget(o, d)}</td></tr>`).join('')}</tbody></table></div>
              ${ops.length > shown.length ? `<div style="padding:10px 16px"><button class="btn sm" data-rsmore="${rule}">Afficher les ${fmtNum(ops.length - shown.length)} autres</button></div>` : ''}</div>`;
          }).join('') || `<div class="card">${emptyState('check', 'Aucune amélioration proposée', 'L\'arborescence respecte déjà les règles.', '', 'good')}</div>`}
        </div>
        <div>
          <div class="card" style="margin-bottom:12px">${cardHead('Options du déplacement')}
            <form class="form" id="rs-opts">
              ${chk('verify_hash', 'Vérifier chaque copie par empreinte SHA-256 (plus lent, contenu garanti)', d.options.verify_hash, editable() ? '' : 'disabled')}
              ${chk('vss_snapshot', 'Cliché instantané du volume avant déplacement (Windows Server)', d.options.vss_snapshot, editable() ? '' : 'disabled')}
              ${chk('backup_mode', 'Mode sauvegarde robocopy (/B) : copie aussi les fichiers refusés au compte de service', d.options.backup_mode, editable() ? '' : 'disabled')}
              ${field('Copies robocopy en parallèle (/MT)', inp('threads', d.options.threads, 'number', `min="1" max="64" ${editable() ? '' : 'disabled'}`))}
            </form></div>
          <div class="card flush">${cardHead('Aperçu de l\'arborescence', 'Trois premiers niveaux · fichiers cumulés', seg('side', [['after', 'Après'], ['before', 'Avant']], 'after'))}<div id="rs-tree"></div></div>
        </div>
      </div>`;
    $$('[data-partial]', view).forEach(c => { c.indeterminate = true; });
    const tree = await api(`/api/restructure/${id}/tree`);
    const drawTree = () => { const side = (view.querySelector('[name=side]:checked') || {}).value || 'after'; $('#rs-tree', view).innerHTML = rsTree(tree[side]); };
    view.querySelectorAll('[name=side]').forEach(r => { r.onchange = drawTree; });
    drawTree();
    const qi = $('#rs-q', view);
    let t;
    qi.oninput = () => { clearTimeout(t); t = setTimeout(() => { q = qi.value.trim(); render().then(() => { const i = $('#rs-q', view); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
    $$('[data-rsmore]', view).forEach(b => { b.onclick = () => { showAll.add(b.dataset.rsmore); render(); }; });
  };

  const save = async body => { p = await api(`/api/restructure/${id}`, { method: 'PUT', body }); header(); bindHead(); await render(); };
  view.addEventListener('change', guard(async e => {
    const el = e.target;
    if (el.matches('[data-rsop]')) await save({ ops: [{ id: el.dataset.rsop, selected: el.checked }] });
    else if (el.matches('[data-rsrule]')) await save({ rules: { [el.dataset.rsrule]: el.checked } });
    else if (el.matches('[data-rsname]')) await save({ ops: [{ id: el.dataset.rsname, name: el.value }] });
    else if (el.closest('#rs-opts')) { const v = formData($('#rs-opts', view)); await save({ options: { verify_hash: !!v.verify_hash, vss_snapshot: !!v.vss_snapshot, backup_mode: !!v.backup_mode, threads: Number(v.threads) || 8 } }); }
  }));
  const follow = async (jobId, label) => {
    const job = await watchJob(jobId, j => { const slot = $('#rs-job', view); if (slot) slot.innerHTML = `<div style="margin-bottom:16px">${jobLine(j, label)}</div>`; });
    p = await api(`/api/restructure/${id}`);
    header(); bindHead(); await render();
    if (job.status !== 'done') toast(job.error || job.status, 'error');
    return job;
  };
  const runJob = async (url, body, label) => follow((await api(url, { method: 'POST', body })).job_id, label);
  const actions = {
    prepare: () => confirmBox('Étape 1 : créer les dossiers', `<p>VigieFS contrôle la sélection (noms, longueurs, fichiers ouverts, espace disque) puis crée <b>uniquement les dossiers</b> de l'arborescence cible. Aucun fichier n'est déplacé.</p>
      <p class="small muted">Vous pouvez ensuite vérifier les dossiers créés (et y appliquer une matrice de droits) avant l'étape 2.</p>`,
      async () => { const j = await runJob(`/api/restructure/${id}/prepare`, null, 'Création des dossiers'); if (j.status === 'done') toast(`${j.result.created} dossier(s) créé(s)`); }, 'Créer les dossiers', false),
    unprepare: () => confirmBox('Annuler l\'étape 1', '<p>Les dossiers créés à l\'étape 1 et restés vides sont supprimés.</p>', async () => { await api(`/api/restructure/${id}/unprepare`, { method: 'POST' }); p = await api(`/api/restructure/${id}`); header(); bindHead(); render(); }, 'Annuler l\'étape 1'),
    execute: () => modal({ title: 'Étape 2 : déplacer les fichiers', danger: true, submit: 'Déplacer',
      body: `<p>${fmtNum(p.selected)} opération(s), environ ${fmtNum(p.data.ops.filter(o => o.selected).reduce((a, o) => a + (o.files || 0), 0))} fichier(s). Déroulement :</p>
        <ol style="margin:0 0 12px 18px"><li>cliché instantané du volume${p.data.options.vss_snapshot ? '' : ' (désactivé)'} et sauvegarde des droits ;</li><li>inventaire du dossier ;</li>
        <li>renommages, puis copie robocopy sans écrasement vers les dossiers cibles ;</li><li>vérification de chaque fichier, suppression des seules sources vérifiées ;</li><li>inventaire final, nouvelle analyse du dossier et des droits.</li></ol>
        <p class="small">Prévenez les utilisateurs : les fichiers ouverts pendant l'opération restent à leur place.</p>
        ${field('Confirmation', inp('confirm', '', 'text', 'placeholder="DÉPLACER" autocomplete="off"'), 'Saisissez DÉPLACER.', true)}`,
      onSubmit: async f => {
        const r = await api(`/api/restructure/${id}/execute`, { method: 'POST', body: { confirm: formData(f).confirm || '' } });
        setTimeout(() => follow(r.job_id, 'Déplacement vérifié').then(j => { if (j.status === 'done') toast(`${fmtNum(j.result.moved)} fichier(s) déplacé(s), ${fmtNum(j.result.kept)} laissé(s) en place`); }), 0);
      } }),
    rollback: () => modal({ title: 'Retour arrière', danger: true, submit: 'Revenir à l\'arborescence d\'origine',
      body: `<p>Chaque fichier déplacé regagne son emplacement d'origine (d'après le manifeste), avec les mêmes garanties : copie sans écrasement, vérification, suppression des seules copies vérifiées. Les renommages sont inversés et les dossiers créés retirés s'ils sont vides.</p>
        ${field('Confirmation', inp('confirm', '', 'text', 'placeholder="ANNULER" autocomplete="off"'), 'Saisissez ANNULER.', true)}`,
      onSubmit: async f => {
        const r = await api(`/api/restructure/${id}/rollback`, { method: 'POST', body: { confirm: formData(f).confirm || '' } });
        setTimeout(() => follow(r.job_id, 'Retour arrière'), 0);
      } }),
    delete: () => confirmBox('Supprimer ce plan ?', '<p>La proposition est supprimée (aucun fichier n\'est touché).</p>', async () => { await api(`/api/restructure/${id}`, { method: 'DELETE' }); location.hash = `#/share/${p.share_id}/architecture`; }, 'Supprimer'),
  };
  const bindHead = () => $$('[data-sact]').forEach(b => { b.onclick = guard(() => actions[b.dataset.sact]()); });
  header(); bindHead(); await render();
}
