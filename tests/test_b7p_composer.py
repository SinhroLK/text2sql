from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from text2sql.generation import B7PComposer, B7PComposerError, load_b7p_composer_config
from text2sql.planning import resolve_semantic_plan
from text2sql.retrieval import (
    LoadedRetrievalIndex,
    QuestionPlanHybridSelector,
    RetrievalIndexEntry,
    build_structural_index,
    load_structural_retrieval_config,
    normalize_retrieval_text,
    write_structural_index,
)
from text2sql.schema import inspect_sqlite_schema, sample_sqlite_mschema_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ZERO_SHA = "0" * 64
SOURCE_INDEX_SHA = "a" * 64
SOURCE_MANIFEST_SHA = "b" * 64


def _column(table: str, column: str) -> dict[str, str]:
    return {"table": table, "column": column}


def _base_payload(question: str) -> dict[str, object]:
    return {
        "plan_version": "semantic-plan-v1",
        "db_id": "fixture",
        "dialect": "sqlite",
        "question": question,
        "outputs": [
            {
                "kind": "column",
                "columns": [_column("customers", "name")],
                "aggregation_alias": None,
                "alias": None,
                "description": None,
            }
        ],
        "sources": ["customers"],
        "joins": [],
        "filters": [],
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


class B7PComposerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "fixture.sqlite"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                status TEXT,
                amount REAL,
                created_at TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER,
                name TEXT NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES categories(id)
            );
            INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob');
            INSERT INTO orders VALUES
                (1, 1, 'paid', 12.5, '2026-01-01'),
                (2, 1, 'pending', 8.0, '2026-02-01');
            INSERT INTO categories VALUES (1, NULL, 'Root'), (2, 1, 'Child');
            """
        )
        connection.close()
        self.schema = inspect_sqlite_schema(self.database, db_id="fixture")
        self.structural_config, self.structural_index = self._build_structural_index()
        self.config_path = self._write_b7p_config()
        self.config = load_b7p_composer_config(self.config_path)
        self.selector = QuestionPlanHybridSelector(
            self.structural_index, self.structural_config
        )
        self.composer = B7PComposer(self.config, self.selector)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_structural_index(self):
        retrieval_rows = (
            ("list customer names", "SELECT name FROM customers"),
            (
                "count orders per customer",
                "SELECT c.name, COUNT(o.id) FROM customers c JOIN orders o "
                "ON c.id=o.customer_id GROUP BY c.name HAVING COUNT(o.id)>1",
            ),
            (
                "find orders above average",
                "SELECT id FROM orders WHERE amount > (SELECT AVG(amount) FROM orders)",
            ),
            (
                "monthly latest orders",
                "SELECT date(created_at), COUNT(*) FROM orders GROUP BY date(created_at) "
                "ORDER BY date(created_at) DESC LIMIT 5",
            ),
            (
                "combine customer names",
                "SELECT name FROM customers UNION SELECT name FROM categories",
            ),
            (
                "category descendants",
                "WITH RECURSIVE tree(id) AS (SELECT id FROM categories UNION ALL "
                "SELECT c.id FROM categories c JOIN tree t ON c.parent_id=t.id) "
                "SELECT id FROM tree",
            ),
        )
        entries = tuple(
            RetrievalIndexEntry(
                retrieval_id=f"spider1-train-{ordinal:05d}",
                source_ordinal=ordinal,
                db_id=f"source_{ordinal}",
                question=question,
                sql=sql,
                question_tokens=tuple(normalize_retrieval_text(question).split()),
            )
            for ordinal, (question, sql) in enumerate(retrieval_rows)
        )
        source = LoadedRetrievalIndex(
            entries=entries,
            manifest={
                "schema_version": 1,
                "index_id": "fixture-train-v1",
                "source": {"dataset_id": "spider1", "split": "train"},
                "counts": {"entries": len(entries)},
                "artifact": {"sha256": SOURCE_INDEX_SHA},
                "leakage_audit": {
                    "source_split_verified_train": True,
                    "spider2_examples_allowed": False,
                    "instance_id_overlaps": 0,
                    "database_overlaps": 0,
                    "normalized_question_overlaps": 0,
                    "spider2_metadata_sha256": ZERO_SHA,
                    "spider2_split_manifest_sha256": ZERO_SHA,
                },
            },
            manifest_sha256=SOURCE_MANIFEST_SHA,
        )
        path = self.root / "configs/retrieval/fixture-structural.toml"
        path.parent.mkdir(parents=True)
        path.write_text(
            f'''schema_version = 1
index_id = "fixture-structural-v1"
structural_version = "sql-skeleton-operators-v1"

[source]
dataset_id = "spider1"
split = "train"
index_id = "fixture-train-v1"
index_sha256 = "{SOURCE_INDEX_SHA}"
manifest_sha256 = "{SOURCE_MANIFEST_SHA}"
expected_entries = {len(entries)}

[policy]
max_results = 3
question_weight = 0.45
structure_weight = 0.55
minimum_structure_score = 0.30
max_sql_chars = 2000
max_total_sql_chars = 4500
require_structural_match = true

[artifact]
filename = "structural-index.jsonl"
''',
            encoding="utf-8",
        )
        config = load_structural_retrieval_config(path)
        index = build_structural_index(config, source)
        output = self.root / "configs/retrieval/generated"
        write_structural_index(index, output)
        return config, index

    def _write_b7p_config(self) -> Path:
        b6r = self.root / "configs/experiments/exp004-b6r.toml"
        b6r.parent.mkdir(parents=True)
        b6r.write_text(
            """mschema_examples_per_column = 3
mschema_max_text_length = 50
mschema_scan_rows_per_column = 24
schema_linker_version = "extractive-lexical-v1"
schema_link_max_tables = 8
schema_link_max_columns_per_table = 256
schema_link_minimum_columns_per_table = 4
schema_link_min_score = 4
schema_link_include_value_matches = true
schema_link_include_foreign_key_closure = true
schema_link_include_all_selected_table_columns = true
schema_link_fallback_mode = "full_schema"\n""",
            encoding="utf-8",
        )
        structural_manifest = self.root / "configs/retrieval/generated/structural-manifest.json"
        path = self.root / "configs/generation/gen001.toml"
        path.parent.mkdir(parents=True)
        path.write_text(
            f'''schema_version = 1
composer_id = "gen001-b7p-composer-v1"
prompt_version = "gen001-b7p-composer-v1"
dialect = "sqlite"
output_candidates = 1
max_prompt_chars = 120000
max_plan_chars = 16000
max_demonstration_question_chars = 1000

[dependencies]
b6r_config_path = "configs/experiments/exp004-b6r.toml"
b6r_config_sha256 = "{hashlib.sha256(b6r.read_bytes()).hexdigest()}"
semantic_plan_version = "semantic-plan-v1"
semantic_plan_record_version = "semantic-plan-record-v1"
structural_config_path = "configs/retrieval/fixture-structural.toml"
structural_config_sha256 = "{self.structural_config.config_sha256}"
structural_manifest_path = "configs/retrieval/generated/structural-manifest.json"
structural_manifest_sha256 = "{self.structural_index.manifest_sha256}"
structural_index_id = "fixture-structural-v1"
structural_index_sha256 = "{self.structural_index.manifest['artifact']['sha256']}"
structural_version = "sql-skeleton-operators-v1"
retrieval_strategy = "question-plan-hybrid-v1"

[schema_evidence]
mschema_examples_per_column = 3
mschema_max_text_length = 50
mschema_scan_rows_per_column = 24
schema_linker_version = "extractive-lexical-v1"
schema_link_max_tables = 8
schema_link_max_columns_per_table = 256
schema_link_minimum_columns_per_table = 4
schema_link_min_score = 4
schema_link_include_value_matches = true
schema_link_include_foreign_key_closure = true
schema_link_include_all_selected_table_columns = true
schema_link_fallback_mode = "full_schema"

[value_grounding]
mode = "semantic-plan-filter-columns-v1"
value_kinds = ["literal", "literal_list", "range", "relative_time"]
max_columns = 16

[runtime]
model_selection = "pending-model001"
temperature = 0.0
max_tokens = 1024
seed = 42
reasoning_effort = "low"
max_retries = 2
timeout_seconds = 60.0
''',
            encoding="utf-8",
        )
        return path

    def _validated(self, payload: dict[str, object]):
        return resolve_semantic_plan(
            json.dumps(payload),
            self.schema,
            expected_question=str(payload["question"]),
        )

    def test_composes_deterministic_single_query_contract_without_provider(self) -> None:
        question = "List customer names"
        plan = self._validated(_base_payload(question))
        with mock.patch(
            "text2sql.generation.b7p.sample_sqlite_mschema_values"
        ) as sampler:
            first = self.composer.compose(
                question, self.database, plan, db_id="fixture"
            )
            second = self.composer.compose(
                question, self.database, plan, db_id="fixture"
            )
        sampler.assert_not_called()

        self.assertEqual(first.prompt, second.prompt)
        self.assertEqual(first.prompt_sha256, second.prompt_sha256)
        self.assertIn("exactly one executable read-only SQLite query", first.prompt)
        self.assertIn("<validated_semantic_plan>", first.prompt)
        self.assertIn("<complete_compact_target_schema>", first.prompt)
        self.assertIn("<linked_detailed_target_mschema>", first.prompt)
        audit = first.to_audit_dict()
        self.assertFalse(audit["provider_called"])
        self.assertEqual(audit["output_candidates"], 1)
        self.assertEqual(audit["model_selection"], "pending-model001")
        self.assertFalse(audit["value_grounding"]["required"])

    def test_selective_value_grounding_includes_only_plan_filter_columns(self) -> None:
        question = "List paid order identifiers"
        payload = _base_payload(question)
        payload["outputs"] = [
            {
                "kind": "column",
                "columns": [_column("orders", "id")],
                "aggregation_alias": None,
                "alias": None,
                "description": None,
            }
        ]
        payload["sources"] = ["orders"]
        payload["filters"] = [
            {
                "columns": [_column("orders", "status")],
                "operator": "eq",
                "value_kind": "literal",
                "value": "paid",
                "description": "status is paid",
            }
        ]
        with mock.patch(
            "text2sql.generation.b7p.sample_sqlite_mschema_values",
            wraps=sample_sqlite_mschema_values,
        ) as sampler:
            result = self.composer.compose(
                question, self.database, self._validated(payload), db_id="fixture"
            )

        grounding = result.to_audit_dict()["value_grounding"]
        self.assertEqual(sampler.call_args.kwargs["columns"], {("orders", "status")})
        self.assertEqual(grounding["requested_columns"], ["orders.status"])
        self.assertEqual(grounding["included_value_counts"], {"orders.status": 2})
        grounding_block = result.prompt.split("<selective_value_grounding>\n", 1)[1].split(
            "\n</selective_value_grounding>", 1
        )[0]
        self.assertIn('"orders.status":["paid","pending"]', grounding_block)
        self.assertNotIn("Alice", grounding_block)

    def test_required_complex_plan_shapes_reach_the_composer_boundary(self) -> None:
        cases: dict[str, dict[str, object]] = {}

        join = _base_payload("List customers with orders")
        join["sources"] = ["customers", "orders"]
        join["joins"] = [
            {
                "left": _column("orders", "customer_id"),
                "right": _column("customers", "id"),
                "join_type": "inner",
            }
        ]
        cases["join"] = join

        nested = _base_payload("Find the largest order above an average")
        nested["outputs"] = [
            {
                "kind": "aggregation",
                "columns": [],
                "aggregation_alias": "largest_amount",
                "alias": None,
                "description": None,
            }
        ]
        nested["sources"] = ["orders"]
        nested["filters"] = [
            {
                "columns": [_column("orders", "amount")],
                "operator": "gt",
                "value_kind": "subquery",
                "value": None,
                "description": "above the average order amount",
            }
        ]
        nested["aggregations"] = [
            {
                "alias": "largest_amount",
                "function": "max",
                "column": _column("orders", "amount"),
                "distinct": False,
            }
        ]
        cases["nested_aggregation"] = nested

        temporal = _base_payload("List orders in the current calendar month")
        temporal["outputs"] = [
            {
                "kind": "column",
                "columns": [_column("orders", "created_at")],
                "aggregation_alias": None,
                "alias": None,
                "description": None,
            }
        ]
        temporal["sources"] = ["orders"]
        temporal["temporal"] = {
            "grain": "month",
            "columns": [_column("orders", "created_at")],
            "window": "current calendar month",
        }
        cases["temporal_window"] = temporal

        set_plan = _base_payload("Combine customer names")
        set_plan["set_operation"] = "union"
        cases["set"] = set_plan

        recursive = _base_payload("List every descendant category")
        recursive["outputs"] = [
            {
                "kind": "column",
                "columns": [_column("categories", "name")],
                "aggregation_alias": None,
                "alias": None,
                "description": None,
            }
        ]
        recursive["sources"] = ["categories"]
        recursive["recursion"] = True
        recursive["set_operation"] = "union_all"
        cases["recursive"] = recursive

        for label, payload in cases.items():
            with self.subTest(shape=label):
                result = self.composer.compose(
                    str(payload["question"]),
                    self.database,
                    self._validated(payload),
                    db_id="fixture",
                )
                self.assertLessEqual(len(result.prompt), self.config.max_prompt_chars)
                self.assertEqual(result.plan.plan.to_dict()["question"], payload["question"])
                self.assertEqual(result.retrieval.strategy, "question-plan-hybrid-v1")

    def test_project_composer_contract_is_frozen_and_dependency_verified(self) -> None:
        config = load_b7p_composer_config(
            PROJECT_ROOT / "configs/generation/gen001-b7p-composer-v1.toml"
        )
        self.assertEqual(config.config_sha256, "6b60bdf833ab6c90cc16471b8b218df24087c4ea02faeaf4239de43ee0ef89ee")
        self.assertEqual(config.output_candidates, 1)
        self.assertEqual(config.model_selection, "pending-model001")
        self.assertEqual(config.structural_index_sha256, "e4a8407153f6cfefa48e2ffd28102908ebb2c57ca089efa5c03d218fa849952d")

    def test_plan_and_schema_provenance_fail_closed(self) -> None:
        question = "List customer names"
        plan = self._validated(_base_payload(question))
        changed = type(plan)(
            plan=plan.plan,
            plan_sha256="f" * 64,
            schema_evidence_sha256=plan.schema_evidence_sha256,
            attempts=plan.attempts,
            repaired=plan.repaired,
            initial_issues=plan.initial_issues,
        )
        with self.assertRaisesRegex(B7PComposerError, "plan hash"):
            self.composer.compose(question, self.database, changed, db_id="fixture")

        changed = type(plan)(
            plan=plan.plan,
            plan_sha256=plan.plan_sha256,
            schema_evidence_sha256="f" * 64,
            attempts=plan.attempts,
            repaired=plan.repaired,
            initial_issues=plan.initial_issues,
        )
        with self.assertRaisesRegex(B7PComposerError, "schema hash"):
            self.composer.compose(question, self.database, changed, db_id="fixture")

    def test_dependency_checksum_drift_is_rejected(self) -> None:
        self.config.b6r_config_path.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(B7PComposerError, "B6R config checksum mismatch"):
            load_b7p_composer_config(self.config_path)


if __name__ == "__main__":
    unittest.main()
