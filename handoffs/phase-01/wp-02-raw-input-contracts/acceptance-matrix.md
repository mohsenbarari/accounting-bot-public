# Acceptance Matrix

| Roadmap acceptance criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| Sections 5.1, 5.2, 5.4, O-03: Four approved sheet contracts with exact stable-ID locations (`Z/P/P/D`), technical headers (`record_id`/`party_id`), raw mappings, activity columns, required headers, and derived column exclusions | Unit tests covering all 4 sheets and dedicated Stable-ID header tests | `tests/test_raw_input_contracts.py::test_buy_sell_contract_specification`, `test_receipts_payments_contract_specification`, `test_inventory_movements_contract_specification`, `test_business_parties_contract_specification`, `test_stable_id_technical_headers` | PASS | Contracts match normative tables exactly for 'خرید-فروش', 'دریافت-پرداخت', 'ورود-خروج', and 'لیست کسبه'. Technical headers validated on all 4 sheets. |
| Sections 5.2, 5.4: Unlisted columns remain excluded and unknown sheet lookup fails explicitly | Explicit lookup test & Hypothesis property test | `tests/test_raw_input_contracts.py::test_unknown_sheet_lookup_fails`, `test_property_classification_invariants` | PASS | `UnknownSheetError` raised on unknown sheet names; unlisted columns classify as `UNLISTED_EXCLUDED`. |
| Sections 5.2, 5.4: Formula cells and cached results never classify as raw input (including F/G/H in خرید-فروش, E in دریافت-پرداخت, F in ورود-خروج) | Deterministic cell classification test | `tests/test_raw_input_contracts.py::test_cell_classification_rules` | PASS | Formula cells in any column classify strictly as `FORMULA_EXCLUDED`. |
| Section 19.6, O-26: Deep registry immutability (`MappingProxyType`), structural invariant self-validation, global field uniqueness across all roles, and Excel column bounds (A-XFD) | Immutability, field collision, and column boundary tests | `tests/test_raw_input_contracts.py::test_immutability_and_frozen_contracts`, `test_contract_validation_rejects_field_name_collisions`, `test_excel_column_boundary_validation`, `test_contract_validation_rejects_invalid_definitions` | PASS | Registry is deeply immutable; field name collisions across Stable/Raw/Derived are rejected with `ContractValidationError`; column addresses strictly bounded to `A` through `XFD` (rejecting `XFE`, `ZZZ`). |
| Section 5.5, O-25: No Float kind exists and exact assignments for integer_toman, decimal, raw_text, uuid7 | ValueKind validation test | `tests/test_raw_input_contracts.py::test_no_float_kind_in_any_contract` | PASS | `ValueKind` contains strictly `raw_text`, `integer_toman`, `decimal`, and `uuid7`. No Float kind exists. |
| Section 19.6: Toolchain and test suite regression verification | `uv lock --check`, `uv sync --frozen --all-packages --all-groups`, `uv run pytest -v` | Pytest CLI output | PASS | 36 tests passed (14 architecture guard + 7 import smoke + 15 raw contract unit & property tests). |
| Section 19.6: Code formatting and strict type checking | `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy .` | Ruff and mypy CLI output | PASS | 32 files formatted, strict mypy passed with zero issues in 11 source files. |
| Section 19.6: Clean Git diff without whitespace or line-ending defects | `git diff --check origin/main...HEAD` | Git CLI output | PASS | Exited with code 0 and zero whitespace defects. |
| O-47, O-51: Repository clean of workbooks, SQLite DBs, PDFs, real identities, secrets, IPs | Tracked files extension scan & pattern search | `git ls-files` & regex scan | PASS | No sensitive assets, real phone numbers, credentials, IPs, or workbooks tracked. |
| O-48: Handoff validation | Skill handoff validator | `validate_handoff.py` | PASS | Handoff directory passes validator with zero errors. |

## Coverage gaps

- None. All in-scope contract definitions, validation invariants, deep immutability, field name uniqueness, Excel column bounds, classifications, and tests for WP-02 are implemented and verified.

## Gate statement

This matrix reports evidence only. Phase 1 is authorized but Gate G1 remains OPEN. This package does not approve the Work Package or close the phase Gate.
