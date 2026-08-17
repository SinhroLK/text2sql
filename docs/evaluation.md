# EVAL-001 execution evaluator

## Scope

EVAL-001 executes generated SQL and reference SQL independently against the same
SQLite database, compares their result sets and returns a structured score that
can be aggregated into Execution Accuracy. It consumes the standard
`Text2SQLExample` objects produced by DATA-003; it does not reload or reinterpret
Spider2 metadata.

This task implements the evaluator core. It does not bundle Spider2 database
files, expose gold SQL to prompting code, generate predictions or run the final
104-example test experiment.

## Execution isolation

For each SQL statement the evaluator:

1. opens the source SQLite file in read-only mode;
2. backs it up into a new in-memory SQLite connection;
3. enables `PRAGMA query_only = ON`;
4. executes exactly one statement with a progress-handler timeout;
5. captures columns, rows, row count, duration and a structured error if needed;
6. closes the in-memory copy.

Generated and reference SQL run in separate copies. Neither statement can change
the source database or affect the other statement's result.

## Spider2-Lite compatibility

The comparator mirrors the pinned official `compare_pandas_table` semantics
without adding pandas as a project dependency:

- compare result values as column vectors;
- ignore output column names;
- allow `condition_cols` to select reference columns;
- preserve row order by default;
- sort values inside each column when `ignore_order=true`;
- compare numeric values with absolute tolerance `1e-2`;
- normalize SQLite `NULL`/floating NaN to `0`, matching the pinned evaluator;
- require every selected reference column vector to match a generated column
  vector.

The compatibility mode is recorded as
`spider2-lite-compatible-column-vectors`. Direct in-memory results are used
instead of a pandas/CSV round trip, so SQLite native values are retained until
comparison.

## Structured result

`SQLiteExecutionEvaluator.evaluate(...)` returns `ExecutionEvaluationResult`
with:

- `example_id` and `db_id`;
- integer `score` (`1` or `0`) and boolean `correct`;
- evaluation status;
- generated/reference execution status, columns, rows, row count and duration;
- comparison settings and matched-column counts;
- structured error category/message.

Possible top-level statuses are:

- `correct`;
- `result_mismatch`;
- `generated_execution_error`;
- `reference_execution_error`;
- `comparison_error`.

`summarize_execution_accuracy(...)` rejects duplicate, missing or extra IDs
before returning `correct / total`. This implements the exact-coverage firewall
required by DATA-001.

## Local verification

Create the existing fixture database:

```bash
python3 scripts/create_fixture_db.py
```

Evaluate two semantically equivalent queries:

```bash
PYTHONPATH=src python3 -m text2sql.evaluation.cli \
  --database data/fixtures/demo.sqlite \
  --generated-sql "SELECT first_name FROM customers ORDER BY customer_id DESC" \
  --reference-sql "SELECT first_name FROM customers ORDER BY customer_id ASC" \
  --ignore-order
```

The installed equivalent is:

```bash
text2sql-evaluate \
  --database data/fixtures/demo.sqlite \
  --generated-sql "SELECT COUNT(*) FROM customers" \
  --reference-sql "SELECT 2"
```

A correct comparison exits with code `0`, a result mismatch with `1`, and an
execution/comparison error with `2`.

## Limitations

- SQLite only; BigQuery and Snowflake are outside the first-version scope.
- Full Spider2-Lite evaluation still requires separately acquired official
  SQLite databases and protected evaluation inputs.
- The official pinned evaluator has no effective SQLite timeout; this wrapper
  adds an outer progress-handler timeout for reproducibility.
- SQL AST allowlisting, production sandboxing and security guardrails belong to
  later `SAFE-*`/`SEC-*` tasks, not EVAL-001.
