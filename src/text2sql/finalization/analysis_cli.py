from __future__ import annotations

import argparse
import json
from pathlib import Path

from .statistics import AnalysisError, analyze_reports, write_analysis


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLAN = PROJECT_ROOT / "configs/final/statistical-plan.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze complete reports under the frozen final-evaluation "
            "protocol"
        )
    )
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        analysis = analyze_reports(
            project_root=PROJECT_ROOT,
            reports_dir=args.reports_dir,
            plan_path=args.plan,
        )
        write_analysis(
            analysis,
            json_path=args.output_json,
            csv_path=args.output_csv,
        )
    except (AnalysisError, OSError, ValueError) as error:
        print(json.dumps({
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "status": "complete",
        "freeze_id": analysis["freeze_id"],
        "example_count": analysis["example_count"],
        "arms": [item["arm"] for item in analysis["arms"]],
        "output_json": args.output_json.as_posix(),
        "output_csv": args.output_csv.as_posix(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
