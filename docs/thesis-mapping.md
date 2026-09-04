# Mapping implementation to the thesis

| Project area | Thesis section | Evidence |
|---|---|---|
| Dataset loaders and split policy | Dataset and methodology | dataset version, checksums, split tests |
| Schema serialization/linking | Proposed method | canonical schema, M-Schema, extractive-lexical-v1, B1/B2/B6/B6R frozen configurations, linking audit and tests |
| Retrieval and DSPy | Prompt optimization | RET-001/RET-002 evidence; completed DSPY-001 B5 compile, frozen program/manifest, 21/10 optimization split, 31-example result and tests |
| Validator and refiner | Verification | B7 configuration and repair metrics |
| Guardrails and sandbox | Security | S0/S1 and adversarial evaluation |
| Experiment runner/evaluator | Results | EVAL-001 comparator, primary EVAL-003 official-result runner, optional EVAL-002 SQL audit, exact-ID summaries and frozen configurations |

`DATA-001` evidence is the pinned Spider2-Lite protocol, the database-disjoint split manifest, ADR-003 and `tests/test_dataset_protocol.py`. `DATA-003` evidence is the metadata manifest, ADR-004, checksum-gated loader, preparation CLI and `tests/test_spider2_loader.py`.

`EVAL-001` evidence is ADR-005, `docs/evaluation.md`, the isolated executor/comparator and `tests/test_execution_evaluator.py`. ADR-008 and `EVAL-003` define the primary official-result scoring path for all 31 development examples. `EVAL-002` remains an optional reference-SQL audit and must not be presented as a primary dependency because 30 reference SQL files are unavailable.

`SCHEMA-001` and `SCHEMA-002` evidence is the canonical model, stable schema hashes, read-only bounded sampling, the XiYan-compatible serializer, B1/B2 results, `docs/schema.md`, and the corresponding unit tests. `LINK-001` evidence is ADR-009, `extractive-lexical-v1`, frozen B6 artifacts, per-example selection metadata, `docs/schema-linking.md`, and the deterministic audit. B6 records 83.49% table reduction, 88.53% column reduction, 82.22% prompt-character reduction, and a completed 1/31 EVAL-003 result. `LINK-002` evidence is ADR-010, the frozen B6R config, recall policy, hybrid prompt, 31-example provider-free audit, and regression tests.

`RET-001` evidence is ADR-011, the pinned Spider 1.0 train config, the
version-controlled 7,000-entry manifest, the checksum-verifying index loader and
`tests/test_retrieval_index.py`. The audit proves zero exact ID, database and
normalized-question overlap with the complete frozen Spider2 metadata.
`RET-002` evidence is ADR-012, frozen B3/B4 configs, the shared few-shot
M-Schema prompt, deterministic fixed-random/TF-IDF selectors, runner metadata,
and two 31-target provider-free audits. Frozen live results add B3 4/31 and B4
5/31 with 23/31 executable outputs in both arms. B4 improves on random by one
example but remains below B6R; RET-002 is evidence of a modest retrieval effect,
not strong absolute model quality.

`DSPY-001` implementation evidence is ADR-013/ADR-014, the frozen
`configs/optimization/dspy001-b5.toml`, `B5TextToSQL` signature, MIPROv2
wrapper, execution-result metric, development-only gold-result scope, 31-target
offline audit, and `tests/test_dspy_b5.py`. Token and recovery evidence adds
the frozen rolling TPM policy, strict per-run replay cache,
`tests/test_dspy_rate_limit.py`, and `tests/test_dspy_recovery.py`. It covers
wait/retry accounting, explicit compatible resume, stochastic-key separation,
integrity/secret/concurrency rejection, and non-cacheable truncations. Runtime
dependency evidence pins and preflights Optuna 4.9.0 before paid requests. The
completed compile selected the original/default instruction with 2/10
validation accuracy. The frozen 31-example B5 run scored 4/31 (12.90%) with
28/31 executable queries; it is a negative optimization result below B4 at
5/31 and B6R at 6/31.

Those reduction figures are engineering measurements, not evidence of SQL accuracy or real schema recall. The project currently has trusted table/column relevance annotations only in fixtures; consequently fixture tests report precision/recall/F1, while frozen B6R supplies its own EVAL-003 claim: 6/31 (19.35%), including five non-empty and one empty-result match.

The methodology chapter must state that development decisions use a custom 31-instance SQLite development set and that the final closed evaluation target is the custom 104-instance SQLite holdout, not the full 547-instance Spider2-Lite leaderboard setting.
