# Overdrive — règles de travail

- **À chaque livraison** (commit poussé avec des changements visibles) :
  1. incrémenter `__version__` dans `app/__init__.py` (nouvelle fonction → mineure, correction → correctif) ;
  2. ajouter en tête de `CHANGELOG.md` une section `## x.y.z — JJ/MM/AAAA` rédigée en français simple pour l'utilisateur.
- Compatibilité anti-triche (FACEIT, Vanguard…) : ne jamais ouvrir en écriture, fermer, vider ou changer la priorité « à chaud »
  d'un processus de jeu, d'anti-triche, de Discord ou de Steam (voir `app/anticheat.py`). Passer par le registre ou des réglages globaux.
- Tout réglage système doit être réversible (sauvegarde via `tweaks.apply_reg_ops` / `store_backup`).
- Interface et messages en français.
