# EVAL-002 Spider2-Lite integration runner

## Status

The integration code and fixture verification are implemented. `EVAL-002` is
`BLOCKED`, not `DONE`, because the pinned real development resources available
to this project are incomplete.

Pinned source commit:

```text
cafb867313aab4e674652054198f383cf4018943
```

Verified resource state at that commit:

- `spider2-lite.jsonl`: present, 547 records / 135 SQLite records;
- `gold/spider2lite_eval.jsonl`: present, all 31 development IDs covered;
- `gold/exec_result`: public execution results cover all 31 development IDs;
- `gold/sql`: only `local309.sql` covers the chosen 31-example development split;
- SQLite database archive: downloaded separately and not present locally.

The 30 missing development reference SQL IDs are:

```text
local009 local010 local068 local070 local071 local072 local167 local168
local169 local170 local171 local202 local244 local269 local270 local272
local273 local274 local275 local277 local279 local286 local310 local311
local335 local336 local344 local354 local355 local356
```

The six required development databases are:

```text
Airlines.sqlite
city_legislation.sqlite
electronic_sales.sqlite
f1.sqlite
music.sqlite
oracle_sql.sqlite
```

No missing SQL is synthesized. Public gold execution CSV files are not silently
substituted for reference SQL because EVAL-002's accepted contract requires both
generated and reference SQL to pass through EVAL-001.

## Expected local layout

Raw benchmark and protected evaluation resources remain outside Git:

```text
data/raw/spider2/
└── spider2-lite/
    ├── spider2-lite.jsonl
    ├── resource/databases/spider2-localdb/
    │   ├── Airlines.sqlite
    │   ├── city_legislation.sqlite
    │   ├── electronic_sales.sqlite
    │   ├── f1.sqlite
    │   ├── music.sqlite
    │   └── oracle_sql.sqlite
    └── evaluation_suite/gold/spider2lite_eval.jsonl

data/private/spider2-lite/gold/sql/
└── <example_id>.sql
```

The official pinned README provides the separate local database download URL.
Reference SQL must come from an authorized official evaluation source tied to
the same pinned snapshot. Its separate `data/private` location makes the
`model pipeline != evaluation gold data` boundary explicit. Do not put it in
`data/raw`, `data/processed`, prompts, retrieval indexes, model inputs or Git.

## Reproducibility checks

Verify the pinned Git resources:

```bash
git -C data/raw/spider2 rev-parse HEAD
sha256sum \
  data/raw/spider2/spider2-lite/spider2-lite.jsonl \
  data/raw/spider2/spider2-lite/evaluation_suite/evaluate.py \
  data/raw/spider2/spider2-lite/evaluation_suite/evaluate_utils.py \
  data/raw/spider2/spider2-lite/evaluation_suite/gold/spider2lite_eval.jsonl
```

Expected hashes, in the same order:

```text
4ba48916576fbd60311a2478c6d4550b5d8cf3fcbc512457ea493b5941ca009d
e93624e4ffa00e51c67bdee1d9e42b534630087cb453270c0dedc0c73e618480
d740984e9097b29aa8d9d01e1b978ae71163309dce1954eaff84cdf2e9834053
5113ddaea6107492c08a898241a9f69ec99b03bc60d014e9f0b12ec59fa28970
```

After acquiring the database and protected SQL resources, record their hashes
without committing their contents:

```bash
find data/raw/spider2/spider2-lite/resource/databases/spider2-localdb \
  -maxdepth 1 -type f -name '*.sqlite' -print0 \
  | sort -z | xargs -0 sha256sum \
  > artifacts/reports/spider2-development-database-sha256.txt
```

Each structured EVAL-002 record also stores the resolved database and reference
SQL SHA-256 values.

## Prediction format

Batch predictions are JSON Lines with exact split coverage:

```json
{"example_id":"local009","generated_sql":"SELECT ..."}
```

Duplicate, missing and extra IDs fail the run before SQL execution.

## CLI

Validate all development resources before any execution:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli preflight \
  --split development
```

The current expected output has `status: blocked`, six missing database IDs and
30 missing reference IDs. After all resources are present, the command returns
`status: ready`. It can also freeze a resource manifest with
`--manifest-output artifacts/reports/spider2-development-resources.json`.

Evaluate one example:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli single \
  --example-id local009 \
  --generated-sql 'SELECT ...'
```

Evaluate the complete 31-example development split:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.spider2_cli batch \
  --split development \
  --predictions artifacts/reports/development-predictions.jsonl \
  --output artifacts/reports/development-evaluation.json
```

All paths have project-layout defaults. They can be overridden with
`--source-jsonl`, `--config`, `--expected-dataset-manifest`, `--database-root`,
`--reference-root` and `--standards-jsonl`. The installed command is
`text2sql-evaluate-spider2`. Resource failures return exit code `2` and a JSON
object with a stable error code and context.

## Component boundary

The runner follows this flow:

```text
DATA-003 metadata
  -> exact database resolver
  -> protected reference SQL store
  -> generated SQL
  -> EVAL-001
  -> structured record and exact-coverage Execution Accuracy
```

`text2sql.pipeline` does not import the protected store. Reference SQL is never
added to `Text2SQLExample` or DATA-003 output.
