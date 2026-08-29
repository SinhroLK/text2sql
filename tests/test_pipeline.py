from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from text2sql.observability import append_jsonl
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import MockSchemaAwareProvider
from text2sql.schema import inspect_sqlite_schema, serialize_simple_schema


SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
"""


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "fixture.sqlite"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(SCHEMA_SQL)
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_inspection_is_stable_and_contains_foreign_key(self) -> None:
        schema = inspect_sqlite_schema(self.database_path, db_id="fixture")
        self.assertEqual([table.name for table in schema.tables], ["customers", "orders"])
        serialized = serialize_simple_schema(schema)
        self.assertIn("FK customer_id -> customers.customer_id", serialized)
        self.assertIn("customer_id: INTEGER [PK]", serialized)

    def test_pipeline_generates_auditable_result_without_execution(self) -> None:
        pipeline = Text2SQLPipeline(MockSchemaAwareProvider())
        result = pipeline.generate(
            "List customers",
            self.database_path,
            db_id="fixture",
        )
        self.assertEqual(result.provider, "mock")
        self.assertEqual(result.execution_status, "not_executed")
        self.assertEqual(result.validation_status, "not_implemented")
        self.assertIn('FROM "customers"', result.selected_sql or "")
        self.assertEqual(len(result.prompt_hash), 64)
        self.assertEqual(len(result.schema_hash), 64)

    def test_mschema_samples_are_cached_per_database_schema_and_policy(self) -> None:
        pipeline = Text2SQLPipeline(MockSchemaAwareProvider())
        with patch(
            "text2sql.pipeline.sample_sqlite_mschema_values",
            return_value={},
        ) as sampler:
            first = pipeline.generate(
                "List customers",
                self.database_path,
                db_id="fixture",
                prompt_variant="mschema",
            )
            pipeline.generate(
                "Count customers",
                self.database_path,
                db_id="fixture",
                prompt_variant="mschema",
            )

        self.assertEqual(sampler.call_count, 1)
        self.assertEqual(
            first.metadata["schema_representation"], "xiyan-compatible-v1"
        )

    def test_jsonl_writer_appends_valid_json(self) -> None:
        output_path = Path(self.temp_dir.name) / "result.jsonl"
        append_jsonl(output_path, {"example_id": "one", "ok": True})
        append_jsonl(output_path, {"example_id": "two", "ok": False})
        rows = [json.loads(line) for line in output_path.read_text().splitlines()]
        self.assertEqual([row["example_id"] for row in rows], ["one", "two"])


if __name__ == "__main__":
    unittest.main()

