from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkRequest:
    run_id: str
    function_name: str
    function_version: str
    benchmark_service_name: str
    namespace: str
    image_reference: str
    minimum_scale: int = 1
    maximum_scale: int = 1
    container_concurrency: int = 1

    http_method: str = "GET"
    request_body: bytes | None = None
    content_type: str | None = None

    warmup_requests: int = 3
    measured_requests: int = 20
    concurrency: int = 1
    measurement_duration_seconds: float = 0.0
    resource_sample_interval_seconds: float = 1.0

    request_timeout_seconds: float = 10.0
    deployment_timeout_seconds: float = 180.0

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError(
                "run_id must not be empty"
            )

        if not self.function_name.strip():
            raise ValueError(
                "function_name must not be empty"
            )

        if not self.benchmark_service_name.strip():
            raise ValueError(
                "benchmark_service_name must not be empty"
            )

        if not self.namespace.strip():
            raise ValueError(
                "namespace must not be empty"
            )

        if not self.image_reference.strip():
            raise ValueError(
                "image_reference must not be empty"
            )

        if self.http_method not in {"GET", "POST"}:
            raise ValueError(
                "http_method must be GET or POST"
            )

        if self.warmup_requests < 0:
            raise ValueError(
                "warmup_requests must be zero or greater"
            )

        if self.measured_requests <= 0:
            raise ValueError(
                "measured_requests must be greater than zero"
            )

        if self.concurrency <= 0:
            raise ValueError(
                "concurrency must be greater than zero"
            )

        if self.measurement_duration_seconds < 0:
            raise ValueError(
                "measurement_duration_seconds must be "
                "zero or greater"
            )

        if self.resource_sample_interval_seconds <= 0:
            raise ValueError(
                "resource_sample_interval_seconds must be "
                "greater than zero"
            )

        if self.request_timeout_seconds <= 0:
            raise ValueError(
                "request_timeout_seconds must be "
                "greater than zero"
            )

        if self.deployment_timeout_seconds <= 0:
            raise ValueError(
                "deployment_timeout_seconds must be "
                "greater than zero"
            )
