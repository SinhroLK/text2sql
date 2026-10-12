from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from text2sql.development.controls import Limits, RequestJournal, digest
from text2sql.development.run import (environment, generate_records, generate_run, load_run,
                                     main, sha, validate_generation, write_json)
from text2sql.development.scoring import official_comparator, score_record, score_run, summarize
from text2sql.domain import GenerationInput
from text2sql.evaluation import OfficialGoldResultStore
from text2sql.execution import SandboxPolicy, SQLiteSandboxExecutor
from text2sql.providers import GroqProviderError
from text2sql.providers.base import ProviderResponse
from text2sql.schema import inspect_sqlite_schema

ROOT = Path(__file__).resolve().parents[1]


class FixtureProvider:
    provider_name = "fixture"
    model_id = "fixture"
    max_retries = 0
    max_tokens = 50

    def __init__(self, outcomes=None):
        self.calls = 0
        self.outcomes = list(outcomes or [])

    def generate(self, request):
        self.calls += 1
        if self.outcomes:
            value = self.outcomes.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return ProviderResponse(("SELECT name FROM customers ORDER BY id",), 10, 10,
                                {"finish_reason": "stop", "completion_complete": True})


class RecoveryRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "run"
        self.directory.mkdir()
        self.database = self.root / "fixture.sqlite"
        with sqlite3.connect(self.database) as conn:
            conn.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);"
                               "INSERT INTO customers VALUES(1,'Alice'),(2,'Bob');")
        self.schema = inspect_sqlite_schema(self.database)
        self.prompts = [{"example_id": f"local00{i}", "db_id": "fixture", "question": "Names",
                         "prompt": f"Names {i}", "prompt_sha256": hashlib.sha256(f"Names {i}".encode()).hexdigest(),
                         "input_token_reservation": 50, "database_path": "fixture.sqlite"} for i in (1, 2)]
        self.arms = [{"arm": a, "max_completion_tokens": 50, "reasoning_effort": "low"} for a in ("C0", "C1", "C2")]
        self.limits = Limits(12, 2000, 1000, 2000, 2, "1", "1", "2")
        self.identity = {"runner_version": "recovery-free-run-v1", "mock": False,
                         "config": {"arms": self.arms, "execution_limits": asdict(SandboxPolicy())},
                         "prompts": self.prompts, "limits": asdict(self.limits),
                         "environment": environment(), "resources": {"fixture.sqlite": sha(self.database)},
                         "expected_ids": [a["arm"] + ":" + p["example_id"] for a in self.arms for p in self.prompts],
                         "target_families": [{"family_id": "fixture-family", "databases": ["fixture"]}]}
        self.now = [100.0]
        self.provider = FixtureProvider()

    def journal(self, identity=None, limits=None):
        return RequestJournal(self.directory / "requests.jsonl", identity or {"expected_ids": self.identity["expected_ids"]},
                              limits or self.limits, clock=lambda: self.now[0])

    def generate(self, journal, **options):
        return generate_records(self.identity, journal,
                lambda p, provider: GenerationInput(p["question"], p["prompt"], self.schema, provider.model_id),
                lambda arm: self.provider, allow_api=True, free_plan_confirmed=True,
                clock=lambda: self.now[0], sleep=lambda seconds: self.now.__setitem__(0, self.now[0] + seconds),
                progress=lambda event: None, **options)

    def test_all_arms_replay_without_reissuing(self):
        with self.journal() as journal:
            records = self.generate(journal)
            self.assertEqual(len(records), 6)
        with self.journal() as journal:
            self.assertEqual(records, self.generate(journal))
        self.assertEqual(self.provider.calls, 6)

    def test_quota_amendment_is_scoped_and_preserves_journal_prefix(self):
        originals = {
            "src/text2sql/development/controls.py": "1d5fd052c6fba4a7d41f45f22cfde9c8c3f9ab811fa61547196294a9bd43d2d4",
            "src/text2sql/development/run.py": "ce5ea3a6bbce5bb9dd3fc82e9b7ddd1749455fdee90d8c1da479c3c622ed42cb",
        }
        self.identity["resources"].update(originals)
        patched = {}
        for name in originals:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("patched fixture")
            patched[name] = sha(path)
        manifest = {"identity": self.identity, "identity_sha256": digest(self.identity)}
        write_json(self.directory / "run.json", manifest)
        journal_path = self.directory / "requests.jsonl"
        journal_path.write_bytes(b"preserved prefix\n")
        details = {"policy": "reported-daily-usage-v1", "run_sha256": manifest["identity_sha256"],
                   "original_sources": originals, "patched_sources": patched,
                   "journal_prefix_bytes": journal_path.stat().st_size,
                   "journal_prefix_sha256": sha(journal_path)}
        def save():
            write_json(self.directory / "quota-accounting-amendment.json",
                       {"details": details, "sha256": digest(details)})
        save()
        self.assertEqual(load_run(self.root, self.directory), manifest)
        journal_path.write_bytes(b"preserved prefix\nnew appended record\n")
        self.assertEqual(load_run(self.root, self.directory), manifest)
        journal_path.write_bytes(b"changed prefix\n")
        with self.assertRaisesRegex(ValueError, "prefix changed"):
            load_run(self.root, self.directory)
        journal_path.write_bytes(b"preserved prefix\n")
        details["patched_sources"]["fixture.sqlite"] = sha(self.database)
        save()
        with self.assertRaisesRegex(ValueError, "Invalid quota"):
            load_run(self.root, self.directory)
        del details["patched_sources"]["fixture.sqlite"]
        save()
        self.database.write_bytes(b"changed database")
        with self.assertRaisesRegex(ValueError, "resource changed"):
            load_run(self.root, self.directory)

    def test_quota_exit_and_resume_preserve_completed_calls(self):
        limits = Limits(12, 2000, 100, 2000, 2, "1", "1", "2")
        with self.journal(limits=limits) as journal:
            self.assertIsNone(self.generate(journal))
        self.assertEqual(self.provider.calls, 1)
        with self.journal(limits=limits) as journal:
            records = self.generate(journal, wait=True)
        self.assertEqual(len(records), 6)
        self.assertEqual(self.provider.calls, 6)

    def test_429_waits_then_retries_once(self):
        self.provider.outcomes = [GroqProviderError("limited", status_code=429, retry_after_seconds=80)]
        with self.journal() as journal:
            self.assertEqual(len(self.generate(journal, wait=True)), 6)
        self.assertEqual(self.provider.calls, 7)
        self.assertGreaterEqual(self.now[0], 180)

    def test_interrupted_call_becomes_failure_without_reissue(self):
        self.provider.outcomes = [KeyboardInterrupt()]
        with self.journal() as journal, self.assertRaises(KeyboardInterrupt):
            self.generate(journal)
        with self.journal() as journal:
            records = self.generate(journal)
        self.assertEqual(records[0]["status"], "interrupted")
        self.assertIsNone(records[0]["generated_sql"])
        self.assertEqual(self.provider.calls, 6)

    def test_bad_credentials_stop_then_resume_retains_failed_case(self):
        self.provider.outcomes = [GroqProviderError("bad key", status_code=401)]
        with self.journal() as journal, self.assertRaisesRegex(ValueError, "credentials"):
            self.generate(journal)
        self.assertEqual(self.provider.calls, 1)
        with self.journal() as journal:
            records = self.generate(journal)
        self.assertIsNone(records[0]["generated_sql"])
        self.assertEqual(self.provider.calls, 6)

    def test_live_flags_are_required_before_provider_invocation(self):
        manifest = {"identity": self.identity, "identity_sha256": digest(self.identity)}
        write_json(self.directory / "run.json", manifest)
        with self.assertRaisesRegex(ValueError, "allow-api"):
            generate_run(self.root, self.directory)
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
            generate_run(self.root, self.directory, allow_api=True, free_plan_confirmed=True)
        self.assertFalse((self.directory / "requests.jsonl").exists())

    def test_resource_drift_is_rejected(self):
        write_json(self.directory / "run.json", {"identity": self.identity, "identity_sha256": digest(self.identity)})
        self.assertEqual(load_run(self.root, self.directory)["identity"], self.identity)
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO customers VALUES(3,'Changed')")
        with self.assertRaisesRegex(ValueError, "resource changed"):
            load_run(self.root, self.directory)

    def test_prediction_edit_cannot_be_scored_even_with_recomputed_digest(self):
        manifest = {"identity": self.identity, "identity_sha256": digest(self.identity)}
        with self.journal() as journal:
            records = self.generate(journal)
            payload = {"run_sha256": manifest["identity_sha256"], "journal_sha256": sha(self.directory / "requests.jsonl"),
                       "mock": False, "records": records, "records_sha256": digest(records)}
            write_json(self.directory / "generation.json", payload)
            validate_generation(self.directory, manifest, journal)
            for field, value in (("generated_sql", "SELECT 'gold answer'"), ("example_id", "local999")):
                bad = deepcopy(payload)
                bad["records"][0][field] = value
                bad["records_sha256"] = digest(bad["records"])
                write_json(self.directory / "generation.json", bad)
                with self.assertRaises(ValueError):
                    validate_generation(self.directory, manifest, journal)

    def gold(self):
        directory = self.root / "gold"
        directory.mkdir(exist_ok=True)
        (directory / "local001.csv").write_text("name\nAlice\nBob\n")
        metadata = self.root / "metadata.jsonl"
        metadata.write_text(json.dumps({"instance_id": "local001", "condition_cols": [], "ignore_order": True}) + "\n")
        return OfficialGoldResultStore.from_official_directory(directory, metadata).get("local001")

    def comparator(self):
        source = ROOT / "data/raw/spider2/spider2-lite/evaluation_suite/evaluate_utils.py"
        return official_comparator(source, sha(source))

    def test_real_sqlite_fixture_scores_with_pinned_official_function(self):
        record = {"key": "C0:local001", "arm": "C0", "example_id": "local001", "db_id": "fixture",
                  "status": "complete", "generated_sql": "SELECT name FROM customers ORDER BY id DESC"}
        result = score_record(record, self.database, self.gold(), SQLiteSandboxExecutor(), self.comparator())
        self.assertTrue(result["correct"])
        self.assertTrue(result["nonempty_correct"])
        record["generated_sql"] = "DELETE FROM customers"
        result = score_record(record, self.database, self.gold(), SQLiteSandboxExecutor(), self.comparator())
        self.assertFalse(result["correct"])
        self.assertNotEqual(result["status"], "result_mismatch")
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 2)

    def test_failed_generation_does_not_execute_sql_and_counts_in_denominator(self):
        class Never:
            def validate_and_execute(self, *args):
                raise AssertionError("Failed generation must not execute")
        record = {"key": "C0:local001", "arm": "C0", "example_id": "local001", "db_id": "fixture",
                  "status": "failed", "generated_sql": None}
        result = score_record(record, self.database, None, Never(), None)
        rows = [{**result, "arm": a["arm"], "key": a["arm"] + ":local001"} for a in self.arms]
        summary = summarize(rows, self.identity, [])
        self.assertEqual(summary["selected_arm"], "C0")
        self.assertEqual(summary["arms"]["C0"]["total"], 1)
        self.assertEqual(summary["arms"]["C0"]["generation_failures"], 1)

    def test_mock_and_incomplete_runs_cannot_be_scored(self):
        with self.assertRaisesRegex(ValueError, "Finish all"):
            score_run(self.root, self.directory, {"identity": self.identity})
        with self.assertRaisesRegex(ValueError, "Mock"):
            score_run(self.root, self.directory, {"identity": {**self.identity, "mock": True}})

    def test_comparator_hash_drift_rejected(self):
        source = ROOT / "data/raw/spider2/spider2-lite/evaluation_suite/evaluate_utils.py"
        with self.assertRaisesRegex(ValueError, "checksum"):
            official_comparator(source, "changed")

    def test_complete_scoring_writes_reports_and_replays_without_execution(self):
        upstream = self.root / "data/raw/spider2/spider2-lite/evaluation_suite"
        gold_root = upstream / "gold/exec_result"
        gold_root.mkdir(parents=True)
        metadata = upstream / "gold/spider2lite_eval.jsonl"
        ids = [p["example_id"] for p in self.prompts]
        metadata.write_text("".join(json.dumps({"instance_id": i, "condition_cols": [], "ignore_order": True}) + "\n" for i in ids))
        utils = upstream / "evaluate_utils.py"
        utils.write_bytes((ROOT / "data/raw/spider2/spider2-lite/evaluation_suite/evaluate_utils.py").read_bytes())
        self.identity["gold_files"] = {}
        for identifier in ids:
            path = gold_root / (identifier + ".csv")
            path.write_text("name\nAlice\nBob\n")
            self.identity["gold_files"][identifier] = [path.relative_to(self.root).as_posix()]
        self.identity["resources"].update({p.relative_to(self.root).as_posix(): sha(p) for p in (metadata, utils)})
        manifest = {"identity": self.identity, "identity_sha256": digest(self.identity)}
        with self.journal(identity={"expected_ids": self.identity["expected_ids"], "run_sha256": manifest["identity_sha256"]}) as journal:
            records = self.generate(journal)
            write_json(self.directory / "generation.json", {"run_sha256": manifest["identity_sha256"],
                       "journal_sha256": sha(self.directory / "requests.jsonl"), "mock": False,
                       "records": records, "records_sha256": digest(records)})
        with patch("builtins.print"):
            self.assertEqual(score_run(self.root, self.directory, manifest), 0)
            with patch.object(SQLiteSandboxExecutor, "validate_and_execute", side_effect=AssertionError("Cached score must replay")):
                self.assertEqual(score_run(self.root, self.directory, manifest), 0)
        scores = json.loads((self.directory / "scores.json").read_text())
        self.assertEqual(scores["summary"]["arms"]["C0"]["correct"], 2)
        self.assertEqual(scores["summary"]["selected_arm"], "C0")
        self.assertTrue((self.directory / "report.md").exists())

    def test_promotion_requires_net_gain_across_two_families(self):
        identity = deepcopy(self.identity)
        identity["target_families"] = [{"family_id": "A", "databases": ["a"]},
                                       {"family_id": "B", "databases": ["b"]}]
        records = []
        for arm in ("C0", "C1", "C2"):
            for i in range(4):
                correct = arm != "C0" and i < 3
                records.append({"arm": arm, "example_id": str(i), "db_id": "a" if i < 2 else "b",
                                "correct": correct, "nonempty_correct": correct, "status": "correct" if correct else "result_mismatch"})
        reservations = [{"example_id": "C1:0", "reserved_usd": "0.01"},
                        {"example_id": "C2:0", "reserved_usd": "0.02"}]
        self.assertEqual(summarize(records, identity, reservations)["selected_arm"], "C1")
        identity["target_families"] = [{"family_id": "one", "databases": ["a", "b"]}]
        self.assertEqual(summarize(records, identity, reservations)["selected_arm"], "C0")


if __name__ == "__main__":
    unittest.main()
