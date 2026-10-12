"""Offline comparison; verifies frozen artifacts and compiles rejected SQL only."""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from text2sql.development.run import load_run, sha
from text2sql.development.controls import digest

root = Path.cwd()
out = Path(__file__).resolve().parent
runs = {}
for name in ("recovery-live001", "recovery-grounded002"):
    path = root / "artifacts/development" / name
    manifest = load_run(root, path)
    scores = json.loads((path / "scores.json").read_text())
    generation = json.loads((path / "generation.json").read_text())
    assert scores["generation_sha256"] == sha(path / "generation.json")
    assert scores["run_sha256"] == generation["run_sha256"] == manifest["identity_sha256"]
    assert generation["records_sha256"] == digest(generation["records"])
    assert generation["journal_sha256"] == sha(path / "requests.jsonl")
    assert [r["key"] for r in scores["records"]] == manifest["identity"]["expected_ids"]
    runs[name] = (manifest["identity"], scores, generation)
old, new = runs.values()
assert old[1]["execution_limits"] == new[1]["execution_limits"]
assert old[1]["comparator_sha256"] == new[1]["comparator_sha256"]
assert old[0]["gold_files"] == new[0]["gold_files"]
for field in ("arms", "model_id", "seed", "temperature"):
    assert old[0]["config"][field] == new[0]["config"][field]
old_records = {r["key"]: r for r in old[1]["records"]}
generated = {r["key"]: r for r in new[2]["records"]}
prompts = {r["example_id"]: r for r in new[0]["prompts"]}
comparisons = {}
for arm in ("C0", "C1", "C2"):
    rows = [r for r in new[1]["records"] if r["arm"] == arm]
    comparisons[arm] = {
        "old_correct": old[1]["summary"]["arms"][arm]["correct"],
        "new_correct": sum(r["correct"] for r in rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "gains": [r["example_id"] for r in rows if r["correct"] and not old_records[r["key"]]["correct"]],
        "losses": [r["example_id"] for r in rows if not r["correct"] and old_records[r["key"]]["correct"]],
    }
rejections = []
for r in new[1]["records"]:
    if r["status"] != "validation_rejected":
        continue
    item = {"key": r["key"], "issues": r["execution"]["validation"]["issue_codes"]}
    db = root / prompts[r["example_id"]]["database_path"]
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: 1, 1000000)
        try:
            conn.execute("EXPLAIN " + generated[r["key"]]["generated_sql"]).fetchone()
            item["sqlite_compile"] = "success"
        except sqlite3.Error as error:
            item.update(sqlite_compile="error", error=str(error))
    rejections.append(item)
failures = [{
    "key": r["key"],
    "error_code": r["metadata"].get("error_code"),
    "finish_reason": r["metadata"].get("finish_reason"),
    "output_tokens": r["output_tokens"],
    "reasoning_tokens": r["metadata"].get("completion_tokens_details", {}).get("reasoning_tokens"),
} for r in new[2]["records"] if r["status"] != "complete"]
audit = {
    "provider_calls": 0,
    "scope": "Saved scores unchanged; EXPLAIN only, no prediction execution or rescoring.",
    "source_hashes": {
        name: {f: sha(root / "artifacts/development" / name / f)
               for f in ("run.json", "generation.json", "scores.json", "requests.jsonl")}
        for name in runs
    },
    "comparisons": comparisons,
    "new_summary": new[1]["summary"],
    "generation_failures": failures,
    "validation_compile_checks": rejections,
    "c2_cases": [{
        "example_id": r["example_id"], "db_id": r["db_id"],
        "old_status": old_records[r["key"]]["status"], "new_status": r["status"],
    } for r in new[1]["records"] if r["arm"] == "C2"],
}
(out / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
print(json.dumps(comparisons, indent=2))
print("Verified both runs. Wrote offline audit; no source experiments changed.")
