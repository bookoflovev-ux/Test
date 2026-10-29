# ⚡ Overdrive — centre de pilotage PC

Version actuelle : voir `app/__init__.py` · historique des nouveautés : [`CHANGELOG.md`](CHANGELOG.md).

Logiciel **gratuit et open source** pour optimiser, entretenir et superviser un PC Windows,
conçu pour le jeu (**Counter-Strike 2**) et les portables **ASUS TUF**. Inspiré d'IObit Advanced
SystemCare / Driver Booster, MyASUS et Armoury Crate, mais **transparent** : chaque réglage est expliqué
et **réversible en un clic**.

L'interface est une application web moderne (thème sombre/clair) : on l'utilise sur le PC, **depuis
son téléphone** (installable sur l'écran d'accueil) ou depuis n'importe où via Tailscale (gratuit).

## ▶️ Installation (Windows 10 / 11)

1. Décompressez le dossier **Overdrive** où vous voulez le garder (ex. `Documents\Overdrive`).
2. Double-cliquez sur **`Installer Overdrive.bat`** et acceptez la demande administrateur.
   Une fenêtre d'installation s'affiche **une seule fois** (1 à 3 minutes). Elle installe Python si besoin,
   les composants, le démarrage caché et l'**icône Overdrive sur le bureau** et dans le menu Démarrer.
3. Ensuite, utilisez simplement l'**icône du bureau** : plus aucune fenêtre noire. Overdrive tourne en arrière-plan,
   avec une icône près de l'horloge (clic droit : Ouvrir, Optimisation express, Game Boost, Quitter).
4. Au premier lancement, Overdrive **analyse le PC** et propose les optimisations adaptées à son matériel
   (page **Analyse du PC**, bouton « Optimiser ce PC automatiquement »).

- Démarrage avec Windows : activé par défaut, désactivable dans *Paramètres → Application*.
- Pour désinstaller : **`Desinstaller Overdrive.bat`** (propose de remettre les réglages Windows d'origine).
- Dépannage : `outils\console-depannage.bat` lance Overdrive avec une console visible, et le journal se trouve
  dans `data\overdrive.log`. `outils\build_exe.bat` génère un `.exe` autonome.
- Températures CPU : [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)
  avec *Options → Remote Web Server → Run* (port 8085). ⚠ **Fermez-le avant de jouer sur FACEIT** : le pilote WinRing0
  de ses anciennes versions est bloqué par l'anti-triche (Overdrive vous prévient s'il est ouvert).

## 🧭 Modules

| Module | Ce qu'il fait |
|---|---|
| **Analyse du PC** | Inventaire matériel (CPU, RAM et nombre de barrettes, GPU dédié/intégré, SSD/HDD, écran, portable/fixe, HVCI) → niveau du PC et diagnostic : écran bridé à 60 Hz, RAM simple canal, XMP désactivé, disque plein, pilote GPU ancien, Windows 10 avec CPU hybride… **Optimisation automatique adaptée** (ex. : services gardés sur disque dur, allègement sur PC modeste, fréquence d'écran maximale) avec point de restauration |
| **Contrôle ASUS** | Pour les TUF / ROG / Vivobook, via le pilote ASUS (comme Armoury Crate / G-Helper) : modes Silencieux / Équilibré / **Turbo**, **Turbo automatique pendant les jeux** (sur secteur), **limite de charge de la batterie** (60/80/100 %), overdrive de l'écran, état MUX et GPU Éco, âge du BIOS. Autres marques : outil constructeur conseillé (Lenovo Vantage, MSI Center, OMEN Hub…) |
| **Tableau de bord** | CPU (par cœur), RAM, GPU (NVIDIA via NVML, AMD/Intel via compteurs Windows), réseau, disques, températures, batterie, score de santé, actions rapides |
| **Mode Jeu · CS2** | Détection Steam/CS2, **profil compétitif en 1 clic** (plan Performances ultimes, Mode Jeu, Game DVR off, priorité MMCSS, HAGS, souris 1:1, CS2 en priorité haute permanente + forcé sur le GPU NVIDIA), **Game Boost** (ferme les applis inutiles, purge la RAM), éditeur `autoexec.cfg`, options de lancement, réglages vidéo, conseils TUF (Turbo, MUX/Ultimate, dual channel) |
| **Mes jeux** | Détection des jeux installés (Steam, Epic, GOG, Ubisoft, Riot, Battle.net, EA, Xbox + catalogue de ~35 jeux populaires), suivi du temps de jeu, **profil par jeu** (Compétitif / Équilibré / Qualité / Désactivé) appliqué automatiquement au lancement — priorité, GPU dédié, purge RAM, applis fermées, plan d'alimentation — puis restauré à la fermeture. Conseils de réglages par jeu. |
| **Optimisations** | 14 réglages Windows documentés (impact, risque, redémarrage), plan d'alimentation, « appliquer les recommandés », « tout restaurer » |
| **Nettoyage** | Temporaires, cache Windows Update, dumps, miniatures, journaux, caches Chrome/Edge/Brave/Firefox/Discord/Steam, shaders GPU, corbeille — **analyse avant suppression** |
| **Fichiers inutiles** | Gros fichiers, doublons (empreinte BLAKE2), vieux téléchargements, dossiers vides → **envoi à la corbeille** (récupérable) |
| **Processus & démarrage** | Gestionnaire de processus (priorité, fermeture, applis inutiles détectées), programmes au démarrage (même mécanisme que le Gestionnaire des tâches), services non essentiels (liste prudente, réversible), purge RAM (liste standby, comme ISLC/RAMMap) |
| **Réseau** | Test de latence/gigue/perte, DNS (Cloudflare, Google, Quad9, AdGuard), vidage DNS, optimisation TCP, réinitialisation de la pile |
| **Mises à jour** | Windows Update **y compris les mises à jour facultatives** et Microsoft Update (Office), détection d'une mise à jour **en attente de redémarrage**, installation au choix. **Pilotes multi-sources** : Windows Update, **serveur officiel ASUS** du modèle exact (pilotes comparés aux identifiants matériels + **BIOS**), **serveur NVIDIA** (dernier Game Ready) ; téléchargement depuis les sites officiels uniquement. Applications via **winget**. Conseils AMD / Intel (outils officiels). |
| **Sauvegarde & maintenance** | Points de restauration, sauvegarde incrémentale de dossiers (disque externe/NAS), sauvegarde config CS2, SFC, DISM, TRIM SSD, CHKDSK |
| **Supervision · Nexus** | Envoi périodique JSON (push) vers votre serveur Nexus, alertes (déclenchées/résolues), endpoint **Prometheus** `/metrics`, JSON `/api/nexus`, `/api/health`, journal d'activité |
| **Paramètres & accès distant** | Lien d'accès avec jeton, verrouiller/veille/redémarrer/éteindre à distance, automatisations (nettoyage, RAM, sauvegarde) |

## 📱 Piloter le PC à distance

- **Même Wi-Fi** : *Paramètres → Accès depuis le téléphone* affiche un lien `http://192.168.x.x:8765/?token=…`.
  Ouvrez-le sur le téléphone puis « Ajouter à l'écran d'accueil ».
- **Depuis n'importe où** : installez [Tailscale](https://tailscale.com/download) (gratuit) sur le PC et le
  téléphone et utilisez l'adresse `100.x.x.x` du PC. Pas de port à ouvrir, trafic chiffré.
- Toute requête qui ne vient pas du PC lui-même exige le jeton (`?token=`, en-tête `X-Token` ou `Authorization: Bearer`).
  Le jeton se régénère depuis les paramètres. Autorisez le port 8765 dans le pare-feu Windows si Windows le demande (réseau privé uniquement).

## 📡 Supervision « Nexus »

**Push** — *Supervision → Connexion à Nexus* : renseignez l'URL d'ingestion (et une clé API facultative,
envoyée en `Authorization: Bearer` et `X-API-Key`). Overdrive envoie toutes les N secondes :

```json
{ "type": "metrics", "host": "PC-TUF", "timestamp": 1792000000,
  "cpu_percent": 12.5, "cpu_temp": 61, "ram_percent": 48.2, "gpu_percent": 97, "gpu_temp": 74,
  "net_down_bps": 120000, "disks": [{"mount": "C:\\", "percent": 71.3}],
  "health": {"score": 90, "issues": []}, "alerts": [], "top_processes": [...] }
```

et pour les alertes : `{"type": "alert", "state": "firing" | "resolved", "label": "...", "value": 92, "limit": 90}`.

**Pull** — `GET /api/nexus` (même JSON), `GET /metrics` (format Prometheus, compatible Grafana / Zabbix /
Netdata / Uptime Kuma), `GET /api/health`. Documentation complète de l'API : `http://127.0.0.1:8765/api/docs`.

## 🧩 Panneau pour le cockpit « Nexus »

`integrations/nexus/overdrive-panel.js` est un panneau autonome à intégrer au cockpit
`dashboard-societe.html`. Il lit Overdrive en local, dans le style du cockpit, et inclut le jeu en cours et
des actions rapides. Le texte à donner à la session Claude Code du cockpit est prêt dans
`integrations/nexus/PROMPT_NEXUS.md`.

## 🛡️ Sécurité et honnêteté

- Chaque modification registre / service / plan d'alimentation sauvegarde sa valeur d'origine dans
  `data/tweaks_backup.json` → bouton **Tout restaurer**. Les points de restauration Windows restent le filet ultime.
- Pas de « nettoyeur de registre » ni de tweak placebo : les réglages proposés ont un effet documenté.
- Les gains réels de FPS sur un portable TUF viennent d'abord de : **secteur branché**, **mode Turbo**,
  **MUX en mode Ultimate**, **RAM en dual channel**, **températures maîtrisées** et pilote GPU à jour.
  Les optimisations Windows apportent surtout de la **stabilité** (moins de saccades, meilleurs 1 % low) et moins de latence.
- Les processus système critiques ne peuvent pas être fermés ; les fichiers sont envoyés à la corbeille.
- Toute **action** exige le jeton, même en local : un site web ouvert dans le navigateur ne peut pas piloter le PC.
  Seules les pages `localhost` (cockpit) peuvent lire les données, et les noms d'hôte inconnus sont refusés (protection DNS rebinding).
- Profils de jeu : uniquement des réglages Windows (priorité, mémoire, alimentation), sans toucher aux fichiers ni à la mémoire
  des jeux, donc compatibles avec les anti-triche.

## 🧱 Architecture

```
overdrive/
├── Installer Overdrive.bat / Desinstaller Overdrive.bat
├── run.py              serveur (FastAPI + uvicorn), port 8765 : --open / --background
├── outils/             installeur PowerShell, console de dépannage, build .exe
├── assets/             icônes (.ico / .png)
├── app/
│   ├── advisor.py      analyse du PC + optimisation adaptée
│   ├── asus.py         contrôle ASUS (ATKACPI)   ├── desktop.py  bureau, zone de notification
│   ├── monitor.py      échantillonnage temps réel + historique
│   ├── gpu.py          NVML / nvidia-smi / compteurs Windows
│   ├── tweaks.py       moteur d'optimisations réversibles
│   ├── gaming.py       CS2, profil compétitif, Game Boost
│   ├── memory.py       purge RAM (NtSetSystemInformation, EmptyWorkingSet)
│   ├── cleaner.py      nettoyage   ├── files.py      fichiers inutiles
│   ├── processes.py    processus   ├── startup.py    démarrage   ├── services.py
│   ├── network.py      réseau      ├── updates.py    Windows Update / winget / ASUS
│   ├── backup.py       sauvegardes ├── maintenance.py SFC, DISM, alimentation
│   ├── supervision.py  Nexus, Prometheus, alertes ├── scheduler.py automatisations
│   └── main.py         API REST + authentification
└── static/             interface (HTML/CSS/JS sans framework, PWA)
```

Sur Linux/macOS l'application démarre en **mode démonstration** (monitoring, nettoyage du cache,
recherche de fichiers) : pratique pour développer l'interface.
