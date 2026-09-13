"""Guard exact source_import_store module execution.

Follows the repository's import-probe pattern for side-effect-free import
verification (IS-01).
"""

from __future__ import annotations

import builtins
import importlib
import importlib.abc
import importlib.machinery
import io
import os
import random
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

TARGET = "accounting_persistence.source_import_store"
PUBLIC_NAMES = (
    "SOURCE_IMPORT_STORE_VERSION",
    "SourceImportStoreReason",
    "SourceImportStoreError",
    "SourceImportDisposition",
    "SourceImportRequest",
    "SourceImportReceipt",
    "SourceImportStoreView",
    "initialize_source_import_store",
    "read_source_import_store",
    "commit_source_import",
)


class ForbiddenSideEffect(RuntimeError):
    """A specific intercepted operation, distinguishable from unrelated failures."""


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise ForbiddenSideEffect("Source import store side-effect guard")


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
        (socket, "create_connection"),
        (time, "time"),
        (time, "time_ns"),
        (time, "monotonic"),
        (time, "monotonic_ns"),
        (time, "perf_counter"),
        (uuid, "uuid1"),
        (uuid, "uuid4"),
        (os, "urandom"),
        (random, "random"),
        (random, "getrandbits"),
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
        # Bootstrap declared dependencies outside the target execution guard
        importlib.import_module("sqlite3")
        importlib.import_module("accounting_contracts")
        code = self.delegate.get_code(TARGET)
        assert code is not None
        print("IMPORT_ENTERED", flush=True)
        with deny_side_effects():
            if sys.argv[1] == "inject_write":
                module.__dict__.update(Path=Path, canary_path=sys.argv[2])
                exec(
                    "Path(canary_path).write_text('synthetic canary')",
                    module.__dict__,
                )
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
    assert TARGET not in sys.modules
    assert sys.argv[1] in ("normal", "inject_write")
    sys.meta_path.insert(0, ProbeFinder())
    try:
        module = importlib.import_module(TARGET)
    except ForbiddenSideEffect as exc:
        assert str(exc) == "Source import store side-effect guard"
        print("IMPORT_REJECTED_BY_GUARD", flush=True)
        return 73
    package = sys.modules["accounting_persistence"]
    for name in PUBLIC_NAMES:
        assert getattr(package, name) is getattr(module, name)
    assert module.SOURCE_IMPORT_STORE_VERSION == "source-import-store.v1"
    print("PROBE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
