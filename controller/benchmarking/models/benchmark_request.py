from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkRequest:
    function_name: str
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

    request_timeout_seconds: float = 10.0
    deployment_timeout_seconds: float = 180.0

    def __post_init__(self) -> None:
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