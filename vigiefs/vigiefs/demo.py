"""Jeu de démonstration : clients, dossiers et fichiers fictifs pour essayer VigieFS sans risque.

    python -m vigiefs demo --data data-demo

Tout est créé dans le dossier de données indiqué (sous-dossier « demo-partages ») : doublons,
fichiers inutiles, documents anciens à archiver, documents abîmés à réparer, chemin trop long,
noms non conformes, fichier interdit et données personnelles fictives. Aucun autre fichier n'est
touché ; supprimer le dossier de données suffit à tout effacer.
"""

import io
import os
import random
import time

from . import docrepair, sensitive
from .db import Database

DEMO_FOLDER = "demo-partages"
WORDS = ("contrat facture client livraison maintenance serveur réseau projet budget planning réunion devis "
         "commande audit conseil dossier procédure échéance règlement relance assurance").split()
LONG_TARGET = 275    # longueur visible par les utilisateurs (chemin UNC) du fichier « trop long »
LOCAL_MAX = 245      # longueur locale maximale : le jeu reste manipulable dans l'Explorateur Windows


def _text(rng, n):
    return " ".join(rng.choices(WORDS, k=n)).capitalize() + "."


def _pdf_bytes(lines, image_kb=0):
    """PDF valide d'une page ; `image_kb` ajoute une image (bruit gris) pour obtenir un fichier volumineux."""
    import pikepdf
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    ops = b"BT /F1 12 Tf 60 780 Td 16 TL " + b" ".join(
        b"(" + line.encode("latin-1", "replace").replace(b"(", b"[").replace(b")", b"]") + b") '" for line in lines) + b" ET"
    resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    if image_kb:
        width, height = 400, max(1, image_kb * 1024 // 400)
        image = pdf.make_stream(os.urandom(width * height), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                Width=width, Height=height, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8)
        resources.XObject = pikepdf.Dictionary(Im1=image)
        ops += b" q 240 0 0 300 180 120 cm /Im1 Do Q"
    page = pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 595, 842], Resources=resources))
    page.obj.Contents = pdf.make_stream(ops)
    pdf.pages.append(page)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


class _Writer:
    def __init__(self, root, rng):
        self.root, self.rng, self.now, self.count, self.size = root, rng, time.time(), 0, 0

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def bytes(self, rel, data, age_days=0):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        self.age(rel, age_days)
        self.count += 1
        self.size += len(data)

    def age(self, rel, age_days):
        t = self.now - age_days * 86400
        os.utime(self.path(rel), (t, t))

    def docx(self, rel, age_days=0, paragraphs=None):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        docrepair.write_minimal_docx(p, paragraphs or [_text(self.rng, 18) for _ in range(self.rng.randint(15, 60))],
                                     title=os.path.splitext(os.path.basename(rel))[0])
        self.age(rel, age_days)
        self.count += 1
        self.size += os.path.getsize(p)

    def xlsx(self, rel, age_days=0, rows=None):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        rows = rows or [[self.rng.choice(WORDS), self.rng.randint(1, 9999), round(self.rng.random() * 1000, 2)]
                        for _ in range(self.rng.randint(30, 300))]
        docrepair.write_minimal_xlsx(p, {"Feuille1": rows})
        self.age(rel, age_days)
        self.count += 1
        self.size += os.path.getsize(p)

    def pdf(self, rel, age_days=0, lines=None):
        self.bytes(rel, _pdf_bytes(lines or [_text(self.rng, 9) for _ in range(30)]), age_days)

    def folder(self, rel, age_days=0):
        os.makedirs(self.path(rel), exist_ok=True)
        self.age(rel, age_days)


def _fake_identity(rng):
    """NIR, IBAN et carte bancaire fictifs mais à clé de contrôle valide (pour la détection RGPD)."""
    body = f"1{rng.randint(60, 99)}{rng.randint(1, 12):02d}75{rng.randint(1, 999):03d}{rng.randint(1, 999):03d}"
    nir = f"{body[0]} {body[1:3]} {body[3:5]} {body[5:7]} {body[7:10]} {body[10:13]} {97 - int(body) % 97:02d}"
    iban = "FR76 3000 6000 0112 3456 7890 189"
    digits = "4970" + "".join(str(rng.randint(0, 9)) for _ in range(11))
    for check in range(10):
        if sensitive.luhn(digits + str(check)):
            break
    card = " ".join((digits + str(check))[i:i + 4] for i in range(0, 16, 4))
    return nir, iban, card


def _cabinet(w, rng, root_len):
    """Cabinet d'avocats : droits, chemins longs, doublons, RGPD, documents abîmés."""
    for y, age in (("2022", 900), ("2023", 560)):
        w.xlsx(f"Comptabilité/{y}/Bilan {y}.xlsx", age)
        w.xlsx(f"Comptabilité/{y}/Grand livre {y}.xlsx", age + 10)
    for i in range(1, 9):
        w.pdf(f"Comptabilité/2024/Factures fournisseurs/Facture F-2024-{i:04d}.pdf", rng.randint(30, 300))
    w.xlsx("Comptabilité/2024/Budget 2024.xlsx", 45)
    w.bytes("Comptabilité/2024/~$Budget 2024.xlsx", b"\x00" * 165, 40)            # verrou Office orphelin
    w.bytes("Comptabilité/2024/Budget 2024.xlsx.bak", os.urandom(90000), 200)      # sauvegarde automatique
    w.bytes("Comptabilité/Thumbs.db", os.urandom(24000), 300)
    w.docx("Direction/Stratégie 2025.docx", 20)
    w.docx("Direction/Conseil/Compte rendu du conseil de mars.docx", 210)
    w.bytes("Direction/export-comptable.log", b"INFO export termine\n" * 15000, 420)
    w.bytes("Direction/rapport.tmp", os.urandom(40000), 12)
    w.folder("Direction/Nouveau dossier", 120)
    for name in ("Contrat de bail", "Assignation", "Conclusions", "Bordereau de pièces"):
        w.docx(f"Dossiers clients/SCI Les Tilleuls/{name}.docx", rng.randint(10, 400))
    # Chemin trop long pour les utilisateurs (vu via le partage UNC), mais manipulable en local.
    deep = "Dossiers clients/SCI Les Tilleuls/Procédure prud'homale 2024/Pièces communiquées par la partie adverse"
    name = ("Annexe 12 - Attestation de témoin rédigée par le gestionnaire de l'immeuble concernant les faits "
            "du 12 mars et la remise des clés")
    budget = max(40, LOCAL_MAX - root_len - 1 - len(deep) - 1 - len(".pdf"))
    long_rel = f"{deep}/{name[:budget].rstrip(' -')}.pdf"
    w.pdf(long_rel, 60)
    nir, iban, card = _fake_identity(rng)
    w.bytes("RH/Paie/Salariés 2024.csv", (
        "Nom;Prénom;NIR;IBAN;Téléphone\n"
        f"DURAND;Julie;{nir};{iban};06 12 34 56 78\n").encode("utf-8"), 30)
    w.docx("RH/Paie/Arrêts maladie 2024.docx", 25, paragraphs=[
        "Suivi des arrêts maladie — confidentiel.",
        "Julie Durand : arrêt maladie du 3 au 14 juin, ordonnance transmise, pathologie non précisée.",
        "Rappel : le diagnostic médical ne doit jamais figurer dans ce document."])
    w.docx("RH/Recrutement/CV - candidat juriste.docx", 75)
    w.bytes("Partage temporaire/Accès Wi-Fi invités.txt",
            "Réseau : CABINET-INVITES\nmot de passe : Plage-Soleil-2024\n".encode("utf-8"), 5)
    w.bytes("Partage temporaire/Paiement fournisseur.txt",
            f"Carte société : {card} exp 09/28\n".encode("utf-8"), 3)
    w.bytes("Partage temporaire/installeur-imprimante.exe", b"MZ" + b"\x00" * 6000, 2)   # extension interdite
    for user, n in (("alice", 3), ("bob", 9), ("carole", 1)):
        for i in range(n):
            w.docx(f"Utilisateurs/{user}/Note {i + 1}.docx", rng.randint(1, 200))
    w.bytes("Utilisateurs/bob/Vidéo réunion.mp4", os.urandom(420000), 15)
    for i in range(14):                                                              # données froides
        w.docx(f"Archives 2019/Dossier {i + 1:02d}/Mémoire.docx", rng.randint(1500, 2200))
        w.pdf(f"Archives 2019/Dossier {i + 1:02d}/Jugement.pdf", rng.randint(1500, 2200))
    blob = _pdf_bytes(["Presentation du cabinet Martin"] + [_text(rng, 10) for _ in range(40)], image_kb=1300)
    w.bytes("Présentation du cabinet.pdf", blob, 700)                                 # doublons
    w.bytes("Direction/Copie de Présentation du cabinet.pdf", blob, 90)
    w.bytes("Partage temporaire/Présentation du cabinet (1).pdf", blob, 4)
    w.bytes("desktop.ini", b"[.ShellClassInfo]\r\n", 400)                             # bloqué par OneDrive
    if os.name != "nt":                                                              # noms impossibles sous Windows
        w.bytes("Direction/CON.txt", b"note", 30)
        w.bytes("Comptabilité/facture?.pdf", _pdf_bytes(["facture"]), 30)
    # Documents abîmés (réparation) : tronqué, rempli de zéros, chiffré, PDF coupé.
    good = io.BytesIO()
    tmp = w.path("Documents abîmés/.tmp.docx")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    docrepair.write_minimal_docx(tmp, [_text(rng, 20) for _ in range(80)], title="Contrat de prestation")
    with open(tmp, "rb") as fh:
        good.write(fh.read())
    os.remove(tmp)
    data = good.getvalue()
    w.bytes("Documents abîmés/Contrat de prestation (tronqué).docx", data[: int(len(data) * 0.7)], 8)
    w.bytes("Documents abîmés/Tableau de bord (zéros).xlsx", b"\x00" * 24000, 8)
    w.bytes("Documents abîmés/Présentation client (chiffrée).pptx", os.urandom(52000), 8)
    pdf = _pdf_bytes([_text(rng, 9) for _ in range(40)])
    w.bytes("Documents abîmés/Procès-verbal (coupé).pdf", pdf[: int(len(pdf) * 0.75)], 8)
    return long_rel


def _transports(w, rng):
    """Transporteur : volume, fichiers anciens, doublons entre dossiers."""
    for week in range(1, 13):
        w.xlsx(f"Plannings/2024/Semaine {week:02d}.xlsx", 300 - week * 7)
    for i in range(1, 7):
        w.docx(f"Chauffeurs/Contrats/Contrat chauffeur {i}.docx", rng.randint(60, 900))
    for i in range(1, 11):
        w.pdf(f"Factures/2024/Facture T-{i:04d}.pdf", rng.randint(10, 200))
    for i in range(1, 16):
        w.pdf(f"Archives 2018/Lettres de voiture/LV-2018-{i:04d}.pdf", rng.randint(2200, 2600))
    blob = os.urandom(1400000)
    w.bytes("Plannings/Carte des tournées.png", blob, 400)
    w.bytes("Chauffeurs/Carte des tournées.png", blob, 50)
    w.bytes("Plannings/2024/~$Semaine 12.xlsx", b"\x00" * 165, 30)
    w.bytes("Plannings/sauvegarde.wbk", os.urandom(70000), 250)


def create(data_dir, seed=7):
    """Crée les fichiers et enregistre les clients / dossiers supervisés. Renvoie un résumé."""
    data_dir = os.path.abspath(data_dir)
    base = os.path.join(data_dir, DEMO_FOLDER)
    db = Database(os.path.join(data_dir, "vigiefs.db"))
    if os.path.isdir(base) or db.query_one("SELECT id FROM clients WHERE name LIKE '%(démo)'"):
        return {"created": False, "base": base}
    rng = random.Random(seed)
    now = time.time()
    shares = []
    specs = [
        ("Cabinet Martin Avocats (démo)", "Commun", os.path.join(base, "Cabinet Martin", "Commun"),
         "\\\\SRV-FICHIERS-CABINET-MARTIN\\"),
        ("Transports Leroy (démo)", "Exploitation", os.path.join(base, "Transports Leroy", "Exploitation"),
         "\\\\SRV-TRANSPORTS-LEROY\\Exploitation"),
    ]
    for client, share_name, root, access in specs:
        w = _Writer(root, rng)
        os.makedirs(root, exist_ok=True)
        if client.startswith("Cabinet"):
            long_rel = _cabinet(w, rng, len(root))
            # Nom de partage calculé pour que le fichier « trop long » dépasse 260 caractères vu du réseau.
            need = LONG_TARGET - len(access) - 1 - len(long_rel)
            access += ("Commun-Cabinet-Martin-Avocats-Paris-Documents-partages-de-tout-le-cabinet"
                       "-et-des-associes")[:max(6, min(80, need))]
            pads = iter(("Espace-partage-des-associes-et-des-collaborateurs-du-cabinet",
                         "Documents-de-travail-communs-a-tous-les-services", "Dossiers-en-cours-et-archives"))
            while len(access) + 1 + len(long_rel) < LONG_TARGET:
                pad = next(pads, None)
                if pad is None:
                    break
                access += "\\" + pad[:LONG_TARGET - len(access) - 2 - len(long_rel)]
        else:
            _transports(w, rng)
        cid = db.execute("INSERT INTO clients(name, notes, created_at) VALUES(?,?,?)",
                         (client, "Client fictif créé par « python -m vigiefs demo ».", now))
        sid = db.execute(
            "INSERT INTO shares(client_id, name, path, description, quota_bytes, scan_interval_min, monitor_enabled, "
            "screening_enabled, stale_days, created_at, access_path, archive_age_days) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, share_name, root, "Dossier de démonstration", int(w.size / 0.88), 60, 1, 1, 365, now, access, 1095))
        shares.append({"id": sid, "client": client, "name": share_name, "path": root, "files": w.count,
                       "size": w.size})
    db.execute("INSERT INTO quotas(share_id, rel_path, limit_bytes, warn_pct, crit_pct, mode, enforcement, auto_apply, "
               "notes, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (shares[0]["id"], "Utilisateurs", 40 * 1024, 80, 95, "soft", "none", 1,
                "Démo : quota d'alerte sur chaque dossier personnel", now, now))
    for key, values in (("recycle", {"dir": os.path.join(data_dir, "demo-corbeille"), "retention_days": 30}),
                        ("archive", {"root": os.path.join(data_dir, "demo-archives"), "verify_hash": True}),
                        ("screening", {"quarantine_dir": os.path.join(data_dir, "demo-quarantaine")}),
                        ("general", {"company_name": "Démo VigieFS"})):
        current = db.get_setting(key, {}) or {}
        current.update(values)
        db.set_setting(key, current)
    db.audit("console", "création du jeu de démonstration", base)
    return {"created": True, "base": base, "shares": shares}
