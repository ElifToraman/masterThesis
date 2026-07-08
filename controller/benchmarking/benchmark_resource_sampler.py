from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from controller.monitoring.vm import VM


@dataclass(frozen=True)
class BenchmarkResourceSample:
    cpu_usage_cores: float
    memory_usage_bytes: int


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

    def sample(
        self,
        *,
        cluster_name: str,
        namespace: str,
        benchmark_service_name: str,
    ) -> BenchmarkResourceSample | None:
        vm = self._vms_by_cluster.get(cluster_name)

        if vm is None:
            return None

        pod_metrics = vm.gather_all_pod_metrics()

        matching_pods = [
            metrics
            for (pod_namespace, pod_name), metrics in pod_metrics.items()
            if pod_namespace == namespace
            and pod_name.startswith(benchmark_service_name)
        ]

        if not matching_pods:
            return None

        return BenchmarkResourceSample(
            cpu_usage_cores=sum(
                pod.cpu_usage_cores for pod in matching_pods
            ),
            memory_usage_bytes=sum(
                pod.memory_usage_bytes for pod in matching_pods
            ),
        )

    @staticmethod
    def summarize(
        samples: list[BenchmarkResourceSample],
    ) -> BenchmarkResourceSummary | None:
        if not samples:
            return None

        cpu_values = [
            sample.cpu_usage_cores for sample in samples
        ]

        memory_values = [
            sample.memory_usage_bytes for sample in samples
        ]

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