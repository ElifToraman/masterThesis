from __future__ import annotations

from collections import defaultdict
from typing import Any

from controller.models import ClusterCandidate
from controller.monitoring.models import MetricsSnapshot


def build_cluster_candidates(
    snapshot: MetricsSnapshot,
    benchmark_results: dict[str, dict[str, Any]],
    function_name: str,
) -> list[ClusterCandidate]:
    nodes_by_cluster = defaultdict(list)
    pods_by_cluster = defaultdict(list)

    for node_metrics in snapshot.node_metrics.values():
        nodes_by_cluster[
            node_metrics.cluster_name
        ].append(node_metrics)

    for pod_metrics in snapshot.pod_metrics.values():
        pods_by_cluster[
            pod_metrics.cluster_name
        ].append(pod_metrics)

    candidates: list[ClusterCandidate] = []

    all_cluster_names = set(snapshot.vm_metrics)
    all_cluster_names.update(nodes_by_cluster)

    for cluster_name in sorted(all_cluster_names):
        nodes = nodes_by_cluster.get(
            cluster_name,
            [],
        )

        pods = pods_by_cluster.get(
            cluster_name,
            [],
        )

        if not nodes:
            continue

        workers = [
            node
            for node in nodes
            if node.node_role == "worker"
        ]

        total_cpu_cores = sum(
            node.cpu_core_count
            for node in nodes
        )

        available_cpu_cores = sum(
            node.cpu_core_count
            * (
                1.0
                - node.cpu_usage_percent / 100.0
            )
            for node in nodes
        )

        total_memory_bytes = sum(
            node.memory_total_bytes
            for node in nodes
        )

        available_memory_bytes = sum(
            node.memory_available_bytes
            for node in nodes
        )

        used_memory_bytes = (
            total_memory_bytes
            - available_memory_bytes
        )

        memory_usage_percent = (
            100.0
            * used_memory_bytes
            / total_memory_bytes
            if total_memory_bytes > 0
            else 0.0
        )

        average_cpu_usage_percent = (
            sum(
                node.cpu_usage_percent
                for node in nodes
            )
            / len(nodes)
        )

        function_deployed = any(
            pod.namespace == "default"
            and (
                pod.pod_name == function_name
                or pod.pod_name.startswith(
                    f"{function_name}-"
                )
            )
            for pod in pods
        )

        benchmark = benchmark_results.get(
            cluster_name
        )

        candidates.append(
            ClusterCandidate(
                cluster_name=cluster_name,
                node_count=len(nodes),
                worker_count=len(workers),
                total_cpu_cores=total_cpu_cores,
                available_cpu_cores=round(
                    available_cpu_cores,
                    3,
                ),
                average_cpu_usage_percent=round(
                    average_cpu_usage_percent,
                    3,
                ),
                total_memory_bytes=(
                    total_memory_bytes
                ),
                available_memory_bytes=(
                    available_memory_bytes
                ),
                memory_usage_percent=round(
                    memory_usage_percent,
                    3,
                ),
                function_deployed=(
                    function_deployed
                ),
                benchmark_success_rate=(
                    float(
                        benchmark["success_rate"]
                    )
                    if benchmark is not None
                    else None
                ),
                benchmark_average_latency_ms=(
                    float(
                        benchmark[
                            "average_warm_latency_ms"
                        ]
                    )
                    if benchmark is not None
                    else None
                ),
                benchmark_p95_latency_ms=(
                    float(
                        benchmark[
                            "p95_warm_latency_ms"
                        ]
                    )
                    if benchmark is not None
                    else None
                ),
                benchmark_first_invocation_latency_ms=(
                    float(
                        benchmark[
                            "first_invocation_latency_ms"
                        ]
                    )
                    if benchmark is not None
                    else None
                ),
                benchmark_deployment_duration_ms=(
                    float(
                        benchmark[
                            "deployment_duration_ms"
                        ]
                    )
                    if benchmark is not None
                    else None
                ),
            )
        )

    return candidates
