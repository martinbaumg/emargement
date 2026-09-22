"""Two pages that have nothing to do with signing an attendance sheet.

/badges — the student's own wall of badges, computed from the weeks already cached
          (see badges.py). No PASS call, no new data collected.
/kpi    — the audience dashboard the footer's « visiteurs cette semaine » opens. Every
          figure on it is a real count (see analytics.py); the tone is the joke, not the
          numbers. Open without logging in, since the footer link is on the login page too.
"""
from flask import redirect, request, session, url_for

import analytics
import badges as badges_mod
from app import app
from csrf import check_csrf, generate_csrf
from db import get_db
from layout import render


def _fr_number(value) -> str:
    """12345 -> "12 345", 1.5 -> "1,5" — French typography, and the dashboard is nothing
    but numbers. Registered as a Jinja filter below."""
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")
    return f"{value:,}".replace(",", " ")


app.jinja_env.filters["fr"] = _fr_number


BADGES_TEMPLATE = """
<h1 class="fr-h3">Palmarès</h1>
<p class="fr-mb-3w">Ce que votre emploi du temps dit de vous, en {{ total }} distinctions.
Tout est calculé sur les semaines déjà chargées depuis PASS — rien n'est envoyé nulle part.</p>

<div class="fr-callout fr-mb-4w">
    <p class="fr-callout__title fr-h5 fr-mb-1w">{{ rank }}</p>
    <p class="fr-callout__text">{{ rank_line }}</p>
    <p class="att-badges-score">{{ earned }}<span class="att-badges-score__total"> / {{ total }}</span></p>
    <div class="att-meter fr-mt-1w" role="img" aria-label="{{ earned }} badges sur {{ total }}">
        <div class="att-meter__fill" style="width: {{ (100 * earned / total)|round }}%"></div>
    </div>
    {% if next_up %}
    <p class="fr-text--sm fr-mt-2w fr-mb-0">Le plus proche : <strong>{{ next_up.title }}</strong>
    ({{ next_up.progress_label }}).</p>
    {% endif %}
    <p class="fr-text--sm fr-mt-1w fr-mb-0">
        {% if featured %}Affiché sur votre profil : <strong>{{ featured.title }}</strong>.
        {% else %}Aucun badge affiché sur votre profil — choisissez-en un ci-dessous.{% endif %}
    </p>
    <p class="fr-text--sm fr-mt-1w fr-mb-0">
        {% if pop.enough %}
        Vos {{ pop.my_score }} badges classants vous placent
        <strong>{{ '1ᵉʳ' if pop.rank == 1 else pop.rank ~ 'ᵉ' }} sur {{ pop.participants }}</strong>,
        dans le top {{ pop.percentile }} %.
        {% else %}
        <span class="att-details">Le classement s'affiche à partir de {{ pop.min_participants }} participants
        ({{ pop.participants }} pour l'instant).</span>
        {% endif %}
    </p>
</div>

<div class="att-kpi-grid fr-mb-4w">
    <div class="att-kpi">
        <p class="att-kpi__label">Heures cumulées</p>
        <p class="att-kpi__value">{{ total_hours }}</p>
        <p class="att-kpi__hint">sur {{ lesson_count|fr }} séance(s)</p>
    </div>
    <div class="att-kpi">
        <p class="att-kpi__label">Semaines chargées</p>
        <p class="att-kpi__value">{{ weeks_loaded|fr }}</p>
        <p class="att-kpi__hint">depuis PASS</p>
    </div>
    <div class="att-kpi">
        <p class="att-kpi__label">Séances à 8h</p>
        <p class="att-kpi__value">{{ facts.early_starts|fr }}</p>
        <p class="att-kpi__hint">et vous y étiez</p>
    </div>
    <div class="att-kpi">
        <p class="att-kpi__label">Intervenant le plus vu</p>
        <p class="att-kpi__value att-kpi__value--text">{{ top_teacher or "—" }}</p>
        <p class="att-kpi__hint">{{ top_teacher_hours if top_teacher else "actualisez depuis PASS" }}</p>
    </div>
</div>

<h2 class="fr-h5">Vos badges</h2>
<ul class="fr-raw-list att-badges">
    {% for b in badges %}
    <li class="att-badge {{ 'att-badge--featured' if b.featured and b.earned }}{{ '' if b.earned else 'att-badge--locked' }}">
        <p class="att-badge__head">
            <span class="{{ b.icon }} att-badge__icon" aria-hidden="true"></span>
            <span class="att-badge__title">{{ b.title }}</span>
        </p>
        {% if b.earned %}
        <p class="fr-mb-1w">
            <span class="fr-badge fr-badge--sm fr-badge--success">Obtenu</span>
            {% if b.featured %}<span class="fr-badge fr-badge--sm fr-badge--info">Sur le profil</span>{% endif %}
        </p>
        <p class="att-badge__flavour">{{ b.flavour }}</p>
        {% else %}
        <p class="fr-badge fr-badge--sm fr-mb-1w">À décrocher</p>
        <p class="att-badge__rule">{{ b.rule }}</p>
        <div class="att-meter fr-mt-1w" role="img" aria-label="Progression : {{ b.progress_label }}">
            <div class="att-meter__fill" style="width: {{ b.progress }}%"></div>
        </div>
        <p class="att-badge__progress">{{ b.progress_label }}</p>
        {% endif %}
        {% if b.rarity %}
        <p class="att-badge__rarity">
            <span class="att-tier {{ b.rarity.class }}">{{ b.rarity.tier }}</span>
            {% if b.rarity.pct %}· {{ b.rarity.pct }} % des participants l'ont
            {% else %}· personne ne l'a encore décroché{% endif %}
        </p>
        {% endif %}
        {% if b.earned %}
        {# One badge at a time: pinning another one simply replaces this one, so the only
           action here is "show it" — or "remove it" on the one currently pinned. #}
        <form method=post action="{{ url_for('badge_featured') }}" class="att-badge__action">
            <input type=hidden name="csrf_token" value="{{ csrf_token }}">
            <input type=hidden name="badge_id" value="{{ '' if b.featured else b.id }}">
            {% if b.featured %}
            <button type=submit class="fr-btn fr-btn--sm fr-btn--tertiary fr-btn--icon-left fr-icon-close-line"
                title="Ne plus afficher « {{ b.title }} » sur le profil">Retirer du profil</button>
            {% else %}
            <button type=submit class="fr-btn fr-btn--sm fr-btn--secondary fr-btn--icon-left fr-icon-user-star-line"
                title="Afficher « {{ b.title }} » sur le profil">Afficher sur le profil</button>
            {% endif %}
        </form>
        {% endif %}
    </li>
    {% endfor %}
</ul>
<h2 class="fr-h5 fr-mt-5w">Classement par TAF</h2>
{% if pop.taf_ranking %}
<p class="fr-text--sm att-details fr-mb-2w">Moyenne de badges par participant, TAF par TAF.
Les badges qui mesurent l'usage de l'application plutôt que vos semaines de cours
(«&nbsp;Explorateur de semaines&nbsp;», «&nbsp;Lanceur d'alerte&nbsp;») ne comptent pas ici.</p>
<ol class="fr-raw-list att-taf {{ 'att-taf--alone' if pop.taf_ranking|length == 1 }}">
    {% for g in pop.taf_ranking %}
    <li class="att-taf__row {{ 'att-taf__row--mine' if g.mine }}">
        <span class="att-taf__rank att-taf__rank--{{ g.position if g.position <= 3 else 'n' }}"
            aria-hidden="true">{{ g.position }}</span>
        <span class="att-taf__name">
            <span class="fr-sr-only">{{ g.position }}. </span>{{ g.taf }}
            {% if g.mine %}<span class="fr-badge fr-badge--sm fr-badge--info">votre TAF</span>{% endif %}
            <span class="att-taf__count">{{ g.participants }} participant{{ 's' if g.participants > 1 else '' }}</span>
        </span>
        {% if pop.taf_ranking|length > 1 %}
        <span class="att-meter att-taf__bar" role="img"
            aria-label="{{ g.average }} badges en moyenne"><span class="att-meter__fill" style="width: {{ g.share }}%"></span></span>
        {% endif %}
        <span class="att-taf__value">{{ g.average|fr }}<span class="att-taf__unit"> badges</span></span>
    </li>
    {% endfor %}
</ol>
{% else %}
<div class="fr-callout fr-mb-3w">
    <p class="fr-callout__text fr-text--sm">Aucun participant n'a encore renseigné son TAF.
    Le classement apparaîtra de lui-même dès qu'un profil en portera un —
    <a class="fr-link" href="{{ url_for('profile') }}">le vôtre, par exemple</a>.</p>
</div>
{% endif %}

<h2 class="fr-h5 fr-mt-5w">Ce que voient les autres</h2>
<p class="fr-text--sm att-details">Rien de nominatif. Cette page n'affiche que des pourcentages, des
moyennes de groupe et votre propre position ; aucun nom, aucun pseudonyme, aucune liste de personnes,
et jamais qui détient quel badge. Sont comptés comme participants les comptes ayant ouvert le
palmarès au moins une fois ({{ pop.participants }} aujourd'hui). Les pourcentages de rareté
n'apparaissent qu'à partir de {{ pop.min_participants }} participants : en dessous, un pourcentage
désigne les personnes qu'il décrit. Un TAF, lui, s'affiche dès son premier participant — une moyenne
de groupe ne dit quelque chose de quelqu'un que si l'on sait qui compose le groupe, et cette
composition n'est publiée nulle part. L'administrateur du service, lui, a la base de données :
l'anonymat vaut entre étudiants, pas vis-à-vis de l'exploitant.</p>

<p class="fr-text--sm att-details fr-mt-3w">Un badge ne compte que ce que l'application a vu :
chargez d'autres semaines sur la page «&nbsp;Cours&nbsp;» et le palmarès se remplit tout seul.
Les salles sont reconnues à leur code PASS (BR-B02-017A) ; les amphis appelés par leur nom ne
comptent pas, ce qui est une injustice assumée.</p>
"""


@app.route("/badges")
def badges():
    username = session.get("username")
    if not username:
        return redirect(url_for("login"))
    return render(BADGES_TEMPLATE, csrf_token=generate_csrf(), **badges_mod.wall(get_db(), username))


@app.route("/badges/featured", methods=["POST"])
def badge_featured():
    """Pins the posted badge to the profile, or unpins the current one when badge_id is empty."""
    username = session.get("username")
    if not username:
        return redirect(url_for("login"))
    if not check_csrf():
        session["_flash"] = "Session expirée, réessayez."
        return redirect(url_for("badges"))
    badge_id = request.form.get("badge_id", "").strip()
    badge = badges_mod.set_featured(get_db(), username, badge_id)
    if badge:
        session["_flash"] = f"« {badge['title']} » est affiché sur votre profil."
    elif badge_id:
        session["_flash"] = "Ce badge n'est pas (encore) obtenu."
    else:
        session["_flash"] = "Badge retiré de votre profil."
    return redirect(url_for("badges"))


def _spark(daily: list, top: int) -> tuple:
    """(line, area) SVG paths for the 30-day trend, drawn in a 100x30 box the browser then
    stretches to the page width (preserveAspectRatio="none"). The stroke is kept at 2px by
    vector-effect, so the only thing that stretches is the geometry — which is exactly what
    a time axis should do, and why the marks carry no end-dot (a circle would stretch too;
    the last value is written beside the chart instead)."""
    n = len(daily)
    if n < 2:
        return "", ""
    points = []
    for i, day in enumerate(daily):
        x = i * 100 / (n - 1)
        y = 29 - (day["views"] / top) * 27
        points.append(f"{x:.2f},{y:.2f}")
    line = "M " + " L ".join(points)
    return line, f"{line} L 100,30 L 0,30 Z"


def _axis_dates(daily: list) -> list:
    """Five date labels for under the curve, each placed at the exact x of its own point
    (the SVG maps point i to i/(n-1) of the width) rather than spread evenly — a label that
    doesn't sit over the day it names is worse than no label at all."""
    n = len(daily)
    if n < 2:
        return []
    marks = sorted({0, n // 4, n // 2, 3 * n // 4, n - 1})
    return [{"label": daily[i]["label"], "pct": round(i * 100 / (n - 1), 2)} for i in marks]


KPI_TEMPLATE = """
<div class="att-kpi-head fr-mb-1w">
    <h1 class="fr-h3 fr-mb-0">Tableau de bord d'audience</h1>
    <p class="fr-badge fr-badge--sm fr-badge--info fr-mb-0">Diffusion restreinte</p>
</div>
<p class="fr-text--sm att-details fr-mb-4w">Pilotage de la fréquentation du service ·
données arrêtées au {{ k.generated_at }} · source unique de vérité · exercice {{ k.quarter }}.</p>

<div class="fr-callout fr-mb-4w">
    <p class="att-kpi__label fr-mb-0">Visiteurs uniques cette semaine</p>
    <p class="att-hero">{{ k.visitors_week|fr }}</p>
    <p class="fr-mb-0">
        {% if k.visitors_delta is none %}
        <span class="att-details">Pas de semaine précédente à laquelle se comparer. Le graphique ne fait que commencer.</span>
        {% elif k.visitors_delta >= 0 %}
        <span class="att-delta att-delta--up">▲ {{ k.visitors_delta|fr }} %</span>
        <span class="att-details">par rapport à la semaine dernière ({{ k.visitors_prev|fr }}). Dynamique confirmée.</span>
        {% else %}
        <span class="att-delta att-delta--down">▼ {{ (-k.visitors_delta)|fr }} %</span>
        <span class="att-details">par rapport à la semaine dernière ({{ k.visitors_prev|fr }}). Repli conjoncturel, sans gravité.</span>
        {% endif %}
    </p>
    <p class="fr-text--sm fr-mt-3w fr-mb-1w">Objectif {{ k.quarter }} : {{ k.target|fr }} visiteurs hebdomadaires
    — atteint à {{ k.target_progress }} %.</p>
    <div class="att-meter" role="img" aria-label="Objectif atteint à {{ k.target_progress }} %">
        <div class="att-meter__fill" style="width: {{ k.target_progress }}%"></div>
    </div>
</div>

{% if logged_in %}
{# The only way in since the menu entry was removed: the wall of badges is personal, this
   dashboard is about the site, and one leads to the other. #}
<ul class="fr-btns-group fr-btns-group--inline-md fr-btns-group--icon-left fr-mb-4w">
    <li><a class="fr-btn fr-btn--secondary fr-icon-award-line" href="{{ url_for('badges') }}">Voir mon palmarès</a></li>
</ul>
{% endif %}

<h2 class="fr-h5">1. Indicateurs clés</h2>
<div class="att-kpi-grid fr-mb-4w">
    <div class="att-kpi"><p class="att-kpi__label">Visiteurs aujourd'hui</p>
        <p class="att-kpi__value">{{ k.visitors_today|fr }}</p><p class="att-kpi__hint">dont vous</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Visiteurs (30 j)</p>
        <p class="att-kpi__value">{{ k.visitors_month|fr }}</p><p class="att-kpi__hint">fenêtre glissante</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Visiteurs depuis l'origine</p>
        <p class="att-kpi__value">{{ k.visitors_total|fr }}</p><p class="att-kpi__hint">navigateurs distincts</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Pages vues (7 j)</p>
        <p class="att-kpi__value">{{ k.views_week|fr }}</p><p class="att-kpi__hint">{{ k.views_today|fr }} aujourd'hui</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Pages vues cumulées</p>
        <p class="att-kpi__value">{{ k.views_total|fr }}</p><p class="att-kpi__hint">{{ k.views_per_day|fr }} par jour</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Pages par visiteur</p>
        <p class="att-kpi__value">{{ k.views_per_visitor|fr }}</p><p class="att-kpi__hint">profondeur de visite</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Taux de rebond</p>
        <p class="att-kpi__value">{{ k.bounce_rate }} %</p><p class="att-kpi__hint">une page et puis s'en vont</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Taux de fidélisation</p>
        <p class="att-kpi__value">{{ k.returning_rate }} %</p><p class="att-kpi__hint">revenus un autre jour</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Nouveaux cette semaine</p>
        <p class="att-kpi__value">{{ k.newcomers|fr }}</p><p class="att-kpi__hint">première visite</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Ancienneté du service</p>
        <p class="att-kpi__value">{{ k.days_online|fr }} j</p><p class="att-kpi__hint">depuis le {{ k.since_label }}</p></div>
</div>

<h2 class="fr-h5">2. Acquisition</h2>
<figure class="att-figure fr-mb-4w" role="group">
    <figcaption class="att-figure__caption">Pages vues par jour, 30 derniers jours.
    Dernier point : <strong>{{ k.daily[-1].views|fr }}</strong> le {{ k.daily[-1].label }}.</figcaption>
    {# Single quotes: |tojson escapes < > & and the apostrophe, never the double quote,
       so a double-quoted attribute would be cut short by the first key. #}
    <div class="att-chart" id="att-chart" data-points='{{ k.daily|tojson }}'>
        <svg viewBox="0 0 100 30" preserveAspectRatio="none" role="img"
             aria-label="Courbe des pages vues sur 30 jours, du {{ k.daily[0].label }} au {{ k.daily[-1].label }}">
            <path d="{{ spark_area }}" fill="currentColor" fill-opacity="0.1"></path>
            <path d="{{ spark_line }}" fill="none" stroke="currentColor" stroke-width="2"
                  stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
        </svg>
        <div class="att-chart__cursor" hidden></div>
        <div class="att-chart__tip" role="status" hidden></div>
    </div>
    <p class="att-axis att-axis--points" aria-hidden="true">
        {% for a in axis_dates %}<span style="left: {{ a.pct }}%">{{ a.label }}</span>{% endfor %}
    </p>
</figure>
<div class="att-kpi-grid fr-mb-4w">
    <div class="att-kpi"><p class="att-kpi__label">Moyenne quotidienne</p>
        <p class="att-kpi__value">{{ k.daily_mean|fr }}</p><p class="att-kpi__hint">visiteurs / jour (30 j)</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Écart-type</p>
        <p class="att-kpi__value">{{ k.daily_stdev|fr }}</p><p class="att-kpi__hint">volatilité maîtrisée</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Meilleure journée</p>
        <p class="att-kpi__value">{{ k.best_day.views|fr }}</p><p class="att-kpi__hint">le {{ k.best_day.label }}</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Journées sans visite</p>
        <p class="att-kpi__value">{{ k.quiet_days|fr }}</p><p class="att-kpi__hint">sur 30 — temps de réflexion</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Jour de pointe</p>
        <p class="att-kpi__value att-kpi__value--text">{{ k.peak_weekday }}</p><p class="att-kpi__hint">historiquement</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Heure de pointe</p>
        <p class="att-kpi__value">{{ "%02d"|format(k.peak_hour.hour) }}h</p>
        <p class="att-kpi__hint">{{ k.peak_hour.views|fr }} pages vues</p></div>
</div>

<figure class="att-figure fr-mb-4w" role="group">
    <figcaption class="att-figure__caption">Répartition des pages vues par heure de la journée.</figcaption>
    <ul class="fr-raw-list att-bars">
        {% for b in k.by_hour %}
        {# An hour with no traffic gets a hairline in the grid colour, not a short blue
           stub: a 2px mark in the data colour reads as "a little", which is a lie. #}
        <li class="att-bars__col">
            <span class="att-bar {{ 'att-bar--empty' if not b.views }}" role="img"
                  style="height: {{ (100 * b.views / k.hour_max)|round(1) }}%"
                  aria-label="{{ "%02d"|format(b.hour) }}h : {{ b.views }} pages vues">
                <span class="att-bar__value">{{ b.views|fr }}</span>
            </span>
        </li>
        {% endfor %}
    </ul>
    <ul class="fr-raw-list att-axis att-axis--hours" aria-hidden="true">
        {% for b in k.by_hour %}<li>{% if b.hour % 6 == 0 %}{{ "%02d"|format(b.hour) }}h{% endif %}</li>{% endfor %}
    </ul>
</figure>

<h2 class="fr-h5">3. Engagement par page</h2>
<div class="fr-table fr-table--bordered fr-mb-4w">
<table>
    <caption class="fr-sr-only">Pages les plus consultées</caption>
    <thead><tr><th>Page</th><th>Pages vues</th><th>Part</th></tr></thead>
    <tbody>
    {% for p in k.top_pages %}
    <tr>
        <td>{{ p.label }}</td>
        <td class="att-num">{{ p.views|fr }}</td>
        <td>
            <div class="att-meter att-meter--sm"><div class="att-meter__fill" style="width: {{ p.share }}%"></div></div>
            <span class="fr-text--sm att-details">{{ p.share|fr }} %</span>
        </td>
    </tr>
    {% endfor %}
    </tbody>
</table>
</div>

<h2 class="fr-h5">4. Production et chaîne de valeur</h2>
<div class="att-kpi-grid fr-mb-4w">
    <div class="att-kpi"><p class="att-kpi__label">Feuilles produites</p>
        <p class="att-kpi__value">{{ k.pdfs|fr }}</p><p class="att-kpi__hint">PDF téléchargés</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Temps humain libéré</p>
        <p class="att-kpi__value">{{ k.time_saved }}</p><p class="att-kpi__hint">à 12 min la feuille, méthode maison</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Feuilles A4 mobilisées</p>
        <p class="att-kpi__value">{{ k.paper|fr }}</p><p class="att-kpi__hint">soit {{ k.trees|fr }} arbre</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Séances en mémoire</p>
        <p class="att-kpi__value">{{ k.cached_lessons|fr }}</p><p class="att-kpi__hint">{{ k.distinct_titles|fr }} intitulés distincts</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Comptes ouverts</p>
        <p class="att-kpi__value">{{ k.accounts|fr }}</p><p class="att-kpi__hint">profils renseignés</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Sollicitations de PASS</p>
        <p class="att-kpi__value">{{ k.refreshes|fr }}</p><p class="att-kpi__hint">semaines actualisées</p></div>
</div>

<h2 class="fr-h5">5. Qualité de service</h2>
<div class="att-kpi-grid fr-mb-4w">
    <div class="att-kpi"><p class="att-kpi__label">Taux de succès de connexion</p>
        <p class="att-kpi__value">{{ k.login_rate }} %</p>
        <p class="att-kpi__hint">{{ k.logins_ok|fr }} réussies, {{ k.logins_ko|fr }} contrariées</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Disponibilité revendiquée</p>
        <p class="att-kpi__value">99,9 %</p><p class="att-kpi__hint">méthode de mesure : la confiance</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Tickets d'assistance</p>
        <p class="att-kpi__value">{{ k.tickets_total|fr }}</p><p class="att-kpi__hint">{{ k.tickets_open|fr }} en cours</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Délai moyen de réponse</p>
        <p class="att-kpi__value">{% if k.reply_hours is none %}—{% else %}{{ k.reply_hours|fr }} h{% endif %}</p>
        <p class="att-kpi__hint">engagement contractuel : aucun</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Résolution de l'assistant</p>
        <p class="att-kpi__value">0,0 %</p><p class="att-kpi__hint">conforme à la spécification</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Pages introuvables</p>
        <p class="att-kpi__value">{{ k.not_found|fr }}</p><p class="att-kpi__hint">erreurs 404 servies</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Robots éconduits</p>
        <p class="att-kpi__value">{{ k.bots_week|fr }}</p><p class="att-kpi__hint">cette semaine, non comptés</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Conformité au DSFR</p>
        <p class="att-kpi__value">100 %</p><p class="att-kpi__hint">auto-évaluation</p></div>
</div>

<h2 class="fr-h5">6. Projection et création de valeur</h2>
<div class="att-kpi-grid fr-mb-4w">
    <div class="att-kpi"><p class="att-kpi__label">Tendance</p>
        <p class="att-kpi__value">{{ k.slope|fr }}</p><p class="att-kpi__hint">visiteur / jour (moindres carrés)</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Qualité de l'ajustement</p>
        <p class="att-kpi__value">R² = {{ k.r2|fr }}</p><p class="att-kpi__hint">intervalle de confiance : aucun</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Projection à 5 ans</p>
        <p class="att-kpi__value">{{ k.projection|fr }}</p><p class="att-kpi__hint">visiteurs / jour, en prolongeant la droite</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Trafic annuel projeté</p>
        <p class="att-kpi__value">{{ k.projection_year|fr }}</p><p class="att-kpi__hint">à horizon 2031, sous réserve de tout</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Valorisation implicite</p>
        <p class="att-kpi__value">{{ k.valuation|fr }} €</p><p class="att-kpi__hint">1 000 € le visiteur, au doigt mouillé</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Coût par visiteur</p>
        <p class="att-kpi__value">0,00 €</p><p class="att-kpi__hint">marge brute : 100 %</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Pénétration du campus</p>
        <p class="att-kpi__value">{{ k.campus_share|fr }} %</p><p class="att-kpi__hint">base 1 200 étudiants</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Lignes de code</p>
        <p class="att-kpi__value">{{ k.lines_of_code|fr }}</p><p class="att-kpi__hint">{{ k.loc_per_visitor|fr }} par visiteur</p></div>
    <div class="att-kpi"><p class="att-kpi__label">Réunions de pilotage</p>
        <p class="att-kpi__value">0</p><p class="att-kpi__hint">économie nette : 0 × 1 h 30</p></div>
</div>

<h2 class="fr-h5">7. Méthodologie et gouvernance</h2>
<div class="fr-callout fr-mb-2w">
    <p class="fr-text--sm fr-mb-2w">Sans ironie, sur ce qui est mesuré : chaque page servie ajoute
    une ligne contenant l'heure, la page, le code de réponse et un identifiant aléatoire tiré pour
    votre navigateur, gardé dans son propre cookie de session. <strong>Ni adresse IP, ni navigateur,
    ni identifiant PASS</strong> : ce tableau de bord sait combien de navigateurs sont passés, jamais qui.
    Les lignes de plus de {{ retention }} jours sont supprimées, et les robots sont comptés à part
    puisqu'ils ne gardent pas de cookie.</p>
    <p class="fr-text--sm fr-mb-0 att-details">Comité de pilotage : 1 personne. Prochaine revue de
    performance : jamais. Niveau de service : meilleur effort. Feuille de route : ouverte à qui la
    demandera. Les chiffres ci-dessus sont exacts ; leur interprétation n'engage personne.</p>
</div>
<details class="fr-mb-4w">
    <summary class="fr-text--sm">Voir les données de la courbe</summary>
    <div class="fr-table fr-table--bordered fr-mt-1w">
    <table>
        <caption class="fr-sr-only">Pages vues et visiteurs par jour sur 30 jours</caption>
        <thead><tr><th>Jour</th><th>Pages vues</th><th>Visiteurs</th></tr></thead>
        <tbody>
        {% for d in k.daily %}
        <tr><td>{{ d.label }}</td><td class="att-num">{{ d.views|fr }}</td><td class="att-num">{{ d.visitors|fr }}</td></tr>
        {% endfor %}
        </tbody>
    </table>
    </div>
</details>
<script>
(function () {
    // Hover layer for the 30-day curve: the SVG is stretched horizontally, so the x of a
    // point is a plain fraction of the container width — no scale object needed. Touch and
    // keyboard get the table above instead, which is why it ships with the chart.
    var chart = document.getElementById("att-chart");
    if (!chart) return;
    var points = JSON.parse(chart.dataset.points || "[]");
    var cursor = chart.querySelector(".att-chart__cursor");
    var tip = chart.querySelector(".att-chart__tip");
    if (!points.length) return;

    chart.addEventListener("mousemove", function (ev) {
        var box = chart.getBoundingClientRect();
        var ratio = Math.min(1, Math.max(0, (ev.clientX - box.left) / box.width));
        var i = Math.round(ratio * (points.length - 1));
        var p = points[i];
        cursor.hidden = false;
        cursor.style.left = (i * 100 / (points.length - 1)) + "%";
        tip.hidden = false;
        tip.textContent = p.label + " · " + p.views + " page(s) vue(s) · " + p.visitors + " visiteur(s)";
        // Flip the label to the left half once it would run off the right edge.
        tip.classList.toggle("att-chart__tip--end", ratio > 0.6);
    });
    chart.addEventListener("mouseleave", function () {
        cursor.hidden = true;
        tip.hidden = true;
    });
})();
</script>
"""


@app.route("/kpi")
def kpi():
    k = analytics.dashboard()
    spark_line, spark_area = _spark(k["daily"], k["daily_max"])
    return render(KPI_TEMPLATE, k=k, spark_line=spark_line, spark_area=spark_area,
                  axis_dates=_axis_dates(k["daily"]), logged_in=bool(session.get("username")),
                  retention=analytics.RETENTION_DAYS)
