from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_GOLD_LIKE_FIELDS = {"gold_sql", "gold_query", "query", "sql"}


class RetrievalIndexError(ValueError):
    """Raised when a retrieval source, firewall, or artifact is invalid."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_retrieval_text(value: str) -> str:
    return " ".join(_TOKEN_PATTERN.findall(value.casefold()))


@dataclass(frozen=True)
class RetrievalIndexConfig:
    schema_version: int
    index_id: str
    source_dataset_id: str
    source_split: str
    dialect: str
    expected_examples: int
    expected_databases: int
    train_sha256: str
    tables_sha256: str
    spider2_metadata_sha256: str
    spider2_split_manifest_sha256: str
    artifact_filename: str
    origin: dict[str, str]


@dataclass(frozen=True)
class RetrievalIndexEntry:
    retrieval_id: str
    source_ordinal: int
    db_id: str
    question: str
    sql: str
    question_tokens: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "retrieval_id": self.retrieval_id,
            "source_dataset_id": "spider1",
            "source_split": "train",
            "source_ordinal": self.source_ordinal,
            "db_id": self.db_id,
            "dialect": "sqlite",
            "question": self.question,
            "sql": self.sql,
            "question_tokens": list(self.question_tokens),
        }


@dataclass(frozen=True)
class RetrievalLeakageFirewall:
    forbidden_instance_ids: frozenset[str]
    forbidden_db_ids: frozenset[str]
    forbidden_question_fingerprints: frozenset[str]
    metadata_sha256: str
    split_manifest_sha256: str


@dataclass(frozen=True)
class LoadedRetrievalIndex:
    entries: tuple[RetrievalIndexEntry, ...]
    manifest: dict[str, Any]
    manifest_sha256: str | None = None


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected):
        raise RetrievalIndexError(f"{key} must be {expected.__name__}")
    if expected is str and not value.strip():
        raise RetrievalIndexError(f"{key} must not be empty")
    return value


def _require_sha256(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RetrievalIndexError(f"{label} must be a lowercase SHA-256")
    return value


def load_retrieval_config(path: str | Path) -> RetrievalIndexConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    source = _required(data, "source", dict)
    firewall = _required(data, "spider2_firewall", dict)
    artifact = _required(data, "artifact", dict)
    policy = _required(data, "leakage_policy", dict)
    expected_policy = {
        "source_split_must_be_train": True,
        "spider2_examples_allowed": False,
        "spider2_databases_allowed": False,
        "spider2_question_overlap_allowed": False,
    }
    if policy != expected_policy:
        raise RetrievalIndexError("retrieval leakage policy is not the frozen safe policy")

    schema_version = _required(data, "schema_version", int)
    if schema_version != 1:
        raise RetrievalIndexError("unsupported retrieval config schema_version")
    source_split = _required(source, "split", str)
    if source_split != "train":
        raise RetrievalIndexError("retrieval source split must be train")
    source_dataset_id = _required(source, "dataset_id", str)
    if source_dataset_id != "spider1":
        raise RetrievalIndexError("retrieval source dataset must be spider1")

    origin = _required(data, "origin", dict)
    for key, value in origin.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise RetrievalIndexError("origin values must be non-empty strings")

    dialect = _required(source, "dialect", str)
    if dialect != "sqlite":
        raise RetrievalIndexError("Spider 1.0 retrieval dialect must be sqlite")

    return RetrievalIndexConfig(
        schema_version=schema_version,
        index_id=_required(data, "index_id", str),
        source_dataset_id=source_dataset_id,
        source_split=source_split,
        dialect=dialect,
        expected_examples=_required(source, "expected_examples", int),
        expected_databases=_required(source, "expected_databases", int),
        train_sha256=_require_sha256(_required(source, "train_sha256", str), "train_sha256"),
        tables_sha256=_require_sha256(_required(source, "tables_sha256", str), "tables_sha256"),
        spider2_metadata_sha256=_require_sha256(
            _required(firewall, "metadata_sha256", str), "metadata_sha256"
        ),
        spider2_split_manifest_sha256=_require_sha256(
            _required(firewall, "split_manifest_sha256", str),
            "split_manifest_sha256",
        ),
        artifact_filename=_required(artifact, "filename", str),
        origin=dict(sorted(origin.items())),
    )


def _verify_checksum(path: Path, expected: str, label: str) -> str:
    actual = sha256_file(path)
    if actual != expected:
        raise RetrievalIndexError(
            f"{label} checksum mismatch: expected {expected}, got {actual}"
        )
    return actual


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RetrievalIndexError(f"invalid {label}: {error}") from error


def build_spider2_leakage_firewall(
    metadata_jsonl: str | Path,
    split_manifest_path: str | Path,
    *,
    expected_metadata_sha256: str,
    expected_split_manifest_sha256: str,
) -> RetrievalLeakageFirewall:
    metadata_path = Path(metadata_jsonl)
    split_path = Path(split_manifest_path)
    metadata_sha256 = _verify_checksum(
        metadata_path, expected_metadata_sha256, "Spider2 metadata"
    )
    split_sha256 = _verify_checksum(
        split_path, expected_split_manifest_sha256, "Spider2 split manifest"
    )
    split = _load_json(split_path, "Spider2 split manifest")
    if not isinstance(split, dict):
        raise RetrievalIndexError("Spider2 split manifest must be an object")

    development_ids = set(_required(split, "development_instance_ids", list))
    test_ids = set(_required(split, "test_instance_ids", list))
    development_dbs = set(_required(split, "development_db_ids", list))
    test_dbs = set(_required(split, "test_db_ids", list))
    if development_ids & test_ids or development_dbs & test_dbs:
        raise RetrievalIndexError("Spider2 split manifest is not disjoint")
    expected_ids = development_ids | test_ids
    expected_dbs = development_dbs | test_dbs

    records: list[dict[str, Any]] = []
    try:
        with metadata_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise RetrievalIndexError(
                        f"Spider2 metadata line {line_number} is empty"
                    )
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise RetrievalIndexError(
                        f"Spider2 metadata line {line_number} must be an object"
                    )
                records.append(record)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RetrievalIndexError(f"invalid Spider2 metadata: {error}") from error

    ids: set[str] = set()
    questions: set[str] = set()
    for record in records:
        gold_fields = _GOLD_LIKE_FIELDS & set(record)
        if gold_fields:
            raise RetrievalIndexError(
                f"Spider2 firewall metadata contains gold-like fields: {sorted(gold_fields)}"
            )
        example_id = _required(record, "example_id", str)
        db_id = _required(record, "db_id", str)
        split_name = _required(record, "split", str)
        question = _required(record, "question", str)
        if example_id in ids:
            raise RetrievalIndexError(f"duplicate Spider2 metadata ID: {example_id}")
        ids.add(example_id)
        expected_split = "development" if example_id in development_ids else "test"
        if example_id not in expected_ids or split_name != expected_split:
            raise RetrievalIndexError(f"Spider2 metadata split mismatch for {example_id}")
        allowed_dbs = development_dbs if split_name == "development" else test_dbs
        if db_id not in allowed_dbs:
            raise RetrievalIndexError(f"Spider2 metadata database mismatch for {example_id}")
        questions.add(normalize_retrieval_text(question))

    if ids != expected_ids:
        missing = sorted(expected_ids - ids)
        extra = sorted(ids - expected_ids)
        raise RetrievalIndexError(
            f"Spider2 firewall coverage mismatch: missing={missing}, extra={extra}"
        )
    return RetrievalLeakageFirewall(
        forbidden_instance_ids=frozenset(ids),
        forbidden_db_ids=frozenset(db.casefold() for db in expected_dbs),
        forbidden_question_fingerprints=frozenset(questions),
        metadata_sha256=metadata_sha256,
        split_manifest_sha256=split_sha256,
    )


def _source_records(path: Path) -> list[dict[str, Any]]:
    value = _load_json(path, "Spider 1.0 train source")
    if not isinstance(value, list):
        raise RetrievalIndexError("Spider 1.0 train source must be a JSON array")
    if not all(isinstance(record, dict) for record in value):
        raise RetrievalIndexError("Spider 1.0 train records must be objects")
    return value


def _schema_database_ids(path: Path) -> set[str]:
    value = _load_json(path, "Spider 1.0 tables source")
    if not isinstance(value, list):
        raise RetrievalIndexError("Spider 1.0 tables source must be a JSON array")
    database_ids: set[str] = set()
    for schema in value:
        if not isinstance(schema, dict):
            raise RetrievalIndexError("Spider 1.0 schema records must be objects")
        db_id = _required(schema, "db_id", str)
        if db_id in database_ids:
            raise RetrievalIndexError(f"duplicate Spider 1.0 schema: {db_id}")
        database_ids.add(db_id)
    return database_ids


def serialize_retrieval_entries(entries: Iterable[RetrievalIndexEntry]) -> bytes:
    return (
        "".join(
            json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for entry in entries
        )
    ).encode("utf-8")


def build_spider1_train_retrieval_index(
    train_json: str | Path,
    tables_json: str | Path,
    config_path: str | Path,
    firewall: RetrievalLeakageFirewall,
    *,
    expected_manifest_path: str | Path | None = None,
) -> LoadedRetrievalIndex:
    train_path = Path(train_json)
    tables_path = Path(tables_json)
    config_file = Path(config_path)
    config = load_retrieval_config(config_file)
    _verify_checksum(train_path, config.train_sha256, "Spider 1.0 train source")
    _verify_checksum(tables_path, config.tables_sha256, "Spider 1.0 tables source")
    if firewall.metadata_sha256 != config.spider2_metadata_sha256:
        raise RetrievalIndexError("Spider2 firewall metadata does not match config")
    if firewall.split_manifest_sha256 != config.spider2_split_manifest_sha256:
        raise RetrievalIndexError("Spider2 firewall split does not match config")

    records = _source_records(train_path)
    schema_db_ids = _schema_database_ids(tables_path)
    if len(records) != config.expected_examples:
        raise RetrievalIndexError(
            f"Spider 1.0 train count mismatch: expected {config.expected_examples}, got {len(records)}"
        )

    entries: list[RetrievalIndexEntry] = []
    source_database_ids: set[str] = set()
    exact_triples: set[tuple[str, str, str]] = set()
    for ordinal, record in enumerate(records):
        db_id = _required(record, "db_id", str)
        question = _required(record, "question", str)
        sql = _required(record, "query", str)
        retrieval_id = f"spider1-train-{ordinal:05d}"
        fingerprint = normalize_retrieval_text(question)
        if db_id not in schema_db_ids:
            raise RetrievalIndexError(f"missing Spider 1.0 schema for database {db_id}")
        if retrieval_id in firewall.forbidden_instance_ids:
            raise RetrievalIndexError(f"Spider2 instance leaked into retrieval: {retrieval_id}")
        if db_id.casefold() in firewall.forbidden_db_ids:
            raise RetrievalIndexError(f"Spider2 database leaked into retrieval: {db_id}")
        if fingerprint in firewall.forbidden_question_fingerprints:
            raise RetrievalIndexError(
                f"Spider2 question leaked into retrieval at source ordinal {ordinal}"
            )
        tokens = tuple(fingerprint.split())
        if not tokens:
            raise RetrievalIndexError(f"empty normalized question at source ordinal {ordinal}")
        source_database_ids.add(db_id)
        exact_triples.add((db_id, question, sql))
        entries.append(
            RetrievalIndexEntry(
                retrieval_id=retrieval_id,
                source_ordinal=ordinal,
                db_id=db_id,
                question=question,
                sql=sql,
                question_tokens=tokens,
            )
        )

    if len(source_database_ids) != config.expected_databases:
        raise RetrievalIndexError(
            "Spider 1.0 train database count mismatch: "
            f"expected {config.expected_databases}, got {len(source_database_ids)}"
        )

    serialized = serialize_retrieval_entries(entries)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "index_id": config.index_id,
        "config_sha256": sha256_file(config_file),
        "source": {
            "dataset_id": config.source_dataset_id,
            "split": config.source_split,
            "dialect": config.dialect,
            "train_sha256": config.train_sha256,
            "tables_sha256": config.tables_sha256,
            "origin": config.origin,
        },
        "counts": {
            "entries": len(entries),
            "databases": len(source_database_ids),
            "duplicate_exact_source_triples": len(entries) - len(exact_triples),
        },
        "leakage_audit": {
            "forbidden_spider2_instances": len(firewall.forbidden_instance_ids),
            "forbidden_spider2_databases": len(firewall.forbidden_db_ids),
            "forbidden_spider2_questions": len(
                firewall.forbidden_question_fingerprints
            ),
            "instance_id_overlaps": 0,
            "database_overlaps": 0,
            "normalized_question_overlaps": 0,
            "spider2_examples_allowed": False,
            "source_split_verified_train": True,
            "spider2_metadata_sha256": firewall.metadata_sha256,
            "spider2_split_manifest_sha256": firewall.split_manifest_sha256,
        },
        "artifact": {
            "filename": config.artifact_filename,
            "sha256": hashlib.sha256(serialized).hexdigest(),
            "format": "deterministic-jsonl",
            "ordering": "source-ordinal",
        },
    }
    if expected_manifest_path is not None:
        expected = _load_json(Path(expected_manifest_path), "expected retrieval manifest")
        if manifest != expected:
            raise RetrievalIndexError(
                "generated retrieval manifest does not match the version-controlled contract"
            )
    return LoadedRetrievalIndex(entries=tuple(entries), manifest=manifest)


def _write_artifact(path: Path, content: bytes, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content:
            return
        if not overwrite:
            raise FileExistsError(f"Refusing to replace existing artifact: {path}")
    path.write_bytes(content)


def write_retrieval_index(
    index: LoadedRetrievalIndex,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    directory = Path(output_dir)
    index_path = directory / str(index.manifest["artifact"]["filename"])
    manifest_path = directory / "retrieval-manifest.json"
    serialized = serialize_retrieval_entries(index.entries)
    manifest_bytes = (
        json.dumps(index.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_artifact(index_path, serialized, overwrite)
    _write_artifact(manifest_path, manifest_bytes, overwrite)
    return index_path, manifest_path


def load_verified_retrieval_index(
    index_path: str | Path,
    manifest_path: str | Path,
    *,
    expected_manifest_path: str | Path | None = None,
) -> LoadedRetrievalIndex:
    artifact_path = Path(index_path)
    manifest = _load_json(Path(manifest_path), "retrieval manifest")
    if not isinstance(manifest, dict):
        raise RetrievalIndexError("retrieval manifest must be an object")
    if expected_manifest_path is not None:
        expected_manifest = _load_json(
            Path(expected_manifest_path), "expected retrieval manifest"
        )
        if manifest != expected_manifest:
            raise RetrievalIndexError(
                "retrieval manifest does not match the version-controlled contract"
            )
    if manifest.get("schema_version") != 1:
        raise RetrievalIndexError("unsupported retrieval manifest schema_version")
    source = _required(manifest, "source", dict)
    if source.get("dataset_id") != "spider1" or source.get("split") != "train":
        raise RetrievalIndexError("retrieval manifest is not Spider 1.0 train-only")
    artifact = _required(manifest, "artifact", dict)
    expected_sha256 = _require_sha256(
        _required(artifact, "sha256", str), "artifact sha256"
    )
    _verify_checksum(artifact_path, expected_sha256, "retrieval index artifact")

    entries: list[RetrievalIndexEntry] = []
    try:
        with artifact_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                record = json.loads(line)
                ordinal = line_number - 1
                expected_id = f"spider1-train-{ordinal:05d}"
                if (
                    record.get("schema_version") != 1
                    or record.get("source_ordinal") != ordinal
                    or record.get("dialect") != "sqlite"
                ):
                    raise RetrievalIndexError(
                        f"retrieval record contract mismatch at line {line_number}"
                    )
                if record.get("retrieval_id") != expected_id:
                    raise RetrievalIndexError(
                        f"retrieval ordering mismatch at line {line_number}"
                    )
                if record.get("source_dataset_id") != "spider1" or record.get(
                    "source_split"
                ) != "train":
                    raise RetrievalIndexError(
                        f"non-training source at retrieval line {line_number}"
                    )
                tokens = record.get("question_tokens")
                if not isinstance(tokens, list) or not all(
                    isinstance(token, str) and token for token in tokens
                ):
                    raise RetrievalIndexError(
                        f"invalid question tokens at retrieval line {line_number}"
                    )
                question = _required(record, "question", str)
                if tokens != normalize_retrieval_text(question).split():
                    raise RetrievalIndexError(
                        f"question token mismatch at retrieval line {line_number}"
                    )
                entries.append(
                    RetrievalIndexEntry(
                        retrieval_id=expected_id,
                        source_ordinal=ordinal,
                        db_id=_required(record, "db_id", str),
                        question=question,
                        sql=_required(record, "sql", str),
                        question_tokens=tuple(tokens),
                    )
                )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RetrievalIndexError(f"invalid retrieval index artifact: {error}") from error

    counts = _required(manifest, "counts", dict)
    if len(entries) != _required(counts, "entries", int):
        raise RetrievalIndexError("retrieval index count does not match manifest")
    leakage = _required(manifest, "leakage_audit", dict)
    if leakage.get("spider2_examples_allowed") is not False:
        raise RetrievalIndexError("retrieval manifest does not forbid Spider2 examples")
    return LoadedRetrievalIndex(
        entries=tuple(entries),
        manifest=manifest,
        manifest_sha256=sha256_file(manifest_path),
    )
