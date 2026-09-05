# Mapping implementation to the thesis

| Project area | Thesis section | Evidence |
|---|---|---|
| Dataset loaders and split policy | Dataset and methodology | dataset version, checksums, split tests |
| Schema serialization/linking | Proposed method | canonical schema, M-Schema, extractive-lexical-v1, B1/B2/B6/B6R frozen configurations, linking audit and tests |
| Retrieval and DSPy | Prompt optimization | RET-001/RET-002 evidence; RET-003 skeleton/operator index and audited question-plus-plan selector; completed DSPY-001 B5 evidence |
| Semantic error analysis | Error analysis and proposed method | SEM-001 checksum-pinned B1/B6R/B4/B5 matrix, frozen 27-failure labels, transition audit and dominant-cause report |
| Typed semantic planning | Proposed method | SEM-002 versioned plan, strict parser, schema/FK/JOIN validation, bounded repair and deterministic provenance hashes |
| B7P SQL composition | Proposed method | GEN-001 frozen provider-independent composer, B6R/SEM-002/RET-003 dependency binding, selective grounding, deterministic prompt/audit and fixtures |
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
`RET-003` evidence is ADR-017, the checksum-bound 7,000-entry
`sql-skeleton-operators-v1` artifact, exact-recomputing verified loader,
`QuestionPlanHybridSelector`, per-target score audit, frozen count/character
bounds, `docs/structural-retrieval.md`, and
`tests/test_structural_retrieval.py`. Its current fixture coverage proves
determinism and boundary enforcement only. The first real 31-target retrieval
audit belongs to GEN-001 because frozen development plans do not yet exist.


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

`SEM-001` evidence is the versioned labeling contract,
`text2sql.analysis.semantic_errors`, its provider-free CLI and tests, and the
checksum-bound 31-example JSONL/Markdown corpus. All 27 B5 failures have a
primary and optional secondary category. The three leading primary causes are
aggregation/grouping (6), output shape (5), and JOIN path/cardinality (4);
21 examples are stable failures across B1/B6R/B4/B5 and eight are
prompt-sensitive. No gold SQL or Spider2 test example is used.

`SEM-002` evidence is `text2sql.planning.semantic_plan`, the provider-free
validation CLI, `docs/semantic-planning.md`, and `tests/test_semantic_plan.py`.
The implementation separates relational intent from SQL composition, validates
exact schema identifiers and connected foreign-key paths, allows at most one
plan-only correction, and records deterministic plan and schema-evidence hashes.
Its fixture coverage is implementation evidence only; RET-003 now consumes its
shape, and the offline GEN-001 composer revalidates its exact record before
prompt construction.

The offline `GEN-001` evidence is ADR-018,
`configs/generation/gen001-b7p-composer-v1.toml`,
`text2sql.generation.b7p`, `docs/b7p-composer.md`, and
`tests/test_b7p_composer.py`. The frozen composer combines exact B6R schema
evidence, a checksum-valid SEM-002 record, bounded RET-003 demonstrations, and
values sampled only for plan filter columns. It proves deterministic composition
and provenance enforcement, not SQL quality. MODEL-001, the 31-example B7P run,
and EVAL-003 scoring remain required before a benchmark claim.

Those reduction figures are engineering measurements, not evidence of SQL accuracy or real schema recall. The project currently has trusted table/column relevance annotations only in fixtures; consequently fixture tests report precision/recall/F1, while frozen B6R supplies its own EVAL-003 claim: 6/31 (19.35%), including five non-empty and one empty-result match.

The methodology chapter must state that development decisions use a custom 31-instance SQLite development set and that the final closed evaluation target is the custom 104-instance SQLite holdout, not the full 547-instance Spider2-Lite leaderboard setting.
