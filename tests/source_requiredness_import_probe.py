"""Subprocess-only probe of requiredness module execution, including failed imports.

The normal package import initializes accepted dependencies first. Only the exact
requiredness loader is intercepted: its code is loaded before the guard, then its
top-level execution runs with all application I/O forbidden. No path allowlist is
needed, and dependency bootstrap (including timezone loading) is not claimed pure.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.abc
import importlib.machinery
import io
import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

TARGET = "accounting_contracts.source_requiredness"
PUBLIC_NAMES = (
    "SourceRequirednessIssueReason",
    "SourceRequirednessIssue",
    "SourceRequirednessReport",
    "SourceRequirednessInputError",
    "evaluate_source_requiredness",
)


class ForbiddenSideEffect(RuntimeError):
    """A deliberately intercepted application operation."""


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise ForbiddenSideEffect("Application side effect during requiredness import")


@contextmanager
def deny_side_effects() -> Iterator[None]:
    seams: list[tuple[Any, str]] = [
        (builtins, "open"),
        (io, "open"),
        (io, "open_code"),
        (os, "open"),
        (Path, "open"),
        (Path, "read_bytes"),
        (Path, "read_text"),
        (Path, "write_bytes"),
        (Path, "write_text"),
        (socket, "socket"),
        (time, "time"),
        (time, "monotonic"),
        (time, "monotonic_ns"),
        (uuid, "uuid4"),
        (threading.Thread, "start"),
    ]
    if hasattr(uuid, "uuid7"):
        seams.append((uuid, "uuid7"))
    originals = [(owner, name, getattr(owner, name)) for owner, name in seams]
    try:
        for owner, name, _ in originals:
            setattr(owner, name, forbidden)
        yield
    finally:
        for owner, name, value in reversed(originals):
            setattr(owner, name, value)


class GuardedLoader(importlib.abc.Loader):
    def __init__(self, delegate: importlib.machinery.SourceFileLoader) -> None:
        self.delegate = delegate

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        # The sole loader read is deliberately outside application execution.
        code = self.delegate.get_code(TARGET)
        assert code is not None
        print("IMPORT_ENTERED", flush=True)
        actions = {
            "path_write": "Path(probe_path).open('w').write('synthetic canary')",
            "builtins_read": "open(probe_path, 'rb').read()",
            "io_read": "io.open(probe_path, 'rb').read()",
            "os_read": "os.close(os.open(probe_path, os.O_RDONLY))",
            "path_read": "Path(probe_path).open('rb').read()",
            "code_read": "Path(probe_path).open('rb').read()",
            "zoneinfo_read": "Path(probe_path).open('rb').read()",
        }
        action = sys.argv[1]
        with deny_side_effects():
            if action != "normal":
                # Inject at the real target import boundary, in the module's scope.
                module.__dict__.update(Path=Path, io=io, os=os, probe_path=sys.argv[2])
                exec(actions[action], module.__dict__)
            exec(code, module.__dict__)
        print("IMPORT_EXECUTED", flush=True)


class ProbeFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        assert spec is not None
        assert isinstance(spec.loader, importlib.machinery.SourceFileLoader)
        spec.loader = GuardedLoader(spec.loader)
        return spec


def main() -> int:
    assert "accounting_contracts" not in sys.modules
    assert TARGET not in sys.modules
    sys.meta_path.insert(0, ProbeFinder())
    try:
        module = importlib.import_module(TARGET)
    except ForbiddenSideEffect:
        assert TARGET not in sys.modules
        assert "accounting_contracts" not in sys.modules
        print("IMPORT_REJECTED_BY_GUARD", flush=True)
        return 73
    package = sys.modules["accounting_contracts"]
    for name in PUBLIC_NAMES:
        assert getattr(package, name) is getattr(module, name)
    assert module.SOURCE_REQUIREDNESS_VERSION == "source-requiredness.v1"
    print("PROBE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
