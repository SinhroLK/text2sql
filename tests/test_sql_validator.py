from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.schema.inspector import inspect_sqlite_schema
from text2sql.security import SQLValidationError, SQLiteSQLValidator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCHEMA = PROJECT_ROOT / "data/fixtures/demo_schema.sql"


class SQLiteSQLValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "demo.sqlite"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(FIXTURE_SCHEMA.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        self.schema = inspect_sqlite_schema(self.database_path, db_id="demo")
        self.validator = SQLiteSQLValidator()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def assert_valid(self, sql: str):
        result = self.validator.validate(sql, self.schema)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.statement_type, "SELECT")
        self.assertEqual(result.issues, ())
        return result

    def assert_invalid(self, sql: str, code: str):
        result = self.validator.validate(sql, self.schema)
        self.assertFalse(result.valid)
        self.assertEqual(result.issues[0].code, code)
        return result

    def test_accepts_join_and_tracks_canonical_identifiers(self) -> None:
        result = self.assert_valid(
            """
            SELECT c.first_name, o.total_amount
            FROM customers AS c
            JOIN orders AS o ON o.customer_id = c.customer_id
            ORDER BY o.order_id
            """
        )

        self.assertEqual(result.referenced_tables, ("customers", "orders"))
        self.assertIn("customers.first_name", result.referenced_columns)
        self.assertIn("orders.total_amount", result.referenced_columns)

    def test_accepts_subquery_aggregate_window_and_union(self) -> None:
        valid_queries = (
            "SELECT first_name FROM customers WHERE customer_id IN "
            "(SELECT customer_id FROM orders WHERE total_amount > 10)",
            "SELECT status, COUNT(*), SUM(total_amount) FROM orders GROUP BY status",
            "SELECT order_id, SUM(total_amount) OVER "
            "(PARTITION BY status ORDER BY order_id) FROM orders",
            "SELECT customer_id FROM customers UNION SELECT customer_id FROM orders",
        )

        for sql in valid_queries:
            with self.subTest(sql=sql):
                self.assert_valid(sql)

    def test_accepts_regular_and_recursive_ctes(self) -> None:
        self.assert_valid(
            "WITH totals AS (SELECT customer_id, SUM(total_amount) AS amount "
            "FROM orders GROUP BY customer_id) "
            "SELECT c.first_name, t.amount FROM customers c "
            "JOIN totals t ON t.customer_id = c.customer_id"
        )
        self.assert_valid(
            "WITH RECURSIVE numbers(n) AS "
            "(SELECT 1 UNION ALL SELECT n + 1 FROM numbers WHERE n < 3) "
            "SELECT n FROM numbers"
        )

    def test_case_and_safe_builtin_functions_are_valid(self) -> None:
        self.assert_valid(
            "SELECT CASE WHEN customer_id = 1 THEN 'yes' ELSE 'no' END "
            "FROM customers"
        )
        self.assert_valid("SELECT replace(first_name, 'A', 'B') FROM customers")

    def test_keywords_inside_literals_are_not_treated_as_operations(self) -> None:
        self.assert_valid("SELECT 'DROP TABLE customers', '/* not a comment */'")

    def test_validation_prepares_but_does_not_evaluate_expressions(self) -> None:
        self.assert_valid("SELECT abs(-9223372036854775808)")

    def test_allows_only_one_select_with_optional_trailing_semicolon(self) -> None:
        self.assert_valid("SELECT first_name FROM customers;")
        self.assert_invalid("SELECT 1; SELECT 2", "multiple_statements")
        self.assert_invalid("SELECT 1;;", "multiple_statements")
        self.assert_invalid("VALUES (1)", "unsupported_statement")

    def test_rejects_ddl_dml_pragma_attach_and_transactions(self) -> None:
        cases = (
            "CREATE TABLE stolen(value TEXT)",
            "DROP TABLE customers",
            "ALTER TABLE customers ADD COLUMN secret TEXT",
            "INSERT INTO customers VALUES (3, 'E', 'V', 'x')",
            "UPDATE customers SET first_name = 'Changed'",
            "DELETE FROM customers",
            "REPLACE INTO customers VALUES (3, 'E', 'V', 'x')",
            "PRAGMA table_info(customers)",
            "ATTACH DATABASE '/tmp/other.sqlite' AS other",
            "VACUUM",
            "BEGIN TRANSACTION",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                result = self.validator.validate(sql, self.schema)
                self.assertFalse(result.valid)
                self.assertIn(
                    result.issues[0].code,
                    {"unsupported_statement", "forbidden_keyword"},
                )

    def test_rejects_comments_parameters_and_malformed_queries(self) -> None:
        self.assert_invalid("SELECT 1 -- hidden", "comments_not_allowed")
        self.assert_invalid("SELECT /* hidden */ 1", "comments_not_allowed")
        self.assert_invalid(
            "SELECT first_name FROM customers WHERE customer_id = ?",
            "parameters_not_allowed",
        )
        self.assert_invalid("SELECT ( FROM customers", "malformed_sql")

    def test_rejects_system_tables_and_side_effect_functions(self) -> None:
        self.assert_invalid("SELECT name FROM sqlite_master", "system_table_access")
        self.assert_invalid(
            "SELECT * FROM pragma_table_info('customers')", "forbidden_function"
        )
        self.assert_invalid(
            "SELECT load_extension('/tmp/unsafe')", "forbidden_function"
        )
        self.assert_invalid(
            "SELECT writefile('/tmp/unsafe', 'data')", "forbidden_function"
        )

    def test_rejects_identifiers_outside_the_canonical_schema(self) -> None:
        self.assert_invalid("SELECT * FROM missing_table", "unknown_table")
        self.assert_invalid("SELECT missing_column FROM customers", "unknown_column")
        self.assert_invalid(
            "SELECT customer_id FROM customers JOIN orders "
            "ON customers.customer_id = orders.customer_id",
            "ambiguous_column",
        )
        self.assert_invalid("SELECT * FROM other.customers", "unknown_schema")

    def test_result_is_auditable_and_raise_helper_preserves_it(self) -> None:
        sql = "SELECT first_name FROM customers WHERE customer_id = 1"
        first = self.assert_valid(sql)
        second = self.assert_valid(sql)
        payload = first.to_dict(include_ast=True)

        self.assertEqual(first.sql_sha256, second.sql_sha256)
        self.assertEqual(first.ast_sha256, second.ast_sha256)
        self.assertIsNotNone(payload["ast"])
        self.assertNotIn("1", str(payload["ast"]))
        self.assertIs(first.require_valid(), first)

        invalid = self.validator.validate("SELECT missing FROM customers", self.schema)
        with self.assertRaises(SQLValidationError) as context:
            invalid.require_valid()
        self.assertIs(context.exception.result, invalid)


if __name__ == "__main__":
    unittest.main()

class CTEScopeRegressionTest(unittest.TestCase):
    setUp = SQLiteSQLValidatorTest.setUp
    tearDown = SQLiteSQLValidatorTest.tearDown
    assert_valid = SQLiteSQLValidatorTest.assert_valid

    def test_count_star_derived_reads_and_physical_provenance(self):
        for sql in (
            'WITH cohort AS (SELECT DISTINCT customer_id FROM orders) SELECT count(*) FROM cohort',
            'WITH cohort AS MATERIALIZED (SELECT customer_id FROM orders), total AS (SELECT count(*) n FROM cohort) SELECT n FROM total',
            'SELECT count(*) FROM (SELECT DISTINCT customer_id FROM orders)',
            'WITH a AS (SELECT * FROM b), b AS (SELECT DISTINCT customer_id FROM orders) SELECT count(*) FROM a',
            'WITH a AS (SELECT 1) SELECT (WITH a AS (SELECT DISTINCT customer_id FROM orders) SELECT count(*) FROM a) FROM a',
        ):
            with self.subTest(sql=sql):
                result = self.assert_valid(sql)
                self.assertIn('orders', result.referenced_tables)
                self.assertNotIn('cohort', result.referenced_tables)

    def test_keyword_cte_names_and_column_lists(self):
        for sql in (
            'WITH RECURSIVE data(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM data WHERE n<3) SELECT n FROM data',
            'WITH data AS (SELECT 1 n), full AS (SELECT n FROM data) SELECT * FROM full',
            'WITH start AS (SELECT 1 n) SELECT * FROM start',
            'WITH start AS (SELECT 1 n) SELECT (n) FROM start',
            'WITH "select"(n) AS (SELECT 1) SELECT * FROM "select"',
        ):
            self.assert_valid(sql)

    def test_cte_scope_cannot_hide_disallowed_operations(self):
        for sql in (
            'WITH a AS (SELECT 1) DELETE FROM orders',
            'WITH a AS (SELECT 1) UPDATE orders SET status=\'x\'',
            'WITH a AS (SELECT 1) INSERT INTO orders(order_id) SELECT * FROM a',
            'WITH a AS (SELECT 1) VALUES(2)',
            'WITH a AS (SELECT * FROM sqlite_master) SELECT count(*) FROM a',
            'WITH a AS (SELECT load_extension(\'x\')) SELECT * FROM a',
            'WITH a AS (SELECT * FROM pragma_table_info(\'orders\')) SELECT * FROM a',
            'WITH a AS (SELECT ?) SELECT * FROM a',
            'WITH a AS (SELECT 1) SELECT * FROM a -- comment',
            'WITH a AS (SELECT 1) SELECT * FROM a; DELETE FROM orders',
            'SELECT * FROM (WITH hidden AS (SELECT 1) SELECT * FROM hidden), hidden',
            'WITH orders AS (SELECT * FROM main.missing) SELECT count(*) FROM orders',
            'WITH a AS (SELECT customer_id FROM orders) SELECT status FROM a',
            'WITH a AS (SELECT 1) SELECT count(DISTINCT order_id) OVER () FROM orders',
        ):
            with self.subTest(sql=sql):
                self.assertFalse(self.validator.validate(sql, self.schema).valid)

    def test_no_database_exception_is_only_for_columnless_reads(self):
        from text2sql.security.sql_validator import _AuthorizationAudit
        audit = _AuthorizationAudit({'orders': 'orders'})
        self.assertEqual(audit.authorize(sqlite3.SQLITE_READ, 'derived', '', None, None), sqlite3.SQLITE_OK)
        for column, database in (('secret',None),('', 'main'),('', 'temp')):
            self.assertEqual(audit.authorize(sqlite3.SQLITE_READ, 'missing', column, database, None), sqlite3.SQLITE_DENY)
