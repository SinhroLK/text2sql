import hashlib
from pathlib import Path
import tempfile
import unittest

from text2sql.development.controls import Limits, RequestJournal
from text2sql.development.provider import JournaledProvider
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import MockSchemaAwareProvider


class RecoveryProviderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = RequestJournal(Path(self.temp.name) / "journal.jsonl",
                                      {"expected_ids": ["dev1"]},
                                      Limits(2, 10000, 10000, 10000, 2, "1", "1", "2"))
        self.addCleanup(self.journal.close)
        self.database = Path(__file__).resolve().parents[1] / "data/fixtures/demo.sqlite"
        self.prepared = Text2SQLPipeline(MockSchemaAwareProvider()).prepare("List customer names", self.database)
        self.kwargs = dict(example_id="dev1", expected_prompt_sha256=hashlib.sha256(
            self.prepared.generation_input.prompt.encode()).hexdigest(), max_completion_tokens=1024,
            token_counter=lambda prompt: len(prompt.encode()) + 256)

    def test_pipeline_mock_rehearsal_and_resume_share_one_reservation(self):
        provider = JournaledProvider(MockSchemaAwareProvider(), self.journal, **self.kwargs)
        pipeline = Text2SQLPipeline(provider)
        first = pipeline.generate("List customer names", self.database)
        second = pipeline.generate("List customer names", self.database)
        self.assertEqual(first.selected_sql, second.selected_sql)
        self.assertIsNotNone(first.selected_sql)
        self.assertTrue(first.metadata["provider"]["offline_mock"])
        self.assertEqual(sum(x["event"] == "reserve" for x in self.journal.events), 1)

    def test_live_provider_is_blocked_before_any_call(self):
        class Live:
            def generate(self, request):
                raise AssertionError("Must not call a live provider")
        with self.assertRaises(PermissionError):
            JournaledProvider(Live(), self.journal, **self.kwargs)
        with self.assertRaisesRegex(ValueError, "Confirm account"):
            JournaledProvider(Live(), self.journal, **self.kwargs, user_authorized_live_calls=True)
        with self.assertRaisesRegex(ValueError, "hidden retries"):
            JournaledProvider(Live(), self.journal, **self.kwargs, user_authorized_live_calls=True,
                              account_limits_and_prices_verified=True)

    def test_changed_prompt_is_blocked_before_reservation(self):
        provider = JournaledProvider(MockSchemaAwareProvider(), self.journal,
                                     **{**self.kwargs, "expected_prompt_sha256": "changed"})
        with self.assertRaisesRegex(ValueError, "prompt changed"):
            provider.generate(self.prepared.generation_input)
        self.assertEqual(len(self.journal.events), 1)


if __name__ == "__main__":
    unittest.main()
