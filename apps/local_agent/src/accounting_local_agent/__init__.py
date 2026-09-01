"""Local Windows Excel agent application.

Provides XLSX source reading, local change tracking, and server sync.
"""

from accounting_local_agent.xlsx_source_reader import (
    XLSX_SOURCE_READER_VERSION,
    SourceRowLocation,
    XlsxCellError,
    XlsxFormulaCoverageError,
    XlsxHeaderError,
    XlsxIdentityError,
    XlsxPackageError,
    XlsxSourceReadError,
    XlsxSourceReadResult,
    XlsxStructureError,
    read_xlsx_source_snapshot,
)

__version__ = "0.1.0"

__all__ = [
    "XLSX_SOURCE_READER_VERSION",
    "SourceRowLocation",
    "XlsxCellError",
    "XlsxFormulaCoverageError",
    "XlsxHeaderError",
    "XlsxIdentityError",
    "XlsxPackageError",
    "XlsxSourceReadError",
    "XlsxSourceReadResult",
    "XlsxStructureError",
    "read_xlsx_source_snapshot",
]
