"""Shared page chrome (DSFR layout), the render() wrapper, and the flash-message helper."""
import colorsys
import functools
import os
import re

from flask import render_template_string, session, url_for

import db as dbmod
from csrf import generate_csrf

# Vendored under static/dsfr/ (see attendance_app/static/dsfr) rather than pulled from
# jsdelivr's CDN at request time — a third-party CDN in the page's dependency chain is
# one more party that sees every request (IP, timing). Bump the vendored tree in
# static/dsfr/ to upgrade.
DSFR_VERSION = "1.15.3"
DSFR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "dsfr")

# « Je suis FIP » (profile switch): every DSFR blue becomes a shade of this pink.
FIP_PINK = "#F60975"
DSFR_MAIN_BLUE = "#000091"  # blue-france-sun-113, the one that lands exactly on FIP_PINK
_HEX_RE = re.compile(r"(#|%23)([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")  # %23: "#" inside SVG data URIs
# Lookahead before the optional quote: with the quote group first, it could match empty and
# let url("data:…") through as relative, breaking every inline SVG (e.g. toggle switches).
_RELATIVE_URL_RE = re.compile(r"url\((?![\"']?(?:data:|https?:|/))([\"']?)")


def _hls(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return colorsys.rgb_to_hls(*(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))


def _fip_color(hex_color: str) -> str | None:
    """A DSFR blue -> the pink at the same place on the lightness scale: the main blue lands
    exactly on FIP_PINK, lighter blues (hover, tints, dark-theme accents) on lighter pinks,
    darker ones on darker pinks. None for anything that isn't blue (greys, reds, greens…)."""
    hue, light, sat = _hls(hex_color)
    if not (195 <= hue * 360 <= 265 and sat > 0.25):
        return None
    if hex_color.lower() == DSFR_MAIN_BLUE:
        return FIP_PINK
    pink_hue, pink_light, pink_sat = _hls(FIP_PINK)
    _, base_light, _ = _hls(DSFR_MAIN_BLUE)
    if light <= base_light:
        new_light = light / base_light * pink_light
    else:
        new_light = pink_light + (light - base_light) / (1 - base_light) * (1 - pink_light)
    r, g, b = colorsys.hls_to_rgb(pink_hue, new_light, sat * pink_sat)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


@functools.lru_cache(maxsize=4)
def fip_dsfr_css(dsfr_base: str) -> str:
    """The vendored dsfr.min.css with every blue swapped (see _fip_color) — variables, both
    themes, literal colors and the blue fills of inline SVGs alike — built once per process,
    so it follows a DSFR upgrade by itself. It's served from another path than static/dsfr/,
    hence its relative url()s (fonts, icons) rewritten as absolute ones under dsfr_base."""
    with open(os.path.join(DSFR_DIR, "dsfr.min.css"), encoding="utf-8") as f:
        css = f.read()

    def swap(m):
        new = _fip_color("#" + m.group(2))
        return m.group(1) + new[1:] if new else m.group(0)

    css = _HEX_RE.sub(swap, css)
    return _RELATIVE_URL_RE.sub(lambda m: f"url({m.group(1)}{dsfr_base}/", css)

LAYOUT = """
<!doctype html>
<html lang="fr" data-fr-scheme="system">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="format-detection" content="telephone=no">
<title>Émargement</title>
<link rel="apple-touch-icon" href='{{ dsfr_base }}/favicon/apple-touch-icon.png'>
<link rel="icon" href='{{ dsfr_base }}/favicon/favicon.svg' type="image/svg+xml">
<link rel="shortcut icon" href='{{ dsfr_base }}/favicon/favicon.ico' type="image/x-icon">
<link rel="manifest" href='{{ dsfr_base }}/favicon/manifest.webmanifest' crossorigin="use-credentials">
<link rel="stylesheet" href='{{ dsfr_css }}'>
<link rel="stylesheet" href='{{ dsfr_base }}/utility/icons/icons.min.css'>
<script type="module" src='{{ dsfr_base }}/dsfr.module.min.js'></script>
<script type="text/javascript" nomodule src='{{ dsfr_base }}/dsfr.nomodule.min.js'></script>
<style>
html,body{height:100%}
body{display:flex;flex-direction:column;margin:0}
body>header,body>footer{flex:none}
body>.att-main{flex:1 0 auto}
.att-details{color:var(--text-mention-grey);font-size:.85em}
/* Header without logo: the service block takes the logo's place in brand-top, so on
   phones the burger sits on the title's row. DSFR draws a separator above the service
   (::before, meant to sit under a logo row) and gives the navbar flex:1 — both assume a logo. */
.att-brand-top{align-items:center}
.att-brand-top .fr-header__service{flex:1;min-width:0}
.att-brand-top .fr-header__service::before{content:none}
/* Burger vertically centered on the header bar (title + tagline), not stuck to the top. */
.att-brand-top .fr-header__navbar{flex:none;align-self:center;margin-top:0}
.att-inline-form{display:inline}

/* Week switcher: ‹ label › on one line at every width. */
.att-week-bar{display:flex;align-items:center;justify-content:space-between;gap:.75rem}
.att-week-bar>.fr-btn{flex:none}
.att-week-bar__label{flex:1;min-width:0;text-align:center}
/* Tablet: week stays centered full width, so center the action buttons under it. */
@media (min-width:48em) and (max-width:61.98em){
  .att-toolbar .fr-btns-group{justify-content:center}
}
/* Desktop: one toolbar row — week switcher (‹ label ›) on the left, actions on the right.
   Negative bottom margin cancels the 1rem DSFR puts under each .fr-btn, so both sides
   share the same vertical center. */
@media (min-width:62em){
  .att-toolbar{display:flex;align-items:center;justify-content:space-between;gap:2rem;margin-bottom:2rem}
  .att-toolbar .att-week-bar{flex:none;justify-content:flex-start;margin-bottom:0 !important}
  .att-toolbar .att-week-bar__label{flex:none}
  .att-toolbar .fr-btns-group{flex:none;flex-wrap:nowrap;justify-content:flex-end;margin-bottom:-1rem}
}

/* Desktop/tablet (md+): table. */
.att-lessons-table .att-time-col{width:8rem}
.att-lessons-table .att-pdf-col{width:15rem}
.att-lessons-table .att-rule-form .fr-btn{white-space:nowrap}
/* !important: DSFR's own zebra-striping rule (.fr-table>table tbody tr:nth-child(2n))
   outranks a plain class on specificity. */
tr.att-excluded{background-color:var(--background-alt-grey) !important}
tr.att-excluded td:not(.att-pdf-col){color:var(--text-mention-grey)}

/* Phones (below md): one card per session instead of a table wider than the screen. */
.att-lesson-list{margin:0;padding:0}
.att-lesson-card{border:1px solid var(--border-default-grey);background:var(--background-default-grey);padding:.75rem 1rem;margin:0 0 .75rem}
.att-lesson-card:last-child{margin-bottom:0}
.att-lesson-card.att-excluded{background:var(--background-alt-grey)}
.att-lesson-card.att-excluded .att-lesson-card__time,
.att-lesson-card.att-excluded .att-lesson-card__title{color:var(--text-mention-grey)}
.att-lesson-card__head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem}
.att-lesson-card__time{margin:0;padding-top:.25rem;font-weight:700}
.att-lesson-card__title{margin:.25rem 0 0;font-weight:500;overflow-wrap:anywhere}
.att-lesson-card__meta{margin:.25rem 0 0;font-size:.875rem;color:var(--text-mention-grey)}
.att-lesson-card .fr-toggle{flex:none;padding:0}

/* CODE UE badge under each course title (cards and table), and the week summary line. */
.att-ue{margin:.25rem 0 0;line-height:1}
.att-excluded .att-ue-missing{display:none}
/* « Sans code UE » is a button: a click turns it into this input, typed in place. */
.att-ue-edit{cursor:pointer;border:0}
.att-ue-edit:hover,.att-ue-edit:focus-visible{text-decoration:underline}
/* font-size 1rem (16px): below that, iOS Safari zooms the whole page when the field gets focus. */
.att-ue-input{display:inline-block;width:9rem;max-width:100%;margin:0;padding:.25rem .5rem;
  font-size:1rem;line-height:1.5rem;text-transform:uppercase}
.att-ue-input::placeholder{text-transform:none}
.att-ue-error{margin:.25rem 0 0;line-height:1.25rem}
.att-week-summary__total{color:var(--text-default-grey)}
.att-week-summary>span:not(:first-child)::before{content:" · "}
@media (max-width:47.98em){
  /* Phones: one fact per line reads better than a wrapped run of " · ". */
  .att-week-summary>span{display:block}
  .att-week-summary>span:not(:first-child)::before{content:none}
  .att-week-summary>span[hidden]{display:none}
}

/* First-login guide (#att-guide): steps as a compact numbered list, and the footer link
   that reopens it is a <button> dressed like its <a> neighbours. */
.att-guide-steps{margin:0;padding-left:1.25rem}
.att-guide-steps>li{margin-bottom:1rem}
.att-guide-steps>li:last-child{margin-bottom:0}
.att-guide-steps p{margin:.25rem 0 0}
.att-guide-steps .fr-highlight{margin:.5rem 0 0 0;padding-left:1rem}
button.att-guide-link{background:none;border:0;padding:0;cursor:pointer;font:inherit}

/* Long course titles in the "toujours exclus" tags wrap instead of overflowing. */
.att-rules .fr-tag{white-space:normal;text-align:left;height:auto;max-width:100%}

/* « Actualiser / Réimporter depuis PASS »: the DSFR icon (a ::before mask) spins while the
   several-second PASS round-trip is pending; repeat clicks are ignored meanwhile. */
@keyframes att-spin { to { transform: rotate(360deg); } }
.fr-btn.att-loading::before{animation:att-spin 1s linear infinite}
.fr-btn.att-loading{pointer-events:none}
/* …with a rotating tongue-in-cheek line right by the button meanwhile, in the same small grey
   type as the login steps. */
.att-quip{margin:0;font-size:.875rem;line-height:1.5rem;color:var(--text-mention-grey);transition:opacity .15s}
.att-quip--swap{opacity:0}
/* Standalone button (« Remplir depuis PASS »): on the same row, after it. */
.fr-btn + .att-quip{display:inline-block;margin-left:.75rem;vertical-align:middle}
/* In a button group (« Réimporter depuis PASS »): beside the button when the row has room,
   under it otherwise; bottom margin mirrors the button's so both stay centered. */
.fr-btns-group>li.att-quip-host{flex-wrap:wrap;align-items:center}
.fr-btns-group>li.att-quip-host>.att-quip{display:block;margin:0 .5rem 1rem}
/* Desktop lessons toolbar: « Actualiser » sits against the right edge, so no room beside it —
   the line hangs just under the button, right-aligned, out of the flow (no toolbar shift). */
@media (min-width:62em){
  .att-toolbar .fr-btns-group>li.att-quip-host{position:relative}
  .att-toolbar .fr-btns-group>li.att-quip-host>.att-quip{position:absolute;top:100%;right:0;margin:-.75rem .5rem 0;white-space:nowrap}
}

/* Login: PASS sign-in + first load of the week take a while — indeterminate bar + step text. */
.att-login-progress{margin-top:1.5rem}
.att-login-progress[hidden]{display:none}
.att-progress{position:relative;height:.25rem;overflow:hidden;border-radius:.125rem;background:var(--background-default-grey)}
.att-progress::before{content:"";position:absolute;top:0;bottom:0;left:0;width:40%;border-radius:.125rem;background:var(--background-action-high-blue-france);animation:att-progress 1.4s ease-in-out infinite}
@keyframes att-progress { from { transform: translateX(-100%); } to { transform: translateX(250%); } }
@media (prefers-reduced-motion:reduce){
  .att-progress::before{animation:none;width:100%;opacity:.6}
}

@media (max-width:47.98em){
  .att-login-btn{width:100%;justify-content:center}
  .fr-callout{padding:1.25rem}
  .att-main.fr-my-4w{margin-top:1.5rem !important}
  .fr-tabs__panel{padding-left:.75rem;padding-right:.75rem}
}
</style>
</head>
<body>
<header role="banner" class="fr-header">
  <div class="fr-header__body">
    <div class="fr-container">
      <div class="fr-header__body-row">
        <div class="fr-header__brand fr-enlarge-link">
          {# No logo: the service name sits in brand-top itself, so on phones the burger
             button lines up with the title instead of with an empty logo row. #}
          <div class="fr-header__brand-top att-brand-top">
            <div class="fr-header__service">
              <a href="{{ url_for('lessons') }}" title="Accueil — Émargement">
                <p class="fr-header__service-title">Émargement</p>
              </a>
              <p class="fr-header__service-tagline">Votre feuille d'émargement pré-remplie depuis PASS.</p>
            </div>
            {% if session.get('token') %}
            <div class="fr-header__navbar">
              <button class="fr-btn--menu fr-btn" data-fr-opened="false" aria-controls="modal-header-menu"
                aria-haspopup="menu" id="button-header-menu" title="Menu">Menu</button>
            </div>
            {% endif %}
          </div>
        </div>
        {% if session.get('token') %}
        <div class="fr-header__tools">
          <div class="fr-header__tools-links">
            <ul class="fr-btns-group">
              <li>
                <a class="fr-btn fr-icon-logout-box-r-line" href="{{ url_for('logout') }}"
                  title="Se déconnecter ({{ display_name }})">Se déconnecter</a>
              </li>
            </ul>
          </div>
        </div>
        {% endif %}
      </div>
    </div>
  </div>
  {% if session.get('token') %}
  <div class="fr-header__menu fr-modal" id="modal-header-menu" aria-labelledby="button-header-menu">
    <div class="fr-container">
      <button class="fr-btn--close fr-btn" aria-controls="modal-header-menu" title="Fermer">Fermer</button>
      <div class="fr-header__menu-links"></div>
      <nav class="fr-nav" id="header-nav" role="navigation" aria-label="Menu principal">
        <ul class="fr-nav__list">
          <li class="fr-nav__item">
            <a class="fr-nav__link" href="{{ url_for('lessons') }}"
              {% if request.endpoint == 'lessons' %}aria-current="page"{% endif %}>Cours</a>
          </li>
          <li class="fr-nav__item">
            <a class="fr-nav__link" href="{{ url_for('profile') }}"
              {% if request.endpoint == 'profile' %}aria-current="page"{% endif %}>Profil</a>
          </li>
        </ul>
      </nav>
    </div>
  </div>
  {% endif %}
</header>
<div class="att-main fr-container fr-my-4w">
{% with m = get_flashed_message() %}{% if m %}
<div class="fr-alert fr-alert--info fr-alert--sm fr-mb-3w"><p>{{ m }}</p></div>
{% endif %}{% endwith %}
{{ body|safe }}
</div>
{% if logged_in %}
{# DSFR modal: centered dialog on desktop, full-width sheet with its own scroll on phones.
   Opened by the footer « Guide d'utilisation » button; data-fr-opened="true" on it opens
   it at load for a student who has never seen it (show_guide). #}
<dialog id="att-guide" class="fr-modal" role="dialog" aria-labelledby="att-guide-title"
  {% if show_guide %}data-mark-seen="{{ url_for('guide_seen') }}" data-csrf="{{ csrf_token }}"{% endif %}>
  <div class="fr-container fr-container--fluid fr-container-md">
    <div class="fr-grid-row fr-grid-row--center">
      <div class="fr-col-12 fr-col-md-10 fr-col-lg-8">
        <div class="fr-modal__body">
          <div class="fr-modal__header">
            <button type="button" class="fr-btn--close fr-btn" title="Fermer le guide" aria-controls="att-guide">Fermer</button>
          </div>
          <div class="fr-modal__content">
            <h1 id="att-guide-title" class="fr-modal__title">Bienvenue sur Émargement</h1>
            <p>Votre feuille d'émargement se prépare en quatre étapes.</p>
            <ol class="att-guide-steps">
              <li>
                <strong>Complétez votre profil.</strong>
                <p>Nom, prénom, formation et TAF sont repris de PASS. Dans «&nbsp;Table des UE&nbsp;», cliquez sur
                «&nbsp;Remplir depuis PASS&nbsp;» : les codes UE de vos cours sont ajoutés automatiquement.</p>
              </li>
              <li>
                <strong>Vérifiez vos codes UE, puis enregistrez.</strong>
                <p class="fr-highlight">Ces codes sont déduits de PASS et peuvent être faux ou manquer :
                relisez chaque ligne, corrigez-la si besoin, puis cliquez sur «&nbsp;Enregistrer&nbsp;».</p>
              </li>
              <li>
                <strong>Choisissez les séances.</strong>
                <p>Sur la page «&nbsp;Cours&nbsp;», retirez du PDF les séances qui ne se signent pas (travail en autonomie…).
                Les séances sans code UE y sont signalées.</p>
              </li>
              <li>
                <strong>Téléchargez et faites signer.</strong>
                <p>«&nbsp;Télécharger la feuille d'émargement&nbsp;», imprimez-la et faites-la signer.
                Si votre emploi du temps change, cliquez sur «&nbsp;Actualiser depuis PASS&nbsp;».</p>
              </li>
            </ol>
          </div>
          <div class="fr-modal__footer">
            <ul class="fr-btns-group fr-btns-group--right fr-btns-group--inline-reverse fr-btns-group--inline-lg fr-btns-group--icon-left">
              <li><a class="fr-btn fr-icon-user-line" href="{{ url_for('profile') }}#ue_table">Compléter mon profil</a></li>
              <li><button type="button" class="fr-btn fr-btn--secondary" aria-controls="att-guide">J'ai compris</button></li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</dialog>
{% endif %}
<footer class="fr-footer" role="contentinfo">
  <div class="fr-container">
    <div class="fr-footer__body">
      <div class="fr-footer__content">
        <p class="fr-footer__content-desc">Outil personnel, non officiel — construit avec le
        <a class="fr-footer__content-link" href="https://www.systeme-de-design.gouv.fr/" target="_blank" rel="noopener noreferrer external">Système de Design de l'État</a>.</p>
        <ul class="fr-footer__content-list">
          <li class="fr-footer__content-item">
            <a class="fr-footer__content-link" href="https://github.com/martinbaumg/emargement" target="_blank" rel="noopener noreferrer external">Code source</a>
          </li>
          <li class="fr-footer__content-item">
            <a class="fr-footer__content-link att-contact-link" data-enc="Y29udGFjdEBiYXVtZ2FlcnRuZXIuZnI=" href="#">Contact</a>
          </li>
          {% if logged_in %}
          <li class="fr-footer__content-item">
            <button type="button" class="fr-footer__content-link att-guide-link" aria-controls="att-guide"
              data-fr-opened="{{ 'true' if show_guide else 'false' }}">Guide d'utilisation</button>
          </li>
          {% endif %}
        </ul>
      </div>
    </div>
  </div>
</footer>
<script>
(function() {
  // Built client-side from base64 rather than a plain mailto: — keeps the address off
  // scraper crawls of the raw HTML without any server-side moving parts.
  document.querySelectorAll(".att-contact-link").forEach(function(link) {
    var addr = atob(link.dataset.enc);
    link.href = "mailto:" + addr;
    link.textContent = addr;
  });

  // First-login guide: once it has actually been shown (DSFR's dsfr.disclose event), record
  // it so it stops opening by itself. Listener set here, before the DSFR module script (run
  // after parsing) opens it.
  var guide = document.getElementById("att-guide");
  if (guide && guide.dataset.markSeen) {
    guide.addEventListener("dsfr.disclose", function() {
      var body = new FormData();
      body.append("csrf_token", guide.dataset.csrf);
      fetch(guide.dataset.markSeen, {method: "POST", body: body, credentials: "same-origin"});
    }, {once: true});
  }

  // The PASS buttons (refresh icon) each start a request lasting several seconds: spin
  // until the next page replaces this one, and keep the wait light with a rotating quip.
  // The quip is decorative (aria-hidden) — the button's aria-busy already says it's loading,
  // and a line read aloud every 2.5 s would be noise.
  var QUIPS = [
    "C'est PASS qui rame, pas moi",
    "Je tape poliment à la porte de PASS…",
    "PASS cherche ses lunettes…",
    "Toujours pas moi, promis. C'est PASS.",
    "PASS réfléchit très fort…",
    "Pendant ce temps, PASS finit son café",
    "PASS trie ses fiches à la main…",
    "Moi je suis prêt depuis longtemps, hein.",
    "PASS arrive, à son rythme…",
    "Si ça traîne, vous savez à qui vous plaindre (pas à moi)."
  ];
  var quip = null;
  function startQuips(btn) {
    if (quip) return;
    var line = document.createElement("p");
    line.className = "att-quip";
    line.setAttribute("aria-hidden", "true");
    var order = QUIPS.slice(1).sort(function() { return Math.random() - 0.5; });
    order.unshift(QUIPS[0]);
    var i = 0;
    line.textContent = order[0];
    btn.insertAdjacentElement("afterend", line);
    var host = btn.parentElement.matches(".fr-btns-group > li") ? btn.parentElement : null;
    if (host) host.classList.add("att-quip-host");
    quip = {line: line, host: host, timer: setInterval(function() {
      i = (i + 1) % order.length;
      line.classList.add("att-quip--swap");
      setTimeout(function() { line.textContent = order[i]; line.classList.remove("att-quip--swap"); }, 150);
    }, 2500)};
  }
  // Why not simply follow the link: once a navigation is pending, browsers (Safari notably)
  // freeze the page being left — CSS animations go on, but timers stop, so the quip would
  // never change. The slow request runs in the background instead, the page staying live,
  // and only then do we go to data-att-then, now fast since the work is done server-side.
  // redirect:"manual": the page the server redirects to shows the flash message, so it must
  // be loaded by that real navigation, not consumed by fetch. Any failure: plain fallback.
  window.attRunThenGo = function(request, thenUrl, fallback) {
    request.then(function() { window.location.href = thenUrl; }, fallback);
  };
  document.querySelectorAll(".fr-btn.fr-icon-refresh-line").forEach(function(btn) {
    btn.addEventListener("click", function(ev) {
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;  // new tab: nothing to wait for here
      btn.classList.add("att-loading");
      btn.setAttribute("aria-busy", "true");
      startQuips(btn);
      if (btn.tagName === "A" && btn.dataset.attThen) {
        ev.preventDefault();
        window.attRunThenGo(fetch(btn.href, {credentials: "same-origin", redirect: "manual"}), btn.dataset.attThen,
                            function() { window.location.href = btn.href; });
      }
    });
  });
  // Back/forward cache restores the page exactly as left, i.e. still spinning.
  window.addEventListener("pageshow", function(ev) {
    if (!ev.persisted) return;
    document.querySelectorAll(".att-loading").forEach(function(btn) {
      btn.classList.remove("att-loading");
      btn.removeAttribute("aria-busy");
    });
    if (quip) {
      clearInterval(quip.timer);
      quip.line.remove();
      if (quip.host) quip.host.classList.remove("att-quip-host");
      quip = null;
    }
  });
})();
</script>
</body></html>
"""


def _header_profile(username: str) -> tuple:
    """(display name, is_fip). The name is prénom + nom from the student's own profile
    (itself pulled from their PASS dossier — see ps.fetch_dossier) when set, falling back
    to their raw login."""
    row = dbmod.get_db().execute(
        "SELECT nom, prenom, is_fip FROM profiles WHERE owner_username=?", (username,)).fetchone()
    name = f"{row['prenom']} {row['nom']}".strip() if row and (row["nom"] or row["prenom"]) else username
    return name, bool(row and row["is_fip"])


def _guide_seen(username: str) -> bool:
    return dbmod.get_db().execute(
        "SELECT 1 FROM guide_seen WHERE owner_username=?", (username,)).fetchone() is not None


def render(body_template, **ctx):
    body = render_template_string(body_template, **ctx)
    username = session.get("username")
    display_name, is_fip = _header_profile(username) if username else (None, False)
    dsfr_base = url_for("static", filename="dsfr")
    # ?v= so browsers drop their cached recolored sheet when the vendored DSFR is bumped.
    dsfr_css = url_for("fip_dsfr_stylesheet", v=DSFR_VERSION) if is_fip else f"{dsfr_base}/dsfr.min.css"
    logged_in = bool(session.get("token") and username)
    return render_template_string(LAYOUT, body=body, dsfr_base=dsfr_base, dsfr_css=dsfr_css,
                                  display_name=display_name, logged_in=logged_in,
                                  show_guide=logged_in and not _guide_seen(username),
                                  csrf_token=generate_csrf() if logged_in else "")


def get_flashed_message():
    return session.pop("_flash", None)
