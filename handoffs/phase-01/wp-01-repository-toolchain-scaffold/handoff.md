# Handoff

## Identity

- Phase: 1 — source and data-model foundation
- Work Package: WP-01: Repository and toolchain scaffold
- Branch/worktree: antigravity/phase-01-repository-toolchain
- Commit(s): ef92436
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Establish the initial reproducible Python 3.13 monorepo workspace, package structure, quality toolchain, Domain architecture isolation guard, CI workflow, and developer documentation under governed boundaries.

### In scope

- Monorepo workspace layout: `apps/local_agent`, `apps/server_api`, `apps/worker`, `packages/domain`, `packages/contracts`, `packages/persistence`, `packages/reporting`, `infra`, `tests`, `handoffs`.
- Configuration of `uv` workspace with `src` layouts, `.python-version` (3.13), and `uv.lock`.
- Component dependency declarations aligned with ADR-0002 through ADR-0005 (with pure Python domain and Windows platform markers).
- Development and quality controls: pytest, pytest-asyncio, hypothesis, testcontainers, ruff, mypy.
- Workspace import smoke tests and automated Domain architecture isolation guard with synthetic negative tests.
- GitHub Actions CI workflow for Linux and Windows runners.
- Developer command documentation in README.md.
- Handoff artifacts and acceptance validation.

### Out of scope

- Excel workbook reading, parsing, COM interaction, hashing, or modification.
- Database schemas, migrations, raw tables, or accounting logic.
- Telegram bot handlers, API endpoints, queue workers, or PDF generation logic.
- Container execution, Docker Compose setup, deployment, or infrastructure mutation.
- Real accounting data, real identities, secrets, or credential handling.
- Modifying ROADMAP.md, ADRs, Gate status, or merging to main.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Section 15.6 / ADR-0001 | ✅ Approved | Created Monorepo with Modular Monolith structure and isolated `accounting-domain` |
| Section 15.6 / ADR-0002 | ✅ Approved | Configured `apps/local_agent` with watchdog, lxml, httpx, cryptography and Windows markers |
| Section 15.6 / ADR-0003 | ✅ Approved | Configured `apps/server_api` with FastAPI, SQLAlchemy, psycopg and Alembic dependencies |
| Section 15.6 / ADR-0004 | ✅ Approved | Configured `packages/reporting` and `apps/worker` with persiantools, jinja2, playwright and aiogram |
| Section 15.6 / ADR-0005 | ✅ Approved | Configured root toolchain with uv, lockfile, pytest, hypothesis, testcontainers, ruff and mypy |
| Section 19.6 | 🧪 Witnessed | Automated tests (smoke + architecture guard) and CI workflow for Linux and Windows |
| O-46 to O-50 | ✅ Approved | Execution on feature branch, no self-approval, full handoff matrix and stop state |

## Changed files

| File | Change | Reason |
|---|---|---|
| `.python-version` | Created | Set Python 3.13 target for uv |
| `pyproject.toml` | Created | Root workspace configuration and quality toolchain settings |
| `uv.lock` | Created | Committed lockfile for frozen reproducible installation |
| `.github/workflows/ci.yml` | Created | GitHub Actions CI workflow for Linux and Windows |
| `README.md` | Modified | Added developer setup and quality toolchain commands |
| `apps/local_agent/*` | Created | Scaffold and pyproject for Windows local agent |
| `apps/server_api/*` | Created | Scaffold and pyproject for FastAPI server API |
| `apps/worker/*` | Created | Scaffold and pyproject for background queue worker |
| `packages/domain/*` | Created | Scaffold and pyproject for pure Python domain layer |
| `packages/contracts/*` | Created | Scaffold and pyproject for contracts and schemas |
| `packages/persistence/*` | Created | Scaffold and pyproject for persistence layer |
| `packages/reporting/*` | Created | Scaffold and pyproject for reporting engine |
| `infra/README.md` | Created | Infrastructure directory placeholder |
| `tests/test_imports.py` | Created | Smoke tests verifying all workspace packages import cleanly |
| `tests/test_architecture_guard.py` | Created | Domain isolation architecture guard and negative tests |
| `handoffs/phase-01/wp-01-repository-toolchain-scaffold/*` | Created | WP-01 Handoff, Acceptance Matrix and Test Results |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible workspace scaffold
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv lock --check` | 0 | Verify lockfile is up to date and resolved |
| `uv sync --frozen --all-packages` | 0 | Verify immutable frozen dependency installation |
| `uv run ruff format --check .` | 0 | Verify code formatting compliance |
| `uv run ruff check .` | 0 | Verify linting rules compliance |
| `uv run mypy .` | 0 | Verify strict static typing |
| `uv run pytest -v` | 0 | Execute import smoke tests and architecture guard tests |
| `git diff --check` | 0 | Verify clean diff with zero whitespace or line-ending defects |
| `python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-01-repository-toolchain-scaffold/` | 0 | Validate completeness and markers of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- Additional artifact paths: none

## Assumptions and open items

- none

## Risks

- Windows COM and PyInstaller dependencies will be exercised in future Phase 1 Windows-specific spike packages on actual Windows host.

## Rollback

1. Delete the feature branch `antigravity/phase-01-repository-toolchain`.
2. Checkout `main` branch to restore the repository to its clean baseline.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is stopped pending independent Codex review. No Gate approval, merge, push, deploy, or next Work Package has been performed.
