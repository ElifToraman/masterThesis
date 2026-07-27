from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from controller.benchmarking.benchmark_repository import (
    BenchmarkRepository,
)
from controller.benchmarking.benchmark_resource_sampler import (
    BenchmarkResourceSampler,
)
from controller.benchmarking.benchmark_service import (
    BenchmarkService,
)
from controller.benchmarking.knative_platform import (
    KnativePlatform,
)
from controller.benchmarking.models import (
    BenchmarkRequest,
)
from controller.image_resolver import (
    resolve_image_for_registry,
)
from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    DEFAULT_RUNTIME_CONFIG_FILE,
    DEFAULT_SUBMISSION_FILE,
    load_cluster_configs,
    load_runtime_config,
    load_submission,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--submission",
        type=Path,
        default=DEFAULT_SUBMISSION_FILE,
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=DEFAULT_RUNTIME_CONFIG_FILE,
    )
    parser.add_argument(
        "--run-id",
        default=None,
    )
    args = parser.parse_args(argv)

    controller_directory = (
        Path(__file__).resolve().parents[1]
    )

    submission = load_submission(args.submission)
    clusters = load_cluster_configs(args.cluster_config)
    runtime_config = load_runtime_config(args.runtime_config)
    vms_by_cluster = {
        name: cluster.create_vm()
        for name, cluster in clusters.items()
    }

    service = BenchmarkService(
        platform=KnativePlatform(),
        resource_sampler=BenchmarkResourceSampler(vms_by_cluster),
    )

    repository = BenchmarkRepository(
        output_file=(
            controller_directory
            / "results"
            / "benchmarks.jsonl"
        ),
    )

    function = submission.function
    benchmark_properties = runtime_config["benchmark"]

    run_id = args.run_id or uuid.uuid4().hex

    for cluster_name, cluster in clusters.items():
        image_reference = resolve_image_for_registry(
            image=function.image,
            registry=cluster.image_registry,
        )

        print(
            f"Benchmarking {cluster_name} "
            f"with image {image_reference}..."
        )

        request = BenchmarkRequest(
            run_id=run_id,
            function_name=function.name,
            function_version=function.version,
            benchmark_service_name=(
                f"{function.service_name}-benchmark"
            ),
            namespace=function.namespace,
            image_reference=image_reference,
            http_method="GET",
            warmup_requests=int(
                benchmark_properties.get(
                    "warmupRequests",
                    5,
                )
            ),
            measured_requests=int(
                benchmark_properties.get(
                    "measuredRequests",
                    50,
                )
            ),
            concurrency=int(
                benchmark_properties.get(
                    "concurrency",
                    5,
                )
            ),
            measurement_duration_seconds=float(
                benchmark_properties.get(
                    "durationSeconds",
                    15,
                )
            ),
            resource_sample_interval_seconds=float(
                benchmark_properties.get(
                    "resourceSampleIntervalSeconds",
                    1,
                )
            ),
            request_timeout_seconds=float(
                benchmark_properties["requestTimeoutSeconds"]
            ),
            deployment_timeout_seconds=float(
                benchmark_properties[
                    "deploymentTimeoutSeconds"
                ]
            ),
        )

        try:
            result = service.benchmark_cluster(
                cluster_name=cluster_name,
                kubernetes_context=cluster.kubernetes_context,
                request=request,
            )
        except Exception as error:
            repository.save_failure(
                run_id=run_id,
                cluster_name=cluster_name,
                kubernetes_context=cluster.kubernetes_context,
                function_name=function.name,
                function_version=function.version,
                image_reference=image_reference,
                error=error,
            )
            print(
                f"{cluster_name}: benchmark failed: "
                f"{type(error).__name__}: {error}"
            )
            continue

        repository.save(result)

        print(
            cluster_name,
            f"deploy={result.deployment_duration_ms:.2f} ms",
            f"first={result.first_invocation_latency_ms:.2f} ms",
            f"warm_avg={result.average_warm_latency_ms:.2f} ms",
            f"warm_p95={result.p95_warm_latency_ms:.2f} ms",
            f"success={result.success_rate:.2%}",
            f"throughput="
            f"{result.throughput_requests_per_second:.2f} req/s",
            f"concurrency={result.benchmark_concurrency}",
            f"cpu_avg={result.average_cpu_usage_cores}",
            f"cpu_peak={result.peak_cpu_usage_cores}",
            f"mem_avg={result.average_memory_usage_bytes}",
            f"mem_peak={result.peak_memory_usage_bytes}",
        )


if __name__ == "__main__":
    main()
