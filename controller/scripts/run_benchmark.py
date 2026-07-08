from __future__ import annotations

from pathlib import Path

from controller.benchmarking.benchmark_repository import (
    BenchmarkRepository,
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
    resolve_cluster_image,
)
from controller.intent_function_parser import (
    parse_intent_function_payload,
)


CLUSTERS = {
    "vm1-cluster": "vm1-cluster",
    "vm2-cluster": "vm2-cluster",
}


def main() -> None:
    controller_directory = (
        Path(__file__).resolve().parents[1]
    )

    intent_function_path = (
        controller_directory
        / "examples"
        / "hello-intent-function.yaml"
    )

    submission = parse_intent_function_payload(
        intent_function_path.read_text(
            encoding="utf-8",
        )
    )

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

    for cluster_name, context in CLUSTERS.items():
        image_reference = resolve_cluster_image(
            cluster_name=cluster_name,
            image=function.image,
        )

        print(
            f"Benchmarking {cluster_name} "
            f"with image {image_reference}..."
        )

        request = BenchmarkRequest(
            function_name=function.name,
            function_version=function.version,
            benchmark_service_name=(
                f"{function.service_name}-benchmark"
            ),
            namespace=function.namespace,
            image_reference=image_reference,
            http_method="GET",
            warmup_requests=3,
            measured_requests=20,
            request_timeout_seconds=10,
            deployment_timeout_seconds=180,
        )

        result = service.benchmark_cluster(
            cluster_name=cluster_name,
            kubernetes_context=context,
            request=request,
        )

        repository.save(result)

        print(
            cluster_name,
            f"deploy={result.deployment_duration_ms:.2f} ms",
            f"first={result.first_invocation_latency_ms:.2f} ms",
            f"warm_avg={result.average_warm_latency_ms:.2f} ms",
            f"warm_p95={result.p95_warm_latency_ms:.2f} ms",
            f"success={result.success_rate:.2%}",
        )


if __name__ == "__main__":
    main()