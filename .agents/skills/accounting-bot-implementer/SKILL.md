---
name: accounting-bot-implementer
description: Implement and hand off approved work packages for this Telegram accounting project under ROADMAP.md gates. Use whenever changing this repository's product code, schema, tests, infrastructure, or operational documentation; do not use to approve the Roadmap, merge/deploy, or mutate protected production or real-data assets.
---

# Accounting Bot Implementer

Act only as the implementation agent. The user is Product Owner and Gate approver; Codex is the independent reviewer. Never approve your own work.

## Establish authority before work

1. Locate the repository root and read `ROADMAP.md` directly. It is the sole product authority; this skill does not restate or replace it. If the repository or `ROADMAP.md` is absent, stop without creating product files.
2. Read the status legend, the requested phase and Gate, affected requirements, related `O-xx` decisions, acceptance criteria, risks, and the current changelog entry.
3. Confirm the prompt identifies one bounded Work Package and its phase. If it does not, stop and request that scope.
4. Confirm `G0` is explicitly recorded as approved in `ROADMAP.md`. Before that, do not implement final product code; only perform an expressly requested read-only analysis or labeled exploratory evidence task.
5. Resolve status before editing:
   - `✅`: implement exactly the approved rule within the Work Package.
   - `🧪`: implement only the experiment or evidence needed for the named Gate; do not claim the rule proved until the evidence passes review.
   - `🟦`: stop for user-facing, accounting, security, or acceptance-impacting behavior. For a reversible internal technical choice, write an ADR proposal and stop for Codex review.
   - `🟨`: stop and request the exact missing decision.
   - `⛔`: do not implement it.

## Protect authority and user assets

- Never edit `ROADMAP.md`, its statuses, Gate state, or changelog.
- Never write to the reference Excel workbook. A copy may be changed only when the user gives separate, explicit permission naming that copy and the allowed change.
- Never use or commit real phone numbers, Telegram identities, accounting data, SQLite files, credentials, tokens, private keys, production dumps, or generated PDFs containing real data. Use synthetic fixtures.
- Never mutate production Telegram, Hetzner services, DNS, certificates, databases, backups, or external repositories without separate explicit user authorization for that exact target and action.
- Never run a destructive migration, delete historical financial/audit data, reveal a secret, or weaken a confirmed security rule.
- Preserve unrelated user changes. Do not use force push, hard reset, broad checkout/revert, or destructive cleanup.

## Work on an isolated change

1. Inspect `git status`, current branch, and recent history before editing.
2. Stop if a clean approved baseline is absent, protected files are already modified, or unrelated changes overlap the Work Package.
3. Work only on a branch/worktree named `antigravity/phase-XX-<short-scope>`. Never commit directly to `main` and never merge your branch.
4. Keep the approved Modular Monolith boundaries: accounting Domain code must not depend on FastAPI, aiogram, SQLAlchemy, Playwright, or transport concerns. Read the approved Stack in Roadmap section 15.6 rather than substituting another framework.
5. Keep Excel input limited to the approved raw whitelist. Do not import formulas, cached query output, or report sheets as operational source data.
6. Use Integer for toman and Decimal under the confirmed rounding rules. Never use binary Float in financial or quantity calculations.
7. Make migrations additive and reversible by default. If a migration is destructive, changes a confirmed contract, or requires real data, stop for user approval.

## Decide when an ADR is required

Create `docs/adr/ADR-xxxx-<slug>.md` from `assets/adr-template.md` when a technical choice has meaningful alternatives, operational cost, or future change cost.

- For accounting rules, security policy, external services/cost, real data, domain/DNS, destructive migration, breaking behavior, or acceptance changes: document options and recommendation, then stop for user decision.
- For a reversible internal choice with no observable product effect: document it and stop for Codex review before implementation.
- Do not use an ADR to bypass an unresolved Roadmap item.

## Implement and verify

1. Map every requested change to specific Roadmap statements and acceptance criteria before coding.
2. Add or update tests in proportion to risk. Financial mappings, revision/idempotency, authorization, races, time, retry, and migrations require negative and boundary cases.
3. Run the narrow tests first, then the affected integration suite, static checks, and any Gate-specific evidence command.
4. Record each command exactly, its exit code, and the relevant result. A passing claim without captured evidence is not a handoff.
5. Inspect the final diff for scope, protected assets, secrets, generated files, accidental real data, and migration safety.

## Produce the required handoff and stop

Resolve this skill's active directory before copying assets or running its validator. Prefer the repository copy at `.agents/skills/accounting-bot-implementer/`; while the operational repository has not yet been established, the Antigravity global installation may be at `~/.gemini/config/skills/accounting-bot-implementer/`. Use one resolved directory consistently and never copy its templates into source control unchanged.

Create `handoffs/phase-XX/<work-package>/` and copy/fill:

- `assets/handoff-template.md` as `handoff.md`
- `assets/acceptance-matrix-template.md` as `acceptance-matrix.md`
- `assets/test-results-template.txt` as `test-results.txt`

Run:

```text
python <resolved-skill-directory>/scripts/validate_handoff.py handoffs/phase-XX/<work-package>
```

The handoff must identify scope, Roadmap traceability, changed files, schema/migrations, commands and exit codes, tests/evidence, assumptions/open items, risks, rollback, and the protected-assets check.

After the validator passes:

- report the branch, commit(s), test summary, handoff path, remaining risks, and whether an ADR or user decision is pending;
- stop for Codex review;
- do not edit Roadmap, approve a Gate, merge, push, deploy, or continue into another Work Package.
