# controller/models/cluster_candidate.py

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClusterCandidate:
    cluster_name: str

    node_count: int
    worker_count: int

    total_cpu_cores: int
    available_cpu_cores: float
    average_cpu_usage_percent: float

    total_memory_bytes: int
    available_memory_bytes: int
    memory_usage_percent: float

    function_deployed: bool

    benchmark_success_rate: float | None
    benchmark_average_latency_ms: float | None
    benchmark_p95_latency_ms: float | None
    benchmark_first_invocation_latency_ms: float | None
    benchmark_deployment_duration_ms: float | None

    @property
    def has_benchmark(self) -> bool:
        return self.benchmark_p95_latency_ms is not None
