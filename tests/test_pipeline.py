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
from text2sql.schema import (
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
    inspect_sqlite_schema,
    serialize_simple_schema,
)


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

    def test_linked_mschema_records_subset_and_linker_audit(self) -> None:
        pipeline = Text2SQLPipeline(MockSchemaAwareProvider())
        result = pipeline.generate(
            "List customer first names",
            self.database_path,
            db_id="fixture",
            prompt_variant="linked_mschema",
        )

        self.assertEqual(
            result.prompt_version, "exp003-linked-mschema-v1"
        )
        self.assertEqual(
            result.metadata["schema_representation"],
            "xiyan-compatible-v1+extractive-lexical-v1",
        )
        linking = result.metadata["schema_linking"]
        self.assertIsInstance(linking, dict)
        self.assertEqual(linking["selected_table_count"], 1)
        self.assertEqual(linking["direct_table_names"], ["customers"])
        self.assertGreater(linking["table_reduction_ratio"], 0.0)
        self.assertNotEqual(
            result.metadata["linked_schema_hash"], result.schema_hash
        )

    def test_hybrid_linked_prompt_preserves_full_schema_and_recall_rules(self) -> None:
        provider = MockSchemaAwareProvider()
        pipeline = Text2SQLPipeline(provider)
        with patch.object(
            provider, "generate", wraps=provider.generate
        ) as generate:
            result = pipeline.generate(
                "List customer first names",
                self.database_path,
                db_id="fixture",
                prompt_variant="hybrid_linked_mschema",
                schema_linking_policy=RecallSchemaLinkingPolicy(
                    max_tables=1,
                    max_columns_per_table=1,
                    minimum_columns_per_table=1,
                ),
            )

        generation_input = generate.call_args.args[0]
        self.assertEqual(
            result.prompt_version, "exp004-recall-linked-mschema-v1"
        )
        self.assertEqual(
            [table.name for table in generation_input.schema.tables],
            ["customers", "orders"],
        )
        self.assertIn("Complete compact schema", generation_input.prompt)
        self.assertIn("Linked detailed M-Schema", generation_input.prompt)
        self.assertIn("orders", generation_input.prompt)
        self.assertIn("do not use QUALIFY", generation_input.prompt)
        self.assertIn("Never return a dummy", generation_input.prompt)
        self.assertEqual(
            result.metadata["schema_linking"]["selected_column_count"],
            3,
        )
        self.assertNotEqual(
            result.metadata["linked_schema_hash"], result.schema_hash
        )

    def test_hybrid_prompt_rejects_column_pruning_policy(self) -> None:
        pipeline = Text2SQLPipeline(MockSchemaAwareProvider())
        with self.assertRaisesRegex(ValueError, "recall"):
            pipeline.generate(
                "List customers",
                self.database_path,
                prompt_variant="hybrid_linked_mschema",
                schema_linking_policy=SchemaLinkingPolicy(),
            )

    def test_schema_linking_policy_requires_linked_prompt(self) -> None:
        pipeline = Text2SQLPipeline(MockSchemaAwareProvider())
        with self.assertRaisesRegex(ValueError, "linked_mschema"):
            pipeline.generate(
                "List customers",
                self.database_path,
                prompt_variant="simple_schema",
                schema_linking_policy=SchemaLinkingPolicy(),
            )

    def test_jsonl_writer_appends_valid_json(self) -> None:
        output_path = Path(self.temp_dir.name) / "result.jsonl"
        append_jsonl(output_path, {"example_id": "one", "ok": True})
        append_jsonl(output_path, {"example_id": "two", "ok": False})
        rows = [json.loads(line) for line in output_path.read_text().splitlines()]
        self.assertEqual([row["example_id"] for row in rows], ["one", "two"])


if __name__ == "__main__":
    unittest.main()

