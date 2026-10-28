/* =====================================================================
 * Overdrive → Cockpit (Nexus) : panneau de supervision du PC.
 * Autonome, sans dépendance. Lit http://127.0.0.1:8765/api/nexus toutes les 5 s.
 * Intégration : <section data-overdrive></section> + ce script (ou OverdrivePanel.mount(el)).
 * Réglages (URL, jeton pour les actions) : localStorage « overdrive-link-v1 », jamais
 * mélangés aux données du cockpit.
 * ===================================================================== */
(function () {
  'use strict';
  var KEY = 'overdrive-link-v1';
  var STYLE_ID = 'odp-style';

  function cfg() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
  function saveCfg(c) { try { localStorage.setItem(KEY, JSON.stringify(c)); } catch (e) { /* stockage bloqué */ } }
  function base() { return (cfg().url || 'http://127.0.0.1:8765').replace(/\/+$/, ''); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function bytes(n) { if (n == null) return '—'; var u = ['o', 'Ko', 'Mo', 'Go', 'To'], i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(1) : Math.round(n)) + ' ' + u[i]; }
  function pct(n) { return n == null ? '—' : Math.round(n) + ' %'; }
  function deg(n) { return n == null ? '—' : Math.round(n) + ' °C'; }
  function dur(s) { s = Math.floor(s || 0); var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? d + ' j ' + h + ' h' : h ? h + ' h ' + m + ' min' : m + ' min'; }

  var CSS = [
    '.odp{--odp-bg:#0b0d0c;--odp-line:rgba(255,255,255,.09);--odp-text:#e9ece6;--odp-muted:#8d948a;--odp-acc:#D4FF3D;--odp-warn:#ffb547;--odp-bad:#ff5c5c;',
    'font-family:"IBM Plex Sans",system-ui,sans-serif;color:var(--odp-text);border:1px solid var(--odp-line);background:var(--odp-bg);padding:18px;min-width:0}',
    '.odp *{box-sizing:border-box}',
    '.odp-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--odp-line);padding-bottom:12px;margin-bottom:14px}',
    '.odp-title{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-weight:600;letter-spacing:.08em;text-transform:uppercase;font-size:12px;color:var(--odp-muted)}',
    '.odp-host{font-weight:600;font-size:16px}',
    '.odp-state{margin-left:auto;font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--odp-muted)}',
    '.odp-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--odp-acc);margin-right:6px;vertical-align:1px}',
    '.odp-dot.off{background:var(--odp-bad)}',
    '.odp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));border-top:1px solid var(--odp-line);border-left:1px solid var(--odp-line)}',
    '.odp-cell{border-right:1px solid var(--odp-line);border-bottom:1px solid var(--odp-line);padding:12px}',
    '.odp-k{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--odp-muted)}',
    '.odp-v{font-family:"IBM Plex Mono",monospace;font-size:22px;font-weight:500;margin-top:4px;font-variant-numeric:tabular-nums}',
    '.odp-s{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--odp-muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.odp-bar{height:3px;background:var(--odp-line);margin-top:8px}.odp-bar>i{display:block;height:100%;background:var(--odp-acc)}',
    '.odp-warn .odp-v{color:var(--odp-warn)}.odp-warn .odp-bar>i{background:var(--odp-warn)}',
    '.odp-bad .odp-v{color:var(--odp-bad)}.odp-bad .odp-bar>i{background:var(--odp-bad)}',
    '.odp-list{margin:14px 0 0;padding:0;list-style:none;font-size:12.5px}',
    '.odp-list li{padding:6px 0;border-top:1px solid var(--odp-line);display:flex;gap:8px}',
    '.odp-list li:before{content:"▲";color:var(--odp-warn);font-size:9px;margin-top:4px}',
    '.odp-list li.ok:before{content:"●";color:var(--odp-acc)}',
    '.odp-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}',
    '.odp-btn{font:500 12px "IBM Plex Mono",monospace;color:var(--odp-text);background:transparent;border:1px solid var(--odp-line);padding:7px 12px;cursor:pointer}',
    '.odp-btn:hover{border-color:var(--odp-acc);color:var(--odp-acc)}.odp-btn:disabled{opacity:.4;cursor:wait}',
    '.odp-btn.main{background:var(--odp-acc);border-color:var(--odp-acc);color:#0b0d0c}',
    '.odp-msg{font:12px "IBM Plex Mono",monospace;color:var(--odp-muted);margin-top:10px;min-height:16px}',
    '.odp-set{display:none;margin-top:12px;gap:8px;flex-wrap:wrap}.odp-set.open{display:flex}',
    '.odp-set input{flex:1;min-width:160px;font:12px "IBM Plex Mono",monospace;background:transparent;color:var(--odp-text);border:1px solid var(--odp-line);padding:7px 10px}',
    '.odp-off{font-size:13px;color:var(--odp-muted);line-height:1.6}.odp-off code{font-family:"IBM Plex Mono",monospace;color:var(--odp-acc)}'
  ].join('');

  function level(v, warn, bad) { return v == null ? '' : v >= bad ? 'odp-bad' : v >= warn ? 'odp-warn' : ''; }
  function cell(k, v, sub, barPct, cls) {
    return '<div class="odp-cell ' + (cls || '') + '"><div class="odp-k">' + k + '</div><div class="odp-v">' + v + '</div>' +
      (sub ? '<div class="odp-s" title="' + esc(sub) + '">' + esc(sub) + '</div>' : '') +
      (barPct != null ? '<div class="odp-bar"><i style="width:' + Math.max(0, Math.min(100, barPct)) + '%"></i></div>' : '') + '</div>';
  }

  function api(path, method) {
    var headers = {};
    var t = cfg().token;
    if (t) headers['X-Token'] = t;
    if (method === 'POST') headers['Content-Type'] = 'application/json';
    return fetch(base() + path, { method: method || 'GET', headers: headers, body: method === 'POST' ? '{}' : undefined })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); });
  }
  function waitJob(id) {
    return api('/api/jobs/' + id).then(function (j) {
      if (j.status === 'done') return j.result;
      if (j.status === 'error') throw new Error(j.error);
      return new Promise(function (r) { setTimeout(r, 800); }).then(function () { return waitJob(id); });
    });
  }

  function mount(el) {
    if (!el || el.__odp) return;
    el.__odp = true;
    if (!document.getElementById(STYLE_ID)) {
      var st = document.createElement('style'); st.id = STYLE_ID; st.textContent = CSS; document.head.appendChild(st);
    }
    el.classList.add('odp');
    el.innerHTML = '<div class="odp-head"><span class="odp-title">PC · Overdrive</span><span class="odp-host">—</span>' +
      '<span class="odp-state"><span class="odp-dot off"></span>connexion…</span></div><div class="odp-body"></div>' +
      '<div class="odp-actions">' +
      '<button class="odp-btn main" data-a="quick">Optimisation express</button>' +
      '<button class="odp-btn" data-a="boost">Game Boost</button>' +
      '<button class="odp-btn" data-a="ram">Libérer la RAM</button>' +
      '<button class="odp-btn" data-a="open">Ouvrir Overdrive</button>' +
      '<button class="odp-btn" data-a="settings">Réglages</button></div>' +
      '<div class="odp-set"><input data-f="url" placeholder="http://127.0.0.1:8765"><input data-f="token" type="password" placeholder="Jeton Overdrive (pour les actions)">' +
      '<button class="odp-btn main" data-a="save">Enregistrer</button></div><div class="odp-msg"></div>';
    var body = el.querySelector('.odp-body'), msg = el.querySelector('.odp-msg');
    var setBox = el.querySelector('.odp-set');

    function render(d) {
      el.querySelector('.odp-host').textContent = d.host || '';
      el.querySelector('.odp-state').innerHTML = '<span class="odp-dot"></span>en ligne · ' + new Date(d.timestamp * 1000).toLocaleTimeString('fr-FR');
      var disk = (d.disks || []).slice().sort(function (a, b) { return b.percent - a.percent; })[0];
      var h = d.health || { score: null, issues: [] };
      var issues = (h.issues || []).concat((d.alerts || []).map(function (a) { return a.label + ' : ' + a.value + a.unit; }));
      body.innerHTML = '<div class="odp-grid">' +
        cell('Santé', h.score == null ? '—' : h.score + '<small style="font-size:12px;color:var(--odp-muted)">/100</small>', 'allumé depuis ' + dur(d.uptime), h.score, h.score != null && h.score < 60 ? 'odp-bad' : h.score < 80 ? 'odp-warn' : '') +
        cell('CPU', pct(d.cpu_percent), (d.cpu_freq_mhz ? (d.cpu_freq_mhz / 1000).toFixed(2) + ' GHz · ' : '') + deg(d.cpu_temp), d.cpu_percent, level(d.cpu_temp, 85, 95) || level(d.cpu_percent, 85, 97)) +
        cell('RAM', pct(d.ram_percent), bytes(d.ram_used) + ' / ' + bytes(d.ram_total), d.ram_percent, level(d.ram_percent, 80, 92)) +
        cell('GPU', pct(d.gpu_percent), (d.gpu_name || 'GPU') + ' · ' + deg(d.gpu_temp), d.gpu_percent, level(d.gpu_temp, 80, 88)) +
        cell('Réseau', bytes(d.net_down_bps) + '/s', '↑ ' + bytes(d.net_up_bps) + '/s', null) +
        (disk ? cell('Disque ' + esc(disk.mount), pct(disk.percent), bytes(disk.free) + ' libres', disk.percent, level(disk.percent, 85, 93)) : '') +
        (d.game ? cell('Jeu en cours', esc(d.game.name), 'profil ' + d.game.profile + ' · ' + d.game.minutes + ' min', null) : '') +
        (d.battery ? cell('Alimentation', Math.round(d.battery.percent) + ' %', d.battery.plugged ? 'secteur' : 'sur batterie', d.battery.percent, d.battery.plugged ? '' : 'odp-warn') : '') +
        '</div><ul class="odp-list">' + (issues.length ? issues.map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('')
        : '<li class="ok">Aucun problème détecté</li>') + '</ul>';
    }
    function offline(err) {
      el.querySelector('.odp-state').innerHTML = '<span class="odp-dot off"></span>hors ligne';
      body.innerHTML = '<div class="odp-off">Overdrive ne répond pas sur <code>' + esc(base()) + '</code>.<br>' +
        'Lancez Overdrive avec son icône sur le PC (ou vérifiez l\'adresse dans Réglages).' +
        (err ? '<br><span style="font-size:11px">' + esc(err) + '</span>' : '') + '</div>';
    }
    function refresh() { api('/api/nexus').then(render, function (e) { offline(e.message); }); }

    el.addEventListener('click', function (e) {
      var b = e.target.closest('[data-a]'); if (!b) return;
      var a = b.getAttribute('data-a');
      if (a === 'open') { window.open(base() + '/', '_blank', 'noopener'); return; }
      if (a === 'settings') {
        setBox.classList.toggle('open');
        setBox.querySelector('[data-f=url]').value = cfg().url || '';
        setBox.querySelector('[data-f=token]').value = cfg().token || '';
        return;
      }
      if (a === 'save') {
        saveCfg({ url: setBox.querySelector('[data-f=url]').value.trim(), token: setBox.querySelector('[data-f=token]').value.trim() });
        setBox.classList.remove('open'); msg.textContent = 'Réglages enregistrés.'; refresh(); return;
      }
      if (!cfg().token) { setBox.classList.add('open'); msg.textContent = 'Collez le jeton Overdrive (Paramètres → Accès à distance) pour lancer des actions.'; return; }
      var run = {
        quick: function () { return api('/api/quick-optimize', 'POST').then(function (r) { return waitJob(r.job); }).then(function (r) { return 'Optimisation terminée : ' + bytes(r.clean.freed) + ' nettoyés' + (r.ram && r.ram.freed != null ? ', ' + bytes(r.ram.freed) + ' de RAM libérés' : '') + '.'; }); },
        boost: function () { return api('/api/gaming/boost', 'POST').then(function (r) { return 'Game Boost : ' + (r.killed.length ? r.killed.join(', ') + ' fermé(s)' : 'aucune appli à fermer') + '.'; }); },
        ram: function () { return api('/api/ram/optimize', 'POST').then(function (r) { return bytes(r.freed) + ' de RAM libérés.'; }); }
      }[a];
      if (!run) return;
      b.disabled = true; msg.textContent = 'En cours…';
      run().then(function (t) { msg.textContent = t; refresh(); }, function (err) { msg.textContent = 'Erreur : ' + err.message; })
        .then(function () { b.disabled = false; });
    });

    refresh();
    el.__odpTimer = setInterval(refresh, 5000);
  }

  window.OverdrivePanel = { mount: mount };
  function auto() { Array.prototype.forEach.call(document.querySelectorAll('[data-overdrive]'), mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', auto); else auto();
})();
