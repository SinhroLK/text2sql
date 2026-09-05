# Architecture decision records

## ADR-001 - Standard-library-only Phase 0

- **Date:** 2026-08-13
- **Status:** accepted
- **Problem:** The initial project depended on API keys, a real MySQL database and mutable notebook state.
- **Decision:** Phase 0 uses only the Python standard library, a deterministic mock provider and a synthetic SQLite fixture.
- **Reason:** The repository must install and test without secrets or external services before research components are added.
- **Consequences:** Phase 0 results are infrastructure checks and must not be reported as model performance.

## ADR-002 - No SQL execution in Phase 0

- **Date:** 2026-08-13
- **Status:** accepted
- **Problem:** The original demo executed unvalidated model output.
- **Decision:** The new CLI generates and records SQL but cannot execute it.
- **Reason:** Execution becomes available only after AST validation, allowlists and a read-only sandbox are implemented.
- **Consequences:** Execution Accuracy is deferred until the official evaluator is implemented.

## ADR-003 - Spider2-Lite SQLite database-disjoint research split

- **Date:** 2026-08-13
- **Status:** accepted
- **Problem:** Spider2-Lite is the recommended modern benchmark, but it is multi-dialect, partly cloud-hosted and has no official train/development/test split. Using all public examples during prompt development would invalidate final evaluation.
- **Options:** use the full 547-example cloud benchmark immediately; use a custom SQLite-only research split; keep Spider 1.0 as the headline benchmark.
- **Decision:** Pin official Spider2 commit `cafb867313aab4e674652054198f383cf4018943` and use its 135 SQLite examples. Split them deterministically by database into 31 development and 104 test examples. Use Spider 1.0 train only as the future retrieval corpus; never put Spider2 examples in retrieval.
- **Reason:** This preserves a current and difficult headline benchmark while making the first implementation reproducible without cloud credentials or query costs. Database-level separation prevents schema leakage.
- **Consequences:** Results must be labelled `Spider2-Lite SQLite custom DB-disjoint test split`; they are not directly comparable with the full 547-example leaderboard. The public test may have appeared in model pretraining. BigQuery/Snowflake remain optional extensions. Any use of ground-truth tables must be a separate ablation marked `oracle tables`.

## ADR-004 - Metadata-only, checksum-gated Spider2-Lite loader

- **Date:** 2026-08-17
- **Status:** accepted
- **Problem:** DATA-001 froze source identity and split IDs, but runtime ingestion could still load a changed file, misroute an ID or accidentally expose gold SQL.
- **Options:** trust any local JSONL; copy the complete upstream dataset into Git; implement a strict metadata-only loader against the pinned source.
- **Decision:** Verify the pinned JSONL checksum before parsing, validate all upstream/platform counts, require exact coverage of the frozen 135 SQLite IDs and DB-disjoint split, reject gold-like fields, and serialize only normalized metadata. Freeze the expected output hash and dataset manifest in `configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json`.
- **Reason:** This makes ingestion deterministic and enforces the DATA-001 leakage policy without committing licensed/large raw data or prematurely implementing evaluation.
- **Consequences:** `DATA-003` can prepare questions and metadata, but cannot execute or score SQL. Database archives, gold-result handling and the official comparator remain the separate `EVAL-001` responsibility.

## ADR-005 - Standard-library Spider2-compatible SQLite evaluator

- **Date:** 2026-08-17
- **Status:** accepted
- **Problem:** The pinned official evaluator depends on pandas and cloud libraries, executes SQLite through a CSV round trip, does not enforce its advertised SQLite timeout, and normally scores only the intersection of supplied and gold IDs.
- **Options:** import the complete official script and its cloud dependencies; invent a strict row/table equality metric; implement a small compatibility layer for the official comparison semantics plus an exact-coverage firewall.
- **Decision:** Execute generated and reference SQL in independent read-only in-memory SQLite copies, reproduce the official column-vector comparison, `condition_cols`, `ignore_order`, NULL normalization and `1e-2` numeric tolerance using the Python standard library, and reject incomplete aggregate coverage.
- **Reason:** This preserves headline-metric compatibility for the SQLite research scope while keeping tests offline, avoiding unused cloud dependencies and preventing source-database mutation.
- **Consequences:** The evaluator core can score supplied generated/reference pairs and produce Execution Accuracy summaries. Full official holdout execution still requires separately acquired SQLite databases and protected evaluation artifacts. AST validation and production sandbox controls remain later tasks.

## ADR-006 - Strict EVAL-002 resource boundary and blocked completion

- **Date:** 2026-08-18
- **Status:** superseded by ADR-008 for primary scoring; retained for optional SQL audits
- **Problem:** DATA-003 and EVAL-001 were implemented, but the roadmap marked Phase 1 done without proving a real 31-example integration run. The pinned repository exposes comparison metadata and execution-result CSVs for the development IDs, only one of their 31 reference SQL files, and no downloaded SQLite archive.
- **Options:** synthesize missing gold SQL; silently compare against public CSVs; weaken the phase criterion; implement the integration boundary and keep the task blocked until the declared SQL-to-SQL contract can be run.
- **Decision:** Add an exact `db_id -> <db_id>.sqlite` resolver, an evaluation-only protected SQL store, strict prediction/reference coverage, per-resource hashes and a runner that calls EVAL-001. Never expose gold SQL through DATA-003 or the model pipeline. Keep `EVAL-002` and Phase 1 `BLOCKED` until all six development databases and all 31 development reference SQL files are present and the real run succeeds.
- **Reason:** A fixture-only success is not evidence that the pinned benchmark integration is reproducible. Synthesized references or an undocumented metric substitution would invalidate the experiment contract.
- **Consequences:** The code is ready to run as soon as authorized resources are added locally. `LLM-002` may proceed offline independently; only benchmark scoring remains gated by EVAL-002.

## ADR-007 - Offline provider progress independent of evaluation resources

- **Date:** 2026-08-18
- **Status:** accepted
- **Problem:** Missing protected EVAL-002 resources blocked scoring but did not technically prevent provider integration.
- **Decision:** Develop and test the Groq adapter, CLI selection, audit metadata and bounded retries offline. Keep live benchmark scoring gated by EVAL-002 and require explicit authorization and GROQ_API_KEY for a smoke request.
- **Reason:** This preserves evaluation integrity without allowing an external resource blocker to halt independent engineering work.
- **Consequences:** openai/gpt-oss-120b is the active candidate in configuration; the deprecated Llama 3.3 ID remains disabled for provenance. No model result exists until a successful authorized request is recorded.

## ADR-008 - Official materialized results are the primary evaluation reference

- **Date:** 2026-08-18
- **Status:** accepted
- **Problem:** The pinned snapshot provides official result CSV variants for all 31 development examples but only one reference SQL file. Requiring unavailable SQL blocks scoring even though the official evaluator explicitly supports execution-result mode.
- **Decision:** Use the pinned official `exec_result` CSV variants plus `condition_cols` and `ignore_order` metadata as the primary EVAL-003 reference. Execute generated SQL once in isolated SQLite, accept a match against any paired official variant, hash every database/CSV/metadata resource, and preserve EVAL-002 as an explicit optional SQL audit mode.
- **Reason:** This uses a complete, versioned official evaluation artifact and preserves the model/evaluation boundary without fabricating SQL.
- **Consequences:** Phase 1 and development preflight no longer depend on the 30 unavailable SQL files. Results remain labelled as the custom DB-disjoint SQLite split, not the full Spider2 leaderboard.


## ADR-009 - Deterministic lexical linker with recall-safe fallback

- **Date:** 2026-08-30
- **Status:** accepted
- **Problem:** Full M-Schema B2 doubled input context relative to B1 and reduced development accuracy, but a learned extractive linker would add training data, dependencies, and a new model before the basic linking hypothesis had been isolated. Missing reference SQL also prevents trustworthy aggregate schema gold labels for 30/31 development examples.
- **Options:** wait for a trained paper reproduction; use another paid LLM as a linker; filter only tables; implement a transparent lexical/value extractor with structural closure; continue sending the full schema.
- **Decision:** Implement versioned **extractive-lexical-v1** scoring over question, canonical identifiers, and already allowed bounded samples. Select a bounded number of direct tables/columns, retain PK/FK endpoints, add deterministic shortest FK paths, and use the complete schema when nothing reaches the threshold. Audit every score/selection and freeze an independent B2-plus-link B6 prototype before retrieval/DSPy.
- **Reason:** This provides a cheap, deterministic and inspectable baseline that directly tests context reduction, preserves joinability, prevents silent no-match data loss, and does not cross the gold/test boundary.
- **Consequences:** The offline audit can report table/column/prompt reduction but not SQL correctness. Fixture annotations support linking precision/recall/F1; the real 31-example claim must rely on EVAL-003 after an authorized live B6 run. Any trained, embedding-based, or B5-combined linker requires a new version/config rather than modifying the frozen v1 result.


## ADR-010 - Preserve failed B6 and introduce a recall-first B6R arm

- **Date:** 2026-08-30
- **Status:** accepted
- **Problem:** Frozen B6 reduced input tokens but scored 1/31. Several B1-correct examples lost required tables or columns during linking, and the sole B6 match was a dummy empty query. Editing B6 in place would destroy reproducibility.
- **Options:** tune the frozen B6 policy in place; abandon linking; add only stricter prompt wording; create a new recall-first hybrid arm; move directly to retrieval.
- **Decision:** Keep B6 and its artifacts unchanged. Add separately versioned B6R with up to eight direct tables, every column in selected tables, a complete compact schema identifier inventory, linked detailed M-Schema as priority context, and explicit SQLite/single-read-only-query/no-dummy instructions. Keep model and generation settings unchanged for the first live comparison.
- **Reason:** This directly addresses the observed identifier omissions while preserving a controlled comparison and the evidentiary value of the negative B6 result.
- **Consequences:** B6R sacrifices most B6 compression and may exceed full B2 on a whitespace-token proxy. Its offline audit proves context construction, not accuracy. B6R requires a new 31-example development run and may not be tuned against the sealed 104-example test split.
- **Outcome:** The frozen B6R run scored 6/31 (19.35%) versus B1 5/31, B2 2/31, and B6 1/31. The recall repair recovered five B6 failures but remains too weak for final use without retrieval/validation work. The test split remained sealed.

## ADR-011 - Checksum-gated Spider 1.0 train-only retrieval corpus

- **Date:** 2026-08-30
- **Status:** accepted
- **Problem:** Few-shot experiments need labeled SQL demonstrations, but using Spider2 development/test examples or the legacy two-column CSV would introduce self-retrieval, schema leakage, or unverifiable provenance.
- **Options:** retrieve from Spider2; reuse the legacy CSV; ingest mutable third-party mirrors; pin the official Spider 1.0 training archive and enforce a Spider2 firewall.
- **Decision:** Pin the official Yale LILY Spider archive, use only `train_spider.json`, verify the archive/train/schema hashes and expected 7,000-example/140-database counts, and serialize deterministic source-ordinal entries. Before construction, require exact frozen Spider2 metadata coverage and reject any Spider2 ID, case-folded database, or normalized-question overlap. Verify generated indexes by manifest checksum before consumers load them.
- **Reason:** Spider 1.0 train is the protocol-approved external labeled corpus. A fail-closed firewall makes the no-Spider2 rule executable and auditable instead of relying on naming conventions.
- **Consequences:** RET-001 is reproducible and safe as a candidate corpus, but it does not select examples or change prompts. RET-002 must version random/similarity policies and log retrieved IDs per target. Exact normalized-question checks do not prove the absence of semantic near-duplicates or LLM pretraining contamination; those remain methodology limitations.

## ADR-012 - Freeze fixed-random B3 and TF-IDF cosine B4 selectors

- **Date:** 2026-08-30
- **Status:** accepted
- **Problem:** RET-002 needs an auditable control for the effect of demonstrations and a question-sensitive retrieval arm without adding an unpinned embedding service, model download, or dependency.
- **Options:** sample new random examples per target; use one seeded fixed sample; introduce an external embedding model; use SQL skeleton similarity; use deterministic TF-IDF cosine similarity over training questions.
- **Decision:** B3 uses one `random-fixed-v1` sample with `k=3` and seed 42 for every development target. B4 uses `tfidf-cosine-v1` with `k=3`, corpus-fitted IDF over normalized Spider 1.0 train questions, cosine ranking, and retrieval-ID tie-breaking. Both use the same full M-Schema few-shot prompt and checksum-verified RET-001 index.
- **Reason:** Fixed B3 isolates the presence of demonstrations from target-aware selection. TF-IDF provides a transparent, deterministic vector-space similarity baseline that runs with the standard library and can be fully reconstructed from the frozen index.
- **Consequences:** B4 measures lexical question similarity, not semantic embedding quality or SQL-structure similarity. Every selection must record target identity, retrieved IDs/databases, ranks, scores, index/manifest hashes, strategy, `k`, and seed. Provider-free audits establish determinism and coverage only; SQL accuracy requires separately authorized live B3/B4 development runs.
- **Outcome:** Frozen B3 scored 4/31 and B4 scored 5/31, with both arms producing executable SQL for 23/31 targets. TF-IDF similarity adds `local272` over the fixed-random control but does not outperform the earlier B1 result and remains below B6R 6/31. RET-002 is complete; B4 is the input baseline for DSPY-001 rather than evidence that lexical retrieval alone is sufficient.


## ADR-013 - Freeze an explicit development-only MIPROv2 B5 protocol

- **Date:** 2026-09-01
- **Status:** accepted
- **Problem:** DSPY-001 must improve the frozen B4 prompt program without letting moving optimizer defaults, Spider2 gold SQL, database overlap, or the sealed test split influence B5.
- **Options:** use MIPROv2 `auto` defaults; optimize and validate on randomly mixed examples; provide gold SQL as labeled demonstrations; freeze an explicit budget with database-disjoint development folds and execution-only feedback.
- **Decision:** Pin DSPy 3.3.1, LiteLLM 1.99.0, Optuna 4.9.0, and the B4 config checksum. Fail preflight before paid calls if any runtime dependency is missing or mismatched. Use MIPROv2 with 3 candidates, 5 trials, zero DSPy-level bootstrapped demonstrations, zero labeled demonstrations, no minibatching, one thread, and seed 42. Train on 21 development examples from Airlines, city_legislation, music, and oracle_sql; validate on 10 from electronic_sales and f1. Scope official gold-result loading to the allowed development IDs and expose only execution correctness to the optimizer. Pin an 8,000-TPM rolling limiter with a 0.90 safety margin; reserve counted input plus maximum output before every shared task/prompt-model call, reconcile with observed usage, and honor provider retry delays. Keep program-aware and tip-aware proposers, but disable data-aware and few-shot-aware proposers so MIPROv2 never concatenates multiple complete B4 contexts into an impossible single-tier request.
- **Reason:** A small explicit budget is reproducible and affordable, database-level separation reduces within-schema validation leakage, and execution results supply task feedback without exposing protected SQL.
- **Consequences:** The offline audit can prove data boundaries, prompt identity, and artifact contracts, but it cannot claim B5 accuracy. The on-demand tier may make compilation slow because large B4 contexts can require roughly one request per rolling minute, but the run now waits instead of repeatedly failing or silently exceeding the declared budget. The task remains in progress until a separately authorized paid compile is frozen and B5 is run over the same 31 development IDs as B3/B4. Any budget, fold, DSPy version, or base-B4 change requires a new optimization identifier.
- **Outcome:** MIPROv2 returned the original/default instruction with 2/10 validation accuracy. The frozen 31-example B5 run scored 4/31 (12.90%) with 28/31 executable queries, below B4 5/31 and B6R 6/31; DSPY-001 is complete as a negative result.

## ADR-014 - Run-scoped replay cache for interrupted B5 optimization

- **Date:** 2026-09-03
- **Status:** accepted
- **Problem:** MIPROv2 can take hours under the 8,000-TPM tier, but DSPy does not persist an in-place Optuna study. Restarting after interruption repeats paid calls; enabling a shared generic cache could instead contaminate independent experiments, collapse stochastic candidates, replay failures/truncations, hide provider use, or load stale/corrupt data.
- **Options:** always restart without recovery; enable DSPy's shared default cache; serialize private optimizer internals; add a strict per-run LM replay cache and restart deterministic orchestration.
- **Decision:** Every plain B5 optimize command creates a unique cache and only an explicit `--resume-run-id` may reopen it for 72 hours. Bind it to config/base-config, code, dependencies, model parameters/endpoint, full prompt hashes, folds, dataset, databases and gold-result resources. Preserve `rollout_id` in request keys. Cache only non-empty `finish_reason="stop"` responses; never cache 429s, provider errors, empty or truncated responses. Use restricted deserialization, a response-hash/two-phase ledger, credential redaction and scanning, a single-process lease, and fail-closed identity/integrity/eviction checks. Recompute execution metrics on replay, account cache hits separately, and leave final B5 evaluation uncached.
- **Reason:** This recovers the expensive deterministic prefix of one interrupted scientific run without allowing cached responses to become evidence for an independent rerun.
- **Consequences:** Resume restarts seeded MIPROv2 and fast-forwards through exact LM responses; it is not an exact serialized instruction pointer or Optuna database. A crash before a response is durably stored repeats that call. If the provider changes an unversioned backend during the 72-hour window, mixed old/new outputs remain possible and must be disclosed; starting a fresh run is the conservative choice when provider drift is suspected. Cache artifacts are local, ignored by Git, and are not publishable model results.

## ADR-015 - Improve first-pass semantic construction before adding a refiner

- **Date:** 2026-09-04
- **Status:** accepted
- **Problem:** B5 reduced generated execution errors from eight to three but scored only 4/31 because 24 executable queries returned the wrong result. Adding more candidates or repairing syntax first could multiply plausible but semantically wrong SQL without addressing question interpretation.
- **Options:** continue directly to the original B7 multi-candidate refiner; rerun MIPROv2 with a larger budget; change only the model; introduce an explicit semantic plan and structural retrieval, prove a stronger single-query draft, and condition B7 on that result.
- **Decision:** Preserve B5 as a negative result and B6R 6/31 as the current best development baseline. Implement provider-free paired error analysis (`SEM-001`), a typed and schema-validated `SemanticPlan` (`SEM-002`), Spider 1.0 train-only SQL-skeleton retrieval (`RET-003`), and a single-query first-pass arm B7P (`GEN-001`). Use recall-first B6R schema evidence and selective value grounding. Permit at most two predeclared B7P versions and at most three frozen model configurations. Promote to B7 only if B7P reaches at least 8/31 EVAL-003, 28/31 executable queries, and two new non-empty correct results over B6R.
- **Reason:** The observed bottleneck is semantic correctness, not parsing or transport. Separating planning from composition exposes whether errors originate in relational interpretation or SQL rendering, while structural retrieval targets operators and query shape rather than superficial question wording. A promotion gate prevents paying for a refiner before the initial query is demonstrably stronger.
- **Consequences:** B7P adds a planning call, structured artifacts, latency, and token cost. Development results are engineering-selection evidence because all 31 examples have already influenced the project; no example-ID-specific rules are allowed, every attempted arm is reported, and the sealed 104-example Spider2 test is opened once only after code/config/model/gates are frozen. Plan/AST/execution proxy scores may diagnose or rank candidates but cannot replace EVAL-003 and may never compare against gold results at runtime.
- **SEM-001 outcome:** The checksum-pinned 31-example paired corpus labels all 27 B5 failures. Dominant primary causes are aggregation/grouping (6), output shape (5), and JOIN path/cardinality (4); 21 examples fail in every compared arm and eight are prompt-sensitive. This evidence confirms SEM-002 and RET-003 as the next tasks before another paid run.

## ADR-016 - Strict schema-bound semantic plan with one repair

- **Date:** 2026-09-05
- **Status:** accepted
- **Problem:** SEM-001 shows that executable SQL often remains semantically wrong, but free-form chain-of-thought is neither reliably parseable nor suitable as an auditable interface between interpretation and SQL composition.
- **Options:** ask the model for SQL directly; store unvalidated prose reasoning; accept a permissive partial plan; define a strict typed plan with schema validation and a bounded correction.
- **Decision:** Introduce provider-independent `semantic-plan-v1` with required fields for output shape, sources, FK-backed joins, predicates/literals, aggregation/grouping, ordering/limit/ties, temporal logic, recursion/set operations, and uncertainties. Parse exactly one JSON object, require exact canonical identifiers and a connected declared-FK join graph, and permit one plan-only correction before failing closed. Store canonical plan and schema-evidence SHA-256 values, attempts, repair state, and initial issues in `semantic-plan-record-v1` for every future GEN-001 prediction.
- **Reason:** A strict intermediate representation makes interpretation errors observable before SQL rendering, directly covers SEM-001's dominant error classes, and provides structural signals for RET-003 without exposing gold outcomes.
- **Consequences:** V1 intentionally rejects self joins and joins without declared foreign keys, so broader join evidence requires a versioned extension. The contract adds a future planner call and possible single repair call, but SEM-002 itself is provider-free and does not alter frozen baselines or claim an accuracy gain. EVAL-003 remains the only primary correctness signal.
