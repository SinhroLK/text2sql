# Text-to-SQL research project

**Development update (5 September):** evaluator side-effect rejection and an opt-in v2 join-evidence contract are implemented. Relational scopes and run provenance remain ahead of MODEL-001. See [review follow-up](docs/review-followup-2026-09-05.md).

Reproducible project foundation for the master thesis **Natural Language to SQL Translation Using Large Language Models**.

This project foundation uses a deterministic mock provider and a small SQLite fixture for executable smoke tests. `DATA-001` freezes the Spider2-Lite SQLite benchmark protocol, `DATA-003` provides its checksum-gated metadata loader, `EVAL-001` provides structured execution comparison, `EVAL-003` provides the official gold-result runner, SCHEMA-002 provides deterministic M-Schema prompts, LINK-001 records the completed linked-M-Schema B6 experiment, LINK-002 provides a recall-repaired B6R arm, RET-001 provides a checksum-gated Spider 1.0 train-only retrieval index, RET-002 provides completed B3/B4 few-shot experiments, DSPY-001 records the completed B5 optimization and development run, SEM-001/SEM-002 provide error analysis plus a typed schema-validated relational plan, and RET-003 provides deterministic question-plus-plan structural retrieval. The strict reference-SQL `EVAL-002` path remains optional.

## Requirements

- Python 3.11 or newer
- Python dependencies installed from `requirements.lock` (including DSPy 3.3.1, LiteLLM 1.99.0, Optuna 4.9.0, and the official Groq SDK)

## Quick start

```bash
python scripts/create_fixture_db.py
PYTHONPATH=src python3 -m text2sql.cli \
  --question "List customer names" \
  --database data/fixtures/demo.sqlite \
  --output artifacts/reports/data001-smoke.jsonl
```

The command prints one JSON result and appends the same structured record to the requested JSONL file. The generation pipeline does **not** execute generated SQL. EVAL-001 provides a separate evaluation-only SQLite executor; production pipeline execution and AST validation remain planned for Phase 5.

## Run tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Optional editable install

```bash
python3 -m pip install --no-build-isolation -e .
text2sql --question "List customer names" --database data/fixtures/demo.sqlite
```

## Current scope

Implemented:

- stable domain models;
- SQLite schema inspection;
- deterministic schema serialization for the baseline prompt;
- provider protocol, deterministic mock provider and official-SDK Groq adapter;
- validated canonical schema model with deterministic serialization and hashes;
- end-to-end pipeline;
- JSONL audit output;
- unit and integration tests;
- versioned configuration placeholders;
- preserved legacy notebooks;
- pinned Spider2-Lite source/evaluator checksums;
- deterministic database-disjoint SQLite split (31 development / 104 test);
- executable protocol validation and leakage-policy tests;
- documented benchmark, evaluation and reporting contract;
- pinned Spider2-Lite SQLite metadata loader;
- checksum validation before JSON parsing;
- deterministic normalized metadata and dataset manifest generation;
- strict 31-development / 104-test ID and database firewall;
- rejection of gold-like SQL fields during metadata ingestion;
- isolated read-only execution of generated and reference SQLite SQL;
- Spider2-Lite-compatible result comparison with order, condition-column,
  NULL and numeric-tolerance handling;
- structured evaluation results and exact-ID Execution Accuracy aggregation;
- local evaluator CLI and fixture-backed integration tests;
- deterministic `db_id` resolver, protected reference SQL store and Spider2 batch runner;
- strict duplicate/missing/extra prediction and reference coverage;
- official gold-result CSV variant evaluation with per-resource checksums;
- a default Spider2 preflight that is ready for all 31 development examples;
- deterministic lexical table/column schema linking with value matches;
- primary/foreign-key preservation and shortest-path FK closure;
- recall-safe full-schema fallback and per-selection audit metadata;
- frozen linked-M-Schema B6 config, 31-example audit, and completed negative live result;
- separate B6R hybrid full-compact/linked-detail prompt, config, audit, and tests.
- pinned official Spider 1.0 training source, license, archive and input checksums;
- deterministic 7,000-example / 140-database train-only retrieval index;
- exact Spider2 ID, database and normalized-question leakage firewall;
- version-controlled retrieval manifest and checksum-verifying artifact loader.
- frozen B3 fixed-random and B4 TF-IDF cosine selectors and experiment configs;
- few-shot M-Schema prompt integration and provider-free per-target retrieval audit.
- frozen DSPY-001/B5 signature, database-disjoint 21/10 development split,
  execution-result metric, offline audit CLI, completed MIPROv2 compile, and
  checksum-verified 31-example B5 result (4/31 correct, 28/31 executable).
- completed provider-free SEM-001 paired error corpus over B1/B6R/B4/B5 with
  exact 31-ID coverage and frozen labels for all 27 B5 failures.
- versioned SEM-002 `SemanticPlan`, strict JSON parser, schema/FK/JOIN-graph
  validator, one plan-only repair boundary, deterministic plan/schema hashes,
  provider-free validation CLI, and offline fixtures.
- deterministic 7,000-entry SQL-skeleton/operator index derived from verified
  Spider 1.0 train SQL, with a frozen manifest and recomputing loader;
- question-plus-`SemanticPlan` hybrid ranking with per-component audit,
  structural-match filtering, and demonstration count/size bounds.
- provider-free `gen001-b7p-composer-v1` prompt composition combining B6R
  evidence, a validated SEM-002 plan, bounded RET-003 demonstrations, selective
  value grounding, exact dependency/provenance checks, and one-candidate audit.

Not implemented yet:

- MODEL-001 capability selection and the live 31-example GEN-001/B7P run;
- the GEN-001 promotion decision and checkpoint/report artifacts;
- SQL AST validation and sandbox execution;
- security evaluation;
- Gradio application built on the new pipeline.

## Security

Do not copy active API keys, database passwords or production databases into this repository. The evaluation-only executor works on isolated in-memory SQLite copies. The future production runtime will additionally use a read-only sandbox user and AST-based SQL validation; the Phase 0 generation CLI still does not execute SQL.

## Dataset protocol

The headline benchmark is the pinned 135-example SQLite portion of Spider2-Lite, split by database into 31 development and 104 test examples. It is explicitly a custom research split, not the full Spider2-Lite leaderboard setting. See `docs/experiments.md` and validate it with the normal test command.

After placing the pinned upstream repository under `data/raw/spider2`, prepare the metadata with:

```bash
PYTHONPATH=src python3 -m text2sql.datasets.cli
```

The command verifies the source checksum and the version-controlled DATA-003
manifest before writing `examples.jsonl` and `dataset-manifest.json` under
`data/processed/spider2-lite-sqlite-v1/`. It does not read or emit gold SQL and
does not execute database queries.

## Train-only retrieval index

After acquiring the official Spider archive as documented in `data/README.md`,
build and verify the RET-001 artifact with:

```bash
PYTHONPATH=src python3 -m text2sql.retrieval.cli
```

The command verifies the pinned Spider 1.0 train and schema files before parsing,
requires exact coverage of the frozen Spider2 firewall, rejects every Spider2 ID,
database or normalized-question overlap, and writes 7,000 deterministic training
entries under `artifacts/retrieval/spider1-train-v1/`. RET-002 connects the
verified index to separately frozen B3/B4 few-shot prompts.

Audit both selectors over all 31 development targets without a provider key:

```bash
PYTHONPATH=src python3 -m text2sql.experiments.retrieval_cli \
  --experiment-config configs/experiments/exp005-b3.toml
PYTHONPATH=src python3 -m text2sql.experiments.retrieval_cli \
  --experiment-config configs/experiments/exp006-b4.toml
```

The audit records retrieved IDs, source databases, ranks and similarity scores,
but does not call Groq or read Spider2 test outcomes. Completed development
results are B3 4/31 and B4 5/31; the sealed test split was not opened.

Build and verify the RET-003 structure index from that exact RET-001 artifact:

```bash
PYTHONPATH=src python3 -m text2sql.retrieval.structural_cli
```

The structural artifact contains normalized skeletons and operator signatures,
not duplicate question/SQL text. The hybrid selector combines question TF-IDF
with operators derived from a validated `SemanticPlan`, audits both raw and
weighted components, returns at most three demonstrations, and enforces SQL-size
budgets. No structural match produces no demonstrations rather than the full B4
context. RET-003 is provider-free implementation evidence; its current
per-target audit is fixture-backed until GEN-001 produces frozen development
plans. See `docs/structural-retrieval.md`.

## DSPY-001 B5

Audit the frozen B5 inputs without a provider key:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli audit \
  --report artifacts/reports/dspy001-b5-audit.json
```

The frozen config uses MIPROv2 with 3 candidates and 5 trials, zero
DSPy-level bootstrapped/labeled demonstrations, and a database-disjoint
21-example training/10-example validation split. The three permitted Spider 1.0
demonstrations remain inside each inherited B4 context.

Start a new paid compile only to reproduce the frozen optimization as an
independent paid run:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli optimize
```

The command prints a unique recovery run ID near startup. If the process is
interrupted, resume that exact run (within 72 hours) with:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli optimize \
  --resume-run-id run-YYYYMMDDtHHMMSSz-xxxxxxxxxxxx
```

A plain `optimize` command always starts an independent cache, so separate
experiments cannot silently share model outputs. Resume restarts deterministic
MIPROv2 orchestration and replays only identical successful LM calls; it is not
an in-place Optuna snapshot. Cache identity covers config, code, dependency,
model, endpoint, prompt, split, dataset, database and gold-result hashes.
Different stochastic `rollout_id` values remain different cache keys.
Corrupt, stale, mismatched, evicted, concurrent, secret-bearing, failed, empty,
or truncated responses fail closed or are not cached.

The optimizer also enforces the frozen 8,000 TPM Groq tier with a 90% rolling
safety budget (7,200 tokens). Cache hits bypass both Groq and the limiter and
are reported separately from provider usage. A 429 honors Groq's retry delay
and is retried up to eight times. A `finish_reason="length"` response stops
the run before it can be cached or scored.

Recovery data lives under
`artifacts/dspy/dspy001-b5/checkpoints/<run-id>/`; it contains an identity and
status file, an integrity-ledger-backed LM cache, hash-only metric progress, and
MIPRO diagnostic snapshots. It never contains the API key. On success the
program and checksum-bound optimization manifest are written under
`artifacts/dspy/dspy001-b5/`.

The completed frozen compile selected the original/default instruction with a
2/10 validation score. Its exact 31-example development run scored 4/31
(12.90%) with 28/31 executable queries, below B4 at 5/31 and B6R at 6/31.
The program, optimization manifest, predictions, and EVAL-003 report are
checksum-bound; the Spider2 test split remains sealed.

After a fresh compilation, run or resume exact 31-example generation and
EVAL-003 scoring:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.optimization.cli run
```

## SEM-001 paired semantic-error analysis

Rebuild the development-only error corpus without a provider key:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.analysis.cli
```

The analysis checksum-verifies the B1, B6R, B4, and B5 predictions and
EVAL-003 reports before parsing, requires exact coverage of the same 31
development IDs, and requires frozen primary/secondary labels for exactly the
27 B5 failures. It neither loads gold SQL nor reads Spider2 test examples.

The three dominant B5 failure categories are aggregation/grouping (6), output
shape (5), and JOIN path/cardinality (4). Twenty-one examples fail in all four
arms, eight are prompt-sensitive, and two are correct in every arm. The
checksum-bound JSONL, Markdown matrix, and manifest are written under
`artifacts/reports/`; the frozen labeling contract is
`configs/analysis/sem001-paired-errors-v1.json`.

SEM-001 is complete and supplies the failure evidence used by SEM-002.

## SEM-002 typed semantic planning

SEM-002 is implemented provider-free. It defines `semantic-plan-v1` for output
shape, sources, joins, predicates/literals, aggregation/grouping, ordering,
limits/ties, temporal logic, recursion/set operations, and explicit
uncertainties. Plans are strict JSON and must validate against exact canonical
schema identifiers and declared foreign-key paths before SQL composition.

Validate and hash a plan against a local SQLite schema with:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.planning.cli \
  --plan path/to/plan.json \
  --database path/to/database.sqlite \
  --db-id database_id \
  --question "The exact source question"
```

An invalid initial plan may receive exactly one caller-supplied plan-only
correction; a second invalid response fails closed. A successful record includes
the canonical plan hash and canonical schema-evidence hash for later GEN-001
prediction metadata. SEM-002 makes no provider call and does not yet improve or
score SQL. See `docs/semantic-planning.md`. RET-003 and the offline GEN-001
composer now consume this plan shape.

## GEN-001 offline B7P composer

The provider-independent `gen001-b7p-composer-v1` contract is frozen. It binds
the exact B6R and RET-003 dependencies, requires a checksum-valid SEM-002 plan,
and produces a deterministic prompt containing the complete compact schema,
linked detailed M-Schema, zero to three structural demonstrations, selective
filter-column values, and the exact question. It requests exactly one read-only
SQLite query and records prompt/schema/plan/retrieval/dependency hashes.

After building RET-001 and RET-003, compose and audit a prompt without a
provider call:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.generation.cli \
  --plan path/to/semantic-plan.json \
  --database path/to/database.sqlite \
  --db-id database_id \
  --question "The exact source question" \
  --prompt-output artifacts/prompts/b7p.txt \
  --audit-output artifacts/reports/b7p-composer-audit.json
```

Six fixtures cover JOIN, nested aggregation/subquery, temporal-window, set and
recursive shapes plus grounding and provenance failures. This is not yet a B7P
accuracy result: MODEL-001 must choose a model under the same frozen contract,
then GEN-001 must produce and score the exact 31-development-ID checkpoint. See
`docs/b7p-composer.md`.

## Execution evaluator

Create the fixture database and compare two SQL statements:

```bash
python3 scripts/create_fixture_db.py
PYTHONPATH=src python3 -m text2sql.evaluation.cli \
  --database data/fixtures/demo.sqlite \
  --generated-sql "SELECT first_name FROM customers ORDER BY customer_id DESC" \
  --reference-sql "SELECT first_name FROM customers ORDER BY customer_id ASC" \
  --ignore-order
```

The evaluator runs each statement in a separate read-only in-memory copy and
prints a structured JSON result. See `docs/evaluation.md` for comparison rules,
exit codes and limitations.

The pinned Spider2 integration commands, required local layout, default gold-result
mode, and optional strict SQL audit mode are documented in `docs/spider2-evaluation-runner.md`.

## Repository map

- `src/text2sql/` - reusable pipeline code;
- `src/text2sql/generation/` - provider-free GEN-001 B7P composition contract and CLI;
- `src/text2sql/retrieval/` - train-only indexes, leakage firewall, and structural selector;
- `configs/` - versioned experiment and security configuration;
- `src/text2sql/analysis/` - provider-free SEM-001 paired error analysis;
- `src/text2sql/planning/` - typed SEM-002 plan, validation, repair boundary, and CLI;
- `configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json` - frozen
  DATA-003 output contract;
- `tests/` - tests that run without external services;
- `data/fixtures/` - small synthetic test resources;
- `notebooks/legacy/` - original proof-of-concept experiments;
- `docs/` - architecture, experiment, decision and source records;
- `docs/sources-and-references.md` - living register of every paper, dataset,
  repository, documentation source and legacy input used by the project;
- `docs/evaluation.md` - EVAL-001 execution and comparison contract;
- `docs/schema-linking.md` - LINK-001 algorithm, frozen policy, offline audit, and B6 commands;
- `docs/b7p-composer.md` - frozen B7P prompt, evidence, audit, and remaining live steps;
- `docs/semantic-planning.md` - SEM-002 contract, validation boundary, and limitations;
- `docs/spider2-evaluation-runner.md` - EVAL-003 default workflow and optional EVAL-002 audit mode;
- `docs/structural-retrieval.md` - RET-003 extraction, ranking, audit, and bounds;
- `artifacts/` - generated results, excluded from Git by default.

## Optional Groq smoke run

The mock provider remains the default. A Groq request is explicit and requires an environment key:

```bash
export GROQ_API_KEY="..."
python3 scripts/create_fixture_db.py
PYTHONPATH=src python3 -m text2sql.cli \
  --question "List customer names" \
  --database data/fixtures/demo.sqlite \
  --provider groq \
  --model-id openai/gpt-oss-120b \
  --temperature 0 \
  --max-tokens 1024 \
  --output artifacts/reports/groq-smoke.jsonl
```

## EXP-001 baseline runner

The B0/B1 development runner is resumable and restricted to the frozen 31-example development split. It checkpoints each successful provider response before continuing and automatically runs EVAL-003 only after exact coverage. The completed EXP-001 run scored B0 at 0/31 and schema-aware B1 at 5/31 (16.13%); reports are stored in `artifacts/reports/`.

Load `GROQ_API_KEY` into the environment, then run B0 (question only):

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
  --experiment-config configs/experiments/exp001-b0.toml \
  --predictions artifacts/experiments/exp001-b0-predictions.jsonl \
  --report artifacts/reports/exp001-b0-report.json
```

Run B1 (question plus complete simple schema):

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
  --experiment-config configs/experiments/exp001-b1.toml \
  --predictions artifacts/experiments/exp001-b1-predictions.jsonl \
  --report artifacts/reports/exp001-b1-report.json
```

Re-running the same command resumes from its JSONL checkpoint and does not repeat completed IDs. Starting both fresh runs requires 62 Groq requests before retries.

## SCHEMA-002 B2 result

The completed 31-example B2 run scored 2/31 (6.45%) with 22/31 executable SQL outputs, compared with B1 at 5/31 (16.13%) and 26/31 executable. B2 used 107,586 input tokens—109.8% more than B1—so full M-Schema did not improve this baseline. To reproduce or re-score the checkpoint, run:

```bash
PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
  --experiment-config configs/experiments/exp002-b2.toml \
  --predictions artifacts/experiments/exp002-b2-predictions.jsonl \
  --report artifacts/reports/exp002-b2-report.json
```

This uses the same model/settings and 31 development IDs as B1, with M-Schema as the only prompt-arm change. With the completed checkpoint present, it makes no Groq requests and regenerates the report. The 104-example test split remains sealed.


## Schema-linking status

LINK-001 is **DONE**. Frozen B6 covers 31/31 development examples and scored
1/31 (3.23%) with 27/31 executable outputs. It used 18,287 input tokens, but
its aggressive pruning removed identifiers needed by previously correct B1
examples. The negative result and artifact hashes are preserved.

LINK-002 is **DONE**. B6R keeps every column in selected tables and also shows
the model the complete compact schema, while retaining linked M-Schema as
priority detail. It covers 31/31 development examples and scores 6/31 (19.35%)
with 25/31 executable outputs, improving by one correct example over B1.

Run the provider-free B6R audit:

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.linking_cli \
      --experiment-config configs/experiments/exp004-b6r.toml \
      --output artifacts/reports/exp004-b6r-linking-audit.json

Re-score completed B6R (no provider calls with the complete checkpoint):

    PYTHONPATH=src .venv/bin/python -m text2sql.experiments.cli \
      --experiment-config configs/experiments/exp004-b6r.toml \
      --predictions artifacts/experiments/exp004-b6r-predictions.jsonl \
      --report artifacts/reports/exp004-b6r-report.json

Prediction/report SHA-256 values are `46664f819c...6577` and
`aaa130ec8e...2fc`. See **docs/schema-linking.md** for both frozen policies,
comparative analysis, failures, limitations, and full hashes.
