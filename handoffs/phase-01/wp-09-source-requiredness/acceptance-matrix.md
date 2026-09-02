# Acceptance Matrix: WP-09 Required-Field Preflight for Raw Source Snapshots

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-09-source-requiredness.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0012-source-requiredness.md`
- **Component Version:** `SOURCE_REQUIREDNESS_VERSION = "source-requiredness.v1"`
- **Execution Baseline:** `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` (PR #31 merged)
- **Tested Code Commit SHA:** `46020d6f45a4af66cc52fe660829516c5c792c98`
- **Target Branch:** `antigravity/phase-01-source-requiredness`
- **Traceability References:** Roadmap 5.2 step 3, 5.4, Sections 6/7, O-16, O-17, O-18, O-32, O-33, O-46..O-51, O-60, O-68, O-69, O-70, O-74, ADR-0012

---

## Detailed Requirement Verification (SR-01 to SR-14)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SR-01** | Exact version string `source-requiredness.v1`, public exports, function and class signatures, and inert pure library behavior. Existing package and dependency boundaries, raw contract versions, and accepted source contracts remain unchanged. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr01_version_exports_and_inert_pure_library` |
| **SR-02** | For every required field of each of the four approved sheets, a valid input with that field set to `None` emits exactly the independently expected `MISSING_VALUE` issue. Multiple rows and fields aggregate all issues correctly. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr02_missing_value_for_every_required_field_across_all_four_sheets` |
| **SR-03** | Required text evaluation: `None`, empty string `""`, ASCII whitespace `"   "`, and Unicode whitespace `"\u2003\u00a0\u3000"` emit their respective distinct reasons (`MISSING_VALUE` vs `BLANK_TEXT`). Nonblank text and preserved surrounding whitespace count as present without stripping, case folding, or normalization. Null date yields `MISSING_VALUE`; non-null invalid date is rejected upstream during snapshot construction. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr03_required_text_presence_null_blank_whitespace_and_unicode` |
| **SR-04** | Required numeric zero (`0`, `"0"`, `Decimal("0")`), accepted signed values (`"-50000"`), and valid Decimal/Integer forms count as present, retaining exact raw representation and hash. `None` is missing. Float, Boolean, and nonfinite values are rejected upstream during snapshot construction. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr04_numeric_zero_signed_values_and_type_preservation` |
| **SR-05** | Optional fields individually and together may be null or blank without an issue: `discount_toman_raw`, `notes_raw`, `account_code_raw`, `customer_flag_raw`, `purity_raw`, and `phone_number_raw`. Rows with blank notes retain their presence. Nonblank unknown names, items, codes, and RS rows are not rejected merely for being unresolved. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr05_optional_fields_null_and_blank_permitted` |
| **SR-06** | A single mixed four-sheet snapshot produces the full exact ordered issue tuple and correct checked/failed-row/issue counts. A row missing several fields counts once as failed. All valid and invalid rows remain in the retained snapshot object. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr06_mixed_four_sheet_snapshot_issue_aggregation_and_counts` |
| **SR-07** | Four present empty sheets produce zero rows, zero issues, and `passes_requiredness=True`. Incomplete snapshots (missing, duplicate, or unknown sheets) and invalid UUIDs are rejected by the existing snapshot constructor. No row minimum, volume threshold, or invented activity rule. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr07_four_empty_sheets_and_upstream_structure_rejection` |
| **SR-08** | Constructors enforce valid issue metadata, sheet/required-field association, and reason/type compatibility (e.g. `BLANK_TEXT` is forbidden on numeric fields). Report direct construction derives results from snapshot; attempts to inject passing flags, omit issues, or mutate frozen instances fail with `SourceRequirednessInputError` or `FrozenInstanceError`. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr08_constructor_invariants_immutability_and_tamper_resistance` |
| **SR-09** | Invalid API argument types produce the fixed typed error without interpolating raw values, paths, names, amounts, or contact details. Markers placed in invalid arguments and sensitive raw strings in workbook notes do not leak in error messages or report `repr`. No raw cell values are stored in issue objects. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr09_error_messages_and_repr_masking_without_raw_leakage` |
| **SR-10** | Repeated evaluation gives identical issues, counts, and status, and never mutates or replaces raw mappings, values, IDs, or hashes. No random, clock, file, network, or thread seams. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr10_purity_repeatability_and_raw_preservation` |
| **SR-11** | Generated four-sheet XLSX workbook passed through WP-05 Reader into preflight: active rows with missing essential input survive extraction and are accurately reported; optional blanks pass. A formula-excluded required cell yields missing raw input (`MISSING_VALUE`); unrelated derived/formula/cache-only changes with unchanged valid Raw do not change issues or hashes. Retains read-only bytes and existing Reader failure behavior. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr11_xlsx_reader_integration_synthetic_workbooks` |
| **SR-12** | Independent property oracle defined without calling production helpers matches all issues and counts under sheet, row, and input-mapping permutations. Mutating one required value across present/null/blank changes only the expected issue. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr12_independent_property_oracle_and_permutations` |
| **SR-13** | A generated 15,000-row synthetic snapshot with a known mix of valid and incomplete rows yields exact counts and ordering, preserving identities and hashes. Evaluator duration executed sub-second (< 0.15s), demonstrating linear O(N) evaluation time with no quadratic row scans. Unchanged Reader and acquisition benchmark scopes and limits are preserved. | PASS (Linux native) | `tests/test_source_requiredness.py::test_sr13_scale_15000_row_synthetic_benchmark` |
| **SR-14** | All 350 existing collected tests retained and passed (364 total collected: 362 passed, 2 platform-conditional Windows-handle skipped). Ruff format and lint checks clean across 79 files; full mypy and win32 mypy clean across 30 source files; baseline whitespace scan clean; publication scan clean. Required native Windows and Linux CI remains mandatory for Codex acceptance. | PASS (Linux native) | Full repository suite: 362 passed, 2 skipped; WP-05 benchmark 11.00s / 62.14 MiB; WP-06 benchmark 11.12s / 61.63 MiB; mypy 0 errors in 30 files. |

---

## Coverage gaps

- **Linux Native:** 362 passed, 2 platform-conditional skipped Windows-handle tests (364 collected total); 14/14 WP-09 tests passing in 3.88s; 15,000-row streaming benchmarks measured at 11.00s / 62.14 MiB (WP-05) and 11.12s / 61.63 MiB (WP-06) (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Local Runtime:** Windows native execution was **NOT RUN** on this Linux development host because Windows OS binaries and filesystem semantics are unavailable. Static type safety for Windows targets is verified via `mypy --platform win32 .` (0 errors in 30 source files).
- **Windows CI Runtime:** Windows execution is **PENDING** automated CI runner verification upon PR submission.

---

## Gate statement

This matrix reports evidence only. It does not approve the Work Package or close the phase Gate. Gate G1 remains `OPEN / IN PROGRESS`.
