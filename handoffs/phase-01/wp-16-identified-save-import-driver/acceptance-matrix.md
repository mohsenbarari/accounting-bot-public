# Acceptance Matrix

| Roadmap acceptance criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| WP-16 ID-01, additive API and inert import | `test_id01_public_api_and_import_inertness` | `tests/test_identified_save_import_driver.py` | PASS, Linux | Exact exports and fresh-process side-effect canary. |
| WP-16 ID-02, admission and not-due path | `test_id02_invalid_and_not_due_admission` | Focused test module | PASS, Linux | Invalid roots rejected before access; no I/O when not due. |
| WP-16 ID-03, lower-call provenance | `test_id03_successful_call_provenance` | Focused test module | PASS, Linux | Coordinator-owned path and unchanged parameters. |
| WP-16 ID-04, success and lease boundary order | `test_id04_success_boundary_ordering`; `test_id04_real_lease_failure_precedes_finish_failure` | Focused test module | PASS, Linux | Actual leased-file rehash and lease-directory removal faults; combined exceptions ordered. |
| WP-16 ID-05, direct not-ready retry | `test_id05_real_acquisition_boundaries`; `test_id05_source_not_ready_transition` | Focused test module | PASS, Linux | Identity preserved and accepted retry deadline. |
| WP-16 ID-06, reader and marker rejection | `test_id06_reader_rejection_transition` | Focused test module | PASS, Linux | No blind retry and follow-up preserved. |
| WP-16 ID-07, fatal failures | `test_id07_fatal_failure_transition` | Focused test module | PASS, Linux | Policy, storage, integrity, cleanup and unexpected paths. |
| WP-16 ID-08, multiple and cancellation failures | `test_id08_ordered_failure_preservation` | Focused test module | PASS, Linux | Ordered ExceptionGroup/BaseExceptionGroup membership. |
| WP-16 ID-09, concurrent ownership | `test_id09_concurrent_attempt_ownership` | Focused test module | PASS, Linux | Barriers, bounded events and joined workers. |
| WP-16 ID-10, four-sheet identified integration | `test_id10_real_synthetic_identity_stack` | Focused test module | PASS, Linux | Literal every-field/type oracle plus independent ADR-0006 row/sheet digests, marker and file hash. |
| WP-16 ID-11, single generation | `test_id11_single_generation_binding`; `test_id11_replacement_at_acquisition_boundary` | Focused test module | PASS, Linux | After-acquisition swap and observation, in-copy, reverification races. |
| WP-16 ID-12, architecture boundary | `test_id12_architecture_boundary_preservation` | Focused test module | PASS, Linux | Fresh interpreter installs persistence guard before import; driver AST and runtime behavior also checked. |
| WP-16 ID-13, model and mutations | `test_id13_independent_history_model`; `test_id13_targeted_mutations_are_detected` | Focused test module | PASS, Linux | At least 40 generated histories and targeted fault sensitivity. |
| WP-16 ID-14, 15,000-row scale | `test_id14_identified_15000_row_benchmark` | `test-results.txt` | PASS, Linux | 10.076 s and 78.84 MiB peak RSS; synthetic rows only. |
| WP-16 ID-15, regression and quality | `test_id15_registered_regression_gates`; repository Ruff, Mypy, pytest, lock and diff checks | `test-results.txt` | PASS locally; CI pending | 1084 collected; 1082 passed, two Windows-only skips on Linux. |

## Coverage gaps

- The corrected candidate has not received an independent non-author review; the preceding candidate was rejected with five findings and is not reused as acceptance evidence.
- Native Windows/Linux CI and mandatory Windows symlink cases require a PR run. The local `mypy --platform win32` check does not satisfy native Windows evidence.
- All evidence uses synthetic workbooks. G1 real-environment exit criteria remain open.

## Gate statement

This matrix reports implementation evidence only. It does not approve WP-16 or close G1.
