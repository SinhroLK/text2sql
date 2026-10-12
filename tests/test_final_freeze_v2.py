from __future__ import annotations

import json
import io
import hashlib
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from text2sql.finalization.freeze_v2 import (
    FINAL_ARMS,
    FreezeError,
    build_freeze_manifest,
    verify_freeze_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "configs/final/run001-freeze-v2.json"


class FinalFreezeV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The working tree is now a new development cycle. Verify the original
        # resources at their immutable release, not by rewriting its manifest.
        cls.snapshot = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.snapshot.cleanup)
        cls.release_root = Path(cls.snapshot.name)
        archive = subprocess.run(
            ["git", "archive", "text2sql-final-v2.1"], cwd=PROJECT_ROOT,
            capture_output=True, check=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for member in stream:
                destination = (cls.release_root / member.name).resolve()
                destination.relative_to(cls.release_root)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with stream.extractfile(member) as source:
                        destination.write_bytes(source.read())
                else:
                    raise ValueError("Unexpected non-regular file in release snapshot")
        # The release also pins a generated, gitignored security fixture.
        # Supply only missing artifacts whose bytes match the original hash.
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for resource in manifest["resources"]:
            destination = cls.release_root / resource["path"]
            if not destination.exists():
                raw = (PROJECT_ROOT / resource["path"]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != resource["sha256"]:
                    raise ValueError("Historical generated fixture checksum mismatch")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)

    def test_manifest_matches_all_historical_v2_resources(self) -> None:
        manifest = verify_freeze_manifest(self.release_root, MANIFEST)

        self.assertEqual(tuple(manifest["selected_arms"]), FINAL_ARMS)
        self.assertEqual(
            manifest["freeze_id"], "run001-final-evaluation-v2.1"
        )
        self.assertEqual(manifest["release_tag"], "text2sql-final-v2.1")
        self.assertEqual(manifest["held_out_examples"], 104)
        self.assertEqual(manifest["development_examples"], 31)
        self.assertFalse(manifest["held_out_accessed"])
        self.assertEqual(manifest["provider_calls"], 0)
        self.assertEqual(manifest, build_freeze_manifest(self.release_root))

    def test_tampered_v2_manifest_is_rejected(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        payload["primary_arm"] = "B0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(FreezeError, "pinned resource changed"):
                verify_freeze_manifest(self.release_root, path)

    def test_changed_source_still_fails_the_historical_release_gate(self) -> None:
        path = self.release_root / "src/text2sql/providers/groq.py"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n# intentional drift fixture\n")
            with self.assertRaisesRegex(FreezeError, "pinned resource changed"):
                verify_freeze_manifest(self.release_root, MANIFEST)
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
