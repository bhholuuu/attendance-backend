"""Shared helpers for building normalized report payloads.

Every exporter (CSV / Excel / PDF) consumes the SAME normalized structure so
report calculation logic is never duplicated. This module translates the report
dicts returned by ``AttendanceReportService`` into a common shape:

    {
      "title": str,
      "subtitle": str,
      "meta":  [(label, value), ...],     # header block
      "summary": [(label, value), ...],   # key figures
      "columns": [str, ...],              # table header
      "rows": [[value, ...], ...],        # table data
    }
"""

from __future__ import annotations

from datetime import date
from typing import List, Tuple

REPORT_TYPES = ("student", "class", "division", "daily", "summary")

REPORT_TITLES = {
    "student": "Student Attendance Report",
    "class": "Class Attendance Report",
    "division": "Division Attendance Report",
    "daily": "Daily Attendance Report",
    "summary": "Attendance Summary Report",
}


def _pct(value) -> str:
    """Render a percentage value (float or None) as a display string."""
    if value is None:
        return "N/A"
    return f"{value}%"


def _today_str() -> str:
    return date.today().isoformat()


def build_payload(report_type: str, data: dict) -> dict:
    """Convert a service report dict into the common export payload."""
    if report_type == "student":
        return _student_payload(data)
    if report_type == "class" or report_type == "division":
        return _class_division_payload(report_type, data)
    if report_type == "daily":
        return _daily_payload(data)
    if report_type == "summary":
        return _summary_payload(data)
    raise ValueError(f"Unknown report type: {report_type}")


def _student_payload(data: dict) -> dict:
    meta: List[Tuple[str, str]] = [
        ("Student", f"{data['student_name']} ({data['roll_number']})"),
        ("Class", data["class_name"]),
        ("Division", data["division_name"]),
        ("Date Range", f"{data['start_date']} to {data['end_date']}"),
        ("Generated", _today_str()),
    ]
    summary: List[Tuple[str, str]] = [
        ("Total Attendance Days", str(data["total_attendance_days"])),
        ("Present Days", str(data["present_days"])),
        ("Absent Days", str(data["absent_days"])),
        ("Attendance %", _pct(data["attendance_percentage"])),
    ]
    columns = ["Date", "Status", "Recorded Status", "Class", "Division"]
    rows = [
        [
            r["date"],
            r["status"].value if hasattr(r["status"], "value") else r["status"],
            r["recorded_status"].value
            if r.get("recorded_status") is not None
            and hasattr(r["recorded_status"], "value")
            else (r["recorded_status"] if r.get("recorded_status") else ""),
            r["class_name"],
            r["division_name"],
        ]
        for r in data["daily_records"]
    ]
    return {
        "title": REPORT_TITLES["student"],
        "subtitle": data["student_name"],
        "meta": meta,
        "summary": summary,
        "columns": columns,
        "rows": rows,
    }


def _class_division_payload(report_type: str, data: dict) -> dict:
    title = REPORT_TITLES[report_type]
    scope = data.get("division_name") or "All Divisions"
    meta: List[Tuple[str, str]] = [
        ("Class", data["class_name"]),
        ("Division", data.get("division_name") or "All Divisions"),
        ("Date Range", f"{data['start_date']} to {data['end_date']}"),
        ("Generated", _today_str()),
    ]
    summary: List[Tuple[str, str]] = [
        ("Total Students", str(data["total_students"])),
        ("Total Attendance Sessions", str(data["total_attendance_sessions"])),
        ("Total Present Records", str(data["total_present_records"])),
        ("Total Absent Records", str(data["total_absent_records"])),
        ("Average Attendance %", _pct(data["average_attendance_percentage"])),
    ]
    columns = ["Roll Number", "Student Name", "Present Days", "Absent Days", "Attendance %"]
    rows = [
        [
            s["roll_number"],
            s["student_name"],
            s["present_days"],
            s["absent_days"],
            _pct(s["attendance_percentage"]),
        ]
        for s in data["student_summary"]["items"]
    ]
    return {
        "title": title,
        "subtitle": f"{data['class_name']} - {scope}",
        "meta": meta,
        "summary": summary,
        "columns": columns,
        "rows": rows,
    }


def _daily_payload(data: dict) -> dict:
    meta: List[Tuple[str, str]] = [
        ("Date", str(data["date"])),
        ("Generated", _today_str()),
    ]
    summary: List[Tuple[str, str]] = [
        ("Classes/Divisions", str(data["pagination"]["total"])),
    ]
    columns = ["Class", "Division", "Total Students", "Present", "Absent", "Attendance %", "Status"]
    rows = [
        [
            r["class_name"],
            r["division_name"],
            r["total_students"],
            r["present"],
            r["absent"],
            _pct(r["percentage"]),
            r["completion_status"],
        ]
        for r in data["items"]
    ]
    return {
        "title": REPORT_TITLES["daily"],
        "subtitle": str(data["date"]),
        "meta": meta,
        "summary": summary,
        "columns": columns,
        "rows": rows,
    }


def _summary_payload(data: dict) -> dict:
    meta: List[Tuple[str, str]] = [
        ("Date Range", f"{data['start_date']} to {data['end_date']}"),
        ("Generated", _today_str()),
    ]
    summary: List[Tuple[str, str]] = [
        ("Total Attendance Sessions", str(data["total_attendance_sessions"])),
        ("Total Students Marked", str(data["total_students_marked"])),
        ("Present Records", str(data["present_records"])),
        ("Absent Records", str(data["absent_records"])),
        ("Average Attendance %", _pct(data["average_attendance_percentage"])),
    ]
    columns = ["Date", "Present", "Absent", "Attendance %"]
    rows = [
        [
            t["date"],
            t["present"],
            t["absent"],
            _pct(t["percentage"]),
        ]
        for t in data["daily_trend"]
    ]
    return {
        "title": REPORT_TITLES["summary"],
        "subtitle": f"{data['start_date']} to {data['end_date']}",
        "meta": meta,
        "summary": summary,
        "columns": columns,
        "rows": rows,
    }
