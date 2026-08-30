"""Automated architecture guard enforcing Domain layer isolation.

Domain code (packages/domain) must remain pure Python and must not depend on
FastAPI, aiogram, SQLAlchemy, Playwright, Excel COM (pywin32/win32com),
persistence, reporting or transport layers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_DOMAIN_MODULES = {
    "fastapi",
    "aiogram",
    "sqlalchemy",
    "playwright",
    "win32com",
    "pythoncom",
    "pywin32",
    "win32gui",
    "win32con",
    "win32process",
    "accounting_contracts",
    "contracts",
    "accounting_persistence",
    "persistence",
    "accounting_reporting",
    "reporting",
    "accounting_local_agent",
    "local_agent",
    "accounting_server_api",
    "server_api",
    "accounting_worker",
    "worker",
    "httpx",
    "watchdog",
    "lxml",
    "jinja2",
    "alembic",
    "psycopg",
    "psycopg2",
    "pydantic",
    "pydantic_settings",
}


def find_forbidden_imports_in_ast(
    tree: ast.AST, filename: str = "<unknown>"
) -> list[str]:
    """Inspect an AST tree for any forbidden module imports."""
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_pkg = alias.name.split(".")[0]
                if top_level_pkg in FORBIDDEN_DOMAIN_MODULES:
                    msg = (
                        f"{filename}:{node.lineno} - "
                        f"Forbidden import: 'import {alias.name}'"
                    )
                    violations.append(msg)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_pkg = node.module.split(".")[0]
                if top_level_pkg in FORBIDDEN_DOMAIN_MODULES:
                    msg = (
                        f"{filename}:{node.lineno} - "
                        f"Forbidden import: 'from {node.module} import ...'"
                    )
                    violations.append(msg)

    return violations


def find_forbidden_imports_in_source(
    source_code: str, filename: str = "<string>"
) -> list[str]:
    """Parse Python source code into AST and check for forbidden imports."""
    tree = ast.parse(source_code, filename=filename)
    return find_forbidden_imports_in_ast(tree, filename=filename)


def scan_directory_for_forbidden_domain_imports(directory: Path) -> list[str]:
    """Scan all Python files in a directory for forbidden domain imports."""
    all_violations: list[str] = []
    for py_file in directory.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        violations = find_forbidden_imports_in_source(content, filename=str(py_file))
        all_violations.extend(violations)
    return all_violations


def test_domain_has_no_forbidden_imports() -> None:
    """Verify that packages/domain/src contains no forbidden imports."""
    repo_root = Path(__file__).resolve().parent.parent
    domain_src = repo_root / "packages" / "domain" / "src"
    assert domain_src.is_dir(), f"Domain source directory missing: {domain_src}"

    violations = scan_directory_for_forbidden_domain_imports(domain_src)
    assert not violations, f"Domain architecture violations found: {violations}"


@pytest.mark.parametrize(
    "forbidden_code,expected_module",
    [
        ("import fastapi\n", "fastapi"),
        ("from fastapi import APIRouter\n", "fastapi"),
        ("import aiogram\n", "aiogram"),
        ("from aiogram.types import Message\n", "aiogram"),
        ("import sqlalchemy\n", "sqlalchemy"),
        ("from sqlalchemy.orm import Session\n", "sqlalchemy"),
        ("import playwright\n", "playwright"),
        ("from playwright.async_api import async_playwright\n", "playwright"),
        ("import win32com.client\n", "win32com"),
        ("from accounting_persistence import models\n", "accounting_persistence"),
        ("from accounting_reporting import generator\n", "accounting_reporting"),
        ("import httpx\n", "httpx"),
        ("import pydantic\n", "pydantic"),
    ],
)
def test_synthetic_negative_forbidden_import_detection(
    forbidden_code: str, expected_module: str
) -> None:
    """Prove that architecture guard detects forbidden imports in synthetic tests."""
    violations = find_forbidden_imports_in_source(
        forbidden_code, filename="synthetic_test.py"
    )
    assert len(violations) >= 1, f"Guard failed to detect {forbidden_code.strip()}"
    assert any(expected_module in v for v in violations), (
        f"Expected {expected_module} in violation message: {violations}"
    )
