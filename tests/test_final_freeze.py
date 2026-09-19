from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text2sql.finalization.freeze import (
    FINAL_ARMS,
    FreezeError,
    build_freeze_manifest,
    verify_freeze_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "configs/final/run001-freeze.json"


class FinalFreezeTest(unittest.TestCase):
    def test_manifest_matches_all_frozen_resources(self) -> None:
        manifest = verify_freeze_manifest(PROJECT_ROOT, MANIFEST)

        self.assertEqual(tuple(manifest["selected_arms"]), FINAL_ARMS)
        self.assertEqual(manifest["held_out_examples"], 104)
        self.assertEqual(manifest["development_examples"], 31)
        self.assertFalse(manifest["held_out_accessed"])
        self.assertEqual(manifest["provider_calls"], 0)
        self.assertEqual(manifest, build_freeze_manifest(PROJECT_ROOT))

    def test_tampered_manifest_is_rejected(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        payload["primary_arm"] = "B0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(FreezeError, "pinned resource changed"):
                verify_freeze_manifest(PROJECT_ROOT, path)


if __name__ == "__main__":
    unittest.main()
