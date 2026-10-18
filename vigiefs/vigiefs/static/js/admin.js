/* VigieFS — administration : paramètres, utilisateurs, clés d'API, audit, tâches */
'use strict';

const SETTINGS_TABS = [['account', 'Mon compte', 'viewer'], ['general', 'Général', 'admin'], ['notifications', 'Notifications', 'admin'], ['security', 'Sécurité & filtrage', 'admin'], ['storage', 'Stockage & réparation', 'admin'], ['compliance', 'Droits, chemins & quotas', 'admin'], ['retention', 'Prévisions & rétention', 'admin'], ['users', 'Utilisateurs', 'admin'], ['apikeys', "Clés d'API", 'admin'], ['audit', "Journal d'audit", 'admin'], ['integration', 'Intégration & système', 'viewer']];
const listToText = l => (Array.isArray(l) ? l.join(', ') : l || '');
const textToList = t => t.split(/[,;\n]/).map(x => x.trim()).filter(Boolean);

async function viewSettings(view, query, tab) {
  const tabs = SETTINGS_TABS.filter(t => can(t[2]));
  tab = tab && tabs.some(t => t[0] === tab) ? tab : (can('admin') ? 'general' : 'account');
  setPage('Paramètres', 'Administration', '', 'Configuration de VigieFS, des notifications, du stockage et des accès.');
  view.innerHTML = `<nav class="tabs">${tabs.map(([k, l]) => `<a href="#/settings/${k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</nav><div id="pane"></div>`;
  const pane = $('#pane', view);
  const st = can('admin') ? await api('/api/settings') : null;
  const save = async (key, body, msg = 'Paramètres enregistrés') => { await api(`/api/settings/${key}`, { method: 'PUT', body }); toast(msg); };
  const formCard = (title, inner, id, extra = '', desc = '') => `<form class="card form" id="${id}">${cardHead(title, desc)}${inner}<div class="btn-row"><button class="btn primary" type="submit">Enregistrer</button>${extra}</div></form>`;
  const onSubmit = (id, fn) => $('#' + id, pane).addEventListener('submit', guard(async e => { e.preventDefault(); await fn(formData(e.target)); }));

  if (tab === 'account') {
    pane.innerHTML = formCard('Changer mon mot de passe', `<div class="form-grid">
      ${field('Mot de passe actuel', inp('current_password', '', 'password', 'autocomplete="current-password"'), '', true)}
      ${field('Nouveau mot de passe', inp('new_password', '', 'password', 'autocomplete="new-password"'), '10 caractères min., 3 types de caractères.')}
      ${field('Confirmation', inp('confirm', '', 'password', 'autocomplete="new-password"'))}</div>`, 'f-pwd');
    onSubmit('f-pwd', async d => { if (d.new_password !== d.confirm) throw new Error('Les mots de passe ne correspondent pas.'); await api('/api/auth/password', { method: 'POST', body: d }); toast('Mot de passe modifié'); $('#f-pwd', pane).reset(); });
  }
  if (tab === 'general') {
    const g = st.general;
    pane.innerHTML = formCard('Général', `<div class="form-grid">
      ${field("Nom de l'entreprise", inp('company_name', g.company_name), 'Affiché dans l\'interface et les notifications.')}
      ${field('Durée de session (heures)', inp('session_hours', g.session_hours, 'number', 'min="1" max="720"'))}
      ${field('Fréquence du planificateur (s)', inp('scheduler_tick_sec', g.scheduler_tick_sec, 'number', 'min="5" max="3600"'), 'Contrôle de disponibilité des dossiers et déclenchement des analyses.')}</div>`, 'f-gen');
    onSubmit('f-gen', async d => { await save('general', d); state.company = d.company_name; });
  }
  if (tab === 'notifications') {
    const s = st.smtp; const w = st.webhook;
    const sevOpts = [['info', 'Info et plus'], ['warning', 'Avertissement et plus'], ['critical', 'Critique uniquement']];
    pane.innerHTML = formCard('E-mail (SMTP)', `${chk('enabled', 'Activer les notifications par e-mail', s.enabled)}<div class="form-grid">
      ${field('Serveur SMTP', inp('host', s.host, 'text', 'placeholder="smtp.office365.com"'))}
      ${field('Port', inp('port', s.port, 'number'), '587 (STARTTLS) ou 465 (SSL)')}
      ${field('Utilisateur', inp('username', s.username, 'text', 'autocomplete="off"'))}
      ${field('Mot de passe', inp('password', s.password, 'password', 'autocomplete="new-password"'))}
      ${field('Expéditeur', inp('sender', s.sender, 'email'))}
      ${field('Gravité minimale', sel('min_severity', sevOpts, s.min_severity))}
      ${field('Destinataires', inp('recipients', listToText(s.recipients)), 'Séparés par des virgules (ex. hotline@, astreinte@).', true)}
      <div class="field">${chk('starttls', 'STARTTLS', s.starttls)}</div></div>`, 'f-smtp', '<button class="btn" type="button" data-test="email">Envoyer un test</button>') +
      formCard('Webhook (centre de supervision, Teams, Slack, PSA…)', `${chk('enabled', 'Activer le webhook', w.enabled)}<div class="form-grid">
      ${field('URL', inp('url', w.url, 'url', 'placeholder="https://supervision.exemple.fr/hooks/vigiefs"'), 'Reçoit un POST JSON pour chaque nouvelle alerte ou escalade.', true)}
      ${field('Secret de signature', inp('secret', w.secret, 'password', 'autocomplete="new-password"'), 'En-tête X-VigieFS-Signature: sha256=HMAC(corps).')}
      ${field('Gravité minimale', sel('min_severity', sevOpts, w.min_severity))}</div>`, 'f-hook', '<button class="btn" type="button" data-test="webhook">Envoyer un test</button>');
    onSubmit('f-smtp', d => save('smtp', { ...d, recipients: textToList(d.recipients) }));
    onSubmit('f-hook', d => save('webhook', d));
    pane.addEventListener('click', guard(async e => { const b = e.target.closest('[data-test]'); if (!b) return; await api(`/api/settings/test-${b.dataset.test}`, { method: 'POST' }); toast('Test envoyé avec succès'); }));
  }
  if (tab === 'security') {
    const r = st.ransomware; const sc = st.screening;
    pane.innerHTML = formCard('Détection de rançongiciel', `${chk('enabled', 'Activer la détection (temps réel + analyses)', r.enabled)}<div class="form-grid">
      ${field("Seuil d'activité massive", inp('burst_threshold', r.burst_threshold, 'number', 'min="10"'), 'Nombre de modifications / renommages / suppressions…')}
      ${field('…dans une fenêtre de (secondes)', inp('burst_window_sec', r.burst_window_sec, 'number', 'min="5"'))}
      ${field('Extensions de fichiers chiffrés', `<textarea name="extensions">${esc(listToText(r.extensions))}</textarea>`, '', true)}
      ${field('Motifs de notes de rançon', `<textarea name="note_patterns">${esc(listToText(r.note_patterns))}</textarea>`, 'Joker * accepté, insensible à la casse.', true)}</div>`, 'f-rs') +
      formCard('Filtrage de fichiers', `<div class="form-grid">
      ${field('Extensions bloquées par défaut', `<textarea name="default_blocked_extensions">${esc(listToText(sc.default_blocked_extensions))}</textarea>`, 'Appliquées aux dossiers dont la liste personnalisée est vide.', true)}
      ${field('Dossier de quarantaine', inp('quarantine_dir', sc.quarantine_dir, 'text', 'placeholder="E:\\VigieFS\\Quarantaine"'), 'Doit être <b>en dehors</b> des dossiers supervisés. Vide = quarantaine désactivée.', true)}</div>`, 'f-sc');
    onSubmit('f-rs', d => save('ransomware', { ...d, extensions: textToList(d.extensions), note_patterns: textToList(d.note_patterns) }));
    onSubmit('f-sc', d => save('screening', { ...d, default_blocked_extensions: textToList(d.default_blocked_extensions) }));
  }
  if (tab === 'storage') {
    const rc = st.recycle; const ar = st.archive; const cl = st.cleanup; const rp = st.repair;
    pane.innerHTML = formCard('Corbeille VigieFS', `<div class="form-grid">
      ${field('Dossier de la corbeille', inp('dir', rc.dir, 'text', 'placeholder="Vide = dossier de données VigieFS\\corbeille"'), 'Idéalement sur le même volume que les partages (déplacement instantané). Doit être <b>hors</b> des dossiers supervisés.', true)}
      ${field('Rétention (jours)', inp('retention_days', rc.retention_days, 'number', 'min="0"'), 'Purge automatique au-delà. 0 = jamais.')}</div>`, 'f-rc', '', 'Tout élément nettoyé, dédoublonné, supprimé ou remplacé y est conservé.') +
      formCard("Stockage d'archive à froid", `<div class="form-grid">
      ${field("Destination des archives", inp('root', ar.root, 'text', 'placeholder="\\\\NAS-ARCHIVE\\VigieFS  ou  E:\\Archives"'), 'NAS, disque d\'archive ou partage réseau économique. Arborescence : Client\\Dossier\\chemin d\'origine.', true)}
      <div class="field full">${chk('verify_hash', 'Vérifier chaque copie par empreinte SHA-256 avant de retirer l\'original (recommandé)', ar.verify_hash)}</div></div>`, 'f-ar') +
      formCard('Détection des fichiers inutiles', `<div class="form-grid cols-3">
      ${field('Temporaires après (jours)', inp('temp_days', cl.temp_days, 'number', 'min="0"'), 'Évite de toucher un document ouvert.')}
      ${field('Sauvegardes auto après (jours)', inp('backup_days', cl.backup_days, 'number', 'min="0"'))}
      ${field('Journaux après (jours)', inp('logs_days', cl.logs_days, 'number', 'min="0"'))}
      ${field('Vides après (jours)', inp('empty_days', cl.empty_days, 'number', 'min="0"'), 'Fichiers et dossiers vides.')}</div>`, 'f-cl', '', 'Ancienneté minimale avant qu\'un fichier soit considéré comme inutile.') +
      formCard('Réparation de documents', `<div class="form-grid">
      ${field('Chemin de LibreOffice (optionnel)', inp('libreoffice_path', rp.libreoffice_path, 'text', 'placeholder="Détection automatique"'), 'soffice.exe — améliore la réparation des anciens formats .doc/.xls/.ppt.')}
      ${field('Taille maximale (Mo)', inp('max_size_mb', rp.max_size_mb, 'number', 'min="1"'), 'Pour l\'atelier et le contrôle d\'intégrité.')}</div>`, 'f-rp');
    onSubmit('f-rc', d => save('recycle', d));
    onSubmit('f-ar', d => save('archive', d));
    onSubmit('f-cl', d => save('cleanup', d));
    onSubmit('f-rp', d => save('repair', d));
  }
  if (tab === 'compliance') {
    const p = st.paths; const r = st.rights; const q = st.quotas; const sv = st.sensitive;
    pane.innerHTML = formCard('Longueur des chemins et nommage', `<div class="form-grid cols-3">
      ${field("Seuil d'avertissement (caractères)", inp('warn_len', p.warn_len, 'number', 'min="50"'))}
      ${field('Seuil critique (MAX_PATH)', inp('crit_len', p.crit_len, 'number', 'min="50"'), 'Windows : 260 caractères.')}
      ${field('Limite OneDrive / SharePoint', inp('cloud_len', p.cloud_len, 'number', 'min="100"'), 'Microsoft : 400 caractères.')}
      ${field('Longueur maximale d\'un nom', inp('name_max', p.name_max, 'number', 'min="10"'))}
      ${field('Profondeur recommandée (niveaux)', inp('max_depth', p.max_depth, 'number', 'min="2"'))}
      ${field('Dossier surchargé au-delà de (éléments)', inp('crowded_items', p.crowded_items, 'number', 'min="100"'))}
      ${field('Profondeur des statistiques d\'architecture', inp('arch_depth', p.arch_depth, 'number', 'min="1" max="8"'))}
      <div class="field">${chk('check_cloud', 'Contrôler la compatibilité OneDrive / SharePoint', p.check_cloud)}</div>
      <div class="field">${chk('realtime_alert', 'Alerter en temps réel à la création d\'un chemin trop long', p.realtime_alert)}</div></div>`, 'f-paths') +
      formCard("Droits d'accès", `<div class="form-grid">
      ${field('Préfixe des groupes proposés', inp('group_prefix', r.group_prefix), 'Ex. GDL → GDL_CLIENT_PARTAGE_DOSSIER_RW / _RO.')}
      ${field('Unité d\'organisation par défaut (AD)', inp('groups_ou', r.groups_ou, 'text', 'placeholder="OU=Groupes,DC=client,DC=local"'))}
      <div class="field full">${chk('scheduled_scans', 'Analyses des droits planifiées (détection des modifications)', r.scheduled_scans)}</div></div>`, 'f-rights') +
      formCard('Quotas', `<div class="form-grid">
      ${field('Contrôle des quotas stricts (minutes)', inp('check_interval_min', q.check_interval_min, 'number', 'min="1"'))}
      ${field('Déverrouillage sous (%)', inp('unlock_below_pct', q.unlock_below_pct, 'number', 'min="50" max="100"'), 'Évite les verrouillages / déverrouillages répétés.')}
      ${field('Modèles de quota', `<textarea name="templates" style="min-height:160px">${esc((q.templates || []).map(t => `${t.name} ; ${t.limit_gb} ; ${t.mode === 'hard' ? 'strict' : 'souple'} ; ${t.warn_pct} ; ${t.crit_pct}`).join('\n'))}</textarea>`, 'Une ligne par modèle : nom ; limite en Go ; strict|souple ; seuil avertissement % ; seuil critique %', true)}</div>`, 'f-quotas') +
      formCard('Données sensibles (RGPD)', `<div class="form-grid">
      ${field('Taille maximale lue par fichier (Mo)', inp('max_file_mb', sv.max_file_mb, 'number', 'min="1"'))}
      ${field('Extensions analysées', `<textarea name="extensions">${esc(listToText(sv.extensions))}</textarea>`, '', true)}</div>`, 'f-sens');
    onSubmit('f-paths', d => save('paths', d));
    onSubmit('f-rights', d => save('rights', d));
    onSubmit('f-quotas', d => {
      const templates = d.templates.split('\n').map(l => l.split(';').map(x => x.trim())).filter(x => x[0]).map(([name, gb, mode, w, c]) => {
        if (!(Number(String(gb).replace(',', '.')) > 0)) throw new Error(`Limite invalide pour « ${name} »`);
        return { name, limit_gb: Number(String(gb).replace(',', '.')), mode: /strict|hard/i.test(mode || '') ? 'hard' : 'soft', warn_pct: Number(w) || 85, crit_pct: Number(c) || 95 };
      });
      return save('quotas', { check_interval_min: d.check_interval_min, unlock_below_pct: d.unlock_below_pct, templates });
    });
    onSubmit('f-sens', d => save('sensitive', { max_file_mb: d.max_file_mb, extensions: textToList(d.extensions) }));
  }
  if (tab === 'retention') {
    const f = st.forecast; const r = st.retention;
    pane.innerHTML = formCard('Prévision de saturation des quotas', `${chk('enabled', 'Alerter avant saturation (tendance sur 30 jours)', f.enabled)}<div class="form-grid">
      ${field('Avertissement si saturation dans moins de (jours)', inp('warn_days', f.warn_days, 'number', 'min="1"'))}
      ${field('Critique si saturation dans moins de (jours)', inp('crit_days', f.crit_days, 'number', 'min="1"'))}</div>`, 'f-fc') +
      formCard('Rétention des données', `<div class="form-grid">
      ${field("Journal d'activité (jours)", inp('events_days', r.events_days, 'number', 'min="1"'))}
      ${field('Analyses conservées par dossier', inp('scans_per_share', r.scans_per_share, 'number', 'min="5"'))}
      ${field('Alertes résolues (jours)', inp('resolved_alerts_days', r.resolved_alerts_days, 'number', 'min="1"'))}</div>`, 'f-ret');
    onSubmit('f-fc', d => save('forecast', d));
    onSubmit('f-ret', d => save('retention', d));
  }
  if (tab === 'users') {
    const load = async () => {
      const users = await api('/api/users');
      pane.innerHTML = `<div class="card"><div class="card-head"><h2>Utilisateurs</h2><div class="spacer"></div><button class="btn primary" data-act="new">${icon('plus')} Nouvel utilisateur</button></div>
        <p class="small muted"><b>Lecture seule</b> : consultation. <b>Opérateur</b> : clients, dossiers, analyses, acquittement, renommage. <b>Administrateur</b> : tout, dont suppression de fichiers et paramètres.</p>
        <table><thead><tr><th>Identifiant</th><th>Nom</th><th>Rôle</th><th>Dernière connexion</th><th></th></tr></thead><tbody>
        ${users.map(u => `<tr><td><b>${esc(u.username)}</b></td><td>${esc(u.full_name)}</td><td>${ROLE_LABEL[u.role]}</td><td>${fmtAgo(u.last_login)}</td>
          <td class="nowrap"><button class="btn sm" data-act="edit" data-id="${u.id}">Modifier</button>${u.id !== state.user.id ? ` <button class="btn sm danger" data-act="del" data-id="${u.id}">Supprimer</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
      pane._users = users;
    };
    const userForm = (u = {}) => `<div class="form-grid">
      ${field('Identifiant', inp('username', u.username, 'text', u.id ? 'readonly' : 'required'))}
      ${field('Nom complet', inp('full_name', u.full_name))}
      ${field('Rôle', sel('role', Object.entries(ROLE_LABEL), u.role || 'operator'))}
      ${field(u.id ? 'Nouveau mot de passe (optionnel)' : 'Mot de passe', inp('password', '', 'password', 'autocomplete="new-password"'), '10 caractères min., 3 types.')}</div>`;
    bindActions(pane, {
      new: () => modal({ title: 'Nouvel utilisateur', body: userForm(), submit: 'Créer', onSubmit: async f => { await api('/api/users', { method: 'POST', body: formData(f) }); toast('Utilisateur créé'); load(); } }),
      edit: el => { const u = pane._users.find(x => x.id == el.dataset.id); modal({ title: `Modifier ${u.username}`, body: userForm(u), onSubmit: async f => { const d = formData(f); if (!d.password) d.password = null; await api(`/api/users/${u.id}`, { method: 'PUT', body: d }); toast('Utilisateur mis à jour'); load(); } }); },
      del: el => { const u = pane._users.find(x => x.id == el.dataset.id); confirmBox('Supprimer le compte', `Supprimer le compte <b>${esc(u.username)}</b> ?`, async () => { await api(`/api/users/${u.id}`, { method: 'DELETE' }); toast('Compte supprimé'); load(); }, 'Supprimer'); },
    });
    await load();
  }
  if (tab === 'apikeys') {
    const load = async () => {
      const keys = await api('/api/apikeys');
      pane.innerHTML = `<div class="card"><div class="card-head"><h2>Clés d'API</h2><div class="spacer"></div><button class="btn primary" data-act="new">${icon('plus')} Nouvelle clé</button></div>
        <p class="small muted">Pour le centre de supervision, Datto RMM, Prometheus ou vos scripts. Transmettez la clé dans l'en-tête <code>X-API-Key</code> ou <code>Authorization: Bearer</code>. Privilégiez le rôle « Lecture seule ».</p>
        <table><thead><tr><th>Nom</th><th>Préfixe</th><th>Rôle</th><th>Créée</th><th>Dernière utilisation</th><th></th></tr></thead><tbody>
        ${keys.length ? keys.map(k => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.prefix)}…</td><td>${ROLE_LABEL[k.role]}</td><td>${fmtDate(k.created_at)}</td><td>${fmtAgo(k.last_used)}</td><td><button class="btn sm danger" data-act="del" data-id="${k.id}">Révoquer</button></td></tr>`).join('') : '<tr><td colspan="6" class="empty">Aucune clé</td></tr>'}</tbody></table></div>`;
    };
    bindActions(pane, {
      new: () => modal({ title: "Nouvelle clé d'API", body: `<div class="form-grid">${field('Nom', inp('name', '', 'text', 'required placeholder="Datto RMM - monitor"'))}${field('Rôle', sel('role', Object.entries(ROLE_LABEL), 'viewer'))}</div>`, submit: 'Générer',
        onSubmit: async f => { const r = await api('/api/apikeys', { method: 'POST', body: formData(f) }); load(); modal({ title: 'Clé générée', noFooter: true, body: `<p>Copiez cette clé maintenant, elle ne sera <b>plus jamais affichée</b> :</p><div class="secret-box">${esc(r.key)}</div>` }); return false; } }),
      del: el => confirmBox('Révoquer la clé', 'Les intégrations utilisant cette clé cesseront de fonctionner immédiatement.', async () => { await api(`/api/apikeys/${el.dataset.id}`, { method: 'DELETE' }); toast('Clé révoquée'); load(); }, 'Révoquer'),
    });
    await load();
  }
  if (tab === 'audit') {
    pane.innerHTML = `<div class="card"><div class="filters"><input type="search" id="aq" placeholder="Filtrer (utilisateur, action, cible)…"></div><div id="al" class="table-wrap tall"></div></div>`;
    const load = async () => {
      const q = $('#aq', pane).value.trim();
      const rows = await api('/api/audit?limit=1000' + (q ? '&q=' + encodeURIComponent(q) : ''));
      $('#al', pane).innerHTML = `<table><thead><tr><th>Date</th><th>Acteur</th><th>Action</th><th>Cible</th><th>Détails</th></tr></thead><tbody>${rows.map(r => `<tr><td class="nowrap">${fmtDate(r.ts, true)}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td class="path">${esc(r.target)}</td><td class="small">${esc(r.details)}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Vide</td></tr>'}</tbody></table>`;
    };
    $('#aq', pane).addEventListener('input', guard(load));
    await load();
  }
  if (tab === 'integration') {
    const sys = await api('/api/system');
    const base = location.origin;
    pane.innerHTML = `<div class="grid cols-2"><div class="card"><h2>Système</h2><dl class="kv">
        <dt>Version</dt><dd>${esc(sys.version)}</dd><dt>Serveur</dt><dd>${esc(sys.hostname)}</dd><dt>Plateforme</dt><dd>${esc(sys.platform)} · Python ${esc(sys.python)}</dd>
        ${sys.data_dir ? `<dt>Données</dt><dd class="mono small">${esc(sys.data_dir)}</dd>` : ''}
        <dt>Démarré depuis</dt><dd>${fmtDuration(sys.uptime_sec)}</dd><dt>Événements enregistrés</dt><dd>${fmtNum(sys.events_recorded)} (file d'attente : ${sys.queue})</dd>
        <dt>Analyses en cours</dt><dd>${sys.scans_running}</dd></dl></div>
      <div class="card"><h2>Points d'intégration</h2><dl class="kv">
        <dt>Statut global</dt><dd class="mono small">GET ${esc(base)}/api/v1/status</dd>
        <dt>Prometheus</dt><dd class="mono small">GET ${esc(base)}/metrics</dd>
        <dt>Documentation API</dt><dd><a href="/api/docs" target="_blank" rel="noopener">${esc(base)}/api/docs</a></dd>
        <dt>Webhook sortant</dt><dd>Paramètres › Notifications</dd></dl>
        <h3>Datto RMM</h3><p class="small">Le script <code>integrations/datto-rmm/VigieFS-Monitor.ps1</code> s'importe comme composant « Monitor » : il interroge <code>/api/v1/status</code> et lève une alerte Datto si VigieFS signale un problème.</p>
        <h3>Exemple</h3><div class="secret-box small">curl -H "X-API-Key: vfs_…" ${esc(base)}/api/v1/status</div></div></div>`;
  }
}


// ------------------------------------------------------------------ tâches
async function viewJobs(view) {
  setPage('Tâches', 'Outils', '', 'Analyses de doublons, nettoyages, archivages, contrôles et réparations en arrière-plan.');
  view.innerHTML = `<div class="card flush"><div class="filters">
    ${sel('status', [['', 'Tous les statuts'], ['active', 'En cours'], ['done', 'Terminées'], ['failed', 'En échec'], ['cancelled', 'Annulées']], '', 'data-f')}
    ${sel('type', [['', 'Tous les types'], ['duplicates', 'Doublons'], ['duplicates_resolve', 'Suppression de doublons'], ['cleanup', 'Nettoyage'], ['archive_simulate', "Simulation d'archivage"], ['archive_run', 'Archivage'], ['archive_restore', 'Restauration'], ['integrity', 'Intégrité'], ['repair_batch', 'Réparation']], '', 'data-f')}
  </div><div id="jl" style="margin-top:14px"></div></div>`;
  const link = j => {
    if (!j.share_id) return j.type === 'duplicates' && j.client_id ? `#/duplicates?client=${j.client_id}` : '';
    const tabFor = { duplicates: 'duplicates', duplicates_resolve: 'duplicates', cleanup: 'cleanup', archive_simulate: 'archive', archive_run: 'archive', integrity: 'integrity', repair_batch: 'integrity' };
    return `#/share/${j.share_id}/${tabFor[j.type] || 'overview'}`;
  };
  const load = async () => {
    const p = new URLSearchParams();
    $$('[data-f]', view).forEach(el => { if (el.value) p.set(el.name, el.value); });
    const rows = await api('/api/jobs?limit=200&' + p);
    $('#jl', view).innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Tâche</th><th>Statut</th><th style="width:220px">Progression</th><th>Lancée</th><th>Durée</th><th></th></tr></thead><tbody>
      ${rows.map(j => { const pr = j.progress || {}; const total = pr.total || pr.to_hash || 0; const done = pr.done ?? pr.hashed ?? 0; const running = ['queued', 'running'].includes(j.status); return `<tr ${link(j) ? `class="clickable" data-go="${link(j)}"` : ''}>
        <td><div class="cell-title">${esc(j.title)}</div><div class="cell-sub">${esc(j.client_name || '')}${j.error ? ` · <span style="color:var(--critical-ink)">${esc(j.error)}</span>` : ''}</div></td>
        <td>${badge(JOB_STATUS, j.status)}</td>
        <td>${running ? progressBar(done, total) + `<div class="cell-sub">${total ? `${compact(done)} / ${compact(total)}` : pr.scanned ? `${compact(pr.scanned)} fichiers` : pr.checked ? `${compact(pr.checked)} documents` : 'démarrage…'}</div>` : '<span class="muted small">—</span>'}</td>
        <td class="nowrap">${fmtDate(j.created_at)}<div class="cell-sub">${esc(j.actor)}</div></td>
        <td class="nowrap">${j.started_at ? fmtDuration((j.finished_at || Date.now() / 1000) - j.started_at) : '—'}</td>
        <td>${running && can('operator') ? `<button class="btn sm" data-cancel-job="${j.id}">Annuler</button>` : ''}</td></tr>`; }).join('')}</tbody></table></div>`
      : emptyState('tasks', 'Aucune tâche', 'Les traitements lancés apparaîtront ici.');
  };
  rowLinks(view);
  view.addEventListener('change', e => { if (e.target.matches('[data-f]')) load(); });
  await load();
  every(2000, guard(load));
}
