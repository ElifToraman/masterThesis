from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean


def nearest_rank_percentile(
    values: tuple[float, ...],
    percentile: float,
) -> float:
    if not values:
        raise ValueError(
            "Cannot calculate a percentile without values"
        )

    if percentile <= 0 or percentile > 100:
        raise ValueError(
            "percentile must be greater than zero "
            "and less than or equal to 100"
        )

    ordered_values = sorted(values)

    rank = math.ceil(
        percentile
        / 100
        * len(ordered_values)
    )

    index = rank - 1

    return ordered_values[index]


@dataclass
class ClusterBenchmarkResult:
    timestamp: datetime

    cluster_name: str
    kubernetes_context: str

    function_name: str
    benchmark_service_name: str
    image_reference: str
    endpoint: str

    deployment_duration_ms: float

    first_invocation_latency_ms: float
    first_invocation_status_code: int

    warm_latency_samples_ms: tuple[float, ...]

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
            raise ValueError(
                "No successful warm latency samples"
            )

        return mean(
            self.warm_latency_samples_ms
        )

    @property
    def p50_warm_latency_ms(self) -> float:
        return nearest_rank_percentile(
            self.warm_latency_samples_ms,
            50,
        )

    @property
    def p95_warm_latency_ms(self) -> float:
        return nearest_rank_percentile(
            self.warm_latency_samples_ms,
            95,
        )

    @property
    def p99_warm_latency_ms(self) -> float:
        return nearest_rank_percentile(
            self.warm_latency_samples_ms,
            99,
        )
