# Acceptance Matrix

All node IDs below include every parametrized case at that node. Independent input
declarations supply expected routing and Planner actions; no production resolver
is used as its own oracle. Total dedicated execution: 71 passing cases.

| Roadmap acceptance criterion | Test or inspection | Evidence location | Result | Notes |
|---|---|---|---|---|
| SB-01 | `tests/test_source_binding.py::test_sb01_public_contract_and_guarded_fresh_import` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Nine exports/signatures, guarded fresh import and guard-specific injection failure |
| SB-02 | `tests/test_source_binding.py::test_sb02_valid_key_and_declared_year_boundaries`; `tests/test_source_binding.py::test_sb02_invalid_uuid_objects_and_types`; `tests/test_source_binding.py::test_sb02_invalid_years` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Declared year vectors, exact int and UUID object/variant/version validation |
| SB-03 | `tests/test_source_binding.py::test_sb03_exact_state_enum`; `tests/test_source_binding.py::test_sb03_invalid_archive_hashes`; `tests/test_source_binding.py::test_sb03_record_type_and_lifecycle_invariants` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Exact enum, active/archived hash combinations and private invalid input |
| SB-04 | `tests/test_source_binding.py::test_sb04_invalid_registry_containers_and_elements`; `tests/test_source_binding.py::test_sb04_duplicates_active_limit_and_defensive_one_shot_copy` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Duplicates, active limit, one-shot iteration and immutable canonical order |
| SB-05 | `tests/test_source_binding.py::test_sb05_active_selects_exact_object` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Both entry points select exact active object despite overlapping raw identities |
| SB-06 | `tests/test_source_binding.py::test_sb06_archives_do_not_invoke_operational_functions` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Archive returns no selected prior and invokes no guarded operational/I/O function |
| SB-07 | `tests/test_source_binding.py::test_sb07_unknown_and_year_mismatch` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Unknown ID never falls back by year; known ID/year mismatch is a typed error |
| SB-08 | `tests/test_source_binding.py::test_sb08_computed_fields_immutability_and_private_prior_repr`; `tests/test_source_binding.py::test_sb08_invalid_entry_inputs_have_fixed_private_errors` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | No derived-field injection; frozen/slot/index invariants and hidden prior hash |
| SB-09 | `tests/test_source_binding.py::test_sb09_empty_party_undated_and_mixed_snapshots_are_independent` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Empty, party-only, undated and mixed evidence leaves binding intact |
| SB-10 | `tests/test_source_binding.py::test_sb10_property_routing_oracle_and_real_permutations` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | 60 Hypothesis examples; independent routing oracle and actual permutations/year changes |
| SB-11 | `tests/test_source_binding.py::test_sb11_pure_construction_resolution_and_guard_control`; `tests/test_source_binding.py::test_sb11_iteration_and_internal_errors_propagate` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Purity, repeatability, negative control and exact exception identity |
| SB-12 | `tests/test_source_binding.py::test_sb12_explicit_planner_composition_never_targets_another_source` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Exact IDs/actions/revisions for unchanged/edit/void/empty/first import; archive and unknown excluded |
| SB-13 | `tests/test_source_binding.py::test_sb13_shared_party_keeps_independent_historical_revisions`; `tests/test_source_binding.py::test_sb13_large_prior_is_never_traversed_or_copied` | test-results.txt: dedicated; scale also has its own log | PASS (Windows local) | Shared permanent party/revision preservation and guarded 15,000-row scale |
| SB-14 | Full quality commands, collected counts, baseline test-blob comparison, original Reader/acquisition benchmarks, scope/publication scans and handoff validator | test-results.txt and final validation appendix | Local PASS; native CI PENDING | 459 unchanged baseline + 71 new = 530; Windows 525 pass/5 disclosed skips; Linux local not run |

## Coverage gaps

- Independent non-author review and native Windows/Linux PR CI have not run.
- Four Windows symlink cases skipped locally for privilege 1314; one POSIX-only
  case skipped as expected. Required CI capability checks are unchanged.
- Fresh import covers target execution after code/dependency loading. Purity
  guards target named seams; they are not a general process sandbox.
- SB-12 uses deliberately prepared prior views; SB-13 preserves shared-party
  history but does not prove persistence projection or global revision updates.
- Workbook marker provenance, durable enrollment/rollover, archive integrity alerts,
  real Excel and the other G1 operational prerequisites are outside this package.

## Gate statement

This matrix reports implementation evidence only. SB-14's native CI and non-author
review remain required before acceptance. It does not approve WP-11 or close G1;
G1 remains OPEN / IN PROGRESS.
