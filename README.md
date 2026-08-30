# Telegram Accounting Reporting

This public repository is the governed operational source for the Telegram accounting reporting project.

## Current status

- Phase: 1 — source and data-model foundation
- G0: approved on 2026-08-30
- Product implementation: Work Packages 01 and 02 accepted and merged; Work Package 03 issued; G1 remains open
- Reference Excel modification: not authorized

Product implementation starts only after the Codex Project Manager records an evidence-backed G0 approval in [ROADMAP.md](ROADMAP.md). G0 is now approved; later work remains bounded by phase Gates and protected-asset rules.

## Sources of authority

- [ROADMAP.md](ROADMAP.md) is the sole product and phase authority.
- [Architecture Decision Records](docs/adr/) capture the accepted O-52 through O-56 architecture decisions without replacing the Roadmap.
- [Work Package 01](docs/work-packages/phase-01/WP-01-repository-toolchain-scaffold.md) is the first accepted implementation package and a foundation contribution to G1.
- [Work Package 02](docs/work-packages/phase-01/WP-02-raw-input-contracts.md) is accepted and merged: it establishes the versioned, immutable and formula-safe raw-input contract for the four approved sheets.
- [ADR-0006](docs/adr/ADR-0006-canonical-source-hashing.md) fixes the versioned canonical byte and source/snapshot hashing contract.
- [Work Package 03](docs/work-packages/phase-01/WP-03-canonical-source-hashing.md) is issued for deterministic source canonicalization, Jalali dates and change-detection hashes; it awaits Antigravity implementation and Codex review.
- [accounting-bot-implementer](.agents/skills/accounting-bot-implementer/SKILL.md) governs future Antigravity work packages and handoffs.

The Owner defines the product vision, performs final acceptance testing and reports corrections. Codex acts as Project Manager and independent reviewer, maintaining the Roadmap and deciding work-package acceptance, Gates and merges. Antigravity only implements bounded approved work packages and cannot self-approve.

## Protected and local-only assets

Real Excel workbooks, SQLite databases, phone numbers, Telegram identities, credentials, production artifacts and generated PDFs must never be committed. Pre-G0 inspection scripts and working artifacts remain local and are excluded by `.gitignore`; they are not the product implementation.

Deployment hostnames, IP addresses and secrets belong only in ignored local environment files or the deployment secret store. Public examples use placeholders and synthetic data.

## Development and Quality Toolchain

This repository uses Python 3.13 and `uv` for workspace dependency management.

### Setup and Bootstrap

```bash
# Install Python 3.13 via uv
uv python install 3.13

# Install all workspace dependencies and groups
uv sync --all-packages --all-groups
```

### Quality and Testing Commands

```bash
# Format and lint checks
uv run ruff format --check .
uv run ruff check .

# Static type checking
uv run mypy .

# Run test suite
uv run pytest

# Verify lockfile consistency
uv lock --check
uv sync --frozen --all-packages --all-groups
```
