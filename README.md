# Telegram Accounting Reporting

This is the sanitized public planning snapshot for the Telegram accounting reporting project. The private operational repository remains the authoritative source for active work, approvals and deployments.

## Current status

- Phase: 0 — Roadmap and governance baseline
- G0: not approved
- Product implementation: not started
- Reference Excel modification: not authorized

No final product code may be implemented until the Product Owner explicitly approves G0 in [ROADMAP.md](ROADMAP.md).

## Public snapshot contents

- [ROADMAP.md](ROADMAP.md) is a sanitized planning snapshot and does not authorize production work by itself.
- [Architecture Decision Records](docs/adr/) capture the accepted O-52 through O-56 architecture decisions without replacing the Roadmap.
- [accounting-bot-implementer](.agents/skills/accounting-bot-implementer/SKILL.md) governs future Antigravity work packages and handoffs.

The Product Owner approves requirements, Gate transitions and merges. Antigravity implements bounded approved work packages. Codex performs independent review.

## Protected and local-only assets

Real Excel workbooks, SQLite databases, phone numbers, Telegram identities, credentials, deployment `.env` files, production artifacts and generated PDFs must never be committed. Pre-G0 inspection scripts and working artifacts remain local and are excluded by `.gitignore`; they are not the product implementation.
