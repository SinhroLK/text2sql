from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from text2sql.planning import SemanticPlan, ValidatedSemanticPlan, semantic_plan_sha256

from .index import (
    LoadedRetrievalIndex,
    RetrievalIndexEntry,
    RetrievalIndexError,
    normalize_retrieval_text,
    sha256_file,
)


if TYPE_CHECKING:
    from text2sql.planning.scoped_plan import ScopedSemanticPlan


STRUCTURAL_INDEX_VERSION = "sql-skeleton-operators-v1"
STRUCTURAL_RETRIEVAL_VERSION = "question-plan-hybrid-v1"

_TOKEN = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]|\b\d+(?:\.\d+)?\b|"
    r"<>|!=|<=|>=|==|\|\||[-+*/%=<>()\[\],.;]|[A-Za-z_][A-Za-z0-9_$]*",
    re.DOTALL,
)
_COMMENT = re.compile(r"^(?:--|/\*)")
_QUOTED = re.compile(r"^(?:'|\"|`|\[)")
_NUMBER = re.compile(r"^\d")
_KEYWORDS = frozenset(
    "select from where join inner left right full cross outer on as with recursive "
    "group by having order limit offset asc desc distinct all union intersect except "
    "case when then else end and or not in exists between like is null over partition "
    "rows range preceding following current row fetch first only values into cast collate "
    "natural using true false".split()
)
_FUNCTIONS = frozenset(
    "count sum avg min max total group_concat row_number rank dense_rank lag lead "
    "first_value last_value nth_value ntile strftime date time datetime julianday "
    "unixepoch extract coalesce round abs lower upper length substr printf".split()
)
_AGGREGATES = frozenset({"count", "sum", "avg", "min", "max", "total", "group_concat"})
_WINDOW_FUNCTIONS = frozenset(
    {"row_number", "rank", "dense_rank", "lag", "lead", "first_value", "last_value", "nth_value", "ntile"}
)
_TEMPORAL = frozenset(
    {"date", "time", "datetime", "julianday", "unixepoch", "strftime", "extract", "year", "month", "day", "week", "quarter", "hour", "minute", "second"}
)


@dataclass(frozen=True)
class StructuralRetrievalConfig:
    schema_version: int
    index_id: str
    structural_version: str
    source_dataset_id: str
    source_split: str
    source_index_id: str
    source_index_sha256: str
    source_manifest_sha256: str
    expected_entries: int
    max_results: int
    question_weight: float
    structure_weight: float
    minimum_structure_score: float
    max_sql_chars: int
    max_total_sql_chars: int
    require_structural_match: bool
    artifact_filename: str
    config_path: Path
    config_sha256: str


@dataclass(frozen=True)
class SQLStructuralSignature:
    join_count: int
    has_subquery: bool
    has_cte: bool
    has_aggregation: bool
    has_group_by: bool
    has_having: bool
    has_window: bool
    set_operation: str
    recursive: bool
    has_ordering: bool
    has_limit: bool
    has_temporal: bool
    has_distinct: bool

    def operator_tags(self) -> frozenset[str]:
        tags = {
            name
            for name in (
                "subquery", "cte", "aggregation", "group_by", "having", "window",
                "recursive", "ordering", "limit", "temporal", "distinct",
            )
            if getattr(self, f"has_{name}", getattr(self, name, False))
        }
        if self.set_operation != "none":
            tags.add(f"set:{self.set_operation}")
        return frozenset(tags)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StructuralIndexEntry:
    source: RetrievalIndexEntry
    skeleton: str
    skeleton_sha256: str
    signature: SQLStructuralSignature
    sql_chars: int

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "retrieval_id": self.source.retrieval_id,
            "source_ordinal": self.source.source_ordinal,
            "skeleton": self.skeleton,
            "skeleton_sha256": self.skeleton_sha256,
            "signature": self.signature.to_dict(),
            "sql_chars": self.sql_chars,
        }


@dataclass(frozen=True)
class LoadedStructuralIndex:
    entries: tuple[StructuralIndexEntry, ...]
    manifest: dict[str, Any]
    manifest_sha256: str | None = None


@dataclass(frozen=True)
class StructuralRankedEntry:
    entry: RetrievalIndexEntry
    rank: int
    question_score: float
    structure_score: float
    question_component: float
    structure_component: float
    total_score: float
    matched_tags: tuple[str, ...]
    missing_tags: tuple[str, ...]
    extra_tags: tuple[str, ...]
    target_join_count: int
    candidate_join_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "retrieval_id": self.entry.retrieval_id,
            "db_id": self.entry.db_id,
            "sql_chars": len(self.entry.sql),
            "scores": {
                "question": self.question_score,
                "structure": self.structure_score,
                "question_component": self.question_component,
                "structure_component": self.structure_component,
                "total": self.total_score,
            },
            "structure_audit": {
                "matched_tags": list(self.matched_tags),
                "missing_tags": list(self.missing_tags),
                "extra_tags": list(self.extra_tags),
                "target_join_count": self.target_join_count,
                "candidate_join_count": self.candidate_join_count,
            },
        }


@dataclass(frozen=True)
class StructuralRetrievalSelection:
    strategy: str
    target_signature: SQLStructuralSignature
    entries: tuple[StructuralRankedEntry, ...]
    total_sql_chars: int
    structurally_eligible: int
    rejected_too_long: int
    rejected_budget: int

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "k": len(self.entries),
            "target_signature": self.target_signature.to_dict(),
            "bounds": {
                "total_sql_chars": self.total_sql_chars,
                "structurally_eligible": self.structurally_eligible,
                "rejected_too_long": self.rejected_too_long,
                "rejected_budget": self.rejected_budget,
                "empty_due_to_no_structural_match": self.structurally_eligible == 0,
            },
            "selected": [entry.to_dict() for entry in self.entries],
        }


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if isinstance(value, bool) and expected in {int, float}:
        raise RetrievalIndexError(f"{key} must be {expected.__name__}")
    if expected is float and isinstance(value, int):
        return float(value)
    if not isinstance(value, expected):
        raise RetrievalIndexError(f"{key} must be {expected.__name__}")
    if expected is str and not value.strip():
        raise RetrievalIndexError(f"{key} must not be empty")
    return value


def _sha(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RetrievalIndexError(f"{label} must be a lowercase SHA-256")
    return value


def load_structural_retrieval_config(path: str | Path) -> StructuralRetrievalConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    source = _required(data, "source", dict)
    policy = _required(data, "policy", dict)
    artifact = _required(data, "artifact", dict)
    config = StructuralRetrievalConfig(
        schema_version=_required(data, "schema_version", int),
        index_id=_required(data, "index_id", str),
        structural_version=_required(data, "structural_version", str),
        source_dataset_id=_required(source, "dataset_id", str),
        source_split=_required(source, "split", str),
        source_index_id=_required(source, "index_id", str),
        source_index_sha256=_sha(_required(source, "index_sha256", str), "source index_sha256"),
        source_manifest_sha256=_sha(_required(source, "manifest_sha256", str), "source manifest_sha256"),
        expected_entries=_required(source, "expected_entries", int),
        max_results=_required(policy, "max_results", int),
        question_weight=_required(policy, "question_weight", float),
        structure_weight=_required(policy, "structure_weight", float),
        minimum_structure_score=_required(policy, "minimum_structure_score", float),
        max_sql_chars=_required(policy, "max_sql_chars", int),
        max_total_sql_chars=_required(policy, "max_total_sql_chars", int),
        require_structural_match=_required(policy, "require_structural_match", bool),
        artifact_filename=_required(artifact, "filename", str),
        config_path=config_path,
        config_sha256=sha256_file(config_path),
    )
    if config.schema_version != 1 or config.structural_version != STRUCTURAL_INDEX_VERSION:
        raise RetrievalIndexError("unsupported structural retrieval contract")
    if config.source_dataset_id != "spider1" or config.source_split != "train":
        raise RetrievalIndexError("structural retrieval source must be Spider 1.0 train")
    if config.expected_entries <= 0 or config.max_results <= 0:
        raise RetrievalIndexError("structural retrieval counts must be positive")
    if config.max_sql_chars <= 0 or config.max_total_sql_chars <= 0:
        raise RetrievalIndexError("structural retrieval SQL limits must be positive")
    if not math.isclose(config.question_weight + config.structure_weight, 1.0):
        raise RetrievalIndexError("structural retrieval weights must sum to 1")
    if min(config.question_weight, config.structure_weight) < 0:
        raise RetrievalIndexError("structural retrieval weights must be non-negative")
    if not 0.0 <= config.minimum_structure_score <= 1.0:
        raise RetrievalIndexError("minimum_structure_score must be between 0 and 1")
    if not config.require_structural_match:
        raise RetrievalIndexError("structural-match filtering is mandatory")
    return config


def _sql_tokens(sql: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(sql):
        value = match.group(0)
        if _COMMENT.match(value):
            continue
        if _QUOTED.match(value) or _NUMBER.match(value):
            tokens.append("literal" if value.startswith("'") or _NUMBER.match(value) else "identifier")
        else:
            tokens.append(value.casefold())
    return tuple(tokens)


def normalize_sql_skeleton(sql: str) -> str:
    skeleton: list[str] = []
    for token in _sql_tokens(sql):
        if token in {"literal", "identifier"} or token in _KEYWORDS or token in _FUNCTIONS:
            skeleton.append(token)
        elif re.fullmatch(r"[a-z_][a-z0-9_$]*", token):
            skeleton.append("identifier")
        else:
            skeleton.append(token)
    return " ".join(skeleton)


def _followed_by_open(tokens: Sequence[str], index: int) -> bool:
    return index + 1 < len(tokens) and tokens[index + 1] == "("


def extract_sql_structure(sql: str) -> SQLStructuralSignature:
    tokens = _sql_tokens(sql)
    words = set(tokens)
    set_operation = "none"
    for operator in ("union", "intersect", "except"):
        if operator in words:
            set_operation = "union_all" if operator == "union" and any(
                tokens[index:index + 2] == ("union", "all") for index in range(len(tokens) - 1)
            ) else operator
            break
    has_aggregation = any(token in _AGGREGATES and _followed_by_open(tokens, index) for index, token in enumerate(tokens))
    return SQLStructuralSignature(
        join_count=tokens.count("join"),
        has_subquery=tokens.count("select") > 1,
        has_cte="with" in words,
        has_aggregation=has_aggregation,
        has_group_by=any(tokens[index:index + 2] == ("group", "by") for index in range(len(tokens) - 1)),
        has_having="having" in words,
        has_window="over" in words or any(token in _WINDOW_FUNCTIONS and _followed_by_open(tokens, index) for index, token in enumerate(tokens)),
        set_operation=set_operation,
        recursive="with" in words and "recursive" in words,
        has_ordering=any(tokens[index:index + 2] == ("order", "by") for index in range(len(tokens) - 1)),
        has_limit="limit" in words or "fetch" in words,
        has_temporal=bool(words & _TEMPORAL),
        has_distinct="distinct" in words,
    )


def semantic_plan_structure(plan: SemanticPlan | ScopedSemanticPlan | ValidatedSemanticPlan) -> SQLStructuralSignature:
    semantic = plan.plan if isinstance(plan, ValidatedSemanticPlan) else plan
    from text2sql.planning.scoped_plan import ScopedSemanticPlan, SelectScope, SetScope, walk_scopes
    if isinstance(semantic, ScopedSemanticPlan):
        scopes = tuple(scope for _, scope in walk_scopes(semantic.root))
        signatures = tuple(semantic_plan_structure(scope.body) for scope in scopes if isinstance(scope, SelectScope))
        operators = {scope.operator for scope in scopes if isinstance(scope, SetScope)}
        # Match the pinned SQL scanner's operator priority and SELECT-count tag.
        operator = next((op for op in ("union_all", "union", "intersect", "except") if op in operators), "none")
        return SQLStructuralSignature(
            join_count=sum(item.join_count for item in signatures),
            has_subquery=len(signatures) > 1,
            has_cte=False,
            has_aggregation=any(item.has_aggregation for item in signatures),
            has_group_by=any(item.has_group_by for item in signatures),
            has_having=any(item.has_having for item in signatures),
            has_window=False,
            set_operation=operator,
            recursive=False,
            has_ordering=any(item.has_ordering for item in signatures) or any(scope.ordering for scope in scopes if isinstance(scope, SetScope)),
            has_limit=any(item.has_limit for item in signatures) or any(scope.limit is not None for scope in scopes if isinstance(scope, SetScope)),
            has_temporal=any(item.has_temporal for item in signatures),
            has_distinct=any(item.has_distinct for item in signatures),
        )
    predicates = (*semantic.filters, *semantic.having)
    temporal = semantic.temporal.grain != "none" or semantic.temporal.window is not None or any(
        predicate.operator == "relative_time" or predicate.value_kind == "relative_time"
        for predicate in predicates
    )
    return SQLStructuralSignature(
        join_count=len(semantic.joins),
        has_subquery=any(predicate.value_kind == "subquery" for predicate in predicates),
        has_cte=semantic.recursion,
        has_aggregation=bool(semantic.aggregations),
        has_group_by=bool(semantic.group_by),
        has_having=bool(semantic.having),
        has_window=any(
            aggregation.function in _WINDOW_FUNCTIONS for aggregation in semantic.aggregations
        ),
        set_operation=semantic.set_operation,
        recursive=semantic.recursion,
        has_ordering=bool(semantic.ordering),
        has_limit=semantic.limit is not None,
        has_temporal=temporal,
        has_distinct=any(aggregation.distinct for aggregation in semantic.aggregations),
    )


def _verify_source(config: StructuralRetrievalConfig, source: LoadedRetrievalIndex) -> None:
    manifest = source.manifest
    artifact = manifest.get("artifact", {})
    source_info = manifest.get("source", {})
    leakage = manifest.get("leakage_audit", {})
    if manifest.get("index_id") != config.source_index_id:
        raise RetrievalIndexError("source retrieval index_id does not match structural config")
    if artifact.get("sha256") != config.source_index_sha256:
        raise RetrievalIndexError("source retrieval index checksum does not match structural config")
    if source.manifest_sha256 != config.source_manifest_sha256:
        raise RetrievalIndexError("source retrieval manifest checksum does not match structural config")
    if source_info.get("dataset_id") != "spider1" or source_info.get("split") != "train":
        raise RetrievalIndexError("structural source is not Spider 1.0 train-only")
    if len(source.entries) != config.expected_entries:
        raise RetrievalIndexError("source retrieval entry count does not match structural config")
    required_zero = ("instance_id_overlaps", "database_overlaps", "normalized_question_overlaps")
    if leakage.get("source_split_verified_train") is not True or leakage.get("spider2_examples_allowed") is not False:
        raise RetrievalIndexError("source retrieval leakage policy is not safe")
    if any(leakage.get(field) != 0 for field in required_zero):
        raise RetrievalIndexError("source retrieval leakage audit contains overlap")


def serialize_structural_entries(entries: Iterable[StructuralIndexEntry]) -> bytes:
    return b"".join(
        (json.dumps(entry.to_record(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for entry in entries
    )


def _serialize_structural_manifest(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

def build_structural_index(config: StructuralRetrievalConfig, source: LoadedRetrievalIndex) -> LoadedStructuralIndex:
    _verify_source(config, source)
    entries: list[StructuralIndexEntry] = []
    counts: Counter[str] = Counter()
    for retrieval in source.entries:
        skeleton = normalize_sql_skeleton(retrieval.sql)
        signature = extract_sql_structure(retrieval.sql)
        for tag in signature.operator_tags():
            counts[tag] += 1
        counts["joins_total"] += signature.join_count
        entries.append(StructuralIndexEntry(
            source=retrieval,
            skeleton=skeleton,
            skeleton_sha256=hashlib.sha256(skeleton.encode("utf-8")).hexdigest(),
            signature=signature,
            sql_chars=len(retrieval.sql),
        ))
    serialized = serialize_structural_entries(entries)
    source_leakage = source.manifest["leakage_audit"]
    manifest = {
        "schema_version": 1,
        "index_id": config.index_id,
        "structural_version": STRUCTURAL_INDEX_VERSION,
        "config_sha256": config.config_sha256,
        "source": {
            "dataset_id": "spider1",
            "split": "train",
            "index_id": config.source_index_id,
            "index_sha256": config.source_index_sha256,
            "manifest_sha256": config.source_manifest_sha256,
        },
        "counts": {
            "entries": len(entries),
            "operator_tags": dict(sorted(counts.items())),
        },
        "policy": {
            "max_results": config.max_results,
            "question_weight": config.question_weight,
            "structure_weight": config.structure_weight,
            "minimum_structure_score": config.minimum_structure_score,
            "max_sql_chars": config.max_sql_chars,
            "max_total_sql_chars": config.max_total_sql_chars,
            "require_structural_match": True,
        },
        "leakage_audit": {
            "source_split_verified_train": True,
            "spider2_examples_allowed": False,
            "instance_id_overlaps": source_leakage["instance_id_overlaps"],
            "database_overlaps": source_leakage["database_overlaps"],
            "normalized_question_overlaps": source_leakage["normalized_question_overlaps"],
            "spider2_metadata_sha256": source_leakage["spider2_metadata_sha256"],
            "spider2_split_manifest_sha256": source_leakage["spider2_split_manifest_sha256"],
        },
        "artifact": {
            "filename": config.artifact_filename,
            "sha256": hashlib.sha256(serialized).hexdigest(),
            "format": "deterministic-jsonl",
            "ordering": "source-ordinal",
            "contains_question_or_sql": False,
        },
    }
    return LoadedStructuralIndex(tuple(entries), manifest, hashlib.sha256(_serialize_structural_manifest(manifest)).hexdigest())


def _write(path: Path, content: bytes, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content and not overwrite:
        raise FileExistsError(f"Refusing to replace existing artifact: {path}")
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)


def write_structural_index(index: LoadedStructuralIndex, output_dir: str | Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    directory = Path(output_dir)
    artifact_path = directory / str(index.manifest["artifact"]["filename"])
    manifest_path = directory / "structural-manifest.json"
    _write(artifact_path, serialize_structural_entries(index.entries), overwrite)
    _write(manifest_path, _serialize_structural_manifest(index.manifest), overwrite)
    return artifact_path, manifest_path


def load_verified_structural_index(
    artifact_path: str | Path,
    manifest_path: str | Path,
    source: LoadedRetrievalIndex,
    config: StructuralRetrievalConfig,
    *,
    expected_manifest_path: str | Path | None = None,
) -> LoadedStructuralIndex:
    _verify_source(config, source)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RetrievalIndexError(f"invalid structural manifest: {error}") from error
    if expected_manifest_path is not None:
        try:
            expected = json.loads(Path(expected_manifest_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RetrievalIndexError(f"invalid expected structural manifest: {error}") from error
        if manifest != expected:
            raise RetrievalIndexError("structural manifest does not match version-controlled contract")
    rebuilt = build_structural_index(config, source)
    if manifest != rebuilt.manifest:
        raise RetrievalIndexError("structural manifest does not match deterministic source derivation")
    artifact_file = Path(artifact_path)
    if sha256_file(artifact_file) != manifest["artifact"]["sha256"]:
        raise RetrievalIndexError("structural index artifact checksum mismatch")
    if artifact_file.read_bytes() != serialize_structural_entries(rebuilt.entries):
        raise RetrievalIndexError("structural index does not match deterministic source derivation")
    return LoadedStructuralIndex(rebuilt.entries, manifest, sha256_file(manifest_path))


def _tfidf(tokens: tuple[str, ...], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values())
    return {token: count / total * idf[token] for token, count in counts.items() if token in idf} if total else {}


def _norm(vector: dict[str, float]) -> float:
    return math.sqrt(sum(value * value for value in vector.values()))


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    left_norm, right_norm = _norm(left), _norm(right)
    if not left_norm or not right_norm:
        return 0.0
    return sum(value * right.get(token, 0.0) for token, value in left.items()) / (left_norm * right_norm)


def _structure_score(target: SQLStructuralSignature, candidate: SQLStructuralSignature) -> tuple[float, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    target_tags, candidate_tags = target.operator_tags(), candidate.operator_tags()
    union = target_tags | candidate_tags
    tag_score = len(target_tags & candidate_tags) / len(union) if union else 1.0
    join_score = 1.0 / (1.0 + abs(target.join_count - candidate.join_count))
    return (
        0.7 * tag_score + 0.3 * join_score,
        tuple(sorted(target_tags & candidate_tags)),
        tuple(sorted(target_tags - candidate_tags)),
        tuple(sorted(candidate_tags - target_tags)),
    )



def _has_structural_match(
    target: SQLStructuralSignature,
    candidate: SQLStructuralSignature,
    matched_tags: tuple[str, ...],
) -> bool:
    if matched_tags or (target.join_count > 0 and target.join_count == candidate.join_count):
        return True
    return not target.operator_tags() and not candidate.operator_tags() and target.join_count == candidate.join_count
class QuestionPlanHybridSelector:
    version = STRUCTURAL_RETRIEVAL_VERSION

    def __init__(self, index: LoadedStructuralIndex, config: StructuralRetrievalConfig) -> None:
        if not index.entries:
            raise RetrievalIndexError("structural retrieval index must not be empty")
        self.index = index
        self.config = config
        frequencies: Counter[str] = Counter()
        for entry in index.entries:
            frequencies.update(set(entry.source.question_tokens))
        total = len(index.entries)
        self._idf = {token: math.log((total + 1) / (frequency + 1)) + 1 for token, frequency in frequencies.items()}
        self._vectors = tuple(_tfidf(entry.source.question_tokens, self._idf) for entry in index.entries)

    def select(self, question: str, plan: ValidatedSemanticPlan) -> StructuralRetrievalSelection:
        query_tokens = tuple(normalize_retrieval_text(question).split())
        if not query_tokens:
            raise RetrievalIndexError("retrieval question must not be empty")
        if not isinstance(plan, ValidatedSemanticPlan):
            raise RetrievalIndexError("hybrid retrieval requires a validated semantic plan record")
        semantic = plan.plan
        if semantic_plan_sha256(semantic) != plan.plan_sha256:
            raise RetrievalIndexError("semantic plan hash does not match its validated record")
        if not re.fullmatch(r"[0-9a-f]{64}", plan.schema_evidence_sha256):
            raise RetrievalIndexError("semantic plan schema evidence hash is invalid")
        if semantic.question != question:
            raise RetrievalIndexError("semantic plan question does not match retrieval question")
        target = semantic_plan_structure(semantic)
        query_vector = _tfidf(query_tokens, self._idf)
        scored: list[tuple[float, float, float, str, StructuralIndexEntry, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
        for item, vector in zip(self.index.entries, self._vectors, strict=True):
            question_score = _cosine(query_vector, vector)
            structure_score, matched, missing, extra = _structure_score(target, item.signature)
            total = self.config.question_weight * question_score + self.config.structure_weight * structure_score
            scored.append((total, structure_score, question_score, item.source.retrieval_id, item, matched, missing, extra))
        scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        selected: list[StructuralRankedEntry] = []
        total_chars = structurally_eligible = rejected_too_long = rejected_budget = 0
        for total, structural, question_score, _, item, matched, missing, extra in scored:
            if structural < self.config.minimum_structure_score or not _has_structural_match(target, item.signature, matched):
                continue
            structurally_eligible += 1
            if item.sql_chars > self.config.max_sql_chars:
                rejected_too_long += 1
                continue
            if total_chars + item.sql_chars > self.config.max_total_sql_chars:
                rejected_budget += 1
                continue
            total_chars += item.sql_chars
            selected.append(StructuralRankedEntry(
                entry=item.source,
                rank=len(selected) + 1,
                question_score=round(question_score, 12),
                structure_score=round(structural, 12),
                question_component=round(self.config.question_weight * question_score, 12),
                structure_component=round(self.config.structure_weight * structural, 12),
                total_score=round(total, 12),
                matched_tags=matched,
                missing_tags=missing,
                extra_tags=extra,
                target_join_count=target.join_count,
                candidate_join_count=item.signature.join_count,
            ))
            if len(selected) == self.config.max_results:
                break
        return StructuralRetrievalSelection(
            strategy=self.version,
            target_signature=target,
            entries=tuple(selected),
            total_sql_chars=total_chars,
            structurally_eligible=structurally_eligible,
            rejected_too_long=rejected_too_long,
            rejected_budget=rejected_budget,
        )


def build_per_target_retrieval_audit(
    selector: QuestionPlanHybridSelector,
    targets: Iterable[tuple[str, str, str, ValidatedSemanticPlan]],
    *,
    scope: str,
) -> dict[str, object]:
    if scope not in {"fixture", "development"}:
        raise RetrievalIndexError("retrieval audit scope must be fixture or development; test is forbidden")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for example_id, db_id, question, plan in targets:
        if not example_id or example_id in seen:
            raise RetrievalIndexError("retrieval audit target IDs must be non-empty and unique")
        seen.add(example_id)
        if plan.plan.db_id != db_id:
            raise RetrievalIndexError(f"retrieval audit db_id mismatch for {example_id}")
        selection = selector.select(question, plan)
        records.append({
            "example_id": example_id,
            "db_id": db_id,
            "question_sha256": hashlib.sha256(normalize_retrieval_text(question).encode()).hexdigest(),
            "plan_sha256": plan.plan_sha256,
            "schema_evidence_sha256": plan.schema_evidence_sha256,
            "retrieval": selection.to_dict(),
        })
    return {
        "schema_version": 1,
        "audit_version": "ret003-per-target-audit-v1",
        "scope": scope,
        "gold_sql_used": False,
        "test_targets_allowed": False,
        "source_structural_manifest_sha256": selector.index.manifest_sha256,
        "counts": {
            "targets": len(records),
            "targets_with_demonstrations": sum(bool(record["retrieval"]["selected"]) for record in records),  # type: ignore[index]
        },
        "targets": records,
    }
