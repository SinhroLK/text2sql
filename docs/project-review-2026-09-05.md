Project review — 5 September 2026

Reviewed commit: `30772b03e4f6fc12ed0fc7b27f26d4588b32cb0d` (`continue phase 5`). The working tree was clean at the start. This review adds only this assessment; it does not change implementation, frozen configurations, or experiment artifacts.

The project has a substantial, working research foundation and a coherent experiment history. The completed development scores are supported by local artifacts. Phase 5 is accurately described as in progress, but the new semantic-plan contract has concrete compatibility problems that should be resolved before MODEL-001. The evaluation executor also needs a small immediate isolation fix independently of the later application sandbox work.

**Verification performed**

- Ran `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`: all 156 tests passed in 7.071 seconds.
- Ran `.venv/bin/python -m pip check`: no broken requirements found.
- Checked all eight experiment reports for exact coverage of the frozen 31 development IDs.
- Compared saved generated execution rows against the pinned upstream `compare_pandas_table` function, including the official CSV round trip, development gold-result variants, and comparison settings: no per-example score differences across the eight arms. This was a scoring audit of saved executions, not a rerun of all SQL queries.
- Verified 39 distinct current resource files referenced by report records: six development databases and 33 selected development gold-result CSV files. No hash mismatches.
- Loaded the SEM-001 paired analysis through its checksum-validating API: 31 examples, 27 B5 failures, 21 stable failures, eight prompt-sensitive examples.
- Loaded and verified both 7,000-entry retrieval artifacts, including deterministic structural re-derivation, and verified the frozen B7P composer configuration and dependency hashes.
- Loaded the actual frozen B5 program through its verified loader and confirmed its pinned DSPy/LiteLLM/Optuna runtime versions.
- Inspected active generation, dataset, evaluation, schema, retrieval, optimization/recovery, analysis, planning, and CLI boundaries; reviewed documentation, tests, packaging, and legacy entry points. Legacy notebooks received static inspection rather than execution.
- Reproduced the findings below with fake providers, existing development resources, or disposable synthetic databases. No paid requests were made and no holdout outcomes were evaluated or inspected. A targeted scan found no Groq API-key patterns in tracked text; this is not a full secret-history audit.

**Prioritized findings**

1. **P1 — The mandatory declared-FK rule blocks legitimate B7P joins on most development databases.** See [semantic_plan.py](../src/text2sql/planning/semantic_plan.py), particularly lines 744–781.

   Every planned join must match a declared foreign key, and all sources must form one connected join graph. Four of the six development databases have zero declared foreign keys:

   | Development database | Tables | Declared FK entries | Development examples |
   |---|---:|---:|---:|
   | Airlines | 8 | 0 | 2 |
   | city_legislation | 15 | 0 | 10 |
   | electronic_sales | 9 | 0 | 1 |
   | f1 | 29 | 0 | 9 |
   | music | 11 | 11 | 1 |
   | oracle_sql | 38 | 35 | 8 |

   Thus 22/31 examples belong to databases where any multi-table join plan is rejected. This does not mean all 22 require joins. However, B6R's successful queries for `local171`, `local202`, and `local310` do use joins in these databases. The current contract cannot explicitly represent those successful join strategies. A more capable model cannot supply missing DDL constraints, and the one permitted repair cannot resolve this contract restriction.

   Introduce a versioned join-evidence policy that distinguishes declared constraints from validated join predicates. Preserve exact identifier checking, audit the evidence and uncertainty for undeclared relationships, and test actual development schema shapes. Do not patch the benchmark databases or insert example-ID-specific rules. Re-freeze the affected plan/composer contract before comparing models.

2. **P1 — Evaluation can create files outside its in-memory database.** See [sqlite_executor.py](../src/text2sql/evaluation/sqlite_executor.py), lines 39–54.

   `PRAGMA query_only=ON` does not prohibit `ATTACH DATABASE`. A disposable reproduction passed `ATTACH DATABASE '<temporary directory>/attached.sqlite' AS other` to `SQLiteQueryExecutor.execute()`. The method returned `execution_error/non_result_statement`, but the external file had already been created. The statement is rejected only after execution.

   Deny attachment and other forbidden operations before execution, preferably through a SQLite authorizer as well as the eventual SQL validator. Add a regression asserting that rejected statements leave no external file. This is a defect in the currently used evaluation boundary, so it should not wait for the B7P accuracy promotion gate. The existing source-database protection tests do not cover it.

   Related limitation: the timeout begins after database backup, and `fetchall()` has no row, byte, or process-memory bound. The current executor is useful evaluation infrastructure, but SAFE-002 still needs bounded execution and result materialization. Resource-limit failures should be explicit, not silently truncated results that can receive a benchmark score.

3. **P2 — A flat plan cannot validate the complex relational scopes claimed by the new pipeline.** See [semantic_plan.py](../src/text2sql/planning/semantic_plan.py), especially `SemanticPlan`, `_parse_predicate`, and the global connectivity/grouping checks; see also [test_b7p_composer.py](../tests/test_b7p_composer.py), line 356 onward.

   A synthetic plan for a valid UNION of customer names and product names is rejected as `disconnected_join_graph`: independent UNION branches do not require a join. Independent subqueries encounter the same global-source limitation. There are no structured branch/subquery scopes or window partitions in the contract; important semantics live in descriptions instead. The composer fixture called `temporal_window` represents a calendar-month filter rather than an analytic SQL window, and the set-operation fixture merely sets `union` on a single-source plan. Passing these fixtures does not establish realistic complex-query coverage.

   Validation also accepts an equality predicate with `value_kind="none"` and `value=null`, and accepts an invented aggregate function name. Both were reproduced. Consequently, the composer's instruction that the validated plan is authoritative is stronger than the actual guarantees.

   Define the supported relational scopes explicitly, give UNION/subquery branches their own sources, distinguish window operations from grouped aggregates, and validate operator/value/function combinations. Add meaningful fixtures with unrelated UNION branches, subqueries, analytic windows, self joins where supported, and complete composite join conditions. Narrow the documented claims for intentionally unsupported forms.

4. **P2 — Baseline checkpoint resume does not enforce full run identity.** See [runner.py](../src/text2sql/experiments/runner.py), `_read_checkpoint` and `BaselineExperimentRunner.run`.

   The reader checks experiment/config IDs, database IDs, and selected retrieval metadata, but does not compare stored question/schema/prompt identity with current inputs or bind the checkpoint to the original database content. In a disposable fixture, adding a column after the first run and resuming reused both old predictions without rejection. Changing only the top-level `generated_sql` to `SELECT 999` also succeeded while `generation.selected_sql` still contained the original query.

   Freeze a run manifest before generation with project revision, dirty state, dependency identity, dataset/database hashes, effective provider settings, and prompt identity; validate it on resume and require duplicate SQL fields to agree. Add single-writer ownership and an explicit recovery policy for a partially written JSONL tail. The B5 compile recovery implementation already provides useful patterns, although it should not be assumed to protect baseline runs.

   This review found no evidence that the existing reports have been corrupted: their checked resource hashes and score comparisons agree. The issue is what a future resume is allowed to accept.

5. **P2 — The direct Groq adapter silently accepts truncated completions.** See [groq.py](../src/text2sql/providers/groq.py), lines 150–179.

   A fake response containing `finish_reason="length"` and `SELECT * FROM` was accepted as a candidate. The finish reason was not retained in metadata. The baseline runner can checkpoint such output as a completed response, obscuring the difference between an incomplete transport completion and a model-generated SQL mistake. B5's `TokenAwareDSPyLM` already rejects incomplete responses, so the two generation paths apply different policies.

   Preserve finish reason and attempt metadata and define an explicit incomplete-response policy for future arms. Record failures in exact-ID evaluation rather than silently dropping them. Keep historical arm policies identifiable. The direct adapter also retries every `GroqProviderError` with fixed one/two-second waits, even though it has parsed rate-limit headers; unify structured retry handling when wiring B7P.

6. **P2 — Valid implicit foreign-key targets crash schema inspection.** See [inspector.py](../src/text2sql/schema/inspector.py), lines 53–61, and `ForeignKeySchema` in [models.py](../src/text2sql/domain/models.py).

   SQLite accepts `CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(parent_id INTEGER REFERENCES parent);`. Its FK metadata has a null target-column field. The inspector forwards that value into a string-only dataclass and raises `AttributeError: 'NoneType' object has no attribute 'strip'`.

   Resolve omitted targets against the referenced primary key, including composite-key order, and produce a structured error when resolution is impossible. This did not prevent inspection of the six current development databases, but it affects the package's general SQLite interface. Also make the policy for generated columns and views explicit before claiming complete schema coverage.

7. **P2 — CSV coercion is not fully equivalent to the official evaluation input path.** See [gold_results.py](../src/text2sql/evaluation/gold_results.py), lines 39–60, and [comparator.py](../src/text2sql/evaluation/comparator.py).

   Gold values are converted to numbers cell by cell, while generated values retain SQLite native types. The official path uses pandas CSV inference on both sides. A generated string `"001"` compared with a one-column gold CSV containing `001` scores zero locally, even though both CSVs normalize to numeric `1` in the official path. A mixed text/numeric-looking column also differs: local loading converts `001` to `1`, while pandas preserves it as text alongside other strings.

   Add differential fixtures for numeric text, mixed columns, missing-value spellings, booleans, and empty results; either match the official normalization or version and document the alternative. No score differences appeared in the eight current development arms, so this is a prospective correctness gap, not a reason to discard their recorded scores.

**Completed work and measured outcomes**

| Area | Reviewed status |
|---|---|
| Project foundation | Modular `src/text2sql` package, command-line entry points, domain types, mock provider, fixtures and tests are implemented. |
| DATA-001 / DATA-003 | Pinned Spider2 metadata, custom database-disjoint 31/104 protocol, checksum validation and exact-ID checks are implemented. |
| EVAL-001 / EVAL-003 | Working isolated-copy execution and official materialized-result scoring; current findings require hardening and normalization tests. |
| EVAL-002 | Optional reference-SQL route; missing reference SQL does not block the primary EVAL-003 path. |
| EXP-001 / SCHEMA / LINK | B0/B1/B2/B6/B6R generation, schema serialization, sampling, linking, audits and live development results are complete. |
| RET-001 / RET-002 | Verified 7,000-example Spider1 train index, fixed-random and TF-IDF selectors, audits and B3/B4 live runs are complete. |
| DSPY-001 | B5 signature, MIPROv2 compile, 21/10 database-disjoint development folds, token limiting, explicit recovery cache, frozen program and live development result are complete. |
| SEM-001 | Checksum-bound paired error corpus and labels for all 27 B5 failures are complete. |
| SEM-002 | Offline parser/validator/repair interface implemented; needs the contract corrections above before broad runtime use. |
| RET-003 | Frozen structural index and selector implemented and verified; no real 31-plan retrieval audit yet. |
| GEN-001 / B7P | Offline deterministic composer and provenance checks implemented; integrated planning/provider runner and scored 31-example checkpoint remain unfinished. |

All reported arms below use the existing gpt-oss-120b experiment family. These are development results under the custom SQLite protocol.

| Arm | Description | Correct / 31 | Accuracy | Executable / 31 | Input tokens |
|---|---|---:|---:|---:|---:|
| B0 | Question only | 0 | 0.00% | 0 | 4,951 |
| B1 | Simple full schema | 5 | 16.13% | 26 | 51,287 |
| B2 | Full M-Schema | 2 | 6.45% | 22 | 107,586 |
| B3 | Random few-shot | 4 | 12.90% | 23 | 117,785 |
| B4 | Similarity few-shot | 5 | 16.13% | 23 | 115,556 |
| B5 | DSPy | 4 | 12.90% | 28 | 121,787 |
| B6 | Aggressively linked schema | 1 | 3.23% | 27 | 18,287 |
| B6R | Recall-first schema | 6 | 19.35% | 25 | 94,140 |

B5's compile selected the original/default instruction at 2/10 validation accuracy. Its 4/31 result is a completed negative optimization result. B6R is the best observed development arm; B1 remains an important inexpensive comparator. The one-example gap between B6R and B1/B4 is not strong evidence of general improvement. B6's 27 executable queries with only one correct result and B5's 28 executable queries with only four correct results support prioritizing semantics over additional syntax repair.

SEM-001's leading B5 failure categories are aggregation/grouping (6), output shape (5), and join path/cardinality (4). The labels are valuable engineering diagnosis, not independently validated gold semantic annotations. All 31 development examples have informed design and/or optimization; the final 104-example holdout remains necessary for a generalization claim. Report database-level variation as well as aggregate accuracy because development contains only six databases, including ten city_legislation and nine f1 examples.

**Remaining implementation and research work**

MODEL-001 has not started. Beyond selecting candidate model IDs, it needs a frozen planner prompt/provider policy, repair budget, composer policy, model selection rule, and complete latency/token/cost accounting. Decide whether models share frozen plans or each supplies its own planner; these answer different comparison questions. The existing 1,024-token composer budget does not establish a budget for the longer JSON planning response. Keep model changes separately attributable from method changes by retaining an appropriate same-model baseline comparison.

GEN-001 still needs an orchestrator for planner response → validation/one repair → retrieval → composition → provider SQL response → checkpoint → EVAL-003. Record plan failures and provider failures for all expected IDs. A terminal plan failure must remain an evaluated failure in the denominator rather than turning the benchmark into a selected subset of successful plans. Preserve exact per-stage usage and lineage.

SAFE-001, the full SAFE-002 runtime, candidate selection/refinement, adversarial evaluation, final experiment runner/configuration freeze, statistical analysis, rebuilt Gradio integration, and thesis chapters remain open. The generic evaluator can accept a test split, but the baseline and B5 generation runners are deliberately development-only: opening final evaluation will require explicit runner work, not just changing a CLI flag.

The root `app.py` is legacy: it connects to MySQL at import, loads the currently empty `schema.json`, and executes model output with validation commented out. `app/README.md` correctly excludes it from the supported pipeline. The notebooks and two-column CSV experiments are historical artifacts; they do not establish present benchmark quality.

**Reproducibility and maintenance gaps**

- `requirements.lock` pins four direct dependencies only. It is not a complete transitive environment lock. Add a reproducible environment snapshot and supported Python/platform information before the final freeze; `pip check` passing verifies the current environment only.
- Ordinary reports/checkpoints lack the project Git revision and dirty-state provenance promised in the methodology. Dataset source commits are present, but they identify a different repository. Extend run manifests rather than relying on mutable prompt-version strings.
- Most prediction, report, and optimizer artifacts are ignored by Git. Their local existence and hashes were verified, but a clone alone cannot reproduce the completed paid evidence. Preserve an immutable artifact bundle with a tracked manifest and documented restore/checksum procedure; large raw datasets can remain separate.
- No checked-in CI workflow was found. Add the offline test command as a required automated check. New regression tests should cover the reproduced failures, not merely assert frozen constants or prompt section presence.
- RET-003's SQL and plan features have asymmetric coverage: nonrecursive CTEs, ordinary SELECT DISTINCT, and aggregate window functions cannot all be represented faithfully by `semantic_plan_structure`. Its temporal text field does not establish an analytic window. Address this alongside the plan extension and verify query/plan feature parity on synthetic equivalents.
- Sampling limits returned rows, but `WHERE ... IS NOT NULL ORDER BY ... LIMIT ...` does not necessarily bound rows scanned or sorting work. Add a deadline/resource policy if applying it to larger or arbitrary databases.
- Reduce status duplication among README, roadmap, architecture and thesis mapping. Remove the tracked `.orig` roadmap and clarify that the root app is archival at its entry point. These are lower priority than correctness. Several modules combine extensive configuration parsing with runtime logic; split them when modifying those areas, without rewriting frozen baseline behavior unnecessarily.

**Recommended next sequence**

1. Fix the current evaluator's external-file side effect and add its regression. Freeze the existing experiment evidence in an artifact bundle.
2. Revise SEM-002 for undeclared join evidence and relational scopes, tighten predicate/function validation, and fix implicit FK inspection. Version the changed contract and update RET-003/B7P dependency identities together.
3. Run a provider-free development compatibility audit across all six schemas plus meaningful synthetic JOIN/subquery/UNION/window/recursive cases. Establish exactly which plan forms are supported before model selection.
4. Strengthen run/checkpoint identity, completion-status handling, and scoring differential tests; build the B7P orchestrator with exact coverage and per-stage accounting.
5. Predeclare up to three MODEL-001 candidates and budgets, compare under the corrected frozen contract, and publish all attempts. Then generate and score the frozen 31-example B7P checkpoint.
6. Apply the existing promotion gate unchanged unless a separately documented methodological decision changes it before measurement: at least 8/31 correct, at least 28/31 executable, and at least two new non-empty successes relative to B6R. Only then invest in the planned candidate/refiner extension. The evaluator isolation fix is independent of this gate.
7. Complete required safety/adversarial work, freeze final code/models/configuration/environment and analysis, and run the held-out evaluation once under the registered protocol. Begin writing completed methodology and negative-result findings now rather than waiting for the final UI.
