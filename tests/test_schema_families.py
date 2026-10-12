import unittest

from text2sql.schema.families import audit_schema_families, spider1_signatures


class SchemaFamiliesTest(unittest.TestCase):
    def test_alias_databases_are_detected_and_output_is_deterministic(self):
        pairs = frozenset({("customer", "id"), ("customer", "name")})
        result = audit_schema_families({"renamed": pairs}, {"source": pairs},
                                       target_splits={"renamed": "development"})
        overlap = result["retrieval_overlaps"][0]
        self.assertEqual(overlap["kind"], "exact_normalized_schema")
        self.assertEqual(overlap["shared_pairs"], 2)
        self.assertEqual(result["training_databases_requiring_review"], ["source"])
        self.assertEqual(result, audit_schema_families({"renamed": pairs}, {"source": pairs},
                                                      target_splits={"renamed": "development"}))

    def test_partial_containment_is_flagged_even_when_jaccard_is_low(self):
        small = frozenset(("table", str(i)) for i in range(5))
        large = frozenset(("table", str(i)) for i in range(100))
        result = audit_schema_families({"large": large}, {"small": small},
                                       target_splits={"large": "test"})
        self.assertEqual(result["retrieval_overlaps"][0]["kind"], "potential_family_overlap")

    def test_generic_single_column_overlap_is_not_a_family(self):
        result = audit_schema_families(
            {"target": frozenset({("customer", "id"), ("customer", "name")})},
            {"source": frozenset({("customer", "id"), ("customer", "salary")})},
            target_splits={"target": "test"},
        )
        self.assertEqual(result["retrieval_overlaps"], [])

    def test_cross_split_aliases_are_reported(self):
        pairs = frozenset({("table", "column")})
        result = audit_schema_families({"dev": pairs, "test": pairs}, {"train": pairs},
                                       target_splits={"dev": "development", "test": "test"})
        self.assertTrue(result["target_overlaps"][0]["cross_split"])

    def test_spider_loader_normalizes_names_and_requires_exact_coverage(self):
        records = [{"db_id": "train", "table_names_original": ["Order_Items"],
                    "column_names_original": [[-1, "*"], [0, "ProductId"]]}]
        self.assertEqual(spider1_signatures(records, {"train"}),
                         {"train": frozenset({("orderitems", "productid")})})
        with self.assertRaises(ValueError):
            spider1_signatures(records, {"missing"})
        with self.assertRaises(ValueError):
            spider1_signatures(records * 2, {"train"})


if __name__ == "__main__":
    unittest.main()
