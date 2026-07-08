from __future__ import annotations

import json
import math
import operator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controller.models import IntentFunction, Objective
from controller.monitoring.models import MetricsSnapshot


COMPARATORS = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    ">=": operator.ge,
    ">": operator.gt,
}


@dataclass(frozen=True)
class ClusterDecisionCandidate:
    cluster_name: str

    # Monitoring summary
    vm_cpu_usage_percent: float | None
    vm_memory_usage_percent: float | None
    vm_ssh_latency_ms: float | None

    node_count: int
    worker_count: int

    total_cpu_cores: int
    available_cpu_cores: float
    required_cpu_cores: float

    total_memory_bytes: int
    available_memory_bytes: int
    required_memory_bytes: int

    average_node_cpu_usage_percent: float
    average_node_memory_usage_percent: float

    pod_count: int
    function_pod_count: int
    total_pod_cpu_millicores: float
    total_pod_memory_bytes: int

    # Benchmark latency summary
    benchmark_success_rate: float | None
    benchmark_average_latency_ms: float | None
    benchmark_p95_latency_ms: float | None
    benchmark_first_invocation_latency_ms: float | None
    benchmark_deployment_duration_ms: float | None

    # Benchmark resource summary
    benchmark_average_cpu_usage_cores: float | None
    benchmark_peak_cpu_usage_cores: float | None
    benchmark_average_memory_usage_bytes: float | None
    benchmark_peak_memory_usage_bytes: float | None

    # Policy result
    feasible: bool
    intent_satisfied: bool
    rejection_reasons: list[str]
    score: float


@dataclass(frozen=True)
class PlacementDecision:
    timestamp: str
    function_name: str
    function_version: str
    service_name: str
    selected_cluster: str | None
    decision_mode: str
    reason: str
    candidates: list[ClusterDecisionCandidate]


class DecisionPolicy:
    """
    Intent-aware cluster policy.

    Inputs:
      - user-submitted function + high-level intent
      - latest benchmark result per cluster
      - latest monitoring snapshot

    Output:
      - selected cluster for Knative Service deployment
    """

    def __init__(
        self,
        *,
        benchmark_file: Path,
        minimum_success_rate: float = 0.95,
        default_required_cpu_cores: float = 0.25,
        default_required_memory_bytes: int = 128 * 1024 * 1024,
        cpu_safety_factor: float = 1.25,
        memory_safety_factor: float = 1.25,
    ) -> None:
        self.benchmark_file = benchmark_file
        self.minimum_success_rate = minimum_success_rate

        # Used only when benchmark history has no resource profile yet.
        self.default_required_cpu_cores = default_required_cpu_cores
        self.default_required_memory_bytes = (
            default_required_memory_bytes
        )

        # Safety margin over observed benchmark usage.
        self.cpu_safety_factor = cpu_safety_factor
        self.memory_safety_factor = memory_safety_factor

    def decide(
        self,
        *,
        submission: IntentFunction,
        snapshot: MetricsSnapshot,
    ) -> PlacementDecision:
        benchmarks = self._load_latest_benchmarks(
            function_name=submission.function.name,
            function_version=submission.function.version,
        )

        cluster_names = sorted(
            set(snapshot.vm_metrics.keys())
            | {
                node.cluster_name
                for node in snapshot.node_metrics.values()
            }
            | {
                pod.cluster_name
                for pod in snapshot.pod_metrics.values()
            }
            | set(benchmarks.keys())
        )

        candidates = [
            self._build_candidate(
                cluster_name=cluster_name,
                submission=submission,
                snapshot=snapshot,
                benchmark=benchmarks.get(cluster_name),
            )
            for cluster_name in cluster_names
        ]

        feasible_candidates = [
            candidate
            for candidate in candidates
            if candidate.feasible
        ]

        satisfied_candidates = [
            candidate
            for candidate in feasible_candidates
            if candidate.intent_satisfied
        ]

        if satisfied_candidates:
            selected = min(
                satisfied_candidates,
                key=lambda candidate: candidate.score,
            )
            mode = "intent-satisfied"
            reason = (
                f"Selected {selected.cluster_name} because it is "
                f"feasible and satisfies the intent with the best "
                f"score."
            )
        elif feasible_candidates:
            selected = min(
                feasible_candidates,
                key=lambda candidate: candidate.score,
            )
            mode = "best-effort"
            reason = (
                "No feasible cluster satisfies all intent objectives. "
                f"Selected {selected.cluster_name} as best-effort."
            )
        else:
            selected = None
            mode = "no-feasible-cluster"
            reason = (
                "No feasible cluster has enough monitoring and "
                "benchmark evidence."
            )

        return PlacementDecision(
            timestamp=datetime.now(timezone.utc).isoformat(),
            function_name=submission.function.name,
            function_version=submission.function.version,
            service_name=submission.function.service_name,
            selected_cluster=(
                selected.cluster_name
                if selected is not None
                else None
            ),
            decision_mode=mode,
            reason=reason,
            candidates=candidates,
        )

    def _build_candidate(
        self,
        *,
        cluster_name: str,
        submission: IntentFunction,
        snapshot: MetricsSnapshot,
        benchmark: dict[str, Any] | None,
    ) -> ClusterDecisionCandidate:
        rejection_reasons: list[str] = []

        vm = snapshot.vm_metrics.get(cluster_name)

        nodes = [
            node
            for node in snapshot.node_metrics.values()
            if node.cluster_name == cluster_name
        ]

        worker_nodes = [
            node
            for node in nodes
            if node.node_role.lower()
            not in {"control-plane", "master"}
        ]

        schedulable_nodes = worker_nodes or nodes

        pods = [
            pod
            for pod in snapshot.pod_metrics.values()
            if pod.cluster_name == cluster_name
        ]

        function_pods = [
            pod
            for pod in pods
            if pod.namespace == submission.function.namespace
            and pod.pod_name.startswith(
                submission.function.service_name
            )
            and "-benchmark" not in pod.pod_name
        ]

        total_cpu_cores = sum(
            node.cpu_core_count
            for node in schedulable_nodes
        )

        available_cpu_cores = sum(
            max(
                0.0,
                node.cpu_core_count
                * (1.0 - node.cpu_usage_percent / 100.0),
            )
            for node in schedulable_nodes
        )

        total_memory_bytes = sum(
            node.memory_total_bytes
            for node in schedulable_nodes
        )

        available_memory_bytes = sum(
            node.memory_available_bytes
            for node in schedulable_nodes
        )

        average_node_cpu = self._average(
            [
                node.cpu_usage_percent
                for node in schedulable_nodes
            ]
        )

        average_node_memory = self._average(
            [
                node.memory_usage_percent
                for node in schedulable_nodes
            ]
        )

        total_pod_cpu_millicores = sum(
            pod.cpu_usage_millicores
            for pod in pods
        )

        total_pod_memory_bytes = sum(
            pod.memory_usage_bytes
            for pod in pods
        )

        benchmark_success_rate = self._number(
            benchmark,
            "success_rate",
        )

        benchmark_average_latency_ms = self._number(
            benchmark,
            "average_warm_latency_ms",
        )

        benchmark_p95_latency_ms = self._number(
            benchmark,
            "p95_warm_latency_ms",
        )

        benchmark_first_invocation_latency_ms = self._number(
            benchmark,
            "first_invocation_latency_ms",
        )

        benchmark_deployment_duration_ms = self._number(
            benchmark,
            "deployment_duration_ms",
        )

        benchmark_average_cpu_usage_cores = self._number(
            benchmark,
            "average_cpu_usage_cores",
        )

        benchmark_peak_cpu_usage_cores = self._number(
            benchmark,
            "peak_cpu_usage_cores",
        )

        benchmark_average_memory_usage_bytes = self._number(
            benchmark,
            "average_memory_usage_bytes",
        )

        benchmark_peak_memory_usage_bytes = self._number(
            benchmark,
            "peak_memory_usage_bytes",
        )

        required_cpu_cores = (
            self._required_cpu_cores_from_benchmark(
                benchmark
            )
        )

        required_memory_bytes = (
            self._required_memory_bytes_from_benchmark(
                benchmark
            )
        )

        if vm is None:
            rejection_reasons.append("missing_vm_metrics")

        if not schedulable_nodes:
            rejection_reasons.append("missing_node_metrics")

        if benchmark is None:
            rejection_reasons.append("missing_benchmark")

        if benchmark_success_rate is None:
            rejection_reasons.append(
                "missing_benchmark_success_rate"
            )
        elif benchmark_success_rate < self.minimum_success_rate:
            rejection_reasons.append(
                f"benchmark_success_rate_below_"
                f"{self.minimum_success_rate}"
            )

        if benchmark_p95_latency_ms is None:
            rejection_reasons.append(
                "missing_benchmark_p95_latency"
            )

        if available_cpu_cores < required_cpu_cores:
            rejection_reasons.append(
                "insufficient_available_cpu"
            )

        if available_memory_bytes < required_memory_bytes:
            rejection_reasons.append(
                "insufficient_available_memory"
            )

        objective_results = [
            self._objective_satisfied(
                objective=objective,
                benchmark=benchmark,
            )
            for objective in submission.intent.objectives
        ]

        supported_objective_results = [
            result
            for result in objective_results
            if result is not None
        ]

        if not supported_objective_results:
            rejection_reasons.append(
                "no_supported_intent_objective"
            )

        intent_satisfied = (
            bool(supported_objective_results)
            and all(supported_objective_results)
        )

        feasible = not rejection_reasons

        score = self._score(
            benchmark_p95_latency_ms=benchmark_p95_latency_ms,
            benchmark_average_latency_ms=(
                benchmark_average_latency_ms
            ),
            benchmark_first_invocation_latency_ms=(
                benchmark_first_invocation_latency_ms
            ),
            benchmark_deployment_duration_ms=(
                benchmark_deployment_duration_ms
            ),
            average_node_cpu_usage_percent=average_node_cpu,
            average_node_memory_usage_percent=average_node_memory,
            available_cpu_cores=available_cpu_cores,
            available_memory_bytes=available_memory_bytes,
        )

        return ClusterDecisionCandidate(
            cluster_name=cluster_name,
            vm_cpu_usage_percent=(
                vm.cpu_usage_percent
                if vm is not None
                else None
            ),
            vm_memory_usage_percent=(
                vm.memory_usage_percent
                if vm is not None
                else None
            ),
            vm_ssh_latency_ms=(
                vm.ssh_latency_ms
                if vm is not None
                else None
            ),
            node_count=len(nodes),
            worker_count=len(worker_nodes),
            total_cpu_cores=total_cpu_cores,
            available_cpu_cores=round(
                available_cpu_cores,
                4,
            ),
            required_cpu_cores=round(
                required_cpu_cores,
                4,
            ),
            total_memory_bytes=total_memory_bytes,
            available_memory_bytes=available_memory_bytes,
            required_memory_bytes=required_memory_bytes,
            average_node_cpu_usage_percent=round(
                average_node_cpu,
                4,
            ),
            average_node_memory_usage_percent=round(
                average_node_memory,
                4,
            ),
            pod_count=len(pods),
            function_pod_count=len(function_pods),
            total_pod_cpu_millicores=round(
                total_pod_cpu_millicores,
                4,
            ),
            total_pod_memory_bytes=total_pod_memory_bytes,
            benchmark_success_rate=benchmark_success_rate,
            benchmark_average_latency_ms=(
                benchmark_average_latency_ms
            ),
            benchmark_p95_latency_ms=benchmark_p95_latency_ms,
            benchmark_first_invocation_latency_ms=(
                benchmark_first_invocation_latency_ms
            ),
            benchmark_deployment_duration_ms=(
                benchmark_deployment_duration_ms
            ),
            benchmark_average_cpu_usage_cores=(
                benchmark_average_cpu_usage_cores
            ),
            benchmark_peak_cpu_usage_cores=(
                benchmark_peak_cpu_usage_cores
            ),
            benchmark_average_memory_usage_bytes=(
                benchmark_average_memory_usage_bytes
            ),
            benchmark_peak_memory_usage_bytes=(
                benchmark_peak_memory_usage_bytes
            ),
            feasible=feasible,
            intent_satisfied=intent_satisfied,
            rejection_reasons=rejection_reasons,
            score=round(score, 4),
        )

    def _score(
        self,
        *,
        benchmark_p95_latency_ms: float | None,
        benchmark_average_latency_ms: float | None,
        benchmark_first_invocation_latency_ms: float | None,
        benchmark_deployment_duration_ms: float | None,
        average_node_cpu_usage_percent: float,
        average_node_memory_usage_percent: float,
        available_cpu_cores: float,
        available_memory_bytes: int,
    ) -> float:
        """
        Lower score is better.

        Main priority:
          1. benchmark p95 warm latency
          2. benchmark average warm latency
          3. current CPU and memory load
          4. cold-start / deployment penalty
          5. resource headroom bonus
        """

        p95 = benchmark_p95_latency_ms
        if p95 is None:
            p95 = 1_000_000.0

        average = benchmark_average_latency_ms
        if average is None:
            average = p95

        first_invocation = (
            benchmark_first_invocation_latency_ms or 0.0
        )

        deployment = benchmark_deployment_duration_ms or 0.0

        memory_headroom_gb = (
            available_memory_bytes / 1024 / 1024 / 1024
        )

        return (
            p95 * 1.00
            + average * 0.20
            + average_node_cpu_usage_percent * 0.10
            + average_node_memory_usage_percent * 0.05
            + first_invocation * 0.01
            + deployment * 0.001
            - available_cpu_cores * 0.10
            - memory_headroom_gb * 0.05
        )

    def _objective_satisfied(
        self,
        *,
        objective: Objective,
        benchmark: dict[str, Any] | None,
    ) -> bool | None:
        if benchmark is None:
            return False

        observed_value = self._objective_observed_value(
            objective=objective,
            benchmark=benchmark,
        )

        if observed_value is None:
            return None

        comparator = COMPARATORS.get(objective.operator)

        if comparator is None:
            return None

        return bool(
            comparator(
                observed_value,
                objective.value,
            )
        )

    def _objective_observed_value(
        self,
        *,
        objective: Objective,
        benchmark: dict[str, Any],
    ) -> float | None:
        measured_by = objective.measured_by.lower()
        name = objective.name.lower()
        text = f"{measured_by} {name}"

        if "p95" in text:
            return self._number(
                benchmark,
                "p95_warm_latency_ms",
            )

        if "p50" in text:
            return self._number(
                benchmark,
                "p50_warm_latency_ms",
            )

        if (
            "average" in text
            or "avg" in text
            or "mean" in text
        ):
            return self._number(
                benchmark,
                "average_warm_latency_ms",
            )

        if "first" in text or "cold" in text:
            return self._number(
                benchmark,
                "first_invocation_latency_ms",
            )

        if "deploy" in text:
            return self._number(
                benchmark,
                "deployment_duration_ms",
            )

        # Default for normal latency/response-time intent.
        if "latency" in text or "response" in text:
            return self._number(
                benchmark,
                "p95_warm_latency_ms",
            )

        return None

    def _load_latest_benchmarks(
        self,
        *,
        function_name: str,
        function_version: str,
    ) -> dict[str, dict[str, Any]]:
        latest_by_cluster: dict[str, dict[str, Any]] = {}

        if not self.benchmark_file.exists():
            return latest_by_cluster

        with self.benchmark_file.open(
            "r",
            encoding="utf-8",
        ) as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if record.get("function_name") != function_name:
                    continue

                # Some old benchmark records may not contain
                # function_version.
                record_version = record.get("function_version")
                if (
                    record_version is not None
                    and record_version != function_version
                ):
                    continue

                cluster_name = record.get("cluster_name")
                if not isinstance(cluster_name, str):
                    continue

                current = latest_by_cluster.get(cluster_name)

                if current is None:
                    latest_by_cluster[cluster_name] = record
                    continue

                if self._timestamp(record) > self._timestamp(
                    current
                ):
                    latest_by_cluster[cluster_name] = record

        return latest_by_cluster

    def _timestamp(
        self,
        record: dict[str, Any],
    ) -> datetime:
        value = record.get("timestamp")

        if not isinstance(value, str):
            return datetime.min.replace(tzinfo=timezone.utc)

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)

        return parsed

    def _required_cpu_cores_from_benchmark(
        self,
        benchmark: dict[str, Any] | None,
    ) -> float:
        peak_cpu = self._number(
            benchmark,
            "peak_cpu_usage_cores",
        )

        average_cpu = self._number(
            benchmark,
            "average_cpu_usage_cores",
        )

        observed_cpu = (
            peak_cpu
            if peak_cpu is not None
            else average_cpu
        )

        if observed_cpu is None or observed_cpu <= 0:
            return self.default_required_cpu_cores

        return max(
            self.default_required_cpu_cores,
            observed_cpu * self.cpu_safety_factor,
        )

    def _required_memory_bytes_from_benchmark(
        self,
        benchmark: dict[str, Any] | None,
    ) -> int:
        peak_memory = self._number(
            benchmark,
            "peak_memory_usage_bytes",
        )

        average_memory = self._number(
            benchmark,
            "average_memory_usage_bytes",
        )

        observed_memory = (
            peak_memory
            if peak_memory is not None
            else average_memory
        )

        if observed_memory is None or observed_memory <= 0:
            return self.default_required_memory_bytes

        return max(
            self.default_required_memory_bytes,
            int(observed_memory * self.memory_safety_factor),
        )

    def _number(
        self,
        record: dict[str, Any] | None,
        key: str,
    ) -> float | None:
        if record is None:
            return None

        value = record.get(key)

        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None

        if math.isnan(parsed) or math.isinf(parsed):
            return None

        return parsed

    def _average(
        self,
        values: list[float],
    ) -> float:
        if not values:
            return 0.0

        return sum(values) / len(values)


def write_decision(
    *,
    decision: PlacementDecision,
    output_file: Path,
) -> None:
    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            asdict(decision),
            file,
            indent=2,
        )
        file.write("\n")
