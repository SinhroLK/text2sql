from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "data" / "fixtures" / "demo_schema.sql"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "fixtures" / "demo.sqlite"


def create_database(schema_path: Path, output_path: Path) -> Path:
    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = schema_path.read_text(encoding="utf-8")
    connection = sqlite3.connect(output_path)
    try:
        connection.executescript(schema_sql)
        connection.commit()
    finally:
        connection.close()
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the Phase 0 SQLite fixture")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    created = create_database(args.schema, args.output)
    print(created)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

