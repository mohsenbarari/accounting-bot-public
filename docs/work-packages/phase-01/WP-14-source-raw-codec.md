# WP-14: Encode and reconstruct lossless Raw row payloads

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 immutable Raw storage prerequisite; cannot close G1
- Status: Issued — implementation may start after the planning PR merges
- Issued by: Codex Project Manager
- Issued on: 2026-09-03
- Implementation workflow: `accounting-bot-implementer`; non-author review required
- Implementation branch: `codex/phase-01-source-raw-codec`
- Planning baseline: `caece6836f8ab0d398ecc27459bfb6a13d669e16`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0017;
  record its actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-14-source-raw-codec/`

## Objective and traceability

Implement the five-symbol `source-raw-codec.v1` API in
[ADR-0017](../../adr/ADR-0017-source-raw-codec.md). Encode one validated Raw row
into deterministic typed UTF-8 JSON bytes and reconstruct it without changing
its scalar types or representations. This addresses Roadmap 4.3/5.2 and O-79:
canonical hashing intentionally collapses equivalent representations, while
immutable history needs original Raw. G1 remains OPEN / IN PROGRESS.

Read ADR-0017 in full. Its seven-element envelope, scalar domain, strict grammar,
hash validation, error/cancellation rules, no-I/O boundary and preservation
criteria are normative. Decimal equality alone is not a representation oracle.
The scalar-subclass restriction belongs only to this new encoding boundary;
do not change existing contracts to reject their previously accepted inputs.

Fixtures and published evidence must be entirely synthetic. No real workbook,
real-data copy, SQLite database, production assets or credentials are accessed.
The API is additive; no Reader, Watcher, Planner or persistence path is rewired.

## Acceptance matrix

Use independently written expected trees/bytes and a scalar preservation oracle
based on literal type/value/Decimal tuples. Do not generate expected bytes with
the codec under test. Distinct node IDs must identify each criterion's cases.

| ID | Required evidence |
|---|---|
| RC-01 | Exact five exports, two signatures, version and error reasons/messages. Inert module initialization uses a scoped no-side-effect guard with a failing injected-side-effect control; distinguish dependency loading. Accept valid row subclasses; reject scalar subclasses explicitly without changing upstream validation. |
| RC-02 | Reviewed literal complete golden byte vectors for all four sheets, including UUID, existing accepted source hash and every whitelist field in the independent field-order oracle. Compare bytes and decoded fields separately. Verify each vector's hash against WP-03, without using the new codec to manufacture the expected format. |
| RC-03 | Preserve None, empty and whitespace-only admissible text, Persian/Arabic digits, distinct Unicode forms, ZWNJ, quotes/backslashes, control characters and untrimmed date/numeric strings. Requiredness-failing but structurally valid rows still round-trip; no policy evaluator is called. Reject invalid UTF-8/lone surrogates without substitution. |
| RC-04 | Preserve exact built-in int/text/Decimal types, very large valid integers, coefficient trailing zeros, signed Decimal zero and positive/negative exponents. Cover integral Decimal in integer-toman fields and negative/zero values. Assert exact Decimal tuples rather than numeric equality. Test under multiple decimal precisions/roundings/capitals/traps and preserve the caller's complete context/flags on success and failure. |
| RC-05 | Reject wrong root argument types, bytes subclasses and valid-row scalar subclasses with INVALID_INPUT. Reject malformed bytes with INVALID_PAYLOAD: empty/truncated/trailing content, BOM, unknown versions/tags, wrong array lengths, objects, JSON numbers/booleans/non-finite tokens and tag/payload mismatch. Validate UTF-8 and shapes before using contract fields. |
| RC-06 | Individually reject missing/extra/duplicate/reordered Raw fields, wrong sheet, unknown/derived fields, invalid/noncanonical UUID or hash and semantic hash mismatch. Reject integer/exponent plus signs, padding, negative zero and non-ASCII digits; reject bad Decimal sign/coefficient/exponent, noncanonical tuples and field-inadmissible values. Where relevant, recompute a matching semantic hash so the test demonstrates structural/tag validation instead of accidentally failing only the hash check. |
| RC-07 | Reject noncanonical but otherwise valid JSON spellings (whitespace/newline, escaped non-ASCII or slash). For every valid payload, re-encoding its decoded row is byte-identical. For rows with permuted input mapping order, encoding remains identical. Validate decoded mapping order and immutability; original rows/mappings remain unchanged. |
| RC-08 | Composition with WP-04 and WP-13: different numeric/date representations yield different codec bytes but the same source hash and exact UNCHANGED Planner items; a real Raw edit yields the expected EDIT/+1. Include the unchanged shared party in a new annual source and preserve archive/prior state. No test-only byte comparison becomes production revision logic; no event/membership/commit API is introduced. |
| RC-09 | Synthetic XLSX through the existing identified WP-12 reader, then row-by-row codec and complete WP-04 snapshot reconstruction. Compare every decoded Raw field, UUID, row/sheet hash and complete Planner result. Reorder rows and alter formula/cache independently; no false actions. Preserve source bytes, marker identity and lease cleanup. No real file or product runtime wiring. |
| RC-10 | Hypothesis: at least 50 generated valid cases across the four sheets and scalar representations, independent tuple/type/value oracle, round-trip/re-encode laws and actual mapping permutations. Generate malformed wire values separately. Do not restrict all Decimal examples to numerically simple forms that hide lost sign/scale. |
| RC-11 | Fixed safe typed str/repr/args exclude synthetic path, UUID, hash and Raw markers for both reasons, including decoder contract failures. Assert exact wrapped ordinary cause and unchanged KeyboardInterrupt/SystemExit identity. Reject raw reason strings/foreign StrEnums with the fixed TypeError. Disclose that preserved cause/traceback data are outside the sanitization guarantee. |
| RC-12 | Isolated controlled mutations are caught by intended semantic assertions: collapse Decimal scale/sign, canonicalize original numeric/date text, and omit decoder hash validation. Retain targeted failures and prove they are expected-value failures rather than import/setup errors. Restore code and identify the final clean tested-code SHA. Guard calls after fixture construction against I/O, clocks, UUID generation, threads and evaluators/Planner; include an injected-side-effect negative control. |
| RC-13 | Replay at least 15,000 synthetic rows row by row; independently compare the complete UUID/field/type/tuple/hash set, reconstructed snapshot and Planner items. Measure fixture, encode, decode and Planner separately, encoded byte totals and peak RSS. Evidence must not silently retain a second whole-workbook payload buffer or impose an unapproved time/size cap. Existing hash-expansion costs are disclosed. |
| RC-14 | Preserve all 783 existing collected test cases and existing test/helper files. Frozen sync/lock, Ruff, full native/win32 mypy, dedicated/full tests, all three existing 15,000-row benchmark boundaries, Handoff, whitespace and public scans pass. Native Windows/Linux CI executes the four mandatory Windows symlink cases; local privilege skips and static win32 checks are disclosed separately. |

Use controlled Events/Barriers/ACKs if integration needs concurrency; capture
background failures and clean up threads/leases in finally. No arbitrary sleeps,
test-only public switches or weakening of existing benchmark/CI gates.

## Allowed changes

- New `packages/contracts/src/accounting_contracts/source_raw_codec.py`.
- Additive exports in `packages/contracts/src/accounting_contracts/__init__.py`.
- Usage and preservation/trust boundaries in `packages/contracts/README.md`.
- New `tests/test_source_raw_codec.py` and focused new synthetic helpers/probes
  under `tests/`; all existing tests/helpers remain unchanged.
- Completed documents in `handoffs/phase-01/wp-14-source-raw-codec/`.

No implementer changes to Roadmap/ADR/WP, existing contract implementations,
Reader/acquisition/coordinator/runtime, schema/persistence, dependency manifests,
lockfile, CI or thresholds. No scalar-class serializer, whole-workbook format,
SQLite transaction, source enrollment/writer, operational import, revision apply,
Sync envelope/signature/payload_hash, financial/RS policy, archive activation,
notification or deployment. A proven conflict with an existing contract needs
a minimal reproduction and PM scope decision; do not weaken an oracle to hide it.

## Validation and delivery

Capture exact commands, raw stdout/stderr and exit status:

```text
uv sync --frozen --all-packages --all-groups
uv lock --check
uv run pytest tests/test_source_raw_codec.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest --collect-only -q
uv run pytest -v
uv run pytest tests/test_source_raw_codec.py -k rc13 -v -s
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-14-source-raw-codec
```

Run every added test file explicitly and retain all 783 baseline node IDs.
Preserve the existing WP-05/06/12 15-second / 128-MiB benchmark thresholds.
Separate local native platform evidence, static win32 typing and CI. Publish
only scanned synthetic evidence; generated files and any probe data stay out of Git.

Deliver `handoff.md`, `acceptance-matrix.md` and `test-results.txt`, mapping
RC-01..14 to exact tests/evidence. Record planning/execution/tested-code/delivery
SHAs, allowed-file diff, golden provenance, mutation/guard/scale limits and
fixed-SHA rollback. Verify rollback in a clean isolated checkout, separating
code commits from later documentation. Do not put an unknown own SHA in a commit.

Stop for non-author review. Codex-authored code needs a reviewer who did not
author it; PM evidence acceptance follows that review and successful native
Windows/Linux CI. This work proves a synthetic row format, not durable atomic
history, full Sync integrity, import eligibility, membership/rollover recovery
or actual Excel behavior. G1 stays OPEN / IN PROGRESS.
