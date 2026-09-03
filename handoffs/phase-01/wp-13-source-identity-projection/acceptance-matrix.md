# Acceptance Matrix

Code: `3ef927dd5016e463b4eed910933f29052c8c7967`; execution baseline:
`dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`.

Test node names below are in `tests/test_source_identity_projection.py`, except
IP-11/12/14 in `tests/test_source_identity_projection_composition.py`. Full
parameterized node IDs and captured command evidence are in `test-results.txt`.

| Criterion | Test or inspection | Evidence | Result / limit |
|---|---|---|---|
| IP-01 | test_ip01_exports_constructors_and_immutability; test_ip01_guarded_fresh_import | dedicated / normal and injected-write subprocesses | PASS; target execution guarded after dependency/code loading. |
| IP-02 | test_ip02_catalog_global_heads_ignore_year_and_preserve_equal_objects; test_ip02_empty_and_archive_only_catalog; test_ip02_semantically_equal_state_subclasses_preserve_objects | dedicated / literal heads/owners, object identity and canonical tie | PASS; archive year does not select head revision; four valid subclass cases preserve canonical/active objects. |
| IP-03 | test_ip03_inconsistent_catalogs | Seven separately addressed conflict cases | PASS; lower-revision disagreements remain detectable beneath a later head. |
| IP-04 | test_ip04_exact_key_routes_and_cause | Four exact routing failures; saved exact underlying exception | PASS; archive/unknown never supply operational prior. |
| IP-05 | test_ip05_exact_projection_scope_and_no_archive_void | Complete literal scoped UUID set plus independent plan table | PASS; active tombstones retained, archive-only absences excluded. |
| IP-06 | test_ip06_new_source_party_borrows_existing_revision | Same/edited/voided head comparisons with literal revisions | PASS; unchanged 7, edit 8, voided 8 reactivates 9. |
| IP-07 | test_ip07_existing_member_transition_table | Six transitions across all four sheets, 24 cases | PASS; full selected-source deletion has no threshold. |
| IP-08 | test_ip08_relocation_precedes_owner_check; test_ip08_foreign_transaction_is_never_reused | 24 home moves and nine foreign-transaction cases | PASS; relocation precedes ownership for that row. |
| IP-09 | test_ip09_independent_multiyear_membership_evolution | A/B/C reference metadata sequence; exact states and complete plan items | PASS; UNCHANGED records new membership in the test-only commit model, then edit/void/reactivation continue 8/9/10. Not durable rollover proof. |
| IP-10 | test_ip10_invalid_roots_are_safe; test_ip10_reason_strictness_and_representations; test_ip10_cancellation_identity; test_ip10_resolver_preserves_existing_cause; test_ip10_catalog_cancellation_is_not_wrapped | Root types, five reasons, safe output, exact cancellation/cause identity | PASS; original diagnostic causes are deliberately not described as sanitized. |
| IP-11 | test_ip11_identified_xlsx_multiyear_composition | Real synthetic lease/marker/Raw/binding/requiredness/fiscal/Planner composition | PASS; shared party, actual reorder/formula/cache, exact one-edit revision, observed 1403/1404 versus declared 1405, immutable source bytes/cleanup. |
| IP-12 | test_ip12_generated_history_matches_independent_model; test_ip12_generated_conflict_below_head_is_rejected | Hypothesis: 60 valid permuted histories and 30 invalid histories | PASS; expected membership/transition tuples never use product indexes or actual plan to construct their state. |
| IP-13 | test_ip13_semantic_mutation_oracles plus isolated mutation_probe.py | Three captured expected-value assertion failures, source-byte digest unchanged | PASS; false VOID, revision reset and ownership bypass caught; child exit 1 is expected. |
| IP-14 | test_ip14_projection_never_revisits_archive; test_ip14_15000_identity_projection_benchmark | Two history-size access traps; complete 15,000-global / 11,250-projected oracle | PASS; fixture and call stages measured separately; no new speed/volume cap. |
| IP-15 | test_ip15_purity_and_injected_write_control; import subprocess guard | Files/network/clocks/UUID/thread guard plus SQLite/evaluators/Planner seams | PASS; exact WP-11 resolver allowed; guards activated after fixtures. |
| IP-16 | Frozen sync/lock, Ruff, full mypy and win32 mypy, collect/full suite, three original benchmarks, scope and public scans | Local Windows 105 dedicated; 778 passed/5 skipped full; 678 old nodes retained | LOCAL PASS; native Linux and native Windows CI still PENDING, including four mandatory Windows symlink cases. |

## Coverage gaps

- Local native Windows ran all available cases; four symlink cases skipped only
  because this account lacks SeCreateSymbolicLinkPrivilege. One other skip is
  POSIX-only. Neither is represented as native CI coverage.
- Native local Linux was not run. Both native PR CI jobs and final non-author
  delivery review remain pending at handoff creation.
- Pure metadata validation cannot prove historical completeness or durable
  source association. SQLite/outbox commit, crash recovery, source enrollment,
  real Excel marker retention and annual rollover are outside WP-13.
- Guarded target-module execution excludes dependency/code loading; mutation
  tests change isolated process memory and leave checkout bytes untouched.

## Gate statement

This matrix records local implementation evidence only. It does not approve
WP-13, authorize a commit to accounting storage or close G1. Independent review,
successful native CI and an explicit PM acceptance record are separate steps.
G1 remains OPEN / IN PROGRESS.
