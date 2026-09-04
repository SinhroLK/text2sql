from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import dspy

from text2sql.evaluation import EvaluationResourceError, OfficialGoldResultStore
from text2sql.optimization import (
    B5ConfigurationError,
    B5ExecutionMetric,
    PreparedB5Dataset,
    PreparedB5Example,
    build_b5_recovery_identity,
    load_b5_optimization_config,
    load_verified_b5_program,
    optimize_b5,
    run_b5,
    validate_b5_runtime_dependencies,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs/optimization/dspy001-b5.toml"


class _FakeBatch:
    def to_dict(self) -> dict[str, object]:
        return {"total": 2, "correct": 2}


class _FakeEvaluator:
    def __init__(self) -> None:
        self.dataset = SimpleNamespace(manifest={"fixture": True})

    def evaluate_one(self, example_id: str, generated_sql: str) -> object:
        correct = generated_sql == "SELECT 1"
        return SimpleNamespace(
            result=SimpleNamespace(
                correct=correct,
                status="correct" if correct else "incorrect",
            )
        )

    def evaluate_batch(self, generated: dict[str, str], *, split: str) -> object:
        if split != "development" or set(generated) != {"local009", "local286"}:
            raise AssertionError("unexpected B5 evaluation scope")
        return _FakeBatch()

    def resource_manifest(self, *, split: str) -> dict[str, object]:
        return {"split": split, "fixture": True}


class _FakeOptimizer:
    def __init__(self, metric: B5ExecutionMetric) -> None:
        self.metric = metric
        self.compile_kwargs: dict[str, object] | None = None

    def compile(self, student: object, **kwargs: object) -> object:
        self.compile_kwargs = kwargs
        trainset = kwargs["trainset"]
        self.metric(trainset[0], dspy.Prediction(sql="SELECT 1"))
        return student


class B5OptimizationTest(unittest.TestCase):
    def _prepared(self) -> PreparedB5Dataset:
        def item(example_id: str, db_id: str) -> PreparedB5Example:
            prompt = f"Question for {example_id}"
            return PreparedB5Example(
                example_id=example_id,
                db_id=db_id,
                prompt=prompt,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                retrieval_audit={"selected": []},
                dspy_example=dspy.Example(
                    b4_context=prompt,
                    example_id=example_id,
                    db_id=db_id,
                ).with_inputs("b4_context"),
            )

        return PreparedB5Dataset(
            train=(item("local009", "Airlines"),),
            validation=(item("local286", "electronic_sales"),),
        )

    def test_frozen_config_uses_explicit_development_only_budget(self) -> None:
        config = load_b5_optimization_config(CONFIG_PATH)

        self.assertEqual(config.baseline, "B5")
        self.assertEqual(config.split, "development")
        self.assertEqual(config.optimizer, "MIPROv2")
        self.assertEqual(config.optimizer_auto, "none")
        self.assertEqual(config.dspy_version, "3.3.1")
        self.assertEqual(config.litellm_version, "1.99.0")
        self.assertEqual(config.optuna_version, "4.9.0")
        self.assertEqual(
            validate_b5_runtime_dependencies(config),
            {"dspy": "3.3.1", "litellm": "1.99.0", "optuna": "4.9.0"},
        )
        self.assertEqual(config.max_bootstrapped_demos, 0)
        self.assertEqual(config.max_labeled_demos, 0)
        self.assertTrue(config.program_aware_proposer)
        self.assertFalse(config.data_aware_proposer)
        self.assertTrue(config.tip_aware_proposer)
        self.assertFalse(config.fewshot_aware_proposer)
        self.assertFalse(config.minibatch)
        self.assertTrue(config.cache)
        self.assertEqual(config.cache_schema_version, 1)
        self.assertEqual(config.cache_size_limit_bytes, 1_073_741_824)
        self.assertEqual(config.cache_resume_max_age_hours, 72)
        self.assertFalse(
            set(config.train_database_ids) & set(config.validation_database_ids)
        )

    def test_runtime_dependency_mismatch_fails_preflight(self) -> None:
        config = load_b5_optimization_config(CONFIG_PATH)

        with self.assertRaisesRegex(
            B5ConfigurationError, "dependency mismatch for optuna"
        ):
            validate_b5_runtime_dependencies(
                replace(config, optuna_version="0.0.0")
            )


    def test_recovery_identity_hashes_string_source_paths(self) -> None:
        config = load_b5_optimization_config(CONFIG_PATH)

        identity = build_b5_recovery_identity(
            config=config,
            prepared=self._prepared(),
            evaluator=_FakeEvaluator(),
            runtime_dependencies=validate_b5_runtime_dependencies(config),
        )

        self.assertEqual(
            set(identity["source_sha256"]),
            {"b5", "rate_limit", "recovery", "evaluator"},
        )
        self.assertTrue(
            all(len(value) == 64 for value in identity["source_sha256"].values())
        )
    def test_overlapping_database_folds_are_rejected(self) -> None:
        original = CONFIG_PATH.read_text(encoding="utf-8")
        absolute_base = PROJECT_ROOT / "configs/experiments/exp006-b4.toml"
        altered = original.replace(
            'base_experiment_config = "../experiments/exp006-b4.toml"',
            f'base_experiment_config = "{absolute_base}"',
        ).replace(
            'validation_database_ids = ["electronic_sales", "f1"]',
            'validation_database_ids = ["Airlines", "f1"]',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.toml"
            path.write_text(altered, encoding="utf-8")
            with self.assertRaisesRegex(B5ConfigurationError, "overlap"):
                load_b5_optimization_config(path)

    def test_execution_metric_enforces_id_firewall_and_redacts_sql(self) -> None:
        metric = B5ExecutionMetric(
            _FakeEvaluator(), allowed_example_ids={"local009"}
        )

        score = metric(
            dspy.Example(example_id="local009"),
            dspy.Prediction(sql="SELECT 1"),
        )

        self.assertEqual(score, 1.0)
        self.assertNotIn("sql", metric.records[0])
        self.assertEqual(
            metric.records[0]["sql_sha256"],
            hashlib.sha256(b"SELECT 1").hexdigest(),
        )
        with self.assertRaisesRegex(ValueError, "firewall"):
            metric(
                dspy.Example(example_id="local999"),
                dspy.Prediction(sql="SELECT 1"),
            )

    def test_frozen_program_run_is_resumable_and_exact_coverage(self) -> None:
        config = replace(load_b5_optimization_config(CONFIG_PATH), cache=False)
        prepared = self._prepared()

        def factory(metric: B5ExecutionMetric, _lm: object, _config: object) -> object:
            return _FakeOptimizer(metric)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_path = root / "program.json"
            manifest_path = root / "manifest.json"
            predictions_path = root / "predictions.jsonl"
            report_path = root / "report.json"
            optimize_b5(
                config=config,
                prepared=prepared,
                evaluator=_FakeEvaluator(),
                program_path=program_path,
                manifest_path=manifest_path,
                optimizer_factory=factory,
                lm=dspy.utils.DummyLM([{"sql": "SELECT 1"}]),
            )
            report = run_b5(
                config=config,
                prepared=prepared,
                evaluator=_FakeEvaluator(),
                program_path=program_path,
                manifest_path=manifest_path,
                predictions_path=predictions_path,
                report_path=report_path,
                lm=dspy.utils.DummyLM(
                    [{"sql": "SELECT 1"}, {"sql": "SELECT 1"}]
                ),
            )
            first_checkpoint = predictions_path.read_bytes()
            resumed = run_b5(
                config=config,
                prepared=prepared,
                evaluator=_FakeEvaluator(),
                program_path=program_path,
                manifest_path=manifest_path,
                predictions_path=predictions_path,
                report_path=report_path,
                lm=dspy.utils.DummyLM([]),
            )

            self.assertEqual(report["evaluation"]["total"], 2)
            self.assertEqual(resumed["generation_summary"]["total"], 2)
            self.assertEqual(predictions_path.read_bytes(), first_checkpoint)
            self.assertEqual(len(first_checkpoint.splitlines()), 2)

    def test_compile_freezes_verified_json_without_gold_sql(self) -> None:
        config = replace(load_b5_optimization_config(CONFIG_PATH), cache=False)
        holder: dict[str, _FakeOptimizer] = {}

        def factory(metric: B5ExecutionMetric, _lm: object, _config: object) -> object:
            optimizer = _FakeOptimizer(metric)
            holder["optimizer"] = optimizer
            return optimizer

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_path = root / "program.json"
            manifest_path = root / "manifest.json"
            manifest = optimize_b5(
                config=config,
                prepared=self._prepared(),
                evaluator=_FakeEvaluator(),
                program_path=program_path,
                manifest_path=manifest_path,
                optimizer_factory=factory,
                lm=dspy.utils.DummyLM([{"sql": "SELECT 1"}]),
            )

            self.assertTrue(program_path.is_file())
            self.assertEqual(manifest["development_firewall"]["test_examples_used"], 0)
            self.assertFalse(manifest["development_firewall"]["spider2_gold_sql_used"])
            self.assertEqual(manifest["optimizer"]["num_trials"], 5)
            self.assertEqual(
                len(holder["optimizer"].compile_kwargs["trainset"]), 1
            )
            compile_kwargs = holder["optimizer"].compile_kwargs
            self.assertEqual(compile_kwargs["max_bootstrapped_demos"], 0)
            self.assertTrue(compile_kwargs["program_aware_proposer"])
            self.assertFalse(compile_kwargs["data_aware_proposer"])
            self.assertTrue(compile_kwargs["tip_aware_proposer"])
            self.assertFalse(compile_kwargs["fewshot_aware_proposer"])
            loaded, loaded_manifest = load_verified_b5_program(
                config=config,
                program_path=program_path,
                manifest_path=manifest_path,
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(
                loaded_manifest["program_artifact"]["sha256"],
                hashlib.sha256(program_path.read_bytes()).hexdigest(),
            )

            program_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(B5ConfigurationError, "identity"):
                load_verified_b5_program(
                    config=config,
                    program_path=program_path,
                    manifest_path=manifest_path,
                )


class GoldResultScopeTest(unittest.TestCase):
    def test_store_loads_only_allowed_development_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results"
            results.mkdir()
            metadata = root / "metadata.jsonl"
            metadata.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "instance_id": "local001",
                                "condition_cols": [],
                                "ignore_order": False,
                            }
                        ),
                        json.dumps(
                            {
                                "instance_id": "local999",
                                "condition_cols": [],
                                "ignore_order": False,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (results / "local001.csv").write_text("value\n1\n", encoding="utf-8")
            (results / "local999.csv").write_text("value\n9\n", encoding="utf-8")

            store = OfficialGoldResultStore.from_official_directory(
                results,
                metadata,
                allowed_example_ids={"local001"},
            )

            self.assertEqual(store.get("local001").example_id, "local001")
            with self.assertRaises(EvaluationResourceError):
                store.get("local999")


if __name__ == "__main__":
    unittest.main()
