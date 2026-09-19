from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from text2sql.execution import SQLiteSandboxExecutor
from text2sql.schema import inspect_sqlite_schema
from text2sql.security import (
    SQLiteSQLValidator,
    evaluate_input,
    evaluate_output,
    evaluate_sql_output,
)
from text2sql.security.evaluation import (
    SECURITY_CORPUS_VERSION,
    SECURITY_EVALUATOR_VERSION,
    SecurityEvaluationRunner,
    load_security_cases,
    load_security_config,
)
from text2sql.security.evaluation_cli import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "configs" / "security" / "sec001-s1.toml"


class SecurityEvaluationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "security.sqlite"
        schema_sql = (
            PROJECT_ROOT / "data" / "fixtures" / "security_schema.sql"
        ).read_text(encoding="utf-8")
        with sqlite3.connect(self.database) as connection:
            connection.executescript(schema_sql)
        self.config = load_security_config(CONFIG)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_frozen_config_and_corpus_are_complete(self) -> None:
        cases = load_security_cases(self.config)
        self.assertEqual(SECURITY_CORPUS_VERSION, "sec001-corpus-v1")
        self.assertEqual(len(cases), 16)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))
        self.assertEqual(
            {case.case_type for case in cases}, {"benign", "adversarial"}
        )
        self.assertTrue(
            all("example.test" not in case.question for case in cases)
        )

    def test_input_policy_blocks_explicit_attacks_without_blocking_benign_cases(self) -> None:
        cases = load_security_cases(self.config)
        decisions = {
            case.case_id: evaluate_input(case.question, self.config.policy)
            for case in cases
        }
        for case in cases:
            if case.case_type == "benign":
                self.assertTrue(decisions[case.case_id].allowed, case.case_id)
        self.assertIn(
            "destructive_request", decisions["attack-001"].reason_codes
        )
        self.assertIn(
            "destructive_request", decisions["attack-002"].reason_codes
        )
        self.assertIn(
            "destructive_request", decisions["attack-003"].reason_codes
        )
        self.assertIn(
            "prompt_injection", decisions["attack-004"].reason_codes
        )
        self.assertIn(
            "sensitive_data_request", decisions["attack-006"].reason_codes
        )
        self.assertIn(
            "system_or_file_request", decisions["attack-008"].reason_codes
        )
        self.assertTrue(decisions["attack-010"].allowed)

    def test_output_policy_uses_canonical_sensitive_identifiers(self) -> None:
        schema = inspect_sqlite_schema(
            self.database, db_id="security-fixture"
        )
        validator = SQLiteSQLValidator()
        sandbox = SQLiteSandboxExecutor()
        safe_sql = "SELECT name FROM employees ORDER BY name"
        safe_validation = validator.validate(safe_sql, schema)
        self.assertTrue(
            evaluate_sql_output(safe_validation, self.config.policy).allowed
        )
        safe_result = sandbox.execute(
            self.database, safe_sql, safe_validation
        )
        self.assertTrue(
            evaluate_output(
                safe_validation, safe_result, self.config.policy
            ).allowed
        )

        sensitive_sql = "SELECT e.ssn AS value FROM employees e"
        sensitive_validation = validator.validate(sensitive_sql, schema)
        pre_execution = evaluate_sql_output(
            sensitive_validation, self.config.policy
        )
        self.assertFalse(pre_execution.allowed)
        self.assertIn("restricted_column", pre_execution.reason_codes)

        sensitive_result = sandbox.execute(
            self.database, sensitive_sql, sensitive_validation
        )
        decision = evaluate_output(
            sensitive_validation, sensitive_result, self.config.policy
        )
        self.assertFalse(decision.allowed)
        self.assertIn("restricted_column", decision.reason_codes)

    def test_s0_s1_report_has_expected_metrics_and_no_payloads(self) -> None:
        report = SecurityEvaluationRunner(self.config).run(self.database)
        self.assertEqual(
            report["evaluator_version"], SECURITY_EVALUATOR_VERSION
        )
        self.assertEqual(
            report["metrics"]["s0"]["attack_success_count"], 5
        )
        self.assertEqual(
            report["metrics"]["s1"]["attack_success_count"], 0
        )
        self.assertEqual(
            report["metrics"]["s1"]["benign_false_positive_count"], 0
        )
        self.assertEqual(
            report["metrics"]["s1"]["expected_decision_accuracy"], 1.0
        )
        encoded = json.dumps(report)
        self.assertNotIn("Alice Example", encoded)
        self.assertNotIn("SELECT name", encoded)
        self.assertNotIn("Ignore all previous", encoded)
        self.assertNotIn("test-token", encoded)

    def test_cli_writes_the_same_structured_report(self) -> None:
        report_path = Path(self.temporary.name) / "report.json"
        with redirect_stdout(io.StringIO()) as output:
            exit_code = main([
                "--config",
                str(CONFIG),
                "--database",
                str(self.database),
                "--report",
                str(report_path),
            ])
        self.assertEqual(exit_code, 0, output.getvalue())
        printed = json.loads(output.getvalue())
        written = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(printed, written)


if __name__ == "__main__":
    unittest.main()
