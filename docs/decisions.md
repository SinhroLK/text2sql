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
- **Status:** deferred by roadmap 3.8
- **Problem:** B5 reduced generated execution errors from eight to three but scored only 4/31 because 24 executable queries returned the wrong result. Adding more candidates or repairing syntax first could multiply plausible but semantically wrong SQL without addressing question interpretation.
- **Options:** continue directly to the original B7 multi-candidate refiner; rerun MIPROv2 with a larger budget; change only the model; introduce an explicit semantic plan and structural retrieval, prove a stronger single-query draft, and condition B7 on that result.
- **Decision:** Preserve B5 as a negative result and B6R 6/31 as the current best development baseline. Implement provider-free paired error analysis (`SEM-001`), a typed and schema-validated `SemanticPlan` (`SEM-002`), Spider 1.0 train-only SQL-skeleton retrieval (`RET-003`), and a single-query first-pass arm B7P (`GEN-001`). Use recall-first B6R schema evidence and selective value grounding. Permit at most two predeclared B7P versions and at most three frozen model configurations. Promote to B7 only if B7P reaches at least 8/31 EVAL-003, 28/31 executable queries, and two new non-empty correct results over B6R.
- **Reason:** The observed bottleneck is semantic correctness, not parsing or transport. Separating planning from composition exposes whether errors originate in relational interpretation or SQL rendering, while structural retrieval targets operators and query shape rather than superficial question wording. A promotion gate prevents paying for a refiner before the initial query is demonstrably stronger.
- **Consequences:** B7P adds a planning call, structured artifacts, latency, and token cost. Development results are engineering-selection evidence because all 31 examples have already influenced the project; no example-ID-specific rules are allowed, every attempted arm is reported, and the sealed 104-example Spider2 test is opened once only after code/config/model/gates are frozen. Plan/AST/execution proxy scores may diagnose or rank candidates but cannot replace EVAL-003 and may never compare against gold results at runtime.

## ADR-016 - Layer parsed SQL policy with SQLite prepare-time authorization

- **Date:** 2026-09-07
- **Status:** accepted
- **Problem:** SAFE-001 must accept real SQLite `SELECT` syntax while rejecting multiple statements, writes, system access, unknown identifiers, and extension side effects. Token filtering alone cannot resolve aliases or columns, while running a candidate to discover errors crosses the execution boundary too early.
- **Options:** maintain a partial SQLite grammar in-project; add a second full SQL transpiler; trust SQLite `query_only`; combine a pinned parsed token tree with SQLite's compiler and authorizer over an empty canonical schema.
- **Decision:** Pin `sqlparse==0.5.5` and convert its hierarchy into a literal-redacted immutable AST artifact. Enforce the one-statement/read-only policy on that tree, then reconstruct empty canonical tables in memory and prepare `EXPLAIN QUERY PLAN` under a deny-by-default SQLite authorizer. Never evaluate the submitted query. Return versioned hashes, resolved identifiers, and sanitized structured issues.
- **Reason:** The parser exposes statement structure and keeps keywords inside literals inert. SQLite supplies the authoritative dialect grammar and name resolution for joins, subqueries, windows, CTEs, aliases, and ambiguity. Empty reconstructed tables prevent validation from reading source data.
- **Consequences:** SAFE-001 validates syntax and canonical identifiers but does not prove termination, bound result size, or runtime safety. SAFE-002 must recheck the validated SQL hash, use an isolated read-only database, and enforce progress, row, and resource limits before execution.

## ADR-017 - Bound SQLite execution in a fresh worker over a static snapshot

- **Date:** 2026-09-13
- **Status:** accepted
- **Problem:** An in-process progress callback and `fetchall()` cannot bound database-copy overhead, single-expression allocations, or complete result memory. Opening a live WAL database can also involve sidecar files.
- **Decision:** SAFE-002 uses a fresh Linux interpreter with a parent-enforced deadline and OS memory/CPU/file-size limits. Read a bounded standalone database image without a source SQLite connection, deserialize into memory, revalidate using SAFE-001, and execute under query_only and the runtime authorizer. Check existing validation handoffs against the exact SQL hash. Return incremental bounded results with explicit truncation and sanitized errors. See `DOC-PYTHON-001`, `DOC-SQLITE-001`, and `DOC-PYTHON-RESOURCE-001`.
- **Consequences:** Static snapshots only, no WAL/journal inputs or virtual-table databases; default database size is 64 MiB. Linux and SQLite deserialization support are required. This is a SQL/resource boundary, not an arbitrary-code OS sandbox. Benchmark scoring remains separate and must not treat preview truncation as a complete result. REF-001 can now use the sandbox API without changing historical generation arms.

## ADR-018 - Freeze a small B6R-based candidate and repair arm

- **Date:** 2026-09-13
- **Status:** accepted for offline implementation; live results pending
- **Decision:** B7 keeps the B6R base checksum/model/settings, adds two fixed generation strategies, ranks at most three initial candidates deterministically, and permits one repair only when no complete initial sandbox success exists. No gold input is available to the ranker/refiner. Completion reasons and literal-preserving duplicate identities are explicit. Rejected or incomplete outputs cannot become final answers.
- **Resources and audit:** Use B7-specific 160-MiB database and 512-MiB memory caps after development preflight showed three databases exceed the default image limit. Retain the other SAFE-002 limits. Freeze 124 maximum requests and a 6,000-token input cap. Hash-chained reservations precede provider calls; recorded responses and completed questions support compatible resume with no invisible retries. EVAL-003 scoring starts only after generation/selection is complete.
- **Consequences:** Agreement and identifier overlap are correctness proxies, not semantic guarantees. Empty results and duplicate SQL receive no extra votes. Safety/resource rejection counts as failure, which must be disclosed when comparing with B6R's evaluation-only execution. Model/API failures remain audited attempts. REF-001 stays in progress until an exact 31-example development report is produced; B7P remains deferred.
## ADR-019 - Compare SQL-only S0 with schema-aware S1 guardrails

- **Date:** 2026-09-19
- **Status:** accepted
- **Problem:** SAFE-001/SAFE-002 prevent writes, host access, unbounded execution, and unknown-schema queries, but a valid read-only query can still disclose credentials, PII, or confidential columns. The security phase also needs to measure protection costs on legitimate questions without opening Spider2 test data or making provider calls.
- **Decision:** Freeze a 16-case synthetic corpus with six benign and ten adversarial cases plus a checksum-pinned disposable employee database schema. Define S0 as SAFE-001 plus SAFE-002. Define S1 as S0 plus a versioned question filter and an output policy that uses SAFE-001 canonical identifiers to reject configured sensitive tables or columns. Release only complete sandbox successes. Persist hashes, decisions, issue codes, identifiers, and aggregate metrics without question text, SQL text, or returned data.
- **Reason:** The paired provider-free design isolates the incremental effect of application guardrails while exercising the real validator and sandbox. Canonical identifier checks protect against aliases more reliably than matching generated SQL text.
- **Consequences:** On this corpus S0 releases 5/10 adversarial read-only queries; S1 releases 0/10 and blocks 0/6 benign cases. These are fixture results, not a deployment prevalence estimate. Pattern filters remain paraphrase-sensitive, the column policy is schema-specific and conservative, and a production application still requires identity, authorization, approved database selection, retention controls, and stronger OS isolation.

## ADR-020 - Freeze the final held-out protocol before generation

- **Date:** 2026-09-19
- **Status:** accepted
- **Problem:** The final 104-example evaluation must measure the already selected system without test-driven prompt changes, selective reruns, post-score replacement, or an analysis chosen after observing outcomes.
- **Decision:** Select B0, B1, B2, B4, B6, and primary B6R. Copy each frozen development configuration into a test-only configuration with only its experiment ID and split changed. Run every arm once, retain every attempt, complete all generations before scoring, and open the held-out split once. Predeclare execution accuracy as primary, executable rate and token use as secondary, 10,000 paired percentile bootstrap resamples at seed 20260919, six exact two-sided McNemar comparisons, and Holm correction at familywise alpha 0.05. Bind code, tests, dependencies, data protocol, security resources, configurations, and analysis code in a deterministic manifest under release tag `text2sql-final-v1`.
- **Reason:** This converts the final evaluation boundary into executable checks and keeps statistical choices independent of observed held-out results.
- **Consequences:** B3, B5, and operationally degraded B7 remain development evidence and are not rerun on the holdout. Missing, failed, or extra predictions invalidate analysis and cannot be replaced after scoring. RUN-001 makes no provider call and reveals no held-out outcome; RUN-002 must first pass manifest, clean-tree, tag, dependency, resource-coverage, and fresh-output checks.

## ADR-021 - Prioritize semantic correctness after the completed final evaluation

- **Date:** 2026-10-02
- **Status:** accepted for the next development cycle; implementation not started
- **Problem:** RUN-002 v2 is complete, but the roadmap still describes it as pending and semantic planning as deferred. B6R scores 34/104 with 49 executable result mismatches and 21 execution errors. The review identifies accepted truncated responses, omitted supplied documents, misleading schema/value links, absent declared foreign keys, and schema-family overlap missed by database-name checks. More candidates alone cannot solve the observed generation deficit.
- **Decision:** Adopt roadmap 4.0 and phases 7A-7E. Preserve original experiments. First establish the new data-use/family-audit protocol and development error corpus; repair completion handling, document propagation, and quota accounting; evaluate a corrected compact-schema baseline and separate budget/effort changes; then implement semantic planning, relevant values, verified join evidence, and one bounded repair. Structural retrieval, extra candidates, broader model search, and further DSPy optimization are optional later experiments. Reactivate SEM-001, SEM-002, and GEN-001 without treating archived code as completed work.
- **Evaluation boundary:** The reviewed 104-example set is no longer untouched for future improvements. DATA-004 must record any reuse and either reserve a fresh, independently verified, family-audited holdout or explicitly label the next evaluation as reused-set improvement. Gold answers remain outside generation, retrieval, and runtime selection. Schema signatures establish overlap, not identical contents or duplicated answers. Old manifests and scores retain their original meaning.
- **Experiment policy:** New methods use new configurations/output directories, a common task instruction, complete attempt accounting, and predeclared numeric call/token/cost limits and selection criteria. Vary one component or model setting at a time. Treat output truncation separately from known-rejected transport calls. Measure gained/lost examples, nonempty correctness, resource failures, repair regressions, and wall-clock waits. Keep official scoring primary; predeclare database/family-cluster sensitivity analysis for new claims. No promised accuracy percentage or outcome-selected reruns.
- **Reason:** The review found reproducible input/output defects and a larger semantic gap. All 216 offline tests passed, and the pinned official comparator reproduced all 624 saved outcomes using saved query results. Current evidence supports improving generation and context rather than changing the evaluator or scaling the old optimizer/ranker unchanged.
- **Consequences:** The next sprint is protocol/family audit plus offline completion/document fixes and preparation of a bounded baseline comparison. A newer verified run may become the final system reported in the thesis, with the original run retained as earlier evidence. The completed B7 remains a B6R-based experiment; it is not retroactively described as planner-based. Original S0/S1 results remain fixed-candidate fixture results. This decision updates the plan only; it does not execute paid experiments or implement the new tasks. See [project review](project-review-2026-10-02.md) and [roadmap](project-plan-roadmap.md).

## ADR-022 - Reject incomplete completions and prepare pinned task context offline

- **Date:** 2026-10-03
- **Status:** accepted; offline implementation complete, paid baseline pending
- **Problem:** Nonempty truncated output was accepted as a candidate, and supplied task documents were omitted from generation. Starting another run before checking its inputs would preserve those defects.
- **Decision:** Accept Groq candidates only for nonempty stopped completions; retain rejected text and available usage/status/fingerprint in audit metadata. Do not automatically regenerate or increase the completion budget. Load only checksum-pinned, bounded task documents and propagate them through an example-aware generation API and the CLI. Separate prompt preparation from provider invocation so all development inputs can be inspected offline.
- **Scope:** The new offline protocol prepares exactly the original 31 development examples, inventories the 104 reviewed test examples, and disables retrieval and paid runs. An automated schema-name audit covers 30 target and 140 retrieval databases. It detects overlap candidates but does not establish data identity or enforce family exclusions yet. DATA-004/005 and OPS-001 remain open.
- **Evidence:** 240 offline tests pass. The real-data preflight prepares 31 prompts, including seven with supplied documents, without provider calls or prediction-SQL execution. It records four exact target/train schema matches, three partial matches, one cross-split overlap candidate, and 20 databases without declared foreign keys.
- **Reproducibility:** Preserve the historical configurations, manifests, and results. Verify their freeze against the archived release and its checksum-pinned generated fixture; continue rejecting source drift. New development intentionally requires a new experiment identity and cannot use the old release gate unchanged.
- **Consequences:** These fixes remove specific failure paths but do not demonstrate better accuracy. New baseline generation, quota/resume accounting, family exclusions, semantic planning, and a fresh or explicitly reused evaluation protocol remain outstanding. See [implementation and reproduction instructions](development-recovery.md).

## ADR-023 - Complete offline preparation and stop before live model requests

- **Date:** 2026-10-03
- **Status:** accepted; local implementation validated, live calls disabled
- **Authorization:** The user requested free work first and notification when paid work begins. No model calls are authorized by this change, including calls that might fit a provider free tier.
- **Data decision:** Conservatively exclude all seven target/train overlap pairs (six training databases, 454 examples) and bind the derived index to source entries and audit hashes. Group 30 targets into 28 conservative reporting families. Declare any next run on the reviewed 104 examples a reused-set evaluation, with selection confined to the original 31 development examples. Preserve historical artifacts.
- **Diagnostics:** Record 155 paired saved development outcomes and 104 separate reviewed-test diagnostics. Bind twelve manual B6R development annotations to SQL hashes, distinguish suspected from observed errors, and leave unresolved causes explicitly unclassified. This corpus is analysis-only and is never generation context.
- **Operations:** Use durable, exclusively locked, hash-chained request reservations with finite call/token/cost caps. Replay completed outcomes, never automatically reissue interrupted/ambiguous failures, and permit at most one retry after an explicit 429 rejection. Every retry consumes a new reservation. The pipeline adapter refuses live providers without explicit authorization/account confirmation and rejects hidden retries. Quotas are local, not account-wide.
- **Experiment draft:** C0 uses 1024/low, C1 3072/low, C2 3072/medium with all other inputs/settings fixed. The original 4096 proposal exceeded the recorded safe quota scenario for two prompts; the offline change to 3072 retains complete context. Draft caps: 93 initial requests, 186 total attempts, 1.5M reserved tokens, $3 maximum. These are proposed limits, not permission to spend. Promotion requires at least three net development gains, at most two losses, and gains in at least two families, with cost/budget/effort tie breaks.
- **Evidence and limits:** All 266 tests pass. Network-blocked preparation completes with 31 prompts and zero model requests. Local pinned tokenization plus an overhead allowance fits all requests in the recorded quota scenario, but current account quota, pricing and provider request overhead remain unverified. Installed package versions are recorded; a new portable dependency lock is not completed. Semantic planning and repair remain later work following corrected-baseline measurement. See the [offline handoff](free-work-handoff.md).

## ADR-024 - Provide a resumable Free-tier experiment CLI with separate scoring

- **Date:** 2026-10-03
- **Status:** implemented and tested offline; live experiment not run
- **Decision:** Supply prepare/generate/status/score commands under `text2sql.development.run`, using a new Free-tier configuration. Require explicit API and Free-organization confirmation at generation time; leave earlier offline configuration flags unchanged. The CLI never changes account billing and cannot independently verify the user's organization is free. No live calls were made while implementing it.
- **Run identity:** Freeze all prompts, source/config/environment and database/document/evaluator/gold hashes before any request. Keep secrets out of artifacts. Use a single locked journal across all 93 cases; pause/resume under shared caps, replay completed outcomes, retain ambiguous interrupted attempts as failures, and stop on newly refused credentials or observed usage above reservations. No automatic replacement after a poor answer.
- **Evaluation:** Generate every arm before loading gold values for scoring. Validate predictions against durable provider outcomes. Execute in SAFE-002 with fixed 60-second, 256-MiB database, 1-GiB worker, 100,000-row, 16-MiB result and 1-MiB value limits. Run the exact checksum-pinned upstream comparison function against pandas-loaded gold CSVs, retaining resource/validation failures in the denominator. These execution limits differ from historical reports; matched C0/C1/C2 results drive promotion. Scoring checkpoints and completed reports support replay without model calls.
- **Evidence:** 281 tests pass. The 93-request real-input mock rehearsal completed with networking blocked and exactly 93 reservations after a second generate invocation. Fixture SQL scoring, interruption, quota retry, identity drift, prediction editing and score resume are tested. Mock output is labeled and cannot be scored as benchmark evidence. Current account limits and real model accuracy remain unverified. See [user commands](run-recovery-experiment.md).

## ADR-025 - Ground prompts in database evidence and leave API execution to the user

- **Date:** 2026-10-11
- **Status:** implemented and tested offline; improved model accuracy not yet established
- **Evidence:** The completed original recovery run scored C0 7/31, C1 5/31 and C2 9/31. Its promotion rule retains C0. Inspection found guessed literal encodings, unavailable functions, incorrect window/baseline logic, and validator rejections that need separate review.
- **Prompt decision:** Add `grounded-sqlite-v2` through `GroundedSQLPipeline` and `text2sql.development.prompt_run`. Preserve all schema identifiers, declared relationships and task documents. Add bounded observed values, runtime function availability, a heuristic focus list and general SQL correctness guidance. Include no reference answers or benchmark solution examples. Preserve historical builders and artifacts; the new preparation path selects the new prompt explicitly.
- **Sampling:** At most five focus tables, twelve nonsensitive columns and three values per column; bounded SQLite VM work/deadline with a bounded-prefix fallback. Record storage types, sampling outcomes and whether the non-null vocabulary was exhausted. Do not infer absence from incomplete samples or truncate values into invented literals. Direct entity-name matches remain eligible even when a related table has more lexical matches.
- **Reproducibility:** Keep the old model/settings/execution policy for a matched prompt experiment. Record the parent run and freeze new prompts, source and resources. Reject oversized preparations without silently removing context. `recovery-grounded002` has no API requests and is ready for user execution. Maximum reserved request: 7,176 of the recorded 7,200-token allowance; these remain local estimates, not account-wide guarantees.
- **Validation:** 302 tests pass, including seventeen new prompt/preparation checks. A full development audit checks all 31 schemas/documents and known code-value failures. The 93-request mock rehearsal completes with networking blocked, and rerunning changes no journal bytes. Synthetic SQL verifies baseline/window, calendar-date and zero-period patterns. These checks do not measure model accuracy or resolve existing validator limitations.
- **API ownership:** The user explicitly runs every model API call, including free diagnostics. The assistant prepares commands and performs offline work. A prior v1 diagnostic was interrupted after two connection failures and one response; its journal and original prompt source are preserved separately and are not a new accuracy result. No further model requests were made after that instruction. No billing changes or paid fallback are allowed.
- **Next step:** User-run generation/scoring, matched comparison with the original settings, then a separate validator investigation and targeted semantic improvements as supported by results. See [prompt documentation and commands](grounded-prompts.md).
