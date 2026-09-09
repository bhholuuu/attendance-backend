"""Report exporters: CSV, Excel, PDF.

Each exporter consumes the same normalized payload built by ``base.build_payload``
from a report dict, so report calculation is never duplicated across formats.
"""

from .base import build_payload
from .csv_exporter import export_csv, csv_filename
from .excel_exporter import export_excel, excel_filename
from .pdf_exporter import export_pdf, pdf_filename

__all__ = [
    "build_payload",
    "export_csv",
    "csv_filename",
    "export_excel",
    "excel_filename",
    "export_pdf",
    "pdf_filename",
]
