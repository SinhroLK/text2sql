from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SEMANTIC_ERROR_SCHEMA_VERSION = 1
EXPECTED_ARM_ORDER = ("B1", "B6R", "B4", "B5")
TARGET_ARM = "B5"


class SemanticErrorAnalysisError(ValueError):
    """Raised when frozen SEM-001 inputs or labels violate the contract."""


@dataclass(frozen=True)
class ArtifactInput:
    path: str
    sha256: str


@dataclass(frozen=True)
class ArmInput:
    baseline: str
    predictions: ArtifactInput
    report: ArtifactInput


@dataclass(frozen=True)
class FailureLabel:
    primary: str
    secondary: tuple[str, ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "secondary": list(self.secondary),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class SemanticErrorSpec:
    analysis_id: str
    scope: str
    arm_order: tuple[str, ...]
    target_arm: str
    categories: tuple[str, ...]
    expected_example_ids: tuple[str, ...]
    arms: Mapping[str, ArmInput]
    labels: Mapping[str, FailureLabel]
    config_path: Path
    config_sha256: str


@dataclass(frozen=True)
class SemanticErrorAnalysis:
    analysis_id: str
    config_sha256: str
    inputs: Mapping[str, Mapping[str, Mapping[str, str]]]
    summary: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(record: Mapping[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SemanticErrorAnalysisError(
            f"{context} field {key!r} must be a non-empty string"
        )
    return value


def _load_artifact_input(
    record: Mapping[str, Any], context: str
) -> ArtifactInput:
    path = _required_string(record, "path", context)
    sha256 = _required_string(record, "sha256", context)
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise SemanticErrorAnalysisError(
            f"{context} sha256 must be a lowercase hexadecimal digest"
        )
    return ArtifactInput(path=path, sha256=sha256)


def load_semantic_error_spec(path: str | Path) -> SemanticErrorSpec:
    source = Path(path).expanduser().resolve()
    raw = source.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SemanticErrorAnalysisError(
            f"Invalid SEM-001 config JSON: {error.msg}"
        ) from error
    if not isinstance(payload, dict):
        raise SemanticErrorAnalysisError("SEM-001 config must be a JSON object")
    if payload.get("schema_version") != SEMANTIC_ERROR_SCHEMA_VERSION:
        raise SemanticErrorAnalysisError("Unsupported SEM-001 schema version")

    analysis_id = _required_string(payload, "analysis_id", "SEM-001 config")
    scope = _required_string(payload, "scope", "SEM-001 config")
    if scope != "development":
        raise SemanticErrorAnalysisError(
            "SEM-001 may analyze only the development scope"
        )
    arm_order_value = payload.get("arm_order")
    if (
        not isinstance(arm_order_value, list)
        or tuple(arm_order_value) != EXPECTED_ARM_ORDER
    ):
        raise SemanticErrorAnalysisError(
            f"SEM-001 arm_order must be {list(EXPECTED_ARM_ORDER)!r}"
        )
    target_arm = _required_string(payload, "target_arm", "SEM-001 config")
    if target_arm != TARGET_ARM:
        raise SemanticErrorAnalysisError("SEM-001 target_arm must be B5")

    categories_value = payload.get("categories")
    if (
        not isinstance(categories_value, list)
        or len(categories_value) < 3
        or any(not isinstance(item, str) or not item for item in categories_value)
        or len(set(categories_value)) != len(categories_value)
    ):
        raise SemanticErrorAnalysisError(
            "SEM-001 categories must contain at least three unique strings"
        )
    categories = tuple(categories_value)

    ids_value = payload.get("expected_example_ids")
    if (
        not isinstance(ids_value, list)
        or not ids_value
        or any(not isinstance(item, str) or not item for item in ids_value)
        or len(set(ids_value)) != len(ids_value)
        or ids_value != sorted(ids_value)
    ):
        raise SemanticErrorAnalysisError(
            "SEM-001 expected_example_ids must be unique and sorted"
        )
    expected_example_ids = tuple(ids_value)

    arms_value = payload.get("arms")
    if not isinstance(arms_value, dict) or set(arms_value) != set(EXPECTED_ARM_ORDER):
        raise SemanticErrorAnalysisError(
            "SEM-001 inputs must define exactly B1, B6R, B4, and B5"
        )
    arms: dict[str, ArmInput] = {}
    for baseline in EXPECTED_ARM_ORDER:
        value = arms_value[baseline]
        if not isinstance(value, dict):
            raise SemanticErrorAnalysisError(
                f"SEM-001 arm {baseline} must be an object"
            )
        declared_baseline = _required_string(
            value, "baseline", f"SEM-001 arm {baseline}"
        )
        if declared_baseline != baseline:
            raise SemanticErrorAnalysisError(
                f"SEM-001 arm {baseline} declares a different baseline"
            )
        predictions = value.get("predictions")
        report = value.get("report")
        if not isinstance(predictions, dict) or not isinstance(report, dict):
            raise SemanticErrorAnalysisError(
                f"SEM-001 arm {baseline} must define predictions and report"
            )
        arms[baseline] = ArmInput(
            baseline=baseline,
            predictions=_load_artifact_input(
                predictions, f"SEM-001 arm {baseline} predictions"
            ),
            report=_load_artifact_input(
                report, f"SEM-001 arm {baseline} report"
            ),
        )

    labels_value = payload.get("labels")
    if not isinstance(labels_value, dict):
        raise SemanticErrorAnalysisError("SEM-001 labels must be an object")
    labels: dict[str, FailureLabel] = {}
    for example_id, value in labels_value.items():
        if example_id not in expected_example_ids or not isinstance(value, dict):
            raise SemanticErrorAnalysisError(
                f"SEM-001 label has invalid example ID {example_id!r}"
            )
        primary = _required_string(
            value, "primary", f"SEM-001 label {example_id}"
        )
        secondary_value = value.get("secondary", [])
        if (
            primary not in categories
            or not isinstance(secondary_value, list)
            or len(secondary_value) > 1
            or any(item not in categories for item in secondary_value)
            or primary in secondary_value
        ):
            raise SemanticErrorAnalysisError(
                f"SEM-001 label {example_id} has invalid categories"
            )
        rationale = _required_string(
            value, "rationale", f"SEM-001 label {example_id}"
        )
        labels[example_id] = FailureLabel(
            primary=primary,
            secondary=tuple(secondary_value),
            rationale=rationale,
        )

    return SemanticErrorSpec(
        analysis_id=analysis_id,
        scope=scope,
        arm_order=tuple(arm_order_value),
        target_arm=target_arm,
        categories=categories,
        expected_example_ids=expected_example_ids,
        arms=arms,
        labels=labels,
        config_path=source,
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _resolve_input(project_root: Path, configured_path: str) -> Path:
    root = project_root.expanduser().resolve()
    resolved = (root / configured_path).resolve()
    if resolved != root and root not in resolved.parents:
        raise SemanticErrorAnalysisError(
            f"SEM-001 input escapes project root: {configured_path}"
        )
    if not resolved.is_file():
        raise SemanticErrorAnalysisError(
            f"SEM-001 input does not exist: {configured_path}"
        )
    return resolved


def _verify_input(
    project_root: Path, artifact: ArtifactInput, context: str
) -> Path:
    path = _resolve_input(project_root, artifact.path)
    actual = _sha256_path(path)
    if actual != artifact.sha256:
        raise SemanticErrorAnalysisError(
            f"{context} checksum mismatch: got {actual}, expected {artifact.sha256}"
        )
    return path


def _load_jsonl(path: Path, context: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise SemanticErrorAnalysisError(
                    f"{context} contains blank line {line_number}"
                )
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise SemanticErrorAnalysisError(
                    f"{context} contains invalid JSON on line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise SemanticErrorAnalysisError(
                    f"{context} line {line_number} is not an object"
                )
            records.append(value)
    return records


def _index_predictions(
    records: list[dict[str, Any]],
    baseline: str,
    expected_ids: set[str],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        example_id = record.get("example_id")
        if not isinstance(example_id, str) or example_id not in expected_ids:
            raise SemanticErrorAnalysisError(
                f"{baseline} predictions contain an ID outside SEM-001 scope"
            )
        if example_id in indexed:
            raise SemanticErrorAnalysisError(
                f"{baseline} predictions contain duplicate ID {example_id}"
            )
        if record.get("baseline") != baseline:
            raise SemanticErrorAnalysisError(
                f"{baseline} prediction {example_id} has wrong baseline"
            )
        db_id = record.get("db_id")
        sql = record.get("generated_sql")
        if (
            not isinstance(db_id, str)
            or not db_id
            or not isinstance(sql, str)
            or not sql.strip()
        ):
            raise SemanticErrorAnalysisError(
                f"{baseline} prediction {example_id} is incomplete"
            )
        indexed[example_id] = record
    if set(indexed) != expected_ids:
        raise SemanticErrorAnalysisError(
            f"{baseline} predictions do not have exact SEM-001 coverage"
        )
    return indexed


def _load_report(
    path: Path,
    baseline: str,
    expected_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SemanticErrorAnalysisError(
            f"{baseline} report contains invalid JSON"
        ) from error
    if not isinstance(payload, dict):
        raise SemanticErrorAnalysisError(f"{baseline} report must be an object")
    experiment = payload.get("experiment")
    evaluation = payload.get("evaluation")
    if not isinstance(experiment, dict) or not isinstance(evaluation, dict):
        raise SemanticErrorAnalysisError(
            f"{baseline} report lacks experiment/evaluation sections"
        )
    if experiment.get("baseline") != baseline:
        raise SemanticErrorAnalysisError(
            f"{baseline} report declares a different baseline"
        )
    if evaluation.get("split") != "development":
        raise SemanticErrorAnalysisError(
            f"{baseline} report is not development-only"
        )
    report_ids = evaluation.get("expected_ids")
    if not isinstance(report_ids, list) or set(report_ids) != expected_ids:
        raise SemanticErrorAnalysisError(
            f"{baseline} report expected IDs do not match SEM-001"
        )
    records = evaluation.get("records")
    if not isinstance(records, list):
        raise SemanticErrorAnalysisError(
            f"{baseline} report records must be a list"
        )
    indexed: dict[str, dict[str, Any]] = {}
    for wrapper in records:
        result = wrapper.get("result") if isinstance(wrapper, dict) else None
        example_id = result.get("example_id") if isinstance(result, dict) else None
        if not isinstance(example_id, str) or example_id not in expected_ids:
            raise SemanticErrorAnalysisError(
                f"{baseline} report contains an ID outside SEM-001 scope"
            )
        if example_id in indexed:
            raise SemanticErrorAnalysisError(
                f"{baseline} report contains duplicate ID {example_id}"
            )
        status = result.get("status")
        correct = result.get("correct")
        if (
            status not in {
                "correct",
                "result_mismatch",
                "generated_execution_error",
                "comparison_error",
            }
            or not isinstance(correct, bool)
            or correct != (status == "correct")
        ):
            raise SemanticErrorAnalysisError(
                f"{baseline} result {example_id} has inconsistent status"
            )
        indexed[example_id] = result
    if set(indexed) != expected_ids:
        raise SemanticErrorAnalysisError(
            f"{baseline} report does not have exact SEM-001 coverage"
        )
    if evaluation.get("total") != len(expected_ids):
        raise SemanticErrorAnalysisError(
            f"{baseline} report total does not match SEM-001 coverage"
        )
    return indexed, evaluation


def _question_from_prediction(record: Mapping[str, Any]) -> str | None:
    generation = record.get("generation")
    if not isinstance(generation, dict):
        return None
    question = generation.get("question")
    return question if isinstance(question, str) and question else None


def _result_view(result: Mapping[str, Any]) -> dict[str, Any]:
    generated = result.get("generated")
    reference = result.get("reference")
    if not isinstance(generated, dict) or not isinstance(reference, dict):
        raise SemanticErrorAnalysisError(
            "SEM-001 evaluation result lacks generated/reference metadata"
        )
    return {
        "correct": result["correct"],
        "status": result["status"],
        "error_category": result.get("error_category"),
        "error_message": result.get("error_message"),
        "generated_columns": generated.get("columns", []),
        "generated_row_count": generated.get("row_count", 0),
        "reference_columns": reference.get("columns", []),
        "reference_row_count": reference.get("row_count", 0),
    }


def run_semantic_error_analysis(
    spec: SemanticErrorSpec,
    project_root: str | Path,
) -> SemanticErrorAnalysis:
    root = Path(project_root).expanduser().resolve()
    expected_ids = set(spec.expected_example_ids)
    predictions: dict[str, dict[str, dict[str, Any]]] = {}
    evaluations: dict[str, dict[str, dict[str, Any]]] = {}
    input_identities: dict[str, dict[str, dict[str, str]]] = {}

    for baseline in spec.arm_order:
        arm = spec.arms[baseline]
        prediction_path = _verify_input(
            root, arm.predictions, f"{baseline} predictions"
        )
        report_path = _verify_input(root, arm.report, f"{baseline} report")
        predictions[baseline] = _index_predictions(
            _load_jsonl(prediction_path, f"{baseline} predictions"),
            baseline,
            expected_ids,
        )
        evaluations[baseline], _ = _load_report(
            report_path, baseline, expected_ids
        )
        input_identities[baseline] = {
            "predictions": {
                "path": arm.predictions.path,
                "sha256": arm.predictions.sha256,
            },
            "report": {
                "path": arm.report.path,
                "sha256": arm.report.sha256,
            },
        }

    target_failures = {
        example_id
        for example_id in expected_ids
        if not evaluations[spec.target_arm][example_id]["correct"]
    }
    if set(spec.labels) != target_failures:
        missing = sorted(target_failures - set(spec.labels))
        extra = sorted(set(spec.labels) - target_failures)
        raise SemanticErrorAnalysisError(
            "SEM-001 labels must cover exactly the B5 failures "
            f"(missing={missing}, extra={extra})"
        )

    records: list[dict[str, Any]] = []
    primary_counts: Counter[str] = Counter()
    behavior_counts: Counter[str] = Counter()
    transition_counts: Counter[str] = Counter()
    for example_id in spec.expected_example_ids:
        db_ids = {
            predictions[baseline][example_id]["db_id"]
            for baseline in spec.arm_order
        }
        if len(db_ids) != 1:
            raise SemanticErrorAnalysisError(
                f"Database mismatch across SEM-001 arms for {example_id}"
            )
        question = _question_from_prediction(predictions["B1"][example_id])
        if question is None:
            raise SemanticErrorAnalysisError(
                f"B1 prediction {example_id} lacks the canonical question"
            )
        for baseline in ("B6R", "B4"):
            other_question = _question_from_prediction(
                predictions[baseline][example_id]
            )
            if other_question != question:
                raise SemanticErrorAnalysisError(
                    f"Question mismatch across SEM-001 arms for {example_id}"
                )

        correct_arms = [
            baseline
            for baseline in spec.arm_order
            if evaluations[baseline][example_id]["correct"]
        ]
        if len(correct_arms) == len(spec.arm_order):
            behavior = "stable_correct"
        elif not correct_arms:
            behavior = "stable_failure"
        else:
            behavior = "prompt_sensitive"
        behavior_counts[behavior] += 1
        status_signature = " -> ".join(
            f"{baseline}:{evaluations[baseline][example_id]['status']}"
            for baseline in spec.arm_order
        )
        transition_counts[status_signature] += 1
        label = spec.labels.get(example_id)
        if label is not None:
            primary_counts[label.primary] += 1

        arm_views: dict[str, Any] = {}
        for baseline in spec.arm_order:
            result = evaluations[baseline][example_id]
            if result.get("db_id") != next(iter(db_ids)):
                raise SemanticErrorAnalysisError(
                    f"{baseline} evaluation database mismatch for {example_id}"
                )
            arm_views[baseline] = {
                "generated_sql": predictions[baseline][example_id][
                    "generated_sql"
                ],
                "evaluation": _result_view(result),
            }
        records.append(
            {
                "schema_version": SEMANTIC_ERROR_SCHEMA_VERSION,
                "analysis_id": spec.analysis_id,
                "scope": spec.scope,
                "example_id": example_id,
                "db_id": next(iter(db_ids)),
                "question": question,
                "behavior": behavior,
                "correct_arms": correct_arms,
                "status_signature": status_signature,
                "arms": arm_views,
                "b5_failure_label": (
                    None if label is None else label.to_dict()
                ),
            }
        )

    dominant = [
        {"category": category, "count": count}
        for category, count in sorted(
            primary_counts.items(), key=lambda item: (-item[1], item[0])
        )[:3]
    ]
    if len(dominant) < 3:
        raise SemanticErrorAnalysisError(
            "SEM-001 must identify at least three dominant primary categories"
        )
    summary = {
        "scope": spec.scope,
        "total_examples": len(records),
        "target_arm": spec.target_arm,
        "target_failures": len(target_failures),
        "target_correct": len(records) - len(target_failures),
        "exact_id_coverage": True,
        "provider_calls": 0,
        "gold_sql_used": False,
        "test_examples_used": 0,
        "behavior_counts": dict(sorted(behavior_counts.items())),
        "primary_category_counts": dict(
            sorted(primary_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "dominant_primary_categories": dominant,
        "status_transition_counts": dict(sorted(transition_counts.items())),
    }
    return SemanticErrorAnalysis(
        analysis_id=spec.analysis_id,
        config_sha256=spec.config_sha256,
        inputs=input_identities,
        summary=summary,
        records=tuple(records),
    )


def _jsonl_bytes(records: tuple[Mapping[str, Any], ...]) -> bytes:
    lines = [
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _escape_markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_bytes(
    analysis: SemanticErrorAnalysis, jsonl_sha256: str
) -> bytes:
    summary = analysis.summary
    lines = [
        "# SEM-001 paired semantic-error analysis",
        "",
        f"- Analysis: `{analysis.analysis_id}`",
        f"- Config SHA-256: `{analysis.config_sha256}`",
        f"- Corpus JSONL SHA-256: `{jsonl_sha256}`",
        f"- Scope: {summary['scope']} ({summary['total_examples']} examples)",
        f"- B5: {summary['target_correct']} correct / "
        f"{summary['target_failures']} failures",
        "- Provider calls: 0",
        "- Gold SQL used: no",
        "- Spider2 test examples used: 0",
        "",
        "## Frozen inputs",
        "",
        "| Arm | Predictions SHA-256 | Report SHA-256 |",
        "|---|---|---|",
    ]
    for baseline in EXPECTED_ARM_ORDER:
        identity = analysis.inputs[baseline]
        lines.append(
            f"| {baseline} | `{identity['predictions']['sha256']}` | "
            f"`{identity['report']['sha256']}` |"
        )
    lines.extend(
        [
            "",
            "## Dominant B5 failure categories",
            "",
            "| Rank | Primary category | Failures |",
            "|---:|---|---:|",
        ]
    )
    for rank, item in enumerate(
        summary["dominant_primary_categories"], start=1
    ):
        lines.append(f"| {rank} | {item['category']} | {item['count']} |")
    lines.extend(
        [
            "",
            "All primary-category counts:",
            "",
            "| Category | Failures |",
            "|---|---:|",
        ]
    )
    for category, count in summary["primary_category_counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(
        [
            "",
            f"## Paired {summary['total_examples']}-example matrix",
            "",
            "| ID | DB | B1 | B6R | B4 | B5 | Behavior | Primary | Secondary | Rationale |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for record in analysis.records:
        label = record["b5_failure_label"]
        primary = "" if label is None else label["primary"]
        secondary = (
            "" if label is None else ", ".join(label["secondary"])
        )
        rationale = "" if label is None else label["rationale"]
        statuses = [
            record["arms"][baseline]["evaluation"]["status"]
            for baseline in EXPECTED_ARM_ORDER
        ]
        lines.append(
            "| "
            + " | ".join(
                _escape_markdown(value)
                for value in (
                    record["example_id"],
                    record["db_id"],
                    *statuses,
                    record["behavior"],
                    primary,
                    secondary,
                    rationale,
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Development-only diagnostic labels are engineering evidence. "
            "They do not use or reconstruct protected gold SQL and do not "
            "replace EVAL-003.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def write_semantic_error_artifacts(
    analysis: SemanticErrorAnalysis,
    *,
    jsonl_path: str | Path,
    markdown_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    jsonl_output = Path(jsonl_path).expanduser().resolve()
    markdown_output = Path(markdown_path).expanduser().resolve()
    manifest_output = Path(manifest_path).expanduser().resolve()

    jsonl = _jsonl_bytes(analysis.records)
    jsonl_sha256 = hashlib.sha256(jsonl).hexdigest()
    markdown = _markdown_bytes(analysis, jsonl_sha256)
    markdown_sha256 = hashlib.sha256(markdown).hexdigest()
    _atomic_write(jsonl_output, jsonl)
    _atomic_write(markdown_output, markdown)

    manifest = {
        "schema_version": SEMANTIC_ERROR_SCHEMA_VERSION,
        "analysis_id": analysis.analysis_id,
        "config_sha256": analysis.config_sha256,
        "inputs": analysis.inputs,
        "summary": analysis.summary,
        "artifacts": {
            "jsonl": {
                "path": jsonl_output.name,
                "sha256": jsonl_sha256,
                "records": len(analysis.records),
            },
            "markdown": {
                "path": markdown_output.name,
                "sha256": markdown_sha256,
            },
        },
    }
    rendered_manifest = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(manifest_output, rendered_manifest)
    return {
        **manifest,
        "manifest_sha256": hashlib.sha256(rendered_manifest).hexdigest(),
    }
