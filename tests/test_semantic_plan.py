from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from text2sql.domain import (
    ColumnSchema,
    ForeignKeySchema,
    SchemaSnapshot,
    TableSchema,
)
from text2sql.planning import (
    SEMANTIC_PLAN_VERSION,
    SEMANTIC_PLAN_V2_VERSION,
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


class SemanticPlanV2Test(unittest.TestCase):
    def setUp(self) -> None:
        original = _schema()
        self.schema = replace(original, tables=tuple(
            replace(table, foreign_keys=()) for table in original.tables
        ))
        self.payload = _valid_payload()
        self.payload["plan_version"] = SEMANTIC_PLAN_V2_VERSION
        self.payload["joins"][0].update(
            evidence="inferred_equality",
            rationale="orders.customer_id identifies the customer referenced by each order.",
        )

    def resolve(self, payload=None, **kwargs):
        return resolve_semantic_plan(
            _render(self.payload if payload is None else payload), self.schema,
            expected_question=QUESTION, expected_plan_version=SEMANTIC_PLAN_V2_VERSION,
            **kwargs,
        )

    def test_inferred_join_without_ddl_constraint_is_audited_as_assumption(self) -> None:
        result = self.resolve()
        audit = result.to_dict()
        self.assertEqual(audit["record_version"], "semantic-plan-record-v2")
        self.assertEqual(audit["join_assumptions"][0]["join_index"], 0)
        self.assertFalse(audit["join_assumptions"][0]["semantically_verified"])
        self.assertEqual(audit["join_assumptions"][0]["rationale"], self.payload["joins"][0]["rationale"])
        self.assertEqual(parse_semantic_plan(serialize_semantic_plan(result.plan)), result.plan)
        self.payload["joins"][0]["rationale"] += " The relationship still needs evaluation."
        self.assertNotEqual(result.plan_sha256, self.resolve().plan_sha256)

    def test_declared_evidence_must_exist_and_inferred_endpoints_must_be_valid(self) -> None:
        variants = (
            ("declared_foreign_key", "customer_id", "join_not_in_schema"),
            ("inferred_equality", "missing", "unknown_column"),
        )
        for evidence, column, expected_code in variants:
            with self.subTest(evidence=evidence):
                self.payload["joins"][0]["evidence"] = evidence
                self.payload["joins"][0]["right"]["column"] = column
                with self.assertRaises(SemanticPlanResolutionError) as failure:
                    self.resolve()
                self.assertIn(expected_code, {i.code for i in failure.exception.issues})
        self.payload["joins"][0]["evidence"] = "declared_foreign_key"
        self.payload["joins"][0]["right"]["column"] = "customer_id"
        result = resolve_semantic_plan(_render(self.payload), _schema(), expected_question=QUESTION)
        self.assertEqual(result.to_dict()["join_assumptions"], [])

    def test_evidence_cannot_bypass_source_connectivity_or_self_join_limits(self) -> None:
        self.payload["sources"].append("products")
        with self.assertRaises(SemanticPlanResolutionError) as failure:
            self.resolve()
        self.assertIn("disconnected_join_graph", {i.code for i in failure.exception.issues})
        self.payload["sources"].remove("products")
        self.payload["joins"][0]["right"] = _column("orders", "order_id")
        with self.assertRaises(SemanticPlanResolutionError) as failure:
            self.resolve()
        self.assertIn("self_join_unsupported", {i.code for i in failure.exception.issues})

    def test_v1_wire_format_and_declared_fk_requirement_are_preserved(self) -> None:
        payload = _valid_payload()
        plan = parse_semantic_plan(_render(payload))
        self.assertEqual(json.loads(serialize_semantic_plan(plan)), payload)
        with self.assertRaises(SemanticPlanResolutionError):
            resolve_semantic_plan(_render(payload), self.schema, expected_question=QUESTION)
        payload["joins"][0]["evidence"] = "inferred_equality"
        with self.assertRaises(SemanticPlanParseError):
            parse_semantic_plan(_render(payload))

    def test_v2_requires_complete_evidence_and_never_downgrades_during_repair(self) -> None:
        for key in ("evidence", "rationale"):
            value = self.payload["joins"][0].pop(key)
            with self.assertRaises(SemanticPlanParseError):
                parse_semantic_plan(_render(self.payload))
            self.payload["joins"][0][key] = value
        self.payload["joins"][0]["rationale"] = " "
        with self.assertRaises(SemanticPlanParseError):
            parse_semantic_plan(_render(self.payload))
        with self.assertRaises(SemanticPlanResolutionError) as failure:
            resolve_semantic_plan(
                "invalid JSON", _schema(), expected_question=QUESTION,
                expected_plan_version=SEMANTIC_PLAN_V2_VERSION,
                repair=lambda request: _render(_valid_payload()),
            )
        self.assertEqual(failure.exception.attempts, 2)
        self.assertIn("plan_version_mismatch", {i.code for i in failure.exception.issues})

    def test_v2_repair_contains_full_contract_and_succeeds_once(self) -> None:
        calls = []
        def repair(request):
            calls.append(request)
            self.assertIn("semantic-plan-v2", request.prompt)
            self.assertIn("inferred_equality", request.prompt)
            self.assertIn('"outputs"', request.prompt)
            return _render(self.payload)
        result = resolve_semantic_plan(
            "invalid JSON", self.schema, expected_question=QUESTION,
            expected_plan_version=SEMANTIC_PLAN_V2_VERSION, repair=repair,
        )
        self.assertTrue(result.repaired)
        self.assertEqual(len(calls), 1)
        prompt = build_semantic_plan_prompt(QUESTION, self.schema, plan_version=SEMANTIC_PLAN_V2_VERSION)
        self.assertIn("not a verified constraint", prompt)


if __name__ == "__main__":
    unittest.main()
