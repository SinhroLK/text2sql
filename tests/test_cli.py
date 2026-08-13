from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from text2sql.cli import main


class CLITest(unittest.TestCase):
    def test_cli_writes_jsonl_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "demo.sqlite"
            output_path = Path(temp_dir) / "result.jsonl"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "CREATE TABLE products (product_id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                connection.commit()
            finally:
                connection.close()

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--question",
                        "List products",
                        "--database",
                        str(database_path),
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            printed = json.loads(stdout.getvalue())
            persisted = json.loads(output_path.read_text().strip())
            self.assertEqual(printed["run_id"], persisted["run_id"])
            self.assertIn('FROM "products"', printed["selected_sql"])


if __name__ == "__main__":
    unittest.main()

