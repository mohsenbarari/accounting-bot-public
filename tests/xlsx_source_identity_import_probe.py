"""Fresh target execution guard; dependency/code-loader reads are outside it."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from source_binding_import_probe import ForbiddenSideEffect, deny_side_effects

TARGET = "accounting_local_agent.xlsx_source_identity"
PUBLIC_NAMES = (
    "XLSX_SOURCE_IDENTITY_VERSION",
    "XLSX_SOURCE_IDENTITY_PROPERTY_NAME",
    "XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES",
    "XlsxSourceIdentityReason",
    "XlsxSourceIdentityError",
    "IdentifiedXlsxSource",
    "read_identified_xlsx_source",
)


class GuardedLoader(importlib.abc.Loader):
    def __init__(self, delegate: importlib.machinery.SourceFileLoader) -> None:
        self.delegate = delegate

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        # The package initializer already loads these. Keep that scope explicit.
        importlib.import_module("accounting_local_agent.xlsx_source_reader")
        importlib.import_module("accounting_local_agent.xlsx_snapshot_acquisition")
        importlib.import_module("accounting_contracts")
        code = self.delegate.get_code(TARGET)
        assert code is not None
        print("IMPORT_ENTERED", flush=True)
        with deny_side_effects():
            if sys.argv[1] == "inject_write":
                module.__dict__.update(Path=Path, canary_path=sys.argv[2])
                exec(
                    "Path(canary_path).write_text('synthetic canary')", module.__dict__
                )
            exec(code, module.__dict__)
        print("IMPORT_EXECUTED", flush=True)


class ProbeFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: Any = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        assert spec is not None
        assert isinstance(spec.loader, importlib.machinery.SourceFileLoader)
        spec.loader = GuardedLoader(spec.loader)
        return spec


def main() -> int:
    assert "accounting_local_agent" not in sys.modules and TARGET not in sys.modules
    assert sys.argv[1] in {"normal", "inject_write"}
    sys.meta_path.insert(0, ProbeFinder())
    try:
        module = importlib.import_module(TARGET)
    except ForbiddenSideEffect:
        assert TARGET not in sys.modules
        assert "accounting_local_agent" not in sys.modules
        print("IMPORT_REJECTED_BY_GUARD", flush=True)
        return 73
    package = sys.modules["accounting_local_agent"]
    for name in PUBLIC_NAMES:
        assert getattr(package, name) is getattr(module, name)
    assert module.XLSX_SOURCE_IDENTITY_VERSION == "xlsx-source-identity.v1"
    print("PROBE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
