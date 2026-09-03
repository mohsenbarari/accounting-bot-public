# Acceptance Matrix

WP-12 / Phase 1 / `xlsx-source-identity.v1`.
Final tested code: `8cb4fb408eeefe718ae0dcd96d41532021392b18`.
P = `tests/test_xlsx_source_identity.py`; L =
`tests/test_xlsx_source_identity_lifecycle.py`. Each function below is an explicit
pytest node under that file; all parameterized case IDs are preserved in the
derived new-node index in test-results.txt. Results are local Windows evidence,
not PM acceptance or a claim that Linux/Windows CI has already run.

| Roadmap criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| XI-01 | P::test_xi01_exports_signature_and_guarded_fresh_import | dedicated; import probe | PASS | Exact seven exports, constants, signature, result fields and identities; target execution under side-effect guard; injected write must exit 73 |
| XI-02 | P::test_xi02_literal_valid_marker_and_raw | dedicated; literal independent Raw matrix | PASS | Both namespace families and encodings, arbitrary prefix/order, alternate/root-relative internal part; literal key and complete Raw checked against standalone Reader and independent expected snapshot |
| XI-03 | P::test_xi03_missing_marker_never_guesses | dedicated; six named cases | PASS | Missing relation, orphan, missing property, empty, undated and mixed-year; standalone compatibility, no guessed key; mixed years independently asserted as 1403/1404 |
| XI-04 | P::test_xi04_duplicate_candidates | dedicated; parameterized cases | PASS | Custom relations including external/invalid competitors, relation IDs, reserved-name duplicates/case collisions, numeric pids, Overrides, duplicate ZIP names and all three metadata aliases |
| XI-05 | P::test_xi05_invalid_wire_values; P::test_xi05_declared_year_boundaries; P::test_xi05_invalid_property_profile; P::test_xi05_unrelated_values_links_and_comments_ignored | dedicated | PASS | Literal grammar/type/namespace/link rules; valid years 1,1404,1405,9377; no current-year floor; invalid and very long pids; unrelated links/values ignored |
| XI-06 | P::test_xi06_invalid_targets; P::test_xi06_package_profile; P::test_xi06_unsafe_xml_rejected; P::test_xi06_metadata_size_boundary; P::test_xi06_actual_decompression_is_bounded; P::test_xi06_malformed_xml_has_fixed_private_error | dedicated; size and bounded-read cases | PASS | Three members independently at cap minus one/exact/plus one; UTF-8/16 DTDs; compressed real-stream proxy proves actual limit-plus-one bounded read and close; no fetch/extraction |
| XI-07 | L::test_xi07_one_leased_zip_keeps_generation_a_when_live_path_becomes_b | dedicated | PASS | Real acquisition of A followed by deliberate live-path B replacement; one identical ZIP object for metadata/Raw; A key/Raw/hash/count, B preserved; no post-yield source reads |
| XI-08 | L::test_xi08_lease_mutation_never_returns; L::test_xi08_delivery_waits_for_exit_ack | dedicated; separate parameter IDs | PASS | Tamper/replacement at two lifecycle points; no result after integrity failure; explicit ZIP-close and lease-exit ACK gates; thread errors captured and joined |
| XI-09 | L::test_xi09_single_failure_identity_and_cause; L::test_xi09_zip_failures_have_fixed_typed_wrappers; L::test_xi09_independent_read_close_integrity_cleanup_failures; L::test_xi09_member_read_and_close_preserve_both_errors; L::test_xi09_corrupt_deflate_metadata_is_typed; L::test_xi09_cleanup_failure_alone_prevents_success; L::test_xi09_corrupt_raw_member_retains_package_taxonomy | dedicated; independent-review | PASS | Real and injected acquisition/read/close/integrity/cleanup failures; ordered independent errors and exact cause identities incl shared cause/cancellation; six real corrupt-DEFLATE cases close R1 |
| XI-10 | L::test_xi10_raw_invariance_and_one_edit_with_planner_oracle | dedicated | PASS | Physical row reorder, formula/cache and unrelated metadata do not alter Raw/plans; one price edit produces one declared ID/action/revision; full-byte changes independent; source read-only |
| XI-11 | L::test_xi11_explicit_binding_requiredness_fiscal_and_planner_composition | dedicated | PASS | A archive/B active/C unknown/wrong-year at same filename; exact prior identities/actions/revisions; empty B voids only B; no archive/unknown Planner calls; marker retained with mixed/undated rows |
| XI-12 | P::test_xi12_error_enum_is_exact_and_sanitized; P::test_xi12_result_invariants_and_private_repr | dedicated | PASS | Exact enum rejects equal strings/foreign enums; frozen/slotted key/Raw identities; fixed version/hash/count validation incl bool and injected fields; no path/raw leakage from new typed messages/repr |
| XI-13 | P::test_xi13_independent_marker_property_oracle | dedicated; mutation | PASS | Hypothesis at least 30 cases with real prefix/order/path/key changes and literal independent expected key; controlled constant parser fails expected-key assertion; Raw remains identical |
| XI-14 | P::test_xi14_combined_15000_row_benchmark | benchmark-xi14; independent-review | PASS | Full adapter 2.668312s / 82.304688 MiB; fixture 2.094276s separate; all 15,000 expected rows, key/hash/count, unchanged bytes and cleanup checked; existing 15s/128MiB limits |
| XI-15 | L::test_xi15_delegated_policy_and_missing_source; L::test_xi15_source_read_only_and_forbidden_downstream_guard | dedicated; import/runtime negative controls | PASS | Existing policy delegated; source/unrelated bytes intact; networking/extraction/identity generation/downstream calls forbidden; private acquisition IDs/temp writes allowed |
| XI-16 | Full scope and retained-node inspection; frozen sync/lock, Ruff, both mypy commands, dedicated/full/original benchmarks, Handoff validation and asset scans | test-results.txt; local verification artifacts | Local PASS; CI pending | 530 baseline cases retained unchanged +148 new =678; 673 pass/5 disclosed skips; 107 formatted files/43 typed sources; no scope changes to baseline tests/CI/deps/authority |

## Coverage gaps

- Native Linux was not run locally. PR CI on Linux and Windows is pending; all
  four mandatory Windows symlink cases must execute there. Four local privilege
  skips and the POSIX-only Windows skip are explicitly not passing coverage.
- The fresh import probe guards target-module execution after dependency/source
  loading. Runtime probes permit existing lease bookkeeping while denying the
  new adapter's forbidden effects. Their limits and injected controls are explicit.
- The Windows snapshot replacement test closes the ZIP at its injection seam to
  permit native pathname replacement, but exercises the live WP-06 lease's real
  integrity exit. The separate in-place tamper cases do not substitute for that
  replacement case. These are synthetic tests, not a real Excel sharing guarantee.
- New metadata corruption wraps zlib/BadZip failures with intact diagnostic causes;
  existing Reader XML/CRC classifications and the earlier WP-06 missing-content-type
  boundary remain unchanged. Public-message sanitation is not full cause redaction.
- Direct DTO construction is not attestation. Real Excel marker retention, source
  enrollment, durable membership/revision/prior projection, final import and rollover
  remain unproved. No physical source is registered or authenticated by these tests.
- Code review and its independent 148-case run passed; final delivery-document
  review follows the documentation commit. Local checks alone do not accept WP-12.

## Gate statement

WP-12 contributes the same-acquisition identity prerequisite only. It does not
close G1, authorize Commit, accept real-data behavior or start another work package.
G1 remains OPEN / IN PROGRESS. PM acceptance requires final non-author review and
successful native Windows/Linux CI for the actual PR head.
