#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIRECTORY = CONTROLLER_DIRECTORY / "config"
DEFAULT_MONITORING_DIRECTORY = (
    CONTROLLER_DIRECTORY / "results" / "monitoring"
)
DEFAULT_BENCHMARK_PROFILE = (
    CONTROLLER_DIRECTORY
    / "results"
    / "benchmarks"
    / "latest-profile.json"
)
DEFAULT_DECISION_DIRECTORY = (
    CONTROLLER_DIRECTORY / "results" / "decisions"
)


METRIC_PROFILE_FIELDS = {
    "response_time_mean_ms": "mean_latency_ms",
    "response_time_p50_ms": "median_latency_ms",
    "response_time_p95_ms": "p95_latency_ms",
    "response_time_p99_ms": "p99_latency_ms",
}


class PolicyError(RuntimeError):
    """Raised when a placement decision cannot be evaluated."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def default_decision_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    return f"decision-{timestamp}"


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result) or math.isinf(result):
        return None

    return result


def rounded(
    value: float | None,
    digits: int = 3,
) -> float | None:
    if value is None:
        return None

    return round(value, digits)


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(
            f"Cannot load JSON file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise PolicyError(
            f"{path} must contain a JSON object"
        )

    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(
            f"Cannot load YAML file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise PolicyError(
            f"{path} must contain a YAML mapping"
        )

    return value


def parse_timestamp(value: str) -> datetime:
    normalized = value

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PolicyError(
            f"Invalid ISO timestamp: {value}"
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def newest_monitoring_snapshot(
    directory: Path,
) -> Path:
    candidates = list(
        directory.glob("monitoring-snapshot-*.json")
    )

    if not candidates:
        raise PolicyError(
            "No monitoring snapshot found. Run "
            "controller/monitoring.py first."
        )

    return max(
        candidates,
        key=lambda path: path.stat().st_mtime,
    )


def run_command(
    command: list[str],
    timeout_seconds: int = 20,
) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PolicyError(
            f"Command timed out: {' '.join(command)}"
        ) from exc

    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "unknown command error"
        )

        raise PolicyError(
            f"Command failed: {' '.join(command)}: "
            f"{message}"
        )

    return completed.stdout


def parse_image_reference(
    image: str,
) -> tuple[str, str]:
    if "@" in image:
        raise PolicyError(
            "Digest-based image references are not "
            "supported by the registry availability check."
        )

    if "/" not in image:
        raise PolicyError(
            f"Image has no registry prefix: {image}"
        )

    repository_and_tag = image.split("/", 1)[1]
    final_component = repository_and_tag.rsplit("/", 1)[-1]

    if ":" in final_component:
        repository, tag = repository_and_tag.rsplit(
            ":",
            1,
        )
    else:
        repository = repository_and_tag
        tag = "latest"

    if not repository or not tag:
        raise PolicyError(
            f"Invalid image reference: {image}"
        )

    return repository, tag


def check_image_available(
    cluster_config: dict[str, Any],
    image: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    repository, tag = parse_image_reference(image)

    registry_config = cluster_config["registry"]

    ssh_alias = registry_config.get(
        "ssh_alias"
    ) or cluster_config["infrastructure"]["ssh_alias"]

    registry_url = registry_config[
        "remote_url"
    ].rstrip("/")

    tags_url = (
        f"{registry_url}/v2/"
        f"{repository}/tags/list"
    )

    try:
        output = run_command(
            [
                "ssh",
                ssh_alias,
                "curl",
                "-fsS",
                tags_url,
            ],
            timeout_seconds=timeout_seconds,
        )

        payload = json.loads(output)

        tags = payload.get("tags") or []

        if not isinstance(tags, list):
            tags = []

        available = tag in tags

        return {
            "checked": True,
            "available": available,
            "repository": repository,
            "tag": tag,
            "ssh_alias": ssh_alias,
            "registry_url": registry_url,
            "error": None,
        }

    except (
        PolicyError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "checked": True,
            "available": False,
            "repository": repository,
            "tag": tag,
            "ssh_alias": ssh_alias,
            "registry_url": registry_url,
            "error": str(exc),
        }


def objective_satisfied(
    measured_value: float,
    operator: str,
    target_value: float,
) -> bool:
    if operator == "<":
        return measured_value < target_value

    if operator == "<=":
        return measured_value <= target_value

    if operator == "==":
        return measured_value == target_value

    if operator == ">=":
        return measured_value >= target_value

    if operator == ">":
        return measured_value > target_value

    raise PolicyError(
        f"Unsupported objective operator: {operator}"
    )


def objective_violation(
    measured_value: float,
    operator: str,
    target_value: float,
) -> float:
    if operator in {"<", "<="}:
        return max(
            0.0,
            measured_value - target_value,
        )

    if operator in {">", ">="}:
        return max(
            0.0,
            target_value - measured_value,
        )

    if operator == "==":
        return abs(
            measured_value - target_value
        )

    raise PolicyError(
        f"Unsupported objective operator: {operator}"
    )


def metric_preference_value(
    measured_value: float,
    operator: str,
    target_value: float,
) -> float:
    if operator in {"<", "<="}:
        return measured_value

    if operator in {">", ">="}:
        return -measured_value

    if operator == "==":
        return abs(
            measured_value - target_value
        )

    raise PolicyError(
        f"Unsupported objective operator: {operator}"
    )


def evaluate_candidate(
    cluster_name: str,
    cluster_index: int,
    infrastructure: dict[str, Any],
    monitoring_cluster: dict[str, Any] | None,
    benchmark_cluster: dict[str, Any] | None,
    descriptor: dict[str, Any],
    intent: dict[str, Any],
    controller_config: dict[str, Any],
) -> dict[str, Any]:
    feasibility_config = controller_config[
        "feasibility"
    ]

    policy_config = controller_config.get(
        "policy",
        {},
    )

    monitoring_timeout = int(
        controller_config["monitoring"][
            "query_timeout_seconds"
        ]
    )

    reasons: list[str] = []

    if monitoring_cluster is None:
        monitoring_summary: dict[str, Any] = {}
        reasons.append(
            "Monitoring data is missing"
        )
    else:
        monitoring_summary = monitoring_cluster.get(
            "summary",
            {},
        )

    reachable = bool(
        monitoring_summary.get("reachable")
    )

    knative_ready = bool(
        monitoring_summary.get("knative_ready")
    )

    prometheus_ready = bool(
        monitoring_summary.get(
            "prometheus_ready"
        )
    )

    ready_workers = int(
        monitoring_summary.get(
            "ready_workers",
            0,
        )
        or 0
    )

    available_cpu = finite_float(
        monitoring_summary.get(
            "available_cpu_millicores"
        )
    )

    available_memory = finite_float(
        monitoring_summary.get(
            "available_memory_mb"
        )
    )

    if not reachable:
        reasons.append(
            "Cluster is not reachable"
        )

    if (
        feasibility_config.get(
            "require_knative_ready",
            True,
        )
        and not knative_ready
    ):
        reasons.append(
            "Knative is not Ready"
        )

    if (
        feasibility_config.get(
            "require_prometheus_ready",
            True,
        )
        and not prometheus_ready
    ):
        reasons.append(
            "Prometheus is not Ready"
        )

    minimum_workers = int(
        feasibility_config[
            "minimum_ready_workers"
        ]
    )

    if ready_workers < minimum_workers:
        reasons.append(
            f"Ready workers {ready_workers} "
            f"is below minimum {minimum_workers}"
        )

    minimum_cpu = float(
        feasibility_config[
            "minimum_available_cpu_millicores"
        ]
    )

    if (
        available_cpu is None
        or available_cpu < minimum_cpu
    ):
        reasons.append(
            f"Available CPU {available_cpu}m "
            f"is below minimum {minimum_cpu}m"
        )

    minimum_memory = float(
        feasibility_config[
            "minimum_available_memory_mb"
        ]
    )

    if (
        available_memory is None
        or available_memory < minimum_memory
    ):
        reasons.append(
            f"Available memory {available_memory}MB "
            f"is below minimum {minimum_memory}MB"
        )

    image_required = bool(
        feasibility_config.get(
            "require_image_available",
            True,
        )
    )

    if image_required:
        image_status = check_image_available(
            infrastructure,
            descriptor["image"],
            monitoring_timeout,
        )

        if not image_status["available"]:
            reasons.append(
                "Function image is not available "
                "in the cluster registry"
            )
    else:
        repository, tag = parse_image_reference(
            descriptor["image"]
        )

        image_status = {
            "checked": False,
            "available": None,
            "repository": repository,
            "tag": tag,
            "error": None,
        }

    if benchmark_cluster is None:
        benchmark_summary: dict[str, Any] = {}
        reasons.append(
            "Benchmark profile is missing"
        )
    else:
        benchmark_summary = benchmark_cluster

    if benchmark_summary.get("status") != "success":
        reasons.append(
            "Benchmark status is not successful"
        )

    success_rate = finite_float(
        benchmark_summary.get(
            "success_rate_percent"
        )
    )

    minimum_success_rate = float(
        policy_config.get(
            "minimum_benchmark_success_rate_percent",
            100,
        )
    )

    if (
        success_rate is None
        or success_rate < minimum_success_rate
    ):
        reasons.append(
            f"Benchmark success rate {success_rate}% "
            f"is below minimum "
            f"{minimum_success_rate}%"
        )

    objective = intent["objective"]
    metric_name = objective["metric"]
    operator = objective["operator"]
    target_value = float(objective["value"])

    profile_field = METRIC_PROFILE_FIELDS.get(
        metric_name
    )

    if profile_field is None:
        raise PolicyError(
            f"Unsupported intent metric: "
            f"{metric_name}"
        )

    measured_value = finite_float(
        benchmark_summary.get(
            profile_field
        )
    )

    satisfied = False
    violation = None

    if measured_value is None:
        reasons.append(
            f"Benchmark metric {profile_field} "
            "is unavailable"
        )
    else:
        satisfied = objective_satisfied(
            measured_value,
            operator,
            target_value,
        )

        violation = objective_violation(
            measured_value,
            operator,
            target_value,
        )

    return {
        "cluster": cluster_name,
        "candidate_order": cluster_index,
        "feasible": not reasons,
        "rejection_reasons": reasons,
        "monitoring": {
            "status": (
                monitoring_cluster or {}
            ).get("status"),
            "reachable": reachable,
            "knative_ready": knative_ready,
            "prometheus_ready": prometheus_ready,
            "ready_workers": ready_workers,
            "available_cpu_millicores": (
                available_cpu
            ),
            "available_memory_mb": (
                available_memory
            ),
            "cpu_load_percent": finite_float(
                monitoring_summary.get(
                    "cpu_load_percent"
                )
            ),
            "memory_load_percent": finite_float(
                monitoring_summary.get(
                    "memory_load_percent"
                )
            ),
        },
        "image": image_status,
        "benchmark": {
            "status": benchmark_summary.get(
                "status"
            ),
            "success_rate_percent": success_rate,
            "mean_latency_ms": finite_float(
                benchmark_summary.get(
                    "mean_latency_ms"
                )
            ),
            "median_latency_ms": finite_float(
                benchmark_summary.get(
                    "median_latency_ms"
                )
            ),
            "p95_latency_ms": finite_float(
                benchmark_summary.get(
                    "p95_latency_ms"
                )
            ),
            "average_cpu_millicores": finite_float(
                benchmark_summary.get(
                    "average_cpu_millicores"
                )
            ),
            "peak_cpu_millicores": finite_float(
                benchmark_summary.get(
                    "peak_cpu_millicores"
                )
            ),
            "average_memory_mb": finite_float(
                benchmark_summary.get(
                    "average_memory_mb"
                )
            ),
            "peak_memory_mb": finite_float(
                benchmark_summary.get(
                    "peak_memory_mb"
                )
            ),
            "worker_node": benchmark_summary.get(
                "worker_node"
            ),
        },
        "objective": {
            "metric": metric_name,
            "benchmark_profile_field": (
                profile_field
            ),
            "operator": operator,
            "target_value": target_value,
            "measured_value": measured_value,
            "satisfied": satisfied,
            "violation": rounded(violation),
        },
        "rank": None,
    }


def ranking_key(
    candidate: dict[str, Any],
    operator: str,
    target_value: float,
) -> tuple[float, float, float, float, int]:
    objective = candidate["objective"]

    measured_value = float(
        objective["measured_value"]
    )

    violation = float(
        objective["violation"]
    )

    average_cpu = finite_float(
        candidate["benchmark"].get(
            "average_cpu_millicores"
        )
    )

    average_memory = finite_float(
        candidate["benchmark"].get(
            "average_memory_mb"
        )
    )

    return (
        violation,
        metric_preference_value(
            measured_value,
            operator,
            target_value,
        ),
        (
            average_cpu
            if average_cpu is not None
            else math.inf
        ),
        (
            average_memory
            if average_memory is not None
            else math.inf
        ),
        int(candidate["candidate_order"]),
    )


def build_decision(
    decision_id: str,
    infrastructure_config: dict[str, Any],
    monitoring_snapshot: dict[str, Any],
    benchmark_profile: dict[str, Any],
    descriptor: dict[str, Any],
    intent: dict[str, Any],
    controller_config: dict[str, Any],
    input_paths: dict[str, str],
    monitoring_age_seconds: float,
) -> dict[str, Any]:
    candidate_names = controller_config[
        "candidate_clusters"
    ]

    infrastructure_clusters = (
        infrastructure_config["clusters"]
    )

    monitoring_clusters = (
        monitoring_snapshot.get(
            "clusters",
            {},
        )
    )

    benchmark_clusters = (
        benchmark_profile.get(
            "clusters",
            {},
        )
    )

    candidates: dict[str, dict[str, Any]] = {}

    for index, cluster_name in enumerate(
        candidate_names
    ):
        candidates[cluster_name] = (
            evaluate_candidate(
                cluster_name=cluster_name,
                cluster_index=index,
                infrastructure=(
                    infrastructure_clusters[
                        cluster_name
                    ]
                ),
                monitoring_cluster=(
                    monitoring_clusters.get(
                        cluster_name
                    )
                ),
                benchmark_cluster=(
                    benchmark_clusters.get(
                        cluster_name
                    )
                ),
                descriptor=descriptor,
                intent=intent,
                controller_config=(
                    controller_config
                ),
            )
        )

    objective = intent["objective"]
    operator = objective["operator"]
    target_value = float(objective["value"])

    feasible_candidates = [
        candidate
        for candidate in candidates.values()
        if candidate["feasible"]
    ]

    ranked_feasible = sorted(
        feasible_candidates,
        key=lambda candidate: ranking_key(
            candidate,
            operator,
            target_value,
        ),
    )

    for rank, candidate in enumerate(
        ranked_feasible,
        start=1,
    ):
        candidate["rank"] = rank

    satisfying_candidates = [
        candidate
        for candidate in ranked_feasible
        if candidate["objective"]["satisfied"]
    ]

    fallback_mode = controller_config.get(
        "policy",
        {},
    ).get(
        "fallback_mode",
        "best-effort",
    )

    selected_candidate = None
    decision_mode = "no-feasible-candidate"
    reason = (
        "No candidate satisfied the feasibility "
        "constraints."
    )

    if satisfying_candidates:
        selected_candidate = satisfying_candidates[0]
        decision_mode = "intent-satisfied"

        reason = (
            f"Selected {selected_candidate['cluster']} "
            f"because it is feasible, satisfies "
            f"{objective['metric']} "
            f"{operator} {target_value}, and has the "
            "highest ranking among satisfying candidates."
        )

    elif ranked_feasible:
        if fallback_mode == "best-effort":
            selected_candidate = ranked_feasible[0]
            decision_mode = "best-effort"

            measured = selected_candidate[
                "objective"
            ]["measured_value"]

            violation = selected_candidate[
                "objective"
            ]["violation"]

            reason = (
                "No feasible candidate satisfies the "
                f"objective {objective['metric']} "
                f"{operator} {target_value}. "
                f"Selected "
                f"{selected_candidate['cluster']} "
                "in best-effort mode because it has "
                "the smallest objective violation. "
                f"Measured value={measured}, "
                f"violation={violation}."
            )
        else:
            decision_mode = "intent-unsatisfied"
            reason = (
                "Feasible candidates exist, but none "
                "satisfies the intent and fallback mode "
                f"is {fallback_mode}."
            )

    selected_cluster = (
        selected_candidate["cluster"]
        if selected_candidate
        else None
    )

    selected_objective_satisfied = (
        bool(
            selected_candidate[
                "objective"
            ]["satisfied"]
        )
        if selected_candidate
        else False
    )

    return {
        "schema_version": 1,
        "decision_id": decision_id,
        "generated_at": utc_now(),
        "phase": "placement-policy",
        "deployment_performed": False,
        "benchmarking_performed": False,
        "inputs": input_paths,
        "function": {
            "name": descriptor["name"],
            "service_name": descriptor[
                "service_name"
            ],
            "image": descriptor["image"],
            "work_ms": descriptor[
                "workload"
            ]["value"],
        },
        "intent": intent,
        "monitoring": {
            "generated_at": (
                monitoring_snapshot.get(
                    "generated_at"
                )
            ),
            "age_seconds": rounded(
                monitoring_age_seconds
            ),
        },
        "benchmark": {
            "run_id": benchmark_profile.get(
                "run_id"
            ),
            "benchmarking_only": (
                benchmark_profile.get(
                    "benchmarking_only"
                )
            ),
            "placement_decision": (
                benchmark_profile.get(
                    "placement_decision"
                )
            ),
        },
        "policy": {
            "fallback_mode": fallback_mode,
            "tie_breakers": (
                controller_config.get(
                    "policy",
                    {},
                ).get(
                    "tie_breakers",
                    [],
                )
            ),
        },
        "decision_mode": decision_mode,
        "selected_cluster": selected_cluster,
        "objective_satisfied": (
            selected_objective_satisfied
        ),
        "reason": reason,
        "candidates": candidates,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate monitoring, benchmark and intent "
            "data and select one candidate cluster."
        )
    )

    parser.add_argument(
        "--config-directory",
        type=Path,
        default=DEFAULT_CONFIG_DIRECTORY,
    )

    parser.add_argument(
        "--monitoring-snapshot",
        type=Path,
    )

    parser.add_argument(
        "--benchmark-profile",
        type=Path,
        default=DEFAULT_BENCHMARK_PROFILE,
    )

    parser.add_argument(
        "--intent",
        type=Path,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--decision-id",
        default=default_decision_id(),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    config_directory = arguments.config_directory

    monitoring_path = (
        arguments.monitoring_snapshot
        if arguments.monitoring_snapshot
        else newest_monitoring_snapshot(
            DEFAULT_MONITORING_DIRECTORY
        )
    )

    intent_path = (
        arguments.intent
        if arguments.intent
        else config_directory / "intent.json"
    )

    output_path = (
        arguments.output
        if arguments.output
        else (
            DEFAULT_DECISION_DIRECTORY
            / f"{arguments.decision_id}.json"
        )
    )

    try:
        infrastructure_config = load_yaml(
            config_directory / "clusters.yaml"
        )

        controller_config = load_json(
            config_directory
            / "controller_config.json"
        )

        descriptor = load_json(
            config_directory
            / "function_descriptor.json"
        )

        intent = load_json(intent_path)

        monitoring_snapshot = load_json(
            monitoring_path
        )

        benchmark_profile = load_json(
            arguments.benchmark_profile
        )

        if (
            intent.get("function")
            != descriptor.get("name")
        ):
            raise PolicyError(
                "Intent function does not match "
                "the function descriptor."
            )

        if (
            benchmark_profile.get("function")
            != descriptor.get("name")
        ):
            raise PolicyError(
                "Benchmark function does not match "
                "the function descriptor."
            )

        if (
            benchmark_profile.get(
                "benchmarking_only"
            )
            is not True
        ):
            raise PolicyError(
                "Benchmark profile is not marked "
                "as benchmarking-only."
            )

        if (
            benchmark_profile.get(
                "placement_decision"
            )
            is not None
        ):
            raise PolicyError(
                "Benchmark profile unexpectedly "
                "contains a placement decision."
            )

        generated_at = monitoring_snapshot.get(
            "generated_at"
        )

        if not isinstance(generated_at, str):
            raise PolicyError(
                "Monitoring snapshot has no "
                "generated_at timestamp."
            )

        monitoring_time = parse_timestamp(
            generated_at
        )

        monitoring_age_seconds = (
            datetime.now(timezone.utc)
            - monitoring_time
        ).total_seconds()

        maximum_age = float(
            controller_config["monitoring"][
                "snapshot_maximum_age_seconds"
            ]
        )

        if monitoring_age_seconds > maximum_age:
            raise PolicyError(
                "Monitoring snapshot is stale: "
                f"age={monitoring_age_seconds:.1f}s, "
                f"maximum={maximum_age:.1f}s. "
                "Generate a fresh snapshot."
            )

        decision_started_at = utc_now()
        decision_started_perf = time.perf_counter()

        decision = build_decision(
            decision_id=arguments.decision_id,
            infrastructure_config=(
                infrastructure_config
            ),
            monitoring_snapshot=(
                monitoring_snapshot
            ),
            benchmark_profile=(
                benchmark_profile
            ),
            descriptor=descriptor,
            intent=intent,
            controller_config=(
                controller_config
            ),
            input_paths={
                "intent": str(
                    intent_path.resolve()
                ),
                "function_descriptor": str(
                    (
                        config_directory
                        / "function_descriptor.json"
                    ).resolve()
                ),
                "controller_config": str(
                    (
                        config_directory
                        / "controller_config.json"
                    ).resolve()
                ),
                "infrastructure_config": str(
                    (
                        config_directory
                        / "clusters.yaml"
                    ).resolve()
                ),
                "monitoring_snapshot": str(
                    monitoring_path.resolve()
                ),
                "benchmark_profile": str(
                    arguments.benchmark_profile.resolve()
                ),
            },
            monitoring_age_seconds=(
                monitoring_age_seconds
            ),
        )

        decision_finished_at = utc_now()
        decision["timing"] = {
            "started_at": decision_started_at,
            "finished_at": decision_finished_at,
            "duration_ms": round(
                (
                    time.perf_counter()
                    - decision_started_perf
                )
                * 1000,
                3,
            ),
            "scope": (
                "Candidate feasibility evaluation, "
                "ranking and placement selection"
            ),
        }

    except PolicyError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            decision,
            file,
            indent=2,
        )
        file.write("\n")

    latest_path = (
        DEFAULT_DECISION_DIRECTORY
        / "latest-decision.json"
    )

    latest_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copyfile(
        output_path,
        latest_path,
    )

    print(f"Decision written to: {output_path}")

    for cluster_name, candidate in decision[
        "candidates"
    ].items():
        objective = candidate["objective"]

        print(
            f"{cluster_name}: "
            f"feasible={candidate['feasible']}, "
            f"rank={candidate['rank']}, "
            f"measured="
            f"{objective['measured_value']}, "
            f"target="
            f"{objective['operator']} "
            f"{objective['target_value']}, "
            f"satisfied="
            f"{objective['satisfied']}, "
            f"violation="
            f"{objective['violation']}"
        )

        for reason in candidate[
            "rejection_reasons"
        ]:
            print(
                f"  rejection: {reason}"
            )

    print()
    print(
        f"Decision mode: "
        f"{decision['decision_mode']}"
    )
    print(
        f"Selected cluster: "
        f"{decision['selected_cluster']}"
    )
    print(
        f"Objective satisfied: "
        f"{decision['objective_satisfied']}"
    )
    print(f"Reason: {decision['reason']}")

    return (
        0
        if decision["selected_cluster"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
