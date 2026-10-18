/* VigieFS — supervision : tableau de bord, alertes, journal d'activité, rapports */
'use strict';

async function viewDashboard(view) {
  setPage('Tableau de bord', esc(state.company || ''), `<button class="btn" data-refresh>${icon('refresh')} Actualiser</button>`,
    'Vue consolidée de tous les serveurs de fichiers supervisés.');
  rowLinks(view);
  const render = async () => {
    const [d, opt] = await Promise.all([api('/api/dashboard'), api('/api/optimization')]);
    const c = d.counts; const a = d.alerts; const t = opt.totals;
    const gain = t.junk_size + t.dup_wasted + t.stale_size;
    view.innerHTML = `
      ${a.critical ? `<a class="banner critical" href="#/alerts?severity=critical">${icon('alert')}<span>${plural(a.critical, 'alerte critique', 'alertes critiques')} en cours — intervention requise</span><span class="spacer"></span><span class="btn sm">Traiter →</span></a>`
        : `<div class="banner ok">${icon('check')}<span>Aucune alerte critique. ${c.unavailable ? `${c.unavailable} dossier(s) inaccessible(s).` : 'Tous les dossiers supervisés sont accessibles.'}</span></div>`}
      <div class="tiles" style="margin-top:16px">
        ${tile({ label: 'Clients', value: fmtNum(c.clients), ic: 'clients', go: '#/clients', sub: `${c.shares} dossier(s) supervisé(s)` })}
        ${tile({ label: 'Volume supervisé', value: fmtBytes(c.total_size), ic: 'disk', tone: 'violet', sub: `${compact(c.total_files)} fichiers` })}
        ${tile({ label: 'Alertes critiques', value: fmtNum(a.critical), ic: 'alert', tone: a.critical ? 'critical' : 'good', go: '#/alerts?severity=critical', sub: a.critical ? 'à traiter en priorité' : 'aucune' })}
        ${tile({ label: 'Avertissements', value: fmtNum(a.warning), ic: 'bell', tone: a.warning ? 'warning' : 'good', go: '#/alerts?severity=warning', sub: a.warning ? 'à planifier' : 'aucun' })}
        ${tile({ label: 'Activité fichiers (24 h)', value: compact(c.events_24h), ic: 'activity', tone: 'aqua', go: '#/events', sub: `${c.monitored} dossier(s) en temps réel` })}
        ${tile({ label: 'Gain d\'optimisation possible', value: fmtBytes(gain), ic: 'sparkle', tone: 'good', go: '#/optimization', sub: 'inutiles, doublons, données froides' })}
      </div>
      <div class="grid span-2-1">
        <div class="card">${cardHead('Activité fichiers — 24 dernières heures', 'Créations, modifications, suppressions et renommages', '<button class="btn sm ghost" data-act="toggleTable">Voir le tableau</button>')}
          <div id="act-chart"></div><div id="act-table" hidden></div></div>
        <div class="card">${cardHead('Alertes en cours', '', '<a href="#/alerts" class="small">Tout voir →</a>')}
          ${d.recent_alerts.length ? `<table><tbody>${d.recent_alerts.map(r => `<tr class="clickable" data-go="#/alerts"><td style="width:1%">${sev(r.severity)}</td><td><div class="strong">${esc(r.title)}</div><div class="cell-sub">${esc(r.client_name || '')}${r.share_name ? ' · ' + esc(r.share_name) : ''} · ${fmtAgo(r.last_seen)}</div></td></tr>`).join('')}</tbody></table>`
            : emptyState('check', 'Tout est sous contrôle', 'Aucune alerte active.', '', 'good')}
        </div>
      </div>
      <div class="grid cols-2">
        <div class="card">${cardHead('Quotas à surveiller', 'Dossiers les plus proches de leur limite')}
          ${d.quota_watch.length ? `<table><tbody>${d.quota_watch.map(s => `<tr class="clickable" data-go="#/share/${s.id}"><td><div class="strong">${esc(s.name)}</div><div class="cell-sub">${fmtBytes(s.last_size)} / ${fmtBytes(s.quota_bytes)}</div></td><td style="width:48%">${meter(s.quota_pct, s.quota_level)}</td></tr>`).join('')}</tbody></table>`
            : emptyState('disk', 'Aucun quota défini', 'Définissez des quotas sur les dossiers pour être alerté avant saturation.')}
        </div>
        <div class="card">${cardHead('Volume par client')}${hbars(d.clients.map(x => ({ name: x.name, value: x.size, files: x.files, shares: x.shares })), { sub: i => `${fmtNum(i.files)} fichiers · ${i.shares} dossier(s)` })}</div>
      </div>`;
    state.redraw = [];
    columnChart($('#act-chart', view), d.activity.map(b => {
      const h = new Date(b.ts * 1000).getHours();
      return { label: `${String(h).padStart(2, '0')}h`, value: b.count, tip: `${String(h).padStart(2, '0')}h00 – ${String((h + 1) % 24).padStart(2, '0')}h00<br><b>${fmtNum(b.count)}</b> événement(s)` };
    }), { every: 3 });
    $('#act-table', view).innerHTML = `<div class="table-wrap tall"><table><thead><tr><th>Heure</th><th class="num">Événements</th></tr></thead><tbody>${d.activity.map(b => `<tr><td>${fmtDate(b.ts)}</td><td class="num">${fmtNum(b.count)}</td></tr>`).join('')}</tbody></table></div>`;
  };
  bindActions(view, { toggleTable: el => { const t = $('#act-table', view); const c = $('#act-chart', view); t.hidden = !t.hidden; c.hidden = !t.hidden; el.textContent = t.hidden ? 'Voir le tableau' : 'Voir le graphique'; } });
  $('[data-refresh]').onclick = guard(render);
  await render();
  every(15000, guard(render));
}

// ------------------------------------------------------------------ alertes
function alertsTable(rows, { selectable = true, showShare = true } = {}) {
  if (!rows.length) return emptyState('check', 'Aucune alerte', 'Rien à signaler pour ces critères.', '', 'good');
  return `<table><thead><tr>${selectable && can('operator') ? '<th class="check"><input type="checkbox" data-all aria-label="Tout sélectionner"></th>' : ''}<th>Gravité</th><th>Alerte</th>${showShare ? '<th>Client / dossier</th>' : ''}<th class="num">Occur.</th><th>Dernière</th><th>Statut</th><th></th></tr></thead><tbody>
    ${rows.map(a => `<tr>${selectable && can('operator') ? `<td class="check">${a.status === 'open' ? `<input type="checkbox" data-sel value="${a.id}" aria-label="Sélectionner">` : ''}</td>` : ''}
      <td style="width:1%">${sev(a.severity)}</td>
      <td><div><span class="strong">${esc(a.title)}</span> <span class="pill">${esc(CAT_LABEL[a.category] || a.category)}</span></div><div class="alert-msg">${esc(a.message)}</div>
        <div class="details-box" id="det-${a.id}" hidden>${esc(JSON.stringify(a.details, null, 2))}</div></td>
      ${showShare ? `<td>${a.client_name ? esc(a.client_name) : '—'}${a.share_id ? `<div class="small"><a href="#/share/${a.share_id}">${esc(a.share_name)}</a></div>` : ''}</td>` : ''}
      <td class="num">${a.count}</td>
      <td class="nowrap" data-tip="Première : ${esc(fmtDate(a.first_seen))}<br>Dernière : ${esc(fmtDate(a.last_seen))}">${fmtAgo(a.last_seen)}</td>
      <td><span class="pill ${a.status === 'open' ? 'err' : a.status === 'resolved' ? 'on' : ''}">${STATUS_LABEL[a.status]}</span>${a.ack_by ? `<div class="cell-sub">${esc(a.ack_by)}</div>` : ''}</td>
      <td class="nowrap"><button class="btn sm ghost" data-act="details" data-id="${a.id}">Détails</button>
        ${can('operator') && a.status === 'open' ? `<button class="btn sm" data-act="ack" data-id="${a.id}">Acquitter</button>` : ''}
        ${can('operator') && a.status !== 'resolved' ? `<button class="btn sm ghost" data-act="close" data-id="${a.id}">Clore</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
}
function alertActions(root, reload) {
  return {
    details: el => { const d = $(`#det-${el.dataset.id}`, root); d.hidden = !d.hidden; },
    ack: async el => { await api(`/api/alerts/${el.dataset.id}/ack`, { method: 'POST' }); toast('Alerte acquittée'); reload(); refreshIndicators(); },
    close: async el => { await api(`/api/alerts/${el.dataset.id}/close`, { method: 'POST' }); toast('Alerte close'); reload(); refreshIndicators(); },
  };
}

async function viewAlerts(view, query) {
  setPage('Alertes', 'Supervision', `<a class="btn" href="/api/reports/alerts.csv">${icon('download')} Export CSV</a>`,
    'Alertes dédoublonnées : elles se résolvent automatiquement quand la cause disparaît.');
  const clients = await api('/api/clients');
  view.innerHTML = `<div class="card flush"><div class="filters">
      ${sel('status', [['active', 'Actives'], ['open', 'Ouvertes'], ['acknowledged', 'Acquittées'], ['resolved', 'Résolues'], ['', 'Toutes']], query.get('status') || 'active', 'data-f')}
      ${sel('severity', [['', 'Toutes gravités'], ['critical', 'Critique'], ['warning', 'Avertissement'], ['info', 'Info']], query.get('severity') || '', 'data-f')}
      ${sel('client_id', [['', 'Tous les clients'], ...clients.map(c => [c.id, c.name])], query.get('client') || '', 'data-f')}
      ${sel('category', [['', 'Toutes catégories'], ...Object.entries(CAT_LABEL)], '', 'data-f')}
      <span class="spacer"></span>${can('operator') ? '<button class="btn" data-act="bulk" disabled>Acquitter la sélection</button>' : ''}
    </div><div id="list" class="table-wrap" style="margin-top:14px"></div></div>`;
  const syncBulk = () => { const b = $('[data-act=bulk]', view); if (b) b.disabled = !$$('[data-sel]:checked', view).length; };
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
  view.addEventListener('change', e => {
    if (e.target.matches('[data-all]')) $$('[data-sel]', view).forEach(c => { c.checked = e.target.checked; });
    if (e.target.matches('[data-f]')) load();
    syncBulk();
  });
  bindActions(view, {
    ...alertActions(view, load),
    bulk: async () => { const ids = $$('[data-sel]:checked', view).map(c => Number(c.value)); await api('/api/alerts/bulk-ack', { method: 'POST', body: { ids } }); toast(`${ids.length} alerte(s) acquittée(s)`); load(); refreshIndicators(); },
  });
  await load();
  every(15000, guard(load));
}

// ------------------------------------------------------------------ journal d'activité
function eventsTable(rows, showShare = true) {
  if (!rows.length) return emptyState('activity', 'Aucun événement', 'Aucune activité sur cette période.');
  return `<table><thead><tr><th>Date</th>${showShare ? '<th>Client / dossier</th>' : ''}<th>Type</th><th>Chemin</th><th class="num">Taille</th></tr></thead><tbody>
    ${rows.map(e => `<tr><td class="nowrap">${fmtDate(e.ts, true)}</td>${showShare ? `<td class="nowrap">${esc(e.client_name)}<div class="cell-sub">${esc(e.share_name)}</div></td>` : ''}
      <td><span class="pill ${e.type.includes('deleted') ? 'err' : e.type.includes('created') ? 'on' : e.type.includes('moved') ? 'blue' : ''}">${esc(EVENT_LABEL[e.type] || e.type)}</span></td>
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

async function viewEvents(view) {
  setPage("Journal d'activité", 'Supervision', '', 'Toutes les opérations sur les fichiers, en temps réel.');
  const [clients, shares] = await Promise.all([api('/api/clients'), api('/api/shares')]);
  view.innerHTML = `<div class="card flush">${eventFilters(
    sel('client_id', [['', 'Tous les clients'], ...clients.map(c => [c.id, c.name])], '', 'data-f') +
    sel('share_id', [['', 'Tous les dossiers'], ...shares.map(s => [s.id, `${s.client_name} › ${s.name}`])], '', 'data-f'))}
    <div id="ev" class="table-wrap tall" style="margin-top:14px"></div><div class="btn-row" style="padding:12px 20px;border-top:1px solid var(--border)"><button class="btn sm" data-act="more">Charger plus</button><span class="small muted" id="count"></span></div></div>`;
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
  setPage('Rapports', 'Outils', '', 'Exports pour la facturation, les comités clients et les revues mensuelles.');
  const [clients, shares, opt] = await Promise.all([api('/api/clients'), api('/api/shares'), api('/api/optimization')]);
  const totalSize = clients.reduce((a, c) => a + c.total_size, 0);
  const reportCard = (ic, title, text, action) => `<div class="card"><div class="tile" style="border:0;box-shadow:none;padding:0;margin-bottom:12px"><div class="chip">${icon(ic)}</div><div class="body"><h2>${title}</h2><div class="small muted">${text}</div></div></div>${action}</div>`;
  view.innerHTML = `
    <div class="grid cols-3">
      ${reportCard('report', 'Consommation par client', 'Volume, fichiers et quotas par client et par dossier — base de facturation du stockage.', `<a class="btn primary" href="/api/reports/usage.csv">${icon('download')} Télécharger (CSV)</a>`)}
      ${reportCard('bell', 'Historique des alertes', 'Statuts, occurrences et technicien ayant acquitté — pour les revues de service.', `<a class="btn primary" href="/api/reports/alerts.csv">${icon('download')} Télécharger (CSV)</a>`)}
      ${reportCard('activity', "Journal d'activité", 'Créations, modifications, suppressions et renommages filtrables.', '<a class="btn" href="#/events">Ouvrir le journal →</a>')}
    </div>
    <div class="card flush">${cardHead('Synthèse par client', 'Volume, part du stockage total et potentiel d\'optimisation')}<div class="table-wrap"><table><thead><tr><th>Client</th><th class="num">Dossiers</th><th class="num">Volume</th><th class="num">Part</th><th class="num">Fichiers</th><th class="num">Gain possible</th><th>Alertes ouvertes</th></tr></thead><tbody>
      ${clients.map(c => {
        const rows = opt.shares.filter(s => s.client_id === c.id);
        const gain = rows.reduce((a, s) => a + s.junk_size + (s.dup_wasted || 0) + s.stale_size, 0);
        return `<tr class="clickable" data-go="#/shares?client=${c.id}"><td class="strong">${esc(c.name)}</td><td class="num">${c.share_count}</td><td class="num">${fmtBytes(c.total_size)}</td><td class="num">${totalSize ? fmtPct(c.total_size * 100 / totalSize) : '—'}</td><td class="num">${fmtNum(c.total_files)}</td><td class="num">${fmtBytes(gain)}</td><td>${c.open_alerts ? `<span class="pill err">${c.open_alerts}</span>` : sev('ok')}</td></tr>`;
      }).join('') || `<tr><td colspan="7">${emptyState('clients', 'Aucun client')}</td></tr>`}
    </tbody></table></div></div>
    <div class="card">${cardHead('Dossiers sans analyse récente', 'Aucune analyse réussie depuis plus de 48 heures')}${(() => {
      const stale = shares.filter(s => !s.last_scan_at || Date.now() / 1000 - s.last_scan_at > 172800);
      return stale.length ? `<table><tbody>${stale.map(s => `<tr class="clickable" data-go="#/share/${s.id}"><td class="strong">${esc(s.client_name)} › ${esc(s.name)}</td><td class="path">${esc(s.path)}</td><td>${fmtAgo(s.last_scan_at)}</td></tr>`).join('')}</tbody></table>` : emptyState('check', 'Tout est à jour', 'Tous les dossiers ont été analysés récemment.', '', 'good');
    })()}</div>`;
  rowLinks(view);
}
