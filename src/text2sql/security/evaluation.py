from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from text2sql.execution import SQLiteSandboxExecutor
from text2sql.schema import inspect_sqlite_schema

from .policy import (
    SECURITY_GUARDRAIL_VERSION,
    SecurityGuardrailPolicy,
    evaluate_input,
    evaluate_output,
    question_sha256,
)
from .sql_validator import SQLiteSQLValidator


SECURITY_CORPUS_VERSION = "sec001-corpus-v1"
SECURITY_EVALUATOR_VERSION = "sec002-evaluator-v1"


@dataclass(frozen=True)
class SecurityCase:
    case_id: str
    case_type: str
    category: str
    question: str
    candidate_sql: str
    expected_s1: str

    def __post_init__(self) -> None:
        string_values = (
            self.case_id, self.case_type, self.category, self.question,
            self.candidate_sql, self.expected_s1,
        )
        if not all(isinstance(value, str) for value in string_values):
            raise ValueError("Security case fields must be strings")
        if not all(value.strip() for value in string_values):
            raise ValueError("Security case fields must not be empty")
        if self.case_type not in {"benign", "adversarial"}:
            raise ValueError("case_type must be benign or adversarial")
        if self.expected_s1 not in {"allow", "block"}:
            raise ValueError("expected_s1 must be allow or block")
        if (self.case_type == "benign") != (self.expected_s1 == "allow"):
            raise ValueError("Benign cases must allow and adversarial cases must block")


@dataclass(frozen=True)
class SecurityEvaluationConfig:
    evaluation_id: str
    corpus_path: Path
    corpus_sha256: str
    fixture_schema_path: Path
    fixture_schema_sha256: str
    policy: SecurityGuardrailPolicy
    config_sha256: str


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_under_root(project_root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Security resource paths must be non-empty strings")
    resolved = (project_root / value).resolve()
    if resolved != project_root and project_root not in resolved.parents:
        raise ValueError("Security resource path escapes the project root")
    if not resolved.is_file():
        raise ValueError(f"Security resource is missing: {value}")
    return resolved


def load_security_config(path: str | Path) -> SecurityEvaluationConfig:
    config_path = Path(path).resolve()
    raw = config_path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    expected = {
        "schema_version", "evaluation_id", "corpus_version", "corpus_path",
        "corpus_sha256", "fixture_schema_path", "fixture_schema_sha256",
        "guardrail_version", "restricted_columns", "restricted_tables",
        "max_question_characters",
    }
    if set(data) != expected or data["schema_version"] != 1:
        raise ValueError("Invalid security evaluation configuration")
    if data["corpus_version"] != SECURITY_CORPUS_VERSION:
        raise ValueError("Unsupported security corpus version")
    if data["guardrail_version"] != SECURITY_GUARDRAIL_VERSION:
        raise ValueError("Unsupported security guardrail version")
    project_root = config_path.parents[2]
    corpus_path = _resolve_under_root(project_root, data["corpus_path"])
    fixture_path = _resolve_under_root(project_root, data["fixture_schema_path"])
    for resource, expected_hash in (
        (corpus_path, data["corpus_sha256"]),
        (fixture_path, data["fixture_schema_sha256"]),
    ):
        if not isinstance(expected_hash, str) or _sha256_file(resource) != expected_hash:
            raise ValueError(f"Security resource checksum mismatch: {resource.name}")
    for key in ("restricted_columns", "restricted_tables"):
        values = data[key]
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) for value in values)
        ):
            raise ValueError(f"{key} must be a list of strings")
    policy = SecurityGuardrailPolicy(
        restricted_columns=tuple(data["restricted_columns"]),
        restricted_tables=tuple(data["restricted_tables"]),
        max_question_characters=data["max_question_characters"],
    )
    evaluation_id = data["evaluation_id"]
    if not isinstance(evaluation_id, str) or not evaluation_id.strip():
        raise ValueError("evaluation_id must not be empty")
    return SecurityEvaluationConfig(
        evaluation_id=evaluation_id,
        corpus_path=corpus_path,
        corpus_sha256=data["corpus_sha256"],
        fixture_schema_path=fixture_path,
        fixture_schema_sha256=data["fixture_schema_sha256"],
        policy=policy,
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_security_cases(config: SecurityEvaluationConfig) -> tuple[SecurityCase, ...]:
    if _sha256_file(config.corpus_path) != config.corpus_sha256:
        raise ValueError("Security corpus checksum changed after config load")
    cases: list[SecurityCase] = []
    for line_number, line in enumerate(config.corpus_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid security corpus JSON on line {line_number}") from exc
        expected = {"case_id", "case_type", "category", "question", "candidate_sql", "expected_s1"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError(f"Invalid security case fields on line {line_number}")
        cases.append(SecurityCase(**payload))
    ids = [case.case_id for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("Security corpus must contain unique case IDs")
    if {case.case_type for case in cases} != {"benign", "adversarial"}:
        raise ValueError("Security corpus must include benign and adversarial cases")
    return tuple(cases)


def _metrics(records: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    benign = [record for record in records if record["case_type"] == "benign"]
    adversarial = [record for record in records if record["case_type"] == "adversarial"]
    benign_blocked = sum(not record[arm]["released"] for record in benign)
    attacks_released = sum(record[arm]["released"] for record in adversarial)
    decisions_correct = sum(
        record[arm]["released"] == (record["expected_s1"] == "allow")
        for record in records
    )
    return {
        "total": len(records),
        "benign": len(benign),
        "adversarial": len(adversarial),
        "released": sum(record[arm]["released"] for record in records),
        "attack_success_count": attacks_released,
        "attack_success_rate": attacks_released / len(adversarial),
        "unsafe_query_count": attacks_released,
        "unsafe_query_rate": attacks_released / len(adversarial),
        "benign_false_positive_count": benign_blocked,
        "benign_false_positive_rate": benign_blocked / len(benign),
        "expected_decision_accuracy": decisions_correct / len(records),
    }


class SecurityEvaluationRunner:
    def __init__(
        self,
        config: SecurityEvaluationConfig,
        sandbox: SQLiteSandboxExecutor | None = None,
    ) -> None:
        self.config = config
        self.sandbox = sandbox or SQLiteSandboxExecutor()
        self.validator = SQLiteSQLValidator()

    def run(self, database_path: str | Path) -> dict[str, Any]:
        database_path = Path(database_path).resolve()
        schema = inspect_sqlite_schema(database_path, db_id="security-fixture")
        records: list[dict[str, Any]] = []
        for case in load_security_cases(self.config):
            input_decision = evaluate_input(case.question, self.config.policy)
            validation = self.validator.validate(case.candidate_sql, schema)
            sandbox_result = (
                self.sandbox.execute(database_path, case.candidate_sql, validation)
                if validation.valid
                else None
            )
            output_decision = evaluate_output(validation, sandbox_result, self.config.policy)
            s0_released = bool(sandbox_result and sandbox_result.succeeded)
            s1_released = input_decision.allowed and output_decision.allowed
            records.append({
                "case_id": case.case_id,
                "case_type": case.case_type,
                "category": case.category,
                "expected_s1": case.expected_s1,
                "question_sha256": question_sha256(case.question),
                "sql_sha256": validation.sql_sha256,
                "validation": {
                    "valid": validation.valid,
                    "issue_codes": [issue.code for issue in validation.issues],
                    "referenced_tables": list(validation.referenced_tables),
                    "referenced_columns": list(validation.referenced_columns),
                },
                "sandbox_status": sandbox_result.status if sandbox_result else "not_run",
                "s0": {"released": s0_released, "reason_codes": [] if s0_released else ["safety_boundary_rejected"]},
                "s1": {
                    "released": s1_released,
                    "input": input_decision.to_dict(),
                    "output": output_decision.to_dict(),
                },
            })
        return {
            "schema_version": 1,
            "evaluator_version": SECURITY_EVALUATOR_VERSION,
            "evaluation_id": self.config.evaluation_id,
            "config_sha256": self.config.config_sha256,
            "corpus_sha256": self.config.corpus_sha256,
            "fixture_schema_sha256": self.config.fixture_schema_sha256,
            "guardrail_version": SECURITY_GUARDRAIL_VERSION,
            "metrics": {"s0": _metrics(records, "s0"), "s1": _metrics(records, "s1")},
            "cases": records,
        }


def write_security_report(path: str | Path, report: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
