from __future__ import annotations

import json
import logging
import operator
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from controller.benchmarking.models.cluster_benchmark_result import (
    percentile,
)
from controller.models import IntentFunction, Objective
from controller.monitoring.models import MetricsSnapshot


logger = logging.getLogger(__name__)

COMPARATORS = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    ">=": operator.ge,
    ">": operator.gt,
}


@dataclass(frozen=True)
class ClusterMonitoringSample:
    cluster_name: str
    selected: bool
    vm_reachable: bool
    node_metrics_available: bool
    node_count: int
    worker_count: int
    pod_count: int
    function_pod_count: int
    vm_cpu_usage_percent: float | None
    vm_memory_usage_percent: float | None
    vm_available_cpu_cores: float | None
    vm_available_memory_bytes: int | None
    average_worker_cpu_usage_percent: float | None
    average_worker_memory_usage_percent: float | None
    function_pod_cpu_millicores: float
    function_pod_memory_bytes: int


@dataclass(frozen=True)
class PostDeploymentSample:
    timestamp: str
    run_id: str
    cluster_name: str
    url: str
    invocation_succeeded: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None
    vm_cpu_usage_percent: float | None
    vm_memory_usage_percent: float | None
    vm_available_cpu_cores: float | None
    vm_available_memory_bytes: int | None
    function_pod_count: int
    function_pod_cpu_millicores: float
    function_pod_memory_bytes: int
    candidate_clusters: list[ClusterMonitoringSample]


@dataclass(frozen=True)
class ObjectiveEvaluation:
    name: str
    measured_by: str
    operator: str
    target: float
    unit: str | None
    observed: float | None
    supported: bool
    satisfied: bool | None


@dataclass(frozen=True)
class PostDeploymentSummary:
    timestamp: str
    run_id: str
    cluster_name: str
    service_name: str
    url: str
    state: str
    window_size: int
    required_window_size: int
    successful_probes: int
    failed_probes: int
    success_rate: float
    average_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    intent_satisfied: bool | None
    objective_evaluations: list[ObjectiveEvaluation]
    constraint_evaluations: list[ObjectiveEvaluation]
    latest_sample: PostDeploymentSample | None
    control_loop_enabled: bool
    consecutive_violation_windows: int
    required_violation_windows: int
    reevaluation_triggered: bool
    reevaluation_run_id: str | None


SnapshotCollector = Callable[[], MetricsSnapshot]
ViolationHandler = Callable[[PostDeploymentSummary], str | None]


class PostDeploymentMonitor:
    def __init__(
        self,
        *,
        run_id: str,
        submission: IntentFunction,
        cluster_name: str,
        url: str,
        snapshot_collector: SnapshotCollector,
        output_directory: Path,
        interval_seconds: float = 10.0,
        window_size: int = 10,
        minimum_samples: int = 3,
        request_timeout_seconds: float = 5.0,
        candidate_cluster_names: list[str] | None = None,
        violation_handler: ViolationHandler | None = None,
        consecutive_violation_windows: int = 3,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                "interval_seconds must be positive"
            )
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        if not 1 <= minimum_samples <= window_size:
            raise ValueError(
                "minimum_samples must be between 1 and window_size"
            )
        if consecutive_violation_windows <= 0:
            raise ValueError(
                "consecutive_violation_windows must be positive"
            )

        self.run_id = run_id
        self.submission = submission
        self.cluster_name = cluster_name
        self.url = url
        self.snapshot_collector = snapshot_collector
        self.output_directory = output_directory
        self.interval_seconds = interval_seconds
        self.window_size = window_size
        self.minimum_samples = minimum_samples
        self.request_timeout_seconds = request_timeout_seconds
        self.candidate_cluster_names = sorted(
            set(candidate_cluster_names or [cluster_name])
        )
        self.violation_handler = violation_handler
        self.consecutive_violation_windows = (
            consecutive_violation_windows
        )

        self.output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._samples: deque[PostDeploymentSample] = deque(
            maxlen=window_size
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._consecutive_violations = 0
        self._reevaluation_triggered = False
        self._reevaluation_run_id: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"post-deployment-monitor-{self.run_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(
                timeout=max(5.0, self.interval_seconds + 1)
            )

    def collect_once(self) -> PostDeploymentSummary:
        invocation = self._invoke()
        snapshot: MetricsSnapshot | None = None
        snapshot_error: str | None = None

        try:
            snapshot = self.snapshot_collector()
        except Exception as error:
            logger.exception(
                "Post-deployment resource collection failed "
                "for %s",
                self.cluster_name,
            )
            snapshot_error = (
                f"{type(error).__name__}: {error}"
            )

        sample = self._build_sample(
            invocation=invocation,
            snapshot=snapshot,
            snapshot_error=snapshot_error,
        )

        with self._lock:
            self._samples.append(sample)
            summary = self._build_summary(
                list(self._samples)
            )

        self._append_sample(sample)
        summary = self._handle_control_loop(summary)
        self._write_summary(summary)
        return summary

    def reset_violation_action(self) -> None:
        """Allow a persistent violation to trigger another evaluation.

        The manager calls this when an automatically triggered orchestration
        fails. The still-running monitor can then collect a fresh sequence of
        violated windows and retry after the configured cooldown.
        """
        with self._action_lock:
            self._consecutive_violations = 0
            self._reevaluation_triggered = False
            self._reevaluation_run_id = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            started_at = time.monotonic()

            try:
                self.collect_once()
            except Exception:
                logger.exception(
                    "Post-deployment monitoring iteration failed "
                    "for run %s",
                    self.run_id,
                )

            elapsed = time.monotonic() - started_at
            self._stop_event.wait(
                max(0.0, self.interval_seconds - elapsed)
            )

    def _invoke(
        self,
    ) -> tuple[bool, int | None, float | None, str | None]:
        started_at = time.perf_counter()

        try:
            with urllib.request.urlopen(
                self.url,
                timeout=self.request_timeout_seconds,
            ) as response:
                response.read()
                latency = round(
                    (time.perf_counter() - started_at)
                    * 1000,
                    3,
                )
                succeeded = 200 <= response.status < 300
                return (
                    succeeded,
                    response.status,
                    latency,
                    None if succeeded else f"HTTP {response.status}",
                )
        except urllib.error.HTTPError as error:
            error.read()
            return (
                False,
                error.code,
                round(
                    (time.perf_counter() - started_at)
                    * 1000,
                    3,
                ),
                f"HTTP {error.code}",
            )
        except (
            TimeoutError,
            urllib.error.URLError,
        ) as error:
            return (
                False,
                None,
                round(
                    (time.perf_counter() - started_at)
                    * 1000,
                    3,
                ),
                str(error),
            )

    def _build_sample(
        self,
        *,
        invocation: tuple[
            bool,
            int | None,
            float | None,
            str | None,
        ],
        snapshot: MetricsSnapshot | None,
        snapshot_error: str | None,
    ) -> PostDeploymentSample:
        succeeded, status, latency, invocation_error = invocation
        candidate_clusters = self._cluster_samples(snapshot)
        vm = (
            snapshot.vm_metrics.get(self.cluster_name)
            if snapshot is not None
            else None
        )
        pods = (
            [
                pod
                for pod in snapshot.pod_metrics.values()
                if pod.cluster_name == self.cluster_name
                and pod.namespace
                == self.submission.function.namespace
                and pod.pod_name.startswith(
                    self.submission.function.service_name
                )
                and "-benchmark" not in pod.pod_name
            ]
            if snapshot is not None
            else []
        )

        available_cpu: float | None = None

        if vm is not None:
            available_cpu = max(
                0.0,
                vm.cpu_core_count
                * (1.0 - vm.cpu_usage_percent / 100.0),
            )

        errors = [
            value
            for value in (
                invocation_error,
                snapshot_error,
            )
            if value
        ]

        return PostDeploymentSample(
            timestamp=_now(),
            run_id=self.run_id,
            cluster_name=self.cluster_name,
            url=self.url,
            invocation_succeeded=succeeded,
            status_code=status,
            latency_ms=latency,
            error="; ".join(errors) if errors else None,
            vm_cpu_usage_percent=(
                vm.cpu_usage_percent if vm is not None else None
            ),
            vm_memory_usage_percent=(
                vm.memory_usage_percent if vm is not None else None
            ),
            vm_available_cpu_cores=(
                round(available_cpu, 4)
                if available_cpu is not None
                else None
            ),
            vm_available_memory_bytes=(
                vm.memory_available_bytes
                if vm is not None
                else None
            ),
            function_pod_count=len(pods),
            function_pod_cpu_millicores=round(
                sum(pod.cpu_usage_millicores for pod in pods),
                4,
            ),
            function_pod_memory_bytes=sum(
                pod.memory_usage_bytes for pod in pods
            ),
            candidate_clusters=candidate_clusters,
        )

    def _cluster_samples(
        self,
        snapshot: MetricsSnapshot | None,
    ) -> list[ClusterMonitoringSample]:
        samples: list[ClusterMonitoringSample] = []

        for cluster_name in self.candidate_cluster_names:
            vm = (
                snapshot.vm_metrics.get(cluster_name)
                if snapshot is not None
                else None
            )
            nodes = (
                [
                    node
                    for node in snapshot.node_metrics.values()
                    if node.cluster_name == cluster_name
                ]
                if snapshot is not None
                else []
            )
            worker_nodes = [
                node
                for node in nodes
                if node.node_role.lower()
                not in {"control-plane", "master"}
            ]
            schedulable_nodes = worker_nodes or nodes
            pods = (
                [
                    pod
                    for pod in snapshot.pod_metrics.values()
                    if pod.cluster_name == cluster_name
                ]
                if snapshot is not None
                else []
            )
            function_pods = [
                pod
                for pod in pods
                if pod.namespace
                == self.submission.function.namespace
                and pod.pod_name.startswith(
                    self.submission.function.service_name
                )
                and "-benchmark" not in pod.pod_name
            ]
            available_cpu: float | None = None

            if vm is not None:
                available_cpu = max(
                    0.0,
                    vm.cpu_core_count
                    * (1.0 - vm.cpu_usage_percent / 100.0),
                )

            samples.append(
                ClusterMonitoringSample(
                    cluster_name=cluster_name,
                    selected=cluster_name == self.cluster_name,
                    vm_reachable=vm is not None,
                    node_metrics_available=bool(nodes),
                    node_count=len(nodes),
                    worker_count=len(worker_nodes),
                    pod_count=len(pods),
                    function_pod_count=len(function_pods),
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
                    vm_available_cpu_cores=(
                        round(available_cpu, 4)
                        if available_cpu is not None
                        else None
                    ),
                    vm_available_memory_bytes=(
                        vm.memory_available_bytes
                        if vm is not None
                        else None
                    ),
                    average_worker_cpu_usage_percent=(
                        round(
                            sum(
                                node.cpu_usage_percent
                                for node in schedulable_nodes
                            )
                            / len(schedulable_nodes),
                            4,
                        )
                        if schedulable_nodes
                        else None
                    ),
                    average_worker_memory_usage_percent=(
                        round(
                            sum(
                                node.memory_usage_percent
                                for node in schedulable_nodes
                            )
                            / len(schedulable_nodes),
                            4,
                        )
                        if schedulable_nodes
                        else None
                    ),
                    function_pod_cpu_millicores=round(
                        sum(
                            pod.cpu_usage_millicores
                            for pod in function_pods
                        ),
                        4,
                    ),
                    function_pod_memory_bytes=sum(
                        pod.memory_usage_bytes
                        for pod in function_pods
                    ),
                )
            )

        return samples

    def _build_summary(
        self,
        samples: list[PostDeploymentSample],
    ) -> PostDeploymentSummary:
        successful = [
            sample
            for sample in samples
            if sample.invocation_succeeded
            and sample.latency_ms is not None
        ]
        latencies = [
            float(sample.latency_ms)
            for sample in successful
            if sample.latency_ms is not None
        ]
        success_rate = (
            len(successful) / len(samples)
            if samples
            else 0.0
        )
        observed = {
            "p95_latency_ms": (
                percentile(latencies, 95)
                if latencies
                else None
            ),
            "p50_latency_ms": (
                percentile(latencies, 50)
                if latencies
                else None
            ),
            "average_latency_ms": (
                sum(latencies) / len(latencies)
                if latencies
                else None
            ),
            "success_rate": success_rate,
            "vm_cpu_usage_percent": (
                samples[-1].vm_cpu_usage_percent
                if samples
                else None
            ),
            "vm_memory_usage_percent": (
                samples[-1].vm_memory_usage_percent
                if samples
                else None
            ),
            "available_cpu_cores": (
                samples[-1].vm_available_cpu_cores
                if samples
                else None
            ),
            "available_memory_bytes": (
                float(
                    samples[-1].vm_available_memory_bytes
                )
                if samples
                and samples[-1].vm_available_memory_bytes
                is not None
                else None
            ),
        }
        objectives = [
            self._evaluate(objective, observed)
            for objective in self.submission.intent.objectives
        ]
        constraints = [
            self._evaluate(constraint, observed)
            for constraint in self.submission.intent.constraints
        ]

        if len(samples) < self.minimum_samples:
            state = "warming-up"
            intent_satisfied: bool | None = None
        else:
            all_evaluations = [*objectives, *constraints]
            intent_satisfied = bool(all_evaluations) and all(
                evaluation.supported
                and evaluation.satisfied is True
                for evaluation in all_evaluations
            )
            state = (
                "intent-satisfied"
                if intent_satisfied
                else "intent-violated"
            )

        return PostDeploymentSummary(
            timestamp=_now(),
            run_id=self.run_id,
            cluster_name=self.cluster_name,
            service_name=self.submission.function.service_name,
            url=self.url,
            state=state,
            window_size=len(samples),
            required_window_size=self.minimum_samples,
            successful_probes=len(successful),
            failed_probes=len(samples) - len(successful),
            success_rate=round(success_rate, 4),
            average_latency_ms=(
                round(sum(latencies) / len(latencies), 3)
                if latencies
                else None
            ),
            p50_latency_ms=(
                round(percentile(latencies, 50), 3)
                if latencies
                else None
            ),
            p95_latency_ms=(
                round(percentile(latencies, 95), 3)
                if latencies
                else None
            ),
            intent_satisfied=intent_satisfied,
            objective_evaluations=objectives,
            constraint_evaluations=constraints,
            latest_sample=samples[-1] if samples else None,
            control_loop_enabled=self.violation_handler is not None,
            consecutive_violation_windows=0,
            required_violation_windows=(
                self.consecutive_violation_windows
            ),
            reevaluation_triggered=False,
            reevaluation_run_id=None,
        )

    def _handle_control_loop(
        self,
        summary: PostDeploymentSummary,
    ) -> PostDeploymentSummary:
        if self.violation_handler is None:
            return summary

        with self._action_lock:
            if summary.state == "intent-violated":
                self._consecutive_violations += 1
            elif not self._reevaluation_triggered:
                self._consecutive_violations = 0

            should_trigger = (
                summary.state == "intent-violated"
                and self._consecutive_violations
                >= self.consecutive_violation_windows
                and not self._reevaluation_triggered
            )

            if should_trigger:
                callback_summary = replace(
                    summary,
                    consecutive_violation_windows=(
                        self._consecutive_violations
                    ),
                )

                try:
                    reevaluation_run_id = self.violation_handler(
                        callback_summary
                    )
                except Exception:
                    logger.exception(
                        "Intent-violation handler failed for run %s",
                        self.run_id,
                    )
                    reevaluation_run_id = None

                if reevaluation_run_id is not None:
                    self._reevaluation_triggered = True
                    self._reevaluation_run_id = reevaluation_run_id

            return replace(
                summary,
                consecutive_violation_windows=(
                    self._consecutive_violations
                ),
                reevaluation_triggered=(
                    self._reevaluation_triggered
                ),
                reevaluation_run_id=self._reevaluation_run_id,
            )

    def _evaluate(
        self,
        requirement: Objective,
        observed: dict[str, float | None],
    ) -> ObjectiveEvaluation:
        value = self._observed_value(requirement, observed)
        comparator = COMPARATORS.get(requirement.operator)
        supported = value is not None and comparator is not None
        satisfied = (
            bool(comparator(value, requirement.value))
            if supported and comparator is not None and value is not None
            else None
        )

        return ObjectiveEvaluation(
            name=requirement.name,
            measured_by=requirement.measured_by,
            operator=requirement.operator,
            target=requirement.value,
            unit=requirement.unit,
            observed=(
                round(value, 4)
                if value is not None
                else None
            ),
            supported=supported,
            satisfied=satisfied,
        )

    def _observed_value(
        self,
        requirement: Objective,
        observed: dict[str, float | None],
    ) -> float | None:
        text = (
            f"{requirement.measured_by} {requirement.name}"
        ).lower()

        if "p95" in text:
            return observed["p95_latency_ms"]
        if "p50" in text:
            return observed["p50_latency_ms"]
        if any(term in text for term in ("average", "avg", "mean")):
            return observed["average_latency_ms"]
        if "success" in text or "availability" in text:
            return observed["success_rate"]
        if "available" in text and "cpu" in text:
            return observed["available_cpu_cores"]
        if "available" in text and "memory" in text:
            return self._convert_bytes(
                observed["available_memory_bytes"],
                requirement.unit,
            )
        if "cpu" in text and (
            "usage" in text or "load" in text
        ):
            return observed["vm_cpu_usage_percent"]
        if "memory" in text and (
            "usage" in text or "load" in text
        ):
            return observed["vm_memory_usage_percent"]
        if "latency" in text or "response" in text:
            return observed["p95_latency_ms"]

        return None

    def _convert_bytes(
        self,
        value: float | None,
        unit: str | None,
    ) -> float | None:
        if value is None:
            return None

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
        divisor = divisors.get((unit or "bytes").lower())
        return value / divisor if divisor is not None else None

    def _append_sample(
        self,
        sample: PostDeploymentSample,
    ) -> None:
        output_file = self.output_directory / "samples.jsonl"

        with output_file.open("a", encoding="utf-8") as file:
            json.dump(asdict(sample), file)
            file.write("\n")

    def _write_summary(
        self,
        summary: PostDeploymentSummary,
    ) -> None:
        output_file = self.output_directory / "latest-summary.json"
        temporary = output_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(summary), indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output_file)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
