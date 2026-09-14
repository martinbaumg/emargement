"""Shared page chrome (DSFR layout), the render() wrapper, and the flash-message helper."""
import colorsys
import functools
import os
import re

from flask import render_template_string, session, url_for

import db as dbmod

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

/* Long course titles in the "toujours exclus" tags wrap instead of overflowing. */
.att-rules .fr-tag{white-space:normal;text-align:left;height:auto;max-width:100%}

/* « Actualiser / Réimporter depuis PASS »: the DSFR icon (a ::before mask) spins while the
   several-second PASS round-trip is pending; repeat clicks are ignored meanwhile. */
@keyframes att-spin { to { transform: rotate(360deg); } }
.fr-btn.att-loading::before{animation:att-spin 1s linear infinite}
.fr-btn.att-loading{pointer-events:none}

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

  // The refresh buttons are plain links: spin until the next page replaces this one.
  document.querySelectorAll(".fr-btn.fr-icon-refresh-line").forEach(function(btn) {
    btn.addEventListener("click", function(ev) {
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;  // new tab: nothing to wait for here
      btn.classList.add("att-loading");
      btn.setAttribute("aria-busy", "true");
    });
  });
  // Back/forward cache restores the page exactly as left, i.e. still spinning.
  window.addEventListener("pageshow", function(ev) {
    if (!ev.persisted) return;
    document.querySelectorAll(".att-loading").forEach(function(btn) {
      btn.classList.remove("att-loading");
      btn.removeAttribute("aria-busy");
    });
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


def render(body_template, **ctx):
    body = render_template_string(body_template, **ctx)
    display_name, is_fip = _header_profile(session["username"]) if session.get("username") else (None, False)
    dsfr_base = url_for("static", filename="dsfr")
    # ?v= so browsers drop their cached recolored sheet when the vendored DSFR is bumped.
    dsfr_css = url_for("fip_dsfr_stylesheet", v=DSFR_VERSION) if is_fip else f"{dsfr_base}/dsfr.min.css"
    return render_template_string(LAYOUT, body=body, dsfr_base=dsfr_base, dsfr_css=dsfr_css,
                                  display_name=display_name)


def get_flashed_message():
    return session.pop("_flash", None)
