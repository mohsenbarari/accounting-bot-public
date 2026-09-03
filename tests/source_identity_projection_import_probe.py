"""Guard fresh target execution; code/dependency loading is explicitly outside it."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from source_binding_import_probe import ForbiddenSideEffect, deny_side_effects

TARGET = "accounting_contracts.source_identity_projection"
PUBLIC_NAMES = (
    "SOURCE_IDENTITY_PROJECTION_VERSION",
    "SourceIdentityProjectionReason",
    "SourceIdentityProjectionError",
    "SourceIdentityCatalog",
    "project_source_prior",
)


class Loader(importlib.abc.Loader):
    def __init__(self, delegate: importlib.machinery.SourceFileLoader) -> None:
        self.delegate = delegate

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        importlib.import_module("accounting_contracts.source_binding")
        importlib.import_module("accounting_contracts.source_change_plan")
        code = self.delegate.get_code(TARGET)
        assert code is not None
        print("IMPORT_ENTERED", flush=True)
        with deny_side_effects():
            if sys.argv[1] == "inject_write":
                module.__dict__.update(Path=Path, canary_path=sys.argv[2])
                exec("Path(canary_path).write_text('SYNTHETIC')", module.__dict__)
            exec(code, module.__dict__)
        print("IMPORT_EXECUTED", flush=True)


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: Any = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        assert spec is not None
        assert isinstance(spec.loader, importlib.machinery.SourceFileLoader)
        spec.loader = Loader(spec.loader)
        return spec


def main() -> int:
    assert "accounting_contracts" not in sys.modules and TARGET not in sys.modules
    sys.meta_path.insert(0, Finder())
    try:
        module = importlib.import_module(TARGET)
    except ForbiddenSideEffect:
        assert TARGET not in sys.modules and "accounting_contracts" not in sys.modules
        print("IMPORT_REJECTED_BY_GUARD", flush=True)
        return 73
    package = sys.modules["accounting_contracts"]
    assert module.SOURCE_IDENTITY_PROJECTION_VERSION == "source-identity-projection.v1"
    assert all(getattr(package, name) is getattr(module, name) for name in PUBLIC_NAMES)
    print("PROBE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
