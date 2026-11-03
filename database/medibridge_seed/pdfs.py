"""Render synthetic medical reports as real PDF files (reportlab, BSD licence).

Every page carries a SYNTHETIC banner. Reports include a realistic identifying header (name, MRN, DOB),
because the privacy filter must be tested against identifiers that real reports contain.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .catalog import FACILITY

BANNER = "SYNTHETIC TEST RECORD - FICTIONAL PATIENT - NOT FOR CLINICAL USE"
_styles = getSampleStyleSheet()
_H = ParagraphStyle("h", parent=_styles["Heading2"], spaceAfter=4)
_B = ParagraphStyle("b", parent=_styles["BodyText"], leading=14)


def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#b00020"))
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawCentredString(LETTER[0] / 2, LETTER[1] - 12 * mm, BANNER)
    canvas.setFillColor(colors.grey)
    canvas.drawCentredString(LETTER[0] / 2, 10 * mm, f"{FACILITY} - page {doc.page}")
    canvas.restoreState()


def _header(patient: dict, title: str, when: date, author: str, department: str) -> list:
    rows = [["Patient", patient["name"], "MRN", patient["mrn"]],
            ["Date of birth", patient["dob"].isoformat(), "Report date", when.isoformat()],
            ["Department", department, "Reported by", author]]
    table = Table(rows, colWidths=[28 * mm, 62 * mm, 26 * mm, 54 * mm])
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                               ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                               ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    return [Paragraph(FACILITY, _styles["Title"]), Paragraph(title, _H), table, Spacer(1, 6 * mm)]


def _build(story: list) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, topMargin=20 * mm, bottomMargin=18 * mm,
                            title="Synthetic medical report", author=FACILITY,
                            subject=BANNER, invariant=1)  # invariant: byte-identical output for identical input
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()


def _paras(sections: list[tuple[str, str]]) -> list:
    story = []
    for heading, body in sections:
        story.append(Paragraph(heading, _H))
        for para in body.split("\n\n"):
            story.append(Paragraph(para.replace("\n", "<br/>"), _B))
        story.append(Spacer(1, 3 * mm))
    return story


def lab_report(patient: dict, when: date, panel: str, results: list[tuple], author: str, comment: str = "") -> bytes:
    rows = [["Test", "Result", "Units", "Reference range", "Flag"]]
    for name, value, unit, low, high in results:
        known = low is not None and high is not None
        flag = ("H" if value > high else "L" if value < low else "") if known else ""
        rows.append([name, f"{value:g}", unit, f"{low:g} - {high:g}" if known else "-", flag])
    table = Table(rows, colWidths=[52 * mm, 24 * mm, 24 * mm, 40 * mm, 14 * mm])
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                               ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                               ("TEXTCOLOR", (4, 1), (4, -1), colors.HexColor("#b00020"))]))
    story = _header(patient, f"Laboratory report - {panel}", when, author, "Clinical Laboratory")
    story += [table, Spacer(1, 5 * mm)]
    if comment:
        story += _paras([("Comment", comment)])
    return _build(story)


def narrative_report(patient: dict, when: date, title: str, department: str, author: str,
                     sections: list[tuple[str, str]]) -> bytes:
    return _build(_header(patient, title, when, author, department) + _paras(sections))
