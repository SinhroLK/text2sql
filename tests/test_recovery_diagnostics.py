import copy
import unittest

from text2sql.development.diagnostics import pair_saved_records


class RecoveryDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.prediction = {"example_id": "dev1", "db_id": "db", "generated_sql": "SELECT 1",
                           "generation": {"question": "Count rows", "output_tokens": 1024,
                                          "metadata": {"provider": {"max_tokens": 1024}}}}
        self.report = {"evaluation": {"expected_ids": ["dev1"], "correct": 0, "records": [
            {"result": {"example_id": "dev1", "db_id": "db", "correct": False,
                        "status": "result_mismatch", "error_message": "mismatch",
                        "generated": {"row_count": 1, "columns": ["a"]},
                        "reference": {"row_count": 2, "columns": ["a"]}}}]}}

    def test_cap_hit_is_suspected_not_proven_truncation(self):
        result = pair_saved_records([self.prediction], self.report, ["dev1"])[0]
        labels = {x["label"]: x for x in result["labels"]}
        self.assertEqual(labels["completion_cap_hit"]["confidence"], "suspected")
        self.assertIn("semantic_unclassified", labels)
        self.assertNotIn("truncation", labels)
        self.assertNotIn("reference", result)

    def test_missing_duplicate_or_wrong_database_outcomes_fail(self):
        for predictions in ([], [self.prediction, self.prediction],
                            [{**self.prediction, "db_id": "other"}]):
            with self.subTest(predictions=predictions), self.assertRaises(ValueError):
                pair_saved_records(predictions, self.report, ["dev1"])

    def test_annotation_must_match_saved_sql(self):
        with self.assertRaisesRegex(ValueError, "different SQL"):
            pair_saved_records([self.prediction], self.report, ["dev1"],
                               {"dev1": {"sql_sha256": "bad", "findings": []}})

    def test_b5_format_requires_pinned_question_and_does_not_invent_usage(self):
        prediction = copy.deepcopy(self.prediction)
        del prediction["generation"]
        with self.assertRaises(ValueError):
            pair_saved_records([prediction], self.report, ["dev1"])
        result = pair_saved_records([prediction], self.report, ["dev1"],
                                    questions={"dev1": "Count rows"})[0]
        self.assertFalse(result["generation_audit_available"])
        self.assertNotIn("completion_cap_hit", [x["label"] for x in result["labels"]])


if __name__ == "__main__":
    unittest.main()
