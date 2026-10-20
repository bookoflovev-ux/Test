/* VigieFS — optimisation du stockage : centre, nettoyage, doublons, archivage à froid, corbeille */
'use strict';

// ------------------------------------------------------------------ centre d'optimisation
async function viewOptimization(view) {
  setPage("Centre d'optimisation", 'Optimisation', '', "Récupérez de l'espace : fichiers inutiles, doublons et données froides à archiver.");
  const o = await api('/api/optimization');
  const t = o.totals;
  const gain = t.junk_size + t.dup_wasted + t.stale_size;
  const pct = t.total_size ? gain * 100 / t.total_size : 0;
  view.innerHTML = `
    <div class="tiles">
      ${tile({ label: 'Gain potentiel total', value: fmtBytes(gain), ic: 'sparkle', hero: true, sub: t.total_size ? `${fmtPct(pct, 0)} du volume supervisé (${fmtBytes(t.total_size)})` : 'aucune analyse disponible' })}
      ${tile({ label: 'Fichiers inutiles', value: fmtBytes(t.junk_size), ic: 'broom', tone: 'warning', sub: `${fmtNum(t.junk_count)} fichiers temporaires, sauvegardes auto…` })}
      ${tile({ label: 'Doublons', value: fmtBytes(t.dup_wasted), ic: 'copy', tone: 'violet', go: '#/duplicates', sub: 'espace occupé par les copies' })}
      ${tile({ label: 'Données froides', value: fmtBytes(t.stale_size), ic: 'archive', tone: 'aqua', go: '#/archive', sub: `${fmtNum(t.stale_count)} fichiers à archiver` })}
      ${tile({ label: 'Déjà archivé', value: fmtBytes(t.archived_size), ic: 'disk', tone: 'good', go: '#/archive', sub: 'libéré sur les serveurs' })}
      ${tile({ label: 'Corbeille VigieFS', value: fmtBytes(t.recycle_size), ic: 'trash', go: '#/recycle', sub: `${fmtNum(t.recycle_count)} élément(s) restaurables` })}
    </div>
    <div class="card flush">${cardHead('Potentiel par dossier', 'Basé sur la dernière analyse et la dernière recherche de doublons de chaque dossier')}
      <div class="table-wrap"><table><thead><tr><th>Dossier</th><th class="num">Volume</th><th class="num">Inutiles</th><th class="num">Doublons</th><th class="num">Données froides</th><th class="num">Gain potentiel</th><th style="min-width:160px">Répartition</th><th></th></tr></thead><tbody>
      ${o.shares.length ? o.shares.map(s => {
        const g = s.junk_size + (s.dup_wasted || 0) + s.stale_size;
        const seg = (v, color, label) => v ? `<span style="flex:${v};background:${color}" data-tip="${esc(label)} : <b>${esc(fmtBytes(v))}</b>"></span>` : '';
        return `<tr class="clickable" data-go="#/share/${s.id}/cleanup">
          <td><div class="cell-title">${esc(s.name)}</div><div class="cell-sub">${esc(s.client_name)} · ${s.scanned_at ? 'analysé ' + fmtAgo(s.scanned_at) : 'jamais analysé'}</div></td>
          <td class="num">${fmtBytes(s.size)}</td>
          <td class="num">${fmtBytes(s.junk_size)}<div class="cell-sub">${fmtNum(s.junk_count)} fichiers</div></td>
          <td class="num">${s.dup_wasted == null ? `<button class="btn sm ghost" data-act="dups" data-id="${s.id}">Rechercher</button>` : fmtBytes(s.dup_wasted) + `<div class="cell-sub">${fmtAgo(s.dup_at)}</div>`}</td>
          <td class="num">${fmtBytes(s.stale_size)}<div class="cell-sub">> ${fmtAge(s.stale_days)}</div></td>
          <td class="num strong">${fmtBytes(g)}</td>
          <td>${g ? `<div class="stack">${seg(s.junk_size, 'var(--warning)', 'Inutiles')}${seg(s.dup_wasted || 0, 'var(--violet)', 'Doublons')}${seg(s.stale_size, 'var(--aqua)', 'Données froides')}</div>` : '<span class="muted small">—</span>'}</td>
          <td class="nowrap"><a class="btn sm" href="#/share/${s.id}/cleanup">Optimiser →</a></td></tr>`;
      }).join('') : `<tr><td colspan="8">${emptyState('folder', 'Aucun dossier supervisé')}</td></tr>`}
      </tbody></table></div>
      <div class="legend" style="padding:0 20px 16px"><span><i style="background:var(--warning)"></i>Fichiers inutiles</span><span><i style="background:var(--violet)"></i>Doublons</span><span><i style="background:var(--aqua)"></i>Données froides</span></div></div>
    <div class="card"><div class="steps row">
      <div><div><b>Nettoyer</b><div class="small muted">Les fichiers temporaires, parasites et sauvegardes automatiques partent dans la corbeille VigieFS, restaurables pendant toute la durée de rétention.</div></div></div>
      <div><div><b>Dédoublonner</b><div class="small muted">Comparaison du contenu par empreinte SHA-256 : un exemplaire est conservé, les copies vont en corbeille après revérification.</div></div></div>
      <div><div><b>Archiver à froid</b><div class="small muted">Les données non utilisées migrent vers le stockage d'archive (copie vérifiée), un raccourci reste en place.</div></div></div>
    </div></div>`;
  rowLinks(view);
  bindActions(view, {
    dups: async el => { const r = await api(`/api/shares/${el.dataset.id}/duplicates`, { method: 'POST', body: { min_size_mb: 1 } }); toast('Recherche de doublons lancée'); location.hash = `#/share/${el.dataset.id}/duplicates`; return r; },
  });
}

// ------------------------------------------------------------------ nettoyage d'un dossier
async function shareCleanup(pane, s) {
  const data = await api(`/api/shares/${s.id}/junk`);
  const total = data.categories.reduce((a, c) => a + c.size, 0);
  const active = (await api(`/api/jobs?share_id=${s.id}&type=cleanup&status=active`))[0];
  pane.innerHTML = `
    <div class="small muted" id="scan-progress"></div>
    <div id="job-slot"></div>
    <div class="card">${cardHead('Fichiers inutiles', data.scanned_at ? `Détectés lors de l'analyse du ${fmtDate(data.scanned_at)} — les conditions sont revérifiées au moment du nettoyage.` : 'Aucune analyse : lancez une analyse pour détecter les fichiers inutiles.',
      `<span class="small muted">Récupérable : <b class="ink-2">${fmtBytes(total)}</b></span>`)}
      <form id="cleanup-form"><div class="cat-grid">${data.categories.map(c => `
        <label class="cat ${c.count ? '' : 'disabled'}"><input type="checkbox" name="${c.key}" ${c.count && c.key !== 'empty' ? 'checked' : ''} ${c.count ? '' : 'disabled'}>
          <span class="t">${esc(c.label)}</span><span class="v">${c.key === 'empty_dirs' ? fmtNum(c.count) : fmtBytes(c.size)}<small>${c.key === 'empty_dirs' ? 'dossier(s)' : plural(c.count, 'fichier', 'fichiers')}</small></span>
          <span class="d">${esc(c.description)}${c.samples && c.samples.length ? ` <a href="#" data-act="samples" data-key="${c.key}">Voir des exemples</a>` : ''}</span></label>`).join('')}</div>
        <div class="btn-row" style="margin-top:16px">${can('operator') ? `<button class="btn primary" type="submit" ${data.scanned_at ? '' : 'disabled'}>${icon('broom')} Envoyer la sélection à la corbeille</button>` : ''}
          <span class="small muted">Rien n'est supprimé définitivement : tout reste restaurable depuis la corbeille.</span></div></form></div>`;
  const slot = $('#job-slot', pane);
  const follow = async jobId => {
    const job = await watchJob(jobId, j => { slot.innerHTML = jobLine(j); });
    slot.innerHTML = job.status === 'done'
      ? `<div class="banner ok" style="margin-bottom:16px">${icon('check')}<span>${plural(job.result.moved, 'élément envoyé', 'éléments envoyés')} à la corbeille · ${fmtBytes(job.result.freed)} libérés${job.result.error_count ? ` · ${job.result.error_count} erreur(s)` : ''}</span><span class="spacer"></span><a class="btn sm" href="#/recycle">Voir la corbeille</a></div>`
      : `<div class="banner warning" style="margin-bottom:16px">${icon('alert')}Nettoyage ${JOB_STATUS[job.status][1].toLowerCase()} ${job.error ? ': ' + esc(job.error) : ''}</div>`;
    if (job.status === 'done') await api(`/api/shares/${s.id}/scan`, { method: 'POST' });
  };
  if (active) follow(active.id);
  bindActions(pane, {
    samples: el => {
      const c = data.categories.find(x => x.key === el.dataset.key);
      modal({ title: c.label, size: 'lg', noFooter: true, body: `<p class="small muted">Échantillon (100 au maximum) issu de la dernière analyse.</p>${fileTable(c.samples, { cols: c.key === 'empty_dirs' ? [] : ['size', 'mtime'] })}` });
    },
  });
  $('#cleanup-form', pane).addEventListener('submit', guard(async e => {
    e.preventDefault();
    const cats = Object.entries(formData(e.target)).filter(([, v]) => v).map(([k]) => k);
    if (!cats.length) return toast('Sélectionnez au moins une catégorie', 'error');
    const chosen = data.categories.filter(c => cats.includes(c.key));
    confirmBox('Confirmer le nettoyage', `<p>Les éléments suivants de <b>${esc(s.name)}</b> vont être déplacés vers la corbeille VigieFS :</p>
      <ul class="list-check">${chosen.map(c => `<li>${esc(c.label)} — ${c.key === 'empty_dirs' ? plural(c.count, 'dossier', 'dossiers') : fmtBytes(c.size)}</li>`).join('')}</ul>`,
    async () => { const r = await api(`/api/shares/${s.id}/cleanup`, { method: 'POST', body: { categories: cats } }); follow(r.job_id); }, 'Nettoyer', false);
  }));
}

// ------------------------------------------------------------------ doublons
function duplicatesResults(job, { showShare }) {
  const r = job.result;
  if (!r.groups.length) return emptyState('check', 'Aucun doublon', `Aucun fichier identique de plus de ${fmtBytes(r.min_size)} parmi ${fmtNum(r.files_scanned)} fichiers.`, '', 'good');
  return `<div class="tiles" style="margin-bottom:16px">
      ${tile({ label: 'Espace récupérable', value: fmtBytes(r.wasted_total), ic: 'sparkle', tone: 'good' })}
      ${tile({ label: 'Groupes de doublons', value: fmtNum(r.group_count), ic: 'copy', tone: 'violet', sub: `${fmtNum(r.groups.reduce((a, g) => a + g.count, 0))} fichiers concernés` })}
      ${tile({ label: 'Fichiers comparés', value: compact(r.files_scanned), ic: 'file', sub: `taille ≥ ${fmtBytes(r.min_size)} · ${fmtDate(job.finished_at)}` })}
    </div>
    <div class="card flush"><div class="filters" style="padding-bottom:14px;border-bottom:1px solid var(--border)">
      <span class="label-text">Exemplaire à conserver</span>${seg('strategy', [['oldest', 'Le plus ancien'], ['newest', 'Le plus récent'], ['shortest', 'Chemin le plus court']], 'oldest')}
      <span class="spacer"></span>${can('operator') ? '<button class="btn primary" data-act="resolve" disabled>Supprimer les copies sélectionnées</button>' : ''}</div>
    <div class="table-wrap tall"><table><thead><tr>${can('operator') ? '<th class="check"><input type="checkbox" data-all aria-label="Tout sélectionner"></th>' : ''}<th>Fichiers identiques</th><th class="num">Taille</th><th class="num">Copies</th><th class="num">Récupérable</th></tr></thead><tbody>
    ${r.groups.slice(0, 500).map(g => `<tr>${can('operator') ? `<td class="check"><input type="checkbox" data-group value="${g.hash}"></td>` : ''}
      <td>${g.files.map((f, i) => `<label class="check small" style="font-weight:450;margin:2px 0"><input type="radio" name="keep-${g.hash}" value="${f.share_id}:${esc(f.path)}" ${i === 0 ? 'data-default' : ''}>${fileIcon(f.path)}<span class="mono">${showShare ? `<b>${esc(f.share)}</b> / ` : ''}${esc(f.path)}</span> <span class="muted">· ${fmtDay(f.mtime)}</span></label>`).join('')}</td>
      <td class="num">${fmtBytes(g.size)}</td><td class="num">${g.count}</td><td class="num strong">${fmtBytes(g.wasted)}</td></tr>`).join('')}
    </tbody></table></div>${r.groups.length > 500 ? '<div class="small muted" style="padding:10px 20px">500 premiers groupes affichés (les plus volumineux).</div>' : ''}</div>
    <p class="small muted" style="margin-top:10px">Cochez un bouton radio pour forcer l'exemplaire conservé d'un groupe ; sinon la règle choisie ci-dessus s'applique. Chaque copie est revérifiée (SHA-256) avant d'être envoyée à la corbeille.</p>`;
}

function bindDuplicates(root, job, reload) {
  const sync = () => { const b = $('[data-act=resolve]', root); if (b) b.disabled = !$$('[data-group]:checked', root).length; };
  const applyStrategy = () => {
    const strategy = (formData(root).strategy) || 'oldest';
    for (const g of job.result.groups.slice(0, 500)) {
      const files = [...g.files];
      const keep = strategy === 'newest' ? files.reduce((a, b) => (b.mtime > a.mtime ? b : a)) : strategy === 'shortest' ? files.reduce((a, b) => (b.path.length < a.path.length ? b : a)) : files.reduce((a, b) => (b.mtime < a.mtime ? b : a));
      const radio = $$(`input[name="keep-${g.hash}"]`, root).find(r => r.value === `${keep.share_id}:${keep.path}`);
      if (radio && !radio.dataset.manual) radio.checked = true;
    }
  };
  root.addEventListener('change', e => {
    if (e.target.matches('[data-all]')) $$('[data-group]', root).forEach(c => { c.checked = e.target.checked; });
    if (e.target.name === 'strategy') { $$('input[type=radio][data-manual]', root).forEach(r => delete r.dataset.manual); applyStrategy(); }
    if (e.target.name && e.target.name.startsWith('keep-')) $$(`input[name="${e.target.name}"]`, root).forEach(r => { r.dataset.manual = '1'; });
    sync();
  });
  applyStrategy();
  return {
    resolve: async () => {
      const hashes = $$('[data-group]:checked', root).map(c => c.value);
      const groups = job.result.groups.filter(g => hashes.includes(g.hash));
      const freed = groups.reduce((a, g) => a + g.wasted, 0);
      const keep = {};
      hashes.forEach(h => { const r = $(`input[name="keep-${h}"]:checked`, root); if (r) keep[h] = r.value; });
      confirmBox('Supprimer les copies', `<p>${plural(groups.length, 'groupe', 'groupes')} sélectionné(s) : ${plural(groups.reduce((a, g) => a + g.count - 1, 0), 'copie sera envoyée', 'copies seront envoyées')} à la corbeille, soit <b>${fmtBytes(freed)}</b> libérés.</p><p class="small muted">Un exemplaire de chaque fichier est conservé. Les copies modifiées depuis l'analyse sont ignorées.</p>`,
        async () => {
          const r = await api(`/api/jobs/${job.id}/resolve-duplicates`, { method: 'POST', body: { strategy: formData(root).strategy || 'oldest', hashes, keep } });
          const done = await watchJob(r.job_id, j => { const b = $('[data-act=resolve]', root); if (b) { b.disabled = true; b.textContent = `Traitement… ${j.progress.done || 0}/${j.progress.total || '?'}`; } });
          toast(done.status === 'done' ? `${done.result.removed} copie(s) envoyée(s) à la corbeille, ${fmtBytes(done.result.freed)} libérés${done.result.skipped_count ? ` — ${done.result.skipped_count} ignorée(s)` : ''}` : 'Échec de la suppression des doublons', done.status === 'done' ? 'ok' : 'error');
          reload(job.params.min_size_mb);
        }, 'Supprimer les copies', false);
    },
  };
}

async function duplicatesPanel(pane, { launchUrl, listQuery, showShare, title, canLaunch = true }) {
  const render = async () => {
    const jobsList = await api(`/api/jobs?type=duplicates&${listQuery}&limit=20`);
    const active = jobsList.find(j => ['queued', 'running'].includes(j.status));
    const last = jobsList.find(j => j.status === 'done');
    pane.innerHTML = `<div class="card" style="margin-bottom:16px">${cardHead(title, 'Fichiers au contenu strictement identique (comparaison SHA-256), quel que soit leur nom.',
      can('operator') && canLaunch ? `<label class="small">Taille min. <input type="number" id="minsize" value="1" min="0" step="0.5" style="width:80px;display:inline-block"> Mo</label><button class="btn primary" data-act="launch" ${active ? 'disabled' : ''}>${icon('search')} Lancer la recherche</button>` : '')}
      <div id="job-slot"></div></div><div id="results"></div>`;
    if (active) {
      const job = await watchJob(active.id, j => { $('#job-slot', pane).innerHTML = jobLine(j); });
      if (job) render();
      return;
    }
    if (!last) { $('#results', pane).innerHTML = `<div class="card">${emptyState('copy', 'Aucune recherche effectuée', 'Lancez une recherche pour identifier les fichiers en double.')}</div>`; return; }
    const job = await api(`/api/jobs/${last.id}`);
    const res = $('#results', pane);
    res.innerHTML = duplicatesResults(job, { showShare });
    // Après suppression des copies, une nouvelle recherche rafraîchit les résultats.
    const relaunch = async minSize => { await api(launchUrl, { method: 'POST', body: { min_size_mb: minSize ?? 1 } }); render(); };
    const acts = bindDuplicates(res, job, relaunch);
    bindActions(res, acts);
  };
  bindActions(pane, {
    launch: async () => { await api(launchUrl, { method: 'POST', body: { min_size_mb: Number($('#minsize', pane).value || 1) } }); toast('Recherche lancée'); render(); },
  });
  await render();
}

async function shareDuplicates(pane, s) {
  await duplicatesPanel(pane, { launchUrl: `/api/shares/${s.id}/duplicates`, listQuery: `share_id=${s.id}`, showShare: false, title: 'Doublons dans ce dossier' });
}

async function viewDuplicates(view, query) {
  setPage('Doublons', 'Optimisation', '', 'Recherchez les doublons dans un dossier ou sur l\'ensemble des dossiers d\'un client.');
  const clients = await api('/api/clients');
  const cid = query.get('client') || (clients[0] && clients[0].id) || '';
  view.innerHTML = `<div class="filters" style="margin-bottom:16px"><span class="label-text">Client</span>${sel('client', clients.map(c => [c.id, `${c.name} (${c.share_count} dossier${c.share_count > 1 ? 's' : ''})`]), cid, 'id="dup-client"')}
    <span class="small muted">La recherche porte sur tous les dossiers supervisés du client, y compris entre dossiers différents.</span></div><div id="dup-pane"></div>`;
  $('#dup-client', view).addEventListener('change', e => { location.hash = `#/duplicates?client=${e.target.value}`; });
  if (!clients.length) { $('#dup-pane', view).innerHTML = `<div class="card">${emptyState('clients', 'Aucun client')}</div>`; return; }
  const client = clients.find(c => String(c.id) === String(cid));
  await duplicatesPanel($('#dup-pane', view), { launchUrl: `/api/clients/${cid}/duplicates`, listQuery: `client_id=${cid}&no_share=1`, showShare: true, title: `Doublons — ${client ? client.name : ''}` });
}

// ------------------------------------------------------------------ archivage à froid
async function shareArchive(pane, s) {
  const [items, opt] = await Promise.all([api(`/api/archive/items?share_id=${s.id}&limit=1000`), api('/api/optimization')]);
  const root = opt.archive_root;
  pane.innerHTML = `
    ${root ? '' : `<div class="banner warning" style="margin-bottom:16px">${icon('alert')}<span>Aucun stockage d'archive configuré : la simulation est possible, pas l'archivage.</span><span class="spacer"></span>${can('admin') ? '<a class="btn sm" href="#/settings/storage">Configurer</a>' : ''}</div>`}
    <form class="card form" id="policy">${cardHead('Règle d\'archivage', root ? `Destination : <span class="mono">${esc(root)}</span>` : 'Destination non configurée')}
      ${archivePolicyFields(s, true)}
      <div class="btn-row">${can('operator') ? `<button class="btn primary" type="button" data-act="simulate">${icon('eye')} Simuler</button><button class="btn" type="button" data-act="save">Enregistrer la règle</button>` : ''}</div></form>
    <div class="card" id="sim">${cardHead('Simulation', 'Estimez le gain avant de déplacer quoi que ce soit.')}<div id="sim-body">${emptyState('archive', 'Aucune simulation', 'Ajustez la règle puis cliquez sur « Simuler » : aucun fichier n\'est déplacé.')}</div></div>
    <div class="card flush">${cardHead('Fichiers archivés', `${plural(items.length, 'fichier', 'fichiers')} · ${fmtBytes(items.reduce((a, i) => a + i.size, 0))}`, can('operator') ? '<button class="btn" data-act="restore" disabled>' + icon('restore') + ' Restaurer la sélection</button>' : '')}
      ${archivedTable(items, false)}</div>`;
  const policy = () => { const d = formData($('#policy', pane)); return { age_days: d.archive_age_days, min_size_kb: d.archive_min_size_kb, extensions: d.archive_extensions, exclude: d.archive_exclude, criterion: d.archive_criterion, stub: d.archive_stub }; };
  const showSim = r => {
    const vmNote = r.vm_protected ? `<div class="banner info" style="margin-bottom:12px">${icon('shield')}<span>${fmtNum(r.vm_protected)} fichier(s) de machine virtuelle (disques, configurations) protégés : jamais archivés.</span></div>` : '';
    $('#sim-body', pane).innerHTML = vmNote + (r.count ? `
      <div class="tiles" style="margin-bottom:16px">${tile({ label: 'Espace libérable', value: fmtBytes(r.size), ic: 'sparkle', tone: 'good', sub: s.last_size ? `${fmtPct(r.size * 100 / s.last_size, 0)} du dossier` : '' })}${tile({ label: 'Fichiers concernés', value: fmtNum(r.count), ic: 'file', tone: 'aqua', sub: r.oldest ? `le plus ancien : ${fmtDay(r.oldest)}` : '' })}</div>
      <div class="grid cols-2"><div><h3>Par type</h3>${hbars(r.by_extension.slice(0, 8).map(e => ({ name: e.name, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div>
      <div><h3>Par sous-dossier</h3>${hbars(r.by_folder.slice(0, 8).map(e => ({ name: e.name, value: e.size, count: e.count })), { sub: i => `${fmtNum(i.count)} fichiers` })}</div></div>
      <h3>Plus gros fichiers concernés</h3>${fileTable(r.samples.slice(0, 50))}
      ${can('operator') ? `<div class="btn-row" style="margin-top:14px"><button class="btn primary" data-act="run" ${root ? '' : 'disabled'}>${icon('archive')} Archiver ces ${fmtNum(r.count)} fichiers</button></div>` : ''}`
      : emptyState('check', 'Rien à archiver', 'Aucun fichier ne correspond à cette règle.', '', 'good'));
  };
  const syncRestore = () => { const b = $('[data-act=restore]', pane); if (b) b.disabled = !$$('[data-item]:checked', pane).length; };
  pane.addEventListener('change', e => { if (e.target.matches('[data-all]')) $$('[data-item]', pane).forEach(c => { c.checked = e.target.checked; }); syncRestore(); });
  bindActions(pane, {
    save: async () => { await api(`/api/shares/${s.id}`, { method: 'PUT', body: shareBody($('#policy', pane), s) }); toast('Règle enregistrée'); },
    simulate: async () => {
      const r = await api(`/api/shares/${s.id}/archive/simulate`, { method: 'POST', body: policy() });
      const job = await watchJob(r.job_id, j => { $('#sim-body', pane).innerHTML = jobLine(j, 'Simulation en cours'); });
      if (job.status === 'done') showSim(job.result); else $('#sim-body', pane).innerHTML = `<div class="error-text">${esc(job.error || 'Simulation interrompue')}</div>`;
    },
    run: () => confirmBox('Lancer l\'archivage', `<p>Les fichiers vont être <b>copiés</b> vers <span class="mono">${esc(root)}</span>, vérifiés (taille + SHA-256), puis retirés du serveur${formData($('#policy', pane)).archive_stub ? ' et remplacés par un raccourci' : ''}.</p><p class="small muted">Restauration possible à tout moment depuis cet onglet ou la page Archivage à froid.</p>`, async () => {
      const r = await api(`/api/shares/${s.id}/archive/run`, { method: 'POST', body: policy() });
      const job = await watchJob(r.job_id, j => { $('#sim-body', pane).innerHTML = jobLine(j, 'Archivage en cours'); });
      toast(job.status === 'done' ? `${job.result.archived} fichier(s) archivé(s), ${fmtBytes(job.result.size)} libérés` : `Archivage : ${job.error || job.status}`, job.status === 'done' ? 'ok' : 'error');
      route();
    }, 'Archiver', false),
    restore: () => restoreArchived(pane),
  });
}

function archivedTable(items, showShare) {
  if (!items.length) return emptyState('archive', 'Aucun fichier archivé');
  return `<div class="table-wrap tall"><table><thead><tr>${can('operator') ? '<th class="check"><input type="checkbox" data-all></th>' : ''}<th>Fichier</th>${showShare ? '<th>Client / dossier</th>' : ''}<th class="num">Taille</th><th>Modifié</th><th>Archivé</th></tr></thead><tbody>
    ${items.map(i => `<tr>${can('operator') ? `<td class="check"><input type="checkbox" data-item value="${i.id}"></td>` : ''}<td class="path">${fileIcon(i.rel_path)}${esc(i.rel_path)}<div class="cell-sub" title="${esc(i.archive_path)}">→ ${esc(i.archive_path)}</div></td>
      ${showShare ? `<td>${esc(i.client_name || '')}<div class="cell-sub">${esc(i.share_name || '')}</div></td>` : ''}<td class="num">${fmtBytes(i.size)}</td><td class="nowrap">${fmtDay(i.mtime)}</td><td class="nowrap">${fmtDate(i.archived_at)}<div class="cell-sub">${esc(i.archived_by)}</div></td></tr>`).join('')}</tbody></table></div>`;
}
async function restoreArchived(root) {
  const ids = $$('[data-item]:checked', root).map(c => Number(c.value));
  if (!ids.length) return;
  confirmBox('Restaurer depuis l\'archive', `${plural(ids.length, 'fichier sera recopié', 'fichiers seront recopiés')} à leur emplacement d'origine (avec un suffixe si un fichier du même nom existe).`, async () => {
    const r = await api('/api/archive/restore', { method: 'POST', body: { ids } });
    const job = await watchJob(r.job_id);
    toast(job.status === 'done' ? `${job.result.restored} fichier(s) restauré(s)` : 'Échec de la restauration', job.status === 'done' ? 'ok' : 'error');
    route();
  }, 'Restaurer', false);
}

async function viewArchive(view) {
  setPage('Archivage à froid', 'Optimisation', '', 'Déplacez les données inutilisées vers un stockage économique, sans les perdre.');
  const [o, shares, items] = await Promise.all([api('/api/optimization'), api('/api/shares'), api('/api/archive/items?limit=2000')]);
  view.innerHTML = `
    ${o.archive_root ? `<div class="banner info">${icon('archive')}<span>Stockage d'archive : <span class="mono">${esc(o.archive_root)}</span></span><span class="spacer"></span>${can('admin') ? '<a class="btn sm" href="#/settings/storage">Modifier</a>' : ''}</div>`
      : `<div class="banner warning">${icon('alert')}<span>Aucun stockage d'archive configuré (NAS, disque d'archive, partage réseau…).</span><span class="spacer"></span>${can('admin') ? '<a class="btn sm" href="#/settings/storage">Configurer</a>' : ''}</div>`}
    <div class="tiles" style="margin-top:16px">
      ${tile({ label: 'Données archivées', value: fmtBytes(o.totals.archived_size), ic: 'archive', tone: 'good', sub: `${fmtNum(items.length)} fichiers libérés des serveurs` })}
      ${tile({ label: 'Données froides restantes', value: fmtBytes(o.totals.stale_size), ic: 'disk', tone: 'aqua', sub: `${fmtNum(o.totals.stale_count)} fichiers candidats` })}
      ${tile({ label: 'Dossiers en archivage auto', value: fmtNum(shares.filter(s => s.archive_enabled).length), ic: 'refresh', sub: `sur ${shares.length} dossier(s)` })}
    </div>
    <div class="card flush">${cardHead('Règles par dossier')}<div class="table-wrap"><table><thead><tr><th>Dossier</th><th>Archivage auto</th><th>Règle</th><th>Dernier passage</th><th class="num">Données froides</th><th></th></tr></thead><tbody>
      ${shares.map(s => { const os = o.shares.find(x => x.id === s.id) || {}; return `<tr class="clickable" data-go="#/share/${s.id}/archive"><td><div class="cell-title">${esc(s.name)}</div><div class="cell-sub">${esc(s.client_name)}</div></td>
        <td>${s.archive_enabled ? `<span class="sev ok">Activé</span><div class="cell-sub">tous les ${s.archive_interval_days} j</div>` : '<span class="sev neutral">Manuel</span>'}</td>
        <td class="small">Inutilisé depuis ${fmtAge(s.archive_age_days)}${s.archive_extensions ? ` · ${esc(s.archive_extensions)}` : ''}${s.archive_min_size_kb ? ` · ≥ ${fmtBytes(s.archive_min_size_kb * 1024)}` : ''}</td>
        <td>${fmtAgo(s.last_archive_at)}</td><td class="num">${fmtBytes(os.stale_size || 0)}</td><td><a class="btn sm" href="#/share/${s.id}/archive">Gérer →</a></td></tr>`; }).join('') || `<tr><td colspan="6">${emptyState('folder', 'Aucun dossier')}</td></tr>`}
    </tbody></table></div></div>
    <div class="card flush">${cardHead('Fichiers archivés', 'Recherchez et restaurez un fichier archivé', `<input type="search" id="aq" placeholder="Rechercher un fichier…" style="width:240px">${can('operator') ? `<button class="btn" data-act="restore" disabled>${icon('restore')} Restaurer la sélection</button>` : ''}`)}
      <div id="arch-list">${archivedTable(items, true)}</div></div>`;
  rowLinks(view);
  const sync = () => { const b = $('[data-act=restore]', view); if (b) b.disabled = !$$('[data-item]:checked', view).length; };
  view.addEventListener('change', e => { if (e.target.matches('[data-all]')) $$('[data-item]', view).forEach(c => { c.checked = e.target.checked; }); sync(); });
  $('#aq', view).addEventListener('input', guard(async e => { $('#arch-list', view).innerHTML = archivedTable(await api('/api/archive/items?limit=2000&q=' + encodeURIComponent(e.target.value)), true); sync(); }));
  bindActions(view, { restore: () => restoreArchived(view) });
}

// ------------------------------------------------------------------ corbeille
async function viewRecycle(view) {
  setPage('Corbeille VigieFS', 'Optimisation', '', 'Tout ce que VigieFS retire (nettoyage, doublons, suppressions, remplacements) est conservé ici avant purge.');
  const shares = await api('/api/shares');
  view.innerHTML = `<div id="rc-banner"></div><div class="card flush"><div class="filters">
    ${sel('share_id', [['', 'Tous les dossiers'], ...shares.map(s => [s.id, `${s.client_name} › ${s.name}`])], '', 'data-f')}
    ${sel('status', [['stored', 'En corbeille'], ['restored', 'Restaurés'], ['purged', 'Purgés']], 'stored', 'data-f')}
    <input type="search" name="q" placeholder="Rechercher (chemin, motif)…" data-f>
    <span class="spacer"></span>${can('operator') ? `<button class="btn" data-act="restore" disabled>${icon('restore')} Restaurer</button>` : ''}${can('admin') ? ` <button class="btn danger" data-act="purge" disabled>${icon('trash')} Purger définitivement</button>` : ''}
  </div><div id="rc-list" style="margin-top:14px"></div></div>`;
  const sync = () => { const n = $$('[data-item]:checked', view).length; $$('[data-act=restore],[data-act=purge]', view).forEach(b => { b.disabled = !n; }); };
  const load = async () => {
    const p = new URLSearchParams();
    $$('[data-f]', view).forEach(el => { if (el.value) p.set(el.name, el.value); });
    const d = await api('/api/recycle?' + p);
    const total = d.items.reduce((a, i) => a + (i.size || 0), 0);
    $('#rc-banner', view).innerHTML = `<div class="tiles" style="margin-bottom:16px">${tile({ label: 'Éléments affichés', value: fmtNum(d.items.length), ic: 'trash' })}${tile({ label: 'Volume', value: fmtBytes(total), ic: 'disk', tone: 'violet' })}${tile({ label: 'Rétention', value: d.retention_days ? d.retention_days + ' jours' : 'Illimitée', ic: 'restore', tone: 'aqua', sub: 'purge automatique ensuite' })}</div>`;
    const stored = p.get('status') !== 'restored' && p.get('status') !== 'purged';
    $('#rc-list', view).innerHTML = d.items.length ? `<div class="table-wrap tall"><table><thead><tr>${stored && can('operator') ? '<th class="check"><input type="checkbox" data-all></th>' : ''}<th>Élément</th><th>Client / dossier</th><th>Motif</th><th class="num">Taille</th><th>Supprimé</th><th>${stored ? 'Purge prévue' : 'Le'}</th></tr></thead><tbody>
      ${d.items.map(i => `<tr>${stored && can('operator') ? `<td class="check"><input type="checkbox" data-item value="${i.id}"></td>` : ''}<td class="path">${i.is_dir ? icon('folder', 'file-icon dir') : fileIcon(i.rel_path)}${esc(i.rel_path)}${i.restored_path ? `<div class="cell-sub">→ ${esc(i.restored_path)}</div>` : ''}</td>
        <td>${esc(i.client_name || '—')}<div class="cell-sub">${esc(i.share_name || '')}</div></td><td><span class="pill">${esc(i.reason)}</span></td>
        <td class="num">${i.is_dir && !i.size ? '—' : fmtBytes(i.size)}</td><td class="nowrap">${fmtDate(i.deleted_at)}<div class="cell-sub">${esc(i.deleted_by)}</div></td>
        <td class="nowrap">${stored ? (i.expires_at ? fmtDay(i.expires_at) : '—') : fmtDate(i.restored_at)}</td></tr>`).join('')}</tbody></table></div>`
      : emptyState('trash', 'Corbeille vide', 'Aucun élément pour ces critères.');
    sync();
  };
  view.addEventListener('change', e => { if (e.target.matches('[data-all]')) $$('[data-item]', view).forEach(c => { c.checked = e.target.checked; }); if (e.target.matches('[data-f]')) load(); sync(); });
  $('[name=q]', view).addEventListener('input', guard(load));
  bindActions(view, {
    restore: async () => { const ids = $$('[data-item]:checked', view).map(c => Number(c.value)); const r = await api('/api/recycle/restore', { method: 'POST', body: { ids } }); toast(`${r.restored} élément(s) restauré(s)${r.errors.length ? ` — ${r.errors.length} erreur(s)` : ''}`, r.errors.length ? 'error' : 'ok'); load(); },
    purge: () => { const ids = $$('[data-item]:checked', view).map(c => Number(c.value)); confirmBox('Purger définitivement', `${plural(ids.length, 'élément sera supprimé', 'éléments seront supprimés')} <b>définitivement</b>. Cette action est irréversible.`, async () => { const r = await api('/api/recycle/purge', { method: 'POST', body: { ids } }); toast(`${r.purged} élément(s) purgé(s)`); load(); }, 'Purger'); },
  });
  await load();
}
