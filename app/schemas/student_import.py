from pydantic import BaseModel


class ImportRowError(BaseModel):
    """Validation error for a single CSV data row / field."""

    row_number: int
    field: str
    error: str


class ImportPreviewResponse(BaseModel):
    """Result of validating a CSV upload (nothing is written to the database)."""

    file_name: str
    total_records: int
    valid_records: int
    invalid_records: int
    preview_token: str
    errors: list[ImportRowError]


class ImportCommitResponse(BaseModel):
    """Result of a confirmed (committed) student import."""

    import_id: int
    file_name: str
    total_records: int
    imported_records: int
    invalid_records: int
    errors: list[ImportRowError]