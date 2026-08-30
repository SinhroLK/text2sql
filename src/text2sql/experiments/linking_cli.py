from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.datasets import load_spider2_lite_sqlite
from text2sql.evaluation import (
    EvaluationResourceError,
    Spider2SQLiteDatabaseResolver,
)
from text2sql.schema import (
    MSchemaSamplePolicy,
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
)

from .config import (
    ExperimentConfigurationError,
    load_baseline_config,
)
from .linking_audit import audit_development_schema_linking


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "data/raw/spider2/spider2-lite/spider2-lite.jsonl"
)
DEFAULT_DATASET_CONFIG = (
    PROJECT_ROOT
    / "configs/datasets/spider2-lite-sqlite-v1.toml"
)
DEFAULT_DATASET_MANIFEST = (
    PROJECT_ROOT
    / "configs/datasets/"
    "spider2-lite-sqlite-metadata-manifest-v1.json"
)
DEFAULT_DATABASE_ROOT = (
    PROJECT_ROOT
    / "data/raw/spider2/spider2-lite/resource/databases/"
    "spider2-localdb"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "artifacts/reports/exp003-b6-linking-audit.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit B6/B6R schema and prompt context without contacting Groq."
        )
    )
    parser.add_argument(
        "--experiment-config",
        type=Path,
        default=(
            PROJECT_ROOT / "configs/experiments/exp003-b6.toml"
        ),
    )
    parser.add_argument(
        "--source-jsonl", type=Path, default=DEFAULT_SOURCE
    )
    parser.add_argument(
        "--dataset-config",
        type=Path,
        default=DEFAULT_DATASET_CONFIG,
    )
    parser.add_argument(
        "--expected-dataset-manifest",
        type=Path,
        default=DEFAULT_DATASET_MANIFEST,
    )
    parser.add_argument(
        "--database-root",
        type=Path,
        default=DEFAULT_DATABASE_ROOT,
    )
    parser.add_argument(
        "--output", type=Path, default=None
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_baseline_config(args.experiment_config)
        if config.baseline not in {"B6", "B6R"}:
            raise ExperimentConfigurationError(
                "Schema-linking audit requires a B6 or B6R configuration"
            )
        dataset = load_spider2_lite_sqlite(
            args.source_jsonl,
            args.dataset_config,
            PROJECT_ROOT,
            args.expected_dataset_manifest,
        )
        payload = audit_development_schema_linking(
            dataset=dataset,
            database_resolver=Spider2SQLiteDatabaseResolver(
                args.database_root
            ),
            sample_policy=MSchemaSamplePolicy(
                examples_per_column=(
                    config.mschema_examples_per_column
                ),
                max_text_length=config.mschema_max_text_length,
                scan_rows_per_column=(
                    config.mschema_scan_rows_per_column
                ),
            ),
            linking_policy=(
                RecallSchemaLinkingPolicy
                if config
                .schema_link_include_all_selected_table_columns
                else SchemaLinkingPolicy
            )(
                max_tables=config.schema_link_max_tables,
                max_columns_per_table=(
                    config.schema_link_max_columns_per_table
                ),
                minimum_columns_per_table=(
                    config.schema_link_minimum_columns_per_table
                ),
                min_score=config.schema_link_min_score,
                include_value_matches=(
                    config.schema_link_include_value_matches
                ),
                include_foreign_key_closure=(
                    config.schema_link_include_foreign_key_closure
                ),
                fallback_mode=(
                    config.schema_link_fallback_mode
                    or "full_schema"
                ),
            ),
            prompt_variant=config.prompt_variant,
        )
        rendered = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        output = (
            args.output
            or (
                PROJECT_ROOT
                / "artifacts/reports/exp004-b6r-linking-audit.json"
                if config.baseline == "B6R"
                else DEFAULT_OUTPUT
            )
        ).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    except (
        EvaluationResourceError,
        ExperimentConfigurationError,
        FileNotFoundError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "complete",
                "output": str(output),
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
