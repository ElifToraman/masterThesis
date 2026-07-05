# controller/scripts/run_benchmark.py

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
from controller.benchmarking.models import BenchmarkRequest


clusters = {
    "vm1-cluster": "vm1-cluster",
    "vm2-cluster": "vm2-cluster",
}

request = BenchmarkRequest(
    function_name="hello",
    benchmark_service_name="hello-benchmark",
    namespace="default",
    image_reference=(
        "host.docker.internal:5000/elif/hello:latest"
    ),
    http_method="GET",
    warmup_requests=3,
    measured_requests=20,
    request_timeout_seconds=10,
    deployment_timeout_seconds=180,
)

service = BenchmarkService(
    platform=KnativePlatform(),
)

repository = BenchmarkRepository(
    output_file=(
        Path(__file__).resolve().parents[1]
        / "results"
        / "benchmarks.jsonl"
    ),
)

for cluster_name, context in clusters.items():
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