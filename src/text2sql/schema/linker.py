from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass, replace
from typing import Iterable, Mapping

from text2sql.domain import SchemaSnapshot, TableSchema

from .canonical import validate_canonical_schema
from .mschema import MSchemaExamples, SampleValue


SCHEMA_LINKER_VERSION = "extractive-lexical-v1"

_STOP_WORDS = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "each",
        "find",
        "for",
        "from",
        "get",
        "give",
        "how",
        "in",
        "is",
        "list",
        "me",
        "of",
        "on",
        "or",
        "show",
        "that",
        "the",
        "their",
        "to",
        "was",
        "were",
        "what",
        "which",
        "who",
        "with",
    }
)


_GENERIC_COLUMN_TOKENS = frozenset(
    {
        "code",
        "count",
        "date",
        "description",
        "id",
        "name",
        "number",
        "type",
        "value",
    }
)


@dataclass(frozen=True)
class SchemaLinkingPolicy:
    max_tables: int = 4
    max_columns_per_table: int = 12
    minimum_columns_per_table: int = 4
    min_score: int = 4
    include_value_matches: bool = True
    include_foreign_key_closure: bool = True
    fallback_mode: str = "full_schema"

    def __post_init__(self) -> None:
        if self.max_tables <= 0 or self.max_columns_per_table <= 0:
            raise ValueError("Schema-linking table and column limits must be positive")
        if self.minimum_columns_per_table <= 0:
            raise ValueError("minimum_columns_per_table must be positive")
        if self.minimum_columns_per_table > self.max_columns_per_table:
            raise ValueError("minimum_columns_per_table cannot exceed the column limit")
        if self.min_score <= 0:
            raise ValueError("Schema-linking min_score must be positive")
        if self.fallback_mode != "full_schema":
            raise ValueError("Only the recall-safe full_schema fallback is supported")


@dataclass(frozen=True)
class RecallSchemaLinkingPolicy(SchemaLinkingPolicy):
    """Recall-first policy that retains every column in selected tables."""

    include_all_selected_table_columns: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.include_all_selected_table_columns:
            raise ValueError("Recall policy must include all selected-table columns")


@dataclass(frozen=True)
class LinkedTable:
    name: str
    score: int
    reasons: tuple[str, ...]
    selection_reason: str


@dataclass(frozen=True)
class LinkedColumn:
    table_name: str
    column_name: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SchemaLinkResult:
    schema: SchemaSnapshot
    policy: SchemaLinkingPolicy
    table_links: tuple[LinkedTable, ...]
    column_links: tuple[LinkedColumn, ...]
    direct_table_names: tuple[str, ...]
    closure_table_names: tuple[str, ...]
    fallback_used: bool
    original_table_count: int
    original_column_count: int

    @property
    def selected_table_count(self) -> int:
        return len(self.schema.tables)

    @property
    def selected_column_count(self) -> int:
        return sum(len(table.columns) for table in self.schema.tables)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": SCHEMA_LINKER_VERSION,
            "policy": asdict(self.policy),
            "fallback_used": self.fallback_used,
            "direct_table_names": list(self.direct_table_names),
            "closure_table_names": list(self.closure_table_names),
            "table_links": [asdict(item) for item in self.table_links],
            "column_links": [asdict(item) for item in self.column_links],
            "original_table_count": self.original_table_count,
            "selected_table_count": self.selected_table_count,
            "original_column_count": self.original_column_count,
            "selected_column_count": self.selected_column_count,
            "table_reduction_ratio": _reduction(
                self.original_table_count, self.selected_table_count
            ),
            "column_reduction_ratio": _reduction(
                self.original_column_count, self.selected_column_count
            ),
        }


@dataclass(frozen=True)
class SchemaLinkingMetrics:
    table_precision: float
    table_recall: float
    table_f1: float
    column_precision: float
    column_recall: float
    column_f1: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _reduction(original: int, selected: int) -> float:
    return 0.0 if original == 0 else 1.0 - selected / original


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str, *, remove_stop_words: bool = False) -> tuple[str, ...]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    raw = re.findall(r"[^\W_]+", expanded.casefold(), flags=re.UNICODE)
    return tuple(
        _singular(token)
        for token in raw
        if not remove_stop_words or token not in _STOP_WORDS
    )


def _contains_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def _identifier_score(
    identifier: str,
    question_tokens: tuple[str, ...],
    *,
    phrase_score: int,
    token_score: int,
) -> tuple[int, tuple[str, ...]]:
    identifier_tokens = _tokens(identifier)
    if not identifier_tokens:
        return 0, ()
    question_set = set(question_tokens)
    overlap = tuple(sorted(set(identifier_tokens) & question_set))
    score = len(overlap) * token_score
    reasons = [f"token:{token}" for token in overlap]
    if _contains_sequence(question_tokens, identifier_tokens):
        score += phrase_score
        reasons.append("identifier_phrase")
    return score, tuple(reasons)


def _value_score(
    values: Iterable[SampleValue], question_tokens: tuple[str, ...]
) -> tuple[int, tuple[str, ...]]:
    best = 0
    reasons: list[str] = []
    for value in values:
        value_tokens = _tokens(str(value))
        if value_tokens and _contains_sequence(question_tokens, value_tokens):
            candidate = 14 + min(len(value_tokens), 4)
            if candidate > best:
                best = candidate
                reasons = ["representative_value"]
    return best, tuple(reasons)


def _foreign_key_graph(schema: SchemaSnapshot) -> dict[str, tuple[str, ...]]:
    adjacency: dict[str, set[str]] = {table.name: set() for table in schema.tables}
    canonical_names = {table.name.casefold(): table.name for table in schema.tables}
    for table in schema.tables:
        for foreign_key in table.foreign_keys:
            target = canonical_names[foreign_key.target_table.casefold()]
            adjacency[table.name].add(target)
            adjacency[target].add(table.name)
    return {
        name: tuple(sorted(neighbors, key=lambda item: (item.casefold(), item)))
        for name, neighbors in adjacency.items()
    }


def _shortest_path(
    graph: Mapping[str, tuple[str, ...]], start: str, target: str
) -> tuple[str, ...]:
    queue = deque([(start, (start,))])
    visited = {start}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for neighbor in graph[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + (neighbor,)))
    return ()


def _full_schema_fallback(
    schema: SchemaSnapshot,
    policy: SchemaLinkingPolicy,
    table_scores: Mapping[str, int],
    table_reasons: Mapping[str, tuple[str, ...]],
    column_scores: Mapping[tuple[str, str], int],
    column_reasons: Mapping[tuple[str, str], tuple[str, ...]],
) -> SchemaLinkResult:
    table_links = tuple(
        LinkedTable(
            table.name,
            table_scores[table.name],
            table_reasons[table.name],
            "fallback_full_schema",
        )
        for table in schema.tables
    )
    column_links = tuple(
        LinkedColumn(
            table.name,
            column.name,
            column_scores[(table.name, column.name)],
            tuple(
                sorted(
                    set(column_reasons[(table.name, column.name)])
                    | {"fallback_full_schema"}
                )
            ),
        )
        for table in schema.tables
        for column in table.columns
    )
    return SchemaLinkResult(
        schema=schema,
        policy=policy,
        table_links=table_links,
        column_links=column_links,
        direct_table_names=tuple(table.name for table in schema.tables),
        closure_table_names=(),
        fallback_used=True,
        original_table_count=len(schema.tables),
        original_column_count=sum(len(table.columns) for table in schema.tables),
    )


def link_schema(
    question: str,
    schema: SchemaSnapshot,
    examples: MSchemaExamples | None = None,
    policy: SchemaLinkingPolicy = SchemaLinkingPolicy(),
) -> SchemaLinkResult:
    """Select a deterministic, recall-aware schema subset for one question."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("Schema-linking question must not be empty")
    validate_canonical_schema(schema)
    canonical_table_names = {
        table.name.casefold(): table.name for table in schema.tables
    }
    canonical_column_names = {
        table.name: {
            column.name.casefold(): column.name
            for column in table.columns
        }
        for table in schema.tables
    }
    example_values = examples or {}
    real_columns = {
        (table.name, column.name)
        for table in schema.tables
        for column in table.columns
    }
    unknown_examples = sorted(set(example_values) - real_columns)
    if unknown_examples:
        raise ValueError(
            f"Schema-linking examples contain unknown columns: {unknown_examples!r}"
        )

    question_tokens = _tokens(question, remove_stop_words=True)
    table_scores: dict[str, int] = {}
    table_reasons: dict[str, tuple[str, ...]] = {}
    column_scores: dict[tuple[str, str], int] = {}
    column_reasons: dict[tuple[str, str], tuple[str, ...]] = {}

    for table in schema.tables:
        own_score, own_reasons = _identifier_score(
            table.name, question_tokens, phrase_score=12, token_score=5
        )
        positive_columns: list[tuple[int, str]] = []
        reasons = list(own_reasons)
        for column in table.columns:
            key = (table.name, column.name)
            score, scored_reasons = _identifier_score(
                column.name, question_tokens, phrase_score=10, token_score=6
            )
            if policy.include_value_matches:
                value_score, value_reasons = _value_score(
                    example_values.get(key, ()), question_tokens
                )
                score += value_score
                scored_reasons += value_reasons
            column_scores[key] = score
            column_reasons[key] = scored_reasons
            identifier_tokens = set(_tokens(column.name))
            relationship_id_without_phrase = (
                "id" in identifier_tokens
                and "identifier_phrase" not in scored_reasons
            )
            if (
                score
                and not identifier_tokens <= _GENERIC_COLUMN_TOKENS
                and not relationship_id_without_phrase
            ):
                positive_columns.append((score, column.name))
        positive_columns.sort(
            key=lambda item: (-item[0], item[1].casefold(), item[1])
        )
        for score, column_name in positive_columns[:2]:
            own_score += score
            reasons.append(f"column:{column_name}")
        table_scores[table.name] = own_score
        table_reasons[table.name] = tuple(reasons)

    ranked_tables = sorted(
        (
            table
            for table in schema.tables
            if table_scores[table.name] >= policy.min_score
        ),
        key=lambda table: (
            -table_scores[table.name],
            table.name.casefold(),
            table.name,
        ),
    )
    direct_tables = tuple(
        table.name for table in ranked_tables[: policy.max_tables]
    )
    if not direct_tables:
        return _full_schema_fallback(
            schema,
            policy,
            table_scores,
            table_reasons,
            column_scores,
            column_reasons,
        )

    selected_table_names = set(direct_tables)
    if policy.include_foreign_key_closure and len(direct_tables) > 1:
        graph = _foreign_key_graph(schema)
        for start_index, start in enumerate(direct_tables):
            for target in direct_tables[start_index + 1 :]:
                selected_table_names.update(_shortest_path(graph, start, target))
    closure_tables = tuple(
        table.name
        for table in schema.tables
        if table.name in selected_table_names and table.name not in direct_tables
    )

    selected_columns: dict[str, set[str]] = {
        table.name: set()
        for table in schema.tables
        if table.name in selected_table_names
    }
    selection_reasons: dict[tuple[str, str], set[str]] = {}
    for table in schema.tables:
        if table.name not in selected_table_names:
            continue
        chosen = selected_columns[table.name]
        structural = {
            column.name for column in table.columns if column.primary_key
        }
        for column_name in structural:
            selection_reasons.setdefault((table.name, column_name), set()).add(
                "primary_key"
            )
        chosen.update(structural)

        if isinstance(policy, RecallSchemaLinkingPolicy):
            for column in table.columns:
                chosen.add(column.name)
                selection_reasons.setdefault(
                    (table.name, column.name), set()
                ).add("recall_all_selected_table_columns")
            continue

        ranked_columns = sorted(
            (
                column
                for column in table.columns
                if column_scores[(table.name, column.name)] >= policy.min_score
            ),
            key=lambda column: (
                -column_scores[(table.name, column.name)],
                column.ordinal_position,
            ),
        )
        for column in ranked_columns:
            if len(chosen) >= policy.max_columns_per_table:
                break
            chosen.add(column.name)
            selection_reasons.setdefault((table.name, column.name), set()).add(
                "lexical_match"
            )
        for column in table.columns:
            if len(chosen) >= policy.minimum_columns_per_table:
                break
            if len(chosen) >= policy.max_columns_per_table:
                break
            chosen.add(column.name)
            selection_reasons.setdefault((table.name, column.name), set()).add(
                "minimum_context"
            )

    for table in schema.tables:
        if table.name not in selected_table_names:
            continue
        for foreign_key in table.foreign_keys:
            target_table = canonical_table_names[
                foreign_key.target_table.casefold()
            ]
            if target_table not in selected_table_names:
                continue
            source_column = canonical_column_names[table.name][
                foreign_key.source_column.casefold()
            ]
            target_column = canonical_column_names[target_table][
                foreign_key.target_column.casefold()
            ]
            selected_columns[table.name].add(source_column)
            selected_columns[target_table].add(target_column)
            selection_reasons.setdefault(
                (table.name, source_column), set()
            ).add("foreign_key")
            selection_reasons.setdefault(
                (target_table, target_column), set()
            ).add("foreign_key")

    filtered_tables: list[TableSchema] = []
    table_links: list[LinkedTable] = []
    column_links: list[LinkedColumn] = []
    for table in schema.tables:
        if table.name not in selected_table_names:
            continue
        chosen_names = selected_columns[table.name]
        columns = tuple(
            replace(column, ordinal_position=index)
            for index, column in enumerate(
                column
                for column in table.columns
                if column.name in chosen_names
            )
        )
        actual_names = {column.name for column in columns}
        foreign_keys_list = []
        for foreign_key in table.foreign_keys:
            target_table = canonical_table_names[
                foreign_key.target_table.casefold()
            ]
            source_column = canonical_column_names[table.name][
                foreign_key.source_column.casefold()
            ]
            target_column = canonical_column_names[target_table][
                foreign_key.target_column.casefold()
            ]
            if (
                target_table in selected_table_names
                and source_column in actual_names
                and target_column in selected_columns[target_table]
            ):
                foreign_keys_list.append(
                    replace(
                        foreign_key,
                        source_column=source_column,
                        target_table=target_table,
                        target_column=target_column,
                    )
                )
        foreign_keys = tuple(foreign_keys_list)
        filtered_tables.append(
            TableSchema(table.name, columns, foreign_keys)
        )
        table_links.append(
            LinkedTable(
                table.name,
                table_scores[table.name],
                table_reasons[table.name],
                (
                    "lexical_match"
                    if table.name in direct_tables
                    else "foreign_key_closure"
                ),
            )
        )
        for column in columns:
            key = (table.name, column.name)
            reasons = (
                set(column_reasons[key])
                | selection_reasons.get(key, set())
            )
            column_links.append(
                LinkedColumn(
                    table.name,
                    column.name,
                    column_scores[key],
                    tuple(sorted(reasons)),
                )
            )

    filtered = SchemaSnapshot(
        schema.db_id, schema.dialect, tuple(filtered_tables)
    )
    validate_canonical_schema(filtered)
    return SchemaLinkResult(
        schema=filtered,
        policy=policy,
        table_links=tuple(table_links),
        column_links=tuple(column_links),
        direct_table_names=direct_tables,
        closure_table_names=closure_tables,
        fallback_used=False,
        original_table_count=len(schema.tables),
        original_column_count=sum(
            len(table.columns) for table in schema.tables
        ),
    )


def evaluate_schema_linking(
    result: SchemaLinkResult,
    *,
    required_tables: Iterable[str],
    required_columns: Iterable[tuple[str, str]],
) -> SchemaLinkingMetrics:
    """Measure a link result when trusted required-schema annotations exist."""

    selected_tables = {
        table.name.casefold() for table in result.schema.tables
    }
    expected_tables = {name.casefold() for name in required_tables}
    selected_columns = {
        (table.name.casefold(), column.name.casefold())
        for table in result.schema.tables
        for column in table.columns
    }
    expected_columns = {
        (table.casefold(), column.casefold())
        for table, column in required_columns
    }
    table_precision, table_recall, table_f1 = _precision_recall_f1(
        selected_tables, expected_tables
    )
    column_precision, column_recall, column_f1 = _precision_recall_f1(
        selected_columns, expected_columns
    )
    return SchemaLinkingMetrics(
        table_precision,
        table_recall,
        table_f1,
        column_precision,
        column_recall,
        column_f1,
    )


def _precision_recall_f1(
    selected: set[object], expected: set[object]
) -> tuple[float, float, float]:
    hits = len(selected & expected)
    precision = hits / len(selected) if selected else float(not expected)
    recall = hits / len(expected) if expected else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return precision, recall, f1

