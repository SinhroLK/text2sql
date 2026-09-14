from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from text2sql.execution import SandboxPolicy, SQLiteSandboxExecutor
from text2sql.execution.cli import main
from text2sql.schema import inspect_sqlite_schema
from text2sql.security import SQLiteSQLValidator

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/demo_schema.sql"


@unittest.skipUnless(sys.platform == "linux", "SAFE-002 requires Linux resource limits")
class SQLiteSandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "demo ?#.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript(FIXTURE.read_text())
        connection.close()
        self.schema = inspect_sqlite_schema(self.database)
        self.validator = SQLiteSQLValidator()
        self.executor = SQLiteSandboxExecutor()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_sql(self, sql: str, **limits):
        executor = SQLiteSandboxExecutor(SandboxPolicy(**limits)) if limits else self.executor
        return executor.execute(self.database, sql, self.validator.validate(sql, self.schema))

    def files(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}

    def test_join_results_and_hashes_without_source_or_filesystem_changes(self):
        before = self.files()
        sql = "SELECT c.first_name, o.total_amount FROM customers c JOIN orders o USING(customer_id) ORDER BY o.order_id"
        result = self.run_sql(sql)
        self.assertTrue(result.succeeded, result)
        self.assertEqual(result.rows, (("Alice", 120.5), ("Bob", 75.0)))
        self.assertEqual(result.database_sha256, hashlib.sha256(before[self.database.name]).hexdigest())
        self.assertEqual(result.sql_sha256, result.validation["sql_sha256"])
        self.assertEqual(result.to_dict()["row_count"], 2)
        self.assertEqual(self.files(), before)

    def test_legitimate_cte_window_aggregate_and_empty_result(self):
        queries = [
            ("WITH x AS (SELECT total_amount FROM orders) SELECT SUM(total_amount) FROM x", ((195.5,),)),
            ("SELECT row_number() OVER (ORDER BY customer_id) FROM customers", ((1,), (2,))),
            ("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<3) SELECT n FROM x", ((1,), (2,), (3,))),
            ("SELECT first_name FROM customers WHERE 0", ()),
        ]
        for sql, expected in queries:
            with self.subTest(sql=sql):
                result = self.run_sql(sql)
                self.assertTrue(result.succeeded, result)
                self.assertEqual(result.rows, expected)

    def test_invalid_or_changed_handoff_never_starts_worker(self):
        valid = self.validator.validate("SELECT 1", self.schema)
        invalid = self.validator.validate("DELETE FROM customers", self.schema)
        cases = [("SELECT 2", valid), ("DELETE FROM customers", invalid),
                 ("SELECT 1", replace(valid, validator_version="unknown")), ("SELECT 1", None)]
        with patch("text2sql.execution.sandbox.subprocess.run") as worker:
            for sql, handoff in cases:
                self.assertEqual(self.executor.execute(self.database, sql, handoff).status, "validation_rejected")
            worker.assert_not_called()

    def test_forged_success_is_revalidated_before_execution(self):
        sql = "DELETE FROM customers"
        forged = replace(self.validator.validate(sql, self.schema), valid=True, issues=())
        before = self.files()
        result = self.executor.execute(self.database, sql, forged)
        self.assertEqual(result.error_code, "snapshot_validation_failed")
        self.assertFalse(result.validation["valid"])
        self.assertEqual(self.files(), before)

    def test_schema_drift_cannot_reuse_prior_validation(self):
        sql = "SELECT first_name FROM customers"
        validated = self.validator.validate(sql, self.schema)
        with sqlite3.connect(self.database) as connection:
            connection.execute("ALTER TABLE customers RENAME COLUMN first_name TO given_name")
        connection.close()
        result = self.executor.execute(self.database, sql, validated)
        self.assertEqual(result.status, "validation_rejected")
        self.assertIn("unknown_column", result.validation["issue_codes"])

    def test_adversarial_queries_leave_database_and_files_unchanged(self):
        target = self.root / "stolen.sqlite"
        before = self.files()
        queries = [
            "DROP TABLE customers", "UPDATE customers SET first_name='stolen'",
            "SELECT 1; DELETE FROM customers", "PRAGMA user_version=42",
            f"ATTACH DATABASE '{target}' AS stolen", f"VACUUM INTO '{target}'",
            f"SELECT writefile('{target}', 'secret')", f"SELECT readfile('{self.database}')",
            f"SELECT load_extension('{target}')", "SELECT name FROM sqlite_master",
            "SELECT * FROM pragma_table_info('customers')", "SELECT * FROM missing_secret_table",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                result = self.executor.validate_and_execute(self.database, sql)
                self.assertEqual(result.status, "validation_rejected", result)
                self.assertNotIn(str(self.root), json.dumps(result.to_dict()))
                self.assertEqual(self.files(), before)

    def test_row_limit_marks_preview_and_exact_boundary_is_complete(self):
        sql = "SELECT first_name FROM customers ORDER BY customer_id"
        result = self.run_sql(sql, max_result_rows=1)
        self.assertEqual(result.rows, (("Alice",),))
        self.assertEqual(result.status, "truncated")
        self.assertFalse(result.succeeded)
        self.assertTrue(result.truncated)
        self.assertTrue(self.run_sql(sql, max_result_rows=2).succeeded)

    def test_result_byte_limit_and_single_value_limit(self):
        sql = "SELECT hex(zeroblob(400)) AS payload FROM customers CROSS JOIN orders"
        result = self.run_sql(sql, max_result_bytes=2048, max_value_bytes=1024)
        self.assertEqual(result.error_code, "result_bytes_limit", result)
        encoded = json.dumps({"columns": result.columns, "rows": result.rows}, separators=(",", ":"))
        self.assertLessEqual(len(encoded.encode()), 2048)
        oversized = self.run_sql("SELECT randomblob(4096)", max_value_bytes=2048)
        self.assertEqual(oversized.status, "resource_limit", oversized)
        self.assertEqual(oversized.rows, ())

    def test_database_size_limit_precedes_deserialization(self):
        result = self.run_sql("SELECT 1", max_database_bytes=100)
        self.assertEqual(result.error_code, "database_size_limit")

    def test_parent_deadline_terminates_nonreturning_query_and_next_run_works(self):
        before = self.files()
        sql = "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x"
        start = time.monotonic()
        result = self.run_sql(sql, timeout_seconds=0.3)
        self.assertEqual(result.status, "timeout", result)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(self.files(), before)
        self.assertTrue(self.run_sql("SELECT 1").succeeded)

    def test_deadline_also_covers_worker_startup(self):
        self.assertEqual(self.run_sql("SELECT 1", timeout_seconds=0.000001).status, "timeout")

    def test_memory_limit_blocks_large_allocation(self):
        result = self.run_sql(
            "SELECT length(randomblob(100000000))",
            memory_limit_bytes=64 * 1024 * 1024, max_database_bytes=1024 * 1024,
            max_value_bytes=128 * 1024 * 1024, max_result_bytes=128 * 1024 * 1024,
        )
        self.assertEqual(result.status, "resource_limit", result)
        self.assertTrue(self.run_sql("SELECT 1").succeeded)

    def test_runtime_errors_are_sanitized(self):
        result = self.run_sql("SELECT abs(-9223372036854775808)")
        self.assertEqual(result.error_code, "query_execution_failed")
        self.assertTrue(result.validation["valid"])
        self.assertNotIn("922337", json.dumps(result.to_dict()))
        self.assertNotIn(str(self.root), json.dumps(result.to_dict()))

    def test_blob_null_and_infinity_have_json_safe_representations(self):
        result = self.run_sql("SELECT X'00FF', NULL, 1e999")
        self.assertTrue(result.succeeded, result)
        self.assertEqual(result.rows[0], ({"type": "blob", "base64": "AP8="}, None, {"type": "float", "value": "inf"}))
        json.dumps(result.to_dict(), allow_nan=False)

    def test_wal_is_rejected_without_modifying_sidecars(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO customers VALUES (3, 'C', 'D', 'c@example.test')")
            connection.commit()
            before = self.files()
            result = self.run_sql("SELECT 1")
            self.assertEqual(result.error_code, "standalone_snapshot_required")
            self.assertEqual(self.files(), before)
        finally:
            connection.close()

    def test_missing_corrupt_and_nonregular_inputs_fail_closed(self):
        missing = self.executor.validate_and_execute(self.root / "missing.sqlite", "SELECT 1")
        self.assertEqual(missing.error_code, "database_not_found")
        corrupt = self.root / "corrupt.sqlite"
        corrupt.write_bytes(b"not a SQLite database")
        self.assertEqual(self.executor.validate_and_execute(corrupt, "SELECT 1").error_code, "invalid_database")
        fifo = self.root / "pipe.sqlite"
        os.mkfifo(fifo)
        self.assertEqual(self.executor.validate_and_execute(fifo, "SELECT 1").error_code, "regular_database_required")

    def test_cli_success_and_rejection_are_structured(self):
        for sql, exit_code in [("SELECT 1", 0), ("DELETE FROM customers", 2)]:
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--database", str(self.database), "--sql", sql])
            self.assertEqual(code, exit_code)
            self.assertIn("status", json.loads(output.getvalue()))

    def test_runtime_authorizer_still_blocks_writes_if_validation_is_bypassed(self):
        from text2sql.execution.worker import _execute

        valid = self.validator.validate("SELECT 1", self.schema)
        before = self.files()
        with patch.object(SQLiteSQLValidator, "validate", return_value=valid):
            result = _execute({"database_path": str(self.database), "sql": "DELETE FROM customers"}, SandboxPolicy())
        self.assertEqual(result["error_code"], "query_execution_failed")
        self.assertEqual(self.files(), before)

    def test_views_and_virtual_tables_fail_closed(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE VIEW names AS SELECT first_name FROM customers")
        connection.close()
        result = self.executor.validate_and_execute(self.database, "SELECT * FROM names")
        self.assertEqual(result.status, "validation_rejected")
        with sqlite3.connect(self.database) as connection:
            try:
                connection.execute("CREATE VIRTUAL TABLE search USING fts5(content)")
            except sqlite3.OperationalError:
                self.skipTest("SQLite build has no FTS5")
        connection.close()
        before = self.files()
        result = self.executor.validate_and_execute(self.database, "SELECT 1")
        self.assertEqual(result.error_code, "virtual_tables_not_supported")
        self.assertEqual(self.files(), before)

    def test_invalid_policy_is_rejected(self):
        for values in [{"timeout_seconds": float("nan")}, {"timeout_seconds": True},
                       {"max_result_rows": 0}, {"max_result_rows": True},
                       {"memory_limit_bytes": 1}, {"max_database_bytes": 1024**3}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                SandboxPolicy(**values)


if __name__ == "__main__":
    unittest.main()
