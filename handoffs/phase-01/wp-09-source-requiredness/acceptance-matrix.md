# Acceptance Matrix: WP-09 Required-Field Preflight for Raw Source Snapshots (Round 1 Correction)

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-09-source-requiredness.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0012-source-requiredness.md`
- **Component Version:** `SOURCE_REQUIREDNESS_VERSION = "source-requiredness.v1"`
- **Execution Baseline:** `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` (PR #31 merged)
- **Tested Code Commit SHA:** `f142de97aec65242be8994a6b8fd73537c1f0c12`
- **Prior Delivery HEAD:** `cea3cf67baa73ff50f4a5783689506f84d8ef40e`
- **Target Branch:** `antigravity/phase-01-source-requiredness`
- **Traceability References:** Roadmap 5.2 step 3, 5.4, Sections 6/7, O-16, O-17, O-18, O-32, O-33, O-46..O-51, O-60, O-68, O-69, O-70, O-74, ADR-0012

---

## Detailed Requirement Verification (SR-01 to SR-14)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SR-01** | Exact version string `source-requiredness.v1`, public exports, function and class signatures, and inert pure library behavior. Module import does not invoke file/network I/O, start threads, or modify contracts. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr01_version_exports_and_inert_pure_library` |
| **SR-02** | Independent approved required-fields matrix covering all 16 sheet/field entries with None producing `MISSING_VALUE`. Multiple rows/fields aggregate all issues. Controlled omission of `transaction_type_raw` is detected by test suite. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr02_missing_value_for_every_required_field_across_all_four_sheets` (16 parameterized nodes), `tests/test_source_requiredness.py::test_sr02_multiple_rows_and_fields_aggregate_all_missing_issues`, `tests/test_source_requiredness.py::test_sr02_omission_of_transaction_type_raw_is_detected` |
| **SR-03** | Required text evaluation: None emits `MISSING_VALUE`, empty string and whitespace emit `BLANK_TEXT`, nonblank text and surrounding whitespace preserved verbatim. Null date emits `MISSING_VALUE`; invalid non-null date rejected upstream during snapshot construction. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr03_required_text_presence_null_blank_whitespace_and_unicode` |
| **SR-04** | Required numeric zero (`0`, `"0"`, `Decimal("0")`), accepted signed values (`"-50000"`), and valid Decimal/Integer forms count as present. Float and Boolean rejected upstream during snapshot construction. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr04_numeric_zero_signed_values_and_type_preservation`, citing upstream test `tests/test_canonical_hashing.py::test_number_canonicalization_and_type_rejections` |
| **SR-05** | Optional fields individually and together permitted null/blank (`discount_toman_raw`, `notes_raw`, `account_code_raw`, `customer_flag_raw`, `purity_raw`, `phone_number_raw`). C/D/H/HA/HS rows with blank notes produce 0 issues. Nonblank unknown names/items/codes and RS rows preserved without resolution or rejection. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr05_optional_fields_null_and_blank_permitted` |
| **SR-06** | Mixed 4-sheet snapshot produces full exact ordered issue tuple and correct checked/failed-row/issue counts. A row missing several fields counts once as failed. All valid and invalid rows retained in snapshot. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr06_mixed_four_sheet_snapshot_issue_aggregation_and_counts` |
| **SR-07** | Four present empty sheets produce 0 rows, 0 issues, and `passes_requiredness=True`. Incomplete snapshots (missing sheets) rejected upstream. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr07_four_empty_sheets_and_upstream_structure_rejection`, citing upstream tests `tests/test_source_change_plan.py::test_full_snapshot_duplicate_sheet_rejected`, `tests/test_source_change_plan.py::test_full_snapshot_unknown_sheet_rejected`, `tests/test_source_change_plan.py::test_invalid_uuid_rejected_in_row_input` |
| **SR-08** | Constructor invariants, immutability, and reason types. Enforces strict `type(self.reason) is SourceRequirednessIssueReason`, rejecting canonical strings and foreign StrEnum with `SourceRequirednessInputError`. Direct report construction on an actually failing snapshot derives exact issues and status, rejecting fabricated counters or passing flags. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr08_constructor_invariants_immutability_and_tamper_resistance` |
| **SR-09** | Error messages and repr masking without raw cell leakage. Invalid snapshot arguments to both public entry points raise typed error. Ordinary signature errors remain TypeError. Snapshot with real issue and markers in names/notes/contact asserts non-empty issues tuple and verifies markers do not leak in report/issue repr or str. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr09_error_messages_and_repr_masking_without_raw_leakage` |
| **SR-10** | Purity and repeatability without seams. Side-effect guard applied after fixture creation forbids filesystem I/O, network sockets, clock queries, and UUID generation during evaluation. Exact snapshot, row, and raw-mapping identities, values, and hashes retained. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr10_purity_repeatability_and_raw_preservation` |
| **SR-11** | XLSX Reader integration with synthetic workbooks. Missing required cell and formula-excluded cell yield `MISSING_VALUE`; blank text yields `BLANK_TEXT`; derived formula changes leave raw hashes and issues unchanged. Original source bytes asserted identical after Reader -> preflight for both generations. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr11_xlsx_reader_integration_synthetic_workbooks`, citing upstream tests `tests/test_xlsx_source_reader.py::test_r6_all_or_nothing_fourth_sheet_late_failure`, `tests/test_xlsx_source_reader.py::test_r6_read_only_integrity_and_clean_cleanup` |
| **SR-12** | Independent property oracle under sheet/row/mapping permutations with inverted UUID byte order. Single-value transitions across present/None/blank. Exact snapshot/raw/ID/hash retention with zero, signed values, and surrounding whitespace. Hypothesis randomized presence combinations across multiple rows. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr12_property_permutations_and_independent_oracle`, `tests/test_source_requiredness.py::test_sr12_property_single_value_mutation_transitions`, `tests/test_source_requiredness.py::test_sr12_property_exact_snapshot_raw_and_hash_retention`, `tests/test_source_requiredness.py::test_sr12_hypothesis_randomized_presence_combinations` |
| **SR-13** | Scale 15,000-row synthetic evaluation benchmark. Separates fixture generation timing from evaluator timing. Measured evaluator execution: 0.0396s with linear O(N) complexity and zero quadratic scans. Verifies independently expected issue ordering across sheets and retains IDs and hashes. No invented ceilings asserted. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr13_scale_15000_row_synthetic_benchmark` |
| **SR-14** | Full repository regression retention: all 350 baseline tests + 34 WP-09 tests pass cleanly (384 collected: 382 passed, 2 platform-conditional Windows skipped). Existing raw contracts registry, version constants, and sheets untouched. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr14_existing_suite_retention_and_contracts_integrity`, full test suite run |

---

## Coverage gaps

- **Linux Native:** 382 passed, 2 platform-conditional skipped Windows-handle tests (384 collected total); 34/34 WP-09 tests passing in 3.80s; 15,000-row streaming benchmarks measured at 11.02s / 61.65 MiB (WP-05) and 11.40s / 61.77 MiB (WP-06) (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Local Runtime:** Windows native execution was **NOT RUN** on this Linux development host because Windows OS binaries and filesystem semantics are unavailable. Static type safety for Windows targets is verified via `mypy --platform win32 .` (0 errors in 30 source files).
- **Windows CI Runtime:** Windows execution is **PENDING** automated CI runner verification upon PR submission.

---

## Gate statement

This matrix reports evidence only. It does not approve the Work Package or close the phase Gate. Gate G1 remains `OPEN / IN PROGRESS`.
