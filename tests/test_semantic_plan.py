from __future__ import annotations

import contextlib
import io
import json
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
from text2sql.planning import (
    SEMANTIC_PLAN_VERSION,
    SemanticPlanParseError,
    SemanticPlanResolutionError,
    build_semantic_plan_prompt,
    parse_semantic_plan,
    resolve_semantic_plan,
    semantic_plan_sha256,
    serialize_semantic_plan,
    validate_semantic_plan,
)
from text2sql.planning.cli import main as semantic_plan_cli
from text2sql.schema import canonical_schema_sha256


QUESTION = "Which customer names have paid orders?"


def _schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        db_id="fixture",
        dialect="sqlite",
        tables=(
            TableSchema(
                "categories",
                (
                    ColumnSchema("category_id", "INTEGER", False, True, 0),
                    ColumnSchema("parent_id", "INTEGER", True, False, 1),
                    ColumnSchema("name", "TEXT", False, False, 2),
                ),
                (ForeignKeySchema("parent_id", "categories", "category_id"),),
            ),
            TableSchema(
                "customers",
                (
                    ColumnSchema("customer_id", "INTEGER", False, True, 0),
                    ColumnSchema("name", "TEXT", False, False, 1),
                ),
            ),
            TableSchema(
                "orders",
                (
                    ColumnSchema("order_id", "INTEGER", False, True, 0),
                    ColumnSchema("customer_id", "INTEGER", False, False, 1),
                    ColumnSchema("status", "TEXT", True, False, 2),
                    ColumnSchema("created_at", "TEXT", True, False, 3),
                ),
                (ForeignKeySchema("customer_id", "customers", "customer_id"),),
            ),
            TableSchema(
                "products",
                (
                    ColumnSchema("product_id", "INTEGER", False, True, 0),
                    ColumnSchema("name", "TEXT", False, False, 1),
                ),
            ),
        ),
    )


def _column(table: str, column: str) -> dict[str, str]:
    return {"table": table, "column": column}


def _valid_payload() -> dict[str, object]:
    return {
        "plan_version": SEMANTIC_PLAN_VERSION,
        "db_id": "fixture",
        "dialect": "sqlite",
        "question": QUESTION,
        "outputs": [
            {
                "kind": "column",
                "columns": [_column("customers", "name")],
                "aggregation_alias": None,
                "alias": "customer_name",
                "description": None,
            }
        ],
        "sources": ["customers", "orders"],
        "joins": [
            {
                "left": _column("orders", "customer_id"),
                "right": _column("customers", "customer_id"),
                "join_type": "inner",
            }
        ],
        "filters": [
            {
                "columns": [_column("orders", "status")],
                "operator": "eq",
                "value_kind": "literal",
                "value": "paid",
                "description": "Keep orders whose status is paid.",
            }
        ],
        "aggregations": [],
        "group_by": [],
        "having": [],
        "ordering": [],
        "limit": None,
        "ties": "not_applicable",
        "temporal": {"grain": "none", "columns": [], "window": None},
        "recursion": False,
        "set_operation": "none",
        "uncertainties": [],
    }


def _render(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class SemanticPlanTest(unittest.TestCase):
    def test_valid_plan_is_deterministic_and_auditable(self) -> None:
        schema = _schema()
        raw = _render(_valid_payload())
        first = resolve_semantic_plan(raw, schema, expected_question=QUESTION)
        second = resolve_semantic_plan(raw, schema, expected_question=QUESTION)

        self.assertEqual(first, second)
        self.assertEqual(first.attempts, 1)
        self.assertFalse(first.repaired)
        self.assertEqual(first.plan_sha256, semantic_plan_sha256(first.plan))
        self.assertEqual(
            first.schema_evidence_sha256,
            canonical_schema_sha256(schema),
        )
        self.assertEqual(len(first.plan_sha256), 64)
        self.assertEqual(
            serialize_semantic_plan(parse_semantic_plan(raw)),
            serialize_semantic_plan(first.plan),
        )
        metadata = first.prediction_metadata()["semantic_plan"]
        self.assertEqual(metadata["plan_sha256"], first.plan_sha256)
        self.assertEqual(
            metadata["schema_evidence_sha256"],
            first.schema_evidence_sha256,
        )

    def test_prompt_is_plan_only_and_contains_exact_schema_evidence(self) -> None:
        prompt = build_semantic_plan_prompt(QUESTION, _schema())

        self.assertIn("Do not write SQL", prompt)
        self.assertIn("customers.customer_id", prompt)
        self.assertIn("FK customer_id -> customers.customer_id", prompt)
        self.assertIn('"uncertainties"', prompt)
        self.assertIn('"set_operation"', prompt)

    def test_strict_parser_rejects_markdown_unknown_fields_and_bad_predicates(self) -> None:
        raw = _render(_valid_payload())
        with self.assertRaisesRegex(SemanticPlanParseError, "strict JSON"):
            parse_semantic_plan(f"```json\n{raw}\n```")

        duplicate = raw[:-1] + ', "db_id": "other"}'
        with self.assertRaisesRegex(
            SemanticPlanParseError, "duplicate object key"
        ):
            parse_semantic_plan(duplicate)

        unknown = _valid_payload()
        unknown["sql"] = "SELECT 1"
        with self.assertRaisesRegex(SemanticPlanParseError, "unknown=.*sql"):
            parse_semantic_plan(_render(unknown))

        bad_predicate = _valid_payload()
        bad_predicate["filters"][0]["operator"] = "between"
        with self.assertRaisesRegex(SemanticPlanParseError, "requires range"):
            parse_semantic_plan(_render(bad_predicate))

    def test_validator_rejects_unknown_identifier_and_unconnected_join_path(self) -> None:
        payload = _valid_payload()
        payload["sources"].append("products")
        payload["outputs"].append(
            {
                "kind": "column",
                "columns": [_column("products", "missing")],
                "aggregation_alias": None,
                "alias": "product_name",
                "description": None,
            }
        )
        plan = parse_semantic_plan(_render(payload))
        validation = validate_semantic_plan(
            plan, _schema(), expected_question=QUESTION
        )
        codes = {issue.code for issue in validation.issues}

        self.assertFalse(validation.valid)
        self.assertIn("unknown_column", codes)
        self.assertIn("disconnected_join_graph", codes)

        wrong_join = _valid_payload()
        wrong_join["joins"][0]["right"] = _column("customers", "name")
        validation = validate_semantic_plan(
            parse_semantic_plan(_render(wrong_join)),
            _schema(),
            expected_question=QUESTION,
        )
        self.assertIn(
            "join_not_in_schema",
            {issue.code for issue in validation.issues},
        )

    def test_aggregation_grouping_ordering_and_temporal_fields_validate(self) -> None:
        payload = _valid_payload()
        payload["question"] = "Count paid orders per customer by month."
        payload["outputs"].append(
            {
                "kind": "aggregation",
                "columns": [],
                "aggregation_alias": "order_count",
                "alias": "paid_order_count",
                "description": None,
            }
        )
        payload["aggregations"] = [
            {
                "alias": "order_count",
                "function": "count",
                "column": _column("orders", "order_id"),
                "distinct": False,
            }
        ]
        payload["group_by"] = [_column("customers", "name")]
        payload["ordering"] = [
            {
                "target_kind": "aggregation_alias",
                "column": None,
                "alias": "order_count",
                "direction": "desc",
            }
        ]
        payload["limit"] = 10
        payload["ties"] = "include"
        payload["temporal"] = {
            "grain": "month",
            "columns": [_column("orders", "created_at")],
            "window": "calendar month",
        }
        plan = parse_semantic_plan(_render(payload))
        validation = validate_semantic_plan(
            plan,
            _schema(),
            expected_question=payload["question"],
        )

        self.assertTrue(validation.valid, validation.issues)

    def test_recursive_set_shape_is_explicit_and_validated(self) -> None:
        payload = _valid_payload()
        payload["question"] = "List every category below the root category."
        payload["outputs"] = [
            {
                "kind": "column",
                "columns": [_column("categories", "name")],
                "aggregation_alias": None,
                "alias": "category_name",
                "description": None,
            }
        ]
        payload["sources"] = ["categories"]
        payload["joins"] = []
        payload["filters"] = []
        payload["recursion"] = True
        payload["set_operation"] = "union_all"
        plan = parse_semantic_plan(_render(payload))
        self.assertTrue(
            validate_semantic_plan(
                plan,
                _schema(),
                expected_question=payload["question"],
            ).valid
        )

        payload["set_operation"] = "none"
        invalid = validate_semantic_plan(
            parse_semantic_plan(_render(payload)),
            _schema(),
            expected_question=payload["question"],
        )
        self.assertIn(
            "invalid_recursive_shape", {issue.code for issue in invalid.issues}
        )

    def test_exactly_one_plan_only_repair_is_permitted(self) -> None:
        invalid_payload = _valid_payload()
        invalid_payload["outputs"][0]["columns"][0]["column"] = "missing"
        calls = []

        def repair(request):
            calls.append(request)
            self.assertEqual(request.attempt, 1)
            self.assertIn("Correct the semantic plan only", request.prompt)
            self.assertIn("unknown_column", request.prompt)
            return _render(_valid_payload())

        result = resolve_semantic_plan(
            _render(invalid_payload),
            _schema(),
            expected_question=QUESTION,
            repair=repair,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.attempts, 2)
        self.assertTrue(result.repaired)
        self.assertEqual(result.initial_issues[0].code, "unknown_column")

    def test_invalid_repair_stops_after_second_attempt(self) -> None:
        calls = 0

        def repair(_request):
            nonlocal calls
            calls += 1
            return "still not JSON"

        with self.assertRaises(SemanticPlanResolutionError) as context:
            resolve_semantic_plan(
                "not JSON",
                _schema(),
                expected_question=QUESTION,
                repair=repair,
            )

        self.assertEqual(calls, 1)
        self.assertEqual(context.exception.attempts, 2)
        self.assertEqual(context.exception.issues[0].code, "parse_error")

    def test_cli_validates_plan_without_provider_or_sql_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "fixture.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE customers (
                    customer_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE orders (
                    order_id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL,
                    status TEXT,
                    created_at TEXT,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                );
                """
            )
            connection.close()
            plan_path = root / "plan.json"
            plan_path.write_text(_render(_valid_payload()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = semantic_plan_cli(
                    [
                        "--plan",
                        str(plan_path),
                        "--database",
                        str(database),
                        "--db-id",
                        "fixture",
                        "--question",
                        QUESTION,
                    ]
                )

        result = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
