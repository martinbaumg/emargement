"""Shared page chrome (DSFR layout), the render() wrapper, and the flash-message helper."""
import colorsys
import functools
import os
import re

from flask import render_template_string, session, url_for

import analytics
import db as dbmod
from csrf import generate_csrf
from roles import is_admin

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

# Scripted chatbot (#att-chat): a choose-your-own-answer tree, every branch ending on the
# same shrug — that's the joke. "wait" overrides the random typing pause, in ms.
CHAT_TREE = {
    "start": {"bot": "Bonjour. Je suis l'assistant automatique d'Émargement. "
                     "En quoi puis-je vous aider ?",
              "choices": [["Ma feuille d'émargement", "feuille"], ["PASS est lent", "pass"],
                          ["J'ai un vrai problème", "vrai"], ["Je m'ennuie", "ennui"]]},
    "feuille": {"bot": "Une feuille d'émargement, c'est un papier qu'on fait signer. "
                       "Jusque-là, tout va bien. Qu'est-ce qui cloche ?",
                "choices": [["Il manque un code UE", "code"], ["Il y a un cours en trop", "trop"],
                            ["Le total d'heures me paraît faux", "heures"]]},
    "code": {"bot": "Sur la page Cours, cliquez sur « Sans code UE » et tapez-le. "
                    "C'est écrit dessus, en fait.",
             "choices": [["Et si je ne le connais pas ?", "codeinconnu"], ["Merci", "jsp"]]},
    "codeinconnu": {"bot": "Il y a « Remplir depuis PASS » dans le profil : ça devine les codes. "
                           "Comme moi, mais en compétent.",
                    "choices": [["Et si ça se trompe ?", "jsp"], ["D'accord", "jsp"]]},
    "trop": {"bot": "Décochez-le. Il disparaît du PDF, pas de votre emploi du temps. "
                    "Je ne fais pas de miracles.",
             "choices": [["Et le travail en autonomie ?", "autonomie"], ["Parfait", "jsp"]]},
    "autonomie": {"bot": "Le travail en autonomie compte dans vos heures mais ne se signe pas. "
                         "Philosophiquement, c'est fascinant.",
                  "choices": [["Si vous le dites", "jsp"]]},
    "heures": {"bot": "Le total compte toutes les séances programmées, même celles que vous "
                      "retirez du PDF. C'est voulu.",
               "choices": [["Pourquoi ?", "jsp"], ["Logique", "jsp"]]},
    "pass": {"bot": "PASS prend son temps. Moi, j'attends. Vous aussi. "
                    "C'est un moment qu'on partage.",
             "choices": [["C'est normal ?", "passnormal"], ["Je peux faire quelque chose ?", "passfaire"]]},
    "passnormal": {"bot": "Normal, non. Habituel, oui.", "choices": [["Et donc ?", "jsp"]]},
    "passfaire": {"bot": "Respirer. Regarder par la fenêtre. Réfléchir à vos choix de vie.",
                  "choices": [["Ça aide", "jsp"], ["Et sinon ?", "jsp"]]},
    "vrai": {"bot": "Un vrai problème, ça demande un vrai humain. Moi, je suis surtout décoratif.",
             "choices": [["Non, je préfère vous parler", "jsp"]],
             "link": ["Ouvrir un ticket", "tickets"]},
    "ennui": {"bot": "Moi aussi, entre deux clics.",
              "choices": [["Racontez une blague", "blague"], ["Parlez-moi du rose FIP", "fip"]]},
    "blague": {"bot": "Deux étudiants entrent dans une salle. Le premier émarge. Le second aussi. Fin.",
               "choices": [["C'était nul", "blague2"], ["Encore", "blague2"]]},
    "blague2": {"bot": "Pourquoi PASS a-t-il traversé la route ? On ne sait pas, il charge encore.",
                "choices": [["J'arrête", "jsp"]]},
    "fip": {"bot": "Le rose permet de reconnaître un FIP à distance. C'est un service rendu à tous.",
            "choices": [["Et les autres ?", "jsp"], ["C'est beau", "jsp"]]},
    "jsp": {"bot": "Je sais pas, je m'en fous.", "wait": 2600,
            "choices": [["Reprendre depuis le début", "start"]],
            "link": ["Ouvrir un vrai ticket", "tickets"]},
}

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

/* Scripted chatbot: launcher pinned bottom-right, conversation as DSFR-coloured bubbles. */
.att-chat-launcher{position:fixed;right:1rem;bottom:1rem;z-index:900;box-shadow:0 4px 12px rgba(0,0,18,.2)}
.att-chat-log{display:flex;flex-direction:column;gap:.75rem;min-height:12rem;max-height:50vh;overflow-y:auto;padding:.25rem}
.att-chat-msg{max-width:85%;margin:0;padding:.5rem .75rem;border-radius:.5rem;overflow-wrap:anywhere}
.att-chat-msg--bot{align-self:flex-start;background:var(--background-alt-grey)}
.att-chat-msg--me{align-self:flex-end;background:var(--background-action-high-blue-france);color:var(--text-inverted-blue-france)}
.att-chat-choices{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}
.att-chat-typing{display:flex;gap:.25rem;align-items:center}
.att-chat-typing span{width:.4rem;height:.4rem;border-radius:50%;background:var(--text-mention-grey);animation:att-typing 1.2s infinite}
.att-chat-typing span:nth-child(2){animation-delay:.2s}
.att-chat-typing span:nth-child(3){animation-delay:.4s}
@keyframes att-typing { 0%,60%,100% { opacity:.25; } 30% { opacity:1; } }
@media (prefers-reduced-motion:reduce){
  .att-chat-typing span{animation:none;opacity:.5}
}

/* Ticket messages: typed by hand in a textarea, so their line breaks are kept. */
.att-ticket-message{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}

/* First-login guide (#att-guide): steps as a compact numbered list, and the footer link
   that reopens it is a <button> dressed like its <a> neighbours. */
.att-guide-steps{margin:0;padding-left:1.25rem}
.att-guide-steps>li{margin-bottom:1rem}
.att-guide-steps>li:last-child{margin-bottom:0}
.att-guide-steps p{margin:.25rem 0 0}
.att-guide-steps .fr-highlight{margin:.5rem 0 0 0;padding-left:1rem}
/* The guide opener is a <button> dressed as a footer link: the UA button font has to be
   overridden, but with the exact size of .fr-footer__bottom-link (.75rem/1.25rem) — plain
   `font:inherit` took the body's 1rem and left it visibly bigger than its neighbours.
   Colour is left to the DSFR class on the same element. */
button.att-guide-link{background:none;border:0;padding:0;cursor:pointer;
  font-family:inherit;font-size:.75rem;line-height:1.25rem;text-align:left}

/* Footer without a brand block: DSFR sizes .fr-footer__content for a logo on its left
   (flex-basis:50%, margin-left:auto), which left the description pushed to the right of an
   empty half. This site has no logo — in the header either — so the content takes the row. */
.att-footer .fr-footer__content{flex-basis:100%;margin-left:0;max-width:none}
/* DSFR closes the footer with a 1px grey line right across the page (the second inset shadow
   of .fr-footer, full viewport width). Nothing sits under the row of links any more, so it
   read as a stray rule at the very bottom: the blue top rule is kept, that one is dropped,
   and the last row gets a little air instead of ending 8px after the text. */
.att-footer{box-shadow:inset 0 2px 0 0 var(--border-plain-blue-france)}
.att-footer .fr-footer__bottom-list{padding-bottom:1.5rem}

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

/* Figures — the badge wall (/badges) and the audience dashboard (/kpi). Both are read as
   numbers, so they share the stat tile, the meter and the chart marks. Every mark wears the
   DSFR action blue through currentColor, which the « Je suis FIP » stylesheet turns pink
   along with the rest; text never wears the mark colour. */
.att-kpi-grid{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fill,minmax(10.5rem,1fr))}
.att-kpi{border:1px solid var(--border-default-grey);background:var(--background-default-grey);padding:.75rem 1rem}
.att-kpi__label{margin:0;font-size:.75rem;line-height:1.25rem;text-transform:uppercase;letter-spacing:.03em;color:var(--text-mention-grey)}
.att-kpi__value{margin:.25rem 0 0;font-size:1.75rem;line-height:2.25rem;font-weight:700}
.att-kpi__value--text{font-size:1.125rem;line-height:1.75rem;overflow-wrap:anywhere}
.att-kpi__hint{margin:.25rem 0 0;font-size:.75rem;line-height:1.25rem;color:var(--text-mention-grey)}
.att-kpi-head{display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
/* Hero figure: one per page, the number the dashboard leads with. Proportional figures —
   tabular ones look loose at this size; the tables below are where digits must line up. */
.att-hero{margin:.25rem 0 .5rem;font-size:4rem;line-height:1.1;font-weight:700}
.att-num{text-align:right;font-variant-numeric:tabular-nums}
.att-delta{font-weight:700}
.att-delta--up{color:var(--text-default-success)}
.att-delta--down{color:var(--text-default-error)}
/* Meter: a share or a progress on one track — filled step over a lighter step of the same ramp. */
/* display:block on both: width and height do nothing on a non-replaced inline element, so a
   meter built out of <span>s drew its track (a grid item is blockified) and no fill at all. */
.att-meter{display:block;height:.5rem;background:var(--background-contrast-grey);border-radius:.25rem;overflow:hidden}
.att-meter--sm{height:.25rem;max-width:12rem}
.att-meter__fill{display:block;height:100%;background:var(--background-action-high-blue-france);border-radius:.25rem}

.att-badges{display:grid;gap:1rem;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));margin:0;padding:0}
.att-badge{display:flex;flex-direction:column;border:1px solid var(--border-default-grey);background:var(--background-default-grey);padding:1rem}
.att-badge--locked{background:var(--background-alt-grey);border-style:dashed}
.att-badge__head{display:flex;align-items:center;gap:.5rem;margin:0 0 .75rem}
.att-badge__icon{color:var(--background-action-high-blue-france)}
.att-badge--locked .att-badge__icon{color:var(--text-disabled-grey)}
.att-badge__title{font-weight:700}
.att-badge__flavour,.att-badge__rule{margin:0;font-size:.875rem;line-height:1.5rem;color:var(--text-mention-grey)}
.att-badge__progress{margin:.25rem 0 0;font-size:.75rem;line-height:1.25rem;color:var(--text-mention-grey);font-variant-numeric:tabular-nums}
/* The action sits at the bottom of the card whatever the text above it measures, so the
   buttons line up across a row of cards. */
.att-badge__action{margin:auto 0 0;padding-top:1rem}
.att-badge--featured{border-color:var(--border-plain-blue-france);box-shadow:inset 0 0 0 1px var(--border-plain-blue-france)}
/* The same badge as shown on the profile: one row, icon apart, and the way back to the wall. */
.att-featured{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
  border:1px solid var(--border-default-grey);background:var(--background-alt-grey);padding:1rem 1.25rem}
.att-featured__icon{flex:none;color:var(--background-action-high-blue-france)}
.att-featured--empty .att-featured__icon{color:var(--text-disabled-grey)}
.att-featured__body{flex:1 1 12rem;min-width:0}
.att-featured__label{margin:0;font-size:.75rem;line-height:1.25rem;text-transform:uppercase;letter-spacing:.03em;color:var(--text-mention-grey)}
.att-featured__title{margin:.125rem 0 0;font-weight:700}
.att-featured--empty .att-featured__title{font-weight:400;color:var(--text-mention-grey)}
.att-featured__flavour{margin:.125rem 0 0;font-size:.875rem;line-height:1.5rem;color:var(--text-mention-grey)}
.att-featured__link{flex:none}
/* How rare a badge is among the participants. The tier wears one of the DSFR's decorative
   palettes (green / terre battue / glycine) — never a status colour, and no blue, so the FIP
   sheet leaves the scale intact. */
.att-badge__rarity{margin:.75rem 0 0;font-size:.75rem;line-height:1.25rem;color:var(--text-mention-grey)}
.att-tier{font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.att-tier--commun{color:var(--text-mention-grey)}
.att-tier--peu-commun{color:var(--text-label-green-emeraude)}
.att-tier--rare{color:var(--text-label-orange-terre-battue)}
.att-tier--epique{color:var(--text-label-purple-glycine)}

/* TAF ranking: one row per group, ranked, the leader's bar full and the others read against it.
   Group averages only — there is no per-person row anywhere on this page. */
.att-taf{margin:0;padding:0}
.att-taf--alone .att-taf__row{grid-template-columns:2rem minmax(0,1fr) auto}
.att-taf__row{display:grid;grid-template-columns:2rem minmax(0,1fr) 10rem auto;align-items:center;gap:1rem;
  padding:.75rem .5rem;border-bottom:1px solid var(--border-default-grey)}
.att-taf__row:first-child{border-top:1px solid var(--border-default-grey)}
.att-taf__row--mine{background:var(--background-alt-grey)}
.att-taf__rank{display:flex;align-items:center;justify-content:center;width:2rem;height:2rem;flex:none;
  border-radius:50%;font-size:.875rem;font-weight:700;background:var(--background-contrast-grey);color:var(--text-default-grey)}
.att-taf__rank--1{background:var(--background-action-high-blue-france);color:var(--text-inverted-blue-france)}
.att-taf__rank--2,.att-taf__rank--3{background:var(--background-contrast-blue-france);color:var(--text-label-blue-france)}
.att-taf__name{font-weight:700;overflow-wrap:anywhere}
.att-taf__count{display:block;font-size:.75rem;line-height:1.25rem;font-weight:400;color:var(--text-mention-grey)}
.att-taf__value{font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.att-taf__unit{font-size:.75rem;font-weight:400;color:var(--text-mention-grey)}
@media (max-width:47.98em){
  /* Phones: the bar is the first thing to go — the number beside it says the same. */
  .att-taf__row{grid-template-columns:2rem minmax(0,1fr) auto;gap:.75rem}
  .att-taf__bar{display:none}
}
.att-badges-score{margin:1.5rem 0 0;font-size:2.5rem;line-height:1;font-weight:700}
.att-badges-score__total{font-size:1.25rem;font-weight:400;color:var(--text-mention-grey)}

/* 30-day trend: the SVG is stretched to the page width (preserveAspectRatio="none") and the
   stroke held at 2px by vector-effect, so only the geometry stretches — which is what a time
   axis is for. No end-dot: a circle would stretch into an ellipse; the last value is written
   in the caption instead, and the whole series is in the table under the chart. */
.att-chart{position:relative;color:var(--background-action-high-blue-france);border-bottom:1px solid var(--border-default-grey)}
.att-chart svg{display:block;width:100%;height:9rem}
.att-chart__cursor{position:absolute;top:0;bottom:0;width:1px;background:var(--border-plain-grey);pointer-events:none}
.att-chart__tip{position:absolute;top:.25rem;left:0;padding:.25rem .5rem;font-size:.75rem;line-height:1.25rem;white-space:nowrap;
  background:var(--background-default-grey);border:1px solid var(--border-default-grey);color:var(--text-default-grey);pointer-events:none}
.att-chart__tip--end{left:auto;right:0}
/* Not .fr-content-media: that one is a column flex box with align-items:center, meant for
   images — it shrinks a full-width chart to its content and leaves the axis no room to
   spread its labels, which is how they ended up as "00h06h12h18h". */
.att-figure{margin:0;width:100%}
.att-figure__caption{margin:0 0 .75rem;font-size:.75rem;line-height:1.25rem;color:var(--text-mention-grey)}
.att-axis{margin:.5rem 0 0;padding:0;font-size:.75rem;line-height:1.25rem;color:var(--text-mention-grey)}
/* Date labels sit at the exact x of their own point (left: i/(n-1) of the width), the first
   flush left and the last flush right so neither hangs off the chart. */
.att-axis--points{position:relative;height:1.25rem}
.att-axis--points>span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.att-axis--points>span:first-child{transform:none}
.att-axis--points>span:last-child{transform:translateX(-100%)}
/* Hour labels ride the same 24 columns as the bars, so each one is centered under its own
   column; the empty cells in between are what keeps them apart. */
.att-axis--hours{display:flex;gap:2px}
.att-axis--hours>li{flex:1;min-width:0;text-align:center}
.att-axis--hours>li:first-child{text-align:left}
/* Hour histogram: plain HTML columns — 2px of surface between neighbours, 4px rounded at the
   data end, square on the baseline, capped at 24px so the band keeps some air. */
.att-bars{display:flex;align-items:flex-end;gap:2px;height:9rem;margin:0;padding:0;border-bottom:1px solid var(--border-default-grey)}
.att-bars__col{flex:1;display:flex;align-items:flex-end;justify-content:center;height:100%}
.att-bar{position:relative;display:block;width:100%;max-width:24px;min-height:2px;background:var(--background-action-high-blue-france);border-radius:4px 4px 0 0}
.att-bar__value{position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:.25rem;font-size:.75rem;
  color:var(--text-default-grey);opacity:0;transition:opacity .1s;pointer-events:none}
.att-bar--empty{min-height:1px;background:var(--border-default-grey)}
.att-bars__col:hover .att-bar__value{opacity:1}
@media (prefers-reduced-motion:reduce){.att-bar__value{transition:none}}
@media (max-width:47.98em){
  /* Phones: 24 columns of two digits don't fit — the hover value and the table carry them. */
  .att-hero{font-size:3rem}
  .att-bars,.att-chart svg{height:7rem}
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
          <li class="fr-nav__item">
            <a class="fr-nav__link" href="{{ url_for('badges') }}"
              {% if request.endpoint == 'badges' %}aria-current="page"{% endif %}>Palmarès</a>
          </li>
          {% if admin %}
          <li class="fr-nav__item">
            <a class="fr-nav__link" href="{{ url_for('admin_tickets') }}"
              {% if request.endpoint and request.endpoint.startswith('admin_') %}aria-current="page"{% endif %}>Tickets
              reçus{% if open_tickets %} ({{ open_tickets }}){% endif %}</a>
          </li>
          {% endif %}
          {# Last on purpose: « Assistance » is where everyone ends up, not where they start. #}
          <li class="fr-nav__item">
            <a class="fr-nav__link" href="{{ url_for('tickets') }}"
              {% if request.endpoint == 'tickets' %}aria-current="page"{% endif %}>Assistance</a>
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
<button type="button" class="fr-btn fr-btn--icon-left fr-icon-question-answer-fill att-chat-launcher"
  aria-controls="att-chat" data-fr-opened="false" title="Assistant automatique">Assistant</button>
<dialog id="att-chat" class="fr-modal" role="dialog" aria-labelledby="att-chat-title">
  <div class="fr-container fr-container--fluid fr-container-md">
    <div class="fr-grid-row fr-grid-row--center">
      <div class="fr-col-12 fr-col-md-8 fr-col-lg-6">
        <div class="fr-modal__body">
          <div class="fr-modal__header">
            <button type="button" class="fr-btn--close fr-btn" title="Fermer l'assistant" aria-controls="att-chat">Fermer</button>
          </div>
          <div class="fr-modal__content">
            <h1 id="att-chat-title" class="fr-modal__title">Assistant automatique</h1>
            <p class="fr-text--sm att-details">Il répond par choix, très vite, et rarement à côté.</p>
            <div id="att-chat-log" class="att-chat-log fr-mt-2w" role="log" aria-live="polite"></div>
            <div id="att-chat-choices" class="att-chat-choices"></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</dialog>
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
<footer class="fr-footer att-footer" role="contentinfo">
  <div class="fr-container">
    <div class="fr-footer__body">
      <div class="fr-footer__content">
        <p class="fr-footer__content-desc"><strong>Émargement</strong> — outil personnel et non
        officiel, sans lien avec l'école. Interface construite avec le
        <a class="fr-footer__content-link" href="https://www.systeme-de-design.gouv.fr/" target="_blank" rel="noopener noreferrer external">Système de Design de l'État</a>.</p>
      </div>
    </div>
    {# The links live in __bottom, not in __content: __content-list is the right-hand column of
       the body grid, so four links there pile up in a narrow stack against the right edge.
       __bottom is a full-width row under a rule, read left to right with DSFR's own separators. #}
    <div class="fr-footer__bottom">
      <ul class="fr-footer__bottom-list">
        <li class="fr-footer__bottom-item">
          <a class="fr-footer__bottom-link" href="https://github.com/martinbaumg/emargement" target="_blank" rel="noopener noreferrer external">Code source</a>
        </li>
        <li class="fr-footer__bottom-item">
          <a class="fr-footer__bottom-link att-contact-link" data-enc="Y29udGFjdEBiYXVtZ2FlcnRuZXIuZnI=" href="#">Contact</a>
        </li>
        {% if logged_in %}
        <li class="fr-footer__bottom-item">
          <button type="button" class="fr-footer__bottom-link att-guide-link" aria-controls="att-guide"
            data-fr-opened="{{ 'true' if show_guide else 'false' }}">Guide d'utilisation</button>
        </li>
        {% endif %}
        <li class="fr-footer__bottom-item">
          <a class="fr-footer__bottom-link" href="{{ url_for('kpi') }}"
            title="Tableau de bord d'audience">{{ weekly_visitors }} visiteur{{ 's' if weekly_visitors > 1 else '' }} cette semaine</a>
        </li>
      </ul>
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

  // Scripted chatbot: the answers are written in CHAT_TREE, the "typing" pause only exists
  // to make choosing feel like a conversation — and to let the final shrug land.
  var chatTree = {{ chat_tree|tojson }};
  var chatLog = document.getElementById("att-chat-log");
  var chatChoices = document.getElementById("att-chat-choices");
  if (chatLog) {
    var chatScroll = function() { chatLog.scrollTop = chatLog.scrollHeight; };
    var chatBubble = function(text, who) {
      var el = document.createElement("p");
      el.className = "att-chat-msg att-chat-msg--" + who;
      el.textContent = text;
      chatLog.appendChild(el);
      chatScroll();
      return el;
    };
    var chatNode = function(id) {
      var node = chatTree[id];
      chatChoices.replaceChildren();
      var dots = document.createElement("p");
      dots.className = "att-chat-msg att-chat-msg--bot att-chat-typing";
      dots.setAttribute("aria-label", "L'assistant écrit…");
      dots.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
      chatLog.appendChild(dots);
      chatScroll();
      setTimeout(function() {
        dots.remove();
        chatBubble(node.bot, "bot");
        (node.choices || []).forEach(function(choice) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "fr-btn fr-btn--secondary fr-btn--sm";
          button.textContent = choice[0];
          button.addEventListener("click", function() {
            chatBubble(choice[0], "me");
            chatNode(choice[1]);
          });
          chatChoices.appendChild(button);
        });
        if (node.link) {
          var link = document.createElement("a");
          link.className = "fr-btn fr-btn--sm";
          link.href = node.link[1];
          link.textContent = node.link[0];
          chatChoices.appendChild(link);
        }
        chatScroll();
      }, node.wait || (700 + Math.random() * 900));
    };
    document.getElementById("att-chat").addEventListener("dsfr.disclose", function() {
      if (!chatLog.childElementCount) chatNode("start");
    });
  }

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


def chat_tree() -> dict:
    """CHAT_TREE with its "link" targets turned into real URLs (they're endpoint names)."""
    tree = {}
    for key, node in CHAT_TREE.items():
        node = dict(node)
        if "link" in node:
            node["link"] = [node["link"][0], url_for(node["link"][1])]
        tree[key] = node
    return tree


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
    admin = logged_in and is_admin(username)
    # Counted here rather than imported from routes_tickets, which imports this module.
    open_tickets = dbmod.get_db().execute(
        "SELECT count(*) FROM tickets WHERE status != 'traite'").fetchone()[0] if admin else 0
    return render_template_string(LAYOUT, body=body, dsfr_base=dsfr_base, dsfr_css=dsfr_css,
                                  display_name=display_name, logged_in=logged_in,
                                  admin=admin, open_tickets=open_tickets, chat_tree=chat_tree(),
                                  show_guide=logged_in and not _guide_seen(username),
                                  weekly_visitors=analytics.weekly_visitors(),
                                  csrf_token=generate_csrf() if logged_in else "")


def get_flashed_message():
    return session.pop("_flash", None)
