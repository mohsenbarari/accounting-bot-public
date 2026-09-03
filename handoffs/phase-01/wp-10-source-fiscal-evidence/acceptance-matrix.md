# Acceptance Matrix

| Roadmap acceptance criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| FE-01 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe01_public_contract_and_guarded_fresh_import` | test-results.txt: wp10 and full | PASS — Windows local | Exact six exports/signatures; normal fresh import; write injection fails with guard-specific exit 73; parent/submodule identities retained. |
| FE-02 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe02_one_year_all_transaction_sheets_both_entrypoints` | test-results.txt: wp10 and full | PASS — Windows local | All three transaction sheets, exact row/year/count oracle, both entry points and unknown/optional inputs retained. |
| FE-03 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe03_mixed_years_complete_ordered_evidence` | test-results.txt: wp10 and full | PASS — Windows local | Full independently declared mixed-year rows/counts; exact report fields exclude year-selection/permission flags. |
| FE-04 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe04_missing_dates_remain_independent_of_requiredness` | test-results.txt: wp10 and full | PASS — Windows local | All-null and mixed dates retained; explicit independent WP-09 missing-date issue identity set. |
| FE-05 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe05_empty_party_only_and_complete_transaction_deletion` | test-results.txt: wp10 and full | PASS — Windows local | Empty/party-only reports and exact 5/3 void counts; guard blocks implicit Planner; explicit Planner result unchanged. |
| FE-06 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe06_canonical_date_vectors`; `tests/test_source_fiscal_evidence.py::test_fe06_invalid_dates_fail_upstream` | test-results.txt: wp10 and full | PASS — Windows local | Eight independent accepted date vectors on all three sheets; five invalid dates rejected by ordinary upstream construction. |
| FE-07 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe07_non_date_fields_and_uuid_time_do_not_select_year`; `tests/test_source_fiscal_evidence.py::test_fe13_xlsx_generations_requiredness_and_independent_planner` | test-results.txt: wp10 and full | PASS — Windows local | Year-like names/notes/contact/amount values, opening-looking notes, changed UUID timestamps and misleading XLSX filenames do not select a year. |
| FE-08 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe08_invalid_metadata_sheets`; `tests/test_source_fiscal_evidence.py::test_fe08_invalid_metadata_identities`; `tests/test_source_fiscal_evidence.py::test_fe08_invalid_metadata_years`; `tests/test_source_fiscal_evidence.py::test_fe08_invalid_metadata_counts`; `tests/test_source_fiscal_evidence.py::test_fe08_valid_metadata_types_and_no_current_year_bounds` | test-results.txt: wp10 and full | PASS — Windows local | Fixed typed errors; real UUIDv7 retained; valid years 1/1399/1403/1405/1500/9377; no invented current-year cutoff; None only on row evidence. |
| FE-09 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe09_computed_constructor_immutability_and_private_repr` | test-results.txt: wp10 and full | PASS — Windows local | Constructor computes all fields; forged field arguments and ordinary mutation fail; raw synthetic markers absent from repr and typed input errors. |
| FE-10 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe10_property_oracle_real_permutations_and_date_transition` | test-results.txt: wp10 and full | PASS — Windows local | 35 generated cases: actual sheet/row/key reversals with order-change and value-equality assertions; one declared date-year/None transition changes exactly one metadata row and independent counts. |
| FE-11 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe11_purity_repeatability_identity_and_guard_control`; `tests/test_source_fiscal_evidence.py::test_fe11_unexpected_parser_failure_propagates_by_identity` | test-results.txt: wp10 and full | PASS — Windows local | Repeatable reports, snapshot/row/raw/value/UUID/hash identity; guarded write negative control; no implicit Planner/requiredness/hash rebuild; RuntimeError and cancellation pass through unchanged. |
| FE-12 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe12_synthetic_15000_rows_complete_oracle_and_retention` | test-results.txt: wp10 and full | PASS — Windows local | All 13,500 transaction metadata rows and year counts checked against declared oracle; 1,500 party rows; all identity/hash retention; separate fixture/evaluation timing. |
| FE-13 / ADR-0013 / O-75 | `tests/test_source_fiscal_evidence.py::test_fe13_xlsx_generations_requiredness_and_independent_planner` | test-results.txt: wp10 and full | PASS — Windows local | Four XLSX generations plus empty workbook; formula/missing dates retained; actual physical reorder and formula/cache change leave fiscal metadata and Planner unchanged; raw date edit changes one row; source bytes preserved. |
| FE-14 / WP-10 validation | Frozen sync/lock, Ruff, both full mypy modes, baseline collection/diff, full suite, original benchmark node IDs, publication scan, isolated rollback and handoff validator | test-results.txt; handoff.md | PARTIAL — native CI pending | 403 unchanged baseline nodes; 56 new cases. Local full 454 pass/5 skip. Strict symlink probe correctly fails four absent-privilege preconditions. Windows CI must execute those four; Linux CI also required. |

## Coverage gaps

- Native Linux local tests were not run; available local Docker engine is stopped/unavailable.
- Native Windows/Linux CI has not run on this implementation commit; no branch was pushed.
- Local Windows lacks symbolic-link privilege (1314). Four strict precondition failures
  are recorded, not reclassified as passes. Local-mode skips are permitted only locally.
- Non-author review remains required; the author does not independently approve this evidence.
- FE-12 algorithm inspection: one registry traversal, per-sheet UUID sorting, one row scan
  with a dictionary count update, and distinct-year sorting. No hash rebuild, raw-mapping
  copies, or nested whole-snapshot scans; O(n log n) time at most, O(n) metadata.

## Gate statement

This matrix records implementation evidence only. WP-10 is not accepted and G1
remains OPEN / IN PROGRESS. Requiredness, operational fiscal binding, archive
eligibility and permission to import/delete are outside this evidence report.
