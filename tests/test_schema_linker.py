from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.domain import (
    ColumnSchema,
    ForeignKeySchema,
    SchemaSnapshot,
    TableSchema,
)
from text2sql.schema import (
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
    evaluate_schema_linking,
    inspect_sqlite_schema,
    link_schema,
)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    status TEXT,
    total REAL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT
);
CREATE TABLE order_items (
    item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE TABLE employees (
    employee_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    salary REAL
);
"""


class SchemaLinkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "fixture.sqlite"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(SCHEMA_SQL)
            connection.commit()
        finally:
            connection.close()
        self.schema = inspect_sqlite_schema(
            self.database_path, db_id="fixture"
        )
        self.examples = {
            ("products", "category"): (
                "Electronics",
                "Books",
            ),
            ("orders", "status"): (
                "pending",
                "delivered",
            ),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_linking_is_deterministic_and_removes_unrelated_schema(self) -> None:
        question = (
            "Which customer names ordered products in the "
            "Electronics category?"
        )
        first = link_schema(
            question, self.schema, self.examples
        )
        second = link_schema(
            question, self.schema, self.examples
        )

        self.assertEqual(first, second)
        names = tuple(table.name for table in first.schema.tables)
        self.assertEqual(
            names,
            ("customers", "order_items", "orders", "products"),
        )
        self.assertNotIn("employees", names)
        self.assertFalse(first.fallback_used)
        self.assertGreater(
            first.original_column_count,
            first.selected_column_count,
        )

    def test_primary_and_foreign_key_columns_preserve_joinability(self) -> None:
        result = link_schema(
            "Show customer names and product categories",
            self.schema,
            self.examples,
        )
        selected = {
            table.name: {column.name for column in table.columns}
            for table in result.schema.tables
        }

        self.assertIn("customer_id", selected["customers"])
        self.assertIn("customer_id", selected["orders"])
        self.assertIn("order_id", selected["orders"])
        self.assertIn("order_id", selected["order_items"])
        self.assertIn("product_id", selected["order_items"])
        self.assertIn("product_id", selected["products"])
        self.assertTrue(
            any(table.foreign_keys for table in result.schema.tables)
        )

    def test_foreign_key_casing_is_normalized_to_canonical_names(self) -> None:
        schema = SchemaSnapshot(
            "mixed_case",
            "sqlite",
            (
                TableSchema(
                    "Authors",
                    (
                        ColumnSchema("ID", "INTEGER", False, True, 0),
                        ColumnSchema("Name", "TEXT", False, False, 1),
                    ),
                ),
                TableSchema(
                    "Books",
                    (
                        ColumnSchema("ID", "INTEGER", False, True, 0),
                        ColumnSchema(
                            "AuthorID", "INTEGER", False, False, 1
                        ),
                        ColumnSchema("Title", "TEXT", False, False, 2),
                    ),
                    (
                        ForeignKeySchema(
                            "authorid", "authors", "id"
                        ),
                    ),
                ),
            ),
        )

        result = link_schema(
            "Show author names and book titles",
            schema,
        )
        books = next(
            table for table in result.schema.tables
            if table.name == "Books"
        )
        foreign_key = books.foreign_keys[0]

        self.assertEqual(foreign_key.source_column, "AuthorID")
        self.assertEqual(foreign_key.target_table, "Authors")
        self.assertEqual(foreign_key.target_column, "ID")

    def test_representative_value_can_select_a_column_and_table(self) -> None:
        with_values = link_schema(
            "Show Electronics items",
            self.schema,
            self.examples,
        )
        without_values = link_schema(
            "Show Electronics items",
            self.schema,
            self.examples,
            SchemaLinkingPolicy(include_value_matches=False),
        )

        self.assertIn(
            "products",
            {table.name for table in with_values.schema.tables},
        )
        category_link = next(
            item
            for item in with_values.column_links
            if item.table_name == "products"
            and item.column_name == "category"
        )
        self.assertIn("representative_value", category_link.reasons)
        self.assertNotIn(
            "products",
            set(without_values.direct_table_names),
        )

    def test_no_match_uses_recall_safe_full_schema_fallback(self) -> None:
        result = link_schema(
            "How many are there?",
            self.schema,
            self.examples,
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.schema, self.schema)
        self.assertEqual(result.selected_table_count, len(self.schema.tables))
        self.assertEqual(result.to_dict()["column_reduction_ratio"], 0.0)

    def test_fixture_schema_metrics_measure_precision_recall_and_f1(self) -> None:
        result = link_schema(
            "Show customer names and product categories",
            self.schema,
            self.examples,
        )
        metrics = evaluate_schema_linking(
            result,
            required_tables=("customers", "products"),
            required_columns=(
                ("customers", "name"),
                ("products", "category"),
            ),
        )

        self.assertEqual(metrics.table_recall, 1.0)
        self.assertEqual(metrics.column_recall, 1.0)
        self.assertGreater(metrics.table_precision, 0.0)
        self.assertGreater(metrics.column_f1, 0.0)

    def test_recall_policy_keeps_every_column_in_selected_tables(self) -> None:
        result = link_schema(
            "Show customer names",
            self.schema,
            self.examples,
            RecallSchemaLinkingPolicy(
                max_columns_per_table=1,
                minimum_columns_per_table=1,
            ),
        )

        customers = next(
            table for table in result.schema.tables
            if table.name == "customers"
        )
        self.assertEqual(
            {column.name for column in customers.columns},
            {"customer_id", "name", "city"},
        )
        reasons = {
            reason
            for link in result.column_links
            if link.table_name == "customers"
            for reason in link.reasons
        }
        self.assertIn("recall_all_selected_table_columns", reasons)

    def test_policy_and_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            SchemaLinkingPolicy(max_tables=0)
        with self.assertRaises(ValueError):
            SchemaLinkingPolicy(
                max_columns_per_table=2,
                minimum_columns_per_table=3,
            )
        with self.assertRaises(ValueError):
            SchemaLinkingPolicy(fallback_mode="first_table")
        with self.assertRaises(ValueError):
            link_schema("", self.schema)
        with self.assertRaisesRegex(ValueError, "unknown columns"):
            link_schema(
                "List customers",
                self.schema,
                {("missing", "column"): ("value",)},
            )


if __name__ == "__main__":
    unittest.main()
