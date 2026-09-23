# WP-16 identified save-import driver handoff

## Identity

- Phase: 1; Gate G1 remains OPEN / IN PROGRESS.
- Work Package: WP-16, `identified-save-import-driver.v1`.
- Baseline: `d41830ebb53cbf5184da58beac96f0c4e24c1d3b`.
- Branch/worktree: `antigravity/phase-01-identified-save-import-driver-review-correction` in `/srv/accounting-bot/worktrees/wp16-review-correction`.
- Tested code commit: `03d462f51d9926241a0b9b23433ef701546f4be6`.
- Implementer: Codex local corrective work after the external Gemini call was blocked.
- Reviewer: separate independent review pending for this corrected candidate.

## Scope

### Requested outcome

Implement the exact two-symbol WP-16 due-read API and correct the five findings from the preceding independent review without changing the product-module bytes of that rejected candidate.

### In scope

- Preserve the new due-read function and export exactly as proposed under ADR-0024.
- Strengthen ID-04, ID-10, ID-11 and ID-12 evidence in the focused test module.
- Correct the README example to construct absolute synthetic source and lease paths.

### Out of scope

- Persistence, runtime switching, real XLSX or accounting data, production access, and WP-17.
- Self-approval of WP-16 or closure of G1.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Phase 1 / G1 | OPEN / IN PROGRESS | Synthetic prerequisite evidence only; no Gate closure. |
| O-72, O-73, O-77; ADR-0024 | Confirmed technical sequencing | One due coordinator attempt consumes one WP-12 identified read without a persistence import or a runtime consumer change. |
| WP-16 ID-01 through ID-15 | Issued, implementation evidence pending | Focused tests, independent literal oracle, real acquisition/lease fault boundaries, regression and scale evidence recorded in the matrix. |

## Changed files

| File | Change | Reason |
|---|---|---|
| `apps/local_agent/src/accounting_local_agent/save_import_coordinator.py` | Added the identified due-read variant; SHA-256 `bc8b0ba1be338d1fd20a3f83c8e509cbe9ee1e61c23defd8af6e564029e8a759` matches the preceding candidate. | WP-16 API and state transitions. |
| `apps/local_agent/src/accounting_local_agent/__init__.py` | Exported the exact two new symbols; SHA-256 `9ce2740f65a0a653214e6bbde3bed7fc8081b611b9eedb347c7c95323fc87fa1` matches the preceding candidate. | WP-16 public API. |
| `tests/test_identified_save_import_driver.py` | Replaced reader-derived ID-10 expected data with a four-sheet literal and ADR-0006 hash oracle; injected actual lease verification and cleanup failures, three acquisition-race boundaries, and a fresh-interpreter persistence guard. | Independent review findings 1-4. |
| `apps/local_agent/README.md` | Uses paths derived from `Path.cwd()` in the synthetic example. | Independent review finding 5. |

## Schema and migrations

- Schema impact: none.
- Migration files: none.
- Backward compatibility: existing `read_due_source`, `SourceWatchRuntime`, coordinator types and prior public signatures are unchanged.
- Data migration/real data used: none; all test files are generated under temporary directories.

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `/root/.local/bin/uv sync --offline --frozen --all-packages --all-groups` | 0 | Reproducible isolated dependencies. |
| `/root/.local/bin/uv lock --check --offline` | 0 | Frozen lockfile check. |
| `.venv/bin/ruff format --check .` | 0 | Repository format gate. |
| `.venv/bin/ruff check .` | 0 | Repository lint gate. |
| `.venv/bin/mypy .` | 0 | Linux static typing, 60 source files. |
| `.venv/bin/mypy --platform win32 .` | 0 | Win32 static typing, 60 source files. |
| `.venv/bin/pytest -q tests/test_identified_save_import_driver.py` | 0 | 70 dedicated tests, including the corrected cases. |
| `.venv/bin/pytest -q` | 0 | Full regression, 1084 collected; 1082 passed and two Windows-only skips on Linux. |
| `.venv/bin/pytest -q -s tests/test_identified_save_import_driver.py -k id14` | 0 | 15,000 generated rows, 10.076 s and 78.84 MiB peak RSS. |
| `git diff --cached --check` | 0 | Changed-file whitespace gate. |
| `git revert --no-edit 03d462f51d9926241a0b9b23433ef701546f4be6` on an isolated scratch worktree | 0 | Rehearsed exact return to the baseline tree. |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Exact command outcomes and platform limits are recorded in `test-results.txt`.
- The prior independent review returned `CHANGES_REQUIRED` with five findings. The corrected candidate has not yet received a new independent review.
- The new ID-10 oracle uses literal Raw values, exact Python types and an ADR-0006 JSON/SHA-256 calculation written in the test, without calling the product reader for expected data.
- ID-11 tests observation, in-copy byte mutation and source reverification as well as the prior after-acquisition generation swap. The in-copy variant follows the accepted Windows-compatible WP-06 mutation pattern.
- ID-04 injects at `_stream_hash_leased_snapshot` and the owned lease directory's actual `rmdir`; combined lease/bookkeeping exceptions are checked in order.

## Assumptions and open items

- The external independent review of this corrected code and native Windows/Linux CI remain pending. Static Win32 Mypy is not native Windows execution.
- The automation controller remains quarantined at the previous rejected proposal; this branch is a reviewable corrective artifact, not an acceptance receipt.

## Risks

- Native Windows file-sharing behavior and CI timing remain to be observed on the PR.
- Releasing the controller before a successful independent review would bypass the WP-16 acceptance boundary and is prohibited.

## Rollback

1. Verify the target branch and a clean worktree before any reversal.
2. On an isolated scratch branch, revert only code commit `03d462f51d9926241a0b9b23433ef701546f4be6`; do not reset or clean unrelated working trees.
3. Compare the resulting tree to baseline `d41830ebb53cbf5184da58beac96f0c4e24c1d3b`. This exact rehearsal returned exit code 0 for `git diff --quiet`.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] Reference Excel workbooks and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential or private key was added.
- [x] No production Telegram, server database, DNS, certificate, backup or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

This candidate is stopped pending independent review and native CI. No Gate approval, merge, push, deploy or next Work Package has been performed.
