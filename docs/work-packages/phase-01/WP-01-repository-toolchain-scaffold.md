# WP-01: Repository and toolchain scaffold

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 foundation only; this Work Package cannot close G1
- Status: Accepted and merged
- Issued by: Codex Project Manager
- Issued on: 2026-08-30
- Accepted by: Codex Project Manager
- Accepted on: 2026-08-30
- Review evidence: PR #7 and final CI Run `33324657599`
- Merge commit: `4f9ad3eae9ccfe376a917ec7a9653bcc7e7fc128`
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-repository-toolchain`
- Baseline: latest clean `origin/main` containing this Work Package
- Handoff path: `handoffs/phase-01/wp-01-repository-toolchain-scaffold/`

## Objective

Create the smallest reproducible Python 3.13 monorepo and quality-tooling baseline required by the accepted architecture. This package establishes boundaries and automated checks only; it does not implement accounting, Excel ingestion, persistence, Telegram, reporting or deployment behavior.

## Roadmap traceability

- Roadmap section 15.6: approved architecture and technology stack.
- Roadmap section 19.6: witnessed Work Packages, independent review and protected assets.
- Phase 1: prerequisite repository foundation for raw-source and data-model work.
- O-46 through O-50: execution authority, branch protection, handoff and stop conditions.
- O-52 and ADR-0001: monorepo, Modular Monolith, Python 3.13 and Domain independence.
- O-53 through O-56 and ADR-0002 through ADR-0005: approved component dependencies and quality toolchain.

## Preconditions and environment

- Work only in `/srv/accounting-bot/workspace` after confirming a clean `main` equal to `origin/main`.
- The server currently has neither `uv` nor Python 3.13 available on PATH. Installing `uv` in the executing user's tool directory and installing uv-managed CPython 3.13 in that user's cache are authorized development bootstrap actions for this Work Package.
- Do not use `apt`, replace system Python, create a system service, expose a port or deploy a container.
- Do not modify `ROADMAP.md`, the accepted ADRs or this Work Package.

## In scope

1. Create the accepted top-level layout:
   - `apps/local_agent`
   - `apps/server_api`
   - `apps/worker`
   - `packages/domain`
   - `packages/contracts`
   - `packages/persistence`
   - `packages/reporting`
   - `infra`
   - `tests`
   - `handoffs`
2. Configure a `uv` workspace using `src` layouts and unambiguous distribution/import names for each app and package. Use Python `>=3.13,<3.14` and create `.python-version` plus a committed `uv.lock`.
3. Declare only the component-specific dependency families already approved by ADR-0002 through ADR-0005. Keep `packages/domain` free of runtime third-party dependencies. Use environment markers for Windows-only packages such as `pywin32`; do not add an unapproved broker, cache, framework or service.
4. Configure the approved development controls: `pytest`, `pytest-asyncio`, Hypothesis, Ruff and mypy. Testcontainers may be declared but no database/container integration test is required in this package.
5. Add minimal import/smoke tests for every workspace member. Placeholder modules must contain no business rules.
6. Add an automated architecture guard that rejects imports from FastAPI, aiogram, SQLAlchemy, Playwright, Excel COM, persistence and transport layers inside `packages/domain`. Include a synthetic negative test proving the guard detects at least one forbidden import.
7. Add a GitHub Actions CI workflow with Linux and Windows jobs that installs from the committed lockfile and runs the applicable lock, formatting/lint, type and test checks without secrets or external services.
8. Add concise developer commands to the README or a dedicated development document without changing product requirements.
9. Produce and validate the required Handoff, Acceptance Matrix and test-results artifacts.

## Out of scope

- Reading, copying, opening through COM, hashing or modifying any Excel workbook.
- Creating UUIDs, raw schemas, SQLite/PostgreSQL tables, Alembic migrations or import logic.
- Accounting calculations, dates, reports, PDFs, Telegram handlers, API endpoints or queues.
- Docker Compose, Caddy, DNS, certificates, ports, services or any production/deployment mutation.
- Real accounting data, real names or phone numbers, Telegram identities, host addresses, credentials or secrets.
- Editing the Roadmap, accepted ADRs, Gate status, GitHub protection settings or `main`.
- Pushing, opening a PR, merging, deploying or continuing into another Work Package.

## Required acceptance evidence

The exact command may vary only when the selected `uv` workspace configuration requires an equivalent. Record every command and exit code in `test-results.txt`.

1. A clean bootstrap from the committed metadata succeeds with uv-managed Python 3.13.
2. The committed lockfile is current and an immutable/frozen sync succeeds for all workspace members and dependency groups.
3. Ruff formatting check and lint both pass.
4. mypy passes for all created application/package source and test code.
5. pytest passes, including workspace import smoke tests and the positive/negative Domain boundary tests.
6. `git diff --check` passes.
7. A repository scan finds no workbook/database/PDF, secret/private key, real phone number, real host address or generated environment artifact.
8. The Handoff validator exits zero for `handoffs/phase-01/wp-01-repository-toolchain-scaffold/`.
9. The final diff contains only scaffold, dependency/quality configuration, tests, CI, developer documentation and Handoff artifacts within this Work Package.
10. After Codex pushes the reviewed branch, all configured GitHub Actions jobs must pass before the Work Package can be accepted or merged.

## Stop conditions

Stop and report to Codex without expanding scope if:

- the baseline is dirty, divergent or contains an unexpected protected asset;
- an approved dependency family cannot support Python 3.13 or the Linux/Windows lock cannot be resolved;
- implementation requires Excel access, real data, a system-level installation, a running external service or a Roadmap/ADR change;
- an architecture choice would change a confirmed product boundary rather than merely express this accepted scaffold.

## Handoff contract

Follow the repository skill exactly. Commit the bounded implementation and complete:

- `handoff.md`
- `acceptance-matrix.md`
- `test-results.txt`

Run the skill validator, then report branch, commit, tests, handoff path, remaining risks and pending decisions. Stop for Codex review. Do not push, merge or deploy.
