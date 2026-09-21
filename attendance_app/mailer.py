"""Outgoing mail for the ticket system.

Everything comes from the environment, so no address or password is ever in the code:
SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (defaults to SMTP_USER),
SMTP_SSL ("1" for an implicit-TLS port like 465), and ADMIN_EMAIL, the address notified
when a ticket is opened.

send() never raises: a ticket that is saved but not mailed is a ticket the admin still
sees in /admin/tickets, while an exception here would lose the student's message.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_EMAIL = "contact@baumgaertner.fr"


def settings() -> dict:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587") or 587),
        "user": user,
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "sender": os.environ.get("SMTP_FROM", "").strip() or user,
        "implicit_ssl": os.environ.get("SMTP_SSL", "").strip() in ("1", "true", "yes"),
    }


def admin_email() -> str:
    return os.environ.get("ADMIN_EMAIL", "").strip() or DEFAULT_ADMIN_EMAIL


def configured() -> bool:
    """False when SMTP isn't set up — callers tell the student their ticket is saved but
    that no mail went out, instead of pretending it did."""
    s = settings()
    return bool(s["host"] and s["sender"])


def send(to: str, subject: str, body: str, reply_to: str = "") -> bool:
    if not configured():
        logger.warning("mailer: SMTP not configured, mail to %s dropped (%s)", to, subject)
        return False
    s = settings()
    message = EmailMessage()
    message["From"] = s["sender"]
    message["To"] = to
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    try:
        if s["implicit_ssl"]:
            server = smtplib.SMTP_SSL(s["host"], s["port"], timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s["host"], s["port"], timeout=15)
        with server:
            if not s["implicit_ssl"]:
                server.starttls(context=ssl.create_default_context())
            if s["user"]:
                server.login(s["user"], s["password"])
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.error("mailer: sending to %s failed (%r)", to, exc)
        return False
    return True


if __name__ == "__main__":
    # `python3 -m mailer destinataire@exemple.fr` — checks the SMTP_* variables of the shell
    # it runs in by actually sending one mail, without going through the app.
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    recipient = sys.argv[1] if len(sys.argv) > 1 else admin_email()
    current = settings()
    print(f"serveur : {current['host'] or '(SMTP_HOST vide)'}:{current['port']} "
          f"| TLS implicite : {current['implicit_ssl']} | compte : {current['user'] or '(aucun)'} "
          f"| expéditeur : {current['sender'] or '(SMTP_FROM vide)'}")
    ok = send(recipient, "[Émargement] Test de configuration SMTP",
              "Si vous lisez ce message, l'envoi d'e-mails de l'application fonctionne.")
    print(f"envoi à {recipient} : {'réussi' if ok else 'échoué (voir le message ci-dessus)'}")
    sys.exit(0 if ok else 1)
