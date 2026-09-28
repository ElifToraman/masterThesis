from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import shlex
from statistics import mean
import time

from controller.monitoring.vm import VM


@dataclass(frozen=True)
class BenchmarkResourceSample:
    cpu_usage_cores: float
    memory_usage_bytes: int
    # Original cAdvisor timestamps, not the time at which we polled Prometheus.
    observation_timestamps: tuple[float, ...]


@dataclass(frozen=True)
class BenchmarkResourceSummary:
    average_cpu_usage_cores: float
    peak_cpu_usage_cores: float
    average_memory_usage_bytes: int
    peak_memory_usage_bytes: int


class BenchmarkResourceSampler:
    def __init__(
        self,
        vms_by_cluster: dict[str, VM],
    ) -> None:
        self._vms_by_cluster = vms_by_cluster
        self._containers: dict[tuple[str, str, str], set[tuple[str, str]]] = {}

    def prepare(
        self,
        *,
        cluster_name: str,
        namespace: str,
        benchmark_service_name: str,
    ) -> None:
        """Pin collection to the live pods and all their regular containers.

        A prefix-only Prometheus lookup can include a deleted previous run or
        just queue-proxy before the user-container's metrics have appeared.
        """
        vm = self._vms_by_cluster[cluster_name]
        command = shlex.join(
            [
                "kubectl",
                "get",
                "pods",
                "--namespace",
                namespace,
                "--selector",
                f"serving.knative.dev/service={benchmark_service_name}",
                "--output",
                "json",
                "--request-timeout=10s",
            ]
        )
        result = vm._run_ssh_command(command)
        if result.returncode != 0:
            raise RuntimeError(f"Cannot discover benchmark containers: {result.stderr}")
        pods = json.loads(result.stdout)["items"]
        containers = {
            (pod["metadata"]["name"], container["name"])
            for pod in pods
            if not pod["metadata"].get("deletionTimestamp")
            and pod.get("status", {}).get("phase") == "Running"
            for container in pod["spec"]["containers"]
        }
        if not containers:
            raise RuntimeError("No running benchmark containers found")
        self._containers[(cluster_name, namespace, benchmark_service_name)] = containers

    def sample(
        self,
        *,
        cluster_name: str,
        namespace: str,
        benchmark_service_name: str,
    ) -> BenchmarkResourceSample | None:
        vm = self._vms_by_cluster.get(cluster_name)

        if vm is None:
            raise RuntimeError(f"No resource collector for {cluster_name}")
        expected = self._containers[(cluster_name, namespace, benchmark_service_name)]
        pod_pattern = "|".join(
            re.escape(pod) for pod in sorted({p for p, _ in expected})
        )
        selector = (
            f"namespace={json.dumps(namespace)},pod=~{json.dumps(pod_pattern)},"
            'container!="",container!="POD"'
        )
        cpu = f"container_cpu_usage_seconds_total{{{selector}}}"
        memory = f"container_memory_working_set_bytes{{{selector}}}"
        results = vm.query_prometheus_many(
            {
                "cpu": f"sum by (pod, container) (rate({cpu}[1m]))",
                "memory": f"sum by (pod, container) ({memory})",
                "cpu_timestamp": f"min by (pod, container) (timestamp({cpu}))",
                "memory_timestamp": f"min by (pod, container) (timestamp({memory}))",
            }
        )
        values: dict[str, dict[tuple[str, str], float]] = {}
        for metric_name, rows in results.items():
            values[metric_name] = {}
            for row in rows:
                labels = row.get("metric", {})
                key = (labels.get("pod"), labels.get("container"))
                if key not in expected:
                    continue
                try:
                    value = float(row["value"][1])
                except (KeyError, IndexError, TypeError, ValueError):
                    return None
                if not math.isfinite(value) or value < 0:
                    return None
                if key in values[metric_name]:
                    return None
                values[metric_name][key] = value

        # Missing CPU (including an absent rate with too few counter samples)
        # is unavailable data, never a measured zero. Require proxy AND user.
        if any(
            set(values.get(metric, {})) != expected
            for metric in (
                "cpu",
                "memory",
                "cpu_timestamp",
                "memory_timestamp",
            )
        ):
            return None
        timestamps = tuple(
            values[metric][key]
            for key in sorted(expected)
            for metric in ("cpu_timestamp", "memory_timestamp")
        )
        if time.time() - min(timestamps) > 60:
            return None
        return BenchmarkResourceSample(
            cpu_usage_cores=sum(values["cpu"].values()),
            memory_usage_bytes=int(sum(values["memory"].values())),
            observation_timestamps=timestamps,
        )

    @staticmethod
    def summarize(
        samples: list[BenchmarkResourceSample],
    ) -> BenchmarkResourceSummary | None:
        if not samples:
            return None

        cpu_values = [sample.cpu_usage_cores for sample in samples]

        memory_values = [sample.memory_usage_bytes for sample in samples]

        return BenchmarkResourceSummary(
            average_cpu_usage_cores=round(
                mean(cpu_values),
                6,
            ),
            peak_cpu_usage_cores=round(
                max(cpu_values),
                6,
            ),
            average_memory_usage_bytes=int(
                mean(memory_values),
            ),
            peak_memory_usage_bytes=max(memory_values),
        )
