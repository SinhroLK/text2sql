from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import GenerationInput, Text2SQLExample
from text2sql.evaluation import Spider2SQLiteDatabaseResolver
from text2sql.experiments import ExperimentRunError
from text2sql.finalization.freeze_v2 import FINAL_ARMS
from text2sql.finalization.runner_v2 import (
    FinalExperimentGenerator,
    load_final_config,
)
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import GroqProviderError
from text2sql.providers.base import ProviderResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SequenceProvider:
    provider_name = "groq"
    model_id = "openai/gpt-oss-120b"

    def __init__(self, errors: list[GroqProviderError] | None = None) -> None:
        self.calls = 0
        self.errors = list(errors or ())

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return ProviderResponse(
            candidates=("SELECT value FROM items",),
            input_tokens=10,
            output_tokens=4,
        )


class FinalRunnerV2Test(unittest.TestCase):
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

    def _runner(
        self,
        provider: SequenceProvider,
        *,
        sleeps: list[float] | None = None,
        operational_events: list[dict] | None = None,
    ) -> FinalExperimentGenerator:
        return FinalExperimentGenerator(
            config=load_final_config(PROJECT_ROOT, "B0"),
            dataset=self.dataset,
            pipeline=Text2SQLPipeline(provider),
            database_resolver=self.resolver,
            transient_retry_seconds=60,
            sleep=(sleeps if sleeps is not None else []).append,
            operational_progress=(
                (operational_events if operational_events is not None else []).append
            ),
        )

    def test_all_v2_configs_are_distinct_test_only_configs(self) -> None:
        for arm in FINAL_ARMS:
            config = load_final_config(PROJECT_ROOT, arm)
            self.assertEqual(config.baseline, arm)
            self.assertEqual(config.split, "test")
            self.assertEqual(config.max_retries, 0)
            self.assertEqual(
                config.experiment_id,
                f"final002-{arm.casefold()}-test-gpt-oss-120b-v2",
            )

    def test_rate_limit_waits_and_retries_same_example(self) -> None:
        provider = SequenceProvider([
            GroqProviderError(
                "HTTP 429 sanitized",
                status_code=429,
                retry_after_seconds=12.5,
                token_reset="12.5s",
                retryable=True,
                category="rate_limit",
            )
        ])
        sleeps: list[float] = []
        events: list[dict] = []
        path = self.root / "predictions.jsonl"

        checkpoint = self._runner(
            provider, sleeps=sleeps, operational_events=events
        ).generate(path)

        self.assertEqual(provider.calls, 105)
        self.assertEqual(sleeps, [12.5])
        self.assertEqual(len(checkpoint), 104)
        self.assertTrue(all(
            item["generation"].get("error_category") is None
            for item in checkpoint.values()
        ))
        self.assertEqual(len(path.read_text().splitlines()), 104)
        self.assertEqual(events[0]["event"], "provider_retry_wait")
        self.assertEqual(events[0]["example_id"], "local000")
        self.assertEqual(events[0]["status_code"], 429)

    def test_nonretryable_error_stops_without_prediction(self) -> None:
        provider = SequenceProvider([
            GroqProviderError(
                "HTTP 401 sanitized",
                status_code=401,
                retryable=False,
                category="request_rejected",
            )
        ])
        path = self.root / "predictions.jsonl"

        with self.assertRaisesRegex(
            ExperimentRunError, "without recording a prediction"
        ):
            self._runner(provider).generate(path)

        self.assertEqual(provider.calls, 1)
        self.assertFalse(path.exists())
        self.assertFalse(
            path.with_name(f".{path.name}.pending.json").exists()
        )

    def test_interrupted_marker_is_retried_without_failure_record(self) -> None:
        provider = SequenceProvider()
        config = load_final_config(PROJECT_ROOT, "B0")
        path = self.root / "predictions.jsonl"
        pending = path.with_name(f".{path.name}.pending.json")
        pending.write_text(json.dumps({
            "schema_version": 1,
            "experiment_id": config.experiment_id,
            "config_sha256": config.config_sha256,
            "example_id": "local000",
        }))

        checkpoint = self._runner(provider).generate(path)

        self.assertEqual(provider.calls, 104)
        self.assertEqual(len(checkpoint), 104)
        self.assertIsNone(
            checkpoint["local000"]["generation"].get("error_category")
        )
        operational = path.with_name(
            f"{path.stem}-operational.jsonl"
        )
        event = json.loads(operational.read_text().splitlines()[0])
        self.assertEqual(event["event"], "interrupted_attempt_resumed")


if __name__ == "__main__":
    unittest.main()
