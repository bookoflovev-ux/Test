/* VigieFS — réparation de documents : atelier, diagnostic, contrôle d'intégrité */
'use strict';

const FAMILY_LABEL = { word: 'Word', excel: 'Excel', powerpoint: 'PowerPoint', pdf: 'PDF', autre: 'Autre' };

function issuesList(issues) {
  if (!issues || !issues.length) return '<p class="small muted">Aucune anomalie détectée.</p>';
  return `<ul class="list-check issues">${issues.map(i => `<li>${esc(typeof i === 'string' ? i : i.message)}</li>`).join('')}</ul>`;
}

function repairResultCard(r, downloadHref) {
  const before = r.before || {};
  const out = r.output_name || (r.output ? r.output.split(/[\\/]/).pop() : null);
  return `<div class="result-card">
    <div class="rc-head">${fileIcon(before.name || out || '')}<div style="min-width:0;flex:1"><div class="strong ellipsis">${esc(before.name || '')}</div>
      <div class="cell-sub">${esc(FAMILY_LABEL[before.family] || before.format || '')} · ${fmtBytes(before.size)}</div></div>
      ${badge(DOC_STATUS, before.status)} ${icon('chevron', 'file-icon')} ${badge(REPAIR_STATUS, r.status)}</div>
    <div class="rc-body">
      <div><h3 style="margin-top:0">Diagnostic</h3>${issuesList(before.issues)}${before.recommendation && r.status === 'failed' ? `<div class="banner warning" style="margin-top:10px">${icon('info')}<span>${esc(before.recommendation)}</span></div>` : ''}</div>
      <div><h3 style="margin-top:0">Opérations effectuées</h3>${r.actions && r.actions.length ? `<ul class="list-check">${r.actions.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : `<p class="small muted">${esc(r.message || 'Aucune opération.')}</p>`}
        ${r.after ? `<p class="small" style="margin-top:10px">Contrôle du fichier produit : ${badge(DOC_STATUS, r.after.status)}</p>` : ''}
        ${out ? `<div class="btn-row" style="margin-top:12px">${downloadHref ? `<a class="btn primary" href="${downloadHref}">${icon('download')} Télécharger « ${esc(out)} »</a>` : `<span class="pill on">${esc(r.output_rel || out)}</span>`}</div>` : ''}
        ${r.status === 'failed' ? '<p class="small muted" style="margin-top:10px">Restaurez la dernière version saine depuis la sauvegarde Datto (Datto SIRIS / BCDR : restauration au niveau fichier).</p>' : ''}</div>
    </div></div>`;
}

/** Modale de diagnostic d'un fichier d'un dossier supervisé, avec réparation. */
async function openDiagnosis(share, path, onDone) {
  const form = modal({ title: 'Diagnostic du document', size: 'lg', noFooter: true, body: `<div id="diag-body"><div class="banner info"><span class="dot run"></span>Analyse de <span class="mono">${esc(path)}</span>…</div></div>` });
  const body = $('#diag-body', form);
  try {
    const d = await api(`/api/shares/${share.id}/diagnose?path=${encodeURIComponent(path)}`);
    const canRepair = can('operator') && ['corrupt', 'warning'].includes(d.status);
    body.innerHTML = `<div class="result-card"><div class="rc-head">${fileIcon(d.name)}<div style="flex:1;min-width:0"><div class="strong ellipsis">${esc(path)}</div><div class="cell-sub">${esc(FAMILY_LABEL[d.family] || d.format)} · ${fmtBytes(d.size)}${d.details && d.details.pages ? ` · ${d.details.pages} page(s)` : ''}</div></div>${badge(DOC_STATUS, d.status)}</div>
      <div class="rc-body"><div><h3 style="margin-top:0">Constat</h3>${issuesList(d.issues)}</div><div><h3 style="margin-top:0">Recommandation</h3><p>${esc(d.recommendation || (d.status === 'ok' ? 'Le document est sain.' : ''))}</p>
      ${canRepair ? `<div class="field" style="margin:12px 0"><label>Mode de réparation</label>${seg('mode', [['copy', 'Créer une copie réparée'], ['replace', 'Remplacer l\'original']], 'copy')}<span class="hint">« Remplacer » place l'original dans la corbeille VigieFS.</span></div>
        <button class="btn primary" type="button" id="do-repair">${icon('wrench')} Réparer</button>` : ''}</div></div></div><div id="repair-out" style="margin-top:14px"></div>`;
    const btn = $('#do-repair', body);
    if (btn) btn.onclick = guard(async () => {
      btn.disabled = true; btn.textContent = 'Réparation en cours…';
      const r = await api(`/api/shares/${share.id}/repair`, { method: 'POST', body: { path, mode: formData(form).mode || 'copy' } });
      $('#repair-out', body).innerHTML = repairResultCard(r, r.id && r.output ? `/api/repairs/${r.id}/download` : null);
      btn.textContent = 'Terminé';
      toast(REPAIR_STATUS[r.status] ? REPAIR_STATUS[r.status][1] : r.status, r.status === 'failed' ? 'error' : 'ok');
      onDone && onDone();
    });
  } catch (e) { body.innerHTML = `<div class="error-text">${esc(e.message)}</div>`; }
}

// ------------------------------------------------------------------ page Réparation
async function viewRepair(view) {
  setPage('Réparation de fichiers', 'Outils', '', 'Diagnostic et réparation des documents Word, Excel, PowerPoint et PDF endommagés.');
  const [caps, shares] = await Promise.all([api('/api/repair/capabilities'), api('/api/shares')]);
  view.innerHTML = `
    <div class="grid span-2-1">
      <div class="card">${cardHead('Atelier de réparation', 'Déposez un document reçu d\'un client (pièce jointe, clé USB…). L\'original n\'est jamais modifié.')}
        ${can('operator') ? `<label class="dropzone" id="drop"><input type="file" id="file" hidden>
          <div class="ic">${icon('upload')}</div><div class="strong">Glissez un fichier ici ou cliquez pour parcourir</div>
          <div class="small muted">Taille maximale configurable (Paramètres › Stockage)</div>
          <div class="formats">${['docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'pdf'].map(f => `<span class="pill">.${f}</span>`).join('')}</div></label>` : '<p class="muted">Rôle opérateur requis.</p>'}
        <div id="lab-result" style="margin-top:16px"></div></div>
      <div class="card">${cardHead('Moteurs de réparation')}
        <ul class="list-check" style="margin-bottom:14px">
          <li>Reconstruction d'archive Office (index détruit, fichier tronqué)</li>
          <li>Réparation XML (caractères invalides, balises, troncature)</li>
          <li>Cohérence du paquet (types, relations, liens cassés)</li>
          <li>PDF : xref, catalogue et pages reconstruits ${caps.pdf ? '' : '<span class="pill err">indisponible</span>'}</li>
          <li>Anciens formats .doc .xls .ppt ${caps.ole ? '' : '<span class="pill err">indisponible</span>'}</li>
          <li>Récupération du texte en dernier recours</li></ul>
        <dl class="kv"><dt>LibreOffice</dt><dd>${caps.libreoffice ? `<span class="sev ok">Détecté</span><div class="cell-sub mono">${esc(caps.libreoffice)}</div>` : '<span class="sev neutral">Absent</span><div class="cell-sub">Optionnel : améliore la réparation des anciens formats binaires.</div>'}</dd></dl>
        <h3>Contrôle d'intégrité d'un dossier</h3>
        <div class="btn-row">${sel('share', shares.map(s => [s.id, `${s.client_name} › ${s.name}`]), '', 'id="int-share" style="flex:1"')}<button class="btn" data-act="integrity" ${shares.length ? '' : 'disabled'}>${icon('shield')} Ouvrir</button></div></div>
    </div>
    <div class="card flush">${cardHead('Historique des réparations', 'Atelier et réparations effectuées dans les dossiers supervisés')}<div id="hist"></div></div>`;
  const loadHistory = async () => {
    const rows = await api('/api/repairs?limit=300');
    $('#hist', view).innerHTML = rows.length ? `<div class="table-wrap tall"><table><thead><tr><th>Date</th><th>Document</th><th>Origine</th><th>Résultat</th><th>Constat</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr><td class="nowrap">${fmtDate(r.created_at)}<div class="cell-sub">${esc(r.actor)}</div></td><td>${fileIcon(r.original_name)}<span class="strong">${esc(r.original_name)}</span>${r.rel_path ? `<div class="cell-sub mono">${esc(r.rel_path)}</div>` : ''}</td>
        <td>${r.source === 'upload' ? '<span class="pill blue">Atelier</span>' : `<span class="pill">${esc(r.share_name || 'Dossier')}</span>`}</td>
        <td>${badge(REPAIR_STATUS, r.status)}</td><td class="small" style="max-width:420px">${esc((r.issues[0] || r.message || '').slice(0, 140))}${r.issues.length > 1 ? ` <span class="muted">(+${r.issues.length - 1})</span>` : ''}</td>
        <td class="nowrap">${r.downloadable && can('operator') ? `<a class="btn sm" href="/api/repairs/${r.id}/download">${icon('download')} ${esc(r.output_name.length > 28 ? r.output_name.slice(0, 26) + '…' : r.output_name)}</a>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : emptyState('wrench', 'Aucune réparation', 'Les réparations effectuées apparaîtront ici.');
  };
  bindActions(view, { integrity: () => { location.hash = `#/share/${$('#int-share', view).value}/integrity`; } });
  const drop = $('#drop', view);
  if (drop) {
    const upload = guard(async file => {
      if (!file) return;
      $('#lab-result', view).innerHTML = `<div class="banner info"><span class="dot run"></span>Analyse et réparation de « ${esc(file.name)} » (${fmtBytes(file.size)})…</div>`;
      try {
        const r = await api(`/api/repair/upload?filename=${encodeURIComponent(file.name)}`, { method: 'POST', raw: file });
        $('#lab-result', view).innerHTML = repairResultCard(r, r.output ? `/api/repairs/${r.id}/download` : null);
        loadHistory();
      } catch (e) { $('#lab-result', view).innerHTML = `<div class="banner critical">${icon('alert')}${esc(e.message)}</div>`; }
    });
    $('#file', view).addEventListener('change', e => upload(e.target.files[0]));
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => upload(e.dataTransfer.files[0]));
  }
  await loadHistory();
}

// ------------------------------------------------------------------ onglet Intégrité d'un dossier
async function shareIntegrity(pane, s) {
  const render = async () => {
    const d = await api(`/api/shares/${s.id}/integrity`);
    pane.innerHTML = `<div class="card">${cardHead('Contrôle d\'intégrité des documents', 'Vérifie la structure de chaque document Word, Excel, PowerPoint et PDF du dossier.',
      can('operator') ? `<button class="btn primary" data-act="run" ${d.running || !s.path_available ? 'disabled' : ''}>${icon('shield')} ${d.last ? 'Relancer le contrôle' : 'Lancer le contrôle'}</button>` : '')}<div id="job-slot"></div></div><div id="int-res"></div>`;
    if (d.running) {
      const job = await watchJob(d.running.id, j => { const slot = $('#job-slot', pane); if (slot) slot.innerHTML = jobLine(j); });
      if (job) render();
      return;
    }
    if (!d.last) { $('#int-res', pane).innerHTML = `<div class="card">${emptyState('shield', 'Aucun contrôle effectué', 'Le contrôle détecte les documents corrompus avant que vos clients ne s\'en aperçoivent.')}</div>`; return; }
    const r = d.last.result;
    const c = r.counts;
    const bad = c.corrupt + c.irrecoverable;
    $('#int-res', pane).innerHTML = `
      <div class="tiles" style="margin:16px 0">
        ${tile({ label: 'Documents vérifiés', value: fmtNum(r.checked), ic: 'file', sub: `le ${fmtDate(d.last.finished_at)}${r.skipped_large ? ` · ${r.skipped_large} trop volumineux ignoré(s)` : ''}` })}
        ${tile({ label: 'Sains', value: fmtNum(c.ok + c.warning), ic: 'check', tone: 'good', sub: c.warning ? `${c.warning} avec anomalie mineure` : '' })}
        ${tile({ label: 'Corrompus réparables', value: fmtNum(c.corrupt), ic: 'wrench', tone: c.corrupt ? 'warning' : 'good' })}
        ${tile({ label: 'Irrécupérables', value: fmtNum(c.irrecoverable), ic: 'alert', tone: c.irrecoverable ? 'critical' : 'good', sub: c.irrecoverable ? 'restauration Datto requise' : '' })}
        ${tile({ label: 'Protégés par mot de passe', value: fmtNum(c.protected), ic: 'lock', tone: 'violet' })}
      </div>
      <div class="card flush">${cardHead('Documents à traiter', bad ? `${bad} document(s) endommagé(s)` : 'Aucun document endommagé',
        can('operator') && r.problems.some(p => p.repairable) ? `${seg('mode', [['copy', 'Copie réparée'], ['replace', 'Remplacer l\'original']], 'copy')}<button class="btn primary" data-act="batch" disabled>${icon('wrench')} Réparer la sélection</button>` : '')}
        ${r.problems.length ? `<div class="table-wrap tall"><table><thead><tr>${can('operator') ? '<th class="check"><input type="checkbox" data-all></th>' : ''}<th>Document</th><th>État</th><th>Constat</th><th class="num">Taille</th><th></th></tr></thead><tbody>
          ${r.problems.map(p => `<tr>${can('operator') ? `<td class="check">${p.repairable ? `<input type="checkbox" data-doc value="${esc(p.path)}">` : ''}</td>` : ''}
            <td class="path">${fileIcon(p.path)}${esc(p.path)}<div class="cell-sub">${esc(FAMILY_LABEL[p.family] || p.format)} · modifié le ${fmtDay(p.mtime)}</div></td>
            <td>${badge(DOC_STATUS, p.status)}</td><td class="small" style="max-width:460px">${esc(p.issues[0] || '')}${p.issues.length > 1 ? ` <span class="muted">(+${p.issues.length - 1})</span>` : ''}${p.status === 'irrecoverable' ? `<div class="cell-sub">${esc(p.recommendation)}</div>` : ''}</td>
            <td class="num">${fmtBytes(p.size)}</td><td><button class="btn sm" data-act="open" data-path="${esc(p.path)}">Détails</button></td></tr>`).join('')}</tbody></table></div>`
          : emptyState('check', 'Tous les documents sont sains', '', '', 'good')}</div>
      <div id="batch-res"></div>`;
  };
  const sync = () => { const b = $('[data-act=batch]', pane); if (b) b.disabled = !$$('[data-doc]:checked', pane).length; };
  pane.addEventListener('change', e => { if (e.target.matches('[data-all]')) $$('[data-doc]', pane).forEach(c => { c.checked = e.target.checked; }); sync(); });
  bindActions(pane, {
    run: async () => { await api(`/api/shares/${s.id}/integrity`, { method: 'POST' }); render(); },
    open: el => openDiagnosis(s, el.dataset.path),
    batch: () => {
      const paths = $$('[data-doc]:checked', pane).map(c => c.value);
      const mode = formData(pane.querySelector('#int-res')).mode || 'copy';
      confirmBox('Réparer les documents', `<p>${plural(paths.length, 'document sera réparé', 'documents seront réparés')} ${mode === 'replace' ? '<b>en remplaçant l\'original</b> (l\'original part dans la corbeille VigieFS)' : 'en créant une copie « (réparé) » à côté de l\'original'}.</p>`, async () => {
        const r = await api(`/api/shares/${s.id}/repair-batch`, { method: 'POST', body: { paths, mode } });
        const job = await watchJob(r.job_id, j => { $('#batch-res', pane).innerHTML = `<div style="margin-top:16px">${jobLine(j, 'Réparation en cours')}</div>`; });
        if (job.status !== 'done') { $('#batch-res', pane).innerHTML = `<div class="banner critical" style="margin-top:16px">${esc(job.error || job.status)}</div>`; return; }
        const x = job.result;
        $('#batch-res', pane).innerHTML = `<div class="card flush" style="margin-top:16px">${cardHead('Résultat de la réparation', `${x.repaired} réparé(s) · ${x.partial + x.text_salvaged} partiel(s) · ${x.failed} échec(s)`)}
          <div class="table-wrap"><table><tbody>${x.items.map(i => `<tr><td class="path">${fileIcon(i.path)}${esc(i.path)}${i.output ? `<div class="cell-sub">→ ${esc(i.output)}</div>` : ''}</td><td>${badge(REPAIR_STATUS, i.status)}</td><td class="small">${esc((i.actions || [])[0] || i.error || '')}</td></tr>`).join('')}</tbody></table></div></div>`;
      }, 'Réparer', false);
    },
  });
  await render();
}
