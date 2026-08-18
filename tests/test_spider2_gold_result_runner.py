from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import Text2SQLExample
from text2sql.evaluation import (
    EvaluationResourceError,
    OfficialGoldResultStore,
    Spider2GoldResultRunner,
    Spider2SQLiteDatabaseResolver,
)


class Spider2GoldResultRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.databases = self.root / "databases"
        self.results = self.root / "exec_result"
        self.databases.mkdir()
        self.results.mkdir()
        connection = sqlite3.connect(self.databases / "fixture.sqlite")
        connection.executescript(
            "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);"
            "INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob');"
        )
        connection.close()
        (self.results / "local001_a.csv").write_text(
            "name,count\nAlice,1\nBob,1\n", encoding="utf-8"
        )
        (self.results / "local001_b.csv").write_text(
            "name\nNobody\n", encoding="utf-8"
        )
        self.metadata = self.root / "spider2lite_eval.jsonl"
        self.metadata.write_text(
            json.dumps(
                {
                    "instance_id": "local001",
                    "condition_cols": [[0], [0]],
                    "ignore_order": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.dataset = LoadedSpider2LiteDataset(
            examples=(
                Text2SQLExample(
                    "local001", "fixture", "Names", "sqlite", split="development"
                ),
            ),
            manifest={},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _runner(self) -> Spider2GoldResultRunner:
        return Spider2GoldResultRunner(
            dataset=self.dataset,
            database_resolver=Spider2SQLiteDatabaseResolver(self.databases),
            gold_results=OfficialGoldResultStore.from_official_directory(
                self.results, self.metadata
            ),
        )

    def test_preflight_needs_no_reference_sql(self) -> None:
        report = self._runner().preflight()
        self.assertTrue(report.ready)
        self.assertEqual(report.missing_gold_result_ids, ())

    def test_any_official_variant_can_match(self) -> None:
        record = self._runner().evaluate_one(
            "local001", "SELECT name FROM customers ORDER BY id DESC"
        )
        self.assertTrue(record.result.correct)
        self.assertEqual(record.matched_gold_result_file, "local001_a.csv")
        self.assertEqual(len(record.matched_gold_result_sha256), 64)

    def test_incorrect_and_execution_error_are_structured(self) -> None:
        wrong = self._runner().evaluate_one("local001", "SELECT id FROM customers")
        broken = self._runner().evaluate_one("local001", "SELECT missing FROM customers")
        self.assertEqual(wrong.result.status, "result_mismatch")
        self.assertEqual(broken.result.status, "generated_execution_error")

    def test_missing_gold_result_fails_coverage(self) -> None:
        self.results.joinpath("local001_a.csv").unlink()
        self.results.joinpath("local001_b.csv").unlink()
        runner = self._runner()
        with self.assertRaises(EvaluationResourceError) as raised:
            runner.evaluate_batch({"local001": "SELECT 1"})
        self.assertEqual(raised.exception.code, "gold_result_coverage_mismatch")

    def test_variant_metadata_count_must_match(self) -> None:
        self.metadata.write_text(
            json.dumps(
                {
                    "instance_id": "local001",
                    "condition_cols": [[0], [0], [0]],
                    "ignore_order": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(EvaluationResourceError) as raised:
            OfficialGoldResultStore.from_official_directory(self.results, self.metadata)
        self.assertEqual(raised.exception.code, "gold_result_variant_mismatch")


if __name__ == "__main__":
    unittest.main()
