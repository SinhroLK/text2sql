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


## LINK-001: B6 linked-M-Schema development arm

B6 (**exp003-b6-gpt-oss-120b-v1**) keeps the B2 model, temperature, seed,
reasoning effort, sample limits, retry count, output cap, timeout, dataset, and
evaluator unchanged. Its only current experimental change is the versioned
**extractive-lexical-v1** schema subset and
**exp003-linked-mschema-v1** prompt.

This early B6 prototype intentionally isolated linking directly against B2
because B3-B5 retrieval/DSPy components were not implemented when it was frozen. A future cumulative
B5+link arm must receive a new experiment/config identifier; it cannot overwrite
this result.

The provider-free development audit is complete:

| Measure | Full B2 | Linked B6 | Reduction |
|---|---:|---:|---:|
| Repeated tables | 751 | 124 | 83.49% |
| Repeated columns | 4,569 | 524 | 88.53% |
| Prompt characters | 313,515 | 55,733 | 82.22% |
| Whitespace-token proxy | 30,835 | 6,723 | 78.20% |

No fallback occurred in 31 development examples. Selected table p50/p95 is 4/5
and selected column p50/p95 is 17/23. The deterministic audit SHA-256 is
**93d85cbdd41a5cf595576a9ed7e925ff3ccd253e5f285be14b8c72a4153ba238**.

Reproduce the offline audit without GROQ_API_KEY:

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.linking_cli \
      --experiment-config configs/experiments/exp003-b6.toml \
      --output artifacts/reports/exp003-b6-linking-audit.json

LINK-001 is **DONE**. Frozen B6 covers 31/31 development IDs and scored
1/31 (3.23%), with 27/31 executable outputs, 18,287 input tokens and 6,929
output tokens. Its only match was a dummy empty query that coincidentally matched
an empty result. B6 therefore demonstrates that aggressive linked-only pruning
reduced cost but damaged recall and did not improve correctness.

The prediction SHA-256 is
**05dad4186f7e16d7a23116e4ecec9a3cf8dbc0e9932f8682079ad7e240ac04df**;
the report SHA-256 is
**838a2631bebdd22db54270b14d05d62dce1e579d464d6cc8678081159e8d9d20**.
The 104 test IDs remained sealed.

Because official reference SQL is unavailable for 30 development examples,
fixture annotations provide schema precision/recall/F1 tests; no fabricated
aggregate real-schema recall is reported. Full methodology and limitations are
in **docs/schema-linking.md**.


## LINK-002: B6R recall-repair arm

B6R (**exp004-b6r-gpt-oss-120b-v1**) is a new arm; it does not modify B6. It
keeps all columns in selected tables, raises the direct-table cap to eight, and
combines linked detailed M-Schema with the complete compact schema. The prompt
also forbids dummy queries and SQLite-incompatible QUALIFY, requires one
read-only SELECT, and asks for qualified ambiguous columns. Generation settings
remain frozen at the B6 values so the live comparison isolates context repair.

The provider-free 31-example audit selected 215/751 tables and 1,764/4,569
columns with zero fallback. Hybrid prompts total 290,853 characters (7.23% less
than full B2) and 36,056 whitespace-token proxy units (16.93% more than B2).
Audit SHA-256:
**b4c75877aef0d7f0617de70891874aac1c074e3f0c495326e382ccb13b1b4c18**.
These numbers measure context, not SQL accuracy.

Reproduce the offline audit:

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.linking_cli \
      --experiment-config configs/experiments/exp004-b6r.toml \
      --output artifacts/reports/exp004-b6r-linking-audit.json

Start or resume the live B6R run:

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
      --experiment-config configs/experiments/exp004-b6r.toml \
      --predictions artifacts/experiments/exp004-b6r-predictions.jsonl \
      --report artifacts/reports/exp004-b6r-report.json

LINK-002 is **DONE**. The checkpoint covers 31/31 development IDs and EVAL-003
reports 6/31 correct (19.35%), 25/31 executable, 19 result mismatches, and 6
execution errors. B6R used 94,140 input and 15,806 output tokens with p50/p95
latency 2,174/3,335 ms. Estimated token cost is USD 0.023605.

Correct IDs are `local171`, `local202`, `local270`, `local274`, `local275`, and
`local310`. Five matches are non-empty; `local275` is an empty-result match but
uses a plausible domain query rather than a dummy. Relative to B1, B6R gains
`local171` and `local310`, loses `local068`, and improves by one example (+3.23
percentage points). It uses 83.56% more input tokens than B1 and 12.50% fewer
than B2.

The six failures are nested-window misuse (`local068`), timeout/interruption
(`local169`, `local170`), token-cap truncation (`local279`), an undefined
alias/column (`local311`), and SQLite-incompatible `generate_series`
(`local355`). No B6R output contains `QUALIFY` or the forbidden dummy pattern.

Prediction SHA-256:
**46664f819ce0d8faa2fe377babda3a6555df38477e0570415df146345df26577**.
Report SHA-256:
**aaa130ec8e2f606058c413ac9434f49b9af618ec2fb5da45214f4896bd7802fc**.
The 104 test IDs remained sealed.

## RET-001: Spider 1.0 train-only retrieval index

RET-001 is complete and does not call an LLM. It pins the official Yale LILY
Spider archive, uses only `train_spider.json`, and validates its 7,000 records
and 140 databases against `tables.json`. Source checksums are verified before
JSON parsing.

The index firewall requires exact coverage of all 135 frozen Spider2 metadata
records and all 30 Spider2 SQLite databases before index construction. Every
Spider 1.0 entry is rejected if its generated ID, case-folded database ID, or
normalized question overlaps Spider2 development or test metadata. The completed
audit found zero overlaps in all three categories.

The deterministic JSONL index has SHA-256
`82ee39e03792647fa7efeddf1fcd293ca068f0fc879d9a88cc27c8546550389e`.
Its version-controlled config and expected manifest are under
`configs/datasets/`; the local artifact is under
`artifacts/retrieval/spider1-train-v1/`.

Rebuild and verify it without provider access:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.retrieval.cli
```

RET-001 creates a safe candidate corpus. RET-002 separately freezes random B3
and similarity B4 selection policies, records the exact retrieved IDs per
target, and keeps the 104-example test outcomes sealed.

## RET-002: B3/B4 few-shot retrieval arms

The offline portion of RET-002 is complete. B3
(`exp005-b3-gpt-oss-120b-v1`) uses `random-fixed-v1`, `k=3`, and retrieval seed
42. Its three demonstrations are sampled once and shared by all targets. B4
(`exp006-b4-gpt-oss-120b-v1`) uses deterministic `tfidf-cosine-v1`, `k=3`,
with stable retrieval-ID tie-breaking. The standard-library TF-IDF index is fit
only over normalized Spider 1.0 train questions.

Both arms keep the B2 model, generation parameters, full M-Schema sampling
policy, dataset, and evaluator fixed. They share
`exp005-fewshot-mschema-v1`; only the demonstration-selection strategy differs.
The prompt labels demonstrations as external training examples and explicitly
forbids copying their database identifiers into the target query.

Provider-free audits cover exactly 31 development targets and 93 selections per
arm. B3 uses 3 unique examples from 3 databases, each appearing for all 31
targets. B4 uses 85 unique examples from 40 databases; no example appears more
than 3 times. Rebuilding produces identical files:

| Arm | Audit SHA-256 |
|---|---|
| B3 | `3df8e8695d1006ec9f96efc50b9e1f52c5266de6459c54da3659e86bfd797dcc` |
| B4 | `a16409c01f81e9822ccc6db41f4dc9debc878b60ca9828d11f426c3892d188c1` |

Reproduce the audits without `GROQ_API_KEY`:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.experiments.retrieval_cli \
  --experiment-config configs/experiments/exp005-b3.toml
PYTHONPATH=src .venv/bin/python -m text2sql.experiments.retrieval_cli \
  --experiment-config configs/experiments/exp006-b4.toml
```

Both live arms now cover exactly 31 development IDs and RET-002 is complete:

| Arm | Correct | Execution accuracy | Executable SQL | Input/output tokens | p50/p95 latency | Estimated Groq cost |
|---|---:|---:|---:|---:|---:|---:|
| B3 fixed-random | 4/31 | 12.90% | 23/31 | 117,785 / 14,043 | 1,828 / 3,913 ms | $0.026094 |
| B4 TF-IDF cosine | 5/31 | 16.13% | 23/31 | 115,556 / 13,145 | 1,872 / 2,996 ms | $0.025220 |

B3 correct IDs are `local068`, `local202`, `local275`, and `local311`. B4
retains those four and additionally solves `local272`. Similarity selection
therefore improves on fixed random by one example (+3.23 percentage points),
but only matches B1 and remains below B6R's 6/31. This is evidence for a small
retrieval benefit, not a strong absolute result.

Prediction/report SHA-256 values:

| Arm | Predictions | Report |
|---|---|---|
| B3 | `5530acf94c632e4bcc352f37b8b2e72c4b90325368944cba836db85ad9255898` | `d00a8e82ea468f4790a1a64581c9985883ae2df0885faf1fba6a7972215f7389` |
| B4 | `d64cd4f5e74cecce79c4aa465b8c762246b25241b5ec2e9f7b850b8f3c7bc580` | `0d2e995ebc7ce2e63b45b8416f301d84794cfe10ece35ee3a0739bccb3c5307a` |

The final B4 request initially exposed a TPD 429. The provider now reports a
sanitized Groq quota category and retry/reset details without exposing account
IDs or key-like strings. The run resumed unchanged after the server-provided
reset. The verified suite passes 108/108 tests and the 104-example test split
remains sealed.


## DSPY-001: B5 optimization over frozen B4

The offline DSPY-001 implementation is complete and the task remains
`IN PROGRESS` until an authorized paid compile produces a frozen B5 artifact
and a complete development result. B5 consumes the exact B4 context: full
M-Schema plus three TF-IDF-selected, verified Spider 1.0 training
demonstrations. It does not change the retrieval index or selector.

`configs/optimization/dspy001-b5.toml` pins DSPy 3.3.1, LiteLLM 1.99.0, Optuna 4.9.0, the B4 config checksum,
the `B5TextToSQL` signature version, and an explicit MIPROv2 budget: 3
candidates, 5 trials, zero DSPy-level bootstrapped demonstrations, zero
labeled demonstrations, no minibatching, one optimizer thread, and seed 42. Explicit
values avoid changes in DSPy's moving `auto` defaults.
 Program-aware and tip-aware instruction proposers remain enabled;
data-aware and few-shot-aware proposers are disabled because they concatenate
multiple complete B4 contexts. The three verified Spider 1.0 examples remain
inside every B4 context, but DSPy does not add another demonstration layer.

The same config pins the observed Groq on-demand limit at 8,000 TPM, applies a
0.90 safety margin (7,200-token rolling budget), uses a 60-second window plus a
2-second buffer, and permits at most eight rate-limit retries. Each call
reserves LiteLLM-counted input plus the 1,024-token maximum output before it is
sent. A successful response replaces the reservation with actual prompt plus
completion usage. Both MIPROv2 prompt-model and task-model calls share this
process-wide limiter. Proactive waits and 429 retry waits emit sanitized JSON to
stderr and their counts/durations are frozen in the optimization manifest.
The real formatted task requests were audited provider-free: the largest input
is 5,682 tokens and its conservative input-plus-output reservation is 6,706,
which fits the 7,200-token safe budget.

Optimization uses only the 31 development examples and splits them by database:

| Fold | Databases | Examples |
|---|---|---:|
| Train | Airlines, city_legislation, music, oracle_sql | 21 |
| Validation | electronic_sales, f1 | 10 |

The metric exposes only EVAL-003 execution correctness. The gold-result store is
scoped to these explicit development IDs before CSV loading; Spider2 gold SQL is
never used as a demonstration or metric input. Metric audit records contain the
example ID, score, status, generated-SQL SHA-256, and bootstrap flag, not SQL
text.

Run the provider-free audit:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli audit \
  --report artifacts/reports/dspy001-b5-audit.json
```

The verified audit covers 21 training and 10 validation examples, makes zero
provider calls, and reports zero test examples and zero gold SQL use. Its frozen
config SHA-256 is
`a1c90046058821d2b4df62e0363e09c3b89d9b381900e9ebb79e15b5983ccf7b`; the
deterministic audit report SHA-256 is
`ba8c7f5078150b908bb6a8bd496258dfafb249844e887b31b1a348f1947bfd1c`.

Before dataset preparation or any paid request, the CLI verifies that all three
optimizer runtime versions exactly match the frozen config. This prevents a
missing optional Optuna dependency from being discovered only after MIPROv2 has
already generated candidates.

The paid compile is a separate explicit command. With no resume flag it
always creates a fresh, uniquely named cache:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli optimize
```

Startup emits `b5_recovery_run_started` and a run ID. If that process is
interrupted, continue only that run with:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli optimize \
  --resume-run-id run-YYYYMMDDtHHMMSSz-xxxxxxxxxxxx
```

This is deterministic replay recovery, not a serialized live Optuna study:
MIPROv2 restarts from seed 42, identical successful LM calls are replayed, and
execution metrics are recomputed against checksum-bound databases/gold results.
The per-run identity binds the full config and B4 config, runtime versions,
program/limiter/cache/evaluator source, model parameters and endpoint, all
train/validation IDs and prompt hashes, and dataset/evaluation resource
manifests. A mismatch, age over 72 hours, completed run, concurrent process,
missing committed entry, ledger/response tampering, or restricted-unpickle
failure aborts instead of silently using the entry. `rollout_id` remains in
the key, preventing stochastic candidates from collapsing into one sample.

Only provider responses with a non-empty completion and
`finish_reason="stop"` are cached. 429s, provider failures, empty responses,
and max-token truncations are never cached; truncation is a terminal
infrastructure error and is not passed to the execution metric. The cache
request redacts credentials, persisted files are mode-restricted and scanned
for `GROQ_API_KEY`, and cached usage is cleared on replay so provider tokens
are not counted twice. The manifest reports provider attempts, cache
hits/misses, replayed original token volume, waits, and retries separately.
Cache files are ignored by Git.

Recovery artifacts are written under
`artifacts/dspy/dspy001-b5/checkpoints/<run-id>/`:

- `run-state.json`: frozen identity, status, resume count, and progress count;
- `lm-cache/cache-ledger.json` plus restricted disk entries: exact successful
  provider responses with two-phase write state and response hashes;
- `metric-progress.jsonl`: append-only example/status/score/SQL-hash records,
  never generated SQL text;
- `mipro/`: DSPy's diagnostic evaluated-program snapshots.

A crash before a response exists necessarily repeats that provider call. A
crash after the cache entry reaches disk but before ledger commit is recovered
from its pending state. Recovery deliberately cannot guarantee that an
unversioned provider deployment did not change between calls; the 72-hour
window limits that exposure, and this limitation must be reported with the
experiment.

It requires `GROQ_API_KEY`. On success, it writes
`artifacts/dspy/dspy001-b5/program-state.json` using DSPy's JSON state format
and a checksum-bound `optimization-manifest.json`. Loading fails closed on
config, version, program, or artifact hash mismatch. Then run or resume the
frozen program with:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli run
```

The run command binds every checkpoint row to the config, program, database,
and prompt hashes; it requires exact 31-ID coverage before EVAL-003 scoring. No
completed live compile or B5 accuracy is claimed in this revision.
