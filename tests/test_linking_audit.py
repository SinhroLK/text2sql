from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import Text2SQLExample
from text2sql.evaluation import Spider2SQLiteDatabaseResolver
from text2sql.experiments.linking_audit import (
    audit_development_schema_linking,
)
from text2sql.schema import (
    MSchemaSamplePolicy,
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
)


class LinkingAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_dir = Path(self.temp_dir.name) / "databases"
        self.database_dir.mkdir()
        connection = sqlite3.connect(
            self.database_dir / "fixture.sqlite"
        )
        try:
            connection.executescript(
                """
                CREATE TABLE customers (
                    customer_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT
                );
                CREATE TABLE orders (
                    order_id INTEGER PRIMARY KEY,
                    customer_id INTEGER,
                    status TEXT,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(customer_id)
                );
                """
            )
            connection.commit()
        finally:
            connection.close()
        self.dataset = LoadedSpider2LiteDataset(
            examples=(
                Text2SQLExample(
                    "local001",
                    "fixture",
                    "List customer first names",
                    "sqlite",
                    "development",
                ),
                Text2SQLExample(
                    "local999",
                    "sealed_missing_database",
                    "Held out",
                    "sqlite",
                    "test",
                ),
            ),
            manifest={},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_audit_uses_only_development_and_reduces_prompt(self) -> None:
        result = audit_development_schema_linking(
            dataset=self.dataset,
            database_resolver=Spider2SQLiteDatabaseResolver(
                self.database_dir
            ),
            sample_policy=MSchemaSamplePolicy(
                examples_per_column=0
            ),
            linking_policy=SchemaLinkingPolicy(),
        )

        self.assertEqual(result["scope"], "development")
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["database_count"], 1)
        self.assertGreater(
            result["summary"]["table_reduction_ratio"], 0.0
        )
        self.assertGreater(
            result["summary"]["prompt_character_reduction_ratio"],
            0.0,
        )
        self.assertEqual(
            [record["example_id"] for record in result["records"]],
            ["local001"],
        )
        self.assertNotIn(
            "question",
            result["records"][0],
        )


    def test_hybrid_audit_records_recall_prompt_and_all_columns(self) -> None:
        result = audit_development_schema_linking(
            dataset=self.dataset,
            database_resolver=Spider2SQLiteDatabaseResolver(
                self.database_dir
            ),
            sample_policy=MSchemaSamplePolicy(
                examples_per_column=0
            ),
            linking_policy=RecallSchemaLinkingPolicy(
                max_tables=1,
                max_columns_per_table=1,
                minimum_columns_per_table=1,
            ),
            prompt_variant="hybrid_linked_mschema",
        )

        record = result["records"][0]
        self.assertEqual(
            record["linked_prompt_version"],
            "exp004-recall-linked-mschema-v1",
        )
        self.assertEqual(
            record["schema_linking"]["selected_column_count"], 3
        )
        self.assertTrue(
            result["linking_policy"]
            ["include_all_selected_table_columns"]
        )


if __name__ == "__main__":
    unittest.main()
