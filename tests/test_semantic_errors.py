from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from text2sql.analysis import (
    SemanticErrorAnalysisError,
    load_semantic_error_spec,
    run_semantic_error_analysis,
    write_semantic_error_artifacts,
)


class SemanticErrorAnalysisTest(unittest.TestCase):
    def _write_inputs(self, root: Path) -> Path:
        example_ids = ["dev001", "dev002", "dev003", "dev004"]
        categories = ["output_shape", "filter_or_literal", "table_or_column"]
        arms: dict[str, object] = {}
        for baseline in ("B1", "B6R", "B4", "B5"):
            prediction_path = root / f"{baseline}-predictions.jsonl"
            prediction_records = []
            report_records = []
            for example_id in example_ids:
                correct = example_id == "dev004" or (
                    example_id == "dev002" and baseline == "B1"
                )
                prediction = {
                    "baseline": baseline,
                    "example_id": example_id,
                    "db_id": "fixture",
                    "generated_sql": f"SELECT '{baseline}-{example_id}'",
                }
                if baseline != "B5":
                    prediction["generation"] = {
                        "question": f"Question {example_id}"
                    }
                prediction_records.append(prediction)
                status = "correct" if correct else "result_mismatch"
                report_records.append(
                    {
                        "result": {
                            "example_id": example_id,
                            "db_id": "fixture",
                            "correct": correct,
                            "status": status,
                            "error_category": (
                                None if correct else "result_mismatch"
                            ),
                            "error_message": (
                                None if correct else "Fixture mismatch"
                            ),
                            "generated": {
                                "columns": ["value"],
                                "row_count": 1,
                            },
                            "reference": {
                                "columns": ["value"],
                                "row_count": 1,
                            },
                        }
                    }
                )
            prediction_path.write_text(
                "\n".join(
                    json.dumps(record, sort_keys=True)
                    for record in prediction_records
                )
                + "\n",
                encoding="utf-8",
            )
            report_path = root / f"{baseline}-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "experiment": {"baseline": baseline},
                        "evaluation": {
                            "split": "development",
                            "expected_ids": example_ids,
                            "total": len(example_ids),
                            "records": report_records,
                        },
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            arms[baseline] = {
                "baseline": baseline,
                "predictions": {
                    "path": prediction_path.name,
                    "sha256": hashlib.sha256(
                        prediction_path.read_bytes()
                    ).hexdigest(),
                },
                "report": {
                    "path": report_path.name,
                    "sha256": hashlib.sha256(
                        report_path.read_bytes()
                    ).hexdigest(),
                },
            }

        config_path = root / "sem001.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "analysis_id": "sem001-fixture-v1",
                    "scope": "development",
                    "arm_order": ["B1", "B6R", "B4", "B5"],
                    "target_arm": "B5",
                    "categories": categories,
                    "expected_example_ids": example_ids,
                    "arms": arms,
                    "labels": {
                        "dev001": {
                            "primary": "output_shape",
                            "secondary": [],
                            "rationale": "Fixture output shape mismatch.",
                        },
                        "dev002": {
                            "primary": "filter_or_literal",
                            "secondary": [],
                            "rationale": "Fixture filter mismatch.",
                        },
                        "dev003": {
                            "primary": "table_or_column",
                            "secondary": [],
                            "rationale": "Fixture identifier mismatch.",
                        },
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def test_builds_exact_paired_corpus_and_checksums_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = load_semantic_error_spec(self._write_inputs(root))
            analysis = run_semantic_error_analysis(spec, root)

            self.assertEqual(analysis.summary["total_examples"], 4)
            self.assertEqual(analysis.summary["target_failures"], 3)
            self.assertEqual(analysis.summary["provider_calls"], 0)
            self.assertEqual(analysis.summary["test_examples_used"], 0)
            self.assertEqual(
                analysis.summary["behavior_counts"],
                {
                    "prompt_sensitive": 1,
                    "stable_correct": 1,
                    "stable_failure": 2,
                },
            )
            self.assertEqual(
                [item["category"] for item in analysis.summary[
                    "dominant_primary_categories"
                ]],
                ["filter_or_literal", "output_shape", "table_or_column"],
            )

            jsonl = root / "corpus.jsonl"
            markdown = root / "corpus.md"
            manifest = root / "manifest.json"
            result = write_semantic_error_artifacts(
                analysis,
                jsonl_path=jsonl,
                markdown_path=markdown,
                manifest_path=manifest,
            )
            self.assertEqual(len(jsonl.read_text().splitlines()), 4)
            self.assertIn("Paired 4-example matrix", markdown.read_text())
            self.assertEqual(
                result["artifacts"]["jsonl"]["sha256"],
                hashlib.sha256(jsonl.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                result["artifacts"]["markdown"]["sha256"],
                hashlib.sha256(markdown.read_bytes()).hexdigest(),
            )

    def test_rejects_changed_checksum_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = load_semantic_error_spec(self._write_inputs(root))
            (root / "B5-predictions.jsonl").write_text(
                "not-json\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                SemanticErrorAnalysisError, "checksum mismatch"
            ):
                run_semantic_error_analysis(spec, root)

    def test_rejects_non_development_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self._write_inputs(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["scope"] = "test"
            config_path.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                SemanticErrorAnalysisError, "development scope"
            ):
                load_semantic_error_spec(config_path)


if __name__ == "__main__":
    unittest.main()
