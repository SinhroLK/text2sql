from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from text2sql.planning import ValidatedSemanticPlan, parse_semantic_plan, semantic_plan_sha256
from text2sql.retrieval.index import (
    LoadedRetrievalIndex,
    RetrievalIndexEntry,
    RetrievalIndexError,
)
from text2sql.retrieval.structural import (
    QuestionPlanHybridSelector,
    build_per_target_retrieval_audit,
    build_structural_index,
    extract_sql_structure,
    load_structural_retrieval_config,
    load_verified_structural_index,
    normalize_sql_skeleton,
    semantic_plan_structure,
    write_structural_index,
)


ZERO_SHA = "0" * 64
SOURCE_INDEX_SHA = "a" * 64
SOURCE_MANIFEST_SHA = "b" * 64


def _entry(ordinal: int, question: str, sql: str) -> RetrievalIndexEntry:
    return RetrievalIndexEntry(
        retrieval_id=f"spider1-train-{ordinal:05d}",
        source_ordinal=ordinal,
        db_id=f"train_db_{ordinal}",
        question=question,
        sql=sql,
        question_tokens=tuple(question.casefold().split()),
    )


def _source() -> LoadedRetrievalIndex:
    entries = (
        _entry(0, "list customer names", "SELECT name FROM customers"),
        _entry(
            1,
            "count orders for customers",
            "SELECT c.name, COUNT(o.id) FROM customers c JOIN orders o "
            "ON c.id = o.customer_id GROUP BY c.name HAVING COUNT(o.id) > 1",
        ),
        _entry(
            2,
            "latest order dates",
            "SELECT created_at FROM orders ORDER BY date(created_at) DESC LIMIT 5",
        ),
    )
    return LoadedRetrievalIndex(
        entries=entries,
        manifest={
            "schema_version": 1,
            "index_id": "fixture-spider1-train-v1",
            "source": {"dataset_id": "spider1", "split": "train"},
            "counts": {"entries": 3},
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


def _plan(question: str, *, aggregate: bool = True):
    payload = {
        "plan_version": "semantic-plan-v1",
        "db_id": "target_db",
        "dialect": "sqlite",
        "question": question,
        "outputs": [
            {
                "kind": "aggregation" if aggregate else "column",
                "columns": [] if aggregate else [{"table": "customers", "column": "name"}],
                "aggregation_alias": "number_of_orders" if aggregate else None,
                "alias": None,
                "description": None,
            }
        ],
        "sources": ["customers", "orders"] if aggregate else ["customers"],
        "joins": [
            {
                "left": {"table": "customers", "column": "id"},
                "right": {"table": "orders", "column": "customer_id"},
                "join_type": "inner",
            }
        ] if aggregate else [],
        "filters": [],
        "aggregations": [
            {
                "alias": "number_of_orders",
                "function": "count",
                "column": {"table": "orders", "column": "id"},
                "distinct": False,
            }
        ] if aggregate else [],
        "group_by": [{"table": "customers", "column": "name"}] if aggregate else [],
        "having": [
            {
                "columns": [{"table": "orders", "column": "id"}],
                "operator": "gt",
                "value_kind": "literal",
                "value": 1,
                "description": "more than one order",
            }
        ] if aggregate else [],
        "ordering": [],
        "limit": None,
        "ties": "not_applicable",
        "temporal": {"grain": "none", "columns": [], "window": None},
        "recursion": False,
        "set_operation": "none",
        "uncertainties": [],
    }
    return parse_semantic_plan(json.dumps(payload))

def _recursive_plan(question: str):
    payload = _plan(question, aggregate=False).to_dict()
    payload["recursion"] = True
    payload["set_operation"] = "union_all"
    return parse_semantic_plan(json.dumps(payload))


def _validated(plan):
    return ValidatedSemanticPlan(
        plan=plan,
        plan_sha256=semantic_plan_sha256(plan),
        schema_evidence_sha256="e" * 64,
        attempts=1,
        repaired=False,
        initial_issues=(),
    )

class StructuralRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "structural.toml"
        self._write_config()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(
        self,
        *,
        minimum: float = 0.30,
        max_results: int = 2,
        max_sql_chars: int = 500,
        max_total_sql_chars: int = 700,
    ) -> None:
        self.config_path.write_text(
            f'''schema_version = 1
index_id = "fixture-structural-v1"
structural_version = "sql-skeleton-operators-v1"

[source]
dataset_id = "spider1"
split = "train"
index_id = "fixture-spider1-train-v1"
index_sha256 = "{SOURCE_INDEX_SHA}"
manifest_sha256 = "{SOURCE_MANIFEST_SHA}"
expected_entries = 3

[policy]
max_results = {max_results}
question_weight = 0.45
structure_weight = 0.55
minimum_structure_score = {minimum}
max_sql_chars = {max_sql_chars}
max_total_sql_chars = {max_total_sql_chars}
require_structural_match = true

[artifact]
filename = "structural-index.jsonl"
''',
            encoding="utf-8",
        )

    def _build(self):
        config = load_structural_retrieval_config(self.config_path)
        return config, build_structural_index(config, _source())

    def test_sql_structure_and_skeleton_are_provider_free_and_literal_blind(self) -> None:
        sql = """WITH RECURSIVE x AS (
            SELECT id FROM t WHERE note = 'SELECT JOIN 99'
            UNION ALL SELECT id FROM x
        ) SELECT COUNT(DISTINCT id) OVER () FROM x ORDER BY date(id) LIMIT 2
        -- JOIN ignored
        """
        signature = extract_sql_structure(sql)

        self.assertTrue(signature.has_cte)
        self.assertTrue(signature.recursive)
        self.assertTrue(signature.has_subquery)
        self.assertTrue(signature.has_aggregation)
        self.assertTrue(signature.has_window)
        self.assertTrue(signature.has_ordering)
        self.assertTrue(signature.has_limit)
        self.assertTrue(signature.has_temporal)
        self.assertEqual(signature.set_operation, "union_all")
        self.assertEqual(signature.join_count, 0)
        skeleton = normalize_sql_skeleton(sql)
        self.assertNotIn("99", skeleton)
        self.assertNotIn("note", skeleton)

        temporal_payload = _plan("monthly customers", aggregate=False).to_dict()
        temporal_payload["temporal"] = {
            "grain": "month",
            "columns": [{"table": "customers", "column": "name"}],
            "window": "calendar month",
        }
        temporal_signature = semantic_plan_structure(parse_semantic_plan(json.dumps(temporal_payload)))
        self.assertTrue(temporal_signature.has_temporal)
        self.assertFalse(temporal_signature.has_window)

    def test_structural_index_is_deterministic_compact_and_verified(self) -> None:
        config, first = self._build()
        _, second = self._build()
        self.assertEqual(first, second)
        self.assertFalse(first.manifest["artifact"]["contains_question_or_sql"])
        self.assertEqual(first.manifest["leakage_audit"]["database_overlaps"], 0)

        artifact, manifest = write_structural_index(first, self.root / "out")
        expected = self.root / "expected.json"
        expected.write_bytes(manifest.read_bytes())
        loaded = load_verified_structural_index(
            artifact, manifest, _source(), config, expected_manifest_path=expected
        )
        self.assertEqual(loaded.entries, first.entries)

        artifact.write_bytes(artifact.read_bytes() + b"tampered\n")
        with self.assertRaisesRegex(RetrievalIndexError, "checksum mismatch"):
            load_verified_structural_index(artifact, manifest, _source(), config)

    def test_source_leakage_audit_is_rechecked(self) -> None:
        config = load_structural_retrieval_config(self.config_path)
        source = _source()
        source.manifest["leakage_audit"]["database_overlaps"] = 1
        with self.assertRaisesRegex(RetrievalIndexError, "contains overlap"):
            build_structural_index(config, source)

    def test_hybrid_ranking_uses_plan_structure_and_audits_components(self) -> None:
        config, index = self._build()
        selector = QuestionPlanHybridSelector(index, config)
        with self.assertRaisesRegex(RetrievalIndexError, "requires a validated"):
            selector.select("list customer names", _plan("list customer names"))  # type: ignore[arg-type]
        selection = selector.select(
            "list customer names", _validated(_plan("list customer names"))
        )

        self.assertEqual(selection.entries[0].entry.source_ordinal, 1)
        audit = selection.entries[0].to_dict()
        self.assertGreater(audit["scores"]["structure"], 0.9)
        self.assertIn("aggregation", audit["structure_audit"]["matched_tags"])
        self.assertLessEqual(len(selection.entries), config.max_results)
        self.assertLessEqual(selection.total_sql_chars, config.max_total_sql_chars)

    def test_no_structural_match_returns_no_full_context(self) -> None:
        self._write_config(minimum=1.0)
        config, index = self._build()
        selection = QuestionPlanHybridSelector(index, config).select(
            "list customer names", _validated(_recursive_plan("list customer names"))
        )
        self.assertEqual(selection.entries, ())
        self.assertEqual(selection.total_sql_chars, 0)

        self.assertTrue(selection.to_dict()["bounds"]["empty_due_to_no_structural_match"])

    def test_per_target_audit_forbids_test_scope_and_records_plan_identity(self) -> None:
        config, index = self._build()
        selector = QuestionPlanHybridSelector(index, config)
        question = "list customer names"
        semantic = _plan(question, aggregate=False)
        plan = _validated(semantic)
        audit = build_per_target_retrieval_audit(
            selector,
            [("fixture-001", "target_db", question, plan)],
            scope="fixture",
        )
        self.assertFalse(audit["gold_sql_used"])
        self.assertEqual(audit["targets"][0]["plan_sha256"], semantic_plan_sha256(semantic))
        self.assertIn("scores", audit["targets"][0]["retrieval"]["selected"][0])
        self.assertEqual(audit["source_structural_manifest_sha256"], index.manifest_sha256)
        with self.assertRaisesRegex(RetrievalIndexError, "test is forbidden"):
            build_per_target_retrieval_audit(selector, [], scope="test")


if __name__ == "__main__":
    unittest.main()
