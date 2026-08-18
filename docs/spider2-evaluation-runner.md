# Spider2-Lite evaluation runner

## Status

The 31-example SQLite development scope is ready for execution. The default
runner evaluates generated SQL against the official materialized execution
results, so the 30 unavailable reference SQL files are not required.

Pinned source commit:

```text
cafb867313aab4e674652054198f383cf4018943
```

Verified development resources:

- all 31 IDs have official gold-result CSV variants and comparison metadata;
- all six required SQLite databases are installed and checksum-verified;
- the default preflight reports `ready: true`;
- official reference SQL exists only for `local309`, but is optional.

## Evaluation modes

`gold-result` is the default and recommended mode. Generated SQL is executed
once in the isolated read-only SQLite executor. Its materialized result is
compared with every official CSV variant using the pinned `condition_cols`,
`ignore_order`, NULL normalization, and numeric tolerance rules. A match with
any valid variant scores one point.

`reference-sql` preserves the earlier strict EVAL-002 path. It executes both
generated SQL and protected reference SQL. Use it only for provenance audits
when authorized SQL is available:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli preflight \
  --reference-mode reference-sql
```

Missing SQL is never synthesized and is never exposed to prompts, retrieval, or
the generation pipeline.

## Required local layout

```text
data/raw/spider2/spider2-lite/
├── spider2-lite.jsonl
├── resource/databases/spider2-localdb/
│   ├── Airlines.sqlite
│   ├── city_legislation.sqlite
│   ├── electronic_sales.sqlite
│   ├── f1.sqlite
│   ├── music.sqlite
│   └── oracle_sql.sqlite
└── evaluation_suite/gold/
    ├── spider2lite_eval.jsonl
    └── exec_result/
        └── <example_id>[_variant].csv
```

These benchmark resources remain outside Git. Each evaluation record includes
the database checksum and the matched CSV filename and checksum. A ready
preflight can also freeze a full resource manifest.

## CLI

Validate the development resources:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli preflight \
  --split development \
  --manifest-output artifacts/reports/spider2-development-resources.json
```

Evaluate one example:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli single \
  --example-id local009 \
  --generated-sql 'SELECT ...'
```

Evaluate the exact 31-example development split:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli batch \
  --split development \
  --predictions artifacts/reports/development-predictions.jsonl \
  --output artifacts/reports/development-evaluation.json
```

Batch predictions use JSON Lines and must cover the requested split exactly:

```json
{"example_id":"local009","generated_sql":"SELECT ..."}
```

Duplicate, missing, and extra IDs fail before execution. Resource failures return
exit code 2 with a structured error. Single evaluation returns 0 for a match, 1
for a result mismatch, and 2 for execution/comparison/resource errors.

## Component boundary

```text
DATA-003 metadata
  -> exact SQLite database resolver
  -> generated SQL in isolated read-only executor
  -> official gold-result variants plus pinned comparison metadata
  -> structured record and exact-coverage Execution Accuracy
```

The generation pipeline never imports evaluation gold resources. Gold-result
CSVs and protected SQL are not added to `Text2SQLExample`, DATA-003 output,
prompts, or retrieval indexes.
