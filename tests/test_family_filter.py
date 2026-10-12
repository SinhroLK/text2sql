import hashlib
import json
import unittest

from text2sql.retrieval.family_filter import digest, filter_schema_families, target_family_groups
from text2sql.retrieval.index import LoadedRetrievalIndex, RetrievalIndexEntry, RetrievalIndexError
from text2sql.retrieval.selector import FixedRandomSelector, TfidfCosineSelector


class FamilyFilterTest(unittest.TestCase):
    def setUp(self):
        self.index = LoadedRetrievalIndex(tuple(
            RetrievalIndexEntry(f"id{i}", i, db, "count rows", "SELECT 1", ("count", "rows"))
            for i, db in enumerate(("exact_alias", "partial_alias", "unrelated"))), {"original": True}, "original")
        self.audit = {"training": [{"db_id": x.db_id} for x in self.index.entries],
                      "retrieval_overlaps": [{"training_db_id": "exact_alias"},
                                             {"training_db_id": "partial_alias"}]}
        self.policy = {"policy_id": "recovery-family-exclusions-v1",
                       "decision": "exclude_all_overlap_candidates",
                       "audit_sha256": digest(self.audit),
                       "source_entries_sha256": hashlib.sha256("".join(
                           json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
                           for e in self.index.entries).encode()).hexdigest(),
                       "excluded_training_databases": ["exact_alias", "partial_alias"]}

    def test_both_selectors_cannot_retrieve_excluded_families(self):
        filtered = filter_schema_families(self.index, self.audit, self.policy)
        for selector in (FixedRandomSelector(filtered, k=1, seed=42), TfidfCosineSelector(filtered, k=1)):
            self.assertEqual(selector.select("count rows").entries[0].entry.db_id, "unrelated")
        self.assertEqual(filtered.manifest["excluded_examples"], 2)
        self.assertEqual(len(self.index.entries), 3)
        self.assertNotEqual(filtered.manifest_sha256, self.index.manifest_sha256)

    def test_missing_exclusion_or_changed_audit_is_rejected(self):
        for policy in ({**self.policy, "excluded_training_databases": ["exact_alias"]},
                       {**self.policy, "audit_sha256": "bad"}):
            with self.assertRaises(RetrievalIndexError):
                filter_schema_families(self.index, self.audit, policy)

    def test_changed_source_question_or_sql_is_rejected(self):
        from dataclasses import replace
        changed = replace(self.index, entries=(replace(self.index.entries[0], sql="SELECT 2"),
                                              *self.index.entries[1:]))
        with self.assertRaisesRegex(RetrievalIndexError, "source entries"):
            filter_schema_families(changed, self.audit, self.policy)

    def test_family_closure_links_shared_training_aliases_and_target_overlaps(self):
        audit = {"targets": [{"db_id": x} for x in ("a", "b", "c", "d")],
                 "retrieval_overlaps": [{"target_db_id": x, "training_db_id": "alias"} for x in ("a", "b")],
                 "target_overlaps": [{"left_db_id": "b", "right_db_id": "c"}]}
        self.assertEqual([x["databases"] for x in target_family_groups(audit)], [["a", "b", "c"], ["d"]])


if __name__ == "__main__":
    unittest.main()
