"""Local Windows Excel agent application.

Provides XLSX source reading, snapshot acquisition, change tracking, and sync.
"""

from accounting_local_agent.xlsx_snapshot_acquisition import (
    XLSX_SNAPSHOT_ACQUISITION_VERSION,
    StableXlsxSnapshot,
    XlsxSnapshotAcquisitionError,
    XlsxSnapshotAcquisitionReason,
    XlsxSnapshotCleanupError,
    XlsxSnapshotIntegrityError,
    XlsxSnapshotStorageError,
    XlsxSourceNotReadyError,
    XlsxSourcePolicyError,
    open_stable_xlsx_snapshot,
)
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
    "XLSX_SNAPSHOT_ACQUISITION_VERSION",
    "XLSX_SOURCE_READER_VERSION",
    "SourceRowLocation",
    "StableXlsxSnapshot",
    "XlsxCellError",
    "XlsxFormulaCoverageError",
    "XlsxHeaderError",
    "XlsxIdentityError",
    "XlsxPackageError",
    "XlsxSnapshotAcquisitionError",
    "XlsxSnapshotAcquisitionReason",
    "XlsxSnapshotCleanupError",
    "XlsxSnapshotIntegrityError",
    "XlsxSnapshotStorageError",
    "XlsxSourceNotReadyError",
    "XlsxSourcePolicyError",
    "XlsxSourceReadError",
    "XlsxSourceReadResult",
    "XlsxStructureError",
    "open_stable_xlsx_snapshot",
    "read_xlsx_source_snapshot",
]
