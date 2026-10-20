"""Gestion des alertes (dédoublonnage, résolution auto) et notifications e-mail / webhook."""

import hashlib
import hmac
import json
import logging
import smtplib
import threading
import time
import urllib.request
from email.message import EmailMessage

from .config import SEVERITY_ORDER, merged_setting

log = logging.getLogger("vigiefs.alerts")

CATEGORY_LABELS = {
    "quota": "Quota",
    "forecast": "Prévision de saturation",
    "screening": "Filtrage de fichiers",
    "ransomware": "Rançongiciel",
    "path": "Disponibilité du dossier",
    "disk": "Espace disque",
    "scan": "Analyse",
    "system": "Système",
    "permissions": "Droits d'accès",
    "paths": "Chemins et nommage",
    "sensitive": "Données sensibles",
    "service": "Service VigieFS",
    "behavior": "Comportement utilisateur",
    "security": "Sécurité des comptes",
}


class AlertManager:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()

    def raise_alert(self, fingerprint, severity, category, title, message,
                    share=None, details=None, notify=True):
        """Crée ou met à jour une alerte active portant la même empreinte.

        Renvoie (alert_id, created). Une alerte acquittée dont la gravité augmente
        est rouverte afin que l'escalade ne passe pas inaperçue.
        """
        now = time.time()
        share_id = share["id"] if share else None
        client_id = share["client_id"] if share else None
        details_json = json.dumps(details or {}, default=str)
        with self._lock:
            existing = self.db.query_one(
                "SELECT * FROM alerts WHERE fingerprint = ? AND status IN ('open','acknowledged') "
                "ORDER BY id DESC LIMIT 1",
                (fingerprint,),
            )
            if existing:
                escalated = SEVERITY_ORDER[severity] > SEVERITY_ORDER.get(existing["severity"], 0)
                status = "open" if escalated else existing["status"]
                self.db.execute(
                    "UPDATE alerts SET last_seen = ?, count = count + 1, severity = ?, title = ?, "
                    "message = ?, details = ?, status = ? WHERE id = ?",
                    (now, severity if escalated else existing["severity"], title, message,
                     details_json, status, existing["id"]),
                )
                if escalated and notify:
                    self._notify_async(existing["id"])
                return existing["id"], False
            alert_id = self.db.execute(
                "INSERT INTO alerts(share_id, client_id, fingerprint, severity, category, title, message, "
                "details, status, count, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?, 'open', 1, ?, ?)",
                (share_id, client_id, fingerprint, severity, category, title, message, details_json, now, now),
            )
        if notify:
            self._notify_async(alert_id)
        return alert_id, True

    def resolve(self, fingerprint, by="auto"):
        return self.db.execute(
            "UPDATE alerts SET status = 'resolved', resolved_at = ?, ack_by = COALESCE(ack_by, ?) "
            "WHERE fingerprint = ? AND status IN ('open','acknowledged')",
            (time.time(), by, fingerprint),
        )

    def acknowledge(self, alert_id, by):
        return self.db.execute(
            "UPDATE alerts SET status = 'acknowledged', ack_by = ?, ack_at = ? WHERE id = ? AND status = 'open'",
            (by, time.time(), alert_id),
        )

    def close(self, alert_id, by):
        return self.db.execute(
            "UPDATE alerts SET status = 'resolved', resolved_at = ?, ack_by = COALESCE(ack_by, ?) "
            "WHERE id = ? AND status != 'resolved'",
            (time.time(), by, alert_id),
        )

    def get(self, alert_id):
        row = self.db.query_one(
            "SELECT a.*, s.name AS share_name, s.path AS share_path, c.name AS client_name FROM alerts a "
            "LEFT JOIN shares s ON s.id = a.share_id LEFT JOIN clients c ON c.id = a.client_id WHERE a.id = ?",
            (alert_id,),
        )
        if row:
            row["details"] = json.loads(row["details"] or "{}")
        return row

    # --- Notifications ---
    def _notify_async(self, alert_id):
        threading.Thread(target=self._notify, args=(alert_id,), daemon=True).start()

    def _notify(self, alert_id):
        alert = self.get(alert_id)
        if not alert:
            return
        try:
            smtp = merged_setting(self.db, "smtp")
            if smtp.get("enabled") and self._passes(alert, smtp):
                send_email(smtp, alert, merged_setting(self.db, "general"))
        except Exception as exc:  # noqa: BLE001 — une notification ne doit jamais casser la supervision
            log.warning("Échec envoi e-mail pour l'alerte %s : %s", alert_id, exc)
        try:
            hook = merged_setting(self.db, "webhook")
            if hook.get("enabled") and hook.get("url") and self._passes(alert, hook):
                send_webhook(hook, alert_payload(alert))
        except Exception as exc:  # noqa: BLE001
            log.warning("Échec webhook pour l'alerte %s : %s", alert_id, exc)

    @staticmethod
    def _passes(alert, channel):
        return SEVERITY_ORDER[alert["severity"]] >= SEVERITY_ORDER.get(channel.get("min_severity", "warning"), 1)


def alert_payload(alert):
    return {
        "source": "vigiefs",
        "event": "alert",
        "id": alert["id"],
        "severity": alert["severity"],
        "category": alert["category"],
        "title": alert["title"],
        "message": alert["message"],
        "status": alert["status"],
        "client": alert.get("client_name"),
        "share": alert.get("share_name"),
        "path": alert.get("share_path"),
        "count": alert["count"],
        "first_seen": alert["first_seen"],
        "last_seen": alert["last_seen"],
        "details": alert.get("details", {}),
    }


def send_email(smtp, alert, general):
    recipients = smtp.get("recipients") or []
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.replace(";", ",").split(",") if r.strip()]
    if not recipients:
        raise ValueError("Aucun destinataire configuré")
    msg = EmailMessage()
    sev = {"critical": "CRITIQUE", "warning": "AVERTISSEMENT", "info": "INFO"}[alert["severity"]]
    who = alert.get("client_name") or general.get("company_name", "")
    msg["Subject"] = f"[VigieFS][{sev}] {who} — {alert['title']}"
    msg["From"] = smtp.get("sender") or "vigiefs@localhost"
    msg["To"] = ", ".join(recipients)
    lines = [
        f"Gravité   : {sev}",
        f"Catégorie : {CATEGORY_LABELS.get(alert['category'], alert['category'])}",
        f"Client    : {alert.get('client_name') or '-'}",
        f"Dossier   : {alert.get('share_name') or '-'} ({alert.get('share_path') or '-'})",
        f"Date      : {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(alert['last_seen']))}",
        "",
        alert["message"],
    ]
    if alert.get("details"):
        lines += ["", "Détails :", json.dumps(alert["details"], indent=2, ensure_ascii=False, default=str)]
    msg.set_content("\n".join(lines))
    port = int(smtp.get("port") or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(smtp["host"], port, timeout=20)
    else:
        server = smtplib.SMTP(smtp["host"], port, timeout=20)
        if smtp.get("starttls", True):
            server.starttls()
    try:
        if smtp.get("username"):
            server.login(smtp["username"], smtp.get("password", ""))
        server.send_message(msg)
    finally:
        server.quit()


def send_webhook(hook, payload):
    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "VigieFS"}
    if hook.get("secret"):
        sig = hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
        headers["X-VigieFS-Signature"] = f"sha256={sig}"
    req = urllib.request.Request(hook["url"], data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status
