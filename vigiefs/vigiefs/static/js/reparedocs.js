/* VigieFS — moteur RepareDocs : lancement (masse / individuel), suivi, résultats, rapport, remise en place. */
'use strict';

const RD_GROUPS = [
  { key: 'repare', label: 'Réparés', tone: 'ok', st: ['REPARE'] },
  { key: 'partiel', label: 'Réparés partiellement', tone: 'warning', st: ['REPARE_PARTIEL'] },
  { key: 'irrecup', label: 'Irrécupérables', tone: 'critical', st: ['IRRECUPERABLE', 'ILLISIBLE'] },
  { key: 'attente', label: 'À traiter', tone: 'warning', st: ['ENDOMMAGE'] },
  { key: 'sain', label: 'Sains', tone: 'neutral', st: ['OK', 'OK_ANOMALIE'] },
  { key: 'autre', label: 'Autres', tone: 'neutral', st: ['PROTEGE', 'VIDE', 'ERREUR'] },
];
const RD_LABEL = { OK: 'Sain', OK_ANOMALIE: 'Sain (anomalie)', REPARE: 'Réparé', REPARE_PARTIEL: 'Partiel', IRRECUPERABLE: 'Irrécupérable', ILLISIBLE: 'Illisible', ENDOMMAGE: 'Endommagé', PROTEGE: 'Protégé', VIDE: 'Vide', ERREUR: 'Erreur' };
const RD_RUN_STATUS = { running: ['info', 'En cours'], done: ['ok', 'Terminé'], stopped: ['warning', 'Interrompu'], failed: ['critical', 'Échec'] };
const RD_KIND = { masse: 'Réparation en masse', individuel: 'Réparation individuelle', restaurer: 'Remise en place' };
const rdTone = s => (RD_GROUPS.find(g => g.st.includes(s)) || RD_GROUPS[5]).tone;
const rdBadge = s => `<span class="sev ${rdTone(s)}">${esc(RD_LABEL[s] || s)}</span>`;
const rdCounts = c => { const g = {}; RD_GROUPS.forEach(x => { g[x.key] = x.st.reduce((a, s) => a + ((c || {})[s] || 0), 0); }); return g; };

let rdInfoCache = null;
async function rdInfo(refresh = false) {
  if (!rdInfoCache || refresh) rdInfoCache = await api('/api/reparedocs/info' + (refresh ? '?refresh=true' : ''));
  return rdInfoCache;
}

/** Bloc « moteur RepareDocs » de la page Réparation. */
function rdEngineBlock(info) {
  if (!info.available && info.dir) {
    return `<div class="banner critical">${icon('alert')}<div><b>RepareDocs détecté mais inutilisable</b>
      <span class="cell-sub mono">${esc(info.dir)}</span><div>${esc(info.error || '')}</div></div></div>`;
  }
  if (!info.available) {
    return `<div class="banner warning">${icon('info')}<div><b>RepareDocs non détecté.</b> ${esc(info.error || '')}
      Installez RepareDocs (dossier contenant <code>runtime\\python.exe</code> et <code>app\\RepareDocs.py</code>, par exemple <code>C:\\RepareDocs</code>)
      ou indiquez son dossier dans Paramètres › Stockage & réparation.</div></div>`;
  }
  const tools = info.tools || {};
  return `<dl class="kv">
    <dt>Version</dt><dd>RepareDocs ${esc(info.version || '?')} <span class="cell-sub mono">${esc(info.dir || '')}</span></dd>
    <dt>Droits</dt><dd>${info.admin ? '<span class="sev ok">Administrateur</span>' : '<span class="sev warning">Sans droits administrateur</span><div class="cell-sub">La relecture directe du disque (données décalées) est indisponible.</div>'}</dd>
    <dt>Outils</dt><dd><span class="pill ${tools.libreoffice ? 'on' : ''}">LibreOffice ${tools.libreoffice ? '✓' : '—'}</span> <span class="pill ${tools.ghostscript ? 'on' : ''}">Ghostscript ${tools.ghostscript ? '✓' : '—'}</span> <span class="pill ${tools.office && Object.keys(tools.office).length && !info.session0 ? 'on' : ''}">Microsoft Office ${tools.office && Object.keys(tools.office).length && !info.session0 ? '✓' : '—'}</span></dd>
  </dl>`;
}

/** Fenêtre de lancement d'une réparation en masse (dossier supervisé, sous-dossier ou chemin libre). */
async function rdLaunch({ shareId = null, path = '' } = {}) {
  const [info, shares] = await Promise.all([rdInfo(), api('/api/shares')]);
  if (!info.available) return toast(info.error || 'RepareDocs est introuvable : voir Paramètres › Stockage & réparation', 'error');
  const d = info.defaults || {};
  const sw = (n, label, hint, on = true) => `<label class="check" style="align-items:flex-start"><input type="checkbox" name="${n}"${on ? ' checked' : ''}> <span>${label}${hint ? `<span class="cell-sub" style="display:block;font-weight:400">${hint}</span>` : ''}</span></label>`;
  modal({ title: 'Réparation en masse avec RepareDocs', size: 'lg', submit: 'Démarrer',
    body: `<div class="form-grid">
      ${field('Dossier supervisé', sel('share_id', shares.map(s => [s.id, `${s.client_name} › ${s.name}`]), shareId || (shares[0] && shares[0].id), 'id="rd-share"'))}
      ${field('Sous-dossier (facultatif)', inp('path', path, 'text', 'placeholder="ex. Comptabilité/2024"'), 'Relatif au dossier supervisé ; vide = tout le dossier.')}
      ${can('admin') ? field('Dossier de sortie (facultatif)', inp('output', '', 'text', 'placeholder="sur un autre disque de préférence, ex. E:\\RepareDocs\\Sorties"'), 'Vide = dossier de sortie par défaut de VigieFS. Ne jamais choisir un dossier dans le dossier analysé.', true) : ''}
      <div class="field full"><label>Types de fichiers</label><div class="btn-row">${[['pdf', 'PDF'], ['word', 'Word'], ['excel', 'Excel'], ['powerpoint', 'PowerPoint']].map(([k, l]) => `<label class="check"><input type="checkbox" name="kind_${k}" checked> ${l}</label>`).join('')}</div></div>
      <div class="field full"><label>Mode</label>${seg('mode', [['repair', 'Analyse et réparation'], ['analyse', 'Analyse seule (aucune réparation)']], 'repair')}</div>
      <div class="field full"><label>Moteurs</label><div class="grid cols-2" style="gap:6px 20px">
        ${sw('use_disk', 'Récupération sur le disque', info.admin ? 'Relit les données décalées directement sur le disque (lecture seule).' : 'Exige les droits administrateur du moteur.', !!info.admin)}
        ${sw('use_vss', 'Versions précédentes Windows', 'Cherche une copie saine dans les clichés instantanés.')}
        ${sw('use_office', 'Microsoft Office', 'Vérifie chaque fichier réparé, tente « Ouvrir et réparer ».', !info.session0)}
        ${sw('use_lo', 'LibreOffice', 'Réenregistre les anciens .doc / .xls / .ppt refusés par Office.')}
        ${sw('use_gs', 'Ghostscript', 'Moteur PDF supplémentaire, s\'il est installé.')}
        ${sw('resume', 'Reprendre un traitement interrompu', '')}
        ${sw('deep', 'Analyse approfondie des PDF', 'Plus lent.', false)}
        ${sw('office_check_legacy', 'Contrôler les .doc/.xls/.ppt sains par Office', 'Très lent.', false)}
      </div></div>
      ${field('Fichiers traités en parallèle', inp('workers', d.workers || 4, 'number', 'min="1" max="32"'))}
      ${field('Relectures disque en parallèle', inp('disk_workers', d.disk_workers || 3, 'number', 'min="1" max="8"'))}
      ${field('Rayon de recherche disque (clusters)', inp('disk_search', 512, 'number', 'min="64" max="65536" step="64"'))}
    </div>
    <p class="small muted" style="margin:12px 0 0">Les originaux ne sont jamais modifiés : les fichiers réparés sont produits dans le dossier de sortie (1-REPARES), avec un rapport. La remise en place est une étape séparée, sur confirmation.</p>`,
    onSubmit: async f => {
      const v = formData(f);
      const opts = { kinds: ['pdf', 'word', 'excel', 'powerpoint'].filter(k => v['kind_' + k]), analyse_only: v.mode === 'analyse',
        workers: Number(v.workers), disk_workers: Number(v.disk_workers), disk_search: Number(v.disk_search) };
      ['use_disk', 'use_vss', 'use_office', 'use_lo', 'use_gs', 'resume', 'deep', 'office_check_legacy'].forEach(k => { opts[k] = !!v[k]; });
      if (!opts.kinds.length) throw new Error('Cochez au moins un type de fichier.');
      const r = await api('/api/reparedocs/runs', { method: 'POST', body: { share_id: Number(v.share_id), path: (v.path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, ''), output: v.output || null, options: opts } });
      location.hash = `#/reparedocs/${r.run_id}`;
    } });
}

/** Liste des traitements RepareDocs (page Réparation, onglet d'un dossier). */
function rdRunsTable(runs) {
  if (!runs.length) return emptyState('wrench', 'Aucun traitement RepareDocs', 'Les traitements lancés apparaîtront ici.');
  return `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Traitement</th><th>Source</th><th>État</th><th>Bilan</th><th></th></tr></thead><tbody>
    ${runs.map(r => { const g = rdCounts(r.summary); return `<tr class="clickable" data-go="#/reparedocs/${r.id}"><td class="nowrap">${fmtDate(r.created_at)}<div class="cell-sub">${esc(r.actor || '')}</div></td>
      <td>${esc(RD_KIND[r.kind] || r.kind)}${r.options && r.options.analyse_only ? ' <span class="pill">analyse seule</span>' : ''}</td>
      <td class="path small">${esc(r.kind === 'individuel' ? (r.origin && (r.origin.rel || r.origin.name)) || '' : r.source || '')}</td>
      <td>${badge(RD_RUN_STATUS, r.status)}</td>
      <td class="small">${r.status === 'running' ? (r.progress ? `${fmtNum(r.progress.done)} / ${fmtNum(r.progress.total)}` : '…') : `${fmtNum(g.repare)} réparé(s) · ${fmtNum(g.irrecup)} irrécupérable(s)`}</td>
      <td>${icon('chevron', 'file-icon')}</td></tr>`; }).join('')}</tbody></table></div>`;
}

// ------------------------------------------------------------------ suivi d'un traitement
async function viewReparedocsRun(view, query, id) {
  const info = await rdInfo();
  let st = await api(`/api/reparedocs/runs/${id}`);
  let lastLog = 0, filter = null, page = 0, q = '';
  const PAGE = 50;
  const header = () => setPage(RD_KIND[st.kind] || 'RepareDocs', `<a href="#/repair">Réparation de fichiers</a> ${icon('chevron', 'file-icon')} RepareDocs`,
    `${st.status === 'running' && can('operator') ? `<button class="btn danger" data-ract="stop">${icon('stop')} Arrêter</button>` : ''}
     <a class="btn" href="/api/reparedocs/runs/${id}/sortie/RAPPORT.html" target="_blank" rel="noopener">${icon('report')} Rapport</a>
     <a class="btn" href="/api/reparedocs/runs/${id}/sortie/" target="_blank" rel="noopener">${icon('folder')} Parcourir les résultats</a>
     ${st.kind === 'masse' && ['done', 'stopped'].includes(st.status) && can('admin') ? `<button class="btn" data-ract="restore">${icon('restore')} Remettre en place</button>` : ''}`,
    `${badge(RD_RUN_STATUS, st.status)} · ${esc(st.kind === 'individuel' ? ((st.origin || {}).rel || (st.origin || {}).name || '') : st.source || '')} → <span class="mono">${esc(st.output || '')}</span>`);
  const phases = info.phases || [];
  const render = () => {
    const live = st.status === 'running' ? (st.progress || {}).counts : null;
    const g = rdCounts(live || st.summary || (st.done || {}).summary);
    const total = Object.values(g).reduce((a, b) => a + b, 0);
    const p = st.progress || {};
    const frac = st.status !== 'running' ? 1 : p.total_bytes ? p.bytes / p.total_bytes : p.total ? p.done / p.total : 0;
    const cur = (st.phase || {}).index ?? -1;
    $('#rd-live', view).innerHTML = `
      ${st.error || st.engine_error ? `<div class="banner critical" style="margin-bottom:12px">${icon('alert')}<span>${esc(st.error || st.engine_error)}</span></div>` : ''}
      ${st.staging ? `<div class="banner info" style="margin-bottom:12px">${icon('folder')}<span>Pendant le traitement, les résultats sont préparés dans <b class="mono">${esc(st.staging)}</b> (autre disque), puis déplacés dans le dossier de sortie à la fin.</span></div>` : ''}
      ${phases.length ? `<ol class="stepper">${phases.map((ph, i) => { const cls = st.status !== 'running' && st.status !== 'failed' ? 'done' : i < cur ? 'done' : i === cur ? 'current' : ''; return `<li class="step ${cls}"><span class="dot">${cls === 'done' ? '✓' : i + 1}</span><span>${esc(ph)}</span></li>`; }).join('')}</ol>` : ''}
      <div class="card"><div class="strong">${esc(st.status === 'running' ? ((st.phase || {}).label || 'Démarrage…') : RD_RUN_STATUS[st.status][1])}</div>
        <div class="progress${st.status === 'running' && !p.total ? ' indeterminate' : ''}" style="margin:10px 0"><div style="width:${(100 * Math.min(1, frac)).toFixed(1)}%"></div></div>
        <div class="small muted">${st.status === 'running' ? (p.total ? `${fmtNum(p.done)} / ${fmtNum(p.total)} fichier(s)${p.total_bytes ? ` · ${fmtBytes(p.bytes)} / ${fmtBytes(p.total_bytes)}` : ''}${p.eta ? ` · reste environ ${fmtDuration(p.eta)}` : ''}` : st.scan ? `${fmtNum(st.scan.found)} fichier(s) trouvé(s)…` : 'Préparation…') : `${fmtNum(total)} fichier(s) analysé(s)`}</div>
        ${st.status === 'running' && (p.current || []).length ? `<div class="cell-sub mono ellipsis" style="margin-top:6px">${esc(p.current.join('  •  '))}</div>` : ''}
        ${st.disk ? `<div class="cell-sub" style="margin-top:6px">Récupération disque : ${st.disk.enabled ? 'active' : 'non utilisée'} — ${esc(st.disk.reason || '')}</div>` : ''}</div>
      <div class="tiles" style="margin-top:16px">${RD_GROUPS.slice(0, 5).map(x => `<div class="tile link${filter === x.key ? ' hero-tile' : ''}" data-rfilter="${x.key}"><div class="chip ${x.tone === 'ok' ? 'good' : x.tone}">${icon(x.key === 'repare' ? 'wrench' : x.key === 'sain' ? 'check' : x.key === 'irrecup' ? 'alert' : 'info')}</div><div class="body"><div class="label">${x.label}</div><div class="value">${fmtNum(g[x.key])}</div><div class="sub">${total ? Math.round(100 * g[x.key] / total) + ' %' : '—'}</div></div></div>`).join('')}</div>`;
    $$('[data-rfilter]', view).forEach(t => { t.onclick = () => { filter = filter === t.dataset.rfilter ? null : t.dataset.rfilter; page = 0; render(); loadResults(); }; });
    const box = $('#rd-logs', view);
    (st.logs || []).forEach(l => { if (l.id > lastLog) { lastLog = l.id; const d = document.createElement('div'); d.className = 'small ' + (l.level === 'error' ? 'err' : ''); d.textContent = l.line; box.appendChild(d); } });
    while (box.childElementCount > 1500) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
    if (st.kind === 'individuel') renderIndividual();
  };
  const renderIndividual = async () => {
    const slot = $('#rd-indiv', view);
    if (!slot || st.status !== 'done') return;
    const r = (await api(`/api/reparedocs/runs/${id}/results?limit=5`)).rows[0];
    if (!r) { slot.innerHTML = ''; return; }
    const fromShare = st.origin && st.origin.share_id;
    slot.innerHTML = `<div class="card">${cardHead('Résultat', esc(r.rel))}
      <p>${rdBadge(r.status)} ${esc(r.method || '')} ${r.cause ? `<span class="cell-sub">${esc(r.cause)}</span>` : ''}</p>
      <div class="btn-row">${r.output_rel ? `<a class="btn primary" href="/api/reparedocs/runs/${id}/sortie/${r.output_rel.split('/').map(encodeURIComponent).join('/')}">${icon('download')} Télécharger le fichier réparé</a>` : ''}
      ${fromShare && r.output_rel && can('operator') ? `<button class="btn" data-ract="apply-copy">${icon('copy')} Déposer une copie « (réparé) » dans le dossier</button>${r.status === 'REPARE' ? `<button class="btn" data-ract="apply-replace">${icon('restore')} Remplacer l'original</button>` : ''}` : ''}</div>
      ${r.status === 'IRRECUPERABLE' ? '<p class="small muted" style="margin-top:10px">Restaurez la dernière version saine depuis la sauvegarde Datto.</p>' : ''}</div>`;
    bindHead();
  };
  const loadResults = async () => {
    const grp = RD_GROUPS.find(x => x.key === filter);
    const r = await api(`/api/reparedocs/runs/${id}/results?status=${grp ? grp.st.join(',') : ''}&q=${encodeURIComponent(q)}&offset=${page * PAGE}&limit=${PAGE}`);
    const pages = Math.max(1, Math.ceil(r.total / PAGE));
    $('#rd-results', view).innerHTML = `<div class="card flush">${cardHead('Fichiers traités', `${fmtNum(r.total)} fichier(s)${grp ? ` · filtre : ${grp.label}` : ''}`,
      `<input type="search" id="rd-q" placeholder="Rechercher un fichier, une méthode, une cause…" value="${esc(q)}" style="width:280px">`)}
      ${r.rows.length ? `<div class="table-wrap tall"><table><thead><tr><th>État</th><th>Fichier</th><th>Méthode / cause</th><th class="num">Taille</th><th></th></tr></thead><tbody>
      ${r.rows.map(x => `<tr><td>${rdBadge(x.status)}</td><td class="path">${fileIcon(x.rel)}${esc(x.rel)}</td><td class="small">${esc(x.method || '')}${x.cause ? `<div class="cell-sub">${esc(x.cause)}</div>` : ''}</td><td class="num">${fmtBytes(x.size)}</td>
        <td>${x.output_rel ? `<a class="btn sm" target="_blank" rel="noopener" href="/api/reparedocs/runs/${id}/sortie/${x.output_rel.split('/').map(encodeURIComponent).join('/')}">Ouvrir</a>` : ''}</td></tr>`).join('')}</tbody></table></div>
      <div class="btn-row" style="padding:12px 16px;justify-content:flex-end"><button class="btn sm" data-rpage="-1" ${page ? '' : 'disabled'}>Précédent</button><span class="small muted">page ${page + 1} / ${pages}</span><button class="btn sm" data-rpage="1" ${page + 1 < pages ? '' : 'disabled'}>Suivant</button></div>`
      : emptyState('file', 'Aucun fichier', st.status === 'running' ? 'Les résultats apparaissent au fil du traitement.' : '')}</div>`;
    const input = $('#rd-q', view);
    let t;
    input.oninput = () => { clearTimeout(t); t = setTimeout(() => { q = input.value.trim(); page = 0; loadResults().then(() => { const i = $('#rd-q', view); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
    $$('[data-rpage]', view).forEach(b => { b.onclick = () => { page += Number(b.dataset.rpage); loadResults(); }; });
  };
  const actions = {
    stop: () => confirmBox('Arrêter le traitement ?', '<p>Le moteur termine proprement le fichier en cours. Relancez plus tard avec « Reprendre » : rien n\'est perdu.</p>', async () => { await api(`/api/reparedocs/runs/${id}/stop`, { method: 'POST' }); toast('Arrêt demandé'); }, 'Arrêter'),
    restore: () => modal({ title: 'Remettre en place les fichiers réparés', danger: true, submit: 'Remettre en place',
      body: `<p>Chaque fichier <b>entièrement</b> réparé va remplacer l'original endommagé, à son emplacement d'origine. Les originaux restent conservés dans « 3-ORIGINAUX_ENDOMMAGES » du dossier de sortie.</p>
        <p><b>Attention :</b> cela écrit sur le disque d'origine. Ne le faites qu'une fois la récupération disque terminée et les résultats vérifiés.</p>${field('Confirmation', inp('confirm', '', 'text', 'placeholder="REMETTRE" autocomplete="off"'), 'Saisissez REMETTRE.', true)}`,
      onSubmit: async f => { const r = await api(`/api/reparedocs/runs/${id}/restore`, { method: 'POST', body: { confirm: formData(f).confirm || '' } }); location.hash = `#/reparedocs/${r.run_id}`; } }),
    'apply-copy': async () => { const r = await api(`/api/reparedocs/runs/${id}/apply`, { method: 'POST', body: { mode: 'copy' } }); toast(`Copie déposée : ${r.path}`); },
    'apply-replace': () => confirmBox('Remplacer l\'original ?', '<p>L\'original endommagé est placé dans la corbeille VigieFS (restaurable), puis remplacé par la version réparée.</p>', async () => { const r = await api(`/api/reparedocs/runs/${id}/apply`, { method: 'POST', body: { mode: 'replace' } }); toast(`Original remplacé : ${r.path}`); }, 'Remplacer'),
  };
  const bindHead = () => $$('[data-ract]').forEach(b => { b.onclick = guard(() => actions[b.dataset.ract]()); });
  view.innerHTML = `<div id="rd-live"></div>${st.kind === 'individuel' ? '<div id="rd-indiv" style="margin-top:16px"></div>' : ''}
    <div class="grid span-2-1" style="margin-top:16px"><div id="rd-results"></div>
    <div class="card">${cardHead('Activité', 'Journal du moteur')}<div id="rd-logs" class="details-box" style="max-height:420px;overflow:auto"></div></div></div>`;
  header(); bindHead(); render(); loadResults();
  let lastResults = Date.now();
  every(2000, guard(async () => {
    if (st.status !== 'running') return;
    st = await api(`/api/reparedocs/runs/${id}?since=${lastLog}`);
    render();
    if (Date.now() - lastResults > 6000 || st.status !== 'running') { lastResults = Date.now(); loadResults(); }
    if (st.status !== 'running') { header(); bindHead(); toast(st.status === 'done' ? 'Traitement terminé : le rapport est prêt.' : 'Traitement interrompu'); }
  }));
}
