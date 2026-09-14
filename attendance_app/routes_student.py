"""Student-facing routes: login/logout, lessons, profile, and the PDF export."""
import datetime
import itertools
import secrets

import requests
from flask import Response, redirect, request, session, url_for

import pass_schedule as ps
import pdf_export
from app import app
from csrf import check_csrf, generate_csrf
from db import get_db
from layout import render
from lesson_utils import _time_bounds, merge_contiguous_lessons, short_teacher_name, teacher_names
from pass_session import LIVE_SESSIONS, current_pass_session, fetch_events_cached, week_fetched

# ---------------------------------------------------------------- student side

LOGIN_TEMPLATE = """
<div class="fr-grid-row fr-grid-row--center">
    <div class="fr-col-12 fr-col-sm-8 fr-col-md-6 fr-col-lg-4">
        <div class="fr-callout">
            <h1 class="fr-callout__title fr-h4">Connexion à votre espace</h1>
            <p class="fr-text--sm fr-mb-3w">Utilisez vos identifiants PASS IMT-Atlantique</p>
            {% if err %}<div class="fr-alert fr-alert--error fr-mb-3w"><p>{{ err }}</p></div>{% endif %}
            <form method=post id="att-login-form">
                <input type=hidden name="csrf_token" value="{{ csrf_token }}">
                <div class="fr-input-group">
                    <label class="fr-label" for="username">Identifiant</label>
                    <div class="fr-input-wrap fr-icon-account-line">
                        <input class="fr-input" id="username" name="username" autocomplete="username" required
                            autocapitalize="none" autocorrect="off" spellcheck="false" enterkeyhint="next">
                    </div>
                </div>
                <div class="fr-input-group">
                    <label class="fr-label" for="password">Mot de passe
                        <span class="fr-hint-text">Reste en mémoire ici le temps de la session, jamais écrit sur le disque.</span>
                    </label>
                    <div class="fr-input-wrap fr-icon-lock-line">
                        <input class="fr-input" type="password" id="password" name="password"
                            autocomplete="current-password" required enterkeyhint="go">
                    </div>
                </div>
                <button type=submit class="fr-btn fr-btn--icon-left fr-icon-lock-line fr-mt-2w att-login-btn">Se connecter</button>
            </form>
            <div class="att-login-progress" id="att-login-progress" role="status" aria-live="polite" hidden>
                <div class="att-progress"></div>
                <p class="fr-text--sm fr-mt-1w fr-mb-0" id="att-login-step"></p>
            </div>
        </div>
    </div>
</div>
<script>
(function () {
    // Signing in to PASS, then loading the current week (agenda + one detail popup per
    // course), runs before the next page arrives — tens of seconds with nothing but the
    // browser's own spinner. The steps are timed, not reported by the server: they only
    // say what is happening in that order, never claim a step is done.
    var form = document.getElementById("att-login-form");
    var btn = form.querySelector("button[type=submit]");
    var progress = document.getElementById("att-login-progress");
    var step = document.getElementById("att-login-step");
    var label = btn.textContent;
    var steps = [
        [0, "Connexion à PASS…"],
        [4000, "Récupération de votre emploi du temps…"],
        [10000, "Récupération des intervenants de chaque cours…"],
        [25000, "Encore quelques secondes, PASS est parfois lent…"]
    ];
    var timers = [];

    form.addEventListener("submit", function (ev) {
        if (btn.classList.contains("att-loading")) { ev.preventDefault(); return; }  // Enter pressed again
        btn.classList.replace("fr-icon-lock-line", "fr-icon-refresh-line");
        btn.classList.add("att-loading");
        btn.setAttribute("aria-busy", "true");
        btn.textContent = "Connexion en cours…";
        // readOnly, not disabled: disabled fields would be left out of the POST.
        form.querySelectorAll(".fr-input").forEach(function (input) { input.readOnly = true; });
        progress.hidden = false;
        steps.forEach(function (s) {
            timers.push(setTimeout(function () { step.textContent = s[1]; }, s[0]));
        });
    });

    // Back/forward cache restores the page mid-loading: put the form back.
    window.addEventListener("pageshow", function (ev) {
        if (!ev.persisted) return;
        timers.forEach(clearTimeout);
        timers = [];
        btn.classList.remove("att-loading");
        btn.classList.replace("fr-icon-refresh-line", "fr-icon-lock-line");
        btn.removeAttribute("aria-busy");
        btn.textContent = label;
        form.querySelectorAll(".fr-input").forEach(function (input) { input.readOnly = false; });
        progress.hidden = true;
        step.textContent = "";
    });
})();
</script>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not check_csrf():
            return render(LOGIN_TEMPLATE, err="Session expirée, réessayez.", csrf_token=generate_csrf())
        username = request.form["username"].strip()
        password = request.form["password"]
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        try:
            ps.login(s, username, password)
        except RuntimeError as e:
            return render(LOGIN_TEMPLATE, err=str(e), csrf_token=generate_csrf())
        token = secrets.token_hex(16)
        LIVE_SESSIONS[token] = s
        session["token"] = token
        session["username"] = username
        session["_flash"] = "Connecté."
        return redirect(url_for("lessons"))

    return render(LOGIN_TEMPLATE, err=None, csrf_token=generate_csrf())


@app.route("/logout")
def logout():
    token = session.pop("token", None)
    session.pop("username", None)
    if token:
        LIVE_SESSIONS.pop(token, None)
        get_db().execute("DELETE FROM live_sessions WHERE token=?", (token,))
        get_db().commit()
    session["_flash"] = "Déconnecté."
    return redirect(url_for("login"))


def _expire_pass_session():
    """A route's live PASS call (menu/agenda fetch) raised RuntimeError — the cookie
    restored from live_sessions is dead server-side (PASS session timeout, or a menu
    structure change). Same cleanup as /logout, but with a message telling the user
    why they're back at the login form instead of a 500."""
    token = session.pop("token", None)
    session.pop("username", None)
    if token:
        LIVE_SESSIONS.pop(token, None)
        get_db().execute("DELETE FROM live_sessions WHERE token=?", (token,))
        get_db().commit()
    session["_flash"] = "Session PASS expirée, reconnectez-vous."
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/")
def index():
    return redirect(url_for("lessons"))


# Bounded so a hand-edited ?week= can't walk the scraper through arbitrarily many live
# PASS fetches — a term's worth of navigation either way is plenty.
MAX_WEEK_OFFSET = 26


def _week_ref(default: int = 0) -> tuple:
    """(offset, reference date) for the week being browsed, from ?week=<offset>."""
    try:
        offset = int(request.args.get("week", default))
    except ValueError:
        offset = default
    offset = max(-MAX_WEEK_OFFSET, min(MAX_WEEK_OFFSET, offset))
    return offset, datetime.date.today() + datetime.timedelta(weeks=offset)


@app.route("/lessons")
def lessons():
    s = current_pass_session()
    if not s:
        return redirect(url_for("login"))
    username = session["username"]

    force = request.args.get("refresh") == "1"
    week_offset, week_ref = _week_ref()
    try:
        events = fetch_events_cached(s, username, force=force, week_ref=week_ref)
    except RuntimeError:
        return _expire_pass_session()
    if force:
        session["_flash"] = "Cours réactualisés depuis PASS."
    week_loaded = week_fetched(username, week_ref)

    excluded_ids, excluded_titles = _exclusions(get_db(), username)
    for e in events:
        e["time_label"] = e["time"].replace("H", ":").replace("-", " – ")
        e["teacher"] = " / ".join(teacher_names([e]))
        e["always_excluded"] = e["title"] in excluded_titles
        e["excluded"] = e["always_excluded"] or e["id"] in excluded_ids
    excluded_count = sum(1 for e in events if e["excluded"])

    today_str = datetime.date.today().strftime("%Y%m%d")
    monday, sunday = ps.week_bounds(week_ref)
    week_label = (f"{datetime.datetime.strptime(monday, '%Y%m%d').strftime('%d/%m')}"
                  f" au {datetime.datetime.strptime(sunday, '%Y%m%d').strftime('%d/%m/%Y')}")
    days = []
    for date_key, group in itertools.groupby(events, key=lambda e: e["date"]):
        d = datetime.datetime.strptime(date_key, "%Y%m%d")
        days.append({
            "date": date_key,
            "tab_id": f"tab-{date_key}",
            "panel_id": f"tab-{date_key}-panel",
            "label": f"{ps.DAYS_FR[d.weekday()]} {d.strftime('%d/%m')}",
            "short_label": f"{ps.DAYS_FR[d.weekday()][:3]}. {d.strftime('%d')}",
            "lessons": list(group),
        })
    # ?day= keeps the student on the tab they were on after a form post (exclusion rules).
    day_keys = [day["date"] for day in days]
    wanted_day = request.args.get("day", "")
    if wanted_day in day_keys:
        default_date = wanted_day
    else:
        default_date = today_str if today_str in day_keys else (day_keys[0] if days else None)
    for day in days:
        day["selected"] = day["date"] == default_date

    return render(
        """
        {% macro pdf_toggle(e, day, prefix) -%}
        <form method=post action="{{ url_for('lesson_exclusion') }}" class="att-exclusion-form">
            <input type=hidden name="csrf_token" value="{{ csrf_token }}">
            <input type=hidden name="lesson_id" value="{{ e.id }}">
            <input type=hidden name="week" value="{{ week_offset }}">
            <input type=hidden name="day" value="{{ day.date }}">
            <div class="fr-toggle">
                <input type="checkbox" class="fr-toggle__input" id="{{ prefix }}pdf-{{ e.id }}" name="included" value="1"
                    data-lesson-id="{{ e.id }}" {{ '' if e.excluded else 'checked' }}>
                <label class="fr-toggle__label" for="{{ prefix }}pdf-{{ e.id }}"
                    data-fr-checked-label="Inclus" data-fr-unchecked-label="Exclu">
                    <span class="fr-sr-only">{{ e.title }}, {{ e.time_label }} : sur le PDF</span></label>
            </div>
            <noscript><button type=submit class="fr-btn fr-btn--tertiary fr-btn--sm fr-mt-1w">Enregistrer</button></noscript>
        </form>
        {%- endmacro %}
        {% macro rule_form(e, day) -%}
        <form method=post action="{{ url_for('title_exclusion') }}" class="att-rule-form" {{ '' if e.excluded else 'hidden' }}>
            <input type=hidden name="csrf_token" value="{{ csrf_token }}">
            <input type=hidden name="week" value="{{ week_offset }}">
            <input type=hidden name="day" value="{{ day.date }}">
            <input type=hidden name="action" value="add">
            <input type=hidden name="title" value="{{ e.title }}">
            <button type=submit class="fr-btn fr-btn--tertiary-no-outline fr-btn--sm fr-btn--icon-left fr-icon-eye-off-line fr-mt-1w"
                title="Exclure toutes les séances « {{ e.title }} », toutes semaines">Toujours exclure ce cours</button>
        </form>
        {%- endmacro %}
        <div class="att-toolbar">
        <div class="att-week-bar fr-mb-3w">
            <a class="fr-btn fr-btn--secondary fr-icon-arrow-left-s-line" href="{{ url_for('lessons', week=week_offset - 1) }}"
               title="Semaine précédente">Semaine précédente</a>
            <div class="att-week-bar__label">
                <h1 class="fr-h4 fr-mb-0">Semaine du {{ week_label }}</h1>
                {% if week_offset %}
                <a class="fr-link fr-link--sm" href="{{ url_for('lessons') }}">Revenir à la semaine courante</a>
                {% endif %}
            </div>
            <a class="fr-btn fr-btn--secondary fr-icon-arrow-right-s-line" href="{{ url_for('lessons', week=week_offset + 1) }}"
               title="Semaine suivante">Semaine suivante</a>
        </div>
        <ul class="fr-btns-group fr-btns-group--inline-md fr-btns-group--icon-left">
            <li>
                <a class="fr-btn fr-icon-file-pdf-line" href="{{ url_for('export_pdf', week=week_offset) }}">Télécharger la feuille d'émargement</a>
            </li>
            <li>
                <a class="fr-btn fr-btn--secondary fr-icon-refresh-line" href="{{ url_for('lessons', week=week_offset, refresh=1) }}">Actualiser depuis PASS</a>
            </li>
        </ul>
        </div>
        <p class="fr-text--sm att-details fr-mb-2w" id="att-excluded-count" aria-live="polite">{% if excluded_count %}{{ excluded_count }} séance(s) exclue(s) du PDF cette semaine.{% endif %}</p>
        {% if not week_loaded %}
        <div class="fr-alert fr-alert--info fr-alert--sm fr-mb-3w">
            <p>Cette semaine n'a pas encore été chargée depuis PASS.
            <a class="fr-link" href="{{ url_for('lessons', week=week_offset, refresh=1) }}">La charger maintenant</a>
            (quelques secondes).</p>
        </div>
        {% elif not days %}
        <div class="fr-alert fr-alert--warning fr-alert--sm fr-mb-3w">
            <p><strong>Aucun cours n'a pu être chargé depuis PASS pour cette semaine.</strong></p>
            <p class="fr-mt-1w">Si vous avez bien des cours, les préférences de votre agenda PASS masquent
            probablement leurs détails. Pour les afficher :</p>
            <ol class="fr-mt-1w">
                <li>sur PASS, ouvrez l'<strong>Agenda</strong> et cliquez sur <strong>Préférences</strong>, en haut à droite ;</li>
                <li>dans <strong>Détails affichés</strong>, cochez toutes les cases : Heures, Description,
                Ressources, Formateurs, Projets et Organismes ;</li>
                <li>cliquez sur <strong>Valider</strong> ;</li>
                <li>revenez ici et cliquez sur « Actualiser depuis PASS ».</li>
            </ol>
        </div>
        {% endif %}
        {% if days %}
        <div class="fr-tabs">
            <ul class="fr-tabs__list" role="tablist" aria-label="Jours de la semaine">
                {% for day in days %}
                <li role="presentation">
                    <button type="button" id="{{ day.tab_id }}" class="fr-tabs__tab"
                        tabindex="{{ '0' if day.selected else '-1' }}" role="tab"
                        aria-selected="{{ 'true' if day.selected else 'false' }}"
                        aria-controls="{{ day.panel_id }}"><span class="fr-hidden-md">{{ day.short_label }}</span><span class="fr-hidden fr-unhidden-md">{{ day.label }}</span></button>
                </li>
                {% endfor %}
            </ul>
            {% for day in days %}
            <div id="{{ day.panel_id }}" class="fr-tabs__panel {{ 'fr-tabs__panel--selected' if day.selected else '' }}"
                role="tabpanel" aria-labelledby="{{ day.tab_id }}" tabindex="0">
                {# Phones: one card per session. Each session is rendered twice (cards / table),
                   the script below keeps both copies of a toggle in sync. #}
                <ul class="fr-raw-list att-lesson-list fr-hidden-md">
                    {% for e in day.lessons %}
                    <li class="att-lesson-card {{ 'att-excluded' if e.excluded else '' }}" data-lesson-row="{{ e.id }}">
                        <div class="att-lesson-card__head">
                            <p class="att-lesson-card__time">{{ e.time_label }}</p>
                            {% if e.always_excluded %}
                            <p class="fr-badge fr-badge--sm">Toujours exclu</p>
                            {% else %}
                            {{ pdf_toggle(e, day, 'm-') }}
                            {% endif %}
                        </div>
                        <p class="att-lesson-card__title">{{ e.title }}</p>
                        {% if e.teacher %}
                        <p class="att-lesson-card__meta"><span class="fr-icon-user-line fr-icon--sm" aria-hidden="true"></span> {{ e.teacher }}</p>
                        {% endif %}
                        {% if not e.always_excluded %}{{ rule_form(e, day) }}{% endif %}
                    </li>
                    {% endfor %}
                </ul>
                <div class="fr-table fr-table--bordered fr-table--layout-fixed att-lessons-table fr-hidden fr-unhidden-md">
                <table>
                <thead><tr><th class="att-time-col">Horaire</th><th>Cours</th><th>Intervenant</th><th class="att-pdf-col">Sur le PDF</th></tr></thead>
                <tbody>
                {% for e in day.lessons %}
                <tr class="{{ 'att-excluded' if e.excluded else '' }}" data-lesson-row="{{ e.id }}">
                    <td>{{ e.time_label }}</td>
                    <td>{{ e.title }}</td>
                    <td><span class="att-details">{{ e.teacher or '—' }}</span></td>
                    <td class="att-pdf-col">
                        {% if e.always_excluded %}
                        <p class="fr-badge fr-badge--sm">Toujours exclu</p>
                        {% else %}
                        {{ pdf_toggle(e, day, 'd-') }}
                        {{ rule_form(e, day) }}
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
                </tbody>
                </table>
                </div>
            </div>
            {% endfor %}
        </div>
        <section class="att-rules fr-mt-4w" aria-labelledby="att-rules-title">
            <h2 id="att-rules-title" class="fr-h6 fr-mb-1w">Séances exclues du PDF</h2>
            <p class="fr-text--sm att-details">Désactivez l'interrupteur d'une séance pour la retirer de la
            feuille d'émargement (travail en autonomie, séance sans signature…). « Toujours exclure ce cours »
            l'applique à toutes les semaines.</p>
            {% if always_excluded %}
            <p class="fr-text--sm fr-mb-1w">Cours toujours exclus, toutes semaines :</p>
            <ul class="fr-tags-group">
                {% for t in always_excluded %}
                <li>
                    <form method=post action="{{ url_for('title_exclusion') }}" class="att-inline-form att-rule-remove-form">
                        <input type=hidden name="csrf_token" value="{{ csrf_token }}">
                        <input type=hidden name="week" value="{{ week_offset }}">
                        <input type=hidden name="day" value="{{ selected_day }}">
                        <input type=hidden name="action" value="remove">
                        <input type=hidden name="title" value="{{ t }}">
                        <button type=submit class="fr-tag fr-tag--dismiss" aria-label="Remettre « {{ t }} » sur le PDF">{{ t }}</button>
                    </form>
                </li>
                {% endfor %}
            </ul>
            {% endif %}
        </section>
        {% endif %}
        <script>
        (function () {
            // Saves each toggle in place; falls back to a normal form post (full reload) if
            // the request fails, so the PDF never silently disagrees with what's shown.
            var counter = document.getElementById('att-excluded-count');
            function refreshCount() {
                var n = document.querySelectorAll('.att-lessons-table tr.att-excluded').length;
                counter.textContent = n ? n + ' séance(s) exclue(s) du PDF cette semaine.' : '';
            }
            // DSFR's dismissible tag removes its own button from the DOM on click, which
            // detaches it from the form before the browser submits — so the post never left.
            // Capture phase on the form runs before DSFR's handler on the button itself.
            document.querySelectorAll('.att-rule-remove-form').forEach(function (form) {
                form.addEventListener('click', function (ev) {
                    if (ev.target.closest('button[type=submit]')) {
                        ev.preventDefault();
                        form.submit();
                    }
                }, true);
            });
            document.querySelectorAll('.att-exclusion-form .fr-toggle__input').forEach(function (input) {
                input.addEventListener('change', function () {
                    var included = input.checked;
                    var rows = document.querySelectorAll('[data-lesson-row="' + CSS.escape(input.dataset.lessonId) + '"]');
                    rows.forEach(function (row) {
                        row.classList.toggle('att-excluded', !included);
                        row.querySelectorAll('.fr-toggle__input').forEach(function (t) { t.checked = included; });
                        row.querySelectorAll('.att-rule-form').forEach(function (f) { f.hidden = included; });
                    });
                    refreshCount();
                    var form = input.form;
                    fetch(form.action, {
                        method: 'POST', body: new FormData(form), credentials: 'same-origin',
                        headers: {'X-Requested-With': 'fetch'}
                    }).then(function (r) {
                        if (!r.ok) throw new Error(r.status);
                    }).catch(function () { form.submit(); });
                });
            });
        })();
        </script>
        """,
        days=days,
        week_offset=week_offset,
        week_label=week_label,
        week_loaded=week_loaded,
        excluded_count=excluded_count,
        always_excluded=sorted(excluded_titles),
        selected_day=default_date or "",
        csrf_token=generate_csrf(),
    )


def _exclusions(db, username):
    """(lesson ids excluded one by one, titles excluded for every session) — both only
    drop lessons from the PDF; /lessons still lists them."""
    ids = {r["lesson_id"] for r in db.execute(
        "SELECT lesson_id FROM lesson_exclusions WHERE owner_username=?", (username,)
    )}
    titles = {r["title"] for r in db.execute(
        "SELECT title FROM title_exclusions WHERE owner_username=?", (username,)
    )}
    return ids, titles


def _back_to_lessons():
    week = request.form.get("week", 0, type=int)
    day = request.form.get("day", "")
    return redirect(url_for("lessons", week=week, day=day) if day else url_for("lessons", week=week))


@app.route("/lessons/exclusion", methods=["POST"])
def lesson_exclusion():
    """Puts one session on or off the PDF. Posted by the row's toggle via fetch (answers
    JSON, the page has already updated itself) or as a plain form without JS (redirects
    back to the same week and day)."""
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    if not current_pass_session():
        return ({"error": "login"}, 401) if wants_json else redirect(url_for("login"))
    if not check_csrf():
        if wants_json:
            return {"error": "csrf"}, 400
        session["_flash"] = "Session expirée, réessayez."
        return _back_to_lessons()
    username = session["username"]
    db = get_db()

    lesson_id = request.form.get("lesson_id", "")
    if not db.execute(
        "SELECT 1 FROM lessons_cache WHERE owner_username=? AND lesson_id=?", (username, lesson_id)
    ).fetchone():
        return ({"error": "unknown lesson"}, 404) if wants_json else _back_to_lessons()

    if request.form.get("included"):
        db.execute("DELETE FROM lesson_exclusions WHERE owner_username=? AND lesson_id=?", (username, lesson_id))
    else:
        db.execute(
            "INSERT OR IGNORE INTO lesson_exclusions (lesson_id, owner_username, created_at) VALUES (?, ?, ?)",
            (lesson_id, username, datetime.datetime.now().isoformat(timespec="seconds")),
        )
    db.commit()
    return {"ok": True} if wants_json else _back_to_lessons()


@app.route("/lessons/title-exclusion", methods=["POST"])
def title_exclusion():
    """Always excludes (or puts back) every session sharing a title, across all weeks —
    for recurring slots like « Travail Autonomie » that never need signing."""
    if not current_pass_session():
        return redirect(url_for("login"))
    if not check_csrf():
        session["_flash"] = "Session expirée, réessayez."
        return _back_to_lessons()
    username = session["username"]
    db = get_db()
    title = request.form.get("title", "")

    if request.form.get("action") == "remove":
        db.execute("DELETE FROM title_exclusions WHERE owner_username=? AND title=?", (username, title))
        session["_flash"] = f"« {title} » figure de nouveau sur vos PDF."
    elif db.execute(
        "SELECT 1 FROM lessons_cache WHERE owner_username=? AND title=?", (username, title)
    ).fetchone():
        db.execute(
            "INSERT OR IGNORE INTO title_exclusions (owner_username, title, created_at) VALUES (?, ?, ?)",
            (username, title, datetime.datetime.now().isoformat(timespec="seconds")),
        )
        session["_flash"] = f"Toutes les séances « {title} » sont exclues de vos PDF."
    db.commit()
    return _back_to_lessons()


@app.route("/profile", methods=["GET", "POST"])
def profile():
    s = current_pass_session()
    if not s:
        return redirect(url_for("login"))
    username = session["username"]
    db = get_db()

    if request.method == "POST":
        if not check_csrf():
            session["_flash"] = "Session expirée, réessayez."
            return redirect(url_for("profile"))
        db.execute(
            "INSERT INTO profiles (owner_username, nom, prenom, formation, taf, campus, apprentissage, ue_table, "
            "show_total_hours, show_teacher_names) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_username) DO UPDATE SET nom=excluded.nom, prenom=excluded.prenom, "
            "formation=excluded.formation, taf=excluded.taf, campus=excluded.campus, "
            "apprentissage=excluded.apprentissage, ue_table=excluded.ue_table, "
            "show_total_hours=excluded.show_total_hours, show_teacher_names=excluded.show_teacher_names",
            (username, request.form["nom"].strip(), request.form["prenom"].strip(),
             request.form["formation"].strip(), request.form["taf"].strip(), request.form["campus"].strip(),
             1, request.form.get("ue_table", "").strip(),
             1 if request.form.get("show_total_hours") else 0,
             1 if request.form.get("show_teacher_names") else 0),
        )
        db.commit()
        session["_flash"] = "Profil enregistré."
        return redirect(url_for("profile"))

    row = db.execute("SELECT * FROM profiles WHERE owner_username=?", (username,)).fetchone()
    if row:
        p = dict(row)
    else:
        p = {"nom": "", "prenom": "", "formation": "", "taf": "",
             "campus": "BREST", "apprentissage": 1, "ue_table": "", "show_total_hours": 0,
             "show_teacher_names": 1}
        try:
            dossier = ps.fetch_dossier(s, username=username)
        except Exception:
            dossier = {}
        p.update({k: v for k, v in dossier.items() if v})

    return render(
        """
        <h1 class="fr-h3">Profil</h1>
        <p class="fr-mb-2w">Ces informations remplissent l'en-tête de la feuille d'émargement. Nom, prénom,
        formation et TAF sont pré-remplis depuis votre dossier étudiant PASS.</p>
        <ul class="fr-btns-group fr-btns-group--inline-md fr-btns-group--icon-left">
            <li>
                <a class="fr-btn fr-btn--secondary fr-icon-refresh-line" href="{{ url_for('profile_import') }}">Réimporter depuis PASS</a>
            </li>
        </ul>
        <form method=post>
            <input type=hidden name="csrf_token" value="{{ csrf_token }}">
            <div class="fr-input-group">
                <label class="fr-label" for="nom">Nom</label>
                <input class="fr-input" id="nom" name="nom" value="{{ p.nom }}" required
                    autocomplete="family-name" autocapitalize="characters">
            </div>
            <div class="fr-input-group">
                <label class="fr-label" for="prenom">Prénom</label>
                <input class="fr-input" id="prenom" name="prenom" value="{{ p.prenom }}" required
                    autocomplete="given-name" autocapitalize="words">
            </div>
            <div class="fr-input-group">
                <label class="fr-label" for="formation">Formation</label>
                <input class="fr-input" id="formation" name="formation" value="{{ p.formation }}" placeholder="ex. FIP A3"
                    autocomplete="off" autocapitalize="characters">
            </div>
            <div class="fr-input-group">
                <label class="fr-label" for="taf">TAF</label>
                <input class="fr-input" id="taf" name="taf" value="{{ p.taf }}" placeholder="ex. NETCLOUD"
                    autocomplete="off" autocapitalize="characters">
            </div>
            <div class="fr-input-group">
                <label class="fr-label" for="campus">Campus</label>
                <select class="fr-select" id="campus" name="campus">
                    <option value="BREST" {{ 'selected' if p.campus == 'BREST' else '' }}>BREST</option>
                    <option value="RENNES" {{ 'selected' if p.campus == 'RENNES' else '' }}>RENNES</option>
                </select>
            </div>
            <div class="fr-input-group">
                <label class="fr-label" for="ue_table">Table des UE
                    <span class="fr-hint-text">Une par ligne, "Nom = CODE" — sert à la table de référence et
                    au remplissage automatique du CODE UE quand le titre d'un cours correspond (optionnel).</span>
                </label>
                <textarea class="fr-input" id="ue_table" name="ue_table" rows=6
                    placeholder="DevOps = DEVOPS&#10;Protocols for the Transport of Information = PTRANSINF">{{ p.ue_table }}</textarea>
            </div>
            <div class="fr-toggle fr-mb-3w">
                <input type="checkbox" class="fr-toggle__input" id="show_total_hours" name="show_total_hours" value="1"
                    {{ 'checked' if p.show_total_hours else '' }}>
                <label class="fr-toggle__label" for="show_total_hours"
                    data-fr-checked-label="Rempli" data-fr-unchecked-label="Vide">Remplir le total des heures de formation sur le PDF</label>
            </div>
            <div class="fr-toggle fr-mb-3w">
                <input type="checkbox" class="fr-toggle__input" id="show_teacher_names" name="show_teacher_names" value="1"
                    {{ 'checked' if p.show_teacher_names else '' }}>
                <label class="fr-toggle__label" for="show_teacher_names"
                    data-fr-checked-label="Rempli" data-fr-unchecked-label="Vide">Remplir le nom des intervenants sur le PDF</label>
            </div>
            <ul class="fr-btns-group fr-btns-group--inline-md">
                <li><button type=submit class="fr-btn">Enregistrer</button></li>
            </ul>
        </form>
        """,
        p=p,
        csrf_token=generate_csrf(),
    )


@app.route("/profile/import")
def profile_import():
    s = current_pass_session()
    if not s:
        return redirect(url_for("login"))
    username = session["username"]
    db = get_db()

    try:
        dossier = ps.fetch_dossier(s, username=username)
    except Exception as e:
        session["_flash"] = f"Import PASS échoué : {e}"
        return redirect(url_for("profile"))

    row = db.execute("SELECT * FROM profiles WHERE owner_username=?", (username,)).fetchone()
    existing = dict(row) if row else {"campus": "", "apprentissage": 1, "ue_table": ""}
    merged = {**existing, **{k: v for k, v in dossier.items() if v}}

    db.execute(
        # show_total_hours spelled out: a table migrated from an older schema still carries
        # that column's former DEFAULT 1 (SQLite can't alter a column default in place).
        "INSERT INTO profiles (owner_username, nom, prenom, formation, taf, campus, apprentissage, ue_table, "
        "show_total_hours) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(owner_username) DO UPDATE SET nom=excluded.nom, prenom=excluded.prenom, "
        "formation=excluded.formation, taf=excluded.taf",
        (username, merged.get("nom", ""), merged.get("prenom", ""), merged.get("formation", ""),
         merged.get("taf", ""), existing.get("campus", ""), existing.get("apprentissage", 1),
         existing.get("ue_table", ""), existing.get("show_total_hours", 0)),
    )
    db.commit()
    session["_flash"] = "NOM / PRENOM / FORMATION / TAF importés depuis PASS."
    return redirect(url_for("profile"))


@app.route("/pdf")
def export_pdf():
    s = current_pass_session()
    if not s:
        return redirect(url_for("login"))
    username = session["username"]
    db = get_db()

    row = db.execute("SELECT * FROM profiles WHERE owner_username=?", (username,)).fetchone()
    if not row:
        session["_flash"] = "Remplissez d'abord votre profil."
        return redirect(url_for("profile"))
    profile_dict = dict(row)

    week_offset, week_ref = _week_ref()
    try:
        events = fetch_events_cached(s, username, week_ref=week_ref)
    except RuntimeError:
        return _expire_pass_session()
    if not week_fetched(username, week_ref):
        session["_flash"] = ("Cette semaine n'a pas encore été chargée depuis PASS — "
                             "cliquez sur « Actualiser depuis PASS ».")
        return redirect(url_for("lessons", week=week_offset))
    # merge_contiguous_lessons/_time_bounds below assume "HHHMM-HHHMM" and crash on
    # anything else — /lessons tolerates a malformed time fine (its helpers are
    # try/except'd), so this filtering stays local to the PDF export instead of
    # dropping the event upstream where it would also vanish from /lessons.
    fetched_count = len(events)
    events = [e for e in events if ps.TIME_RE.match(e["time"])]

    if not events:
        # Distinguish a genuinely empty week from "PASS returned events but none carried a
        # usable HHHMM-HHHMM range" (agenda served in a grid view, see ps.AGENDA_VIEW) —
        # both used to surface as the same misleading "Aucun cours cette semaine."
        session["_flash"] = "Aucun cours cette semaine." if not fetched_count else (
            f"{fetched_count} cours récupérés depuis PASS, mais aucun n'a d'horaire exploitable — "
            "feuille d'émargement impossible à générer. Dans l'agenda PASS, ouvrez « Préférences », "
            "cochez toutes les cases de « Détails affichés », validez, puis cliquez sur « Actualiser depuis PASS »."
        )
        return redirect(url_for("lessons", week=week_offset))

    # Week header from every timed lesson, so excluding e.g. Monday's only session doesn't
    # shift "Semaine du".
    dates = [datetime.datetime.strptime(e["date"], "%Y%m%d") for e in events]
    week_start = min(dates).strftime("%d/%m/%Y")
    week_end = max(dates).strftime("%d/%m/%Y")
    week_number = min(dates).isocalendar()[1]

    excluded_ids, excluded_titles = _exclusions(db, username)
    events = [e for e in events if e["id"] not in excluded_ids and e["title"] not in excluded_titles]
    if not events:
        session["_flash"] = "Toutes les séances de cette semaine sont exclues du PDF."
        return redirect(url_for("lessons", week=week_offset))

    ue_table = []
    for line in profile_dict.get("ue_table", "").splitlines():
        if "=" in line:
            name, code = line.split("=", 1)
            ue_table.append((name.strip(), code.strip()))

    lessons_for_pdf = []
    total_hours = 0.0
    for group in merge_contiguous_lessons(events):
        first, last = group[0], group[-1]
        d = datetime.datetime.strptime(first["date"], "%Y%m%d")

        duration_minutes, pause_minutes, prev_end = 0, 0, None
        for ev in group:
            s, e_ = _time_bounds(ev["time"])
            duration_minutes += e_ - s
            if prev_end is not None:
                pause_minutes += s - prev_end
            prev_end = e_
        duration_hours = duration_minutes / 60
        total_hours += duration_hours
        start_min, _ = _time_bounds(first["time"])
        _, end_min = _time_bounds(last["time"])

        code_ue = ""
        for name, code in ue_table:
            if name and any(name in ev["title"] or name in ev["details"] for ev in group):
                code_ue = code
                break

        teacher_name = (" / ".join(short_teacher_name(n) for n in teacher_names(group))
                        if profile_dict.get("show_teacher_names", 1) else "")

        lessons_for_pdf.append({
            "date_label": d.strftime('%d/%m/%Y'),
            "start": f"{start_min // 60:02d}:{start_min % 60:02d}",
            "end": f"{end_min // 60:02d}:{end_min % 60:02d}",
            "pause_min": pause_minutes,
            "duration_hours": duration_hours,
            "title": first["title"],
            "code_ue": code_ue,
            "teacher_name": teacher_name,
        })

    pdf_bytes = pdf_export.build_pdf(
        profile_dict, week_start, week_end, str(week_number), lessons_for_pdf,
        ue_table=ue_table,
        total_hours=pdf_export.format_hours(total_hours) if total_hours else "",
        show_total_hours=bool(profile_dict.get("show_total_hours", 0)),
    )
    filename = f"emargement_{profile_dict['nom']}_{min(dates).strftime('%Y%m%d')}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
