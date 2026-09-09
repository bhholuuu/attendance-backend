"""Bulk CSV student import with validated preview + transactional commit.

Flow (PART 6):
    Upload -> Validate -> Preview Results -> Confirm Import -> Commit (single
    transaction)

Key guarantees (PART 3/5/7/8/9):
  * The same field rules as the manual student-creation API are reused, so a
    bulk import can never bypass validation.
  * The whole file is validated before anything is written.
  * By default (``allow_partial=False``) a commit is refused if any row is
    invalid, so nothing is imported and nothing is silently skipped. Setting
    ``allow_partial=True`` imports only the valid rows in one transaction and
    reports every invalid row.
  * All valid rows are committed in a single transaction; an unexpected
    database failure rolls everything back.
  * Roll-number uniqueness follows the existing model: unique among *active*
    students within the same class + division. Existing conflicts are reported,
    never overwritten.
  * Multiple students may legitimately share a parent phone number; duplicate
    parent numbers are NOT treated as errors.
  * An audit record (safe metadata only; never the file bytes) is stored for
    every committed import.
"""

import csv
import hashlib
import io
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.phone import normalize_phone_number
from app.models.school_class import Division, SchoolClass
from app.models.student import Student
from app.models.student_import import StudentImport
from app.models.user import User

EXPECTED_HEADER = [
    "class_name",
    "division_name",
    "student_name",
    "roll_number",
    "parent_name",
    "parent_whatsapp_number",
]

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_ROWS = 5000

_duplicate_roll = (
    "A student with this roll number already exists in this class and division."
)
_duplicate_roll_in_file = "Duplicate roll number in this file for the same class and division."
_required = "{field} is required."
_too_long = "{field} must be at most {max_len} characters."


@dataclass
class ValidationResult:
    token: str
    total_records: int
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def valid_records(self) -> int:
        return len(self.rows)

    @property
    def invalid_records(self) -> int:
        return len({e["row_number"] for e in self.errors})


def _abort(status_code: int, message: str):
    raise HTTPException(status_code=status_code, detail=message)


def _token(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bad_file(message: str):
    _abort(status.HTTP_400_BAD_REQUEST, message)


def _parse_csv(raw: bytes) -> list:
    """Decode and parse a CSV upload into data rows.

    Returns a list of cell-lists (excluding the header). Raises HTTP 400 for
    structural problems: wrong encoding, empty file, wrong header, no data
    rows, or too many rows.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        _bad_file("File must be a UTF-8 encoded CSV (Excel-compatible).")

    if not text.strip():
        _bad_file("File is empty.")

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader]
    if not rows:
        _bad_file("File is empty.")

    header = [cell.strip() for cell in rows[0]]
    if header != EXPECTED_HEADER:
        _bad_file(
            "Header must be exactly: " + ", ".join(EXPECTED_HEADER)
        )

    data_rows = rows[1:]
    if not data_rows:
        _bad_file("File contains no data rows.")
    if len(data_rows) > MAX_ROWS:
        _bad_file(f"File exceeds the maximum of {MAX_ROWS} rows.")

    return data_rows


def _validate_rows(db: Session, data_rows: list, existing_keys) -> ValidationResult:
    """Return validated parse rows plus a list of per-row errors.

    ``data_rows`` uses 1-based line numbers including the header (header = row
    1), so operators can map errors straight back to spreadsheet rows.
    """
    result = ValidationResult(token="", total_records=len(data_rows))

    seen_in_file = {}  # (class, division, roll) -> first row number

    for line_no, cells in enumerate(data_rows, start=2):
        errors = []
        fields = dict(zip(EXPECTED_HEADER, [c.strip() if c else c for c in cells]))

        if len(cells) != len(EXPECTED_HEADER):
            errors.append(
                {
                    "row_number": line_no,
                    "field": "row",
                    "error": f"Expected {len(EXPECTED_HEADER)} columns, found {len(cells)}.",
                }
            )
            result.errors.extend(errors)
            continue

        class_name = (fields.get("class_name") or "").strip()
        division_name = (fields.get("division_name") or "").strip()
        roll_number = (fields.get("roll_number") or "").strip()

        # ---- class / division relationships ----
        school_class = None
        division = None
        errors_append = lambda f, m: errors.append(
            {"row_number": line_no, "field": f, "error": m}
        )

        if not class_name:
            errors_append("class_name", _required.format(field="Class name"))
        else:
            school_class = (
                db.query(SchoolClass)
                .filter(
                    SchoolClass.name == class_name,
                    SchoolClass.is_active.is_(True),
                )
                .first()
            )
            if school_class is None:
                errors_append(
                    "class_name", "Class not found or inactive."
                )

        if not division_name:
            errors_append(
                "division_name", _required.format(field="Division name")
            )
        elif class_name and school_class is not None:
            division = (
                db.query(Division)
                .filter(
                    Division.class_id == school_class.id,
                    Division.name == division_name,
                    Division.is_active.is_(True),
                )
                .first()
            )
            if division is None:
                errors_append(
                    "division_name",
                    "Division not found in this class or inactive.",
                )

        # ---- student name ----
        student_name = (fields.get("student_name") or "").strip()
        if not student_name:
            errors_append(
                "student_name", _required.format(field="Student name")
            )
        elif len(student_name) > 100:
            errors_append(
                "student_name",
                _too_long.format(field="Student name", max_len=100),
            )

        # ---- parent name ----
        parent_name = (fields.get("parent_name") or "").strip()
        if not parent_name:
            errors_append("parent_name", _required.format(field="Parent name"))
        elif len(parent_name) > 100:
            errors_append(
                "parent_name",
                _too_long.format(field="Parent name", max_len=100),
            )

        # ---- roll number ----
        if not roll_number:
            errors_append(
                "roll_number", _required.format(field="Roll number")
            )
        elif len(roll_number) > 30:
            errors_append(
                "roll_number",
                _too_long.format(field="Roll number", max_len=30),
            )

        # ---- parent whatsapp number (same rule as the manual create API) ----
        normalized_phone = None
        raw_phone = (fields.get("parent_whatsapp_number") or "").strip()
        if not raw_phone:
            errors_append(
                "parent_whatsapp_number",
                _required.format(field="Parent WhatsApp number"),
            )
        else:
            try:
                normalized_phone = normalize_phone_number(raw_phone)
            except ValueError as exc:
                errors_append("parent_whatsapp_number", str(exc))

        # ---- intra-file duplicate roll (same class/division) ----
        if class_name and division_name and roll_number:
            file_key = (class_name, division_name, roll_number)
            if file_key in seen_in_file:
                errors_append("roll_number", _duplicate_roll_in_file)
            else:
                seen_in_file[file_key] = line_no

        # ---- conflict with existing active student (same class/division/roll) ----
        if (
            not errors
            and school_class is not None
            and division is not None
            and (school_class.id, division.id, roll_number) in existing_keys
        ):
            errors_append("roll_number", _duplicate_roll)

        if errors:
            result.errors.extend(errors)
            continue

        result.rows.append(
            {
                "class_id": school_class.id,
                "division_id": division.id,
                "name": student_name,
                "roll_number": roll_number,
                "parent_name": parent_name,
                "parent_whatsapp_number": normalized_phone,
            }
        )

    return result


def _existing_roll_keys(db: Session) -> set:
    """All active (class_id, division_id, roll_number) keys in the database."""
    rows = (
        db.query(Student.class_id, Student.division_id, Student.roll_number)
        .filter(Student.is_active.is_(True))
        .all()
    )
    return {(c, d, r) for c, d, r in rows}


def preview_import(db: Session, *, raw: bytes) -> ValidationResult:
    """Validate an uploaded CSV without writing anything to the database."""
    if len(raw) == 0:
        _bad_file("File is empty.")
    if len(raw) > MAX_FILE_BYTES:
        _bad_file(f"Uploaded file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit.")

    data_rows = _parse_csv(raw)
    result = _validate_rows(db, data_rows, _existing_roll_keys(db))
    result.token = _token(raw)
    return result


def commit_import(
    db: Session,
    *,
    raw: bytes,
    filename: str,
    preview_token: str,
    allow_partial: bool,
    admin: User,
) -> tuple[StudentImport, list]:
    """Commit a previously previewed import in a single transaction.

    Returns the audit record and the list of invalid-row errors. Raises HTTP
    400/409 without writing when the import must be refused.
    """
    if preview_token != _token(raw):
        _abort(
            status.HTTP_400_BAD_REQUEST,
            "Preview token does not match the uploaded file. Re-run preview with "
            "the same file before committing.",
        )

    data_rows = _parse_csv(raw)
    result = _validate_rows(db, data_rows, _existing_roll_keys(db))

    if result.errors and not allow_partial:
        _abort(
            status.HTTP_400_BAD_REQUEST,
            f"File contains {result.invalid_records} invalid row(s); no records "
            "were imported. Review the preview results and commit again with "
            "allow_partial=true to import only the valid rows.",
        )

    students = [
        Student(
            class_id=r["class_id"],
            division_id=r["division_id"],
            name=r["name"],
            roll_number=r["roll_number"],
            parent_name=r["parent_name"],
            parent_whatsapp_number=r["parent_whatsapp_number"],
            is_active=True,
            created_by=admin.id,
        )
        for r in result.rows
    ]

    audit = StudentImport(
        imported_by=admin.id,
        file_name=filename,
        preview_token=preview_token,
        allow_partial=allow_partial,
        total_records=result.total_records,
        imported_records=len(students),
        invalid_records=result.invalid_records,
    )

    try:
        db.add_all(students)
        db.add(audit)
        db.commit()
    except IntegrityError:
        db.rollback()
        _abort(
            status.HTTP_409_CONFLICT,
            "Import aborted: a concurrent change conflicted with the imported "
            "data. No records were imported.",
        )
    except Exception:
        db.rollback()
        raise

    db.refresh(audit)
    return audit, result.errors