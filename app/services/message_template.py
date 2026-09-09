from typing import Optional

from app.models.attendance import AttendanceStatus


class MessageTemplateService:
    """Renders individual parent notification message content.

    The template is abstracted so it can be customized centrally and, in the
    future, driven by configuration. Each message is rendered for a *single*
    student only, so templates must never reference other students.
    """

    DEFAULT_TEMPLATE = (
        "Dear {parent_name},\n\n"
        "This is to inform you that {student_name} was marked "
        "{attendance_status} on {attendance_date}.\n\n"
        "Regards,\n"
        "School Administration"
    )

    def __init__(self, template: Optional[str] = None) -> None:
        self.template = template or self.DEFAULT_TEMPLATE

    def _format_date(self, raw: str) -> str:
        """Best-effort human-readable date from an ISO date string."""
        try:
            from datetime import date

            parts = raw.split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return date(y, m, d).strftime("%d %B %Y")
        except (IndexError, ValueError):
            return raw

    def render(
        self,
        *,
        parent_name: str,
        student_name: str,
        attendance_status: AttendanceStatus,
        attendance_date: str,
    ) -> str:
        """Render the message for one student.

        Only the placeholders corresponding to this student are substituted,
        so no other student's information is ever included.
        """
        status_word = (
            "present" if attendance_status == AttendanceStatus.PRESENT else "absent"
        )
        values = {
            "parent_name": str(parent_name),
            "student_name": str(student_name),
            "attendance_status": status_word,
            "attendance_date": self._format_date(attendance_date),
        }
        rendered = self.template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", value)
        return rendered
