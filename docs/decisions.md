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
