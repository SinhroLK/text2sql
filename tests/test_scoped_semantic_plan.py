from __future__ import annotations

import contextlib
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from text2sql.evaluation.sqlite_executor import SQLiteQueryExecutor
from text2sql.planning import (
    SEMANTIC_PLAN_V3_VERSION, SemanticPlanParseError, SemanticPlanResolutionError,
    build_semantic_plan_prompt, parse_semantic_plan, resolve_semantic_plan,
    semantic_plan_sha256, serialize_semantic_plan, validate_semantic_plan,
)
from text2sql.planning.cli import main as validate_cli
from text2sql.planning.scoped_plan import MAX_SCOPE_COUNT, MAX_SCOPE_DEPTH
from text2sql.retrieval.structural import extract_sql_structure, semantic_plan_structure
from text2sql.schema import inspect_sqlite_schema


def column(table, name):
    return {"table": table, "column": name}


def output(table, name, alias=None):
    return {"kind": "column", "columns": [column(table, name)], "aggregation_alias": None, "alias": alias, "description": None}


def select(scope_id, table, name):
    return {
        "kind": "select", "scope_id": scope_id, "outputs": [output(table, name, "value")],
        "sources": [table], "joins": [], "filters": [], "aggregations": [],
        "group_by": [], "having": [], "ordering": [], "limit": None,
        "ties": "not_applicable", "temporal": {"grain": "none", "columns": [], "window": None},
    }


def predicate(table, name, operator="eq", value_kind="literal", value="paid", subquery=None):
    return {"columns": [column(table, name)], "operator": operator, "value_kind": value_kind,
            "value": value, "description": "Apply the stated comparison.", "subquery": subquery}


def combined(left, right, operator="union", scope_id="combined"):
    return {"kind": "set", "scope_id": scope_id, "operator": operator, "left": left,
            "right": right, "ordering": [], "limit": None}


def envelope(root, question="Combine customer and product names"):
    return {"plan_version": SEMANTIC_PLAN_V3_VERSION, "db_id": "fixture", "dialect": "sqlite",
            "question": question, "root": root, "uncertainties": []}


def union_payload():
    return envelope(combined(select("customers_branch", "customers", "name"), select("products_branch", "products", "name")))


def subquery_payload():
    root = select("orders_branch", "orders", "id")
    inner = select("price_average", "products", "price")
    inner["outputs"] = [{"kind": "aggregation", "columns": [], "aggregation_alias": "mean", "alias": "value", "description": None}]
    inner["aggregations"] = [{"alias": "mean", "function": "avg", "column": column("products", "price"), "distinct": False}]
    root["filters"] = [predicate("orders", "amount", "gt", "subquery", None, inner)]
    return envelope(root, "List order IDs whose amount exceeds the average product price")


class ScopedSemanticPlanTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "fixture.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript("""
                CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, price REAL);
                CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL, status TEXT);
                INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob');
                INSERT INTO products VALUES (1, 'Book', 10), (2, 'Pen', 2);
                INSERT INTO orders VALUES (1, 1, 12, 'paid'), (2, 2, 3, 'pending');
            """)
        self.schema = inspect_sqlite_schema(self.database, db_id="fixture")

    def tearDown(self):
        self.temporary.cleanup()

    def resolve(self, payload):
        return resolve_semantic_plan(json.dumps(payload), self.schema, expected_question=payload["question"], expected_plan_version=SEMANTIC_PLAN_V3_VERSION)

    def codes(self, payload):
        plan = parse_semantic_plan(json.dumps(payload))
        return {issue.code for issue in validate_semantic_plan(plan, self.schema).issues}

    def test_unrelated_union_branches_validate_and_have_executable_semantics(self):
        payload = union_payload()
        payload["root"]["ordering"] = [{"target_kind": "output_alias", "column": None, "alias": "value", "direction": "asc"}]
        record = self.resolve(payload)
        sql = "SELECT name AS value FROM customers UNION SELECT name AS value FROM products ORDER BY value"
        result = SQLiteQueryExecutor().execute(self.database, sql)
        self.assertTrue(result.succeeded, result.error_message)
        self.assertEqual(result.rows, (("Alice",), ("Bob",), ("Book",), ("Pen",)))
        self.assertEqual(semantic_plan_structure(record), extract_sql_structure(sql))
        self.assertEqual(json.loads(serialize_semantic_plan(record.plan)), payload)
        self.assertEqual(record.to_dict()["record_version"], "semantic-plan-record-v3")

    def test_scalar_subquery_has_independent_aggregation_and_sources(self):
        payload = subquery_payload()
        record = self.resolve(payload)
        sql = "SELECT id FROM orders WHERE amount > (SELECT AVG(price) FROM products)"
        result = SQLiteQueryExecutor().execute(self.database, sql)
        self.assertEqual(result.rows, ((1,),))
        self.assertEqual(semantic_plan_structure(record), extract_sql_structure(sql))
        self.assertEqual(self.resolve(payload).plan_sha256, record.plan_sha256)
        payload["root"]["filters"][0]["subquery"]["aggregations"][0]["function"] = "max"
        self.assertNotEqual(self.resolve(payload).plan_sha256, record.plan_sha256)

    def test_in_subquery_and_exists_are_bound_to_their_predicates(self):
        inner = select("paid", "orders", "customer_id")
        inner["filters"] = [predicate("orders", "status")]
        outer = select("customers", "customers", "name")
        outer["filters"] = [predicate("customers", "id", "in", "subquery", None, inner)]
        self.resolve(envelope(outer))
        outer["filters"][0].update(operator="exists", columns=[])
        self.resolve(envelope(outer))
        outer["filters"][0]["subquery"] = None
        with self.assertRaisesRegex(SemanticPlanParseError, "subquery must be present"):
            parse_semantic_plan(json.dumps(envelope(outer)))

    def test_branch_references_do_not_leak_to_siblings_or_outer_queries(self):
        payload = union_payload()
        payload["root"]["left"]["outputs"] = [output("products", "name")]
        self.assertIn("undeclared_source", self.codes(payload))
        payload = subquery_payload()
        payload["root"]["filters"][0]["subquery"]["filters"] = [predicate("orders", "status")]
        self.assertIn("undeclared_source", self.codes(payload))
        payload["root"]["filters"][0]["subquery"]["aggregations"][0]["column"] = column("products", "missing")
        self.assertIn("unknown_column", self.codes(payload))

    def test_set_and_scalar_projection_arity_and_final_ordering_are_checked(self):
        payload = union_payload()
        payload["root"]["right"]["outputs"].append(output("products", "price"))
        self.assertIn("set_output_arity", self.codes(payload))
        payload = subquery_payload()
        inner = payload["root"]["filters"][0]["subquery"]
        inner["outputs"].append(dict(inner["outputs"][0], alias="other"))
        self.assertIn("subquery_output_arity", self.codes(payload))
        payload = union_payload()
        payload["root"]["ordering"] = [{"target_kind": "column", "column": column("customers", "name"), "alias": None, "direction": "asc"}]
        self.assertIn("invalid_set_ordering", self.codes(payload))

    def test_join_connectivity_remains_local_and_assumptions_include_scope_id(self):
        payload = union_payload()
        branch = payload["root"]["left"]
        branch["sources"].append("orders")
        self.assertIn("disconnected_join_graph", self.codes(payload))
        branch["joins"] = [{"left": column("orders", "customer_id"), "right": column("customers", "id"), "join_type": "inner", "evidence": "inferred_equality", "rationale": "Order customer identifiers refer to customers.id."}]
        record = self.resolve(payload)
        assumption = record.to_dict()["join_assumptions"][0]
        self.assertEqual(assumption["scope_id"], "customers_branch")
        self.assertFalse(assumption["semantically_verified"])

    def test_incomplete_predicates_and_unsupported_aggregates_are_rejected(self):
        for operator, kind, value in (("eq", "none", None), ("gt", "literal_list", [1, 2]), ("like", "literal", 42)):
            with self.subTest(operator=operator):
                root = select("orders", "orders", "id")
                root["filters"] = [predicate("orders", "amount", operator, kind, value)]
                with self.assertRaises(SemanticPlanParseError):
                    parse_semantic_plan(json.dumps(envelope(root)))
        for function, col, distinct in (("invented", column("products", "price"), False), ("count", None, True)):
            with self.subTest(function=function):
                payload = subquery_payload()
                payload["root"]["filters"][0]["subquery"]["aggregations"][0].update(function=function, column=col, distinct=distinct)
                with self.assertRaises(SemanticPlanParseError):
                    parse_semantic_plan(json.dumps(payload))

    def test_scope_ids_depth_count_and_unknown_fields_are_bounded(self):
        payload = union_payload(); payload["root"]["right"]["scope_id"] = "customers_branch"
        with self.assertRaisesRegex(SemanticPlanParseError, "duplicates"):
            parse_semantic_plan(json.dumps(payload))
        root = select("last", "customers", "name")
        for i in range(MAX_SCOPE_DEPTH):
            root = combined(root, select(f"side{i}", "products", "name"), scope_id=f"set{i}")
        with self.assertRaisesRegex(SemanticPlanParseError, "limits"):
            parse_semantic_plan(json.dumps(envelope(root)))
        root = select("root", "orders", "id")
        root["filters"] = [dict(predicate("orders", "id", "exists", "subquery", None, select(f"sub{i}", "products", "id")), columns=[]) for i in range(MAX_SCOPE_COUNT)]
        with self.assertRaisesRegex(SemanticPlanParseError, "limits"):
            parse_semantic_plan(json.dumps(envelope(root)))
        payload = union_payload(); payload["root"]["left"]["correlations"] = []
        with self.assertRaisesRegex(SemanticPlanParseError, "unknown"):
            parse_semantic_plan(json.dumps(payload))

    def test_repair_stays_v3_and_dataclass_replacement_cannot_bypass_validation(self):
        payload = union_payload(); calls = []
        def repair(request):
            calls.append(request)
            self.assertIn("semantic-planner-v3", request.prompt)
            return json.dumps(payload)
        record = resolve_semantic_plan("invalid", self.schema, expected_question=payload["question"], expected_plan_version=SEMANTIC_PLAN_V3_VERSION, repair=repair)
        self.assertEqual(len(calls), 1)
        self.assertTrue(record.repaired)
        branch = record.plan.root.left
        changed = replace(branch, body=replace(branch.body, question="Different question"))
        altered = replace(record.plan, root=replace(record.plan.root, left=changed))
        self.assertFalse(validate_semantic_plan(altered, self.schema).valid)
        self.assertIn("scope_identity_mismatch", {i.code for i in validate_semantic_plan(altered, self.schema).issues})
        with self.assertRaises(SemanticPlanResolutionError):
            resolve_semantic_plan(json.dumps(payload), self.schema, expected_question=payload["question"], expected_plan_version="semantic-plan-v2")

    def test_v3_planner_prompt_and_validation_cli(self):
        payload = union_payload()
        prompt = build_semantic_plan_prompt(payload["question"], self.schema, plan_version=SEMANTIC_PLAN_V3_VERSION)
        self.assertIn("do not join independent set branches", prompt)
        path = Path(self.temporary.name) / "plan.json"; path.write_text(json.dumps(payload))
        output_buffer = io.StringIO()
        with contextlib.redirect_stdout(output_buffer):
            status = validate_cli(["--plan", str(path), "--database", str(self.database), "--db-id", "fixture", "--question", payload["question"], "--plan-version", SEMANTIC_PLAN_V3_VERSION])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output_buffer.getvalue())["record_version"], "semantic-plan-record-v3")


if __name__ == "__main__":
    unittest.main()
