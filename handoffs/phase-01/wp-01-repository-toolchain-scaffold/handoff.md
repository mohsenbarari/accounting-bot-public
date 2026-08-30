# Handoff

## Identity

- Phase: 1 — source and data-model foundation
- Work Package: WP-01: Repository and toolchain scaffold
- Branch/worktree: antigravity/phase-01-repository-toolchain
- Commit(s): 51fa56b (initial scaffold baseline), c05e73b (reproducible bootstrap and CI alignment)
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Establish the initial reproducible Python 3.13 monorepo workspace, package structure, quality toolchain, Domain architecture isolation guard, CI workflow, and developer documentation under governed boundaries, updated for reproducible uv 0.12.7 bootstrap, GitHub Actions permissions, and full dependency group synchronization.

### In scope

- Monorepo workspace layout: `apps/local_agent`, `apps/server_api`, `apps/worker`, `packages/domain`, `packages/contracts`, `packages/persistence`, `packages/reporting`, `infra`, `tests`, `handoffs`.
- Configuration of `uv` workspace with `src` layouts, `.python-version` (3.13), `uv.lock`, and `[tool.uv] required-version = "==0.12.7"`.
- Component dependency declarations aligned with ADR-0002 through ADR-0005 (with pure Python domain and Windows platform markers).
- Development and quality controls: pytest, pytest-asyncio, hypothesis, testcontainers, ruff, mypy.
- Workspace import smoke tests and automated Domain architecture isolation guard with synthetic negative tests.
- GitHub Actions CI workflow with `astral-sh/setup-uv@v9.0.0`, uv 0.12.7, `permissions: contents: read`, frozen sync with `--all-groups`, push on `main` and pull_request on `main`.
- Developer command documentation in README.md.
- Handoff artifacts and acceptance validation including recorded PR CI run evidence.

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
| Section 15.6 / ADR-0005 | ✅ Approved | Configured root toolchain with uv 0.12.7, lockfile, pytest, hypothesis, testcontainers, ruff and mypy |
| Section 19.6 | 🧪 Witnessed | Automated tests (smoke + architecture guard) and CI workflow for Linux and Windows |
| O-46 to O-50 | ✅ Approved | Execution on feature branch, no self-approval, full handoff matrix and stop state |

## Changed files

| File | Change | Reason |
|---|---|---|
| `.python-version` | Created | Set Python 3.13 target for uv |
| `pyproject.toml` | Created / Modified | Root workspace configuration, quality toolchain settings, and pinned uv required-version == 0.12.7 |
| `uv.lock` | Created | Committed lockfile for frozen reproducible installation |
| `.github/workflows/ci.yml` | Created / Modified | GitHub Actions CI workflow with immutable setup-uv v9.0.0, permissions, and all-groups sync |
| `README.md` | Modified | Added developer setup and quality toolchain commands including --all-groups |
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
| `handoffs/phase-01/wp-01-repository-toolchain-scaffold/*` | Created / Modified | WP-01 Handoff, Acceptance Matrix and Test Results |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible workspace scaffold
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv --version` | 0 | Verify uv version is 0.12.7 |
| `uv lock --check` | 0 | Verify lockfile is up to date and resolved |
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify immutable frozen dependency installation with all groups |
| `uv run ruff format --check .` | 0 | Verify code formatting compliance |
| `uv run ruff check .` | 0 | Verify linting rules compliance |
| `uv run mypy .` | 0 | Verify strict static typing |
| `uv run pytest -v` | 0 | Execute import smoke tests and architecture guard tests |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace or line-ending defects against origin/main |
| `git ls-files \| grep -E '\.(xlsx\|xls\|xlsm\|sqlite\|sqlite3\|db\|pdf\|key\|pem\|env)$' \|\| true` | 0 | Verify no forbidden extensions or database/secret files tracked in git |
| `git grep -n -i -E '(password\s*[:=]\|secret\s*[:=]\|bearer\s+[A-Za-z0-9]\|BEGIN RSA\|BEGIN OPENSSH\|09[0-9]{9}\|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})' -- ':!ROADMAP.md' ':!docs/adr/*' ':!.agents/*' ':!handoffs/*' \|\| true` | 0 | Verify no sensitive patterns, private keys, IP addresses or real phone numbers in source/tests |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-01-repository-toolchain-scaffold/` | 0 | Validate completeness and markers of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- External PR CI run evidence: https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33322916013 (both Ubuntu and Windows runners succeeded).
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
