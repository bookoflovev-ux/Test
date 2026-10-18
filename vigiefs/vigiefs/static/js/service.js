/* VigieFS — service 24 h/24 : composants, disponibilité, journal, arrêt / redémarrage, maintenance. */
'use strict';

const SERVICE_MODE = {
  'windows-service': 'Service Windows', systemd: 'Service systemd', 'tache-planifiee': 'Tâche planifiée + garde',
  console: 'Superviseur (console)', 'supervisé': 'Superviseur',
};
const COMP_STATUS = { ok: ['ok', 'OK'], repaired: ['info', 'Relancé'], degraded: ['warning', 'Dégradé'], error: ['critical', 'En erreur'] };
const SERVICE_STATUS = { ok: ['ok', 'En service'], repaired: ['ok', 'En service'], degraded: ['warning', 'Dégradé'], error: ['critical', 'En erreur'] };
const INCIDENT_KIND = { plantage: 'arrêt inattendu du processus', blocage: 'processus bloqué', sans_reponse: 'interface sans réponse', interruption: 'arrêt brutal du service ou du serveur' };
const JOURNAL_TONE = { incident: 'critical', interruption: 'critical', reparation: 'warning', relance: 'info', pret: 'ok', arret: 'neutral', redemarrage: 'neutral', maintenance: 'info', superviseur_demarre: 'neutral', superviseur_arrete: 'neutral', demarrage: 'neutral' };
const COMP_LABEL = { scheduler: 'Planificateur', monitor: 'Surveillance temps réel', events: "File d'événements", database: 'Base de données', disk: 'Espace disque des données', memory: 'Mémoire', jobs: 'Tâches de fond' };

const pctText = v => (v == null ? '—' : v.toLocaleString('fr-FR', { maximumFractionDigits: v >= 99.9 ? 3 : 2 }) + ' %');

function journalDetail(e) {
  const by = e.actor ? `par ${esc(e.actor)}` : '';
  const why = e.reason ? ` — ${esc(e.reason)}` : '';
  switch (e.event) {
    case 'incident': return `${esc(INCIDENT_KIND[e.kind] || e.kind)} (${esc(e.detail || '')})${e.ran_sec != null ? ` après ${fmtDuration(e.ran_sec)} de fonctionnement` : ''}`;
    case 'interruption': return `${esc(e.detail || '')}${e.since ? ` — dernier signe de vie ${fmtDate(e.since, true)}` : ''}`;
    case 'pret': return e.startup_sec != null ? `démarré en ${fmtDuration(e.startup_sec)}` : '';
    case 'relance': return `tentative ${fmtNum(e.attempt)}, après ${fmtDuration(e.delay)}`;
    case 'reparation': return `${esc(COMP_LABEL[e.component] || e.component || '')} : ${esc(e.detail || '')}`;
    case 'maintenance': return `${fmtDuration((e.minutes || 0) * 60)} ${by}${why}`;
    case 'superviseur_demarre': return esc(SERVICE_MODE[e.mode] || e.mode || '');
    case 'arret': case 'redemarrage': return `${by}${why}`;
    default: return '';
  }
}

async function viewService(view) {
  let s = await api('/api/service');
  const header = () => setPage('Service & disponibilité', 'Administration',
    can('admin') ? `${s.maintenance ? `<button class="btn" data-sact="unmaint">${icon('play')} Fin de maintenance</button>` : `<button class="btn" data-sact="maint">${icon('wrench')} Mode maintenance</button>`}
      <button class="btn" data-sact="backup">${icon('download')} Sauvegarder la base</button>
      <button class="btn" data-sact="restart"${s.can_restart ? '' : ' disabled title="Disponible en mode service uniquement"'}>${icon('refresh')} Redémarrer</button>
      <button class="btn danger" data-sact="stop">${icon('stop')} Arrêter</button>` : '',
    'Fonctionnement 24 h/24, 7 j/7 : relance automatique, auto-réparation, journal de disponibilité. Seul un arrêt volontaire arrête VigieFS.');
  const render = () => {
    const comps = s.components || [];
    const okCount = comps.filter(c => c.status === 'ok' || c.status === 'repaired').length;
    const sup = s.supervisor;
    const av30 = s.availability['30d'], av7 = s.availability['7d'];
    const [stTone, stLabel] = SERVICE_STATUS[s.status] || ['neutral', s.status];
    const modeLabel = s.supervised ? (SERVICE_MODE[s.mode] || s.mode) : 'Console (sans superviseur)';
    const inc = sup && sup.last_incident;
    view.innerHTML = `
      ${s.maintenance ? `<div class="banner info" style="margin-bottom:16px">${icon('wrench')}<div><b>Mode maintenance</b> jusqu'au ${fmtDate(s.maintenance.until)} (${esc(s.maintenance.by || '')}${s.maintenance.reason ? ' — ' + esc(s.maintenance.reason) : ''}) : analyses, archivages, contrôles de quotas et alertes de disponibilité planifiés sont suspendus ; la surveillance temps réel et la détection de rançongiciel restent actives.</div></div>` : ''}
      ${s.supervised ? '' : `<div class="banner warning" style="margin-bottom:16px">${icon('alert')}<div><b>VigieFS tourne sans superviseur</b> : il ne sera pas relancé automatiquement en cas d'incident ni après un redémarrage du serveur. Pour un fonctionnement 24 h/24, installez le service (<code>scripts\\install-windows.ps1</code> ou <code>scripts/install-linux.sh</code>) ou lancez <code>python -m vigiefs supervise</code>.</div></div>`}
      <div class="tiles">
        ${tile({ label: 'État du service', value: `<span class="sev ${stTone}" style="font-size:15px">${esc(stLabel)}</span>`, ic: 'activity', tone: stTone === 'ok' ? 'good' : stTone, sub: `${esc(modeLabel)}<br>${okCount} / ${comps.length} composants OK`, hero: true })}
        ${tile({ label: 'Disponibilité 30 j', value: pctText(av30.pct), ic: 'check', tone: av30.pct == null || av30.pct >= 99.9 ? 'good' : av30.pct >= 99 ? 'warning' : 'critical', sub: av30.pct == null ? 'mesurée en mode service' : `coupure subie : ${fmtDuration(av30.unplanned_sec)}` })}
        ${tile({ label: 'Disponibilité 7 j', value: pctText(av7.pct), ic: 'check', tone: av7.pct == null || av7.pct >= 99.9 ? 'good' : 'warning', sub: plural(av7.incidents, 'incident', 'incidents') })}
        ${tile({ label: 'En service depuis', value: fmtDuration(s.uptime_sec), ic: 'play', sub: `depuis le ${fmtDate(s.started_at)}` })}
        ${tile({ label: 'Relances automatiques', value: fmtNum(sup ? sup.incidents : 0), ic: 'refresh', tone: sup && sup.incidents ? 'warning' : 'good', sub: inc ? `dernière ${fmtAgo(inc.at)}` : 'aucun incident' })}
      </div>
      <div class="grid cols-2">
        <div class="card flush">${cardHead('Composants', 'Contrôlés toutes les 10 s par la garde interne ; un composant arrêté est relancé à chaud.')}
          <table><tbody>${comps.map(c => `<tr><td style="width:1%">${badge(COMP_STATUS, c.status)}</td>
            <td><div class="strong">${esc(c.label)}</div><div class="cell-sub">${esc(c.detail || '')}</div></td>
            <td class="small muted nowrap" style="text-align:right">${c.repairs ? `relancé ${plural(c.repairs, 'fois', 'fois')}<br>${fmtAgo(c.last_repair)}` : ''}</td></tr>`).join('') || `<tr><td>${emptyState('info', 'Premier contrôle en cours')}</td></tr>`}
          </tbody></table></div>
        <div class="card">${cardHead('Garanties de fonctionnement continu')}
          <ul class="guarantees">
            <li>${icon('check')}<div><b>Superviseur</b> — le moteur est relancé en quelques secondes s'il s'arrête, se bloque (plus de battement de cœur pendant 5 min) ou si l'interface ne répond plus ; délai progressif de 1 s à 60 s, sans limite de tentatives.</div></li>
            <li>${icon('check')}<div><b>Service système</b> — Windows (actions de récupération, démarrage automatique différé) ou systemd (Restart=on-failure, chien de garde WatchdogSec) relance le superviseur lui-même.</div></li>
            <li>${icon('check')}<div><b>Auto-réparation</b> — planificateur, observateur de fichiers, écriture du journal et surveillance de chaque dossier relancés à chaud, sans coupure.</div></li>
            <li>${icon('check')}<div><b>Réseau</b> — l'accès aux partages est sondé avec un délai maximal : un NAS ou un partage UNC qui ne répond plus ne bloque pas VigieFS ; la surveillance reprend seule au retour du partage.</div></li>
            <li>${icon('check')}<div><b>Base de données</b> — point de contrôle WAL horaire, contrôle d'intégrité et sauvegarde en ligne quotidiens (7 copies), restauration en une commande.</div></li>
            <li>${icon('check')}<div><b>Ressources</b> — alerte d'espace disque des données, redémarrage propre planifié si la mémoire dépasse la limite (sans couper une tâche en cours).</div></li>
            <li>${icon('check')}<div><b>Arrêt sur décision uniquement</b> — bouton Arrêter, <code>python -m vigiefs stop</code>, <code>Stop-Service VigieFS</code> ou <code>systemctl stop vigiefs</code> ; l'arrêt est journalisé (qui, quand, pourquoi) et n'est jamais relancé automatiquement.</div></li>
          </ul></div>
      </div>
      <div class="card flush">${cardHead('Journal de disponibilité', '30 derniers jours : démarrages, incidents, relances, réparations, arrêts volontaires')}
        <div class="table-wrap tall"><table><thead><tr><th>Date</th><th>Événement</th><th>Détail</th></tr></thead><tbody>
        ${s.journal.map(e => `<tr><td class="nowrap">${fmtDate(e.ts, true)}</td><td class="nowrap"><span class="sev ${JOURNAL_TONE[e.event] || 'neutral'}">${esc(e.label)}</span></td><td>${journalDetail(e)}</td></tr>`).join('') || `<tr><td colspan="3">${emptyState('activity', 'Journal vide', 'Le journal de disponibilité est alimenté par le superviseur (mode service).')}</td></tr>`}
        </tbody></table></div></div>`;
  };
  const refresh = async () => { s = await api('/api/service'); header(); bindHead(); render(); };

  const waitBack = async (startedBefore) => {
    view.insertAdjacentHTML('afterbegin', `<div class="banner info" id="svc-wait" style="margin-bottom:16px"><span class="dot run"></span>Redémarrage en cours : reconnexion automatique dès que VigieFS est de nouveau en service…</div>`);
    for (let i = 0; i < 90; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try { const n = await api('/api/service'); if (n.started_at !== startedBefore) { toast('VigieFS a redémarré'); s = n; header(); bindHead(); render(); return; } } catch (e) { /* hors ligne pendant le redémarrage */ }
    }
    toast('Redémarrage non confirmé : vérifiez « python -m vigiefs status »', 'error');
  };
  const actions = {
    maint: () => modal({ title: 'Mode maintenance', submit: 'Activer',
      body: `<p class="small ink-2" style="margin-top:0">Pendant une intervention (migration, sauvegarde lourde, mises à jour), les actions planifiées sont suspendues et le moniteur Datto reste au vert. La surveillance temps réel et la détection de rançongiciel restent actives. Fin automatique à l'échéance.</p>
        <div class="form-grid">${field('Durée', sel('minutes', [['30', '30 min'], ['60', '1 heure'], ['120', '2 heures'], ['240', '4 heures'], ['480', '8 heures'], ['1440', '24 heures'], ['4320', '3 jours']], '120'))}
        ${field('Motif', inp('reason', '', 'text', 'placeholder="ex. migration du NAS"'), '', true)}</div>`,
      onSubmit: async f => { const d = formData(f); await api('/api/service/maintenance', { method: 'POST', body: { minutes: Number(d.minutes), reason: d.reason || '' } }); toast('Mode maintenance activé'); refresh(); } }),
    unmaint: async () => { await api('/api/service/maintenance', { method: 'DELETE' }); toast('Mode maintenance terminé'); refresh(); },
    backup: async () => { const r = await api('/api/service/db-backup', { method: 'POST' }); toast(`Base contrôlée et sauvegardée (${r.backup_file})`); refresh(); },
    restart: () => modal({ title: 'Redémarrer VigieFS', submit: 'Redémarrer', size: 'sm',
      body: `<p style="margin-top:0">Le superviseur arrête proprement le moteur (tâches en cours interrompues puis signalées) et le relance immédiatement. L'interface est indisponible quelques secondes.</p>${field('Motif (journalisé)', inp('reason', '', 'text'), '', true)}`,
      onSubmit: async f => { const before = s.started_at; await api('/api/service/action', { method: 'POST', body: { action: 'restart', reason: formData(f).reason || '' } }); waitBack(before); } }),
    stop: () => modal({ title: 'Arrêter VigieFS', submit: 'Arrêter le service', size: 'sm', danger: true,
      body: `<div class="banner warning" style="margin-bottom:12px">${icon('alert')}<div>La supervision, les alertes, les quotas stricts et la détection de rançongiciel seront <b>arrêtés</b> jusqu'à un redémarrage manuel. Cet arrêt volontaire ne sera pas relancé automatiquement.</div></div>
        ${field('Motif (journalisé)', inp('reason', '', 'text', 'placeholder="ex. maintenance du serveur"'), '', true)}
        ${field('Confirmation', inp('confirm', '', 'text', 'placeholder="ARRÊTER" autocomplete="off"'), 'Saisissez ARRÊTER pour confirmer.', true)}`,
      onSubmit: async f => {
        const d = formData(f);
        await api('/api/service/action', { method: 'POST', body: { action: 'stop', reason: d.reason || '', confirm: d.confirm || '' } });
        clearTimers();
        view.innerHTML = emptyState('stop', 'VigieFS est arrêté', `Arrêt volontaire enregistré. Pour le relancer : <code>Start-Service VigieFS</code> (Windows), <code>systemctl start vigiefs</code> (Linux) ou <code>start-vigiefs.bat</code>.`);
      } }),
  };
  const bindHead = () => $$('[data-sact]').forEach(b => { b.onclick = guard(() => actions[b.dataset.sact]()); });
  header(); bindHead(); render();
  every(10000, guard(async () => { if (!$('#svc-wait') && !$('#modal-root').innerHTML) { s = await api('/api/service'); render(); } }));
}
