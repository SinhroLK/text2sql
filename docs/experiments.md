# Experiment protocol

## Current state

`phase0-smoke-v1` remains an infrastructure smoke test, not a research result. `DATA-001` freezes the benchmark protocol, `DATA-003` implements its metadata-only loader, and `EVAL-001` implements the SQLite execution/comparison core. `EVAL-003` is ready for all 31 development examples; strict reference-SQL `EVAL-002` is optional. `LLM-002` uses official Groq SDK 1.6.0 and completed a successful `openai/gpt-oss-120b` smoke: valid SQL, 224 input tokens, 124 output tokens, and 697 ms latency. This smoke validates infrastructure only and is not a benchmark result.

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

The primary correctness metric is the official execution-result comparator restricted to the frozen 104-ID test manifest. `EVAL-001` mirrors the pinned upstream comparator semantics in a standard-library compatibility layer: column-vector matching, optional `condition_cols`, optional row-order ignoring, numeric tolerance `1e-2` and official NULL-to-zero normalization. Generated and reference SQL execute independently in read-only in-memory copies of the same SQLite database. EVAL-002 validates all development prediction IDs before execution and stores database/reference hashes; the 104-ID test remains unopened until the pipeline is frozen.

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

## EXP-001: B0/B1 development baseline

EXP-001 uses `openai/gpt-oss-120b`, temperature `0`, seed `42`, GPT-OSS reasoning effort `low`, a 1024-token output cap, a 60-second timeout, and at most two project-level retries. Official SDK retries remain disabled.

- B0 (`exp001-b0-gpt-oss-120b-v1`) receives the original question and SQLite dialect only. The prompt must not contain serialized schema text.
- B1 (`exp001-b1-gpt-oss-120b-v1`) receives the same question plus the complete deterministic simple-schema serialization.
- Both configurations run the same frozen 31 development IDs in sorted order.
- Each successful response is appended immediately to a config-hash-bound JSONL checkpoint.
- Resume rejects duplicate IDs, foreign/test IDs, database mismatches, and changed configuration hashes.
- Scoring begins only after exact coverage and uses EVAL-003 official gold-result variants.

The scored report freezes generation parameters, token totals, total/p50/p95 latency, executable-SQL rate, the DATA-003 manifest, and the complete EVAL-003 database/gold-result checksum manifest. Both 31-record checkpoints and scored reports exist, so EXP-001 is `DONE`. The 104-example test split remained sealed.

| Arm | Correct | Execution accuracy | Executable SQL | Input/output tokens | p50/p95 latency | Estimated Groq cost |
|---|---:|---:|---:|---:|---:|---:|
| B0 | 0/31 | 0.00% | 0/31 | 4,951 / 12,871 | 1,304 / 4,428 ms | $0.008465 |
| B1 | 5/31 | 16.13% | 26/31 | 51,287 / 12,328 | 1,371 / 1,998 ms | $0.015090 |

B1 correct IDs are `local068`, `local202`, `local270`, `local274`, and `local275`. The cost estimate uses the run-time GPT-OSS 120B rates of $0.15/M input and $0.60/M output tokens. Prediction and report artifacts are stored under `artifacts/experiments/` and `artifacts/reports/`.

## SCHEMA-002: B2 M-Schema development baseline

B2 (`exp002-b2-gpt-oss-120b-v1`) keeps the EXP-001 model and generation settings fixed and changes only the schema context from the B1 simple serializer to `exp002-mschema-v1`. It adds primary/foreign-key structure and bounded representative values.

The frozen sampling policy is 3 examples per column, a 50-character text limit, and a 24-row scan limit. Sampling is read-only, deterministic, cached per database/schema/policy, and omits columns whose names indicate sensitive data. Exact policy values are stored in each generation result and scored report.

The implementation is verified over all six development databases and the controlled live B2 run is complete over all 31 development IDs. SCHEMA-002 is `DONE`; the 104-example test split remained sealed.

| Arm | Correct | Execution accuracy | Executable SQL | Input/output tokens | p50/p95 latency | Estimated Groq cost |
|---|---:|---:|---:|---:|---:|---:|
| B1 | 5/31 | 16.13% | 26/31 | 51,287 / 12,328 | 1,371 / 1,998 ms | $0.015090 |
| B2 | 2/31 | 6.45% | 22/31 | 107,586 / 13,417 | 1,783 / 19,431 ms | $0.024188 |

B2 correct IDs are `local202` and `local311`. Compared with B1, execution accuracy decreased by 9.68 percentage points while input tokens increased by 109.8%. Nine generations had execution errors; `local167` and `local170` reached the 1,024-token output cap and produced incomplete SQL. This negative result rejects the hypothesis that full M-Schema plus raw representative values is sufficient by itself and motivates selective schema linking.

The B2 prediction SHA-256 is `5a63026a14368fcfe80e12902e933e8ec297c8be450f3379791acd2ea72233db`; the scored-report SHA-256 is `d08bf39121d326c00385f87058b15befd3a3a8bdf8663e4e6471cd48899d42be`.

To reproduce or re-score the completed run, use:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
  --experiment-config configs/experiments/exp002-b2.toml \
  --predictions artifacts/experiments/exp002-b2-predictions.jsonl \
  --report artifacts/reports/exp002-b2-report.json
```

The command is resumable and restricted to the same 31 development IDs. With the completed checkpoint present, re-running makes no provider requests and regenerates the EVAL-003 report. Starting from an empty checkpoint requires 31 successful provider responses before retries.
