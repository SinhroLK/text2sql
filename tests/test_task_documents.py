from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from text2sql.cli import main
from text2sql.datasets.documents import TaskDocumentStore
from text2sql.domain import Text2SQLExample
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers.base import ProviderResponse


class TaskDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.text = "Use five RFM buckets. Most recent customers have recency score 1."
        self.file = self.root / "RFM.md"
        self.file.write_text(self.text)
        self.digest = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.store = TaskDocumentStore(self.root, {"RFM.md": self.digest})
        self.db = self.root / "fixture.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE customers (name TEXT)")
        self.provider = Mock(provider_name="fixture", model_id="fixture")
        self.provider.generate.return_value = ProviderResponse(("SELECT name FROM customers",))
        self.pipeline = Text2SQLPipeline(self.provider)
        self.example = Text2SQLExample("fixture", "fixture", "Compute RFM segments",
                                      "sqlite", "development", metadata={"external_knowledge": "RFM.md"})

    def test_example_propagates_document_and_provenance_to_provider(self):
        result = self.pipeline.generate_example(self.example, self.db, document_store=self.store)
        request = self.provider.generate.call_args.args[0]
        self.assertIn(self.text, request.prompt)
        self.assertIn(self.example.question, request.prompt)
        self.assertEqual(result.metadata["documents"][0]["sha256"], self.digest)
        self.assertIn("task-documents-v1", result.prompt_version)
        self.assertEqual(result.execution_status, "not_executed")

    def test_preparation_never_calls_provider_and_hash_changes_with_document(self):
        plain = self.pipeline.prepare(self.example.question, self.db)
        documented = self.pipeline.prepare(self.example.question, self.db,
                                           documents=self.store.load("RFM.md"))
        self.provider.generate.assert_not_called()
        self.assertNotEqual(plain.generation_input.prompt, documented.generation_input.prompt)
        self.assertNotEqual(plain.prompt_version, documented.prompt_version)

    def test_required_document_cannot_be_silently_omitted(self):
        with self.assertRaisesRegex(ValueError, "requires"):
            self.pipeline.generate_example(self.example, self.db)
        self.provider.generate.assert_not_called()

    def test_null_reference_needs_no_document(self):
        example = Text2SQLExample("fixture", "fixture", "List customers", "sqlite", "development")
        result = self.pipeline.generate_example(example, self.db)
        self.assertEqual(result.metadata["documents"], [])

    def test_missing_or_tampered_document_fails_before_generation(self):
        self.file.write_text("Tampered definition")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.pipeline.generate_example(self.example, self.db, document_store=self.store)
        self.file.unlink()
        with self.assertRaises(FileNotFoundError):
            self.pipeline.generate_example(self.example, self.db, document_store=self.store)
        self.provider.generate.assert_not_called()

    def test_paths_gold_files_and_unlisted_documents_are_rejected(self):
        for name in ("../RFM.md", "/RFM.md", "folder/RFM.md", "folder\\RFM.md", "answer.sql", "other.md", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.load(name)

    def test_symlinks_and_special_files_are_rejected(self):
        self.file.unlink()
        self.file.symlink_to(self.db)
        with self.assertRaises(OSError):
            self.store.load("RFM.md")
        self.file.unlink()
        os.mkfifo(self.file)
        with self.assertRaisesRegex(ValueError, "regular"):
            self.store.load("RFM.md")

    def test_oversized_empty_and_invalid_utf8_documents_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "byte limit"):
            TaskDocumentStore(self.root, {"RFM.md": self.digest}, max_document_bytes=3).load("RFM.md")
        for content in (b" ", b"bad\x00text", b"\xff"):
            self.file.write_bytes(content)
            store = TaskDocumentStore(self.root, {"RFM.md": hashlib.sha256(content).hexdigest()})
            with self.assertRaises((ValueError, UnicodeDecodeError)):
                store.load("RFM.md")

    def test_cli_persists_incomplete_attempt_and_returns_failure(self):
        self.provider.generate.return_value = ProviderResponse((), output_tokens=1024, metadata={
            "completion_complete": False, "error_code": "generation_truncated",
            "rejected_candidates": ["SELECT name FROM"], "finish_reason": "length",
        })
        output = self.root / "attempt.jsonl"
        with patch("text2sql.cli.GroqProvider", return_value=self.provider), redirect_stdout(StringIO()):
            code = main(["--question", "List customers", "--database", str(self.db),
                         "--provider", "groq", "--model-id", "fixture", "--output", str(output)])
        record = json.loads(output.read_text())
        self.assertEqual(code, 1)
        self.assertIsNone(record["selected_sql"])
        self.assertEqual(record["error_category"], "generation_truncated")
        self.assertEqual(record["output_tokens"], 1024)

    def test_cli_loads_pinned_document(self):
        manifest = self.root / "documents.json"
        manifest.write_text(json.dumps({"policy_version": "task-documents-v1",
                                        "documents": {"RFM.md": self.digest}}))
        with patch("text2sql.cli.GroqProvider", return_value=self.provider), redirect_stdout(StringIO()):
            code = main(["--question", self.example.question, "--database", str(self.db),
                         "--provider", "groq", "--model-id", "fixture",
                         "--document-root", str(self.root), "--document-manifest", str(manifest),
                         "--external-knowledge", "RFM.md"])
        self.assertEqual(code, 0)
        self.assertIn(self.text, self.provider.generate.call_args.args[0].prompt)


if __name__ == "__main__":
    unittest.main()
