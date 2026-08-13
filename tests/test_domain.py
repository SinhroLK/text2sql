from __future__ import annotations

import unittest

from text2sql.domain import Text2SQLExample


class Text2SQLExampleTest(unittest.TestCase):
    def test_example_preserves_original_question(self) -> None:
        question = "Which trains do not stop in London?"
        example = Text2SQLExample(
            example_id="example-1",
            db_id="rail",
            question=question,
            dialect="sqlite",
            split="fixture",
        )
        self.assertEqual(example.question, question)


if __name__ == "__main__":
    unittest.main()

