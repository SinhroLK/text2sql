# Text-to-SQL research project

Reproducible project foundation for the master thesis **Natural Language to SQL Translation Using Large Language Models**.

This project foundation uses a deterministic mock provider and a small SQLite fixture for executable smoke tests. `DATA-001` freezes the Spider2-Lite SQLite benchmark protocol, `DATA-003` provides its checksum-gated metadata loader, `EVAL-001` provides structured execution comparison, and `EVAL-003` provides the ready official gold-result runner. The strict reference-SQL `EVAL-002` path remains optional.

## Requirements

- Python 3.11 or newer
- Python dependencies installed from `requirements.lock` (including the official Groq SDK)

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
- a default Spider2 preflight that is ready for all 31 development examples.

Not implemented yet:

- M-Schema;
- retrieval and DSPy optimization;
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
- `configs/` - versioned experiment and security configuration;
- `configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json` - frozen
  DATA-003 output contract;
- `tests/` - tests that run without external services;
- `data/fixtures/` - small synthetic test resources;
- `notebooks/legacy/` - original proof-of-concept experiments;
- `docs/` - architecture, experiment, decision and source records;
- `docs/sources-and-references.md` - living register of every paper, dataset,
  repository, documentation source and legacy input used by the project;
- `docs/evaluation.md` - EVAL-001 execution and comparison contract;
- `docs/spider2-evaluation-runner.md` - EVAL-003 default workflow and optional EVAL-002 audit mode;
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

Re-running the same command resumes from its JSONL checkpoint and does not repeat completed IDs. Starting both fresh runs requires 62 Groq requests before retries. Do not run the 104-example test split during development.
