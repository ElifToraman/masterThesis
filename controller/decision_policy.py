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
    objective_score: float
    score: float


@dataclass(frozen=True)
class PlacementDecision:
    timestamp: str
    run_id: str | None
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
        maximum_benchmark_age_seconds: float = 300.0,
        expected_run_id: str | None = None,
        expected_images: dict[str, str] | None = None,
        scoring_weights: dict[str, float] | None = None,
        cold_start_reference_ms: float = 1000.0,
        deployment_reference_ms: float = 60_000.0,
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
        self.maximum_benchmark_age_seconds = (
            maximum_benchmark_age_seconds
        )
        self.expected_run_id = expected_run_id
        self.expected_images = expected_images or {}
        self.scoring_weights = scoring_weights or {
            "objectives": 0.60,
            "load": 0.15,
            "cold_start": 0.10,
            "deployment": 0.05,
            "headroom": 0.10,
        }
        self.cold_start_reference_ms = (
            cold_start_reference_ms
        )
        self.deployment_reference_ms = (
            deployment_reference_ms
        )

        if (
            self.cold_start_reference_ms <= 0
            or self.deployment_reference_ms <= 0
        ):
            raise ValueError(
                "Score normalization references must be positive"
            )

        if any(
            weight < 0
            for weight in self.scoring_weights.values()
        ):
            raise ValueError(
                "Scoring weights must not be negative"
            )

        if sum(self.scoring_weights.values()) <= 0:
            raise ValueError(
                "At least one scoring weight must be positive"
            )

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
            run_id=self.expected_run_id,
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

        # Every Kind node in a candidate cluster is a container running
        # on the same Chameleon VM. Summing node capacities would therefore
        # count the same physical CPU and memory once per Kind container.
        # Placement feasibility must use the physical VM as the capacity
        # boundary; node metrics are still used below for Kubernetes load
        # and topology summaries.
        if vm is None:
            total_cpu_cores = 0
            available_cpu_cores = 0.0
            total_memory_bytes = 0
            available_memory_bytes = 0
        else:
            total_cpu_cores = max(0, vm.cpu_core_count)
            available_cpu_cores = max(
                0.0,
                total_cpu_cores
                * (1.0 - vm.cpu_usage_percent / 100.0),
            )
            available_cpu_cores = min(
                float(total_cpu_cores),
                available_cpu_cores,
            )

            total_memory_bytes = max(
                0,
                vm.memory_total_bytes,
            )
            available_memory_bytes = min(
                total_memory_bytes,
                max(0, vm.memory_available_bytes),
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

        observed_metrics = {
            "benchmark_success_rate": benchmark_success_rate,
            "benchmark_average_latency_ms": (
                benchmark_average_latency_ms
            ),
            "benchmark_p95_latency_ms": benchmark_p95_latency_ms,
            "benchmark_p50_latency_ms": self._number(
                benchmark,
                "p50_warm_latency_ms",
            ),
            "benchmark_first_invocation_latency_ms": (
                benchmark_first_invocation_latency_ms
            ),
            "benchmark_deployment_duration_ms": (
                benchmark_deployment_duration_ms
            ),
            "benchmark_throughput_requests_per_second": self._number(
                benchmark,
                "throughput_requests_per_second",
            ),
            "vm_cpu_usage_percent": (
                vm.cpu_usage_percent if vm is not None else None
            ),
            "vm_memory_usage_percent": (
                vm.memory_usage_percent if vm is not None else None
            ),
            "available_cpu_cores": available_cpu_cores,
            "available_memory_bytes": float(
                available_memory_bytes
            ),
        }

        objective_results = [
            self._requirement_satisfied(
                objective=objective,
                observed_metrics=observed_metrics,
            )
            for objective in submission.intent.objectives
        ]

        for objective, result in zip(
            submission.intent.objectives,
            objective_results,
        ):
            if result is None:
                rejection_reasons.append(
                    f"unsupported_intent_objective:{objective.name}"
                )

        if not objective_results:
            rejection_reasons.append(
                "no_supported_intent_objective"
            )

        intent_satisfied = (
            bool(objective_results)
            and all(
                result is True
                for result in objective_results
            )
        )

        for constraint in submission.intent.constraints:
            constraint_result = self._requirement_satisfied(
                objective=constraint,
                observed_metrics=observed_metrics,
            )

            if constraint_result is None:
                rejection_reasons.append(
                    f"unsupported_constraint:{constraint.name}"
                )
            elif not constraint_result:
                rejection_reasons.append(
                    f"constraint_violated:{constraint.name}"
                )

        feasible = not rejection_reasons

        objective_score = self._weighted_objective_score(
            objectives=submission.intent.objectives,
            observed_metrics=observed_metrics,
        )

        score = self._score(
            objective_score=objective_score,
            benchmark_first_invocation_latency_ms=(
                benchmark_first_invocation_latency_ms
            ),
            benchmark_deployment_duration_ms=(
                benchmark_deployment_duration_ms
            ),
            average_node_cpu_usage_percent=average_node_cpu,
            average_node_memory_usage_percent=average_node_memory,
            total_cpu_cores=total_cpu_cores,
            available_cpu_cores=available_cpu_cores,
            total_memory_bytes=total_memory_bytes,
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
            objective_score=round(objective_score, 4),
            score=round(score, 4),
        )

    def _score(
        self,
        *,
        objective_score: float,
        benchmark_first_invocation_latency_ms: float | None,
        benchmark_deployment_duration_ms: float | None,
        average_node_cpu_usage_percent: float,
        average_node_memory_usage_percent: float,
        total_cpu_cores: int,
        available_cpu_cores: float,
        total_memory_bytes: int,
        available_memory_bytes: int,
    ) -> float:
        # Every component is dimensionless and bounded to [0, 1].
        load = self._clamp01(
            (
                average_node_cpu_usage_percent
                + average_node_memory_usage_percent
            )
            / 200.0
        )
        cold_start = self._clamp01(
            (benchmark_first_invocation_latency_ms or 0.0)
            / self.cold_start_reference_ms
        )
        deployment = self._clamp01(
            (benchmark_deployment_duration_ms or 0.0)
            / self.deployment_reference_ms
        )
        cpu_headroom = self._clamp01(
            available_cpu_cores
            / max(float(total_cpu_cores), 1.0)
        )
        memory_headroom = self._clamp01(
            available_memory_bytes
            / max(float(total_memory_bytes), 1.0)
        )
        headroom_penalty = 1.0 - (
            cpu_headroom + memory_headroom
        ) / 2.0

        components = {
            "objectives": self._clamp01(objective_score),
            "load": load,
            "cold_start": cold_start,
            "deployment": deployment,
            "headroom": headroom_penalty,
        }
        total_weight = sum(self.scoring_weights.values())

        return sum(
            components.get(name, 0.0) * weight
            for name, weight in self.scoring_weights.items()
        ) / total_weight

    def _requirement_satisfied(
        self,
        *,
        objective: Objective,
        observed_metrics: dict[str, float | None],
    ) -> bool | None:
        observed_value = self._objective_observed_value(
            objective=objective,
            observed_metrics=observed_metrics,
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
        observed_metrics: dict[str, float | None],
    ) -> float | None:
        measured_by = objective.measured_by.lower()
        name = objective.name.lower()
        text = f"{measured_by} {name}"

        if "p95" in text:
            return observed_metrics["benchmark_p95_latency_ms"]

        if "p50" in text:
            return observed_metrics["benchmark_p50_latency_ms"]

        if (
            "average" in text
            or "avg" in text
            or "mean" in text
        ):
            return observed_metrics[
                "benchmark_average_latency_ms"
            ]

        if "first" in text or "cold" in text:
            return observed_metrics[
                "benchmark_first_invocation_latency_ms"
            ]

        if "deploy" in text:
            return observed_metrics[
                "benchmark_deployment_duration_ms"
            ]

        if "success" in text:
            return observed_metrics["benchmark_success_rate"]

        if "throughput" in text or "request" in text:
            return observed_metrics[
                "benchmark_throughput_requests_per_second"
            ]

        if "available" in text and "cpu" in text:
            return observed_metrics["available_cpu_cores"]

        if "available" in text and "memory" in text:
            value = observed_metrics["available_memory_bytes"]
            return self._convert_bytes(value, objective.unit)

        if "cpu" in text and (
            "usage" in text or "load" in text
        ):
            return observed_metrics["vm_cpu_usage_percent"]

        if "memory" in text and (
            "usage" in text or "load" in text
        ):
            return observed_metrics["vm_memory_usage_percent"]

        # Default for normal latency/response-time intent.
        if "latency" in text or "response" in text:
            return observed_metrics["benchmark_p95_latency_ms"]

        return None

    def _weighted_objective_score(
        self,
        *,
        objectives: list[Objective],
        observed_metrics: dict[str, float | None],
    ) -> float:
        weighted_total = 0.0
        total_weight = 0.0

        for objective in objectives:
            observed = self._objective_observed_value(
                objective=objective,
                observed_metrics=observed_metrics,
            )

            if observed is None:
                continue

            target = objective.value

            if objective.operator in {"<", "<="}:
                ratio = observed / max(abs(target), 1e-9)
            elif objective.operator in {">", ">="}:
                ratio = target / max(abs(observed), 1e-9)
            else:
                ratio = abs(observed - target) / max(
                    abs(target),
                    1.0,
                )

            weighted_total += self._clamp01(ratio) * objective.weight
            total_weight += objective.weight

        if total_weight == 0:
            return 1.0

        return weighted_total / total_weight

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

                timestamp = self._timestamp(record)
                age_seconds = (
                    datetime.now(timezone.utc) - timestamp
                ).total_seconds()

                if (
                    age_seconds < 0
                    or age_seconds
                    > self.maximum_benchmark_age_seconds
                ):
                    continue

                if (
                    self.expected_run_id is not None
                    and record.get("run_id")
                    != self.expected_run_id
                ):
                    continue

                expected_image = self.expected_images.get(
                    cluster_name
                )

                if (
                    expected_image is not None
                    and record.get("image_reference")
                    != expected_image
                ):
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

    def _convert_bytes(
        self,
        value: float | None,
        unit: str | None,
    ) -> float | None:
        if value is None:
            return None

        normalized_unit = (unit or "bytes").strip().lower()
        divisors = {
            "b": 1,
            "byte": 1,
            "bytes": 1,
            "kib": 1024,
            "mib": 1024**2,
            "gib": 1024**3,
            "kb": 1000,
            "mb": 1000**2,
            "gb": 1000**3,
        }
        divisor = divisors.get(normalized_unit)

        if divisor is None:
            return None

        return value / divisor

    def _clamp01(self, value: float) -> float:
        return min(1.0, max(0.0, value))


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
