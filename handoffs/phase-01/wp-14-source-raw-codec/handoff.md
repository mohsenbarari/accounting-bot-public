# Handoff

## Identity

- Phase: 1 — source and data-model foundation; G1 OPEN / IN PROGRESS.
- Work Package: WP-14, `source-raw-codec.v1`, RC-01 through RC-14.
- Branch: `codex/phase-01-source-raw-codec`.
- Planning baseline: `caece6836f8ab0d398ecc27459bfb6a13d669e16`.
- Approved planning commit: `621910885c3e43da9903bdec47fd2c06544194d7`, merged in PR #46.
- Execution baseline: `a80f899aeb53feeffe9928d078de7aee68bdb6a4`, clean main containing Roadmap 0.56, ADR-0017 and WP-14.
- Initial code: `80339af0b882aad1180b03fed3807ae08220fe3d`.
- Tested code after independent review R1: `f4895f992accbe6dff6f6910643962ae264343ac`.
- Delivery: the subsequent documentation-only commit containing these three Handoff files. Its actual SHA is recorded after creation in the external delivery report; this document does not predict its own commit hash.
- Implementer: Codex, applying `accounting-bot-implementer`.
- Reviewer: separate non-author Codex agent `wp14_independent_review`. Independent execution at the tested-code SHA passed; final delivery review is recorded separately after the documents are fixed.

## Scope

### Requested outcome

Encode one validated Raw row as deterministic typed UTF-8 JSON bytes and reconstruct
an immutable WP-04 row, preserving original text, exact scalar type and Decimal
sign/coefficient/exponent. Reject invalid or noncanonical payloads and semantic
hash mismatch using the fixed typed error contract.

### In scope

- Five public exports, one pure codec module and additive usage documentation.
- Seven-element envelope and registry-ordered fields prescribed by ADR-0017.
- Synthetic golden, property, composition, purity, mutation and scale evidence.
- All 783 baseline test node IDs and existing tests/helpers preserved.

### Out of scope

- Persistent schema, SQLite transactions, history/revision application, membership writes and Sync envelopes.
- Reader/runtime wiring, financial policy, operational import, enrollment, real Excel or production work.
- Changes to Roadmap, ADR/WP, existing contracts, dependency/lock files, CI or benchmark thresholds.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| 4.3, 5.2, O-79 / ADR-0017 | Design approved; implementation evidence pending PM acceptance | Versioned lossless Raw row representation distinct from canonical source hashing |
| Phase 1 / WP-14 RC-01..14 | Issued; non-author review and native CI required | Pure additive codec plus independently checked synthetic evidence |
| O-68 / WP-03 and O-69 / WP-04 | Existing accepted contracts | Reuse field/UUID/hash validation; representation-only differences remain UNCHANGED |
| O-78 / ADR-0016 | Existing membership/projection contract | Shared-party composition preserves prior Raw and archive state; no membership mutation |
| O-46 / O-49 and Gate G1 | PM governance; OPEN / IN PROGRESS | Implementation does not accept its own evidence or close the phase gate |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/contracts/src/accounting_contracts/source_raw_codec.py` | New codec and typed errors | ADR-0017 five-symbol API |
| `packages/contracts/src/accounting_contracts/__init__.py` | Five additive exports | RC-01 |
| `packages/contracts/README.md` | Usage, preservation and trust boundaries | ADR-0017 |
| `tests/test_source_raw_codec.py` | 171 collected core cases, including four input-boundary regressions | RC-01..07, RC-10..13 |
| `tests/test_source_raw_codec_composition.py` | Two composition cases | RC-08..09 |
| `tests/source_raw_codec_support.py` | Independent field order, Raw fixtures and scalar/row oracle | RC-02..10 |
| `tests/source_raw_codec_import_probe.py` | Fresh-process import guard and injected-write control | RC-01 |
| `tests/source_raw_codec_benchmark.py` | Row-by-row 15,000-row replay with full oracle | RC-13 |
| `handoffs/phase-01/wp-14-source-raw-codec/handoff.md` | Identity, scope and bounded rollback | Delivery |
| `handoffs/phase-01/wp-14-source-raw-codec/acceptance-matrix.md` | Criterion-to-evidence mapping | Delivery |
| `handoffs/phase-01/wp-14-source-raw-codec/test-results.txt` | Captured results with original log hashes | Delivery |

## Schema and migrations

- Schema impact and migration files: none. The new format is a pure row component.
- Backward compatibility: existing validators, fields, hashes, Planner and runtime are unchanged. Existing scalar subclasses remain accepted upstream; the new encoder rejects them explicitly. Valid row subclasses are accepted without promising subtype identity after decoding.
- Data migration or real data used: none. All workbook generations, row identifiers and values in tests are synthetic.

## Commands and exit codes

All commands run in the isolated checkout. `test-results.txt` records exact argv
equivalents, timestamps, output and original hashes. The pinned Python is 3.13.15.

| Command | Exit code | Purpose |
|---|---:|---|
| `uv sync --frozen --all-packages --all-groups` | 0 | Existing dependency environment; initial blocked-network attempt disclosed |
| `uv lock --check` | 0 | 88-package lock consistency |
| `uv run ruff format --check .` | 0 | 131 files formatted |
| `uv run ruff check .` | 0 | Lint |
| `uv run mypy .` | 0 | 55 source files on local Windows |
| `uv run mypy --platform win32 .` | 0 | 55 source files, static win32 typing |
| `uv run pytest tests/test_source_raw_codec.py tests/test_source_raw_codec_composition.py -v` | 0 | 173 dedicated cases |
| `uv run pytest --collect-only -q` | 0 | 956 collected cases |
| `uv run pytest --collect-only -q -o addopts=` | 0 | Exact IDs; all 783 baseline nodes retained |
| `uv run pytest -v` | 0 | 951 passed, 5 platform/privilege skips |
| `uv run pytest tests/test_source_raw_codec.py -k rc13 -v -s` | 0 | Complete 15,000-row replay |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | Existing WP-05 boundary |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | Existing WP-06 boundary |
| `uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s` | 0 | Existing WP-12 boundary |
| `git diff --check a80f899aeb53feeffe9928d078de7aee68bdb6a4...HEAD` | 0 | Fixed-baseline whitespace check |
| `python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-14-source-raw-codec` | 0 | Unchanged structural validator after all three documents are written; final result recorded externally |

Additional local scripts capture the three isolated mutation failures, public
pattern scan, scope/hash comparison, independent execution and fixed-code rollback.
These scripts and generated artifacts remain outside the Git payload.

## Tests and evidence

- `acceptance-matrix.md` maps each RC criterion to exact node names and evidence.
- Four complete golden byte literals, `GOLDEN_JSON` in `tests/test_source_raw_codec.py`, were written independently of the new codec. Their UUIDs, all field names/orders, typed scalars and accepted source hashes are explicit; tests separately verify hashes with WP-03 and compare decoded types/values/Decimal tuples.
- The three in-memory mutations each fail at the intended semantic assertion: Decimal sign/scale, original date text, and decoder semantic-hash agreement. They do not fail during setup/import. The clean tested product SHA256 is `efff3c9d8d6781ad0eba37bb01457c0963ef9b0f1a659ceae3878026b740f1b1`.
- Parent run: 173 dedicated cases in 6.26s; full local Windows run: 951 passed, 5 skipped in 80.20s. No background-thread warning was emitted.
- Non-author initial execution passed 169 dedicated cases in 6.08s and 229 additional probes. Subsequent adversarial review found R1: an invalid root's class descriptor could escape input classification. The fixed encoder inspects the actual type; four error/spoof/context/cancellation regressions failed before the fix and pass afterward, without invoking that descriptor. Refreshed independent evidence is recorded below in test results and the final delivery review.
- Non-author R1 execution at `f4895f992accbe6dff6f6910643962ae264343ac` passed all 173 dedicated cases in 6.07s, the 229 reviewer probes, and the original root-boundary reproduction. No outstanding product finding remains; fixed-document review is recorded separately.
- RC-13: 15,000 rows and 15,000 complete Planner items compared; 6,563,472 encoded bytes, fixture 1.7134080s, encode 0.1897502s, decode 0.9731411s, snapshot reconstruction 1.1848969s, Planner 0.0992505s, call-window peak RSS 120.23046875 MiB. At most one caller payload is retained; input and reconstructed snapshots are retained for the independent oracle. No new benchmark cap is imposed.
- Unchanged benchmarks: WP-05 2.8214s / 58.61 MiB; WP-06 2.9271s / 60.20 MiB; WP-12 read 2.7186293s / 82.84765625 MiB, fixture 2.0581765s separately. Existing 15s / 128-MiB gates pass.
- Original logs, argv, hashes and probes are in the ignored local directory `work/reviews/wp14-implementation-20260903/`. Published output normalizes line endings/trailing whitespace only; any derived index or excerpt is labeled. Scans are bounded pattern checks, not a guarantee about arbitrary prose.

## Assumptions and open items

- Native local Windows evidence is available. Local Linux was not run. Both mypy commands were executed on Windows; neither is native Linux evidence.
- Native Windows/Linux PR CI is pending. Four mandatory Windows symlink cases were skipped locally because the host lacks symlink privilege; the unchanged CI precondition requires them to execute. The fifth local skip is POSIX-only.
- Final non-author delivery review and PM evidence acceptance follow preparation of this package and successful native CI. Gate G1 remains open.
- Requiredness-failing but structurally valid rows are supported. Empty/blank dates rejected by the existing date contract remain invalid; this codec does not override that upstream rule.

## Risks

- Public error messages are fixed and safe; preserved diagnostic causes and tracebacks can contain Raw data and are outside that guarantee.
- The embedded source hash checks semantic agreement, not exact-byte integrity, authenticity, UUID/source association or history. Equivalent Raw representations can have different codec bytes and the same source hash without creating a revision.
- Existing canonical hash expansion and interpreter/parser resource limits remain. Per-row processing avoids a second whole-workbook payload buffer but is not an admission-control policy.
- Durable Raw/revision/membership/outbox transactions, generation freshness, crash recovery, source enrollment and migrations after format use require later work. This synthetic evidence does not prove real Excel behavior or financial validity.

## Rollback

1. Inspect `git status --porcelain` and verify the intended repository and target SHAs. Preserve unrelated work; do not reset or delete it. Use a new, verified isolated scratch checkout for rehearsal.
2. The final verified rehearsal started at `f4895f992accbe6dff6f6910643962ae264343ac` in `rollback-code-r1-checkout`, on `codex/wp14-rollback-verification`. It executed `git revert --no-edit f4895f992accbe6dff6f6910643962ae264343ac` and then `git revert --no-edit 80339af0b882aad1180b03fed3807ae08220fe3d`. The fixed baseline-to-code range contains exactly those two commits.
3. Both `git diff --quiet a80f899aeb53feeffe9928d078de7aee68bdb6a4` and `git diff --cached --quiet a80f899aeb53feeffe9928d078de7aee68bdb6a4` returned 0. The resulting Git tree exactly matched that baseline. The implementation checkout, including its untracked Handoff drafts, was unchanged. Raw commands and results are in `test-results.txt` and `rollback.log`.
4. This rehearsal covers two fixed code commits only. Later Handoff documentation is excluded. Reverting the code from a documentation tip leaves the Handoff files; it does not establish full-tree baseline equality at that tip.
5. For full delivery rollback, use the actual fixed documentation SHA from the final delivery report first, followed by `f4895f992accbe6dff6f6910643962ae264343ac` and `80339af0b882aad1180b03fed3807ae08220fe3d`, on a clean scratch branch. Inspect conflicts and compare with the fixed baseline before applying an approved reversal elsewhere. No floating baseline-to-HEAD range or recursive deletion is prescribed.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated by implementation.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is complete at the tested-code SHA and handed off for non-author
delivery review. No Gate approval, push, PR, merge, deploy or next Work Package is
included in this implementation. Publication and acceptance have their own PM
record and native CI evidence. G1 remains OPEN / IN PROGRESS.
