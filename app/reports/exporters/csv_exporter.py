"""CSV exporter (V2.2).

Uses Python's ``csv.writer`` so escaping/quoting is handled correctly; we never
hand-concatenate CSV strings. Output is produced in an in-memory buffer with
UTF-8 encoding and CRLF line endings (RFC 4180 friendly).
"""

from __future__ import annotations

import csv
import io

from .base import build_payload
from .filename import build_filename


def export_csv(report_type: str, data: dict) -> bytes:
    """Serialize a report dict into UTF-8 CSV bytes."""
    payload = build_payload(report_type, data)

    text = io.StringIO()
    writer = csv.writer(text, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    # Header block.
    for label, value in payload["meta"]:
        writer.writerow([label, value])
    writer.writerow([])
    for label, value in payload["summary"]:
        writer.writerow([label, value])
    writer.writerow([])

    # Column headers + rows.
    writer.writerow(payload["columns"])
    for row in payload["rows"]:
        writer.writerow(row)

    return text.getvalue().encode("utf-8")


def csv_filename(report_type: str, data: dict) -> str:
    """Safe CSV filename for the given report data."""
    return build_filename(report_type, data, ".csv")
