from __future__ import annotations

import copy
import unittest

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.development.preflight import validate_development_scope
from text2sql.domain.models import Text2SQLExample


class DevelopmentScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = LoadedSpider2LiteDataset((
            Text2SQLExample("dev1", "db1", "Count rows", "sqlite", "development"),
            Text2SQLExample("test1", "db2", "Count rows", "sqlite", "test"),
        ), {})
        self.protocol = {
            "protocol_id": "recovery-development-v1", "schema_version": 1,
            "status": "offline_preparation", "paid_runs_enabled": False,
            "gold_in_generation": False, "retrieval_enabled": False,
            "development_example_ids": ["dev1"],
            "reviewed_test_example_ids": ["test1"],
            "fresh_holdout_status": "not_reserved",
        }

    def test_original_development_scope_is_accepted(self) -> None:
        validate_development_scope(self.protocol, self.dataset)

    def test_test_examples_cannot_be_added_to_development(self) -> None:
        for ids in (["dev1", "test1"], ["test1"], []):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_development_scope(
                    {**self.protocol, "development_example_ids": ids}, self.dataset)

    def test_missing_duplicate_and_malformed_exposure_ids_are_rejected(self) -> None:
        for field in ("development_example_ids", "reviewed_test_example_ids"):
            for values in (None, "dev1", [{}], [1], [""], [],
                           self.protocol[field] * 2):
                with self.subTest(field=field, values=values), self.assertRaises(ValueError):
                    validate_development_scope({**self.protocol, field: values}, self.dataset)

    def test_offline_preparation_cannot_enable_paid_gold_or_retrieval(self) -> None:
        for field in ("paid_runs_enabled", "gold_in_generation", "retrieval_enabled"):
            for value in (True, None, 0, "false"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_development_scope({**self.protocol, field: value}, self.dataset)

    def test_protocol_cannot_claim_a_fresh_holdout_or_another_version(self) -> None:
        for field, value in (("fresh_holdout_status", "reserved"),
                             ("schema_version", 2), ("schema_version", True),
                             ("status", "ready"), ("protocol_id", "another")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_development_scope({**self.protocol, field: value}, self.dataset)
        for protocol in ([], None):
            with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                validate_development_scope(protocol, self.dataset)

    def test_validation_does_not_mutate_protocol(self) -> None:
        original = copy.deepcopy(self.protocol)
        validate_development_scope(self.protocol, self.dataset)
        self.assertEqual(self.protocol, original)

    def test_v2_explicitly_declares_reused_final_scope(self) -> None:
        protocol = {**self.protocol, "protocol_id": "recovery-development-v2",
                    "final_evaluation_example_ids": ["test1"],
                    "final_evaluation_status": "reused-set evaluation; no untouched generalization claim",
                    "family_policy": "configs/development/family-exclusions-v1.json"}
        validate_development_scope(protocol, self.dataset)
        for key, value in (("final_evaluation_example_ids", ["dev1"]),
                           ("final_evaluation_status", "fresh holdout"),
                           ("family_policy", "arbitrary.json")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_development_scope({**protocol, key: value}, self.dataset)


if __name__ == "__main__":
    unittest.main()
