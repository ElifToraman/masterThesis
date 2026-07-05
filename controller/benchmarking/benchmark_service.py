# controller/benchmarking/benchmark_service.py

from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .models import (
    BenchmarkRequest,
    ClusterBenchmarkResult,
)
from .platform import FunctionPlatform


class BenchmarkService:
    def __init__(
        self,
        platform: FunctionPlatform,
    ) -> None:
        self._platform = platform

    def benchmark_cluster(
        self,
        cluster_name: str,
        kubernetes_context: str,
        request: BenchmarkRequest,
    ) -> ClusterBenchmarkResult:
        service_name = request.benchmark_service_name

        if self._platform.service_exists(
            kubernetes_context=kubernetes_context,
            namespace=request.namespace,
            service_name=service_name,
        ):
            self._platform.delete_service(
                kubernetes_context=kubernetes_context,
                namespace=request.namespace,
                service_name=service_name,
            )

        try:
            deployment_started = time.perf_counter()

            self._platform.deploy_service(
                kubernetes_context=kubernetes_context,
                request=request,
            )

            self._platform.wait_until_ready(
                kubernetes_context=kubernetes_context,
                namespace=request.namespace,
                service_name=service_name,
                timeout_seconds=(
                    request.deployment_timeout_seconds
                ),
            )

            deployment_duration_ms = (
                time.perf_counter() - deployment_started
            ) * 1000

            endpoint = self._platform.get_service_url(
                kubernetes_context=kubernetes_context,
                namespace=request.namespace,
                service_name=service_name,
            )

            (
                first_latency_ms,
                first_status_code,
            ) = self._invoke(
                endpoint=endpoint,
                request=request,
            )

            for _ in range(request.warmup_requests):
                self._invoke(
                    endpoint=endpoint,
                    request=request,
                )

            warm_samples: list[float] = []
            successful_requests = 0
            failed_requests = 0

            for _ in range(request.measured_requests):
                try:
                    latency_ms, status_code = self._invoke(
                        endpoint=endpoint,
                        request=request,
                    )

                    if 200 <= status_code < 300:
                        warm_samples.append(latency_ms)
                        successful_requests += 1
                    else:
                        failed_requests += 1

                except (
                    TimeoutError,
                    urllib.error.URLError,
                    RuntimeError,
                ):
                    failed_requests += 1

            return ClusterBenchmarkResult(
                timestamp=datetime.now(timezone.utc),
                cluster_name=cluster_name,
                kubernetes_context=kubernetes_context,
                function_name=request.function_name,
                benchmark_service_name=service_name,
                image_reference=request.image_reference,
                endpoint=endpoint,
                deployment_duration_ms=round(
                    deployment_duration_ms,
                    3,
                ),
                first_invocation_latency_ms=round(
                    first_latency_ms,
                    3,
                ),
                first_invocation_status_code=(
                    first_status_code
                ),
                warm_latency_samples_ms=tuple(
                    warm_samples
                ),
                successful_requests=successful_requests,
                failed_requests=failed_requests,
            )

        finally:
            self._platform.delete_service(
                kubernetes_context=kubernetes_context,
                namespace=request.namespace,
                service_name=service_name,
            )

    def _invoke(
        self,
        endpoint: str,
        request: BenchmarkRequest,
    ) -> tuple[float, int]:
        headers: dict[str, str] = {}

        if request.content_type is not None:
            headers["Content-Type"] = request.content_type

        http_request = urllib.request.Request(
            url=endpoint,
            data=request.request_body,
            headers=headers,
            method=request.http_method,
        )

        started_at = time.perf_counter()

        try:
            with urllib.request.urlopen(
                http_request,
                timeout=request.request_timeout_seconds,
            ) as response:
                response.read()

                latency_ms = (
                    time.perf_counter() - started_at
                ) * 1000

                return latency_ms, response.status

        except urllib.error.HTTPError as error:
            error.read()

            latency_ms = (
                time.perf_counter() - started_at
            ) * 1000

            return latency_ms, error.code
