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
    load_baseline_config,
)
from text2sql.pipeline import Text2SQLPipeline
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
        variant = "question_only" if baseline == "B0" else "simple_schema"
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
        return (
            BaselineExperimentRunner(
                config=load_baseline_config(self._config(baseline)),
                dataset=self.dataset,
                pipeline=Text2SQLPipeline(provider),
                evaluator=FakeEvaluator(self.resolver),
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
