# Mapping implementation to the thesis

| Project area | Thesis section | Evidence |
|---|---|---|
| Dataset loaders and split policy | Dataset and methodology | dataset version, checksums, split tests |
| Schema serialization/linking | Proposed method | canonical schema, M-Schema, extractive-lexical-v1, B1/B2/B6/B6R frozen configurations, linking audit and tests |
| Retrieval and DSPy | Prompt optimization | B3/B4/B5 configurations |
| Validator and refiner | Verification | B7 configuration and repair metrics |
| Guardrails and sandbox | Security | S0/S1 and adversarial evaluation |
| Experiment runner/evaluator | Results | EVAL-001 comparator, primary EVAL-003 official-result runner, optional EVAL-002 SQL audit, exact-ID summaries and frozen configurations |

`DATA-001` evidence is the pinned Spider2-Lite protocol, the database-disjoint split manifest, ADR-003 and `tests/test_dataset_protocol.py`. `DATA-003` evidence is the metadata manifest, ADR-004, checksum-gated loader, preparation CLI and `tests/test_spider2_loader.py`.

`EVAL-001` evidence is ADR-005, `docs/evaluation.md`, the isolated executor/comparator and `tests/test_execution_evaluator.py`. ADR-008 and `EVAL-003` define the primary official-result scoring path for all 31 development examples. `EVAL-002` remains an optional reference-SQL audit and must not be presented as a primary dependency because 30 reference SQL files are unavailable.

`SCHEMA-001` and `SCHEMA-002` evidence is the canonical model, stable schema hashes, read-only bounded sampling, the XiYan-compatible serializer, B1/B2 results, `docs/schema.md`, and the corresponding unit tests. `LINK-001` evidence is ADR-009, `extractive-lexical-v1`, frozen B6 artifacts, per-example selection metadata, `docs/schema-linking.md`, and the deterministic audit. B6 records 83.49% table reduction, 88.53% column reduction, 82.22% prompt-character reduction, and a completed 1/31 EVAL-003 result. `LINK-002` evidence is ADR-010, the frozen B6R config, recall policy, hybrid prompt, 31-example provider-free audit, and regression tests.

Those reduction figures are engineering measurements, not evidence of SQL accuracy or real schema recall. The project currently has trusted table/column relevance annotations only in fixtures; consequently fixture tests report precision/recall/F1, while frozen B6R supplies its own EVAL-003 claim: 6/31 (19.35%), including five non-empty and one empty-result match.

The methodology chapter must state that development decisions use a custom 31-instance SQLite development set and that the final closed evaluation target is the custom 104-instance SQLite holdout, not the full 547-instance Spider2-Lite leaderboard setting.
