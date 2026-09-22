from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any


FREEZE_MANIFEST_VERSION = "run001-freeze-v2"
FINAL_ARMS = ("B0", "B1", "B2", "B4", "B6", "B6R")
ARM_CONFIGS = {
    "B0": ("configs/experiments/exp001-b0.toml", "configs/final/final002-b0-test.toml"),
    "B1": ("configs/experiments/exp001-b1.toml", "configs/final/final002-b1-test.toml"),
    "B2": ("configs/experiments/exp002-b2.toml", "configs/final/final002-b2-test.toml"),
    "B4": ("configs/experiments/exp006-b4.toml", "configs/final/final002-b4-test.toml"),
    "B6": ("configs/experiments/exp003-b6.toml", "configs/final/final002-b6-test.toml"),
    "B6R": ("configs/experiments/exp004-b6r.toml", "configs/final/final002-b6r-test.toml"),
}
EXPECTED_COMPARISONS = (
    ("B0", "B1"),
    ("B1", "B2"),
    ("B2", "B4"),
    ("B2", "B6"),
    ("B6", "B6R"),
    ("B1", "B6R"),
)
RESOURCE_GLOBS = (
    "src/text2sql/**/*.py",
    "tests/**/*.py",
    "configs/datasets/*",
    "configs/experiments/*",
    "configs/optimization/*",
    "configs/security/*",
    "configs/final/*.toml",
    "configs/*.yaml",
    "data/fixtures/security*",
)
RESOURCE_FILES = (
    "pyproject.toml",
    "requirements.lock",
    "docs/final-evaluation-v2.md",
    "docs/security-evaluation.md",
    "scripts/run_final_evaluation_v2.sh",
)


class FreezeError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise FreezeError(f"Invalid or missing TOML resource: {path}") from error


def _validate_plan(plan: dict[str, Any]) -> None:
    if (
        plan.get("schema_version") != 1
        or plan.get("freeze_id") != "run001-final-evaluation-v2"
        or plan.get("status") != "frozen"
        or plan.get("held_out_split") != "test"
        or plan.get("held_out_examples") != 104
        or tuple(plan.get("selected_arms", ())) != FINAL_ARMS
        or plan.get("primary_arm") != "B6R"
        or plan.get("security_arm") != "S1"
        or plan.get("release_tag") != "text2sql-final-v2"
    ):
        raise FreezeError("Final statistical plan identity or selected arms changed")
    intervals = plan.get("confidence_intervals")
    tests = plan.get("hypothesis_tests")
    run_policy = plan.get("run_policy")
    if not isinstance(intervals, dict) or (
        intervals.get("method") != "paired_nonparametric_bootstrap_percentile"
        or intervals.get("confidence_level") != 0.95
        or intervals.get("replicates") != 10_000
        or intervals.get("seed") != 20_260_919
    ):
        raise FreezeError("Bootstrap protocol changed")
    if not isinstance(tests, dict) or (
        tests.get("method") != "exact_mcnemar_two_sided"
        or tests.get("multiple_comparison_correction") != "holm"
        or tests.get("familywise_alpha") != 0.05
    ):
        raise FreezeError("Paired hypothesis-test protocol changed")
    comparisons = plan.get("comparisons")
    if not isinstance(comparisons, list) or tuple(
        (item.get("control"), item.get("treatment"))
        for item in comparisons
        if isinstance(item, dict)
    ) != EXPECTED_COMPARISONS:
        raise FreezeError("Predeclared paired comparisons changed")
    if not isinstance(run_policy, dict) or (
        run_policy.get("provider_runs_per_arm") != 1
        or run_policy.get("retain_every_attempt") is not True
        or run_policy.get("allow_test_specific_prompt_changes") is not False
        or run_policy.get("allow_replacement_after_scoring") is not False
        or run_policy.get("score_only_after_all_predictions_complete") is not True
        or run_policy.get("open_held_out_split_once") is not True
        or run_policy.get("retry_rate_limits_without_prediction") is not True
        or run_policy.get("retry_transient_provider_errors_without_prediction") is not True
        or run_policy.get("resume_interrupted_attempts_without_prediction") is not True
        or run_policy.get("record_sanitized_operational_events") is not True
    ):
        raise FreezeError("Held-out run policy changed")


def _validate_arm_configs(root: Path) -> list[dict[str, str]]:
    arms: list[dict[str, str]] = []
    for arm in FINAL_ARMS:
        template_relative, final_relative = ARM_CONFIGS[arm]
        template_path = root / template_relative
        final_path = root / final_relative
        template = _read_toml(template_path)
        final = _read_toml(final_path)
        expected_id = f"final002-{arm.casefold()}-test-gpt-oss-120b-v2"
        if (
            template.get("baseline") != arm
            or template.get("split") != "development"
            or final.get("baseline") != arm
            or final.get("split") != "test"
            or final.get("experiment_id") != expected_id
        ):
            raise FreezeError(f"Invalid final configuration identity for {arm}")
        normalized_template = dict(template)
        normalized_final = dict(final)
        normalized_template["split"] = "test"
        normalized_template["experiment_id"] = expected_id
        normalized_template["max_retries"] = 0
        if normalized_template != normalized_final:
            raise FreezeError(
                f"Final {arm} config drifted from its development template"
            )
        arms.append({
            "arm": arm,
            "template_path": template_relative,
            "template_sha256": _sha256(template_path),
            "config_path": final_relative,
            "config_sha256": _sha256(final_path),
        })
    return arms


def _resource_paths(root: Path) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for pattern in RESOURCE_GLOBS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    paths.update(root / relative for relative in RESOURCE_FILES)
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FreezeError(f"Freeze resource is missing: {missing[0]}")
    return tuple(sorted(paths, key=lambda path: path.relative_to(root).as_posix()))


def _resource_records(root: Path) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in _resource_paths(root)
    ]


def _source_tree_sha256(resources: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for resource in resources:
        digest.update(resource["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(resource["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_freeze_manifest(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    plan_path = root / "configs/final/statistical-plan-v2.toml"
    plan = _read_toml(plan_path)
    _validate_plan(plan)
    arms = _validate_arm_configs(root)

    split_path = root / "configs/datasets/spider2-lite-sqlite-split-v1.json"
    try:
        split = json.loads(split_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FreezeError("Invalid frozen split manifest") from error
    development_ids = split.get("development_instance_ids")
    test_ids = split.get("test_instance_ids")
    if (
        not isinstance(development_ids, list)
        or not isinstance(test_ids, list)
        or len(development_ids) != 31
        or len(test_ids) != 104
        or set(development_ids) & set(test_ids)
    ):
        raise FreezeError("Frozen development/test split coverage changed")

    resources = _resource_records(root)
    return {
        "schema_version": 1,
        "manifest_version": FREEZE_MANIFEST_VERSION,
        "freeze_id": plan["freeze_id"],
        "status": "frozen",
        "release_tag": plan["release_tag"],
        "held_out_split": "test",
        "held_out_examples": 104,
        "development_examples": 31,
        "selected_arms": list(FINAL_ARMS),
        "primary_arm": "B6R",
        "security_arm": "S1",
        "statistical_plan": {
            "path": "configs/final/statistical-plan-v2.toml",
            "sha256": _sha256(plan_path),
            "bootstrap_replicates": 10_000,
            "bootstrap_seed": 20_260_919,
            "confidence_level": 0.95,
            "paired_test": "exact_mcnemar_two_sided",
            "multiple_comparison_correction": "holm",
        },
        "arms": arms,
        "dataset": {
            "protocol_id": split.get("protocol_id"),
            "split_manifest_path": split_path.relative_to(root).as_posix(),
            "split_manifest_sha256": _sha256(split_path),
        },
        "resources": resources,
        "resource_count": len(resources),
        "source_tree_sha256": _source_tree_sha256(resources),
        "held_out_accessed": False,
        "provider_calls": 0,
    }


def write_freeze_manifest(
    project_root: str | Path,
    output_path: str | Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    output = Path(output_path).resolve()
    if output.exists() and not replace:
        raise FreezeError("Freeze manifest already exists; use explicit replace")
    manifest = build_freeze_manifest(project_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_freeze_manifest(
    project_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FreezeError("Invalid or missing RUN-001 freeze manifest") from error
    current = build_freeze_manifest(project_root)
    if recorded != current:
        raise FreezeError(
            "RUN-001 freeze verification failed: a pinned resource changed"
        )
    return current
