from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from text2sql.domain import GenerationInput
from text2sql.execution import SandboxResult
from text2sql.providers.base import ProviderResponse
from text2sql.refinement.config import load_refinement_config
from text2sql.refinement.core import RefinementPipeline, prepare_context, sql_identity
from text2sql.refinement.journal import CallJournal
from text2sql.refinement.runner import generate_run, load_completed_run, read_predictions, score_run

ROOT = Path(__file__).resolve().parents[1]


class SequenceProvider:
    provider_name = "groq"
    model_id = "openai/gpt-oss-120b"

    def __init__(self, values):
        self.values, self.requests = list(values), []

    def generate(self, request):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, ProviderResponse) else ProviderResponse((value,), 10, 5, {"finish_reason": "stop"})


class RefinementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "fixture.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT); INSERT INTO customers VALUES(1,'Alice'),(2,'Bob');")
        connection.close()
        self.config = load_refinement_config(ROOT / "configs/experiments/exp007-b7.toml")
        self.schema, self.context = prepare_context("List customer names", self.database, "fixture", self.config)

    def tearDown(self):
        self.temp.cleanup()

    def run_values(self, values):
        provider = SequenceProvider(values)
        result = RefinementPipeline(self.config, provider).run("List customer names", self.database, self.schema, self.context)
        return result, provider

    def test_three_strategies_and_deterministic_tie_break(self):
        values = ["SELECT 1", "SELECT 2", "SELECT 3"]
        first, provider = self.run_values(values)
        second, _ = self.run_values(values)
        self.assertEqual(first["selected_candidate_id"], 0)
        self.assertEqual(first["selected_sql"], second["selected_sql"])
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(provider.requests[0].prompt, self.context)
        self.assertEqual(len({r.prompt for r in provider.requests}), 3)
        self.assertEqual(first["input_tokens"], 30)
        self.assertIsNone(first["repair_target_id"])

    def test_agreement_uses_distinct_complete_nonempty_results(self):
        result, _ = self.run_values(["SELECT 1", "SELECT 2", "SELECT 1+1"])
        self.assertEqual(result["selected_candidate_id"], 1)
        self.assertEqual(result["candidates"][1]["agreement"], 1)

    def test_duplicate_queries_do_not_manufacture_consensus(self):
        result, _ = self.run_values(["SELECT 1", "SELECT 2", "SELECT 2"])
        self.assertEqual(result["selected_candidate_id"], 0)
        self.assertEqual(result["candidates"][2]["duplicate_of"], 1)
        self.assertEqual(result["candidates"][1]["agreement"], 0)

    def test_literals_are_part_of_duplicate_identity(self):
        self.assertNotEqual(sql_identity("SELECT 1"), sql_identity("SELECT 2"))
        self.assertNotEqual(sql_identity("SELECT 'A'"), sql_identity("SELECT 'B'"))
        self.assertEqual(sql_identity("SELECT  1"), sql_identity("SELECT 1"))

    def test_empty_results_get_no_consensus_bonus(self):
        result, _ = self.run_values(["SELECT 1 WHERE 0", "SELECT 2 WHERE 0", "SELECT 3"])
        self.assertTrue(all(c["agreement"] == 0 for c in result["candidates"]))

    def test_unknown_identifier_gets_one_successful_repair(self):
        result, provider = self.run_values(["SELECT missing FROM customers"] * 3 + ["SELECT name FROM customers"])
        self.assertEqual(len(provider.requests), 4)
        self.assertEqual(result["selected_candidate_id"], 3)
        self.assertIsNone(result["selection_only_id"])
        self.assertEqual(result["repair_target_id"], 0)
        self.assertIn("unknown_column", provider.requests[3].prompt)
        self.assertNotIn(str(self.root), provider.requests[3].prompt)

    def test_failed_repair_never_recurses_or_returns_invalid_sql(self):
        result, provider = self.run_values(["SELECT missing FROM customers"] * 4)
        self.assertEqual(len(provider.requests), 4)
        self.assertIsNone(result["selected_sql"])
        self.assertEqual(result["status"], "no_usable_candidate")

    def test_unsafe_candidates_are_neither_executed_nor_repaired(self):
        before = self.database.read_bytes()
        provider = SequenceProvider(["DELETE FROM customers", "DROP TABLE customers", "SELECT load_extension('/tmp/secret')"])
        with patch("text2sql.refinement.core.SQLiteSandboxExecutor.execute") as execute:
            result = RefinementPipeline(self.config, provider).run("List names", self.database, self.schema, self.context)
            execute.assert_not_called()
        self.assertEqual(len(provider.requests), 3)
        self.assertIsNone(result["selected_sql"])
        self.assertEqual(self.database.read_bytes(), before)

    def test_generation_truncation_is_audited_not_executed(self):
        partial = ProviderResponse(("SELECT name FROM",), 10, 1024, {"finish_reason": "length"})
        result, provider = self.run_values([partial, "SELECT 2", "SELECT 3"])
        self.assertEqual(result["candidates"][0]["status"], "generation_error")
        self.assertEqual(result["selected_candidate_id"], 1)
        self.assertEqual(len(provider.requests), 3)

    def test_all_truncated_generations_can_get_one_repair(self):
        partial = ProviderResponse(("SELECT name FROM",), 10, 1024, {"finish_reason": "length"})
        result, provider = self.run_values([partial] * 3 + ["SELECT name FROM customers"])
        self.assertEqual(len(provider.requests), 4)
        self.assertEqual(result["selected_candidate_id"], 3)

    def test_empty_transport_failures_are_not_repaired(self):
        failed = ProviderResponse((), metadata={"error_code": "provider_failure"})
        result, provider = self.run_values([failed] * 3)
        self.assertIsNone(result["selected_sql"])
        self.assertEqual(len(provider.requests), 3)

    def test_truncated_sandbox_results_never_become_final_answers(self):
        provider = SequenceProvider(["SELECT 1", "SELECT 2", "SELECT 3"])
        sandbox = SimpleNamespace(execute=lambda *args: SandboxResult("truncated", "a" * 64, 1, rows=((1,),), truncated=True))
        result = RefinementPipeline(self.config, provider, sandbox).run("List names", self.database, self.schema, self.context)
        self.assertIsNone(result["selected_sql"])
        self.assertEqual(len(provider.requests), 3)

    def test_runtime_failure_repair_diagnostic_omits_raw_sqlite_error(self):
        result, provider = self.run_values(["SELECT abs(-9223372036854775808)"] * 3 + ["SELECT 1"])
        self.assertEqual(result["selected_candidate_id"], 3)
        self.assertIn("query_execution_failed", provider.requests[-1].prompt)
        self.assertNotIn("integer overflow", provider.requests[-1].prompt)

    def test_config_preserves_base_and_bounded_scope(self):
        self.assertEqual(self.config.base.max_tokens, 1024)
        for kwargs in ({"max_candidates": 4}, {"max_repairs": 2}, {"max_calls": 125}, {"max_input_tokens": 7000}):
            with self.assertRaises(ValueError):
                replace(self.config, **kwargs)

    def journal(self, provider, **kwargs):
        return CallJournal(self.root / "calls.jsonl", kwargs.pop("identity", {"expected_ids": ["local001"]}),
                           self.config, provider, kwargs.pop("max_cost_usd", 1.0), counter=lambda *args: 100, **kwargs)

    def request(self, text="SELECT 1"):
        return GenerationInput("Question", text, self.schema, self.config.base.model_id)

    def test_journal_replays_without_new_provider_calls(self):
        provider = SequenceProvider(["SELECT 1"])
        journal = self.journal(provider)
        try:
            first = journal.call("local001:0", self.request())
        finally:
            journal.close()
        second = self.journal(provider)
        try:
            self.assertEqual(second.call("local001:0", self.request()), first)
            self.assertEqual(second.replayed, 1)
            self.assertEqual(len(provider.requests), 1)
        finally:
            second.close()

    def test_ambiguous_interrupted_call_is_not_reissued(self):
        provider = SequenceProvider([RuntimeError("interrupted")])
        journal = self.journal(provider)
        try:
            with self.assertRaises(RuntimeError):
                journal.call("local001:0", self.request())
        finally:
            journal.close()
        journal = self.journal(provider)
        try:
            response = journal.call("local001:0", self.request())
            self.assertEqual(response.metadata["error_code"], "interrupted_call_not_reissued")
            self.assertEqual(len(provider.requests), 1)
        finally:
            journal.close()

    def test_budget_exhaustion_precedes_provider_call(self):
        provider = SequenceProvider([])
        journal = self.journal(provider, max_cost_usd=0.00001)
        try:
            with self.assertRaisesRegex(ValueError, "budget"):
                journal.call("local001:0", self.request())
            self.assertEqual(provider.requests, [])
        finally:
            journal.close()

    def test_journal_refuses_test_ids_and_prompt_changes(self):
        provider = SequenceProvider(["SELECT 1"])
        journal = self.journal(provider)
        try:
            with self.assertRaisesRegex(ValueError, "boundary"):
                journal.call("local999:0", self.request())
            journal.call("local001:0", self.request())
            with self.assertRaisesRegex(ValueError, "prompt mismatch"):
                journal.call("local001:0", self.request("changed"))
        finally:
            journal.close()

    def test_journal_rejects_drift_corruption_and_concurrent_writer(self):
        provider = SequenceProvider([])
        journal = self.journal(provider)
        try:
            with self.assertRaises(BlockingIOError):
                self.journal(provider)
        finally:
            journal.close()
        with self.assertRaisesRegex(ValueError, "identity"):
            self.journal(provider, identity={"expected_ids": ["local002"]})
        path = self.root / "calls.jsonl"
        path.write_text(path.read_text().replace("local001", "local003"))
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.journal(provider)

    def test_runner_resume_and_scoring_are_separate(self):
        provider = SequenceProvider(["SELECT 1", "SELECT 2", "SELECT 3"])
        prepared = {"local001": (SimpleNamespace(question="Question", db_id="fixture"), self.database, self.schema, self.context)}
        identity = {"expected_ids": ["local001"]}
        with patch("text2sql.refinement.journal.count_input_tokens", return_value=100), redirect_stdout(io.StringIO()):
            run = generate_run(self.config, prepared, identity, provider, self.root / "run", 1.0)
            resumed = generate_run(self.config, prepared, identity, provider, self.root / "run", 1.0)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(run["records"], resumed["records"])
        offline = load_completed_run(self.config, identity, self.root / "run")
        self.assertEqual(offline["records"], run["records"])
        calls = []
        def evaluate(key, sql):
            calls.append((key, sql))
            return SimpleNamespace(result=SimpleNamespace(correct=sql == "SELECT 1", status="correct" if sql == "SELECT 1" else "result_mismatch"))
        report = score_run(run, SimpleNamespace(evaluate_one=evaluate))
        self.assertEqual(report["evaluation"]["final"]["correct"], 1)
        self.assertEqual(report["repair"]["attempts"], 0)
        self.assertEqual(len(calls), 3)
        with self.assertRaisesRegex(ValueError, "identity"):
            read_predictions(self.root / "run/predictions.jsonl", {"expected_ids": ["local999"]})

    def test_oversized_prompt_is_rejected_without_spending(self):
        provider = SequenceProvider([])
        journal = self.journal(provider)
        journal.counter = lambda *args: 6001
        try:
            result = journal.call("local001:0", self.request())
            self.assertEqual(result.metadata["error_code"], "input_token_limit")
            self.assertEqual(journal.reserved_cost, 0)
            self.assertEqual(provider.requests, [])
        finally:
            journal.close()

    def test_scoring_counts_refusal_as_failure_without_executing_it(self):
        outcome, _ = self.run_values(["DROP TABLE customers"] * 3)
        record = {"example_id": "local001", "db_id": "fixture", **outcome}
        run = {"identity": {"expected_ids": ["local001"]}, "records": {"local001": record},
               "provider_calls": 3, "replayed_calls": 0, "reserved_cost_usd": 0, "journal_sha256": "a" * 64}
        with patch.object(SimpleNamespace(), "evaluate_one", create=True) as evaluate:
            report = score_run(run, SimpleNamespace(evaluate_one=evaluate))
            evaluate.assert_not_called()
        self.assertEqual(report["evaluation"]["final"]["correct"], 0)
        self.assertEqual(report["evaluation"]["final"]["total"], 1)

    def test_provider_preserves_length_finish_reason_and_disables_sdk_retries(self):
        from text2sql.refinement.provider import B7GroqProvider

        completion = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="SELECT name FROM"))],
                                     model=self.config.base.model_id, usage=SimpleNamespace(prompt_tokens=11, completion_tokens=1024))
        with patch("text2sql.refinement.provider.groq.Groq") as sdk:
            sdk.return_value.chat.completions.create.return_value = completion
            provider = B7GroqProvider(self.config, api_key="test-key")
            response = provider.generate(self.request())
            self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
            self.assertEqual(response.metadata["finish_reason"], "length")
            self.assertEqual(response.output_tokens, 1024)


if __name__ == "__main__":
    unittest.main()
