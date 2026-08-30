# Acceptance Matrix

| Roadmap acceptance criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| Section 15.6 / ADR-0001: Monorepo layout with Modular Monolith structure (`apps/*`, `packages/*`, `infra`, `tests`) | Directory tree inspection | `apps/`, `packages/`, `infra/`, `tests/` | PASS | All 7 workspace components created with standard `src` layout and `py.typed` markers. |
| Section 15.6 / ADR-0001: Domain package independence (no third-party transport or persistence dependencies) | Automated architecture guard test + AST scan | `tests/test_architecture_guard.py` | PASS | Pure Python domain validated; positive AST check and 13 synthetic negative tests passed. |
| Section 15.6 / ADR-0002 to ADR-0005: Approved component dependency families with Python 3.13 and locked workspace | `uv lock --check` & `uv sync --frozen --all-packages --all-groups` | `uv.lock`, `pyproject.toml` | PASS | Resolved 88 packages, installed 81 packages with CPython 3.13.15 and `[tool.uv] required-version = "==0.12.7"`. |
| Section 19.6 / ADR-0005: Ruff linting and formatting controls | `uv run ruff format --check .` & `uv run ruff check .` | Ruff CLI output | PASS | 27 files formatted, all lint rules passed. |
| Section 19.6 / ADR-0005: mypy strict type checking | `uv run mypy .` | mypy CLI output | PASS | 9 source files checked with zero type issues in strict mode. |
| Section 19.6 / ADR-0005: Unit and smoke test suite | `uv run pytest -v` | Pytest CLI output | PASS | 21 tests passed (7 import smoke tests + 14 architecture guard tests). |
| Section 19.6: Clean Git diff without whitespace or EOF defects | `git diff --check origin/main...HEAD` | Git CLI output | PASS | Exited with code 0 and zero whitespace or line-ending defects against `origin/main`. |
| O-47 / O-51: Repository clean of real data, secrets, workbooks, SQLite DBs, or PDFs | Tracked files extension scan & pattern search | `git ls-files` & regex scan | PASS | No workbooks, SQLite databases, PDFs, secrets, real phone numbers or credentials tracked. |
| Section 19.6: CI workflow for Linux and Windows | GitHub Actions workflow configuration & PR Run #33322916013 | `.github/workflows/ci.yml`, https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33322916013 | PASS | Configured with `astral-sh/setup-uv@v9.0.0`, uv 0.12.7, `permissions: contents: read`, frozen sync with all groups; external CI passed on Ubuntu and Windows. |
| O-48: Handoff validation | Skill handoff validator | `validate_handoff.py` | PASS | Handoff directory passes validator with zero errors. |

## Coverage gaps

- None. All in-scope repository toolchain, scaffold, guard, CI and quality controls for WP-01 are implemented and verified, including external GitHub Actions PR CI execution on Linux and Windows (Run: https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33322916013).

## Gate statement

This matrix reports evidence only. It does not approve the Work Package or close the phase Gate.
