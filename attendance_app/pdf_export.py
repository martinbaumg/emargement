"""
Generates a "feuille d'émargement" PDF for the logged-in app user, matching the
layout of the official IMT Atlantique template (CHAMROUKHI_feuille_émargement_
FIPA3_NETCLOUD.pdf): portrait A4, top-left school logo, "APPRENANT" header box,
week/UE reference tables, and a per-session table with both signature columns
left blank — the printed sheet is signed by hand.
Built from scratch with reportlab (the source PDF is flat, no fillable form
fields) but reusing the logo/footer images extracted from that file and its
colors/column layout, rather than approximating them.

Scope:
- Header uses the logged-in user's own profile info, not any other student's.
- The apprentissage warning banner only appears if the user's profile marks
  them as "en contrat d'apprentissage" (it's specific to that arrangement, not
  universal — the original file happened to be for such a student).
- UE reference table + per-row CODE UE are populated from the profile's
  freeform "Name = CODE" list (optional) — PASS/the agenda doesn't expose
  official UE codes, so there's no way to derive this automatically.
- Consecutive same-title lessons on the same day separated by a short gap
  (e.g. two 1h15 slots forming one continuous session with a 15-min break)
  are pre-merged by the caller into a single row spanning the full range —
  "Durée pause" only ever reflects a real merged break, never a lone slot.
- "A cocher si absence justifiée" is left blank (no data source) — same as
  a paper sheet, meant to be handled by whoever prints/annotates it.
"""
import datetime
import io
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGO_TOP = os.path.join(ASSETS_DIR, "logo_top.png")
FOOTER_BAND = os.path.join(ASSETS_DIR, "footer_band.png")

GREEN_HEADER = colors.HexColor("#A9D08E")   # main table header
LIGHT_GREEN = colors.HexColor("#C6E0B4")    # UE reference table header
LIGHT_BLUE = colors.HexColor("#DDEBF7")     # APPRENANT label column
GREY = colors.HexColor("#D0CECE")
RED = colors.red


def format_hours(hours: float) -> str:
    """0.25 -> "0h15", 1.25 -> "1h15", 29.666 -> "29h40". The column is headed
    "Durée formation (en h)" on the official sheet, and a decimal like 0.17 reads as
    neither 10 minutes nor anything else useful — hours and minutes is how the paper
    version is filled in. Rounded to the nearest minute, since the durations come from
    start/end times that are themselves whole minutes."""
    total_minutes = round(hours * 60)
    return f"{total_minutes // 60}h{total_minutes % 60:02d}"


def _academic_year_label() -> str:
    today = datetime.date.today()
    start = today.year if today.month >= 9 else today.year - 1
    return f"Année académique {start}-{start + 1}"


def _footer(canvas, doc):
    canvas.saveState()
    # native footer_band.png is 563x120 px; draw at a fixed width, proportional height
    draw_w = 70 * mm
    draw_h = draw_w * (120 / 563)
    canvas.drawImage(FOOTER_BAND, 12 * mm, 6 * mm, width=draw_w, height=draw_h,
                      preserveAspectRatio=True, mask="auto")
    canvas.restoreState()


def build_pdf(profile: dict, week_start: str, week_end: str, week_number: str, lessons: list,
              ue_table: list = None, total_hours: str = "", show_total_hours: bool = True) -> bytes:
    """
    profile: {"nom","prenom","formation","taf","campus","apprentissage" (0/1)}
    week_start/week_end: "dd/mm/yyyy" strings
    lessons: list of dicts, already merged by the caller — date_label, start ("HH:MM"),
             end ("HH:MM"), pause_min (int or "" — only set on an actual merged row),
             duration_hours (float, actual teaching time, excludes any pause; rendered
             as "1h15" by format_hours), title,
             code_ue, teacher_name
    ue_table: optional list of (name, code) tuples for the reference table
    total_hours: already-formatted label for the "Total heures de formation" box
                 (see format_hours)
    show_total_hours: False keeps that box but leaves it blank, to be filled in by hand
                      (profile setting)
    """
    ue_table = ue_table or []
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=22 * mm,
    )
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9)
    red_bold = ParagraphStyle("red_bold", parent=styles["Normal"], fontSize=7.5, leading=9.5,
                               textColor=RED, fontName="Helvetica-Bold", alignment=TA_CENTER)
    year_style = ParagraphStyle("year", parent=styles["Normal"], fontSize=9,
                                 fontName="Helvetica-Bold", alignment=TA_CENTER)
    note_style = ParagraphStyle("note", parent=styles["Normal"], fontSize=7, leading=9,
                                 textColor=colors.HexColor("#444444"))

    elements = []

    logo = Image(LOGO_TOP)
    logo.drawWidth = 16 * mm
    logo.drawHeight = 16 * mm * (99 / 150)  # native 150x99px aspect ratio
    logo.hAlign = "LEFT"
    elements.append(logo)
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(_academic_year_label(), year_style))  # centered on full page width
    elements.append(Spacer(1, 3 * mm))

    apprenant_data = [
        ["APPRENANT", ""],
        ["NOM", profile.get("nom", "")],
        ["PRENOM", profile.get("prenom", "")],
        ["FORMATION", profile.get("formation", "")],
        ["TAF", profile.get("taf", "")],
        ["CAMPUS", profile.get("campus", "")],
    ]
    apprenant_table = Table(apprenant_data, colWidths=[30 * mm, 45 * mm])
    apprenant_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (1, 0), LIGHT_BLUE),
        ("BACKGROUND", (0, 1), (0, -1), LIGHT_BLUE),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    right_note = []
    if profile.get("apprentissage"):
        right_note = [
            Paragraph("IMPORTANT : cet étudiant est en contrat d'apprentissage", red_bold),
            Paragraph("Chaque séance doit faire l'objet d'une double signature à la fin de "
                      "la séance (intervenant et apprenant).", red_bold),
        ]
    header_row = Table([[apprenant_table, right_note]], colWidths=[80 * mm, 105 * mm])
    header_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_row)
    elements.append(Spacer(1, 4 * mm))

    week_table = Table([["Semaine du", week_start, "au", week_end, "N° de\nsemaine", week_number]],
                        colWidths=[20 * mm, 22 * mm, 8 * mm, 22 * mm, 18 * mm, 12 * mm])
    week_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, 0), GREEN_HEADER),
        ("BACKGROUND", (2, 0), (2, 0), GREEN_HEADER),
        ("BACKGROUND", (4, 0), (4, 0), GREEN_HEADER),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    total_table = Table([["Total heures de\nformation", total_hours if show_total_hours else ""]],
                        colWidths=[35 * mm, 20 * mm])
    total_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, 0), GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    # week_table hugs the left margin, total_table sits at the right margin, gap between them
    usable_width = A4[0] - 24 * mm
    gap = usable_width - 102 * mm - 55 * mm
    week_row = Table([[week_table, "", total_table]], colWidths=[102 * mm, gap, 55 * mm])
    week_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(week_row)
    elements.append(Spacer(1, 4 * mm))

    if ue_table:
        ue_data = [["UE", "CODE UE"]] + [[name, code] for name, code in ue_table]
        ue_tbl = Table(ue_data, colWidths=[130 * mm, 27 * mm])
        ue_tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREEN),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ]))
        elements.append(ue_tbl)
        elements.append(Spacer(1, 3 * mm))

    elements.append(Paragraph(
        "Possibilité de rassembler plusieurs créneaux mais il faut indiquer la durée de "
        "pause précisée dans PASS", note_style))
    elements.append(Spacer(1, 2 * mm))

    cols = ["DATE", "CODE\nUE", "Horaire\ndébut", "Horaire\nfin", "Durée\npause (min)",
            "Durée\nformation\n(en h)", "Nom intervenant", "Signature\nintervenant",
            "Signature\nétudiant", "A cocher si\nabsence\njustifiée"]
    rows = [cols]
    for lesson in lessons:
        pause_min = lesson.get("pause_min") or ""
        hours = lesson.get("duration_hours")
        hours_label = format_hours(hours) if hours is not None else ""
        rows.append([
            lesson.get("date_label", ""),
            lesson.get("code_ue", ""),
            lesson.get("start", ""), lesson.get("end", ""), pause_min, hours_label,
            Paragraph(lesson.get("teacher_name", ""), small),
            "",  # Signature intervenant — signed by hand
            "",  # Signature étudiant — signed by hand
            "",
        ])

    table = Table(
        rows,
        colWidths=[18 * mm, 12 * mm, 13 * mm, 13 * mm, 16 * mm, 16 * mm,
                   36 * mm, 24 * mm, 24 * mm, 11 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), GREEN_HEADER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 6.5),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (1, 1), (5, -1), "CENTER"),
        # Signature intervenant / Signature étudiant columns — gray header only
        ("BACKGROUND", (7, 0), (8, 0), GREY),
        # A cocher si absence justifiée — smaller, red header text, no fill
        ("BACKGROUND", (9, 0), (9, 0), colors.white),
        ("TEXTCOLOR", (9, 0), (9, 0), RED),
        ("FONTSIZE", (9, 0), (9, 0), 5.5),
    ]))
    elements.append(table)

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
