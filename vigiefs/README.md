# VigieFS — supervision et administration de serveurs de fichiers

Console web de **supervision, d'administration et de prévention** pour les dossiers et
partages de fichiers de vos clients, dans l'esprit du *Gestionnaire de ressources du
serveur de fichiers* (FSRM) de Windows Server, mais pensée **multi-clients** pour une
société d'infogérance. Elle complète Datto (sauvegarde / RMM) sans le remplacer.

![Tableau de bord](docs/tableau-de-bord.png)

## Fonctionnalités

### Supervision
| Domaine | Ce que fait VigieFS |
|---|---|
| **Multi-clients** | Clients → dossiers supervisés (chemins locaux `D:\…` ou UNC `\\SRV\partage`). Vue consolidée, recherche globale (Ctrl K). |
| **Quotas** | Quota logiciel par dossier, seuils d'avertissement / critique, **prévision de saturation** (tendance sur 30 jours). |
| **Anti-rançongiciel** | Détection temps réel des extensions chiffrées, des notes de rançon et des **rafales de modifications** → alerte critique immédiate. |
| **Filtrage de fichiers** | Extensions interdites, alerte ou **quarantaine** automatique. |
| **Journal d'activité** | Créations, modifications, suppressions, renommages en temps réel, export CSV. |
| **Alertes** | Dédoublonnées, escalade, acquittement, résolution automatique ; e-mail (SMTP) et webhook signé HMAC. |

### Optimisation du stockage
| Domaine | Ce que fait VigieFS |
|---|---|
| **Centre d'optimisation** | Gain potentiel par client et par dossier : fichiers inutiles + doublons + données froides. |
| **Fichiers inutiles** | Détectés à chaque analyse : verrous Office orphelins (`~$…`), `.tmp`, téléchargements interrompus, `Thumbs.db`, `.DS_Store`, sauvegardes automatiques (`.bak`, `.wbk`, `.xlk`, `.asd`…), anciens journaux, fichiers vides, **dossiers vides**. Ancienneté minimale réglable pour ne jamais toucher un document ouvert. |
| **Doublons** | Comparaison du **contenu** (taille → empreinte partielle → SHA-256), dans un dossier ou **sur tous les dossiers d'un client**. Choix de l'exemplaire conservé (le plus ancien, le plus récent, chemin le plus court ou manuel) ; chaque copie est **revérifiée** avant suppression. |
| **Archivage à froid** | Règle par dossier (ancienneté, taille, extensions, exclusions, date de modification ou d'accès), **simulation** du gain, copie **vérifiée SHA-256** vers un NAS / disque d'archive en conservant les dates, **raccourci `[archivé]`** laissé à la place, restauration en un clic, planification automatique. |
| **Corbeille VigieFS** | Tout ce que VigieFS retire (nettoyage, doublons, suppression dans l'explorateur, remplacement par une version réparée) est **restaurable** pendant la durée de rétention, puis purgé automatiquement. |
| **Tâches** | Traitements longs en arrière-plan avec progression, annulation et historique. |

### Réparation de documents
| Domaine | Ce que fait VigieFS |
|---|---|
| **Diagnostic** | Word, Excel, PowerPoint (`.docx/.xlsx/.pptx` et variantes macro/modèle, anciens `.doc/.xls/.ppt`) et PDF : archive, XML, cohérence du paquet, liens internes, pages PDF. Détecte aussi les fichiers **remplis de zéros**, **chiffrés par un rançongiciel** ou **protégés par mot de passe** (non corrompus). |
| **Réparation Office** | Reconstruction de l'archive même si l'index est détruit ou le fichier tronqué, réparation XML (caractères invalides, balises non refermées, troncature), régénération des types de contenu et des relations, correction des liens cassés (images manquantes remplacées, références orphelines retirées), suppression des parties non essentielles endommagées (miniature, `calcChain.xml`…). |
| **Réparation PDF** | Données parasites retirées, fin de fichier restaurée, **table xref reconstruite**, **catalogue et arborescence des pages recréés** pour un PDF tronqué, pages illisibles remplacées par une page blanche (numérotation conservée). |
| **Anciens formats** | Conversion réparatrice via LibreOffice (si installé), sinon récupération du texte. |
| **Dernier recours** | Extraction du texte (Word/PowerPoint) ou des données (Excel) dans un nouveau document. |
| **Contrôle d'intégrité** | Analyse d'un dossier entier, liste des documents corrompus / irrécupérables, **réparation en lot** (copie « (réparé) » ou remplacement de l'original, mis en corbeille). |
| **Atelier** | Glisser-déposer d'un document reçu d'un client, téléchargement du fichier réparé, historique. |

Principes : l'original n'est jamais écrasé sans être conservé, chaque fichier réparé est **re-diagnostiqué** avant d'être proposé, et un fichier irrécupérable est signalé comme tel avec la marche à suivre (restauration Datto).

### Administration et intégration
Explorateur avec ACL NTFS, rôles (lecture seule / opérateur / administrateur), journal d'audit, API REST documentée (`/api/docs`), clés d'API, `/api/v1/status`, `/metrics` Prometheus, composant **Datto RMM**, rapports CSV par client.

| Centre d'optimisation | Nettoyage | Doublons |
|---|---|---|
| ![](docs/optimisation.png) | ![](docs/nettoyage.png) | ![](docs/doublons.png) |

| Archivage à froid (simulation) | Contrôle d'intégrité | Diagnostic et réparation |
|---|---|---|
| ![](docs/archivage.png) | ![](docs/integrite.png) | ![](docs/diagnostic.png) |

| Atelier de réparation | Thème sombre |
|---|---|
| ![](docs/reparation.png) | ![](docs/theme-sombre.png) |

## Installation sur Windows Server

Prérequis : **Python 3.10+** (cocher « Add python.exe to PATH » à l'installation).

**Essai rapide** : double-cliquez sur `start-vigiefs.bat`, puis ouvrez <http://localhost:8470/>.

**Installation permanente** (PowerShell administrateur, depuis le dossier `vigiefs`) :

```powershell
.\scripts\install-windows.ps1 -DataDir 'D:\VigieFS\data' -Port 8470 -ListenAll `
    -ServiceAccount 'DOMAINE\svc-vigiefs' -ServicePassword '…'
```

- Avec [NSSM](https://nssm.cc) dans le PATH, VigieFS devient un **service Windows** ;
  sinon une tâche planifiée « au démarrage » est créée.
- Le compte de service doit pouvoir **lire** les dossiers supervisés (et **modifier**
  pour la quarantaine et l'explorateur). Pour des partages distants, préférez un compte
  de service du domaine à SYSTEM.
- `-ListenAll` ouvre l'interface au réseau (et le port du pare-feu). Sinon elle n'écoute
  que sur `127.0.0.1`.

Au premier accès, l'écran **« Première installation »** crée le compte administrateur.

### Linux / autre

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m vigiefs --host 0.0.0.0 --port 8470 --data /var/lib/vigiefs
```

### Options

```
python -m vigiefs [--host 127.0.0.1] [--port 8470] [--data DOSSIER]
                  [--ssl-certfile cert.pem --ssl-keyfile key.pem]
                  [--reset-password UTILISATEUR]
```

Variables d'environnement équivalentes : `VIGIEFS_HOST`, `VIGIEFS_PORT`, `VIGIEFS_DATA`.
`VIGIEFS_FRAME_ANCESTORS` (ex. `https://supervision.exemple.fr`) autorise l'intégration
de l'interface en iframe dans votre centre de supervision.

Pour un accès hors du réseau d'administration, servez VigieFS en **HTTPS**
(`--ssl-certfile/--ssl-keyfile`) ou derrière un reverse proxy (IIS ARR, nginx…).

## Prise en main

1. **Clients** → *Nouveau client*.
2. **Dossiers supervisés** → *Ajouter un dossier* : client, chemin, quota, fréquence
   d'analyse, surveillance temps réel, filtrage. Une première analyse démarre aussitôt.
3. **Paramètres › Notifications** : SMTP (ex. `smtp.office365.com:587`) et/ou webhook
   (Teams, Slack, PSA, centre de supervision). Bouton *Envoyer un test*.
4. **Paramètres › Sécurité & filtrage** : seuils anti-rançongiciel, extensions bloquées,
   dossier de quarantaine (**en dehors** des dossiers supervisés).
5. **Paramètres › Stockage & réparation** : dossier de la corbeille et rétention, destination
   des **archives à froid** (NAS, disque d'archive…), anciennetés minimales des fichiers inutiles,
   chemin de LibreOffice (optionnel, pour les anciens formats `.doc/.xls/.ppt`).
6. **Paramètres › Utilisateurs** : comptes des techniciens (rôle *Opérateur*) et des
   personnes en consultation (*Lecture seule*).

### Rôles

| Rôle | Droits |
|---|---|
| Lecture seule | Consultation de tout (sauf paramètres), exports CSV. |
| Opérateur | + clients, dossiers, analyses, acquittement d'alertes, nettoyage, doublons, archivage et restauration, réparation, opérations sur les fichiers (suppressions vers la corbeille). |
| Administrateur | + purge définitive de la corbeille, suppression de clients, paramètres, utilisateurs, clés d'API, journal d'audit. |

## Intégration au centre de supervision (étape 2)

L'interface est entièrement construite sur l'API REST : le futur panneau de contrôle
du centre de supervision peut consommer exactement les mêmes points d'accès.

- **Documentation interactive** : `http://serveur:8470/api/docs` (OpenAPI).
- **Authentification machine** : créez une clé dans *Paramètres › Clés d'API* et
  envoyez-la dans `X-API-Key: vfs_…` ou `Authorization: Bearer vfs_…`.
- **Statut consolidé** (stable, versionné) :

  ```bash
  curl -H "X-API-Key: vfs_…" http://serveur:8470/api/v1/status
  ```
  ```json
  {"status": "critical", "alerts": {"critical": 1, "warning": 2, "info": 0},
   "shares": [{"client": "ACME", "name": "Compta", "status": "critical", "size": 123456,
               "quota_pct": 97.2, "monitoring": "active",
               "alerts": [{"severity": "critical", "category": "ransomware", "title": "…"}]}]}
  ```
- **Prometheus / Grafana** : `GET /metrics` (taille, fichiers, quota, disponibilité par
  dossier, alertes ouvertes par gravité).
- **Webhook sortant** : `POST` JSON à chaque nouvelle alerte ou escalade, signé par
  `X-VigieFS-Signature: sha256=<HMAC-SHA256(corps, secret)>`.

### Datto RMM

`integrations/datto-rmm/VigieFS-Monitor.ps1` est un composant de type **Monitor** :

1. Datto RMM › *Components* › *New Component* › catégorie **Monitors**, script PowerShell,
   collez le contenu du fichier.
2. Déclarez les variables `VigieFSUrl`, `VigieFSApiKey` (clé *Lecture seule*) et
   `AlertLevel` (`critical` ou `warning`).
3. Ajoutez le monitor à la politique du serveur hébergeant VigieFS : une alerte Datto
   est levée dès que VigieFS signale un problème, avec le détail en diagnostic.

## Architecture

```
vigiefs/
├── vigiefs/
│   ├── api.py          API REST FastAPI + service de l'interface
│   ├── engine.py       Planificateur, analyses, règles proactives, rétention
│   ├── jobs.py         Tâches de fond persistées (progression, annulation)
│   ├── optimize.py     Fichiers inutiles, nettoyage, doublons
│   ├── storage.py      Corbeille VigieFS, archivage à froid
│   ├── docrepair.py    Diagnostic et réparation Word / Excel / PowerPoint / PDF
│   ├── integrity.py    Contrôle d'intégrité d'un dossier, atelier de réparation
│   ├── monitor.py      Surveillance temps réel (watchdog), anti-rançongiciel
│   ├── scanner.py      Analyse d'arborescence, doublons, quarantaine
│   ├── alerts.py       Alertes (dédoublonnage, escalade) + e-mail / webhook
│   ├── rules.py        Quotas, prévisions, filtrage, sécurité des chemins
│   ├── permissions.py  ACL NTFS (icacls) / droits POSIX
│   ├── auth.py         Comptes, sessions, clés d'API
│   ├── db.py           SQLite (WAL)
│   └── static/         Interface web (HTML/CSS/JS natif en modules, aucune dépendance externe)
├── integrations/datto-rmm/VigieFS-Monitor.ps1
├── scripts/install-windows.ps1
└── tests/
```

Toutes les données (base SQLite, journaux applicatifs) sont dans le dossier `--data`.
**Sauvegardez ce dossier** (ex. avec Datto). L'interface fonctionne hors ligne : aucune
ressource n'est chargée depuis Internet.

## Limites connues

- Les quotas sont **logiciels** : VigieFS alerte et prévoit, mais ne bloque pas l'écriture
  (pour un blocage strict, combinez avec un quota FSRM / NTFS).
- La surveillance temps réel d'un partage UNC distant dépend du serveur distant
  (notifications SMB) ; les analyses planifiées restent la source de vérité.
- Le propriétaire NTFS n'est pas affiché (seules les ACL via `icacls`).
- La réparation de documents traite la corruption de **structure** (archive, XML, PDF). Un
  document dont le contenu a été écrasé (zéros, chiffrement) est irrécupérable : restaurez-le
  depuis Datto. Les documents protégés par mot de passe ne sont pas déchiffrés.
- Sans LibreOffice, les anciens formats binaires `.doc/.xls/.ppt` endommagés ne peuvent être
  récupérés qu'en texte brut.
- La corbeille et les archives doivent se trouver **hors** des dossiers supervisés ; pour des
  déplacements instantanés, placez la corbeille sur le même volume que les partages.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```
