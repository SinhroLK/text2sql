from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .sql_validator import SQLValidationResult

if TYPE_CHECKING:
    from text2sql.execution import SandboxResult


SECURITY_GUARDRAIL_VERSION = "sec002-guardrails-v1"


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    stage: str
    reason_codes: tuple[str, ...]
    policy_version: str = SECURITY_GUARDRAIL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityGuardrailPolicy:
    restricted_columns: tuple[str, ...]
    restricted_tables: tuple[str, ...] = ()
    max_question_characters: int = 4_000

    def __post_init__(self) -> None:
        if type(self.max_question_characters) is not int or self.max_question_characters < 1:
            raise ValueError("max_question_characters must be a positive integer")
        for name, values in (
            ("restricted_columns", self.restricted_columns),
            ("restricted_tables", self.restricted_tables),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError(f"{name} must be a tuple of strings")
            normalized = [value.casefold() for value in values]
            if any(not value.strip() for value in values) or len(normalized) != len(set(normalized)):
                raise ValueError(f"{name} must contain unique non-empty identifiers")


_INPUT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prompt_injection", re.compile(
        r"\b(?:ignore|disregard|override)\b.{0,40}\b(?:previous|prior|system|instructions?)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("destructive_request", re.compile(
        r"\b(?:drop\b.{0,60}\b(?:table|database)|delete\b|"
        r"update\b.{0,60}\b(?:employee|record|row|salary|set|table)\b|"
        r"insert\s+into|alter\s+table|vacuum\s+into|attach\s+database)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("sensitive_data_request", re.compile(
        r"\b(?:social\s+security|ssn|api[ _-]?tokens?|passwords?|credit[ -]?card|"
        r"email\s+addresses?|salar(?:y|ies))\b",
        re.IGNORECASE,
    )),
    ("system_or_file_request", re.compile(
        r"\b(?:sqlite_master|sqlite_schema|readfile|writefile|load_extension|host\s+file)\b|"
        r"\bsqlite\b.{0,30}\b(?:internal|schema)\b",
        re.IGNORECASE | re.DOTALL,
    )),
)


def evaluate_input(question: str, policy: SecurityGuardrailPolicy) -> GuardrailDecision:
    if not isinstance(question, str) or not question.strip():
        return GuardrailDecision(False, "input", ("invalid_question",))
    if len(question) > policy.max_question_characters:
        return GuardrailDecision(False, "input", ("question_too_long",))
    codes = tuple(code for code, pattern in _INPUT_RULES if pattern.search(question))
    return GuardrailDecision(not codes, "input", codes)


def evaluate_sql_output(
    validation: SQLValidationResult,
    policy: SecurityGuardrailPolicy,
) -> GuardrailDecision:
    if not validation.valid:
        return GuardrailDecision(False, "output", ("sql_validation_failed",))

    tables = {name.casefold() for name in validation.referenced_tables}
    columns = {name.casefold() for name in validation.referenced_columns}
    restricted_tables = tables & {name.casefold() for name in policy.restricted_tables}
    restricted_columns = columns & {name.casefold() for name in policy.restricted_columns}
    codes: list[str] = []
    if restricted_tables:
        codes.append("restricted_table")
    if restricted_columns:
        codes.append("restricted_column")
    return GuardrailDecision(not codes, "output", tuple(codes))


def evaluate_output(
    validation: SQLValidationResult,
    sandbox: "SandboxResult | None",
    policy: SecurityGuardrailPolicy,
) -> GuardrailDecision:
    sql_decision = evaluate_sql_output(validation, policy)
    if not sql_decision.allowed:
        return sql_decision
    codes: list[str] = []
    if sandbox is None or not sandbox.succeeded:
        codes.append("sandbox_not_successful")
    return GuardrailDecision(not codes, "output", tuple(codes))


def question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()
