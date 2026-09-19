from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text2sql.finalization.statistics import (
    AnalysisError,
    ArmObservations,
    analyze_observations,
    bootstrap_mean_interval,
    exact_mcnemar_two_sided,
    holm_adjust,
    load_arm_report,
    write_analysis,
)


class FinalStatisticsTest(unittest.TestCase):
    def _observations(
        self, arm: str, values: tuple[int, ...]
    ) -> ArmObservations:
        ids = tuple(f"local{index:03d}" for index in range(len(values)))
        return ArmObservations(
            arm=arm,
            experiment_id=f"fixture-{arm}",
            report_path=f"{arm}.json",
            report_sha256=arm.casefold() * 32,
            example_ids=ids,
            correct=values,
            executable=tuple(1 for _ in values),
            input_tokens=100,
            output_tokens=20,
        )

    def test_bootstrap_is_seeded_and_paired_difference_is_reported(self) -> None:
        values = (0, 0, 1, 1)
        first = bootstrap_mean_interval(
            values, confidence_level=0.95, replicates=500, seed=42
        )
        second = bootstrap_mean_interval(
            values, confidence_level=0.95, replicates=500, seed=42
        )
        self.assertEqual(first, second)

        result = analyze_observations(
            {
                "B0": self._observations("B0", (0, 0, 1, 1)),
                "B1": self._observations("B1", (1, 0, 1, 1)),
            },
            comparisons=(("B0", "B1"),),
            confidence_level=0.95,
            replicates=500,
            seed=42,
            alpha=0.05,
        )
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["accuracy_difference"], 0.25)
        self.assertEqual(comparison["treatment_only_correct"], 1)
        self.assertEqual(comparison["control_only_correct"], 0)

    def test_exact_mcnemar_and_holm_match_known_values(self) -> None:
        result = exact_mcnemar_two_sided(
            (1, 1, 0, 0, 0, 0),
            (0, 0, 1, 1, 1, 1),
        )
        self.assertEqual(result["control_only_correct"], 2)
        self.assertEqual(result["treatment_only_correct"], 4)
        self.assertAlmostEqual(result["p_value"], 0.6875)

        adjusted = holm_adjust(
            {"a": 0.01, "b": 0.03, "c": 0.5}, alpha=0.05
        )
        self.assertAlmostEqual(adjusted["a"]["adjusted_p_value"], 0.03)
        self.assertAlmostEqual(adjusted["b"]["adjusted_p_value"], 0.06)
        self.assertFalse(adjusted["b"]["reject_at_familywise_alpha"])

    def test_report_loader_rejects_incomplete_coverage(self) -> None:
        report = {
            "experiment": {
                "baseline": "B0",
                "split": "test",
                "experiment_id": "final001-b0-test-gpt-oss-120b-v1",
            },
            "generation_summary": {"input_tokens": 1, "output_tokens": 1},
            "evaluation": {
                "split": "test",
                "total": 1,
                "evaluated": 1,
                "correct": 1,
                "incorrect": 0,
                "execution_errors": 0,
                "execution_accuracy": 1.0,
                "records": [{
                    "result": {
                        "example_id": "local001",
                        "correct": True,
                        "status": "correct",
                    }
                }],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(AnalysisError, "coverage mismatch"):
                load_arm_report(
                    path,
                    arm="B0",
                    expected_ids=("local001", "local002"),
                )

    def test_report_loader_keeps_execution_failures_in_denominator(self) -> None:
        records = [
            {"result": {
                "example_id": "local001",
                "correct": True,
                "status": "correct",
            }},
            {"result": {
                "example_id": "local002",
                "correct": False,
                "status": "generated_execution_error",
            }},
        ]
        report = {
            "experiment": {
                "baseline": "B0",
                "split": "test",
                "experiment_id": "final001-b0-test-gpt-oss-120b-v1",
            },
            "generation_summary": {"input_tokens": 2, "output_tokens": 2},
            "evaluation": {
                "split": "test",
                "expected_ids": ["local001", "local002"],
                "total": 2,
                "evaluated": 1,
                "correct": 1,
                "incorrect": 0,
                "execution_errors": 1,
                "comparison_errors": 0,
                "execution_accuracy": 0.5,
                "records": records,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            observations = load_arm_report(
                path,
                arm="B0",
                expected_ids=("local001", "local002"),
            )
        self.assertEqual(observations.correct, (1, 0))
        self.assertEqual(observations.executable, (1, 0))

    def test_analysis_outputs_are_immutable(self) -> None:
        analysis = {"arms": [{
            "arm": "B0",
            "total": 1,
            "correct": 1,
            "execution_accuracy": 1.0,
            "execution_accuracy_ci": {"lower": 1.0, "upper": 1.0},
            "executable": 1,
            "executable_query_rate": 1.0,
            "executable_query_rate_ci": {
                "lower": 1.0, "upper": 1.0
            },
            "input_tokens": 1,
            "output_tokens": 1,
            "report_sha256": "a" * 64,
        }]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "analysis.json"
            csv_path = root / "analysis.csv"
            write_analysis(
                analysis, json_path=json_path, csv_path=csv_path
            )
            with self.assertRaisesRegex(AnalysisError, "immutable"):
                write_analysis(
                    analysis, json_path=json_path, csv_path=csv_path
                )


if __name__ == "__main__":
    unittest.main()
