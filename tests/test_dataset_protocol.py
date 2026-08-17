from __future__ import annotations

import unittest
from pathlib import Path

from text2sql.datasets import load_and_validate_protocol, validate_protocol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-v1.toml"


class DatasetProtocolTest(unittest.TestCase):
    def test_frozen_protocol_and_split_firewall(self) -> None:
        protocol, manifest = load_and_validate_protocol(CONFIG_PATH, PROJECT_ROOT)

        self.assertEqual(protocol["benchmark"]["expected_scope_total"], 135)
        self.assertEqual(len(manifest["development_instance_ids"]), 31)
        self.assertEqual(len(manifest["test_instance_ids"]), 104)
        self.assertFalse(protocol["benchmark"]["oracle_tables"])
        self.assertFalse(protocol["benchmark"]["official_leaderboard_comparable"])

    def test_validator_rejects_test_leakage(self) -> None:
        protocol, manifest = load_and_validate_protocol(CONFIG_PATH, PROJECT_ROOT)
        protocol["leakage_policy"]["test_results_visible_during_development"] = True

        with self.assertRaisesRegex(ValueError, "leakage policy"):
            validate_protocol(protocol, manifest)

    def test_protocol_is_documented(self) -> None:
        experiments = (PROJECT_ROOT / "docs/experiments.md").read_text(encoding="utf-8")
        decisions = (PROJECT_ROOT / "docs/decisions.md").read_text(encoding="utf-8")
        self.assertIn("spider2-lite-sqlite-v1.toml", experiments)
        self.assertIn("ADR-003", decisions)


if __name__ == "__main__":
    unittest.main()
