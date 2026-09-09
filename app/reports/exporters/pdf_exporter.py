"""PDF exporter (V2.2) using ReportLab.

Produces a professional, printable single-page-ish report: title, report type,
entity/date-range meta, summary, and a student/daily table with totals.

Rendered into an in-memory buffer (no temp files). ReportLab's built-in fonts
are Latin-1 only, so non-ASCII characters are sanitized to '?' to avoid
encoding crashes while keeping the output printable.
"""

from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .base import build_payload
from .filename import build_filename

_HEADER_BG = colors.HexColor("#1F4E79")
_HEADER_TXT = colors.white
_ALT_BG = colors.HexColor("#EEF3FA")


def _clean(value) -> str:
    """Make a string safe for ReportLab's Latin-1 fonts."""
    text = str(value)
    out = []
    for ch in text:
        code = ord(ch)
        if 32 <= code <= 126 or ch in "€£¥°©®€–—’‘“”":
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def export_pdf(report_type: str, data: dict) -> bytes:
    """Serialize a report dict into a printable PDF (in-memory bytes)."""
    payload = build_payload(report_type, data)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="School Attendance Report",
        author="School Attendance Management System",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleX", parent=styles["Title"], fontSize=16, spaceAfter=4
    )
    sub_style = ParagraphStyle(
        "SubX", parent=styles["Normal"], textColor=colors.HexColor("#444444")
    )
    kv_style = ParagraphStyle("KVX", parent=styles["Normal"], fontSize=9)
    small = ParagraphStyle("SmallX", parent=styles["Normal"], fontSize=8)

    story = [Paragraph(_clean(payload["title"]), title_style)]
    story.append(Paragraph(_clean(payload["subtitle"]), sub_style))
    story.append(Spacer(1, 6))

    # Meta + generated time.
    meta_rows = [[Paragraph("<b>%s</b>" % _clean(k), kv_style), Paragraph(_clean(v), kv_style)] for k, v in payload["meta"]]
    meta_rows.append(
        [
            Paragraph("<b>Generated</b>", kv_style),
            Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kv_style),
        ]
    )
    meta_table = Table(meta_rows, colWidths=[45 * mm, 140 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 8))

    # Summary block.
    story.append(Paragraph("<b>Summary</b>", kv_style))
    story.append(Spacer(1, 3))
    summary_rows = [
        [Paragraph("<b>%s</b>" % _clean(k), small), Paragraph(_clean(v), small)]
        for k, v in payload["summary"]
    ]
    summary_table = Table(summary_rows, colWidths=[45 * mm, 140 * mm])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F2F2")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 10))

    # Details table.
    story.append(Paragraph("<b>Details</b>", kv_style))
    story.append(Spacer(1, 3))
    if payload["rows"]:
        header = [_clean(c) for c in payload["columns"]]
        body = [[_clean(c) for c in row] for row in payload["rows"]]
        detail_table = Table([header] + body, repeatRows=1)
        detail_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TXT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ALT_BG]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(detail_table)
    else:
        story.append(Paragraph("<i>No data available for the selected period.</i>", small))

    doc.build(story)
    return buf.getvalue()


def pdf_filename(report_type: str, data: dict) -> str:
    """Safe PDF filename for the given report data."""
    return build_filename(report_type, data, ".pdf")
