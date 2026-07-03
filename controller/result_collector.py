#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent

DEFAULT_CONFIG_DIRECTORY = (
    CONTROLLER_DIRECTORY / "config"
)

DEFAULT_BENCHMARK_PROFILE = (
    CONTROLLER_DIRECTORY
    / "results"
    / "benchmarks"
    / "latest-profile.json"
)

DEFAULT_DECISION = (
    CONTROLLER_DIRECTORY
    / "results"
    / "decisions"
    / "latest-decision.json"
)

DEFAULT_EXECUTION = (
    CONTROLLER_DIRECTORY
    / "results"
    / "deployments"
    / "latest-execution.json"
)

DEFAULT_EXPERIMENTS_DIRECTORY = (
    CONTROLLER_DIRECTORY
    / "results"
    / "experiments"
)

DEFAULT_PLACEMENT_HISTORY = (
    CONTROLLER_DIRECTORY
    / "results"
    / "placement_results.csv"
)


BENCHMARK_RESULT_FIELDS = [
    "benchmark_run_id",
    "timestamp",
    "cluster",
    "node",
    "pod",
    "function",
    "sample_type",
    "sample",
    "request_id",
    "response_time_ms",
    "function_duration_ms",
    "cpu_millicores",
    "memory_mb",
    "resource_sample_timestamp",
    "resource_sample_offset_ms",
    "resource_sample_match",
    "http_status",
    "success",
    "error",
]


PLACEMENT_RESULT_FIELDS = [
    "experiment_id",
    "timestamp",
    "function",
    "intent_metric",
    "intent_operator",
    "intent_target_value",
    "selected_cluster",
    "decision_mode",
    "predicted_latency_ms",
    "actual_latency_ms",
    "predicted_intent_satisfied",
    "actual_intent_satisfied",
    "vm1_available_cpu_millicores",
    "vm1_available_memory_mb",
    "vm1_cpu_load_percent",
    "vm1_load_1m",
    "vm2_available_cpu_millicores",
    "vm2_available_memory_mb",
    "vm2_cpu_load_percent",
    "vm2_load_1m",
    "decision_time_ms",
    "deployment_time_ms",
    "invocation_count",
    "successful_invocations",
    "failed_invocations",
    "execution_node",
    "execution_pod",
    "execution_successful",
    "decision_id",
    "execution_id",
]


class ResultCollectorError(RuntimeError):
    """Raised when experiment artifacts are inconsistent."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def default_experiment_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    return f"experiment-{timestamp}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultCollectorError(
            f"Cannot load JSON file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise ResultCollectorError(
            f"{path} must contain a JSON object"
        )

    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as file:
            return list(csv.DictReader(file))
    except OSError as exc:
        raise ResultCollectorError(
            f"Cannot load CSV file {path}: {exc}"
        ) from exc


def write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(rows)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = str(value)

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        result = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(timezone.utc)


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result) or math.isinf(result):
        return None

    return result


def nested_value(
    value: dict[str, Any],
    *keys: str,
) -> Any:
    current: Any = value

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def first_present(
    value: dict[str, Any],
    paths: list[tuple[str, ...]],
) -> Any:
    for path in paths:
        result = nested_value(value, *path)

        if result is not None:
            return result

    return None


def resolve_benchmark_directory(
    profile_path: Path,
    profile: dict[str, Any],
) -> Path:
    run_id = profile.get("run_id")

    if not run_id:
        raise ResultCollectorError(
            "Benchmark profile has no run_id."
        )

    candidate = profile_path.parent / str(run_id)

    if candidate.is_dir():
        return candidate

    if (
        profile_path.parent.name
        == str(run_id)
    ):
        return profile_path.parent

    raise ResultCollectorError(
        "Cannot locate benchmark run directory "
        f"for run_id={run_id}"
    )


def resolve_monitoring_path(
    explicit_path: Path | None,
    decision: dict[str, Any],
) -> Path:
    if explicit_path is not None:
        path = explicit_path
    else:
        source = nested_value(
            decision,
            "inputs",
            "monitoring_snapshot",
        )

        if not source:
            raise ResultCollectorError(
                "Decision does not identify its "
                "monitoring snapshot."
            )

        path = Path(str(source))

    if not path.is_file():
        raise ResultCollectorError(
            "Monitoring snapshot does not exist: "
            f"{path}. Supply it with "
            "--monitoring-snapshot."
        )

    return path


def validate_artifacts(
    descriptor: dict[str, Any],
    intent: dict[str, Any],
    benchmark: dict[str, Any],
    decision: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    function_name = descriptor.get("name")

    if intent.get("function") != function_name:
        raise ResultCollectorError(
            "Intent and function descriptor do not match."
        )

    if benchmark.get("function") != function_name:
        raise ResultCollectorError(
            "Benchmark and function descriptor do not match."
        )

    decision_function = nested_value(
        decision,
        "function",
        "name",
    )

    if decision_function != function_name:
        raise ResultCollectorError(
            "Decision and function descriptor do not match."
        )

    if (
        benchmark.get("benchmarking_only")
        is not True
    ):
        raise ResultCollectorError(
            "Benchmark artifact is not marked "
            "benchmarking-only."
        )

    if benchmark.get("placement_decision") is not None:
        raise ResultCollectorError(
            "Benchmark artifact unexpectedly contains "
            "a placement decision."
        )

    selected_cluster = decision.get(
        "selected_cluster"
    )

    if not selected_cluster:
        raise ResultCollectorError(
            "Placement decision has no selected cluster."
        )

    if (
        execution.get("selected_cluster")
        != selected_cluster
    ):
        raise ResultCollectorError(
            "Execution cluster does not match "
            "the placement decision."
        )

    if (
        execution.get("decision_id")
        != decision.get("decision_id")
    ):
        raise ResultCollectorError(
            "Execution decision_id does not match "
            "the placement decision."
        )

    if execution.get("deployment_performed") is not True:
        raise ResultCollectorError(
            "Execution artifact contains no deployment."
        )


def build_resource_index(
    rows: list[dict[str, str]],
) -> tuple[
    dict[tuple[str, str], list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    by_pod: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    by_cluster: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in rows:
        timestamp = parse_timestamp(
            row.get("sample_timestamp")
        )

        if timestamp is None:
            continue

        normalized = {
            **row,
            "_timestamp": timestamp,
        }

        cluster = row.get("cluster", "")
        pod = row.get("pod", "")

        by_cluster[cluster].append(normalized)

        if pod:
            by_pod[(cluster, pod)].append(
                normalized
            )

    return by_pod, by_cluster


def nearest_resource_sample(
    request: dict[str, str],
    by_pod: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ],
    by_cluster: dict[
        str,
        list[dict[str, Any]],
    ],
) -> tuple[
    dict[str, Any] | None,
    str,
    float | None,
]:
    request_time = parse_timestamp(
        request.get("started_at")
    )

    if request_time is None:
        return None, "none", None

    cluster = request.get("cluster", "")
    pod = request.get("pod", "")

    candidates = by_pod.get(
        (cluster, pod),
        [],
    )

    match_type = "exact-pod"

    if not candidates:
        candidates = by_cluster.get(
            cluster,
            [],
        )
        match_type = "cluster-only"

    if not candidates:
        return None, "none", None

    sample = min(
        candidates,
        key=lambda item: abs(
            (
                item["_timestamp"]
                - request_time
            ).total_seconds()
        ),
    )

    offset_ms = abs(
        (
            sample["_timestamp"]
            - request_time
        ).total_seconds()
        * 1000
    )

    return sample, match_type, round(
        offset_ms,
        3,
    )


def create_benchmark_results(
    benchmark_profile: dict[str, Any],
    request_rows: list[dict[str, str]],
    resource_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_pod, by_cluster = build_resource_index(
        resource_rows
    )

    output: list[dict[str, Any]] = []

    for request in request_rows:
        (
            resource,
            match_type,
            offset_ms,
        ) = nearest_resource_sample(
            request,
            by_pod,
            by_cluster,
        )

        resource = resource or {}

        output.append(
            {
                "benchmark_run_id": (
                    benchmark_profile.get("run_id")
                ),
                "timestamp": request.get(
                    "started_at"
                ),
                "cluster": request.get("cluster"),
                "node": request.get("node"),
                "pod": request.get("pod"),
                "function": benchmark_profile.get(
                    "function"
                ),
                "sample_type": request.get(
                    "request_type"
                ),
                "sample": request.get(
                    "request_number"
                ),
                "request_id": request.get(
                    "request_id"
                ),
                "response_time_ms": request.get(
                    "response_time_ms"
                ),
                "function_duration_ms": request.get(
                    "function_duration_ms"
                ),
                "cpu_millicores": resource.get(
                    "cpu_millicores",
                    "",
                ),
                "memory_mb": resource.get(
                    "memory_mb",
                    "",
                ),
                "resource_sample_timestamp": (
                    resource.get(
                        "sample_timestamp",
                        "",
                    )
                ),
                "resource_sample_offset_ms": (
                    offset_ms
                    if offset_ms is not None
                    else ""
                ),
                "resource_sample_match": match_type,
                "http_status": request.get(
                    "http_status"
                ),
                "success": request.get("success"),
                "error": request.get("error"),
            }
        )

    return output


def monitoring_summary(
    monitoring: dict[str, Any],
    cluster_name: str,
) -> dict[str, Any]:
    return (
        monitoring.get("clusters", {})
        .get(cluster_name, {})
        .get("summary", {})
    )


def observed_names(
    execution: dict[str, Any],
    collection_name: str,
    direct_name: str,
) -> list[str]:
    values: list[str] = []

    collection = nested_value(
        execution,
        "placement",
        collection_name,
    )

    if isinstance(collection, list):
        for item in collection:
            if (
                isinstance(item, dict)
                and item.get("name")
            ):
                values.append(
                    str(item["name"])
                )

    direct = nested_value(
        execution,
        "placement",
        direct_name,
    )

    if direct:
        values.append(str(direct))

    return sorted(set(values))


def build_placement_row(
    experiment_id: str,
    monitoring: dict[str, Any],
    decision: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    intent_objective = nested_value(
        decision,
        "intent",
        "objective",
    )

    if not isinstance(intent_objective, dict):
        raise ResultCollectorError(
            "Decision has no intent objective."
        )

    selected_cluster = str(
        decision["selected_cluster"]
    )

    selected_candidate = (
        decision.get("candidates", {})
        .get(selected_cluster, {})
    )

    predicted_value = nested_value(
        selected_candidate,
        "objective",
        "measured_value",
    )

    actual_value = nested_value(
        execution,
        "actual_execution_evaluation",
        "measured_value",
    )

    vm1 = monitoring_summary(
        monitoring,
        "vm1-cluster",
    )

    vm2 = monitoring_summary(
        monitoring,
        "vm2-cluster",
    )

    decision_time_ms = first_present(
        decision,
        [
            ("timing", "duration_ms"),
            ("decision_time_ms",),
            ("policy_duration_ms",),
        ],
    )

    deployment_time_ms = first_present(
        execution,
        [
            ("deployment_timing", "duration_ms"),
            ("deployment_time_ms",),
        ],
    )

    nodes = observed_names(
        execution,
        "kubernetes_observed_nodes",
        "kubernetes_selected_node",
    )

    pods = observed_names(
        execution,
        "kubernetes_observed_pods",
        "pod",
    )

    return {
        "experiment_id": experiment_id,
        "timestamp": execution.get(
            "generated_at",
            utc_now(),
        ),
        "function": nested_value(
            decision,
            "function",
            "name",
        ),
        "intent_metric": intent_objective.get(
            "metric"
        ),
        "intent_operator": intent_objective.get(
            "operator"
        ),
        "intent_target_value": intent_objective.get(
            "value"
        ),
        "selected_cluster": selected_cluster,
        "decision_mode": decision.get(
            "decision_mode"
        ),
        "predicted_latency_ms": predicted_value,
        "actual_latency_ms": actual_value,
        "predicted_intent_satisfied": (
            decision.get("objective_satisfied")
        ),
        "actual_intent_satisfied": nested_value(
            execution,
            "actual_execution_evaluation",
            "satisfied",
        ),
        "vm1_available_cpu_millicores": vm1.get(
            "available_cpu_millicores"
        ),
        "vm1_available_memory_mb": vm1.get(
            "available_memory_mb"
        ),
        "vm1_cpu_load_percent": vm1.get(
            "cpu_load_percent"
        ),
        "vm1_load_1m": vm1.get("load_1m"),
        "vm2_available_cpu_millicores": vm2.get(
            "available_cpu_millicores"
        ),
        "vm2_available_memory_mb": vm2.get(
            "available_memory_mb"
        ),
        "vm2_cpu_load_percent": vm2.get(
            "cpu_load_percent"
        ),
        "vm2_load_1m": vm2.get("load_1m"),
        "decision_time_ms": (
            decision_time_ms
            if decision_time_ms is not None
            else ""
        ),
        "deployment_time_ms": (
            deployment_time_ms
            if deployment_time_ms is not None
            else ""
        ),
        "invocation_count": execution.get(
            "invocation_count"
        ),
        "successful_invocations": execution.get(
            "successful_invocations"
        ),
        "failed_invocations": execution.get(
            "failed_invocations"
        ),
        "execution_node": ";".join(nodes),
        "execution_pod": ";".join(pods),
        "execution_successful": execution.get(
            "execution_successful"
        ),
        "decision_id": decision.get(
            "decision_id"
        ),
        "execution_id": execution.get(
            "execution_id"
        ),
    }


def append_history(
    path: Path,
    row: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing_rows: list[dict[str, str]] = []

    if path.is_file():
        existing_rows = read_csv(path)

    experiment_id = str(row["experiment_id"])

    existing_rows = [
        existing
        for existing in existing_rows
        if existing.get("experiment_id")
        != experiment_id
    ]

    combined_rows: list[dict[str, Any]] = [
        *existing_rows,
        row,
    ]

    write_csv(
        path,
        PLACEMENT_RESULT_FIELDS,
        combined_rows,
    )


def copy_artifact(
    source: Path,
    destination: Path,
) -> None:
    if not source.is_file():
        raise ResultCollectorError(
            f"Source artifact does not exist: {source}"
        )

    shutil.copy2(source, destination)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consolidate existing benchmark, placement "
            "and execution artifacts."
        )
    )

    parser.add_argument(
        "--config-directory",
        type=Path,
        default=DEFAULT_CONFIG_DIRECTORY,
    )

    parser.add_argument(
        "--benchmark-profile",
        type=Path,
        default=DEFAULT_BENCHMARK_PROFILE,
    )

    parser.add_argument(
        "--decision",
        type=Path,
        default=DEFAULT_DECISION,
    )

    parser.add_argument(
        "--execution",
        type=Path,
        default=DEFAULT_EXECUTION,
    )

    parser.add_argument(
        "--monitoring-snapshot",
        type=Path,
    )

    parser.add_argument(
        "--results-directory",
        type=Path,
        default=DEFAULT_EXPERIMENTS_DIRECTORY,
    )

    parser.add_argument(
        "--placement-history",
        type=Path,
        default=DEFAULT_PLACEMENT_HISTORY,
    )

    parser.add_argument(
        "--experiment-id",
        default=default_experiment_id(),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    config_directory = arguments.config_directory
    descriptor_path = (
        config_directory
        / "function_descriptor.json"
    )
    intent_path = config_directory / "intent.json"

    try:
        descriptor = load_json(descriptor_path)
        intent = load_json(intent_path)

        benchmark_profile = load_json(
            arguments.benchmark_profile
        )

        decision = load_json(arguments.decision)
        execution = load_json(arguments.execution)

        monitoring_path = resolve_monitoring_path(
            arguments.monitoring_snapshot,
            decision,
        )

        monitoring = load_json(monitoring_path)

        validate_artifacts(
            descriptor,
            intent,
            benchmark_profile,
            decision,
            execution,
        )

        benchmark_directory = (
            resolve_benchmark_directory(
                arguments.benchmark_profile,
                benchmark_profile,
            )
        )

        benchmark_files = benchmark_profile.get(
            "files",
            {},
        )

        requests_path = (
            benchmark_directory
            / benchmark_files.get(
                "requests_csv",
                "requests.csv",
            )
        )

        resources_path = (
            benchmark_directory
            / benchmark_files.get(
                "resource_samples_csv",
                "resource_samples.csv",
            )
        )

        request_rows = read_csv(requests_path)
        resource_rows = read_csv(resources_path)

        experiment_directory = (
            arguments.results_directory
            / arguments.experiment_id
        )

        if experiment_directory.exists():
            raise ResultCollectorError(
                "Experiment directory already exists: "
                f"{experiment_directory}"
            )

        experiment_directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        benchmark_results = (
            create_benchmark_results(
                benchmark_profile,
                request_rows,
                resource_rows,
            )
        )

        write_csv(
            experiment_directory
            / "benchmark_results.csv",
            BENCHMARK_RESULT_FIELDS,
            benchmark_results,
        )

        benchmark_profiles = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "experiment_id": (
                arguments.experiment_id
            ),
            "function": benchmark_profile.get(
                "function"
            ),
            "benchmark_run_id": (
                benchmark_profile.get("run_id")
            ),
            "image": benchmark_profile.get(
                "image"
            ),
            "work_ms": benchmark_profile.get(
                "work_ms"
            ),
            "warmup_requests": (
                benchmark_profile.get(
                    "warmup_requests"
                )
            ),
            "measured_requests": (
                benchmark_profile.get(
                    "measured_requests"
                )
            ),
            "clusters": benchmark_profile.get(
                "clusters",
                {},
            ),
        }

        with (
            experiment_directory
            / "benchmark_profiles.json"
        ).open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                benchmark_profiles,
                file,
                indent=2,
            )
            file.write("\n")

        placement_row = build_placement_row(
            arguments.experiment_id,
            monitoring,
            decision,
            execution,
        )

        write_csv(
            experiment_directory
            / "placement_results.csv",
            PLACEMENT_RESULT_FIELDS,
            [placement_row],
        )

        append_history(
            arguments.placement_history,
            placement_row,
        )

        decision_explanation = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "experiment_id": (
                arguments.experiment_id
            ),
            "function": descriptor.get("name"),
            "intent": decision.get("intent"),
            "selected_cluster": decision.get(
                "selected_cluster"
            ),
            "decision_mode": decision.get(
                "decision_mode"
            ),
            "predicted_intent_satisfied": (
                decision.get(
                    "objective_satisfied"
                )
            ),
            "reason": decision.get("reason"),
            "candidates": decision.get(
                "candidates",
                {},
            ),
            "actual_execution": {
                "execution_id": execution.get(
                    "execution_id"
                ),
                "selected_cluster": execution.get(
                    "selected_cluster"
                ),
                "execution_successful": (
                    execution.get(
                        "execution_successful"
                    )
                ),
                "runtime": execution.get(
                    "runtime"
                ),
                "placement": execution.get(
                    "placement"
                ),
                "actual_execution_evaluation": (
                    execution.get(
                        "actual_execution_evaluation"
                    )
                ),
                "successful_invocations": (
                    execution.get(
                        "successful_invocations"
                    )
                ),
                "failed_invocations": (
                    execution.get(
                        "failed_invocations"
                    )
                ),
            },
            "timing": {
                "decision_time_ms": (
                    placement_row[
                        "decision_time_ms"
                    ]
                ),
                "deployment_time_ms": (
                    placement_row[
                        "deployment_time_ms"
                    ]
                ),
                "note": (
                    "Empty timing values mean the source "
                    "phase did not explicitly record that "
                    "duration. Phase 12 does not estimate "
                    "or invent missing timings."
                ),
            },
            "sources": {
                "monitoring_snapshot": str(
                    monitoring_path.resolve()
                ),
                "benchmark_profile": str(
                    arguments.benchmark_profile.resolve()
                ),
                "placement_decision": str(
                    arguments.decision.resolve()
                ),
                "execution_result": str(
                    arguments.execution.resolve()
                ),
            },
            "placement_decision": decision,
        }

        with (
            experiment_directory
            / "decision_explanation.json"
        ).open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                decision_explanation,
                file,
                indent=2,
            )
            file.write("\n")

        copy_artifact(
            intent_path,
            experiment_directory / "intent.json",
        )

        copy_artifact(
            descriptor_path,
            experiment_directory
            / "function_descriptor.json",
        )

        copy_artifact(
            monitoring_path,
            experiment_directory
            / "monitoring_snapshot.json",
        )

        copy_artifact(
            arguments.benchmark_profile,
            experiment_directory
            / "benchmark_profile_raw.json",
        )

        copy_artifact(
            requests_path,
            experiment_directory
            / "benchmark_requests_raw.csv",
        )

        copy_artifact(
            resources_path,
            experiment_directory
            / "resource_samples.csv",
        )

        copy_artifact(
            arguments.decision,
            experiment_directory
            / "placement_decision.json",
        )

        copy_artifact(
            arguments.execution,
            experiment_directory
            / "execution_result.json",
        )

    except ResultCollectorError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2

    print(
        f"Experiment ID: "
        f"{arguments.experiment_id}"
    )
    print(
        f"Benchmark rows: "
        f"{len(benchmark_results)}"
    )
    print(
        f"Selected cluster: "
        f"{placement_row['selected_cluster']}"
    )
    print(
        f"Predicted latency: "
        f"{placement_row['predicted_latency_ms']}ms"
    )
    print(
        f"Actual latency: "
        f"{placement_row['actual_latency_ms']}ms"
    )
    print(
        f"Predicted intent satisfied: "
        f"{placement_row['predicted_intent_satisfied']}"
    )
    print(
        f"Actual intent satisfied: "
        f"{placement_row['actual_intent_satisfied']}"
    )
    print(
        f"EXPERIMENT_DIRECTORY="
        f"{experiment_directory.resolve()}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
