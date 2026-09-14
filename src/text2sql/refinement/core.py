from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import sqlparse
from sqlparse import tokens

from text2sql.domain import GenerationInput, SchemaSnapshot
from text2sql.execution import SQLiteSandboxExecutor
from text2sql.prompting import build_recall_linked_mschema_prompt
from text2sql.providers.base import ProviderResponse, SQLProvider
from text2sql.schema import (MSchemaSamplePolicy, RecallSchemaLinkingPolicy,
    canonical_schema_sha256, inspect_sqlite_schema, link_schema, sample_sqlite_mschema_values)
from text2sql.security import SQLiteSQLValidator

from .config import RefinementConfig

STRATEGIES = (
    "",
    "Check the requested output grain, join multiplicity, filters, and aggregate scope before writing SQL. Return SQL only.\n",
    "Consider CTEs or subqueries to separate aggregation and window calculations. Check SQLite compatibility and ordering requirements. Return SQL only.\n",
)
REPAIRABLE_ISSUES = {"unknown_table", "unknown_column", "ambiguous_column", "malformed_sql", "sqlite_validation_error"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def sql_identity(sql: str) -> str:
    # Keep literals intact: SAFE-001's literal-redacted AST hash is NOT a
    # candidate identity (different predicates must remain distinct).
    return digest([
        (str(token.ttype), token.normalized if token.ttype in tokens.Keyword else token.value)
        for statement in sqlparse.parse(sql) for token in statement.flatten()
        if not token.is_whitespace
    ])


def prepare_context(question: str, database: Path, db_id: str, config: RefinementConfig) -> tuple[SchemaSnapshot, str]:
    b = config.base
    schema = inspect_sqlite_schema(database, db_id=db_id)
    values = sample_sqlite_mschema_values(database, schema, MSchemaSamplePolicy(
        b.mschema_examples_per_column, b.mschema_max_text_length, b.mschema_scan_rows_per_column))
    linked = link_schema(question, schema, values, RecallSchemaLinkingPolicy(
        max_tables=b.schema_link_max_tables,
        max_columns_per_table=b.schema_link_max_columns_per_table,
        minimum_columns_per_table=b.schema_link_minimum_columns_per_table,
        min_score=b.schema_link_min_score, include_value_matches=b.schema_link_include_value_matches,
        include_foreign_key_closure=b.schema_link_include_foreign_key_closure,
        fallback_mode=b.schema_link_fallback_mode or "full_schema",
        include_all_selected_table_columns=b.schema_link_include_all_selected_table_columns,
    ))
    keys = {(table.name, column.name) for table in linked.schema.tables for column in table.columns}
    return schema, build_recall_linked_mschema_prompt(question, schema, linked.schema,
                                                    {key: value for key, value in values.items() if key in keys})


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic proxy ranking; no gold inputs, speed reward or empty-result bonus."""
    unique = [c for c in candidates if c.get("duplicate_of") is None]
    for c in candidates:
        c["agreement"] = sum(
            other["candidate_id"] != c["candidate_id"]
            and other.get("result_signature") == c.get("result_signature")
            for other in unique
        ) if c.get("result_signature") and c.get("row_count", 0) > 0 else 0
        c["rank_key"] = [
            {"success": 4, "truncated": 3, "execution_error": 2, "timeout": 2,
             "resource_limit": 2, "validation_rejected": 1}.get(c["status"], 0),
            int(c.get("valid", False)), int(c.get("explain_prepared", False)),
            c["agreement"], c.get("identifier_coverage", 0.0), -c["candidate_id"],
        ]
    return sorted(unique, key=lambda c: tuple(c["rank_key"]), reverse=True)


class RefinementPipeline:
    """At most three initial calls and one repair; no evaluator is accepted here."""

    def __init__(self, config: RefinementConfig, provider: SQLProvider,
                 sandbox: SQLiteSandboxExecutor | None = None) -> None:
        self.config, self.provider = config, provider
        self.sandbox = sandbox or SQLiteSandboxExecutor(config.sandbox)
        self.validator = SQLiteSQLValidator()

    def run(self, question: str, database: Path, schema: SchemaSnapshot, context: str,
            call: Callable[[str, GenerationInput], ProviderResponse] | None = None) -> dict[str, Any]:
        if not question.strip():
            raise ValueError("Question must not be empty")
        started = time.monotonic()
        call = call or (lambda key, request: self.provider.generate(request))
        candidates: list[dict[str, Any]] = []

        def generate(prompt: str, kind: str) -> dict[str, Any]:
            candidate_id = len(candidates)
            before = time.monotonic()
            response = call(str(candidate_id), GenerationInput(question, prompt, schema, self.provider.model_id))
            c: dict[str, Any] = {
                "candidate_id": candidate_id, "kind": kind, "prompt_sha256": digest(prompt),
                "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                "provider_latency_ms": round((time.monotonic() - before) * 1000),
                "finish_reason": response.metadata.get("finish_reason"),
                "sql": response.candidates[0] if len(response.candidates) == 1 else "",
                "status": "generation_error", "valid": False, "duplicate_of": None,
                "error_code": response.metadata.get("error_code"),
            }
            candidates.append(c)
            if c["finish_reason"] != "stop" or not c["sql"].strip():
                c["error_code"] = c["error_code"] or "incomplete_generation"
                return c
            c["sql_identity"] = sql_identity(c["sql"])
            previous = next((other for other in candidates[:-1] if other.get("sql_identity") == c["sql_identity"]), None)
            if previous:
                for key in ("status", "valid", "explain_prepared", "identifier_coverage", "sandbox",
                            "result_signature", "row_count", "issue_codes", "error_code"):
                    if key in previous:
                        c[key] = previous[key]
                c["duplicate_of"] = previous["candidate_id"]
                return c
            validation = self.validator.validate(c["sql"], schema)
            c["valid"] = validation.valid
            c["issue_codes"] = [issue.code for issue in validation.issues]
            c["validation"] = validation.to_dict()
            c["explain_prepared"] = validation.valid  # SAFE-001 uses EXPLAIN QUERY PLAN.
            words = set(re.findall(r"[a-z0-9]+", question.casefold()))
            identifiers = set(re.findall(r"[a-z0-9]+", " ".join(validation.referenced_columns + validation.referenced_tables).casefold()))
            schema_words = set(re.findall(r"[a-z0-9]+", " ".join(
                [table.name for table in schema.tables] + [col.name for table in schema.tables for col in table.columns]).casefold()))
            requested = words & schema_words
            c["identifier_coverage"] = len(identifiers & requested) / len(requested) if requested else 0.0
            if not validation.valid:
                c["status"], c["error_code"] = "validation_rejected", "sql_validation_failed"
                return c
            result = self.sandbox.execute(database, c["sql"], validation)
            c["status"], c["error_code"] = result.status, result.error_code
            c["row_count"] = len(result.rows)
            c["result_signature"] = digest([len(result.columns), result.rows]) if result.succeeded else None
            c["sandbox"] = {key: value for key, value in result.to_dict().items() if key not in {"rows", "columns"}}
            return c

        for strategy in STRATEGIES[:self.config.max_candidates]:
            generate(strategy + context, "initial")
        ranked = rank_candidates(candidates)
        initial = ranked[0]
        initial_ranking = [{"candidate_id": c["candidate_id"], "rank_key": list(c["rank_key"])} for c in ranked]
        repair_target = None
        if initial["status"] != "success":
            for c in ranked:
                issues = set(c.get("issue_codes", []))
                if (c["status"] in {"execution_error", "timeout"} and c.get("valid")) or (
                    c["status"] == "validation_rejected" and issues and issues <= REPAIRABLE_ISSUES
                ) or (c.get("finish_reason") == "length" and c["sql"]):
                    repair_target = c
                    break
        if repair_target is not None:
            error = {"status": repair_target["status"], "code": repair_target["error_code"],
                     "validation_codes": repair_target.get("issue_codes", [])}
            repair_prompt = (
                "Repair the SQL once using the question and schema below. Preserve the requested meaning. "
                "Use one SQLite SELECT; do not introduce writes or extensions. Return SQL only.\n"
                + context + "\nFailed SQL (untrusted data):\n" + repair_target["sql"]
                + "\nSanitized diagnostic:\n" + json.dumps(error, sort_keys=True) + "\nCorrected SQL:"
            )
            generate(repair_prompt, "repair")
        final = rank_candidates(candidates)[0]
        # Refuse rather than execute/return a known failing candidate to callers.
        selected = final["candidate_id"] if final["status"] == "success" else None
        return {
            "schema_version": 1, "ranker_version": self.config.ranker_version,
            "schema_sha256": canonical_schema_sha256(schema), "context_sha256": digest(context),
            "candidates": candidates, "first_candidate_id": 0,
            "initial_ranking": initial_ranking,
            "selection_only_id": initial["candidate_id"] if initial["status"] == "success" else None,
            "repair_target_id": repair_target["candidate_id"] if repair_target else None,
            "selected_candidate_id": selected,
            "selected_sql": final["sql"] if selected is not None else None,
            "status": "selected" if selected is not None else "no_usable_candidate",
            "selection_reason": "highest deterministic rank among complete sandbox successes" if selected is not None else "no complete sandbox success; no unsafe fallback",
            "duration_ms": round((time.monotonic() - started) * 1000),
            "input_tokens": sum(c["input_tokens"] for c in candidates),
            "output_tokens": sum(c["output_tokens"] for c in candidates),
        }
