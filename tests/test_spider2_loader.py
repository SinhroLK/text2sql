from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import (
    load_spider2_lite_sqlite,
    serialize_examples,
    validate_dataset_manifest,
    write_processed_dataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SOURCE = PROJECT_ROOT / "data/fixtures/spider2-lite-mini.jsonl"


class Spider2LiteLoaderTest(unittest.TestCase):
    def _write_protocol(self, root: Path, source_path: Path) -> tuple[Path, Path]:
        split = {
            "schema_version": 1,
            "protocol_id": "fixture-spider2-lite-v1",
            "source_commit": "a" * 40,
            "source_data_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "split_method": {"unit": "db", "salt": "fixture-salt", "algorithm": "fixture"},
            "development_db_ids": ["fixture_dev"],
            "test_db_ids": ["fixture_test"],
            "development_instance_ids": ["local001"],
            "test_instance_ids": ["local002"],
        }
        split_path = root / "configs/datasets/split.json"
        split_path.parent.mkdir(parents=True)
        split_path.write_text(json.dumps(split, sort_keys=True), encoding="utf-8")
        split_sha256 = hashlib.sha256(split_path.read_bytes()).hexdigest()
        source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

        protocol = f'''schema_version = 1
protocol_id = "fixture-spider2-lite-v1"

[benchmark]
name = "Spider 2.0-Lite fixture"
dialect = "sqlite"
expected_upstream_total = 4
expected_scope_total = 2
expected_development_total = 1
expected_test_total = 1
official_split_available = false
official_leaderboard_comparable = false
oracle_tables = false

[source]
repository_url = "https://example.invalid/Spider2"
commit = "{'a' * 40}"
data_path = "spider2-lite/spider2-lite.jsonl"
data_sha256 = "{source_sha256}"
evaluator_sha256 = "{'b' * 64}"
evaluator_utils_sha256 = "{'c' * 64}"
evaluation_manifest_sha256 = "{'d' * 64}"
pinned_bigquery_total = 1
pinned_snowflake_total = 1
pinned_sqlite_total = 2

[split]
manifest_path = "configs/datasets/split.json"
manifest_sha256 = "{split_sha256}"
salt = "fixture-salt"

[leakage_policy]
gold_sql_in_prompt = false
gold_sql_in_training = false
gold_sql_in_retrieval = false
test_questions_in_retrieval = false
test_results_visible_during_development = false
manual_per_test_prompt_edits = false

[evaluation]
headline_label = "Spider2-Lite SQLite fixture"
require_exact_id_coverage = true
missing_or_extra_predictions_fail_run = true
'''
        config_path = root / "configs/datasets/protocol.toml"
        config_path.write_text(protocol, encoding="utf-8")
        return config_path, split_path

    def test_loads_only_frozen_sqlite_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, _ = self._write_protocol(root, FIXTURE_SOURCE)

            dataset = load_spider2_lite_sqlite(FIXTURE_SOURCE, config_path, root)

            self.assertEqual([example.example_id for example in dataset.examples], ["local001", "local002"])
            self.assertEqual(len(dataset.for_split("development")), 1)
            self.assertEqual(len(dataset.for_split("test")), 1)
            self.assertTrue(all(example.dialect == "sqlite" for example in dataset.examples))
            self.assertTrue(all(example.gold_sql is None for example in dataset.examples))
            self.assertEqual(dataset.examples[0].metadata["external_knowledge"], "customers.md")
            self.assertEqual(dataset.manifest["counts"]["upstream_total"], 4)
            self.assertEqual(dataset.manifest["counts"]["selected_total"], 2)
            self.assertFalse(dataset.manifest["scope"]["contains_gold_sql"])

    def test_checksum_is_checked_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, _ = self._write_protocol(root, FIXTURE_SOURCE)
            corrupted_source = root / "corrupted.jsonl"
            corrupted_source.write_text("not json\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                load_spider2_lite_sqlite(corrupted_source, config_path, root)

    def test_rejects_gold_like_fields_even_with_matching_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "gold-like.jsonl"
            rows = [json.loads(line) for line in FIXTURE_SOURCE.read_text(encoding="utf-8").splitlines()]
            rows[2]["gold_sql"] = "SELECT name FROM customers"
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            config_path, _ = self._write_protocol(root, source)

            with self.assertRaisesRegex(ValueError, "gold-like fields"):
                load_spider2_lite_sqlite(source, config_path, root)

    def test_writes_deterministic_manifest_and_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, _ = self._write_protocol(root, FIXTURE_SOURCE)
            dataset = load_spider2_lite_sqlite(FIXTURE_SOURCE, config_path, root)
            output_dir = root / "processed"

            examples_path, manifest_path = write_processed_dataset(dataset, output_dir)
            expected_examples = serialize_examples(dataset.examples)
            self.assertEqual(examples_path.read_bytes(), expected_examples)
            self.assertEqual(
                dataset.manifest["artifacts"]["examples_sha256"],
                hashlib.sha256(expected_examples).hexdigest(),
            )
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), dataset.manifest)

            write_processed_dataset(dataset, output_dir)
            examples_path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_processed_dataset(dataset, output_dir)
            write_processed_dataset(dataset, output_dir, overwrite=True)
            self.assertEqual(examples_path.read_bytes(), expected_examples)

    def test_manifest_validation_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, _ = self._write_protocol(root, FIXTURE_SOURCE)
            dataset = load_spider2_lite_sqlite(FIXTURE_SOURCE, config_path, root)
            changed = json.loads(json.dumps(dataset.manifest))
            changed["counts"]["selected_total"] = 3

            with self.assertRaisesRegex(ValueError, "does not match"):
                validate_dataset_manifest(dataset.manifest, changed)


if __name__ == "__main__":
    unittest.main()
