"""V3 relational scopes; legacy flat plans retain their original wire format."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

from text2sql.domain import SchemaSnapshot
from text2sql.schema import serialize_simple_schema
from .semantic_plan import (
    SEMANTIC_PLAN_V2_VERSION,
    SEMANTIC_PLAN_V3_VERSION,
    SemanticOrdering,
    SemanticOutput,
    SemanticPlan,
    SemanticPlanIssue,
    SemanticPlanParseError,
    SemanticPlanValidation,
    SemanticUncertainty,
    _PLAN_KEYS,
    _PREDICATE_KEYS,
    _expect_choice,
    _expect_list,
    _expect_object,
    _expect_string,
    _parse_ordering,
    _parse_uncertainty,
    _response_contract_template,
    parse_semantic_plan,
    validate_semantic_plan,
)

MAX_SCOPE_DEPTH = 4
MAX_SCOPE_COUNT = 16
_ENVELOPE_KEYS = frozenset({"plan_version", "db_id", "dialect", "question", "root", "uncertainties"})
_BODY_KEYS = _PLAN_KEYS - {"plan_version", "db_id", "dialect", "question", "uncertainties", "recursion", "set_operation"}
_SELECT_KEYS = _BODY_KEYS | {"kind", "scope_id"}
_SET_KEYS = frozenset({"kind", "scope_id", "operator", "left", "right", "ordering", "limit"})
_SET_OPERATORS = frozenset({"union", "union_all", "intersect", "except"})
_AGGREGATE_FUNCTIONS = frozenset({"count", "sum", "avg", "min", "max", "total", "group_concat"})
_PREDICATE_VALUES = {
    **{op: frozenset({"literal", "column", "subquery"}) for op in ("eq", "neq", "gt", "gte", "lt", "lte")},
    **{op: frozenset({"literal_list", "subquery"}) for op in ("in", "not_in")},
    **{op: frozenset({"none"}) for op in ("is_null", "is_not_null")},
    **{op: frozenset({"subquery"}) for op in ("exists", "not_exists")},
    **{op: frozenset({"literal"}) for op in ("contains", "starts_with", "ends_with")},
    "like": frozenset({"literal", "column"}),
    "between": frozenset({"range"}),
    "relative_time": frozenset({"relative_time"}),
}


@dataclass(frozen=True)
class PredicateSubquery:
    clause: str
    predicate_index: int
    query: QueryScope


@dataclass(frozen=True)
class SelectScope:
    scope_id: str
    body: SemanticPlan
    subqueries: tuple[PredicateSubquery, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = {key: value for key, value in self.body.to_dict().items() if key in _BODY_KEYS}
        payload.update(kind="select", scope_id=self.scope_id)
        for clause in ("filters", "having"):
            # asdict preserves tuples; JSON arrays must also be editable here.
            payload[clause] = [dict(predicate, subquery=None) for predicate in payload[clause]]
        for binding in self.subqueries:
            payload[binding.clause][binding.predicate_index]["subquery"] = binding.query.to_dict()
        return payload


@dataclass(frozen=True)
class SetScope:
    scope_id: str
    operator: str
    left: QueryScope
    right: QueryScope
    ordering: tuple[SemanticOrdering, ...]
    limit: int | None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return {
            "kind": "set", "scope_id": self.scope_id, "operator": self.operator,
            "left": self.left.to_dict(), "right": self.right.to_dict(),
            "ordering": [asdict(order) for order in self.ordering], "limit": self.limit,
        }


QueryScope = SelectScope | SetScope


@dataclass(frozen=True)
class ScopedSemanticPlan:
    plan_version: str
    db_id: str
    dialect: str
    question: str
    root: QueryScope
    uncertainties: tuple[SemanticUncertainty, ...]

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return {
            "plan_version": self.plan_version, "db_id": self.db_id,
            "dialect": self.dialect, "question": self.question,
            "root": self.root.to_dict(),
            "uncertainties": [asdict(item) for item in self.uncertainties],
        }


def walk_scopes(root: QueryScope, path: str = "$.root") -> Iterator[tuple[str, QueryScope]]:
    yield path, root
    if isinstance(root, SetScope):
        yield from walk_scopes(root.left, path + ".left")
        yield from walk_scopes(root.right, path + ".right")
    else:
        for binding in root.subqueries:
            yield from walk_scopes(
                binding.query, f"{path}.{binding.clause}[{binding.predicate_index}].subquery"
            )


def scope_outputs(scope: QueryScope) -> tuple[SemanticOutput, ...]:
    return scope.body.outputs if isinstance(scope, SelectScope) else scope_outputs(scope.left)


def _strict_body(body: SemanticPlan, path: str) -> None:
    for index, aggregation in enumerate(body.aggregations):
        if aggregation.function not in _AGGREGATE_FUNCTIONS:
            raise SemanticPlanParseError(f"{path}.aggregations[{index}] has an unsupported aggregate function")
        if aggregation.distinct and aggregation.column is None:
            raise SemanticPlanParseError(f"{path}.aggregations[{index}] DISTINCT requires a column")
    for clause in ("filters", "having"):
        for index, predicate in enumerate(getattr(body, clause)):
            where = f"{path}.{clause}[{index}]"
            if predicate.value_kind not in _PREDICATE_VALUES[predicate.operator]:
                raise SemanticPlanParseError(f"{where} operator/value_kind combination is invalid")
            expected_columns = 0 if predicate.operator in {"exists", "not_exists"} else 2 if predicate.value_kind == "column" else 1
            if len(predicate.columns) != expected_columns:
                raise SemanticPlanParseError(f"{where} requires {expected_columns} operand columns")
            if predicate.operator in {"like", "contains", "starts_with", "ends_with"} and predicate.value_kind == "literal" and not isinstance(predicate.value, str):
                raise SemanticPlanParseError(f"{where} requires a text pattern")


def parse_scoped_payload(payload: Any) -> ScopedSemanticPlan:
    envelope = _expect_object(payload, "$", _ENVELOPE_KEYS)
    if envelope["plan_version"] != SEMANTIC_PLAN_V3_VERSION:
        raise SemanticPlanParseError("Expected semantic-plan-v3")
    identity = {name: _expect_string(envelope[name], f"$.{name}") for name in ("db_id", "dialect", "question")}
    seen: set[str] = set()

    def parse_scope(value: Any, path: str, depth: int) -> QueryScope:
        if depth > MAX_SCOPE_DEPTH or len(seen) >= MAX_SCOPE_COUNT:
            raise SemanticPlanParseError(f"{path} exceeds scope depth/count limits")
        if not isinstance(value, dict):
            raise SemanticPlanParseError(f"{path} must be a select or set object")
        kind = _expect_choice(value.get("kind"), path + ".kind", frozenset({"select", "set"}))
        record = _expect_object(value, path, _SELECT_KEYS if kind == "select" else _SET_KEYS)
        scope_id = _expect_string(record["scope_id"], path + ".scope_id")
        if scope_id in seen:
            raise SemanticPlanParseError(f"{path}.scope_id duplicates {scope_id!r}")
        seen.add(scope_id)
        if kind == "set":
            operator = _expect_choice(record["operator"], path + ".operator", _SET_OPERATORS)
            left = parse_scope(record["left"], path + ".left", depth + 1)
            right = parse_scope(record["right"], path + ".right", depth + 1)
            ordering = tuple(_parse_ordering(item, f"{path}.ordering[{i}]") for i, item in enumerate(_expect_list(record["ordering"], path + ".ordering")))
            limit = record["limit"]
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
                raise SemanticPlanParseError(path + ".limit must be a positive integer or null")
            return SetScope(scope_id, operator, left, right, ordering, limit)
        body_payload = {key: record[key] for key in _BODY_KEYS}
        bindings: list[PredicateSubquery] = []
        for clause in ("filters", "having"):
            predicates = []
            for index, item in enumerate(_expect_list(record[clause], path + "." + clause)):
                where = f"{path}.{clause}[{index}]"
                predicate = _expect_object(item, where, _PREDICATE_KEYS | {"subquery"})
                nested = predicate["subquery"]
                if (predicate["value_kind"] == "subquery") != (nested is not None):
                    raise SemanticPlanParseError(f"{where}.subquery must be present exactly for subquery value_kind")
                if nested is not None:
                    bindings.append(PredicateSubquery(clause, index, parse_scope(nested, where + ".subquery", depth + 1)))
                predicates.append({key: predicate[key] for key in _PREDICATE_KEYS})
            body_payload[clause] = predicates
        body_payload.update(identity, plan_version=SEMANTIC_PLAN_V2_VERSION, recursion=False, set_operation="none", uncertainties=[])
        try:
            body = parse_semantic_plan(json.dumps(body_payload, allow_nan=False))
        except (SemanticPlanParseError, ValueError) as error:
            raise SemanticPlanParseError(f"{path}: {error}") from error
        _strict_body(body, path)
        return SelectScope(scope_id, body, tuple(bindings))

    root = parse_scope(envelope["root"], "$.root", 1)
    uncertainties = tuple(_parse_uncertainty(item, f"$.uncertainties[{i}]") for i, item in enumerate(_expect_list(envelope["uncertainties"], "$.uncertainties")))
    return ScopedSemanticPlan(SEMANTIC_PLAN_V3_VERSION, **identity, root=root, uncertainties=uncertainties)


def validate_scoped_plan(plan: ScopedSemanticPlan, schema: SchemaSnapshot, *, expected_question: str | None = None) -> SemanticPlanValidation:
    # Reparse so callers constructing/replacing dataclasses cannot bypass bounds,
    # strict predicate/function contracts, or the envelope's scope identities.
    try:
        canonical = parse_scoped_payload(json.loads(json.dumps(plan.to_dict(), allow_nan=False)))
    except (ValueError, TypeError, RecursionError) as error:
        return SemanticPlanValidation(False, (SemanticPlanIssue("invalid_scoped_plan", "$", str(error)),))
    if plan != canonical:
        return SemanticPlanValidation(False, (SemanticPlanIssue("scope_identity_mismatch", "$", "Scoped body identity differs from its envelope"),))
    issues: list[SemanticPlanIssue] = []
    for path, scope in walk_scopes(plan.root):
        if isinstance(scope, SelectScope):
            validation = validate_semantic_plan(scope.body, schema, expected_question=expected_question)
            issues.extend(SemanticPlanIssue(item.code, path + item.path[1:], item.message) for item in validation.issues)
            for binding in scope.subqueries:
                predicate = getattr(scope.body, binding.clause)[binding.predicate_index]
                if predicate.operator not in {"exists", "not_exists"} and len(scope_outputs(binding.query)) != 1:
                    issues.append(SemanticPlanIssue("subquery_output_arity", f"{path}.{binding.clause}[{binding.predicate_index}].subquery", "Scalar and IN subqueries must project exactly one column"))
        else:
            if len(scope_outputs(scope.left)) != len(scope_outputs(scope.right)):
                issues.append(SemanticPlanIssue("set_output_arity", path, "Set branches must project the same number of columns"))
            aliases = {item.alias for item in scope_outputs(scope.left) if item.alias is not None}
            for index, ordering in enumerate(scope.ordering):
                if ordering.target_kind != "output_alias" or ordering.alias not in aliases:
                    issues.append(SemanticPlanIssue("invalid_set_ordering", f"{path}.ordering[{index}]", "Set ordering must use a left-branch output alias"))
    return SemanticPlanValidation(not issues, tuple(issues))


def scoped_join_assumptions(plan: ScopedSemanticPlan) -> list[dict[str, Any]]:
    from dataclasses import asdict
    return [
        {"scope_id": scope.scope_id, "join_index": index, "left": asdict(join.left),
         "right": asdict(join.right), "rationale": join.rationale, "semantically_verified": False}
        for _, scope in walk_scopes(plan.root) if isinstance(scope, SelectScope)
        for index, join in enumerate(scope.body.joins) if join.evidence == "inferred_equality"
    ]


def build_scoped_plan_prompt(question: str, schema: SchemaSnapshot) -> str:
    body = _response_contract_template(question=question, db_id=schema.db_id, dialect=schema.dialect)
    root = {key: value for key, value in body.items() if key in _BODY_KEYS}
    root.update(kind="select", scope_id="main")
    root["joins"][0].update(evidence="declared_foreign_key|inferred_equality", rationale="Explain the equality relationship; inference is not a verified constraint.")
    root["filters"][0]["subquery"] = None
    contract = {"plan_version": SEMANTIC_PLAN_V3_VERSION, "db_id": schema.db_id, "dialect": schema.dialect, "question": question, "root": root, "uncertainties": []}
    alternative = {"kind": "set", "scope_id": "combined", "operator": "union|union_all|intersect|except", "left": "select or set scope object", "right": "select or set scope object", "ordering": [], "limit": None}
    return (
        "Prompt version: semantic-planner-v3\n"
        "Translate the question into a scoped relational plan. Do not write SQL.\n"
        "Return exactly one JSON object with the fields shown, replacing placeholders.\n"
        "Each select has its own sources, outputs, joins, filters, aggregations and ordering. "
        "Use exact schema identifiers. Connect sources only within the same select; do not join independent set branches. "
        "Each equality join needs declared_foreign_key or inferred_equality evidence and a nonempty rationale. "
        "Inferred equalities are assumptions, not verified constraints or cardinalities.\n"
        "Every filter/having predicate has subquery=null unless value_kind=subquery, in which case embed a complete select or set scope. "
        "Subqueries are uncorrelated: never reference an outer or sibling source. "
        "Scalar and IN subqueries project one column; EXISTS/NOT EXISTS use no outer operand columns. "
        "All filters in a scope are conjunctive. Do not encode OR or correlation in descriptions. "
        "Set branches must have equal output counts. Final set ordering uses a left-branch output alias. "
        "Use unique scope_id values everywhere, at most 16 scopes and depth 4. "
        "Self-join aliases, CTEs, recursion and analytic windows are not supported in this version. "
        "Aggregates: count, sum, avg, min, max, total, group_concat. Only count may omit a column; DISTINCT requires a column. "
        "Record unsupported or ambiguous requirements in uncertainties; never silently replace them.\n\n"
        f"Schema evidence:\n{serialize_simple_schema(schema)}\n\nQuestion:\n{question}\n\n"
        f"Response contract:\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n\n"
        f"Set scope alternative (left/right must be objects):\n{json.dumps(alternative, indent=2)}"
    )
