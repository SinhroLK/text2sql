# Text-to-SQL research project

Reproducible project foundation for the master thesis **Natural Language to SQL Translation Using Large Language Models**.

This project foundation uses a deterministic mock provider and a small SQLite fixture for executable smoke tests. `DATA-001` freezes the Spider2-Lite SQLite benchmark protocol, `DATA-003` provides its checksum-gated metadata loader, and `EVAL-001` provides a structured SQLite execution evaluator.

## Requirements

- Python 3.11 or newer
- No runtime dependencies for the current EVAL-001 foundation

## Quick start

```bash
python scripts/create_fixture_db.py
PYTHONPATH=src python -m text2sql.cli \
  --question "List customer names" \
  --database data/fixtures/demo.sqlite \
  --output artifacts/reports/data001-smoke.jsonl
```

The command prints one JSON result and appends the same structured record to the requested JSONL file. The Phase 0 pipeline does **not** execute generated SQL. EVAL-001 provides a separate evaluation-only SQLite executor; production pipeline execution and AST validation remain planned for Phase 5.

## Run tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Optional editable install

```bash
python -m pip install --no-build-isolation -e .
text2sql --question "List customer names" --database data/fixtures/demo.sqlite
```

## Current scope

Implemented:

- stable domain models;
- SQLite schema inspection;
- deterministic schema serialization for the baseline prompt;
- provider protocol and deterministic mock provider;
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
- local evaluator CLI and fixture-backed integration tests.

Not implemented yet:

- Spider2 SQLite database archive ingestion and execution;
- Groq or another real LLM provider;
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
PYTHONPATH=src python -m text2sql.datasets.cli
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
- `artifacts/` - generated results, excluded from Git by default.
