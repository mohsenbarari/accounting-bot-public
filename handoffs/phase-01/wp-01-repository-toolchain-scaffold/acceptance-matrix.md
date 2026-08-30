# Acceptance Matrix

| Roadmap acceptance criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| Section 15.6 / ADR-0001: Monorepo layout with Modular Monolith structure (`apps/*`, `packages/*`, `infra`, `tests`) | Directory tree inspection | `apps/`, `packages/`, `infra/`, `tests/` | PASS | All 7 workspace components created with standard `src` layout and `py.typed` markers. |
| Section 15.6 / ADR-0001: Domain package independence (no third-party transport or persistence dependencies) | Automated architecture guard test + AST scan | `tests/test_architecture_guard.py` | PASS | Pure Python domain validated; positive AST check and 13 synthetic negative tests passed. |
| Section 15.6 / ADR-0002 to ADR-0005: Approved component dependency families with Python 3.13 and locked workspace | `uv lock --check` & `uv sync --frozen --all-packages` | `uv.lock`, `pyproject.toml` | PASS | Resolved 88 packages, installed 81 packages with CPython 3.13.15. |
| Section 19.6 / ADR-0005: Ruff linting and formatting controls | `uv run ruff format --check .` & `uv run ruff check .` | Ruff CLI output | PASS | 25 files formatted, all lint rules passed. |
| Section 19.6 / ADR-0005: mypy strict type checking | `uv run mypy .` | mypy CLI output | PASS | 9 source files checked with zero type issues in strict mode. |
| Section 19.6 / ADR-0005: Unit and smoke test suite | `uv run pytest -v` | Pytest CLI output | PASS | 21 tests passed (7 import smoke tests + 14 architecture guard tests). |
| Section 19.6: Clean Git diff without whitespace or EOF defects | `git diff --check` | Git CLI output | PASS | Exited with code 0 and zero whitespace defects. |
| O-47 / O-51: Repository clean of real data, secrets, workbooks, SQLite DBs, or PDFs | Repository pattern scan | `find .` scan | PASS | No sensitive assets, workbooks or DB files present in tracked files. |
| Section 19.6: CI workflow for Linux and Windows | GitHub Actions workflow configuration | `.github/workflows/ci.yml` | PASS | Configured for Ubuntu and Windows runners with uv setup, lock check, lint, type-check and pytest. |
| O-48: Handoff validation | Skill handoff validator | `validate_handoff.py` | PASS | Handoff directory passes validator with zero errors. |

## Coverage gaps

- None. All in-scope repository toolchain, scaffold, guard, CI and quality controls for WP-01 are implemented and verified.

## Gate statement

This matrix reports evidence only. It does not approve the Work Package or close the phase Gate.
