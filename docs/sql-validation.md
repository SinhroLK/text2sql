# SAFE-001 SQLite SQL validation

`text2sql.security.SQLiteSQLValidator` validates a generated query before any execution boundary sees it. It accepts exactly one SQLite `SELECT`, including `WITH`, joins, subqueries, aggregates, windows, unions, and recursive CTEs.

Validation has three fail-closed layers:

1. `sqlparse==0.5.5` produces a hierarchical token tree. The validator rejects comments, multiple statements, placeholders, non-`SELECT` roots, DDL/DML/transaction keywords, and known filesystem or extension functions.
2. The canonical `SchemaSnapshot` is reconstructed as empty tables in an isolated in-memory SQLite connection. Types, defaults, indexes, triggers, views, and source data are deliberately absent.
3. SQLite prepares `EXPLAIN QUERY PLAN <query>` with an authorizer that permits only `SELECT`, `READ`, safe function lookup, and recursive CTE planning. Reads must resolve to canonical tables and columns; system tables, pragma table functions, attached schemas, and every other authorization action are denied.

The submitted query is never evaluated. Preparation can therefore validate SQLite grammar, aliases, CTE output columns, ambiguous names, and correlated references without reading application rows or invoking query expressions.

Each `SQLValidationResult` records the validator version, statement type, exact SQL SHA-256, literal-redacted AST SHA-256, canonical tables and columns observed by SQLite, and one structured issue when validation fails. `require_valid()` raises `SQLValidationError` while preserving that result for the SAFE-002 handoff.

Validate from the command line:

```bash
PYTHONPATH=src python3 -m text2sql.security.cli \
  --database data/fixtures/demo.sqlite \
  --sql "SELECT first_name FROM customers ORDER BY customer_id"
```

Use `--sql-file path/to/query.sql` for a file and `--include-ast` to include the normalized parsed tree. Exit status is `0` for a valid query and `2` for a rejected query.

SAFE-001 does not execute SQL, impose runtime time/row/resource limits, or replace the evaluation executor. SAFE-002 owns those controls and must accept only a query accompanied by a successful SAFE-001 result whose SQL hash still matches.
