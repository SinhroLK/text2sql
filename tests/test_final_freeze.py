from __future__ import annotations

import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "configs/final/run001-freeze.json"


class HistoricalFinalFreezeTest(unittest.TestCase):
    def test_v1_manifest_remains_preserved(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["freeze_id"], "run001-final-evaluation-v1"
        )
        self.assertEqual(manifest["release_tag"], "text2sql-final-v1")
        self.assertEqual(
            manifest["source_tree_sha256"],
            "36c441bf7a1cadec6b3db002c4a16b2fc56c702e5d0c506a6f11dd4cfe6ba994",
        )
        self.assertEqual(manifest["resource_count"], 137)
        self.assertFalse(manifest["held_out_accessed"])
        self.assertEqual(manifest["provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
