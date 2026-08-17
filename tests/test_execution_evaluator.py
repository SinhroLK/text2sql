from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.domain import Text2SQLExample
from text2sql.evaluation import SQLiteExecutionEvaluator, summarize_execution_accuracy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCHEMA = PROJECT_ROOT / "data/fixtures/demo_schema.sql"


class SQLiteExecutionEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "demo.sqlite"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(FIXTURE_SCHEMA.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        self.example = Text2SQLExample(
            example_id="local-fixture-001",
            db_id="demo",
            question="Fixture evaluator test",
            dialect="sqlite",
            split="fixture",
        )
        self.evaluator = SQLiteExecutionEvaluator(timeout_seconds=2)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def evaluate(self, generated_sql: str, reference_sql: str, **kwargs):
        return self.evaluator.evaluate(
            example=self.example,
            database_path=self.database_path,
            generated_sql=generated_sql,
            reference_sql=reference_sql,
            **kwargs,
        )

    def test_identical_results_are_correct_even_with_different_aliases(self) -> None:
        result = self.evaluate(
            "SELECT first_name AS generated_name FROM customers ORDER BY customer_id",
            "SELECT first_name AS reference_name FROM customers ORDER BY customer_id",
        )

        self.assertTrue(result.correct)
        self.assertEqual(result.score, 1)
        self.assertEqual(result.status, "correct")
        self.assertEqual(result.generated.row_count, 2)

    def test_different_results_are_incorrect(self) -> None:
        result = self.evaluate(
            "SELECT first_name FROM customers WHERE customer_id = 1",
            "SELECT first_name FROM customers WHERE customer_id = 2",
        )

        self.assertFalse(result.correct)
        self.assertEqual(result.score, 0)
        self.assertEqual(result.status, "result_mismatch")

    def test_different_row_order_can_be_ignored(self) -> None:
        result = self.evaluate(
            "SELECT first_name FROM customers ORDER BY customer_id DESC",
            "SELECT first_name FROM customers ORDER BY customer_id ASC",
            ignore_order=True,
        )

        self.assertTrue(result.correct)
        self.assertTrue(result.comparison and result.comparison.ignore_order)

    def test_row_order_is_significant_by_default(self) -> None:
        result = self.evaluate(
            "SELECT first_name FROM customers ORDER BY customer_id DESC",
            "SELECT first_name FROM customers ORDER BY customer_id ASC",
        )

        self.assertFalse(result.correct)

    def test_generated_sql_execution_error_is_structured(self) -> None:
        result = self.evaluate(
            "SELECT missing_column FROM customers",
            "SELECT first_name FROM customers",
        )

        self.assertFalse(result.correct)
        self.assertEqual(result.status, "generated_execution_error")
        self.assertEqual(result.generated.status, "execution_error")
        self.assertEqual(result.generated.error_type, "OperationalError")
        self.assertTrue(result.reference.succeeded)

    def test_matching_empty_results_are_correct(self) -> None:
        result = self.evaluate(
            "SELECT first_name FROM customers WHERE customer_id < 0",
            "SELECT first_name FROM customers WHERE customer_id > 100",
        )

        self.assertTrue(result.correct)
        self.assertEqual(result.generated.row_count, 0)
        self.assertEqual(result.reference.row_count, 0)

    def test_null_values_follow_official_zero_normalization(self) -> None:
        result = self.evaluate("SELECT NULL AS value", "SELECT 0 AS value")

        self.assertTrue(result.correct)
        self.assertEqual(result.generated.rows, ((None,),))

    def test_basic_numeric_results_use_official_tolerance(self) -> None:
        close = self.evaluate("SELECT 120.501 AS value", "SELECT 120.509 AS value")
        far = self.evaluate("SELECT 120.50 AS value", "SELECT 120.52 AS value")

        self.assertTrue(close.correct)
        self.assertFalse(far.correct)
        self.assertEqual(close.comparison.numeric_tolerance if close.comparison else None, 1e-2)

    def test_condition_cols_select_reference_columns(self) -> None:
        result = self.evaluate(
            "SELECT total_amount FROM orders ORDER BY order_id",
            "SELECT status, total_amount FROM orders ORDER BY order_id",
            condition_cols=(1,),
        )

        self.assertTrue(result.correct)
        self.assertEqual(result.comparison.condition_cols if result.comparison else None, (1,))

    def test_fixture_database_is_not_modified_by_evaluation(self) -> None:
        result = self.evaluate(
            "UPDATE customers SET first_name = 'Changed'",
            "SELECT first_name FROM customers ORDER BY customer_id",
        )
        connection = sqlite3.connect(self.database_path)
        try:
            names = connection.execute("SELECT first_name FROM customers ORDER BY customer_id").fetchall()
        finally:
            connection.close()

        self.assertEqual(result.status, "generated_execution_error")
        self.assertEqual(names, [("Alice",), ("Bob",)])

    def test_execution_accuracy_summary_requires_exact_id_coverage(self) -> None:
        correct = self.evaluate("SELECT 1", "SELECT 1")
        second_example = Text2SQLExample(
            example_id="local-fixture-002",
            db_id="demo",
            question="Second fixture evaluator test",
            dialect="sqlite",
            split="fixture",
        )
        incorrect = self.evaluator.evaluate(
            example=second_example,
            database_path=self.database_path,
            generated_sql="SELECT 1",
            reference_sql="SELECT 2",
        )

        summary = summarize_execution_accuracy(
            [correct, incorrect],
            expected_ids=["local-fixture-001", "local-fixture-002"],
        )
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.correct, 1)
        self.assertEqual(summary.execution_accuracy, 0.5)

        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            summarize_execution_accuracy(
                [correct],
                expected_ids=["local-fixture-001", "local-fixture-002"],
            )


if __name__ == "__main__":
    unittest.main()
