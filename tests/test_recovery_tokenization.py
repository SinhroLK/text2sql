from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from text2sql.development.tokenization import load_local_counter, reserve_input_tokens


class RecoveryTokenizationTest(unittest.TestCase):
    def test_missing_or_changed_vocabulary_never_fetches(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                socket.socket, "connect", side_effect=AssertionError("No networking")):
            path = Path(directory) / "vocabulary"
            with self.assertRaises(FileNotFoundError):
                load_local_counter(path)
            path.write_text("tampered vocabulary")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_local_counter(path)

    def test_allowance_is_explicit_and_rejects_invalid_counts(self):
        self.assertEqual(reserve_input_tokens(1000), 1445)
        for count in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                reserve_input_tokens(count)

    def test_pinned_local_vocabulary_works_with_network_disabled(self):
        path = Path(__file__).resolve().parents[1] / "data/raw/tokenizers/o200k_base.tiktoken"
        if not path.exists():
            self.skipTest("Optional locally downloaded vocabulary is absent")
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No networking")):
            counter = load_local_counter(path)
            self.assertEqual(counter("hello world"), 2)
            self.assertGreater(counter("<|start|>user"), 1)


if __name__ == "__main__":
    unittest.main()
