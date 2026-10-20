/* VigieFS — virtualisation Hyper-V : hôte, VM, disques virtuels, données des VM, cohérence avec l'archivage */
'use strict';

const VM_STATE = { Running: ['ok', 'En marche'], Off: ['neutral', 'Éteinte'], Saved: ['warning', 'Enregistrée'], Paused: ['warning', 'En pause'], Starting: ['info', 'Démarrage'], Stopping: ['info', 'Arrêt'] };
const VHD_TYPE = { Dynamic: 'dynamique', Fixed: 'fixe', Differencing: 'différenciation' };
const hvAge = ts => (ts ? Math.max(0, Math.round((Date.now() / 1000 - ts) / 86400)) : null);

async function viewVirtualization(view, query) {
  let selected = query.get('vm') || null;
  setPage('Virtualisation (Hyper-V)', 'Gestion', '', 'VigieFS installé sur l\'hôte Hyper-V : machines virtuelles, disques virtuels, données de chaque VM et cohérence avec l\'archivage.');
  rowLinks(view);
  const render = async () => {
    const d = await api('/api/hyperv');
    $('#page-actions').innerHTML = `${can('operator') && !d.demo ? `<button class="btn" data-hact="refresh" ${d.job ? 'disabled' : ''}>${icon('refresh')} Actualiser l'inventaire</button>` : ''}`;
    if (!d.inventory) {
      view.innerHTML = `<div class="card">${emptyState('disk', d.state && d.state.error ? 'Hyper-V non disponible' : 'Aucun inventaire Hyper-V',
        d.platform === 'windows' ? `${esc((d.state || {}).error || '')} Installez VigieFS sur l'hôte Hyper-V (service Windows) : l'inventaire des VM, de leurs disques et de leurs données est alors automatique, toutes les 6 heures.`
          : 'L\'inventaire Hyper-V se fait depuis l\'hôte Windows Hyper-V, où VigieFS doit être installé.',
        can('operator') && d.platform === 'windows' ? `<button class="btn primary" data-hact="refresh" ${d.job ? 'disabled' : ''}>${icon('refresh')} Lancer l'inventaire</button>` : '')}</div>`;
      return;
    }
    const inv = d.inventory;
    const vms = inv.vms;
    const run = vms.filter(v => v.running).length;
    const onHost = vms.reduce((a, v) => a + v.file_size, 0);
    const maxSize = vms.reduce((a, v) => a + v.max_size, 0);
    const snaps = vms.reduce((a, v) => a + v.snapshots.length, 0);
    const oldestSnap = Math.min(...vms.filter(v => v.oldest_snapshot).map(v => v.oldest_snapshot), Infinity);
    const orphans = inv.orphans.reduce((a, o) => a + (o.size || 0), 0);
    const withData = vms.filter(v => ((v.guest || {}).shares || []).length || v.shares.length);
    const unsup = withData.filter(v => !v.shares.length);
    const sevCount = s => d.findings.filter(f => f.severity === s).length;
    view.innerHTML = `
      ${d.demo ? `<div class="banner info">${icon('info')}<span><b>Inventaire de démonstration</b> : un hôte Hyper-V fictif (HV-01) et ses VM. Sur un vrai hôte, l'inventaire est automatique et les actions (découverte, analyse, compactage) sont disponibles.</span></div>` : ''}
      ${d.job ? `<div class="banner info"><span class="dot run"></span><span>Inventaire en cours…</span></div>` : ''}
      <div class="tiles" style="margin-top:16px">
        ${tile({ label: 'Machines virtuelles', value: `${fmtNum(run)} / ${fmtNum(vms.length)}`, ic: 'disk', sub: `en marche · hôte ${esc(inv.host.name || '')}` })}
        ${tile({ label: 'Disques des VM sur l\'hôte', value: fmtBytes(onHost), ic: 'disk', tone: 'violet', sub: `${fmtBytes(maxSize)} au maximum` })}
        ${tile({ label: 'Points de contrôle', value: fmtNum(snaps), ic: 'restore', tone: snaps ? 'warning' : 'good', sub: isFinite(oldestSnap) ? `le plus ancien : ${hvAge(oldestSnap)} jour(s)` : 'aucun' })}
        ${tile({ label: 'Disques orphelins', value: fmtBytes(orphans), ic: 'trash', tone: orphans ? 'warning' : 'good', sub: `${fmtNum(inv.orphans.length)} fichier(s) sans VM` })}
        ${tile({ label: 'VM avec des données', value: fmtNum(withData.length), ic: 'folder', tone: unsup.length ? 'warning' : 'good', sub: unsup.length ? `${unsup.length} sans supervision` : 'toutes supervisées' })}
        ${tile({ label: 'Constats', value: fmtNum(d.findings.length), ic: 'alert', tone: sevCount('critical') ? 'critical' : sevCount('warning') ? 'warning' : 'good', sub: `${sevCount('critical')} critique(s) · ${sevCount('warning')} avertissement(s)` })}
      </div>
      <div class="grid span-2-1">
        <div class="card">${cardHead('Constats', `Inventaire du ${fmtDate(d.collected_at)}`)}
          ${d.findings.length ? `<div style="max-height:430px;overflow:auto">${d.findings.map(f => `<div class="finding"><div>${sev(f.severity)}</div><div class="body"><div class="strong">${esc(f.title)}</div><div class="alert-msg">${esc(f.message)}</div></div></div>`).join('')}</div>`
            : emptyState('check', 'Rien à signaler', 'Points de contrôle, volumes et disques virtuels en ordre.', '', 'good')}</div>
        <div class="card">${cardHead('Volumes de l\'hôte', 'Fichiers des VM et croissance possible des disques dynamiques')}
          ${inv.volumes.map(v => { const pct = v.size ? 100 - v.free * 100 / v.size : 0; const over = v.potential_growth > v.free;
            return `<div style="margin-bottom:14px"><div class="btn-row" style="justify-content:space-between"><span class="strong mono">${esc(v.root)}</span><span class="small muted">${esc(v.label || '')} · ${fmtBytes(v.free)} libres sur ${fmtBytes(v.size)}</span></div>
              ${meter(pct, pct >= 95 ? 'critical' : pct >= 90 ? 'warning' : '')}
              <div class="cell-sub">${fmtBytes(v.vm_files)} de disques virtuels · peuvent encore grandir de <b class="${over ? 'err' : ''}">${fmtBytes(v.potential_growth)}</b>${over ? ' (plus que l\'espace libre)' : ''}</div></div>`; }).join('') || '<div class="empty">Aucun volume</div>'}</div>
      </div>
      <div class="card flush">${cardHead('Machines virtuelles', 'Cliquez sur une VM pour ses disques, ses données et les actions')}
        <div class="table-wrap"><table><thead><tr><th>VM</th><th>État</th><th class="num">Sur l'hôte</th><th class="num">Maximum</th><th class="num">7 jours</th><th>Points de contrôle</th><th>Données</th><th>Dernière écriture</th></tr></thead><tbody>
        ${vms.map(v => { const g = v.guest || {}; const found = (g.shares || []).length;
          return `<tr class="clickable${selected === v.id ? ' selected' : ''}" data-vm="${esc(v.id)}"><td><div class="strong">${esc(v.name)}</div><div class="cell-sub">${esc(v.os || '')}${v.fqdn ? ` · ${esc(v.fqdn)}` : ''}${v.ips.length ? ` · ${esc(v.ips.join(', '))}` : ''}</div></td>
            <td>${badge(VM_STATE, v.state)}</td><td class="num">${fmtBytes(v.file_size)}</td><td class="num">${fmtBytes(v.max_size)}</td>
            <td class="num">${v.growth_7d == null ? '—' : (v.growth_7d >= 0 ? '+' : '') + fmtBytes(v.growth_7d)}</td>
            <td>${v.snapshots.length ? `<span class="pill ${hvAge(v.oldest_snapshot) >= 7 ? 'warn' : ''}">${v.snapshots.length} · ${hvAge(v.oldest_snapshot)} j</span>` : '<span class="muted">—</span>'}</td>
            <td>${found ? `${fmtNum(found)} partage(s)` : ''}${v.shares.length ? ` <span class="pill on">${v.shares.length} supervisé(s)</span>` : found ? ' <span class="pill warn">non supervisés</span>' : '<span class="muted">—</span>'}</td>
            <td class="nowrap small">${v.last_write ? fmtAgo(v.last_write) : '—'}</td></tr>`; }).join('')}</tbody></table></div></div>
      <div id="hv-detail"></div>
      <div class="grid cols-2">
        <div class="card flush">${cardHead('Disques virtuels orphelins', 'Rattachés à aucune VM : vérifiez puis supprimez-les ou archivez-les depuis Hyper-V')}
          <div class="table-wrap"><table><tbody>${inv.orphans.map(o => `<tr><td class="path small mono">${esc(o.path)}</td><td class="num">${fmtBytes(o.size)}</td><td class="nowrap small">${o.mtime ? fmtDay(o.mtime) : ''}</td></tr>`).join('') || '<tr><td class="muted">Aucun disque orphelin</td></tr>'}</tbody></table></div></div>
        <div class="card">${cardHead('Cohérence avec l\'archivage', 'Ce que VigieFS garantit sur un hôte de virtualisation')}
          <ul class="small" style="margin:0;padding-left:18px;line-height:1.6">
            <li>Les <b>disques et fichiers de VM</b> (.vhdx, .avhdx, .vmcx, .vmrs, .vmdk…) ne sont <b>jamais</b> archivés, nettoyés, dédoublonnés, déplacés ni supprimés, quel que soit le dossier supervisé.</li>
            <li>Les <b>dossiers de stockage des VM</b> ci-dessous sont exclus de l'archivage, du nettoyage, des doublons et de la réorganisation (ils restent comptés dans les volumes).</li>
            <li>Archiver ou nettoyer <b>dans</b> une VM libère de la place dans la VM, mais un disque dynamique ne rétrécit sur l'hôte qu'après <b>compactage</b> (VM arrêtée).</li>
            <li>Les <b>doublons entre VM</b> se recherchent sur tous les dossiers supervisés rattachés aux VM (ci-dessous).</li></ul>
          ${d.protected.length ? `<details class="small" style="margin-top:8px"><summary>${d.protected.length} dossier(s) protégé(s)</summary><div class="details-box mono">${d.protected.map(esc).join('<br>')}</div></details>` : ''}</div>
      </div>
      <div id="hv-dups"></div>`;
    $$('[data-vm]', view).forEach(tr => { tr.onclick = () => { selected = tr.dataset.vm; history.replaceState(null, '', `#/virtualization?vm=${encodeURIComponent(selected)}`); drawDetail(); $$('[data-vm]', view).forEach(x => x.classList.toggle('selected', x.dataset.vm === selected)); }; });
    const drawDetail = () => {
      const v = vms.find(x => x.id === selected);
      const slot = $('#hv-detail', view);
      if (!v) { slot.innerHTML = ''; return; }
      const g = v.guest || {};
      const supervised = new Set(v.shares.map(s => (s.path || '').toLowerCase()));
      const off = (v.state || '').toLowerCase() === 'off';
      const scan = g.offline;
      slot.innerHTML = `<div class="card">${cardHead(v.name, `${esc(v.os || 'Système inconnu')} · ${v.cpus} vCPU · ${fmtBytes(v.memory || v.memory_startup)} de mémoire · génération ${v.generation}`,
        `${can('operator') && v.running && !d.demo ? `<button class="btn sm" data-hact="discover">${icon('search')} Découvrir ses données</button>` : ''}
         ${can('admin') && !v.running && !d.demo ? `<button class="btn sm" data-hact="scan">${icon('eye')} Analyser le contenu</button>` : ''}
         ${can('admin') && off && !d.demo && v.disks.some(x => x.type === 'Dynamic') ? `<button class="btn sm" data-hact="compact">${icon('disk')} Compacter les disques…</button>` : ''}`)}
        <h3>Disques virtuels</h3><div class="table-wrap"><table><thead><tr><th>Disque</th><th>Type</th><th class="num">Fichier</th><th class="num">Maximum</th><th class="num">Fragm.</th><th>Dernière écriture</th></tr></thead><tbody>
        ${v.disks.map(x => `<tr><td class="path small mono">${esc(x.path || '')}${x.chain.length ? `<div class="cell-sub">chaîne de points de contrôle : ${x.chain.map(c => `${esc((c.path || '').split('\\').pop())} (${fmtBytes(c.file_size || 0)})`).join(' → ')}</div>` : ''}${x.error ? `<div class="cell-sub err">${esc(x.error)}</div>` : ''}</td>
          <td>${esc(VHD_TYPE[x.type] || x.type || '')}</td><td class="num">${fmtBytes(x.file_size + x.chain_size)}</td><td class="num">${fmtBytes(x.size)}</td><td class="num">${x.frag == null ? '—' : x.frag + ' %'}</td><td class="small nowrap">${x.mtime ? fmtDate(x.mtime) : '—'}</td></tr>`).join('')}</tbody></table></div>
        ${v.snapshots.length ? `<h3>Points de contrôle</h3><div class="small">${v.snapshots.map(s => `<span class="pill ${hvAge(s.created) >= 7 ? 'warn' : ''}">${esc(s.name)} · ${fmtDay(s.created)}</span>`).join(' ')}</div>` : ''}
        <div class="grid cols-2" style="margin-top:12px">
          <div><h3>Partages découverts</h3>${g.error ? `<div class="small muted">${esc(g.error)}</div>` : ''}
            ${(g.shares || []).length ? `<table><tbody>${g.shares.map(s => { const unc = `\\\\${g.target}\\${s.name}`; const sup = supervised.has(unc.toLowerCase());
              return `<tr><td><div class="strong">${esc(s.name)}</div><div class="cell-sub mono">${esc(unc)}${s.path ? ` · ${esc(s.path)}` : ''}</div></td><td style="width:1%" class="nowrap">${sup ? '<span class="pill on">supervisé</span>' : can('operator') ? `<button class="btn sm" data-hact="supervise" data-share="${esc(s.name)}">${icon('plus')} Superviser</button>` : '<span class="pill warn">non supervisé</span>'}</td></tr>`; }).join('')}</tbody></table>`
              : `<div class="empty">${v.running ? (g.checked_at ? 'Aucun partage de données' : 'Données pas encore découvertes') : 'VM éteinte : analysez le contenu de ses disques'}</div>`}
            ${(g.volumes || []).length ? `<h3>Volumes dans la VM</h3>${g.volumes.map(x => `<div class="small">${esc(x.drive)} ${esc(x.label || '')} : ${fmtBytes(x.size - x.free)} utilisés sur ${fmtBytes(x.size)}</div>`).join('')}` : ''}</div>
          <div><h3>Dossiers supervisés rattachés</h3>
            ${v.shares.length ? `<table><tbody>${v.shares.map(s => `<tr class="clickable" data-go="#/share/${s.id}"><td><div class="strong">${esc(s.name)}</div><div class="cell-sub mono">${esc(s.path)}</div></td><td class="num">${s.size ? fmtBytes(s.size) : ''}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Aucun</div>'}
            ${can('operator') ? `<button class="btn sm ghost" data-hact="link" style="margin-top:8px">${icon('plus')} Rattacher un dossier supervisé…</button>` : ''}
            <div class="cell-sub" style="margin-top:6px">Rattachement automatique pour les chemins \\\\${esc(v.fqdn || v.name)}\\… ; manuel si VigieFS est installé dans la VM elle-même.</div></div></div>
        ${scan ? `<h3>Contenu analysé (${fmtDate(scan.scanned_at)})</h3>${scan.disks.map(dk => `<div class="small mono">${esc(dk.disk)}${dk.error ? ` — <span class="err">${esc(dk.error)}</span>` : ''}</div>${dk.volumes.map(vol => `<div class="grid cols-2" style="margin:8px 0 14px"><div><div class="strong">${esc(vol.label || vol.path)} — ${fmtBytes(vol.report.total_size)} · ${fmtNum(vol.report.file_count)} fichiers</div><div class="cell-sub">${fmtBytes(vol.report.stale_size)} non modifiés depuis 2 ans${vol.report.newest_mtime ? ` · dernier fichier modifié le ${fmtDay(vol.report.newest_mtime)}` : ''}</div>${hbars((vol.report.top_folders || []).slice(0, 6).map(f => ({ name: f.name, value: f.size })))}</div><div>${hbars((vol.report.by_extension || []).slice(0, 6).map(e => ({ name: e.ext, value: e.size })))}</div></div>`).join('')}`).join('')}` : ''}</div>`;
      rowLinks(slot);
    };
    drawDetail();
    duplicatesPanel($('#hv-dups', view), { launchUrl: '/api/hyperv/duplicates', listQuery: 'scope=hyperv', showShare: true, title: 'Doublons entre les VM', canLaunch: vms.reduce((a, v) => a + v.shares.length, 0) >= 2 });
    const actions = {
      refresh: async () => { const r = await api('/api/hyperv/refresh', { method: 'POST' }); toast('Inventaire lancé'); await watchJob(r.job_id); render(); },
      discover: async () => { const r = await api(`/api/hyperv/vms/${encodeURIComponent(selected)}/discover`, { method: 'POST' }); toast(r.error ? `Découverte impossible : ${r.error}` : `${r.shares.length} partage(s) trouvé(s) (${r.method})`, r.error ? 'error' : 'ok'); render(); },
      scan: () => confirmBox('Analyser le contenu de la VM ?', '<p>Ses disques sont montés <b>en lecture seule</b> sur l\'hôte, analysés (volume, types, ancienneté), puis démontés. La VM doit rester éteinte pendant l\'analyse.</p>',
        async () => { const r = await api(`/api/hyperv/vms/${encodeURIComponent(selected)}/scan`, { method: 'POST', body: {} }); toast('Analyse lancée'); const j = await watchJob(r.job_id); if (j.status !== 'done') toast(j.error || 'Échec de l\'analyse', 'error'); render(); }, 'Analyser', false),
      compact: () => modal({ title: 'Compacter les disques dynamiques', danger: true, submit: 'Compacter',
        body: `<p>Optimize-VHD (mode complet) rend à l'hôte l'espace libéré dans la VM. La VM doit rester <b>arrêtée</b> pendant l'opération, qui peut durer longtemps sur un gros disque.</p>${field('Confirmation', inp('confirm', '', 'text', 'placeholder="COMPACTER" autocomplete="off"'), 'Saisissez COMPACTER.', true)}`,
        onSubmit: async f => { const r = await api(`/api/hyperv/vms/${encodeURIComponent(selected)}/compact`, { method: 'POST', body: { confirm: formData(f).confirm || '' } }); toast('Compactage lancé'); watchJob(r.job_id).then(j => { toast(j.status === 'done' ? `Compactage terminé : ${fmtBytes(j.result.freed || 0)} rendus à l'hôte` : (j.error || 'Échec'), j.status === 'done' ? 'ok' : 'error'); render(); }); } }),
      supervise: async el => {
        const v = vms.find(x => x.id === selected); const g = v.guest || {};
        const clients = await api('/api/clients');
        if (!clients.length) return toast("Créez d'abord un client.", 'error');
        modal({ title: `Superviser \\\\${g.target}\\${el.dataset.share}`, submit: 'Superviser',
          body: `<div class="form-grid">${field('Client', sel('client_id', clients.map(c => [c.id, c.name]), clients[0].id))}${field('Nom du dossier supervisé', inp('name', `${v.name} — ${el.dataset.share}`))}
            ${field('Chemin', inp('path', `\\\\${g.target}\\${el.dataset.share}`), 'Le compte du service VigieFS doit pouvoir lire ce partage.', true)}</div>`,
          onSubmit: async f => { const x = formData(f); await api('/api/shares', { method: 'POST', body: { client_id: Number(x.client_id), name: x.name, path: x.path, scan_interval_min: 360, monitor_enabled: true } }); toast('Dossier ajouté — première analyse lancée'); render(); } });
      },
      link: async () => {
        const shares = await api('/api/shares');
        modal({ title: 'Rattacher un dossier supervisé à la VM', submit: 'Rattacher',
          body: field('Dossier supervisé', sel('share_id', shares.map(s => [s.id, `${s.client_name} › ${s.name} (${s.path})`]), shares[0] && shares[0].id), 'Pour un dossier supervisé par un chemin local (VigieFS installé dans la VM) ou un nom DFS.', true),
          onSubmit: async f => { await api(`/api/hyperv/vms/${encodeURIComponent(selected)}/links`, { method: 'POST', body: { share_id: Number(formData(f).share_id) } }); toast('Dossier rattaché'); render(); } });
      },
    };
    const handler = e => { const el = e.target.closest('[data-hact]'); if (el) { e.preventDefault(); e.stopPropagation(); guard(actions[el.dataset.hact])(el); } };
    view.onclick = handler;
    $('#page-actions').onclick = handler;
  };
  await render();
}
