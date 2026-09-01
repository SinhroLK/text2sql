from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import GenerationInput, Text2SQLExample
from text2sql.experiments import (
    BaselineExperimentRunner,
    ExperimentConfigurationError,
    ExperimentRunError,
    audit_development_retrieval,
    load_baseline_config,
)
from text2sql.pipeline import Text2SQLPipeline
from text2sql.retrieval import (
    LoadedRetrievalIndex,
    RetrievalIndexEntry,
)
from text2sql.providers.base import ProviderResponse
from text2sql.evaluation import Spider2SQLiteDatabaseResolver


MODEL_ID = "openai/gpt-oss-120b"


class CountingProvider:
    provider_name = "groq"
    model_id = MODEL_ID

    def __init__(self) -> None:
        self.inputs: list[GenerationInput] = []

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        self.inputs.append(generation_input)
        return ProviderResponse(
            candidates=("SELECT name FROM customers",),
            input_tokens=10,
            output_tokens=4,
        )


@dataclass
class FakeBatch:
    total: int
    evaluated: int

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "evaluated": self.evaluated,
            "execution_accuracy": 1.0,
        }


class FakeEvaluator:
    def __init__(self, resolver: Spider2SQLiteDatabaseResolver) -> None:
        self.database_resolver = resolver
        self.calls: list[dict[str, str]] = []
    def resource_manifest(self, *, split: str) -> dict[str, str]:
        return {"split": split, "fixture": "sha256-pinned"}


    def evaluate_batch(
        self, generated_sql: dict[str, str], *, split: str
    ) -> FakeBatch:
        self.calls.append(dict(generated_sql))
        if split != "development":
            raise AssertionError("test split crossed EXP-001 firewall")
        return FakeBatch(total=len(generated_sql), evaluated=len(generated_sql))


class BaselineExperimentRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        database_dir = self.root / "databases"
        database_dir.mkdir()
        connection = sqlite3.connect(database_dir / "fixture.sqlite")
        connection.executescript(
            "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);"
            "INSERT INTO customers VALUES (1, 'Alice');"
        )
        connection.close()
        self.resolver = Spider2SQLiteDatabaseResolver(database_dir)
        self.dataset = LoadedSpider2LiteDataset(
            examples=(
                Text2SQLExample(
                    "local001", "fixture", "List customers", "sqlite", "development"
                ),
                Text2SQLExample(
                    "local002", "fixture", "Show customer names", "sqlite", "development"
                ),
                Text2SQLExample(
                    "local999", "sealed", "Held out", "sqlite", "test"
                ),
            ),
            manifest={},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _config(self, baseline: str) -> Path:
        variant = {
            "B0": "question_only",
            "B1": "simple_schema",
            "B2": "mschema",
            "B3": "fewshot_mschema",
            "B4": "fewshot_mschema",
            "B6": "linked_mschema",
            "B6R": "hybrid_linked_mschema",
        }[baseline]
        path = self.root / f"{baseline.lower()}.toml"
        path.write_text(
            "\n".join(
                [
                    "schema_version = 1",
                    f'experiment_id = "fixture-{baseline.lower()}"',
                    f'baseline = "{baseline}"',
                    'split = "development"',
                    'reasoning_effort = "low"',
                    f'prompt_variant = "{variant}"',
                    'provider = "groq"',
                    *(
                        [
                            "mschema_examples_per_column = 3",
                            "mschema_max_text_length = 50",
                            "mschema_scan_rows_per_column = 24",
                        ]
                        if baseline in {"B2", "B3", "B4", "B6", "B6R"} else []
                    ),
                    *(
                        [
                            'retrieval_index_id = "fixture-index"',
                            f'retrieval_index_sha256 = "{"a" * 64}"',
                            f'retrieval_manifest_sha256 = "{"b" * 64}"',
                            (
                                'retrieval_strategy = "random-fixed-v1"'
                                if baseline == "B3"
                                else 'retrieval_strategy = "tfidf-cosine-v1"'
                            ),
                            "retrieval_k = 2",
                            *(
                                ["retrieval_seed = 42"]
                                if baseline == "B3"
                                else []
                            ),
                        ]
                        if baseline in {"B3", "B4"} else []
                    ),
                    *(
                        [
                            'schema_linker_version = "extractive-lexical-v1"',
                            "schema_link_max_tables = 4",
                            "schema_link_max_columns_per_table = 12",
                            "schema_link_minimum_columns_per_table = 4",
                            "schema_link_min_score = 4",
                            "schema_link_include_value_matches = true",
                            "schema_link_include_foreign_key_closure = true",
                            'schema_link_fallback_mode = "full_schema"',
                        ]
                        if baseline in {"B6", "B6R"} else []
                    ),
                    *(
                        [
                            "schema_link_include_all_selected_table_columns = true"
                        ]
                        if baseline == "B6R" else []
                    ),
                    f'model_id = "{MODEL_ID}"',
                    "temperature = 0.0",
                    "max_tokens = 1024",
                    "seed = 42",
                    "max_retries = 2",
                    "timeout_seconds = 60.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _runner(self, baseline: str) -> tuple[BaselineExperimentRunner, CountingProvider]:
        provider = CountingProvider()
        retrieval_index = (
            LoadedRetrievalIndex(
                entries=(
                    RetrievalIndexEntry(
                        "spider1-train-00000",
                        0,
                        "school",
                        "List student names",
                        "SELECT name FROM students",
                        ("list", "student", "names"),
                    ),
                    RetrievalIndexEntry(
                        "spider1-train-00001",
                        1,
                        "library",
                        "Count available books",
                        "SELECT count(*) FROM books",
                        ("count", "available", "books"),
                    ),
                ),
                manifest={
                    "index_id": "fixture-index",
                    "artifact": {"sha256": "a" * 64},
                },
                manifest_sha256="b" * 64,
            )
            if baseline in {"B3", "B4"}
            else None
        )
        return (
            BaselineExperimentRunner(
                config=load_baseline_config(self._config(baseline)),
                dataset=self.dataset,
                pipeline=Text2SQLPipeline(provider),
                evaluator=FakeEvaluator(self.resolver),
                retrieval_index=retrieval_index,
            ),
            provider,
        )

    def test_b0_prompt_contains_no_schema_and_never_touches_test(self) -> None:
        runner, provider = self._runner("B0")
        result = runner.run(self.root / "b0.jsonl", self.root / "b0-report.json")

        self.assertEqual(len(provider.inputs), 2)
        self.assertTrue(all("Schema:" not in item.prompt for item in provider.inputs))
        self.assertEqual(result["generation_summary"]["total"], 2)
        ids = {
            json.loads(line)["example_id"]
            for line in (self.root / "b0.jsonl").read_text().splitlines()
        }
        self.assertEqual(ids, {"local001", "local002"})

    def test_b1_prompt_contains_full_schema(self) -> None:
        runner, provider = self._runner("B1")
        runner.run(self.root / "b1.jsonl", self.root / "b1-report.json")

        self.assertTrue(all("Schema:" in item.prompt for item in provider.inputs))
        self.assertTrue(all("customers" in item.prompt for item in provider.inputs))

    def test_b2_prompt_contains_mschema_and_sample_values(self) -> None:
        runner, provider = self._runner("B2")
        result = runner.run(self.root / "b2.jsonl", self.root / "b2-report.json")

        self.assertEqual(len(provider.inputs), 2)
        self.assertTrue(
            all("〖DB_ID〗 fixture" in item.prompt for item in provider.inputs)
        )
        self.assertTrue(
            all("# Table: customers" in item.prompt for item in provider.inputs)
        )
        self.assertTrue(
            all('Examples: ["Alice"]' in item.prompt for item in provider.inputs)
        )
        self.assertEqual(
            result["experiment"]["mschema_sample_policy"],
            {
                "examples_per_column": 3,
                "max_text_length": 50,
                "scan_rows_per_column": 24,
            },
        )
        checkpoint = [
            json.loads(line)
            for line in (self.root / "b2.jsonl").read_text().splitlines()
        ]
        self.assertTrue(
            all(
                item["generation"]["metadata"]["schema_representation"]
                == "xiyan-compatible-v1"
                for item in checkpoint
            )
        )

    def test_b6_prompt_and_report_contain_linking_audit(self) -> None:
        runner, provider = self._runner("B6")
        result = runner.run(
            self.root / "b6.jsonl",
            self.root / "b6-report.json",
        )

        self.assertEqual(len(provider.inputs), 2)
        self.assertTrue(
            all("Linked M-Schema:" in item.prompt for item in provider.inputs)
        )
        self.assertEqual(
            result["experiment"]["schema_linking_policy"]["version"],
            "extractive-lexical-v1",
        )
        summary = result["generation_summary"]["schema_linking"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["fallback_count"], 0)
        checkpoint = [
            json.loads(line)
            for line in (self.root / "b6.jsonl").read_text().splitlines()
        ]
        self.assertTrue(
            all(
                item["generation"]["metadata"]["schema_linking"]["version"]
                == "extractive-lexical-v1"
                for item in checkpoint
            )
        )

    def test_b3_and_b4_record_retrieval_audit(self) -> None:
        for baseline, strategy in (
            ("B3", "random-fixed-v1"),
            ("B4", "tfidf-cosine-v1"),
        ):
            with self.subTest(baseline=baseline):
                runner, provider = self._runner(baseline)
                result = runner.run(
                    self.root / f"{baseline.lower()}.jsonl",
                    self.root / f"{baseline.lower()}-report.json",
                )
                self.assertEqual(len(provider.inputs), 2)
                self.assertTrue(
                    all(
                        "Training demonstrations:" in item.prompt
                        and "Target M-Schema:" in item.prompt
                        for item in provider.inputs
                    )
                )
                policy = result["experiment"]["retrieval_policy"]
                self.assertEqual(policy["strategy"], strategy)
                self.assertEqual(policy["k"], 2)
                summary = result["generation_summary"]["retrieval"]
                self.assertEqual(summary["targets"], 2)
                self.assertEqual(summary["selections"], 4)
                checkpoint = [
                    json.loads(line)
                    for line in (
                        self.root / f"{baseline.lower()}.jsonl"
                    ).read_text().splitlines()
                ]
                self.assertTrue(
                    all(
                        row["generation"]["metadata"]["retrieval"]["k"] == 2
                        for row in checkpoint
                    )
                )

    def test_provider_free_retrieval_audit_covers_only_development(self) -> None:
        for baseline, strategy in (
            ("B3", "random-fixed-v1"),
            ("B4", "tfidf-cosine-v1"),
        ):
            with self.subTest(baseline=baseline):
                runner, provider = self._runner(baseline)
                self.assertIsNotNone(runner.retrieval_index)
                payload = audit_development_retrieval(
                    config=runner.config,
                    dataset=runner.dataset,
                    retrieval_index=runner.retrieval_index,
                )

                self.assertEqual(provider.inputs, [])
                self.assertEqual(payload["scope"], "development")
                self.assertEqual(
                    payload["retrieval_policy"]["strategy"], strategy
                )
                self.assertEqual(payload["summary"]["targets"], 2)
                self.assertEqual(payload["summary"]["selections"], 4)
                self.assertEqual(
                    {row["target_example_id"] for row in payload["records"]},
                    {"local001", "local002"},
                )
                self.assertNotIn(
                    "local999",
                    {row["target_example_id"] for row in payload["records"]},
                )

    def test_b3_checkpoint_rejects_wrong_retrieval_index_id(self) -> None:
        runner, _ = self._runner("B3")
        predictions = self.root / "b3-checkpoint.jsonl"
        runner.run(predictions, self.root / "b3-checkpoint-report.json")
        rows = [
            json.loads(line) for line in predictions.read_text().splitlines()
        ]
        rows[0]["generation"]["metadata"]["retrieval"]["index_id"] = (
            "wrong-index"
        )
        predictions.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        resumed, _ = self._runner("B3")
        with self.assertRaises(ExperimentRunError) as raised:
            resumed.run(predictions, self.root / "resume-report.json")
        self.assertEqual(
            raised.exception.code, "checkpoint_retrieval_mismatch"
        )

    def test_b6r_prompt_and_report_preserve_recall_context(self) -> None:
        runner, provider = self._runner("B6R")
        result = runner.run(
            self.root / "b6r.jsonl",
            self.root / "b6r-report.json",
        )

        self.assertEqual(len(provider.inputs), 2)
        self.assertTrue(
            all(
                "Complete compact schema" in item.prompt
                and "Linked detailed M-Schema" in item.prompt
                and "do not use QUALIFY" in item.prompt
                for item in provider.inputs
            )
        )
        policy = result["experiment"]["schema_linking_policy"]
        self.assertTrue(
            policy["include_all_selected_table_columns"]
        )
        self.assertEqual(
            result["generation_summary"]["schema_linking"]["total"],
            2,
        )
        checkpoint = [
            json.loads(line)
            for line in (self.root / "b6r.jsonl").read_text().splitlines()
        ]
        self.assertTrue(
            all(
                item["generation"]["prompt_version"]
                == "exp004-recall-linked-mschema-v1"
                for item in checkpoint
            )
        )

    def test_b6r_config_requires_recall_column_policy(self) -> None:
        path = self._config("B6R")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "schema_link_include_all_selected_table_columns = true\n",
                "",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExperimentConfigurationError):
            load_baseline_config(path)

    def test_b6_config_requires_complete_linking_policy(self) -> None:
        path = self._config("B6")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "schema_link_max_tables = 4\n", ""
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExperimentConfigurationError):
            load_baseline_config(path)

    def test_completed_checkpoint_resumes_without_provider_calls(self) -> None:
        runner, provider = self._runner("B0")
        predictions = self.root / "resume.jsonl"
        report = self.root / "resume-report.json"
        first = runner.run(predictions, report)
        calls_after_first = len(provider.inputs)
        second = runner.run(predictions, report)

        self.assertEqual(calls_after_first, 2)
        self.assertEqual(len(provider.inputs), calls_after_first)
        self.assertEqual(first, second)
        self.assertEqual(len(predictions.read_text().splitlines()), 2)

    def test_checkpoint_rejects_test_or_unknown_id(self) -> None:
        runner, _ = self._runner("B0")
        config = runner.config
        path = self.root / "bad.jsonl"
        path.write_text(
            json.dumps(
                {
                    "experiment_id": config.experiment_id,
                    "config_sha256": config.config_sha256,
                    "example_id": "local999",
                    "db_id": "sealed",
                    "generated_sql": "SELECT 1",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ExperimentRunError) as raised:
            runner.run(path, self.root / "report.json")
        self.assertEqual(raised.exception.code, "checkpoint_coverage_mismatch")

    def test_b2_config_requires_sampling_limits(self) -> None:
        path = self._config("B2")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "mschema_max_text_length = 50\n", ""
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExperimentConfigurationError):
            load_baseline_config(path)

        path = self._config("B2")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "mschema_scan_rows_per_column = 24",
                "mschema_scan_rows_per_column = 0",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExperimentConfigurationError):
            load_baseline_config(path)

    def test_config_rejects_test_split(self) -> None:
        path = self._config("B0")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'split = "development"', 'split = "test"'
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExperimentConfigurationError):
            load_baseline_config(path)


if __name__ == "__main__":
    unittest.main()
