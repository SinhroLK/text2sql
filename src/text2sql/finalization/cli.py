from __future__ import annotations

import argparse
import json
from pathlib import Path

from .freeze import FreezeError, verify_freeze_manifest, write_freeze_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/final/run001-freeze.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or verify the deterministic RUN-001 freeze manifest"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    create.add_argument("--replace", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = write_freeze_manifest(
                PROJECT_ROOT, args.manifest, replace=args.replace
            )
        else:
            manifest = verify_freeze_manifest(PROJECT_ROOT, args.manifest)
    except (FreezeError, OSError, ValueError) as error:
        print(json.dumps({
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "status": "frozen",
        "freeze_id": manifest["freeze_id"],
        "source_tree_sha256": manifest["source_tree_sha256"],
        "resource_count": manifest["resource_count"],
        "selected_arms": manifest["selected_arms"],
        "held_out_accessed": manifest["held_out_accessed"],
        "provider_calls": manifest["provider_calls"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
