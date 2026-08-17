# Experiment protocol

## Current state

`phase0-smoke-v1` remains an infrastructure smoke test, not a research result. `DATA-001` freezes the benchmark protocol, `DATA-003` implements its metadata-only loader, and `EVAL-001` implements the SQLite execution/comparison core. A real model and final benchmark run are still absent.

The machine-readable source of truth is `configs/datasets/spider2-lite-sqlite-v1.toml`; the exact database and instance assignment is in `configs/datasets/spider2-lite-sqlite-split-v1.json`; the deterministic normalized-output contract is in `configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json`.

## DATA-003: metadata ingestion contract

The loader verifies the pinned source SHA-256 before JSON parsing, validates all
547 upstream IDs/platform counts and selects exactly the 135 SQLite IDs already
frozen by DATA-001. It returns standard `Text2SQLExample` objects and writes a
deterministic `examples.jsonl` plus `dataset-manifest.json` when explicitly run.

The normalized metadata contains the original question, database ID, dialect,
frozen split, source ID/commit and optional `external_knowledge` reference. It
contains no gold SQL, result files or database bytes. Fields resembling gold SQL
are rejected, and the generated manifest must exactly match the version-controlled
DATA-003 manifest. The loader itself remains metadata-only; EVAL-001 consumes its
standard `Text2SQLExample` objects while protected reference SQL and database
paths are supplied through the separate evaluation boundary.

## DATA-001: frozen benchmark decision

### Scope

The primary master-thesis benchmark is the **SQLite portion of Spider 2.0-Lite at pinned commit `cafb867313aab4e674652054198f383cf4018943`**. The pinned `spider2-lite.jsonl` contains 547 examples, of which 135 are local SQLite instances. Its SHA-256 checksum and the checksums of the official evaluator inputs are stored in the TOML protocol.

The official project currently describes Spider2-Lite as a 547-example, multi-dialect benchmark. The pinned snapshot contains 205 BigQuery, 207 Snowflake and 135 SQLite IDs according to the evaluator's routing rules. This differs from older/public summary counts, which is exactly why the commit and hashes are mandatory.

This project does **not** call its result a full Spider2-Lite score. The headline is always:

> Spider2-Lite SQLite custom DB-disjoint test split

BigQuery and Snowflake are optional later extensions. Spider2-Snow is not part of the MVP.

### Researcher-defined split

Spider2-Lite publishes no official train/development/test split. We therefore create a deterministic research split only inside the 135 SQLite examples:

- split unit: database ID, never individual questions;
- ranking: `SHA-256("text2sql-master-data001-v1|" + db_id)`;
- development: the ranked database prefix whose example count is nearest to 20% of 135;
- result: 31 development examples from 6 databases;
- test: 104 examples from the remaining 24 databases.

Database-level grouping is the firewall: a schema/database visible during development cannot appear in the test set. The explicit IDs are versioned rather than recalculated at runtime.

### Allowed uses

| Data | Allowed | Forbidden |
|---|---|---|
| Spider 1.0 official train | future retrieval/few-shot corpus after its ingestion checksum is frozen | headline evaluation |
| Spider2-Lite SQLite development (31) | prompt selection, DSPy optimization, thresholds, development error analysis | retrieval index, final score |
| Spider2-Lite SQLite test (104) | one final evaluation after configuration lock | prompt editing, DSPy, threshold selection, retrieval, exploratory error-driven changes |
| Target-specific schema/docs/external knowledge | context for that same target, because it is part of the benchmark task | use as examples for another target |
| Spider2 gold SQL | evaluator internals/diagnostics only | prompt, retrieval, training or DSPy demonstrations |

All Spider2 inputs are public, so the test set is held out from **our pipeline**, not guaranteed unseen in the LLM provider's pretraining. This limitation must appear in the thesis.

### Oracle tables

Ground-truth table selection is disabled. If it is ever tested, it must be a separate ablation explicitly labelled `oracle tables`, never the primary result.

## Evaluation contract

The primary correctness metric is the official execution-result comparator restricted to the frozen 104-ID test manifest. `EVAL-001` mirrors the pinned upstream comparator semantics in a standard-library compatibility layer: column-vector matching, optional `condition_cols`, optional row-order ignoring, numeric tolerance `1e-2` and official NULL-to-zero normalization. Generated and reference SQL execute independently in read-only in-memory copies of the same SQLite database.

Before aggregation, `summarize_execution_accuracy` fails if there is any duplicate, missing or extra prediction ID. The official command mode remains `exec_result`; the local compatibility layer returns structured results that can later be exported to official CSV input. No final score may be reported until all expected frozen IDs are covered.

Every result must record:

- upstream dataset and evaluator commit/checksums;
- split-manifest hash;
- project Git commit and dependency lock;
- model ID, generation parameters and seed where supported;
- prompt, schema and retrieval-index hashes;
- whether oracle tables were used (default `false`);
- execution timestamp and database snapshot identifier.

## Frozen-evaluation rules

1. Keep development and test databases disjoint.
2. Build retrieval indexes only from an external training split; never from Spider2-Lite.
3. Never optimize prompts, examples or thresholds on test answers.
4. Run every compared configuration over exactly the same frozen IDs.
5. Treat environment/network failures separately from semantic failures.
6. Do not inspect test results until the configuration is locked.
7. Any later protocol change requires a new protocol ID, manifest and documented ADR; the v1 files are immutable.

## Primary sources

- Spider 2.0 official site: <https://spider2-sql.github.io/>
- Spider 2.0 official repository: <https://github.com/xlang-ai/Spider2>
- Spider 2.0 paper (ICLR 2025 Oral): <https://arxiv.org/abs/2411.07763>
- Official Spider2-Lite evaluator instructions: <https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/evaluation_suite>
