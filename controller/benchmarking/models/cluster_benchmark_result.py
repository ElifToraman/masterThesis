from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean

@dataclass(frozen=True)
class ClusterBenchmarkResult:
    timestamp: datetime

    cluster_name: str
    kubernetes_context: str

    function_name: str
    function_version: str

    benchmark_service_name: str
    image_reference: str
    endpoint: str

    deployment_duration_ms: float

    first_invocation_latency_ms: float
    first_invocation_status_code: int | None

    warm_latency_samples_ms: list[float]

    successful_requests: int
    failed_requests: int

    @property
    def total_requests(self) -> int:
        return (
            self.successful_requests
            + self.failed_requests
        )

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0

        return (
            self.successful_requests
            / self.total_requests
        )

    @property
    def average_warm_latency_ms(self) -> float:
        if not self.warm_latency_samples_ms:
            return 0.0

        return sum(
            self.warm_latency_samples_ms
        ) / len(self.warm_latency_samples_ms)

    @property
    def p50_warm_latency_ms(self) -> float:
        return percentile(
            self.warm_latency_samples_ms,
            50,
        )

    @property
    def p95_warm_latency_ms(self) -> float:
        return percentile(
            self.warm_latency_samples_ms,
            95,
        )


def percentile(
    values: list[float],
    percentile_value: float,
) -> float:
    if not values:
        return 0.0

    sorted_values = sorted(values)

    index = (
        percentile_value
        / 100
        * (len(sorted_values) - 1)
    )

    lower_index = int(index)
    upper_index = min(
        lower_index + 1,
        len(sorted_values) - 1,
    )

    weight = index - lower_index

    return (
        sorted_values[lower_index]
        * (1 - weight)
        + sorted_values[upper_index]
        * weight
    )