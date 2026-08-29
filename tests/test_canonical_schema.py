from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import load_spider2_lite_sqlite
from text2sql.domain import (
    ColumnSchema,
    ForeignKeySchema,
    SchemaSnapshot,
    TableSchema,
)
from text2sql.evaluation import Spider2SQLiteDatabaseResolver
from text2sql.experiments.cli import (
    DEFAULT_DATABASE_ROOT,
    DEFAULT_DATASET_CONFIG,
    DEFAULT_DATASET_MANIFEST,
    DEFAULT_SOURCE,
    PROJECT_ROOT,
)
from text2sql.schema import (
    CANONICAL_SCHEMA_VERSION,
    canonical_schema_payload,
    canonical_schema_sha256,
    inspect_sqlite_schema,
    serialize_canonical_schema,
    validate_canonical_schema,
)


EXPECTED_DEVELOPMENT_HASHES = {
    "Airlines": "9068b9206771c1a22e9a0e1202f3ce0457fe3b60305bf1377238c5333e5e7fb3",
    "city_legislation": "501adacbcb6829ca0d116de3ed472515555f0c91d466efec983b27e7220a556c",
    "electronic_sales": "94906d57add840cf99cc115b390e121714595ca1d5d1dc878d42c49268027bf2",
    "f1": "5cfec3956f3d878a8cbc5a13d6eb0d9bcaf661a963fa157563d23c0f64507195",
    "music": "8a4c2459c4d39529cebba2fd95a1ebeef9ffa55f6add920cb69f0461560d3182",
    "oracle_sql": "973ae4b075c858fc4c83202fd705dca3a8f19a8a61a589e1c35c5e4db5288f75",
}


class CanonicalSchemaTest(unittest.TestCase):
    def test_existing_single_primary_key_constructor_remains_compatible(self) -> None:
        column = ColumnSchema("id", "INTEGER", True, True)

        self.assertEqual(column.primary_key_position, 1)
    def test_inspector_captures_positions_defaults_and_composite_foreign_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE parent (
                    part_a INTEGER,
                    part_b INTEGER,
                    label TEXT DEFAULT 'unknown',
                    PRIMARY KEY (part_a, part_b)
                );
                CREATE TABLE child (
                    parent_a INTEGER NOT NULL,
                    parent_b INTEGER NOT NULL,
                    FOREIGN KEY (parent_a, parent_b)
                        REFERENCES parent(part_a, part_b)
                );
                """
            )
            connection.close()

            schema = inspect_sqlite_schema(path, db_id="fixture")

        parent = next(table for table in schema.tables if table.name == "parent")
        self.assertEqual(
            [(column.name, column.ordinal_position) for column in parent.columns],
            [("part_a", 0), ("part_b", 1), ("label", 2)],
        )
        self.assertEqual(
            [column.primary_key_position for column in parent.columns],
            [1, 2, 0],
        )
        self.assertEqual(parent.columns[2].default_sql, "'unknown'")
        child = next(table for table in schema.tables if table.name == "child")
        self.assertEqual(
            [
                (
                    foreign_key.constraint_id,
                    foreign_key.sequence,
                    foreign_key.source_column,
                    foreign_key.target_column,
                )
                for foreign_key in child.foreign_keys
            ],
            [(0, 0, "parent_a", "part_a"), (0, 1, "parent_b", "part_b")],
        )

    def test_serialization_and_hash_are_deterministic(self) -> None:
        dataset = load_spider2_lite_sqlite(
            DEFAULT_SOURCE,
            DEFAULT_DATASET_CONFIG,
            PROJECT_ROOT,
            DEFAULT_DATASET_MANIFEST,
        )
        resolver = Spider2SQLiteDatabaseResolver(DEFAULT_DATABASE_ROOT)
        db_ids = sorted({example.db_id for example in dataset.for_split("development")})
        actual = {}
        for db_id in db_ids:
            first = inspect_sqlite_schema(resolver.resolve(db_id).path, db_id=db_id)
            second = inspect_sqlite_schema(resolver.resolve(db_id).path, db_id=db_id)
            self.assertEqual(
                serialize_canonical_schema(first),
                serialize_canonical_schema(second),
            )
            actual[db_id] = canonical_schema_sha256(first)
        self.assertEqual(actual, EXPECTED_DEVELOPMENT_HASHES)

    def test_payload_is_versioned_and_contains_only_real_identifiers(self) -> None:
        schema = SchemaSnapshot(
            db_id="fixture",
            dialect="sqlite",
            tables=(
                TableSchema(
                    name="items",
                    columns=(
                        ColumnSchema(
                            name="item_id",
                            data_type="INTEGER",
                            nullable=True,
                            primary_key=True,
                            ordinal_position=0,
                            primary_key_position=1,
                        ),
                    ),
                ),
            ),
        )
        payload = canonical_schema_payload(schema)
        self.assertEqual(payload["schema_version"], CANONICAL_SCHEMA_VERSION)
        self.assertEqual(payload["tables"][0]["columns"][0]["name"], "item_id")

    def test_validator_rejects_nonexistent_foreign_key_identifiers(self) -> None:
        schema = SchemaSnapshot(
            db_id="fixture",
            dialect="sqlite",
            tables=(
                TableSchema(
                    name="child",
                    columns=(
                        ColumnSchema("id", "INTEGER", True, False),
                    ),
                    foreign_keys=(
                        ForeignKeySchema("missing", "parent", "id"),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "Foreign key source"):
            validate_canonical_schema(schema)


if __name__ == "__main__":
    unittest.main()
