"""Analysis-only corpus from saved outcomes; never imported by generation."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

DEVELOPMENT = {"B1": "exp001-b1", "B2": "exp002-b2", "B4": "exp006-b4",
               "B5": "dspy001-b5", "B6R": "exp004-b6r"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pair_saved_records(predictions: list[dict], report: dict, expected_ids: list[str],
                       annotations: dict | None = None, questions: dict | None = None) -> list[dict]:
    expected = set(expected_ids)
    if len(expected) != len(expected_ids):
        raise ValueError("Duplicate expected diagnostic IDs")
    by_id = {p["example_id"]: p for p in predictions}
    results = {r["result"]["example_id"]: r["result"] for r in report["evaluation"]["records"]}
    if (len(by_id) != len(predictions) or set(by_id) != expected or set(results) != expected
            or len(results) != len(report["evaluation"]["records"])
            or set(report["evaluation"]["expected_ids"]) != expected):
        raise ValueError("Saved prediction/report coverage mismatch")
    records = []
    for example_id in sorted(expected):
        prediction, result = by_id[example_id], results[example_id]
        if prediction["db_id"] != result["db_id"]:
            raise ValueError("Saved database identity mismatch")
        generation = prediction.get("generation", {})
        question = generation.get("question") or (questions or {}).get(example_id)
        if not isinstance(question, str) or not question:
            raise ValueError("Saved record needs a pinned question source")
        provider = generation.get("metadata", {}).get("provider", {})
        labels = []
        message = (result.get("error_message") or "").lower()
        for fragment, label in (("no such column", "binding_missing_column"),
                                ("ambiguous column", "binding_ambiguous_column"),
                                ("no such function", "dialect_missing_function"),
                                ("syntax error", "syntax_error"),
                                ("incomplete input", "incomplete_sql"),
                                ("misuse of window", "window_function_misuse"),
                                ("interrupted", "execution_resource_interruption")):
            if fragment in message:
                labels.append({"label": label, "confidence": "observed",
                               "evidence": result["error_message"]})
        if provider.get("finish_reason") == "length":
            labels.append({"label": "truncation", "confidence": "observed",
                           "evidence": "saved finish_reason=length"})
        elif (provider.get("max_tokens") and
              generation.get("output_tokens", 0) >= provider["max_tokens"]):
            labels.append({"label": "completion_cap_hit", "confidence": "suspected",
                           "evidence": "token count reached cap; historical finish reason unavailable"})
        generated, reference = result.get("generated") or {}, result.get("reference") or {}
        if result.get("status") == "result_mismatch":
            labels.append({"label": "semantic_unclassified", "confidence": "unclassified",
                           "evidence": "saved execution succeeded but official comparison failed"})
            if generated.get("row_count") != reference.get("row_count"):
                labels.append({"label": "row_count_mismatch", "confidence": "observed",
                               "evidence": "output row counts differ; this does not identify the cause"})
        sql_hash = hashlib.sha256(prediction["generated_sql"].encode()).hexdigest()
        annotation = (annotations or {}).get(example_id)
        if annotation:
            if annotation["sql_sha256"] != sql_hash:
                raise ValueError("Semantic annotation refers to different SQL")
            labels.extend(annotation["findings"])
        records.append({"example_id": example_id, "db_id": prediction["db_id"],
                        "question": question, "generated_sql": prediction["generated_sql"],
                        "generation_audit_available": bool(generation),
                        "sql_sha256": sql_hash, "correct": result["correct"],
                        "status": result["status"], "labels": labels,
                        "generated_shape": [generated.get("row_count"), len(generated.get("columns", []))],
                        "reference_shape": [reference.get("row_count"), len(reference.get("columns", []))]})
    if sum(bool(r["correct"]) for r in records) != report["evaluation"]["correct"]:
        raise ValueError("Saved summary differs from its individual outcomes")
    return records


def build(root: Path) -> dict:
    protocol_path = root / "configs/development/recovery-development-v2.json"
    annotation_path = root / "configs/development/semantic-annotations-v1.json"
    protocol = json.loads(protocol_path.read_text())
    annotations = json.loads(annotation_path.read_text())
    resources = {str(p.relative_to(root)): sha(p) for p in (protocol_path, annotation_path)}
    development, reviewed = [], []
    questions = {p["example_id"]: p["generation"]["question"] for p in map(json.loads,
                 (root / "artifacts/experiments/exp001-b1-predictions.jsonl").read_text().splitlines())}
    inputs = [(arm, root / f"artifacts/experiments/{name}-predictions.jsonl",
               root / f"artifacts/reports/{name}-report.json", "development")
              for arm, name in DEVELOPMENT.items()]
    inputs.append(("B6R", root / "artifacts/final/run002-v2/predictions/final002-b6r-test-predictions.jsonl",
                   root / "artifacts/final/run002-v2/reports/final002-b6r-test-report.json", "reviewed_test"))
    for arm, predictions_path, report_path, split in inputs:
        predictions = [json.loads(line) for line in predictions_path.read_text().splitlines()]
        report = json.loads(report_path.read_text())
        ids = protocol["development_example_ids" if split == "development" else "reviewed_test_example_ids"]
        notes = annotations["development_b6r"] if split == "development" and arm == "B6R" else {}
        records = pair_saved_records(predictions, report, ids, notes, questions)
        destination = development if split == "development" else reviewed
        destination.extend({**r, "arm": arm, "split": split} for r in records)
        resources.update({str(p.relative_to(root)): sha(p) for p in (predictions_path, report_path)})
    paired = [{"example_id": example_id,
               "correct_by_arm": {r["arm"]: r["correct"] for r in development
                                  if r["example_id"] == example_id}}
              for example_id in sorted(protocol["development_example_ids"])]
    return {"analysis_only": True, "provider_calls": 0, "prediction_sql_executions": 0,
            "limitation": "Saved outcomes, not fresh executions. Suspected labels are not proven causes. Never supply this corpus to generation.",
            "resource_hashes": resources, "development": development,
            "reviewed_test_diagnostics": reviewed, "paired_development": paired,
            "development_labels": dict(Counter(x["label"] for r in development for x in r["labels"])),
            "development_scores": {arm: sum(r["correct"] for r in development if r["arm"] == arm)
                                   for arm in DEVELOPMENT}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Choose a new output directory")
    corpus = build(args.project_root)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for key in ("development", "reviewed_test_diagnostics"):
        rows = corpus.pop(key)
        (args.output_dir / f"{key}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        corpus[key + "_records"] = len(rows)
    (args.output_dir / "summary.json").write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: corpus[key] for key in ("development_records", "reviewed_test_diagnostics_records", "development_scores")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
