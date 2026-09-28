from __future__ import annotations

from dataclasses import dataclass

from controller.function_profiles import InvocationProfile


@dataclass(frozen=True)
class BenchmarkRequest:
    run_id: str
    function_name: str
    function_version: str
    benchmark_service_name: str
    namespace: str
    image_reference: str
    invocation: InvocationProfile
    minimum_scale: int = 1
    maximum_scale: int = 1
    container_concurrency: int = 1

    warmup_requests: int = 3
    measured_requests: int = 20
    concurrency: int = 1
    measurement_duration_seconds: float = 0.0
    resource_sample_interval_seconds: float = 1.0
    resource_warmup_timeout_seconds: float = 90.0
    minimum_resource_samples: int = 3

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

        if self.resource_warmup_timeout_seconds <= 0:
            raise ValueError("resource_warmup_timeout_seconds must be positive")
        if self.minimum_resource_samples < 1:
            raise ValueError("minimum_resource_samples must be positive")

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
