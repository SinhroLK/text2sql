# SAFE-002 SQLite sandbox execution

`text2sql.execution.SQLiteSandboxExecutor` validates and executes one read-only SELECT in a fresh Linux subprocess. It is independent of the generation pipeline and the EVAL-001/EVAL-003 scoring paths. REF-001 can consume its structured output without receiving raw SQLite exceptions.

## Usage

Create the fixture if needed, then explicitly execute a query:

```bash
python3 scripts/create_fixture_db.py
PYTHONPATH=src .venv/bin/python -m text2sql.execution.cli \
  --database data/fixtures/demo.sqlite \
  --sql "SELECT first_name FROM customers ORDER BY customer_id"
```

The installed entry point is `text2sql-execute-sql`. It also accepts `--sql-file`, `--timeout-seconds`, and `--max-result-rows`. Exit code 0 means complete successful execution; rejection, errors, timeouts, and truncated previews return 2. The generation CLI remains generation-only.

For an existing SAFE-001 handoff:

```python
from text2sql.execution import SQLiteSandboxExecutor

result = SQLiteSandboxExecutor().execute(database_path, sql, validation_result)
```

The executor rejects an unsuccessful validation, unknown validator version, or changed SQL hash before spawning. The worker then independently validates against the actual database snapshot; forged success flags and stale schema validations cannot bypass that check. For callers without a prior result, `validate_and_execute(database_path, sql)` performs SAFE-001 inside the same bounded worker before executing the submitted query.

## Isolation and limits

1. The parent starts a fresh interpreter with no inherited application connections, registered UDFs, or provider-key environment variables. The deadline covers interpreter startup, reading the database, validation, execution, serialization, and response collection. A timeout kills and reaps the worker.
2. Linux resource limits bound address space and CPU time and prohibit file growth/core dumps. Failure to establish limits prevents execution. SQLite temporary storage stays in memory.
3. The worker reads a bounded regular file into a byte image and hashes those exact bytes. SQLite never opens the source file. The worker deserializes only the isolated image and enables `query_only`, disables trusted-schema behavior and extension loading, and prohibits attached databases.
4. It inspects the same connection, runs SAFE-001 over the resulting canonical schema, and installs the existing SELECT/READ/function authorizer on the execution connection. Virtual-table databases and views as query targets are unsupported.
5. Results are fetched incrementally. The executor does not inject LIMIT into the SQL. A row beyond the cap distinguishes an exact-size complete result from a truncated preview; a query that cannot produce that lookahead row before the deadline times out.

| Setting | Default |
|---|---:|
| Total worker deadline | 10 seconds |
| Returned rows | 1,000 |
| Encoded result data (`columns` and `rows`) | 1 MiB |
| SQLite string/BLOB/row length limit | 256 KiB |
| Source database image | 64 MiB |
| Worker address space | 256 MiB |
| SQL length | 100,000 characters |

`SandboxPolicy` is authoritative and validates overrides. `configs/security_policy.yaml` documents its defaults; editing that file does not automatically alter runtime behavior. Memory and deadline limits also protect schema preparation and large allocations inside a single SQLite expression. CPU exhaustion or other abnormal worker exits are conservatively reported as `resource_limit/worker_terminated`, not diagnosed more precisely without evidence.

## Input contract and practical limits

Supply a static, standalone SQLite snapshot in rollback-journal format. WAL-mode headers and existing `-wal`, `-shm`, or `-journal` files are rejected. Export a consistent snapshot from a live database using an external trusted workflow before calling SAFE-002; do not remove journals or rewrite headers to make a live database pass. The executor detects ordinary modifications during its read but does not provide transaction-consistent access to concurrently written files.

The tested guarantees concern database/file contents and directory entries; ordinary OS access-time changes from reading files are not excluded. The worker is a resource-limited process with SQL-level controls, not an OS container or a general arbitrary-code sandbox. Callers must select authorized database paths. Hostile SQLite engine exploits and Python code execution require a stronger OS isolation boundary. Unsupported platforms fail closed. Python/SQLite must expose `Connection.deserialize()`.

## Result and audit contract

`SandboxResult` contains status, rows/columns, truncation flag, duration, policy, SQL hash, snapshot hash, canonical schema hash, and a sanitized validation summary. Schema hashes use the stable internal database ID `sandbox`; do not compare them directly with prompt hashes using another database ID. Failures before snapshot preparation, or a killed worker, may have no database/schema/validation hashes.

Statuses are `success`, `truncated`, `validation_rejected`, `timeout`, `resource_limit`, and `execution_error`. Only `success` sets `succeeded=True`. Row/byte previews are explicitly incomplete and must never be scored as full benchmark results. A single oversized value or execution failure returns no partial rows.

Errors contain fixed public messages and codes, never raw SQL, database paths, literals, or SQLite exception text. Returned rows and column labels are intentionally user data; callers decide whether to log them. BLOBs use `{"type":"blob","base64":"..."}` and non-finite floats use `{"type":"float","value":"inf"}` (or the corresponding value). NULL remains JSON null. The byte cap covers compact ASCII-escaped JSON for the data section; audit metadata is additional.

## Verification and references

`tests/test_sqlite_sandbox.py` exercises legitimate relational queries, handoff tampering, schema drift, runtime authorization, blocked filesystem/SQL operations, timeouts, memory/database/value/result limits, WAL rejection, JSON result encoding, and source-file preservation. The benchmark executor and frozen predictions are unchanged.

The implementation uses the documented [Python SQLite connection APIs](https://docs.python.org/3/library/sqlite3.html), [SQLite deserialization contract](https://www.sqlite.org/c3ref/deserialize.html), and [Linux/Unix resource-limit APIs](https://docs.python.org/3/library/resource.html). Deserialization does not support WAL images; SAFE-002 rejects them rather than modifying the input format.
