from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from text2sql.retrieval import (
    FixedRandomSelector,
    RetrievalIndexError,
    RetrievalLeakageFirewall,
    TfidfCosineSelector,
    build_retrieval_selector,
    build_spider1_train_retrieval_index,
    build_spider2_leakage_firewall,
    load_verified_retrieval_index,
    normalize_retrieval_text,
    write_retrieval_index,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAIN_FIXTURE = PROJECT_ROOT / "data/fixtures/spider1-train-mini.json"
TABLES_FIXTURE = PROJECT_ROOT / "data/fixtures/spider1-tables-mini.json"


class RetrievalIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.split_path = self.root / "spider2-split.json"
        self.metadata_path = self.root / "spider2-examples.jsonl"
        split = {
            "development_instance_ids": ["local001"],
            "test_instance_ids": ["local999"],
            "development_db_ids": ["sealed_dev"],
            "test_db_ids": ["sealed_test"],
        }
        self.split_path.write_text(
            json.dumps(split, sort_keys=True), encoding="utf-8"
        )
        metadata = [
            {
                "example_id": "local001",
                "db_id": "sealed_dev",
                "question": "A development benchmark question",
                "dialect": "sqlite",
                "split": "development",
            },
            {
                "example_id": "local999",
                "db_id": "sealed_test",
                "question": "A sealed benchmark question",
                "dialect": "sqlite",
                "split": "test",
            },
        ]
        self.metadata_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in metadata),
            encoding="utf-8",
        )
        self.metadata_hash = hashlib.sha256(self.metadata_path.read_bytes()).hexdigest()
        self.split_hash = hashlib.sha256(self.split_path.read_bytes()).hexdigest()
        self.config_path = self._write_config()
        self.firewall = build_spider2_leakage_firewall(
            self.metadata_path,
            self.split_path,
            expected_metadata_sha256=self.metadata_hash,
            expected_split_manifest_sha256=self.split_hash,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(
        self,
        *,
        train_path: Path = TRAIN_FIXTURE,
        tables_path: Path = TABLES_FIXTURE,
    ) -> Path:
        path = self.root / "retrieval.toml"
        path.write_text(
            f'''schema_version = 1
index_id = "fixture-spider1-train-v1"

[origin]
official_page_url = "https://example.invalid/spider"
license = "CC BY-SA 4.0"

[source]
dataset_id = "spider1"
split = "train"
dialect = "sqlite"
expected_examples = 2
expected_databases = 2
train_sha256 = "{hashlib.sha256(train_path.read_bytes()).hexdigest()}"
tables_sha256 = "{hashlib.sha256(tables_path.read_bytes()).hexdigest()}"

[spider2_firewall]
metadata_sha256 = "{self.metadata_hash}"
split_manifest_sha256 = "{self.split_hash}"

[leakage_policy]
source_split_must_be_train = true
spider2_examples_allowed = false
spider2_databases_allowed = false
spider2_question_overlap_allowed = false

[artifact]
filename = "retrieval-index.jsonl"
''',
            encoding="utf-8",
        )
        return path

    def _build(self, firewall: RetrievalLeakageFirewall | None = None):
        return build_spider1_train_retrieval_index(
            TRAIN_FIXTURE,
            TABLES_FIXTURE,
            self.config_path,
            firewall or self.firewall,
        )

    def test_builds_deterministic_train_only_index(self) -> None:
        first = self._build()
        second = self._build()

        self.assertEqual(first, second)
        self.assertEqual(len(first.entries), 2)
        self.assertEqual(first.entries[0].retrieval_id, "spider1-train-00000")
        self.assertEqual(first.entries[0].question_tokens, ("list", "every", "student", "name"))
        self.assertEqual(first.manifest["counts"]["databases"], 2)
        self.assertEqual(first.manifest["leakage_audit"]["database_overlaps"], 0)
        self.assertFalse(first.manifest["leakage_audit"]["spider2_examples_allowed"])

    def test_source_checksum_is_checked_before_json_parsing(self) -> None:
        corrupted = self.root / "corrupted.json"
        corrupted.write_text("not json", encoding="utf-8")

        with self.assertRaisesRegex(RetrievalIndexError, "checksum mismatch"):
            build_spider1_train_retrieval_index(
                corrupted, TABLES_FIXTURE, self.config_path, self.firewall
            )

    def test_rejects_spider2_database_leakage(self) -> None:
        contaminated = RetrievalLeakageFirewall(
            forbidden_instance_ids=self.firewall.forbidden_instance_ids,
            forbidden_db_ids=self.firewall.forbidden_db_ids | {"school"},
            forbidden_question_fingerprints=self.firewall.forbidden_question_fingerprints,
            metadata_sha256=self.metadata_hash,
            split_manifest_sha256=self.split_hash,
        )

        with self.assertRaisesRegex(RetrievalIndexError, "database leaked"):
            self._build(contaminated)

    def test_rejects_normalized_spider2_question_leakage(self) -> None:
        contaminated = RetrievalLeakageFirewall(
            forbidden_instance_ids=self.firewall.forbidden_instance_ids,
            forbidden_db_ids=self.firewall.forbidden_db_ids,
            forbidden_question_fingerprints=(
                self.firewall.forbidden_question_fingerprints
                | {normalize_retrieval_text("LIST every student name!")}
            ),
            metadata_sha256=self.metadata_hash,
            split_manifest_sha256=self.split_hash,
        )

        with self.assertRaisesRegex(RetrievalIndexError, "question leaked"):
            self._build(contaminated)

    def test_firewall_requires_exact_spider2_coverage(self) -> None:
        incomplete = self.root / "incomplete.jsonl"
        incomplete.write_text(
            self.metadata_path.read_text(encoding="utf-8").splitlines()[0] + "\n",
            encoding="utf-8",
        )
        checksum = hashlib.sha256(incomplete.read_bytes()).hexdigest()

        with self.assertRaisesRegex(RetrievalIndexError, "coverage mismatch"):
            build_spider2_leakage_firewall(
                incomplete,
                self.split_path,
                expected_metadata_sha256=checksum,
                expected_split_manifest_sha256=self.split_hash,
            )

    def test_written_index_is_checksum_verified(self) -> None:
        index_path, manifest_path = write_retrieval_index(
            self._build(), self.root / "output"
        )
        loaded = load_verified_retrieval_index(index_path, manifest_path)
        self.assertEqual(len(loaded.entries), 2)

        index_path.write_bytes(index_path.read_bytes() + b"tampered\n")
        with self.assertRaisesRegex(RetrievalIndexError, "checksum mismatch"):
            load_verified_retrieval_index(index_path, manifest_path)

    def test_verified_loader_can_require_frozen_manifest(self) -> None:
        index_path, manifest_path = write_retrieval_index(
            self._build(), self.root / "frozen-output"
        )
        expected_path = self.root / "expected-manifest.json"
        expected_path.write_bytes(manifest_path.read_bytes())
        loaded = load_verified_retrieval_index(
            index_path,
            manifest_path,
            expected_manifest_path=expected_path,
        )
        self.assertEqual(len(loaded.entries), 2)

        changed = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed["index_id"] = "untrusted-index"
        manifest_path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(
            RetrievalIndexError, "version-controlled contract"
        ):
            load_verified_retrieval_index(
                index_path,
                manifest_path,
                expected_manifest_path=expected_path,
            )

    def test_project_contract_is_frozen_to_official_train(self) -> None:
        config = (
            PROJECT_ROOT / "configs/datasets/spider1-train-retrieval-v1.toml"
        ).read_text(encoding="utf-8")
        manifest = json.loads(
            (
                PROJECT_ROOT
                / "configs/datasets/spider1-train-retrieval-manifest-v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn('split = "train"', config)
        self.assertEqual(manifest["counts"]["entries"], 7000)
        self.assertEqual(manifest["counts"]["databases"], 140)
        self.assertEqual(manifest["leakage_audit"]["forbidden_spider2_instances"], 135)
        self.assertEqual(manifest["leakage_audit"]["database_overlaps"], 0)
        self.assertEqual(manifest["leakage_audit"]["normalized_question_overlaps"], 0)

    def test_fixed_random_selection_is_seeded_and_target_independent(self) -> None:
        index = self._build()
        selector = FixedRandomSelector(index, k=1, seed=42)
        first = selector.select("students")
        second = selector.select("completely different target")

        self.assertEqual(first, second)
        self.assertEqual(first.strategy, "random-fixed-v1")
        self.assertIsNone(first.entries[0].score)

    def test_tfidf_selection_is_deterministic_and_question_sensitive(self) -> None:
        index = self._build()
        selector = TfidfCosineSelector(index, k=1)

        student = selector.select("Show student names")
        books = selector.select("Count available books")

        self.assertEqual(student.entries[0].entry.db_id, "school")
        self.assertEqual(books.entries[0].entry.db_id, "library")
        self.assertGreater(student.entries[0].score or 0.0, 0.0)
        self.assertEqual(student, selector.select("Show student names"))

    def test_selector_factory_enforces_frozen_seed_policy(self) -> None:
        index = self._build()
        with self.assertRaisesRegex(RetrievalIndexError, "requires a seed"):
            build_retrieval_selector(
                index, strategy="random-fixed-v1", k=1, seed=None
            )
        with self.assertRaisesRegex(RetrievalIndexError, "does not use a seed"):
            build_retrieval_selector(
                index, strategy="tfidf-cosine-v1", k=1, seed=42
            )


if __name__ == "__main__":
    unittest.main()
