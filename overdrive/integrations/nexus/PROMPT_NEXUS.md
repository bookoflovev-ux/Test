# Brancher Overdrive sur le cockpit « Nexus » (dashboard-societe.html)

À copier-coller tel quel dans la session Claude Code du cockpit (« Dashboard gestion SASU »),
**sur le PC**, une fois Overdrive décompressé et lancé (`start.bat`).

---

Ajoute au cockpit `C:\Users\deleu\OneDrive\Documents\CLAUDE\dashboard-societe.html` un panneau
de supervision de mon PC alimenté par **Overdrive**, mon logiciel d'optimisation qui tourne en local
sur `http://127.0.0.1:8765`.

Fichier à intégrer : `overdrive-panel.js`, dans le dossier d'Overdrive sous
`integrations\nexus\overdrive-panel.js` (demande-moi où j'ai décompressé Overdrive si tu ne le trouves pas).
C'est un script autonome sans dépendance qui :
- lit `GET http://127.0.0.1:8765/api/nexus` toutes les 5 s (CORS déjà autorisé pour `localhost:*`) ;
- affiche santé, CPU, RAM, GPU, températures, réseau, disque le plus plein, alimentation, **jeu en cours**
  et alertes, dans le style du cockpit (IBM Plex, accent #D4FF3D, hairlines, zéro ombre) ;
- propose les actions Optimisation express / Game Boost / Libérer la RAM, qui demandent le jeton
  Overdrive saisi via son bouton « Réglages » (stocké sous la clé localStorage `overdrive-link-v1`).

Ce que je veux :
1. **Copie le contenu du script dans le HTML** (balise `<script>` en fin de fichier), car le cockpit est un fichier autonome.
2. Crée une **route `#pc`** (entrée de menu « PC · Overdrive ») contenant `<section data-overdrive></section>`.
   Si la route est rendue dynamiquement, appelle `OverdrivePanel.mount(element)` après le rendu.
3. Ajoute sur la page d'accueil une **petite tuile de résumé** (santé + jeu en cours) qui renvoie vers `#pc`,
   en réutilisant `fetch('http://127.0.0.1:8765/api/nexus')`, et qui affiche « PC hors ligne » si Overdrive ne répond pas.
4. Règles :
   - ne jamais écrire dans `cockpit-societe-v1`, `snowball-finance-v3` ni `command-center-v2` ;
   - rien d'Overdrive en mode démo `?demo` ;
   - garder le design existant ;
   - sauvegarder une copie du HTML avant modification.
5. Vérifie dans la preview `cockpit` (port 4190) que le panneau affiche « en ligne » quand Overdrive tourne,
   et un état « hors ligne » propre quand il est arrêté.

Format des données renvoyées par `/api/nexus` (pour la tuile) :
`host, timestamp, cpu_percent, cpu_temp, cpu_freq_mhz, ram_percent, ram_used, ram_total, gpu_percent,
gpu_temp, gpu_name, net_down_bps, net_up_bps, disks[{mount, percent, free}], battery{percent, plugged},
uptime, health{score, issues[]}, alerts[{label, value, unit, limit}], game{name, profile, minutes} | null`.
