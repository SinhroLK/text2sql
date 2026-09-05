from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset, load_spider2_lite_sqlite
from text2sql.domain import Text2SQLExample
from text2sql.evaluation import (
    EvaluationResourceError,
    ProtectedReferenceSQLStore,
    Spider2EvaluationRunner,
    Spider2SQLiteDatabaseResolver,
    load_generated_sql_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINI_SOURCE = PROJECT_ROOT / "data/fixtures/spider2-lite-mini.jsonl"


class Spider2EvaluationRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_dir = self.root / "databases"
        self.sql_dir = self.root / "protected/sql"
        self.database_dir.mkdir(parents=True)
        self.sql_dir.mkdir(parents=True)
        self._create_database("fixture_dev")
        self._create_database("fixture_test")
        (self.sql_dir / "local001.sql").write_text(
            "SELECT name FROM customers ORDER BY id", encoding="utf-8"
        )
        (self.sql_dir / "local002.sql").write_text("SELECT COUNT(*) FROM customers", encoding="utf-8")
        self.metadata_path = self.root / "protected/spider2lite_eval.jsonl"
        self.metadata_path.write_text(
            "\n".join(
                [
                    json.dumps({"instance_id": "local001", "condition_cols": [], "ignore_order": True}),
                    json.dumps({"instance_id": "local002", "condition_cols": [0], "ignore_order": True}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.dataset = LoadedSpider2LiteDataset(
            examples=(
                Text2SQLExample("local001", "fixture_dev", "Names", "sqlite", split="development"),
                Text2SQLExample("local002", "fixture_test", "Count", "sqlite", split="test"),
            ),
            manifest={"fixture": True},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_database(self, db_id: str) -> Path:
        path = self.database_dir / f"{db_id}.sqlite"
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);"
                "INSERT INTO customers(name) VALUES ('Alice'), ('Bob');"
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def _runner(self, dataset: LoadedSpider2LiteDataset | None = None) -> Spider2EvaluationRunner:
        return Spider2EvaluationRunner(
            dataset=dataset or self.dataset,
            database_resolver=Spider2SQLiteDatabaseResolver(self.database_dir),
            references=ProtectedReferenceSQLStore.from_official_directory(
                self.sql_dir, self.metadata_path
            ),
        )

    def test_db_id_maps_to_exact_sqlite_path(self) -> None:
        resolved = Spider2SQLiteDatabaseResolver(self.database_dir).resolve("fixture_dev")

        self.assertEqual(resolved.path, (self.database_dir / "fixture_dev.sqlite").resolve())
        self.assertEqual(len(resolved.sha256), 64)

    def test_missing_sqlite_database_has_structured_error(self) -> None:
        with self.assertRaises(EvaluationResourceError) as raised:
            Spider2SQLiteDatabaseResolver(self.database_dir).resolve("missing")

        self.assertEqual(raised.exception.code, "database_not_found")
        self.assertIn("expected_path", raised.exception.context)

    def test_reference_sql_is_found_only_through_protected_store(self) -> None:
        reference = ProtectedReferenceSQLStore.from_official_directory(
            self.sql_dir, self.metadata_path
        ).get("local001")

        self.assertIn("SELECT name", reference.sql)
        self.assertTrue(reference.ignore_order)
        self.assertEqual(reference.condition_cols_variants, (None,))

    def test_missing_reference_sql_has_structured_error(self) -> None:
        store = ProtectedReferenceSQLStore.from_official_directory(self.sql_dir, self.metadata_path)
        with self.assertRaises(EvaluationResourceError) as raised:
            store.get("local999")
        self.assertEqual(raised.exception.code, "reference_sql_not_found")

    def test_duplicate_reference_metadata_is_rejected(self) -> None:
        duplicate = self.metadata_path.read_text(encoding="utf-8")
        duplicate += json.dumps({"instance_id": "local001", "condition_cols": [], "ignore_order": True}) + "\n"
        self.metadata_path.write_text(duplicate, encoding="utf-8")

        with self.assertRaises(EvaluationResourceError) as raised:
            ProtectedReferenceSQLStore.from_official_directory(self.sql_dir, self.metadata_path)
        self.assertEqual(raised.exception.code, "duplicate_reference_id")

    def test_unrelated_sql_without_metadata_does_not_block_selected_scope(self) -> None:
        (self.sql_dir / "unrelated.sql").write_text("SELECT 1", encoding="utf-8")

        store = ProtectedReferenceSQLStore.from_official_directory(self.sql_dir, self.metadata_path)

        self.assertEqual(store.get("local001").example_id, "local001")
        with self.assertRaises(EvaluationResourceError) as raised:
            store.get("unrelated")
        self.assertEqual(raised.exception.code, "evaluation_metadata_missing")

    def test_duplicate_data003_example_id_is_rejected(self) -> None:
        duplicate_dataset = LoadedSpider2LiteDataset(
            examples=(self.dataset.examples[0], self.dataset.examples[0]),
            manifest={},
        )
        with self.assertRaises(EvaluationResourceError) as raised:
            self._runner(duplicate_dataset)
        self.assertEqual(raised.exception.code, "duplicate_example_id")

    def test_data003_loader_supplies_runner_examples(self) -> None:
        config_path, manifest_path = self._write_fixture_protocol()
        dataset = load_spider2_lite_sqlite(
            MINI_SOURCE,
            config_path,
            self.root,
            manifest_path,
        )

        self.assertEqual(len(dataset.for_split("development")), 1)
        result = self._runner(dataset).evaluate_one(
            "local001", "SELECT name FROM customers ORDER BY id DESC"
        )
        self.assertTrue(result.result.correct)

    def test_single_example_evaluation_uses_eval001(self) -> None:
        record = self._runner().evaluate_one(
            "local001", "SELECT name AS other_alias FROM customers ORDER BY id DESC"
        )

        self.assertTrue(record.result.correct)
        self.assertEqual(record.result.status, "correct")
        self.assertEqual(len(record.database_sha256), 64)
        self.assertEqual(len(record.reference_sql_sha256), 64)

    def test_batch_evaluation_aggregates_execution_accuracy(self) -> None:
        batch = self._runner().evaluate_batch(
            {"local001": "SELECT name FROM customers WHERE id = 1"},
            split="development",
        )

        self.assertEqual(batch.total, 1)
        self.assertEqual(batch.evaluated, 1)
        self.assertEqual(batch.correct, 0)
        self.assertEqual(batch.incorrect, 1)
        self.assertEqual(batch.execution_errors, 0)
        self.assertEqual(batch.execution_accuracy, 0.0)

    def test_missing_prediction_id_fails_coverage(self) -> None:
        with self.assertRaises(EvaluationResourceError) as raised:
            self._runner().evaluate_batch({}, split="development")
        self.assertEqual(raised.exception.code, "prediction_coverage_mismatch")
        self.assertEqual(raised.exception.context["missing"], ["local001"])

    def test_extra_prediction_id_fails_coverage(self) -> None:
        with self.assertRaises(EvaluationResourceError) as raised:
            self._runner().evaluate_batch(
                {"local001": "SELECT 1", "local999": "SELECT 1"},
                split="development",
            )
        self.assertEqual(raised.exception.code, "prediction_coverage_mismatch")
        self.assertEqual(raised.exception.context["extra"], ["local999"])

    def test_duplicate_prediction_jsonl_is_rejected(self) -> None:
        path = self.root / "predictions.jsonl"
        line = json.dumps({"example_id": "local001", "generated_sql": "SELECT 1"})
        path.write_text(f"{line}\n{line}\n", encoding="utf-8")

        with self.assertRaises(EvaluationResourceError) as raised:
            load_generated_sql_jsonl(path)
        self.assertEqual(raised.exception.code, "duplicate_prediction_id")

    def test_explicit_failed_completion_stays_in_evaluation_denominator(self) -> None:
        path = self.root / "failed-prediction.jsonl"
        row = {"schema_version": 2, "example_id": "local001", "generated_sql": "",
               "generation_status": "failed", "failure_code": "incomplete_completion"}
        path.write_text(json.dumps(row) + "\n")
        batch = self._runner().evaluate_batch(load_generated_sql_jsonl(path), split="development")
        self.assertEqual(batch.total, 1)
        self.assertEqual(batch.execution_errors, 1)
        self.assertEqual(batch.execution_accuracy, 0.0)
        row.pop("failure_code")
        path.write_text(json.dumps(row) + "\n")
        with self.assertRaises(EvaluationResourceError):
            load_generated_sql_jsonl(path)

    def test_execution_error_is_propagated_and_counted(self) -> None:
        batch = self._runner().evaluate_batch(
            {"local001": "SELECT missing_column FROM customers"},
            split="development",
        )

        self.assertEqual(batch.execution_errors, 1)
        self.assertEqual(batch.evaluated, 0)
        self.assertEqual(batch.execution_accuracy, 0.0)
        self.assertEqual(batch.records[0].result.status, "generated_execution_error")

    def test_reference_coverage_is_checked_before_batch_execution(self) -> None:
        (self.sql_dir / "local001.sql").unlink()
        runner = self._runner()
        with self.assertRaises(EvaluationResourceError) as raised:
            runner.evaluate_batch({"local001": "SELECT 1"}, split="development")
        self.assertEqual(raised.exception.code, "reference_coverage_mismatch")

    def test_preflight_reports_missing_database_and_reference(self) -> None:
        (self.database_dir / "fixture_dev.sqlite").unlink()
        (self.sql_dir / "local001.sql").unlink()
        runner = self._runner()

        preflight = runner.preflight(split="development")

        self.assertFalse(preflight.ready)
        self.assertEqual(preflight.missing_database_ids, ("fixture_dev",))
        self.assertEqual(preflight.missing_reference_ids, ("local001",))

    def test_ready_preflight_can_freeze_resource_checksums(self) -> None:
        runner = self._runner()

        preflight = runner.preflight(split="development")
        manifest = runner.resource_manifest(split="development")

        self.assertTrue(preflight.ready)
        self.assertEqual(manifest["split"], "development")
        self.assertEqual(manifest["databases"][0]["db_id"], "fixture_dev")
        self.assertEqual(len(manifest["databases"][0]["sha256"]), 64)
        self.assertEqual(manifest["reference_sql"][0]["example_id"], "local001")
        self.assertEqual(len(manifest["evaluation_metadata"]["sha256"]), 64)

    def test_evaluation_metadata_checksum_is_pinned(self) -> None:
        with self.assertRaises(EvaluationResourceError) as raised:
            ProtectedReferenceSQLStore.from_official_directory(
                self.sql_dir,
                self.metadata_path,
                expected_metadata_sha256="0" * 64,
            )

        self.assertEqual(raised.exception.code, "evaluation_metadata_checksum_mismatch")

    def _write_fixture_protocol(self) -> tuple[Path, Path]:
        split = {
            "schema_version": 1,
            "protocol_id": "fixture-spider2-lite-v1",
            "source_commit": "a" * 40,
            "source_data_sha256": hashlib.sha256(MINI_SOURCE.read_bytes()).hexdigest(),
            "split_method": {"unit": "db", "salt": "fixture", "algorithm": "fixture"},
            "development_db_ids": ["fixture_dev"],
            "test_db_ids": ["fixture_test"],
            "development_instance_ids": ["local001"],
            "test_instance_ids": ["local002"],
        }
        split_path = self.root / "configs/split.json"
        split_path.parent.mkdir(parents=True, exist_ok=True)
        split_path.write_text(json.dumps(split, sort_keys=True), encoding="utf-8")
        source_sha = hashlib.sha256(MINI_SOURCE.read_bytes()).hexdigest()
        split_sha = hashlib.sha256(split_path.read_bytes()).hexdigest()
        config_path = self.root / "configs/protocol.toml"
        config_path.write_text(
            f'''schema_version = 1
protocol_id = "fixture-spider2-lite-v1"
[benchmark]
name = "fixture"
dialect = "sqlite"
expected_upstream_total = 4
expected_scope_total = 2
expected_development_total = 1
expected_test_total = 1
official_split_available = false
official_leaderboard_comparable = false
oracle_tables = false
[source]
repository_url = "https://example.invalid"
commit = "{'a' * 40}"
data_path = "spider2-lite.jsonl"
data_sha256 = "{source_sha}"
evaluator_sha256 = "{'b' * 64}"
evaluator_utils_sha256 = "{'c' * 64}"
evaluation_manifest_sha256 = "{'d' * 64}"
pinned_bigquery_total = 1
pinned_snowflake_total = 1
pinned_sqlite_total = 2
[split]
manifest_path = "configs/split.json"
manifest_sha256 = "{split_sha}"
salt = "fixture"
[leakage_policy]
gold_sql_in_prompt = false
gold_sql_in_training = false
gold_sql_in_retrieval = false
test_questions_in_retrieval = false
test_results_visible_during_development = false
manual_per_test_prompt_edits = false
[evaluation]
headline_label = "fixture"
require_exact_id_coverage = true
missing_or_extra_predictions_fail_run = true
''',
            encoding="utf-8",
        )
        dataset = load_spider2_lite_sqlite(MINI_SOURCE, config_path, self.root)
        manifest_path = self.root / "configs/manifest.json"
        manifest_path.write_text(json.dumps(dataset.manifest), encoding="utf-8")
        return config_path, manifest_path


if __name__ == "__main__":
    unittest.main()
