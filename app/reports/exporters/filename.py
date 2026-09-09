"""Safe, deterministic filenames for exported reports.

Never derive a filename directly from user-controlled path values. We build a
sanitized base from entity names (stripped of unsafe characters) plus the date
range, so a filename can never contain path separators or traversal sequences.
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def slugify(value: str, fallback: str = "report") -> str:
    """Return a URL-/path-safe slug from an arbitrary string."""
    cleaned = _UNSAFE.sub("_", value).strip("_")
    if not cleaned:
        return fallback
    return cleaned[:60]


def build_filename(report_type: str, data: dict, extension: str) -> str:
    """Build a safe filename for a report given the service data.

    Parameters are sanitized and a fixed prefix is used so the result never
    depends on untrusted path segments. ``extension`` should include the dot
    (e.g. ".csv", ".xlsx", ".pdf").
    """
    prefix = _prefix_for(report_type)
    if report_type == "student":
        base = slugify(data.get("student_name", "student"), "student")
        return (
            f"{prefix}_{base}_{data.get('start_date')}_to_"
            f"{data.get('end_date')}{extension}"
        )
    if report_type in ("class", "division"):
        class_slug = slugify(data.get("class_name", "class"), "class")
        div_slug = slugify(
            data.get("division_name") or "all", "all"
        )
        return (
            f"{prefix}_{class_slug}_{div_slug}_"
            f"{data.get('start_date')}_to_{data.get('end_date')}{extension}"
        )
    if report_type == "daily":
        return f"{prefix}_{data.get('date')}{extension}"
    if report_type == "summary":
        return (
            f"{prefix}_{data.get('start_date')}_to_"
            f"{data.get('end_date')}{extension}"
        )
    return f"{prefix}_{extension.lstrip('.')}"[:60]


def _prefix_for(report_type: str) -> str:
    return {
        "student": "student_attendance",
        "class": "class_attendance",
        "division": "division_attendance",
        "daily": "daily_attendance",
        "summary": "attendance_summary",
    }.get(report_type, "attendance_report")
