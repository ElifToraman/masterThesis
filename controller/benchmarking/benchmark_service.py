from __future__ import annotations

import time
import urllib.error
import urllib.request
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from datetime import datetime, timezone

from .benchmark_resource_sampler import (
    BenchmarkResourceSample,
    BenchmarkResourceSampler,
)
from .models import (
    BenchmarkRequest,
    ClusterBenchmarkResult,
)
from .platform import FunctionPlatform


class BenchmarkService:
    def __init__(
        self,
        platform: FunctionPlatform,
        resource_sampler: BenchmarkResourceSampler | None = None,
    ) -> None:
        self._platform = platform
        self._resource_sampler = resource_sampler

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
                timeout_seconds=request.deployment_timeout_seconds,
            )

            deployment_duration_ms = (
                time.perf_counter() - deployment_started
            ) * 1000

            endpoint = self._platform.get_service_url(
                kubernetes_context=kubernetes_context,
                namespace=request.namespace,
                service_name=service_name,
            )

            first_latency_ms, first_status_code = self._invoke(
                endpoint=endpoint,
                request=request,
            )

            for _ in range(request.warmup_requests):
                self._invoke(
                    endpoint=endpoint,
                    request=request,
                )

            (
                warm_samples,
                successful_requests,
                failed_requests,
                measurement_duration_seconds,
                resource_samples,
            ) = self._run_measured_load(
                cluster_name=cluster_name,
                endpoint=endpoint,
                request=request,
            )

            resource_summary = None

            if self._resource_sampler is not None:
                resource_summary = self._resource_sampler.summarize(
                    resource_samples
                )

            return ClusterBenchmarkResult(
                timestamp=datetime.now(timezone.utc),
                run_id=request.run_id,
                cluster_name=cluster_name,
                kubernetes_context=kubernetes_context,
                function_name=request.function_name,
                benchmark_service_name=service_name,
                image_reference=request.image_reference,
                function_version=request.function_version,
                endpoint=endpoint,
                deployment_duration_ms=round(
                    deployment_duration_ms,
                    3,
                ),
                first_invocation_latency_ms=round(
                    first_latency_ms,
                    3,
                ),
                first_invocation_status_code=first_status_code,
                warm_latency_samples_ms=tuple(warm_samples),
                successful_requests=successful_requests,
                failed_requests=failed_requests,
                benchmark_concurrency=request.concurrency,
                measurement_duration_seconds=round(
                    measurement_duration_seconds,
                    3,
                ),
                average_cpu_usage_cores=(
                    resource_summary.average_cpu_usage_cores
                    if resource_summary is not None
                    else None
                ),
                peak_cpu_usage_cores=(
                    resource_summary.peak_cpu_usage_cores
                    if resource_summary is not None
                    else None
                ),
                average_memory_usage_bytes=(
                    resource_summary.average_memory_usage_bytes
                    if resource_summary is not None
                    else None
                ),
                peak_memory_usage_bytes=(
                    resource_summary.peak_memory_usage_bytes
                    if resource_summary is not None
                    else None
                ),
            )

        finally:
            self._platform.delete_service(
                kubernetes_context=kubernetes_context,
                namespace=request.namespace,
                service_name=service_name,
            )

    def _run_measured_load(
        self,
        *,
        cluster_name: str,
        endpoint: str,
        request: BenchmarkRequest,
    ) -> tuple[
        list[float],
        int,
        int,
        float,
        list[BenchmarkResourceSample],
    ]:
        started_at = time.monotonic()
        deadline = (
            started_at + request.measurement_duration_seconds
            if request.measurement_duration_seconds > 0
            else None
        )

        with ThreadPoolExecutor(
            max_workers=request.concurrency,
            thread_name_prefix="benchmark-request",
        ) as executor:
            futures = self._start_load_workers(
                executor=executor,
                endpoint=endpoint,
                request=request,
                deadline=deadline,
            )
            resource_samples = self._sample_while_running(
                futures=futures,
                cluster_name=cluster_name,
                request=request,
            )

        samples: list[float] = []
        successful = 0
        failed = 0

        for future in futures:
            worker_samples, worker_successful, worker_failed = (
                future.result()
            )
            samples.extend(worker_samples)
            successful += worker_successful
            failed += worker_failed

        duration = time.monotonic() - started_at

        return (
            samples,
            successful,
            failed,
            duration,
            resource_samples,
        )

    def _start_load_workers(
        self,
        *,
        executor: ThreadPoolExecutor,
        endpoint: str,
        request: BenchmarkRequest,
        deadline: float | None,
    ) -> list[
        Future[tuple[list[float], int, int]]
    ]:
        if deadline is not None:
            return [
                executor.submit(
                    self._duration_worker,
                    endpoint,
                    request,
                    deadline,
                )
                for _ in range(request.concurrency)
            ]

        request_counts = [
            request.measured_requests
            // request.concurrency
            for _ in range(request.concurrency)
        ]

        for index in range(
            request.measured_requests % request.concurrency
        ):
            request_counts[index] += 1

        return [
            executor.submit(
                self._fixed_count_worker,
                endpoint,
                request,
                count,
            )
            for count in request_counts
            if count > 0
        ]

    def _duration_worker(
        self,
        endpoint: str,
        request: BenchmarkRequest,
        deadline: float,
    ) -> tuple[list[float], int, int]:
        samples: list[float] = []
        successful = 0
        failed = 0

        while time.monotonic() < deadline:
            result = self._measure_one(endpoint, request)

            if result is None:
                failed += 1
            else:
                latency, succeeded = result

                if succeeded:
                    samples.append(latency)
                    successful += 1
                else:
                    failed += 1

        return samples, successful, failed

    def _fixed_count_worker(
        self,
        endpoint: str,
        request: BenchmarkRequest,
        count: int,
    ) -> tuple[list[float], int, int]:
        samples: list[float] = []
        successful = 0
        failed = 0

        for _ in range(count):
            result = self._measure_one(endpoint, request)

            if result is None:
                failed += 1
            else:
                latency, succeeded = result

                if succeeded:
                    samples.append(latency)
                    successful += 1
                else:
                    failed += 1

        return samples, successful, failed

    def _measure_one(
        self,
        endpoint: str,
        request: BenchmarkRequest,
    ) -> tuple[float, bool] | None:
        try:
            latency_ms, status_code = self._invoke(
                endpoint=endpoint,
                request=request,
            )
        except (
            TimeoutError,
            urllib.error.URLError,
            RuntimeError,
        ):
            return None

        return latency_ms, 200 <= status_code < 300

    def _sample_while_running(
        self,
        *,
        futures: list[Future],
        cluster_name: str,
        request: BenchmarkRequest,
    ) -> list[BenchmarkResourceSample]:
        samples: list[BenchmarkResourceSample] = []

        if self._resource_sampler is None:
            return samples

        while any(not future.done() for future in futures):
            sample = self._resource_sampler.sample(
                cluster_name=cluster_name,
                namespace=request.namespace,
                benchmark_service_name=(
                    request.benchmark_service_name
                ),
            )

            if sample is not None:
                samples.append(sample)

            time.sleep(
                request.resource_sample_interval_seconds
            )

        return samples

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
