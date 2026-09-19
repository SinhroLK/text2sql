from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import GenerationInput, Text2SQLExample
from text2sql.finalization.freeze import FINAL_ARMS
from text2sql.finalization.runner import (
    PROVIDER_FAILURE_SQL,
    FinalExperimentGenerator,
    load_final_config,
)
from text2sql.experiments import ExperimentRunError
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import GroqProviderError
from text2sql.providers.base import ProviderResponse
from text2sql.evaluation import Spider2SQLiteDatabaseResolver


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CountingProvider:
    provider_name = "groq"
    model_id = "openai/gpt-oss-120b"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        self.calls += 1
        if self.fail:
            raise GroqProviderError("sanitized fixture failure")
        return ProviderResponse(
            candidates=("SELECT value FROM items",),
            input_tokens=10,
            output_tokens=4,
        )


class FinalRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        database_root = self.root / "databases"
        database_root.mkdir()
        connection = sqlite3.connect(database_root / "fixture.sqlite")
        connection.executescript(
            "CREATE TABLE items(value TEXT);"
            "INSERT INTO items VALUES ('ok');"
        )
        connection.close()
        self.resolver = Spider2SQLiteDatabaseResolver(database_root)
        self.dataset = LoadedSpider2LiteDataset(
            examples=tuple(
                Text2SQLExample(
                    f"local{index:03d}",
                    "fixture",
                    f"Question {index}",
                    "sqlite",
                    "test",
                )
                for index in range(104)
            ),
            manifest={"fixture": True},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_frozen_final_configs_load_as_test_only(self) -> None:
        for arm in FINAL_ARMS:
            config = load_final_config(PROJECT_ROOT, arm)
            self.assertEqual(config.baseline, arm)
            self.assertEqual(config.split, "test")
            self.assertEqual(
                config.experiment_id,
                f"final001-{arm.casefold()}-test-gpt-oss-120b-v1",
            )

    def test_generation_checkpoint_resumes_without_repeating_attempts(self) -> None:
        provider = CountingProvider()
        config = load_final_config(PROJECT_ROOT, "B0")
        runner = FinalExperimentGenerator(
            config=config,
            dataset=self.dataset,
            pipeline=Text2SQLPipeline(provider),
            database_resolver=self.resolver,
        )
        path = self.root / "predictions.jsonl"
        first = runner.generate(path)
        second = runner.generate(path)

        self.assertEqual(len(first), 104)
        self.assertEqual(set(second), set(first))
        self.assertEqual(
            {key: item["generated_sql"] for key, item in second.items()},
            {key: item["generated_sql"] for key, item in first.items()},
        )
        self.assertEqual(provider.calls, 104)
        self.assertEqual(len(path.read_text().splitlines()), 104)

    def test_ambiguous_interrupted_attempt_is_not_reissued(self) -> None:
        provider = CountingProvider()
        config = load_final_config(PROJECT_ROOT, "B0")
        path = self.root / "interrupted.jsonl"
        pending = path.with_name(f".{path.name}.pending.json")
        pending.write_text(
            json.dumps({
                "schema_version": 1,
                "experiment_id": config.experiment_id,
                "config_sha256": config.config_sha256,
                "example_id": "local000",
            }),
            encoding="utf-8",
        )
        checkpoint = FinalExperimentGenerator(
            config=config,
            dataset=self.dataset,
            pipeline=Text2SQLPipeline(provider),
            database_resolver=self.resolver,
        ).generate(path)

        self.assertEqual(provider.calls, 103)
        self.assertFalse(pending.exists())
        self.assertEqual(
            checkpoint["local000"]["generation"]["error_category"],
            "ambiguous_interrupted_provider_attempt",
        )

    def test_terminal_provider_failures_are_retained_once(self) -> None:
        provider = CountingProvider(fail=True)
        config = load_final_config(PROJECT_ROOT, "B0")
        events = []
        runner = FinalExperimentGenerator(
            config=config,
            dataset=self.dataset,
            pipeline=Text2SQLPipeline(provider),
            database_resolver=self.resolver,
            progress=lambda *event: events.append(event),
        )
        path = self.root / "failures.jsonl"
        with self.assertRaisesRegex(
            ExperimentRunError, "failure was retained"
        ):
            runner.generate(path)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(events[0][2], "terminal_provider_error")
        replacement = CountingProvider()
        resumed = FinalExperimentGenerator(
            config=config,
            dataset=self.dataset,
            pipeline=Text2SQLPipeline(replacement),
            database_resolver=self.resolver,
        ).generate(path)
        self.assertEqual(replacement.calls, 103)
        self.assertEqual(len(resumed), 104)
        failed = resumed["local000"]
        self.assertEqual(failed["generated_sql"], PROVIDER_FAILURE_SQL)
        self.assertEqual(
            failed["generation"]["error_category"],
            "terminal_provider_error",
        )


if __name__ == "__main__":
    unittest.main()
