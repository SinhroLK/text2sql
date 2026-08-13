# Text-to-SQL research project

Reproducible project foundation for the master thesis **Natural Language to SQL Translation Using Large Language Models**.

This Phase 0 version deliberately uses a deterministic mock provider and a small SQLite fixture. It validates the project structure, schema inspection, prompt construction, CLI and JSONL audit trail without requiring API keys or access to a real database.

## Requirements

- Python 3.11 or newer
- No runtime dependencies for Phase 0

## Quick start

```bash
python scripts/create_fixture_db.py
PYTHONPATH=src python -m text2sql.cli \
  --question "List customer names" \
  --database data/fixtures/demo.sqlite \
  --output artifacts/reports/phase0-demo.jsonl
```

The command prints one JSON result and appends the same structured record to the requested JSONL file. The generated SQL is **not executed** in Phase 0. Safe execution and AST validation are planned for Phase 5.

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
- preserved legacy notebooks.

Not implemented yet:

- official Spider/Spider 2.0 loaders and evaluator;
- Groq or another real LLM provider;
- M-Schema;
- retrieval and DSPy optimization;
- SQL AST validation and sandbox execution;
- security evaluation;
- Gradio application built on the new pipeline.

## Security

Do not copy active API keys, database passwords or production databases into this repository. The future runtime will use a read-only sandbox user and AST-based SQL validation. The Phase 0 CLI generates SQL but does not execute it.

## Repository map

- `src/text2sql/` - reusable pipeline code;
- `configs/` - versioned experiment and security configuration;
- `tests/` - tests that run without external services;
- `data/fixtures/` - small synthetic test resources;
- `notebooks/legacy/` - original proof-of-concept experiments;
- `docs/` - architecture, experiment and decision records;
- `artifacts/` - generated results, excluded from Git by default.

