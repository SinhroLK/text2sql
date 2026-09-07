from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

import sqlparse
from sqlparse import tokens as Token
from sqlparse.sql import Function, Statement, Token as SQLToken, TokenList

from text2sql.domain import SchemaSnapshot
from text2sql.schema.canonical import validate_canonical_schema


SQL_VALIDATOR_VERSION = "safe001-sqlite-v1"

_FORBIDDEN_KEYWORDS = frozenset(
    {
        "ALTER",
        "ANALYZE",
        "ATTACH",
        "BEGIN",
        "COMMIT",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "INSERT",
        "PRAGMA",
        "REINDEX",
        "RELEASE",
        "REPLACE",
        "ROLLBACK",
        "SAVEPOINT",
        "TRANSACTION",
        "TRIGGER",
        "UPDATE",
        "VACUUM",
    }
)

_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "edit",
        "fts3_tokenizer",
        "load_extension",
        "readfile",
        "shell",
        "sqlite_compileoption_get",
        "sqlite_compileoption_used",
        "sqlite_source_id",
        "sqlite_version",
        "writefile",
    }
)

_SYSTEM_TABLE_PREFIXES = ("sqlite_", "pragma_")


@dataclass(frozen=True)
class SQLiteValidationPolicy:
    max_sql_characters: int = 100_000
    deny_comments: bool = True
    allow_trailing_semicolon: bool = True

    def __post_init__(self) -> None:
        if self.max_sql_characters < 1:
            raise ValueError("max_sql_characters must be positive")


@dataclass(frozen=True)
class SQLAstNode:
    kind: str
    value: str | None = None
    children: tuple["SQLAstNode", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True)
class SQLValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class SQLValidationResult:
    valid: bool
    validator_version: str
    statement_type: str | None
    sql_sha256: str
    ast_sha256: str | None
    referenced_tables: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    issues: tuple[SQLValidationIssue, ...]
    ast: SQLAstNode | None = None

    def to_dict(self, *, include_ast: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "valid": self.valid,
            "validator_version": self.validator_version,
            "statement_type": self.statement_type,
            "sql_sha256": self.sql_sha256,
            "ast_sha256": self.ast_sha256,
            "referenced_tables": list(self.referenced_tables),
            "referenced_columns": list(self.referenced_columns),
            "issues": [asdict(issue) for issue in self.issues],
        }
        if include_ast:
            payload["ast"] = self.ast.to_dict() if self.ast else None
        return payload

    def require_valid(self) -> "SQLValidationResult":
        if not self.valid:
            raise SQLValidationError(self)
        return self


class SQLValidationError(ValueError):
    def __init__(self, result: SQLValidationResult) -> None:
        self.result = result
        detail = "; ".join(issue.message for issue in result.issues)
        super().__init__(detail or "SQL validation failed")


class _AuthorizationAudit:
    def __init__(self, allowed_tables: dict[str, str]) -> None:
        self.allowed_tables = allowed_tables
        self.tables: set[str] = set()
        self.columns: set[str] = set()
        self.denied_issue: SQLValidationIssue | None = None

    def authorize(
        self,
        action: int,
        argument_one: str | None,
        argument_two: str | None,
        database_name: str | None,
        trigger_or_view: str | None,
    ) -> int:
        del trigger_or_view
        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
        }
        recursive_action = getattr(sqlite3, "SQLITE_RECURSIVE", None)
        if recursive_action is not None:
            allowed_actions.add(recursive_action)

        if action not in allowed_actions:
            self.denied_issue = SQLValidationIssue(
                "non_readonly_operation",
                "The statement requests an operation outside the read-only SELECT policy",
            )
            return sqlite3.SQLITE_DENY

        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (argument_two or argument_one or "").casefold()
            if (
            function_name in _FORBIDDEN_FUNCTIONS
            or function_name.startswith("pragma_")
        ):
                self.denied_issue = SQLValidationIssue(
                    "forbidden_function",
                    f"Function {function_name!r} is not allowed",
                )
                return sqlite3.SQLITE_DENY

        if action == sqlite3.SQLITE_READ:
            table_name = argument_one or ""
            folded_table = table_name.casefold()
            if folded_table.startswith(_SYSTEM_TABLE_PREFIXES):
                self.denied_issue = SQLValidationIssue(
                    "system_table_access",
                    f"System table {table_name!r} is not allowed",
                )
                return sqlite3.SQLITE_DENY
            canonical_table = self.allowed_tables.get(folded_table)
            if canonical_table is None:
                self.denied_issue = SQLValidationIssue(
                    "unknown_table",
                    f"Table {table_name!r} is not in the canonical schema",
                )
                return sqlite3.SQLITE_DENY
            self.tables.add(canonical_table)
            if argument_two:
                self.columns.add(f"{canonical_table}.{argument_two}")

        if database_name and database_name.casefold() not in {"main", "temp"}:
            self.denied_issue = SQLValidationIssue(
                "unknown_schema",
                f"Database schema {database_name!r} is not allowed",
            )
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


class SQLiteSQLValidator:
    """Validate one SQLite SELECT without evaluating the submitted query."""

    def __init__(self, policy: SQLiteValidationPolicy | None = None) -> None:
        self.policy = policy or SQLiteValidationPolicy()

    def validate(self, sql: str, schema: SchemaSnapshot) -> SQLValidationResult:
        if not isinstance(sql, str):
            return self._invalid(
                hashlib.sha256(b"").hexdigest(),
                SQLValidationIssue("invalid_sql_type", "SQL must be a string"),
            )
        sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        early_issue = self._validate_input(sql, schema)
        if early_issue:
            return self._invalid(sql_hash, early_issue)

        try:
            statements = tuple(
                statement
                for statement in sqlparse.parse(sql)
                if any(not token.is_whitespace for token in statement.tokens)
            )
        except Exception:
            return self._invalid(
                sql_hash,
                SQLValidationIssue("parse_error", "SQL could not be parsed"),
            )

        if len(statements) != 1:
            return self._invalid(
                sql_hash,
                SQLValidationIssue(
                    "multiple_statements",
                    "Exactly one SQL statement is required",
                ),
            )

        statement = statements[0]
        ast = _build_ast(statement)
        ast_hash = _ast_sha256(ast)
        statement_type = statement.get_type().upper()
        issue = self._validate_ast_policy(statement, statement_type)
        if issue:
            return self._invalid(sql_hash, issue, statement_type, ast, ast_hash)

        audit = _AuthorizationAudit(
            {table.name.casefold(): table.name for table in schema.tables}
        )
        connection = sqlite3.connect(":memory:")
        try:
            self._create_empty_schema(connection, schema)
            connection.set_authorizer(audit.authorize)
            connection.execute("EXPLAIN QUERY PLAN " + sql)
        except sqlite3.Error as exc:
            issue = audit.denied_issue or _sqlite_issue(exc)
            return self._invalid(
                sql_hash,
                issue,
                statement_type,
                ast,
                ast_hash,
                audit,
            )
        finally:
            connection.close()

        return SQLValidationResult(
            valid=True,
            validator_version=SQL_VALIDATOR_VERSION,
            statement_type=statement_type,
            sql_sha256=sql_hash,
            ast_sha256=ast_hash,
            referenced_tables=tuple(sorted(audit.tables, key=str.casefold)),
            referenced_columns=tuple(sorted(audit.columns, key=str.casefold)),
            issues=(),
            ast=ast,
        )

    def _validate_input(
        self, sql: str, schema: SchemaSnapshot
    ) -> SQLValidationIssue | None:
        if not sql.strip():
            return SQLValidationIssue("empty_sql", "SQL must not be empty")
        if len(sql) > self.policy.max_sql_characters:
            return SQLValidationIssue(
                "sql_too_large",
                f"SQL exceeds the {self.policy.max_sql_characters}-character limit",
            )
        if schema.dialect.casefold() != "sqlite":
            return SQLValidationIssue(
                "unsupported_dialect", "SAFE-001 accepts only SQLite schemas"
            )
        try:
            validate_canonical_schema(schema)
        except ValueError:
            return SQLValidationIssue(
                "invalid_canonical_schema", "Canonical schema validation failed"
            )
        if any(not table.columns for table in schema.tables):
            return SQLValidationIssue(
                "invalid_canonical_schema",
                "Every canonical table must contain at least one column",
            )
        return None

    def _validate_ast_policy(
        self, statement: Statement, statement_type: str
    ) -> SQLValidationIssue | None:
        flattened = tuple(statement.flatten())
        if self.policy.deny_comments and any(
            token.ttype in Token.Comment for token in flattened
        ):
            return SQLValidationIssue(
                "comments_not_allowed", "SQL comments are not allowed"
            )

        semicolons = [
            token
            for token in flattened
            if token.ttype is Token.Punctuation and token.value == ";"
        ]
        if semicolons:
            significant = [
                token
                for token in flattened
                if not token.is_whitespace and token.ttype not in Token.Comment
            ]
            trailing_only = (
                self.policy.allow_trailing_semicolon
                and len(semicolons) == 1
                and significant[-1] is semicolons[0]
            )
            if not trailing_only:
                return SQLValidationIssue(
                    "multiple_statements",
                    "Only one optional trailing semicolon is allowed",
                )

        if statement_type != "SELECT":
            return SQLValidationIssue(
                "unsupported_statement",
                "Exactly one read-only SELECT, optionally introduced by WITH, is required",
            )

        forbidden_function = _find_forbidden_function(statement)
        if forbidden_function:
            return SQLValidationIssue(
                "forbidden_function",
                f"Function {forbidden_function!r} is not allowed",
            )

        for token in flattened:
            if token.ttype in Token.Name.Placeholder:
                return SQLValidationIssue(
                    "parameters_not_allowed", "Unbound SQL parameters are not allowed"
                )
            if token.ttype in Token.Keyword:
                keyword = token.normalized.upper().split()[0]
                if keyword in _FORBIDDEN_KEYWORDS:
                    return SQLValidationIssue(
                        "forbidden_keyword", f"Keyword {keyword!r} is not allowed"
                    )
        return None

    @staticmethod
    def _create_empty_schema(
        connection: sqlite3.Connection, schema: SchemaSnapshot
    ) -> None:
        for table in schema.tables:
            columns = ", ".join(
                f"{_quote_identifier(column.name)} BLOB" for column in table.columns
            )
            connection.execute(
                f"CREATE TABLE {_quote_identifier(table.name)} ({columns})"
            )

    @staticmethod
    def _invalid(
        sql_hash: str,
        issue: SQLValidationIssue,
        statement_type: str | None = None,
        ast: SQLAstNode | None = None,
        ast_hash: str | None = None,
        audit: _AuthorizationAudit | None = None,
    ) -> SQLValidationResult:
        return SQLValidationResult(
            valid=False,
            validator_version=SQL_VALIDATOR_VERSION,
            statement_type=statement_type,
            sql_sha256=sql_hash,
            ast_sha256=ast_hash,
            referenced_tables=tuple(
                sorted(audit.tables, key=str.casefold) if audit else ()
            ),
            referenced_columns=tuple(
                sorted(audit.columns, key=str.casefold) if audit else ()
            ),
            issues=(issue,),
            ast=ast,
        )


def _find_forbidden_function(token: SQLToken) -> str | None:
    if isinstance(token, Function):
        function_name = (token.get_name() or "").casefold()
        if (
            function_name in _FORBIDDEN_FUNCTIONS
            or function_name.startswith("pragma_")
        ):
            return function_name
    if isinstance(token, TokenList):
        for child in token.tokens:
            forbidden = _find_forbidden_function(child)
            if forbidden:
                return forbidden
    return None


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _build_ast(token: SQLToken) -> SQLAstNode:
    if isinstance(token, TokenList):
        children = tuple(
            _build_ast(child)
            for child in token.tokens
            if not child.is_whitespace
        )
        return SQLAstNode(kind=token.__class__.__name__.casefold(), children=children)

    value = token.normalized
    if token.ttype in Token.Literal:
        value = "<literal>"
    elif token.ttype in Token.Comment:
        value = "<comment>"
    elif token.ttype in Token.Name.Placeholder:
        value = "<parameter>"
    elif token.ttype in Token.Keyword:
        value = value.upper()
    return SQLAstNode(kind=str(token.ttype), value=value)


def _ast_sha256(ast: SQLAstNode) -> str:
    encoded = json.dumps(
        ast.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sqlite_issue(error: sqlite3.Error) -> SQLValidationIssue:
    message = str(error)
    folded = message.casefold()
    mappings = (
        (
            "no such table",
            "unknown_table",
            "A referenced table is not in the canonical schema",
        ),
        (
            "no such column",
            "unknown_column",
            "A referenced column is not in the canonical schema",
        ),
        ("ambiguous column", "ambiguous_column", "A column reference is ambiguous"),
        (
            "no such database",
            "unknown_schema",
            "A referenced database schema is not allowed",
        ),
    )
    if "no such table" in folded:
        missing_name = folded.partition("no such table:")[2].strip()
        if "." in missing_name:
            return SQLValidationIssue(
                "unknown_schema", "A referenced database schema is not allowed"
            )
    for fragment, code, public_message in mappings:
        if fragment in folded:
            return SQLValidationIssue(code, public_message)
    if re.search(
        r"\bsyntax error\b|\bincomplete input\b|\bunrecognized token\b", folded
    ):
        return SQLValidationIssue("malformed_sql", "SQL syntax is invalid")
    return SQLValidationIssue("sqlite_validation_error", "SQLite rejected the query")
