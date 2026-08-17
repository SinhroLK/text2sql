# Text-to-SQL research project

Reproducible project foundation for the master thesis **Natural Language to SQL Translation Using Large Language Models**.

This Phase 1 foundation uses a deterministic mock provider and a small SQLite fixture for executable smoke tests. `DATA-001` additionally freezes the Spider2-Lite SQLite benchmark protocol before loaders, real models or evaluators are added.

## Requirements

- Python 3.11 or newer
- No runtime dependencies for the current Phase 1 DATA-001 foundation

## Quick start

```bash
python scripts/create_fixture_db.py
PYTHONPATH=src python -m text2sql.cli \
  --question "List customer names" \
  --database data/fixtures/demo.sqlite \
  --output artifacts/reports/data001-smoke.jsonl
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
- pinned Spider2-Lite source/evaluator checksums;
- deterministic database-disjoint SQLite split (31 development / 104 test);
- executable protocol validation and leakage-policy tests;
- documented benchmark, evaluation and reporting contract.

Not implemented yet:

- Spider2-Lite loader and official evaluator wrapper (`DATA-003`, `EVAL-001`);
- Groq or another real LLM provider;
- M-Schema;
- retrieval and DSPy optimization;
- SQL AST validation and sandbox execution;
- security evaluation;
- Gradio application built on the new pipeline.

## Security

Do not copy active API keys, database passwords or production databases into this repository. The future runtime will use a read-only sandbox user and AST-based SQL validation. The Phase 0 CLI generates SQL but does not execute it.

## Dataset protocol

The headline benchmark is the pinned 135-example SQLite portion of Spider2-Lite, split by database into 31 development and 104 test examples. It is explicitly a custom research split, not the full Spider2-Lite leaderboard setting. See `docs/experiments.md` and validate it with the normal test command.

## Repository map

- `src/text2sql/` - reusable pipeline code;
- `configs/` - versioned experiment and security configuration;
- `tests/` - tests that run without external services;
- `data/fixtures/` - small synthetic test resources;
- `notebooks/legacy/` - original proof-of-concept experiments;
- `docs/` - architecture, experiment and decision records;
- `artifacts/` - generated results, excluded from Git by default.
