from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .freeze import EXPECTED_COMPARISONS, FINAL_ARMS


ANALYSIS_SCHEMA_VERSION = 1
EXECUTABLE_STATUSES = frozenset({"correct", "result_mismatch"})


class AnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class ArmObservations:
    arm: str
    experiment_id: str
    report_path: str
    report_sha256: str
    example_ids: tuple[str, ...]
    correct: tuple[int, ...]
    executable: tuple[int, ...]
    input_tokens: int
    output_tokens: int


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise AnalysisError("Cannot calculate a percentile from no observations")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    confidence_level: float,
    replicates: int,
    seed: int,
) -> tuple[float, float]:
    observations = tuple(float(value) for value in values)
    if not observations:
        raise AnalysisError("Bootstrap requires at least one observation")
    if not 0.0 < confidence_level < 1.0:
        raise AnalysisError("confidence_level must be between zero and one")
    if replicates <= 0:
        raise AnalysisError("replicates must be positive")
    generator = random.Random(seed)
    count = len(observations)
    estimates = [
        sum(observations[generator.randrange(count)] for _ in range(count)) / count
        for _ in range(replicates)
    ]
    tail = (1.0 - confidence_level) / 2.0
    return (_percentile(estimates, tail), _percentile(estimates, 1.0 - tail))


def exact_mcnemar_two_sided(
    control: Sequence[int], treatment: Sequence[int]
) -> dict[str, Any]:
    if len(control) != len(treatment) or not control:
        raise AnalysisError("McNemar inputs must be non-empty paired observations")
    control_only = sum(
        left == 1 and right == 0 for left, right in zip(control, treatment)
    )
    treatment_only = sum(
        left == 0 and right == 1 for left, right in zip(control, treatment)
    )
    discordant = control_only + treatment_only
    if discordant == 0:
        p_value = 1.0
    else:
        lower = min(control_only, treatment_only)
        tail = sum(math.comb(discordant, k) for k in range(lower + 1))
        p_value = min(1.0, 2.0 * tail / (2**discordant))
    return {
        "control_only_correct": control_only,
        "treatment_only_correct": treatment_only,
        "discordant_pairs": discordant,
        "p_value": p_value,
    }


def holm_adjust(
    p_values: Mapping[str, float], *, alpha: float
) -> dict[str, dict[str, Any]]:
    if not 0.0 < alpha < 1.0:
        raise AnalysisError("alpha must be between zero and one")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        if not 0.0 <= value <= 1.0:
            raise AnalysisError(f"Invalid p-value for {name}")
        running = max(running, min(1.0, (total - rank) * value))
        adjusted[name] = running
    return {
        name: {
            "adjusted_p_value": adjusted[name],
            "reject_at_familywise_alpha": adjusted[name] <= alpha,
        }
        for name in p_values
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"Invalid or missing JSON report: {path}") from error
    if not isinstance(payload, dict):
        raise AnalysisError(f"Report root must be an object: {path}")
    return payload


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AnalysisError(f"{label} must be a non-negative integer")
    return value


def load_arm_report(
    path: str | Path,
    *,
    arm: str,
    expected_ids: Sequence[str],
    expected_config_sha256: str | None = None,
) -> ArmObservations:
    report_path = Path(path)
    report = _read_json(report_path)
    experiment = report.get("experiment")
    evaluation = report.get("evaluation")
    generation = report.get("generation_summary")
    expected_experiment_id = (
        f"final001-{arm.casefold()}-test-gpt-oss-120b-v1"
    )
    if not isinstance(experiment, dict) or (
        experiment.get("baseline") != arm
        or experiment.get("split") != "test"
        or experiment.get("experiment_id") != expected_experiment_id
        or (
            expected_config_sha256 is not None
            and experiment.get("config_sha256")
            != expected_config_sha256
        )
    ):
        raise AnalysisError(
            f"{arm} report has the wrong frozen experiment identity"
        )
    if not isinstance(evaluation, dict) or not isinstance(generation, dict):
        raise AnalysisError(
            f"{arm} report is missing evaluation or generation data"
        )
    records = evaluation.get("records")
    if not isinstance(records, list):
        raise AnalysisError(f"{arm} evaluation records must be a list")

    by_id: dict[str, dict[str, Any]] = {}
    for wrapper in records:
        if (
            not isinstance(wrapper, dict)
            or not isinstance(wrapper.get("result"), dict)
        ):
            raise AnalysisError(
                f"{arm} contains a malformed evaluation record"
            )
        result = wrapper["result"]
        example_id = result.get("example_id")
        if not isinstance(example_id, str) or example_id in by_id:
            raise AnalysisError(
                f"{arm} contains a missing or duplicate example ID"
            )
        correct = result.get("correct")
        status = result.get("status")
        if type(correct) is not bool or not isinstance(status, str):
            raise AnalysisError(f"{arm}/{example_id} has an invalid outcome")
        if correct != (status == "correct"):
            raise AnalysisError(
                f"{arm}/{example_id} has inconsistent correctness fields"
            )
        by_id[example_id] = result

    ordered_ids = tuple(expected_ids)
    missing = sorted(set(ordered_ids) - set(by_id))
    extra = sorted(set(by_id) - set(ordered_ids))
    if missing or extra or len(by_id) != len(ordered_ids):
        raise AnalysisError(
            f"{arm} report coverage mismatch: missing={missing}, extra={extra}"
        )
    if (
        evaluation.get("split") != "test"
        or evaluation.get("total") != len(ordered_ids)
        or tuple(evaluation.get("expected_ids", ())) != ordered_ids
    ):
        raise AnalysisError(
            f"{arm} report summary does not cover the frozen test split"
        )

    correct_values = tuple(
        int(by_id[item]["correct"]) for item in ordered_ids
    )
    statuses = tuple(by_id[item]["status"] for item in ordered_ids)
    executable_values = tuple(
        int(status in EXECUTABLE_STATUSES) for status in statuses
    )
    recorded_correct = sum(correct_values)
    recorded_incorrect = sum(
        status == "result_mismatch" for status in statuses
    )
    recorded_execution_errors = sum(
        status.endswith("execution_error") for status in statuses
    )
    recorded_comparison_errors = sum(
        status == "comparison_error" for status in statuses
    )
    if (
        evaluation.get("evaluated")
        != recorded_correct + recorded_incorrect
        or evaluation.get("correct") != recorded_correct
        or evaluation.get("incorrect") != recorded_incorrect
        or evaluation.get("execution_errors") != recorded_execution_errors
        or evaluation.get("comparison_errors")
        != recorded_comparison_errors
        or (
            recorded_correct
            + recorded_incorrect
            + recorded_execution_errors
            + recorded_comparison_errors
            != len(ordered_ids)
        )
        or not math.isclose(
            float(evaluation.get("execution_accuracy", -1.0)),
            recorded_correct / len(ordered_ids),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise AnalysisError(
            f"{arm} aggregate metrics disagree with its records"
        )

    return ArmObservations(
        arm=arm,
        experiment_id=expected_experiment_id,
        report_path=report_path.as_posix(),
        report_sha256=_sha256(report_path),
        example_ids=ordered_ids,
        correct=correct_values,
        executable=executable_values,
        input_tokens=_integer(
            generation.get("input_tokens"), f"{arm} input_tokens"
        ),
        output_tokens=_integer(
            generation.get("output_tokens"), f"{arm} output_tokens"
        ),
    )


def analyze_observations(
    observations: Mapping[str, ArmObservations],
    *,
    comparisons: Sequence[tuple[str, str]],
    confidence_level: float,
    replicates: int,
    seed: int,
    alpha: float,
) -> dict[str, Any]:
    if not observations:
        raise AnalysisError("No arm observations were provided")
    ids = next(iter(observations.values())).example_ids
    if not ids or any(
        item.example_ids != ids for item in observations.values()
    ):
        raise AnalysisError(
            "All arms must contain the same ordered example IDs"
        )

    arms: list[dict[str, Any]] = []
    for arm, item in observations.items():
        accuracy_ci = bootstrap_mean_interval(
            item.correct,
            confidence_level=confidence_level,
            replicates=replicates,
            seed=seed,
        )
        executable_ci = bootstrap_mean_interval(
            item.executable,
            confidence_level=confidence_level,
            replicates=replicates,
            seed=seed,
        )
        arms.append({
            "arm": arm,
            "experiment_id": item.experiment_id,
            "report_path": item.report_path,
            "report_sha256": item.report_sha256,
            "total": len(ids),
            "correct": sum(item.correct),
            "execution_accuracy": sum(item.correct) / len(ids),
            "execution_accuracy_ci": {
                "lower": accuracy_ci[0], "upper": accuracy_ci[1]
            },
            "executable": sum(item.executable),
            "executable_query_rate": sum(item.executable) / len(ids),
            "executable_query_rate_ci": {
                "lower": executable_ci[0], "upper": executable_ci[1]
            },
            "input_tokens": item.input_tokens,
            "output_tokens": item.output_tokens,
        })

    comparison_rows: list[dict[str, Any]] = []
    raw_p_values: dict[str, float] = {}
    for control, treatment in comparisons:
        if control not in observations or treatment not in observations:
            raise AnalysisError(
                f"Missing arm for comparison {control}->{treatment}"
            )
        left = observations[control].correct
        right = observations[treatment].correct
        differences = tuple(r - l for l, r in zip(left, right))
        interval = bootstrap_mean_interval(
            differences,
            confidence_level=confidence_level,
            replicates=replicates,
            seed=seed,
        )
        mcnemar = exact_mcnemar_two_sided(left, right)
        name = f"{control}->{treatment}"
        raw_p_values[name] = mcnemar["p_value"]
        comparison_rows.append({
            "comparison": name,
            "control": control,
            "treatment": treatment,
            "accuracy_difference": sum(differences) / len(differences),
            "accuracy_difference_ci": {
                "lower": interval[0], "upper": interval[1]
            },
            **mcnemar,
        })

    adjusted = holm_adjust(raw_p_values, alpha=alpha)
    for row in comparison_rows:
        row.update(adjusted[row["comparison"]])
    return {"arms": arms, "comparisons": comparison_rows}


def analyze_reports(
    *,
    project_root: str | Path,
    reports_dir: str | Path,
    plan_path: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    report_root = Path(reports_dir).resolve()
    plan_file = Path(plan_path).resolve()
    try:
        plan = tomllib.loads(plan_file.read_text(encoding="utf-8"))
        split = json.loads(
            (
                root
                / "configs/datasets/spider2-lite-sqlite-split-v1.json"
            ).read_text(encoding="utf-8")
        )
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise AnalysisError(
            "Unable to load the frozen plan or split manifest"
        ) from error

    arms = tuple(plan.get("selected_arms", ()))
    comparisons_data = plan.get("comparisons")
    if arms != FINAL_ARMS or not isinstance(comparisons_data, list):
        raise AnalysisError(
            "Statistical plan does not contain the frozen arms"
        )
    comparisons = tuple(
        (item.get("control"), item.get("treatment"))
        for item in comparisons_data
        if isinstance(item, dict)
    )
    if comparisons != EXPECTED_COMPARISONS:
        raise AnalysisError(
            "Statistical plan comparison family changed"
        )
    expected_ids = split.get("test_instance_ids")
    if not isinstance(expected_ids, list) or len(expected_ids) != 104:
        raise AnalysisError("Frozen held-out ID coverage changed")

    report_files = {
        arm: report_root
        / f"final001-{arm.casefold()}-test-report.json"
        for arm in arms
    }
    expected_paths = set(report_files.values())
    unexpected = sorted(
        path.name
        for path in report_root.glob("final001-*-test-report.json")
        if path not in expected_paths
    )
    if unexpected:
        raise AnalysisError(
            f"Unexpected final report files: {unexpected}"
        )
    observations = {
        arm: load_arm_report(
            path,
            arm=arm,
            expected_ids=expected_ids,
            expected_config_sha256=_sha256(
                root
                / f"configs/final/final001-{arm.casefold()}-test.toml"
            ),
        )
        for arm, path in report_files.items()
    }
    intervals = plan.get("confidence_intervals", {})
    tests = plan.get("hypothesis_tests", {})
    calculated = analyze_observations(
        observations,
        comparisons=comparisons,
        confidence_level=float(intervals.get("confidence_level")),
        replicates=int(intervals.get("replicates")),
        seed=int(intervals.get("seed")),
        alpha=float(tests.get("familywise_alpha")),
    )
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "freeze_id": plan.get("freeze_id"),
        "primary_arm": plan.get("primary_arm"),
        "held_out_split": "test",
        "example_count": len(expected_ids),
        "statistical_protocol": {
            "confidence_interval": intervals,
            "hypothesis_tests": tests,
        },
        **calculated,
    }


def write_analysis(
    analysis: Mapping[str, Any],
    *,
    json_path: str | Path,
    csv_path: str | Path,
) -> None:
    output_json = Path(json_path)
    output_csv = Path(csv_path)
    if output_json.exists() or output_csv.exists():
        raise AnalysisError(
            "Analysis outputs are immutable; choose new output paths"
        )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(analysis, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fields = (
        "arm",
        "total",
        "correct",
        "execution_accuracy",
        "execution_accuracy_ci_lower",
        "execution_accuracy_ci_upper",
        "executable",
        "executable_query_rate",
        "executable_query_rate_ci_lower",
        "executable_query_rate_ci_upper",
        "input_tokens",
        "output_tokens",
        "report_sha256",
    )
    with output_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for arm in analysis["arms"]:
            writer.writerow({
                **{key: arm[key] for key in fields if key in arm},
                "execution_accuracy_ci_lower": (
                    arm["execution_accuracy_ci"]["lower"]
                ),
                "execution_accuracy_ci_upper": (
                    arm["execution_accuracy_ci"]["upper"]
                ),
                "executable_query_rate_ci_lower": (
                    arm["executable_query_rate_ci"]["lower"]
                ),
                "executable_query_rate_ci_upper": (
                    arm["executable_query_rate_ci"]["upper"]
                ),
            })
