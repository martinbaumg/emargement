"""Support tickets: /tickets for students, /admin/tickets for the site's admin (roles.py).

Opening a ticket mails ADMIN_EMAIL; answering one from the admin space mails the student
back at the address they gave. Mail failures never lose a ticket — see mailer.send.
"""
import datetime
import re

from flask import redirect, request, session, url_for

import mailer
from app import app
from csrf import check_csrf, generate_csrf
from db import get_db
from layout import render
from roles import is_admin

# ouvert -> en_cours -> traite, with the DSFR badge each status is shown with.
STATUSES = {
    "ouvert": ("Ouvert", "fr-badge--info"),
    "en_cours": ("En cours", "fr-badge--new"),
    "traite": ("Traité", "fr-badge--success"),
}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s.]+\.[^@\s]+")
MAX_SUBJECT = 120
MAX_MESSAGE = 4000


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _rows_to_tickets(rows) -> list:
    tickets = []
    for r in rows:
        t = dict(r)
        t["status_label"], t["status_badge"] = STATUSES.get(t["status"], STATUSES["ouvert"])
        t["created_label"] = datetime.datetime.fromisoformat(t["created_at"]).strftime("%d/%m/%Y à %H:%M")
        tickets.append(t)
    return tickets


def open_tickets_count() -> int:
    """Badge next to « Tickets reçus » in the menu (layout) — admin only."""
    return get_db().execute("SELECT count(*) FROM tickets WHERE status != 'traite'").fetchone()[0]


TICKETS_TEMPLATE = """
<h1 class="fr-h3">Assistance</h1>
<p class="fr-mb-3w">Un problème, une erreur sur votre feuille d'émargement, une idée ?
Ouvrez un ticket : vous recevrez la réponse par e-mail, et vous la retrouverez aussi sur cette page.</p>
<div class="fr-callout fr-mb-4w">
    <h2 class="fr-callout__title fr-h5">Nouveau ticket</h2>
    <form method=post class="fr-mt-2w">
        <input type=hidden name="csrf_token" value="{{ csrf_token }}">
        <div class="fr-input-group">
            <label class="fr-label" for="subject">Sujet</label>
            <input class="fr-input" id="subject" name="subject" maxlength="{{ max_subject }}" required
                value="{{ draft.subject }}" placeholder="ex. Code UE manquant sur ma feuille">
        </div>
        <div class="fr-input-group">
            <label class="fr-label" for="email">Adresse e-mail pour la réponse
                <span class="fr-hint-text">Utilisée uniquement pour répondre à ce ticket.</span>
            </label>
            <input class="fr-input" type="email" id="email" name="email" required autocomplete="email"
                autocapitalize="none" value="{{ draft.email }}">
        </div>
        <div class="fr-input-group">
            <label class="fr-label" for="message">Message
                <span class="fr-hint-text">Décrivez ce qui s'est passé : la semaine concernée, le cours, ce que vous attendiez.</span>
            </label>
            <textarea class="fr-input" id="message" name="message" rows="5" maxlength="{{ max_message }}"
                required>{{ draft.message }}</textarea>
        </div>
        {# --icon-left on the group: without it DSFR renders these as icon-only buttons. #}
        <ul class="fr-btns-group fr-btns-group--inline-md fr-btns-group--icon-left">
            <li><button type=submit class="fr-btn fr-btn--icon-left fr-icon-mail-line">Envoyer le ticket</button></li>
        </ul>
    </form>
</div>
<h2 class="fr-h5">Vos tickets</h2>
{% if not tickets %}
<p class="fr-text--sm att-details">Vous n'avez encore ouvert aucun ticket.</p>
{% else %}
<div class="fr-accordions-group">
    {% for t in tickets %}
    <section class="fr-accordion">
        <h3 class="fr-accordion__title">
            <button type="button" class="fr-accordion__btn" aria-expanded="{{ 'true' if loop.first else 'false' }}"
                aria-controls="ticket-{{ t.id }}">#{{ t.id }} — {{ t.subject }}</button>
        </h3>
        <div class="fr-collapse" id="ticket-{{ t.id }}">
            <p class="fr-badge fr-badge--sm {{ t.status_badge }}">{{ t.status_label }}</p>
            <p class="fr-text--sm att-details fr-mt-1w">Ouvert le {{ t.created_label }} · réponse à {{ t.email }}</p>
            <p class="att-ticket-message fr-mt-2w">{{ t.message }}</p>
            {% if t.reply %}
            <div class="fr-highlight fr-mt-2w">
                <p class="fr-text--sm att-details fr-mb-1w">Réponse</p>
                <p class="att-ticket-message">{{ t.reply }}</p>
            </div>
            {% endif %}
        </div>
    </section>
    {% endfor %}
</div>
{% endif %}
"""


@app.route("/tickets", methods=["GET", "POST"])
def tickets():
    username = session.get("username")
    if not username:
        return redirect(url_for("login"))
    db = get_db()
    draft = {"subject": "", "email": "", "message": ""}

    if request.method == "POST":
        if not check_csrf():
            session["_flash"] = "Session expirée, réessayez."
            return redirect(url_for("tickets"))
        draft = {k: request.form.get(k, "").strip() for k in ("subject", "email", "message")}
        draft["subject"] = draft["subject"][:MAX_SUBJECT]
        draft["message"] = draft["message"][:MAX_MESSAGE]
        if not (draft["subject"] and draft["message"]) or not EMAIL_RE.fullmatch(draft["email"]):
            session["_flash"] = "Sujet, message et adresse e-mail valide sont nécessaires."
        else:
            cur = db.execute(
                "INSERT INTO tickets (owner_username, email, subject, message, status, created_at, updated_at, reply) "
                "VALUES (?, ?, ?, ?, 'ouvert', ?, ?, '')",
                (username, draft["email"], draft["subject"], draft["message"], _now(), _now()),
            )
            db.commit()
            sent = mailer.send(
                mailer.admin_email(),
                f"[Émargement] Nouveau ticket #{cur.lastrowid} : {draft['subject']}",
                f"{username} ({draft['email']}) a ouvert le ticket #{cur.lastrowid}.\n\n"
                f"Sujet : {draft['subject']}\n\n{draft['message']}\n\n"
                f"Traiter le ticket : {url_for('admin_tickets', _external=True)}",
                reply_to=draft["email"],
            )
            session["_flash"] = ("Ticket envoyé, vous recevrez la réponse par e-mail."
                                 if sent else
                                 "Ticket enregistré. L'e-mail de notification n'a pas pu partir, "
                                 "mais l'administrateur le verra dans son espace.")
            return redirect(url_for("tickets"))

    rows = db.execute("SELECT * FROM tickets WHERE owner_username=? ORDER BY id DESC", (username,))
    return render(TICKETS_TEMPLATE, tickets=_rows_to_tickets(rows), draft=draft,
                  max_subject=MAX_SUBJECT, max_message=MAX_MESSAGE, csrf_token=generate_csrf())


ADMIN_TEMPLATE = """
<h1 class="fr-h3">Tickets reçus</h1>
<p class="fr-mb-3w">{{ open_count }} ticket(s) à traiter sur {{ tickets|length }}.
{% if not mail_ready %}<br><span class="fr-text--sm att-details">SMTP non configuré : les réponses ne partiront pas par e-mail
(variables SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM).</span>{% endif %}</p>
{% if not tickets %}
<p class="fr-text--sm att-details">Aucun ticket pour l'instant.</p>
{% else %}
<div class="fr-accordions-group">
    {% for t in tickets %}
    <section class="fr-accordion">
        <h2 class="fr-accordion__title">
            <button type="button" class="fr-accordion__btn" aria-expanded="{{ 'true' if t.status != 'traite' else 'false' }}"
                aria-controls="admin-ticket-{{ t.id }}">#{{ t.id }} — {{ t.subject }} ({{ t.status_label }})</button>
        </h2>
        <div class="fr-collapse" id="admin-ticket-{{ t.id }}">
            <p class="fr-badge fr-badge--sm {{ t.status_badge }}">{{ t.status_label }}</p>
            <p class="fr-text--sm att-details fr-mt-1w">{{ t.owner_username }} · {{ t.email }} · ouvert le {{ t.created_label }}</p>
            <p class="att-ticket-message fr-mt-2w">{{ t.message }}</p>
            <form method=post action="{{ url_for('admin_ticket_update', ticket_id=t.id) }}" class="fr-mt-3w">
                <input type=hidden name="csrf_token" value="{{ csrf_token }}">
                <div class="fr-select-group">
                    <label class="fr-label" for="status-{{ t.id }}">Statut</label>
                    <select class="fr-select" id="status-{{ t.id }}" name="status">
                        {% for key, label in status_choices %}
                        <option value="{{ key }}" {{ 'selected' if t.status == key else '' }}>{{ label }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="fr-input-group">
                    <label class="fr-label" for="reply-{{ t.id }}">Réponse
                        <span class="fr-hint-text">Envoyée à {{ t.email }} si la notification est cochée.</span>
                    </label>
                    <textarea class="fr-input" id="reply-{{ t.id }}" name="reply" rows="4">{{ t.reply }}</textarea>
                </div>
                <div class="fr-checkbox-group fr-mb-3w">
                    <input type="checkbox" id="notify-{{ t.id }}" name="notify" value="1" checked>
                    <label class="fr-label" for="notify-{{ t.id }}">Prévenir l'étudiant par e-mail</label>
                </div>
                {# --icon-left on the group: without it DSFR renders these as icon-only buttons. #}
        <ul class="fr-btns-group fr-btns-group--inline-md fr-btns-group--icon-left">
                    <li><button type=submit class="fr-btn fr-btn--icon-left fr-icon-check-line">Enregistrer</button></li>
                </ul>
            </form>
        </div>
    </section>
    {% endfor %}
</div>
{% endif %}
"""


@app.route("/admin/tickets")
def admin_tickets():
    if not is_admin():
        return redirect(url_for("lessons"))
    db = get_db()
    # Open ones first, newest first inside each group: the admin's queue, not a log.
    rows = db.execute("SELECT * FROM tickets ORDER BY (status = 'traite'), id DESC")
    tickets_list = _rows_to_tickets(rows)
    return render(ADMIN_TEMPLATE, tickets=tickets_list, open_count=open_tickets_count(),
                  status_choices=[(k, v[0]) for k, v in STATUSES.items()],
                  mail_ready=mailer.configured(), csrf_token=generate_csrf())


@app.route("/admin/tickets/<int:ticket_id>", methods=["POST"])
def admin_ticket_update(ticket_id):
    if not is_admin():
        return redirect(url_for("lessons"))
    if not check_csrf():
        session["_flash"] = "Session expirée, réessayez."
        return redirect(url_for("admin_tickets"))
    db = get_db()
    row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        session["_flash"] = "Ticket introuvable."
        return redirect(url_for("admin_tickets"))

    status = request.form.get("status", "")
    if status not in STATUSES:
        status = row["status"]
    reply = request.form.get("reply", "").strip()[:MAX_MESSAGE]
    db.execute("UPDATE tickets SET status=?, reply=?, updated_at=? WHERE id=?", (status, reply, _now(), ticket_id))
    db.commit()

    if not request.form.get("notify"):
        session["_flash"] = f"Ticket #{ticket_id} mis à jour, sans e-mail."
        return redirect(url_for("admin_tickets"))
    label = STATUSES[status][0]
    body = f"Votre ticket #{ticket_id} « {row['subject']} » est maintenant : {label}.\n\n"
    if reply:
        body += f"Réponse :\n{reply}\n\n"
    body += (f"Votre message :\n{row['message']}\n\n"
             f"Suivi de vos tickets : {url_for('tickets', _external=True)}")
    sent = mailer.send(row["email"], f"[Émargement] Votre ticket #{ticket_id} : {row['subject']}", body,
                       reply_to=mailer.admin_email())
    session["_flash"] = (f"Ticket #{ticket_id} mis à jour, e-mail envoyé à {row['email']}." if sent else
                         f"Ticket #{ticket_id} mis à jour, mais l'e-mail n'a pas pu partir.")
    return redirect(url_for("admin_tickets"))
