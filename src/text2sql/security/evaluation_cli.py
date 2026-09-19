from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluation import SecurityEvaluationRunner, load_security_config, write_security_report


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "security" / "sec001-s1.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the provider-free S0/S1 security evaluation")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = SecurityEvaluationRunner(load_security_config(args.config)).run(args.database)
        if args.report:
            write_security_report(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
