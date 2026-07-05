from pathlib import Path
import csv

from .vm import VM
from .models import (
    NodeMetrics,
    PodMetrics,
    VMConfig,
    VMMetrics,
)
def vm_metrics_to_row(metrics: VMMetrics) -> dict[str, object]:
    return {
        "metric_type": "vm",
        "timestamp": metrics.timestamp.isoformat(),
        "cluster_name": metrics.cluster_name,
        "host": metrics.host,
        "hostname": metrics.hostname,
        "node_name": "",
        "node_role": "",
        "prometheus_instance": "",
        "latency":metrics.ssh_latency_ms,
        "namespace": "",
        "pod_name": "",
        "cpu_core_count": metrics.cpu_core_count,
        "cpu_usage_percent": metrics.cpu_usage_percent,
        "cpu_usage_cores": "",
        "cpu_usage_millicores": "",
        "memory_total_bytes": metrics.memory_total_bytes,
        "memory_available_bytes": metrics.memory_available_bytes,
        "memory_used_bytes": metrics.memory_used_bytes,
        "memory_usage_percent": metrics.memory_usage_percent,
        "memory_rss_bytes": "",
        "load_average_1m": metrics.load_average_1m,
        "load_average_5m": metrics.load_average_5m,
        "load_average_15m": metrics.load_average_15m,
        "network_receive_bytes_per_second": "",
        "network_transmit_bytes_per_second": "",
        "container_count": "",
    }


def node_metrics_to_row(metrics: NodeMetrics) -> dict[str, object]:
    return {
        "metric_type": "node",
        "timestamp": metrics.timestamp.isoformat(),
        "cluster_name": metrics.cluster_name,
        "host": "",
        "hostname": "",
        "node_name": metrics.node_name,
        "node_role": metrics.node_role,
        "prometheus_instance": metrics.prometheus_instance,
        "latency": metrics.prometheus_query_latency_ms,
        "namespace": "",
        "pod_name": "",
        "cpu_core_count": metrics.cpu_core_count,
        "cpu_usage_percent": metrics.cpu_usage_percent,
        "cpu_usage_cores": "",
        "cpu_usage_millicores": "",
        "memory_total_bytes": metrics.memory_total_bytes,
        "memory_available_bytes": metrics.memory_available_bytes,
        "memory_used_bytes": metrics.memory_used_bytes,
        "memory_usage_percent": metrics.memory_usage_percent,
        "memory_rss_bytes": "",
        "load_average_1m": metrics.load_average_1m,
        "load_average_5m": metrics.load_average_5m,
        "load_average_15m": metrics.load_average_15m,
        "network_receive_bytes_per_second": "",
        "network_transmit_bytes_per_second": "",
        "container_count": "",
    }


def pod_metrics_to_row(metrics: PodMetrics) -> dict[str, object]:
    return {
        "metric_type": "pod",
        "timestamp": metrics.timestamp.isoformat(),
        "cluster_name": metrics.cluster_name,
        "host": "",
        "hostname": "",
        "node_name": metrics.node_name,
        "node_role": "",
        "prometheus_instance": "",
        "latency": metrics.prometheus_query_latency_ms,
        "namespace": metrics.namespace,
        "pod_name": metrics.pod_name,
        "cpu_core_count": "",
        "cpu_usage_percent": "",
        "cpu_usage_cores": metrics.cpu_usage_cores,
        "cpu_usage_millicores": metrics.cpu_usage_millicores,
        "memory_total_bytes": "",
        "memory_available_bytes": "",
        "memory_used_bytes": metrics.memory_usage_bytes,
        "memory_usage_percent": "",
        "memory_rss_bytes": metrics.memory_rss_bytes,
        "load_average_1m": "",
        "load_average_5m": "",
        "load_average_15m": "",
        "network_receive_bytes_per_second": (
            metrics.network_receive_bytes_per_second
        ),
        "network_transmit_bytes_per_second": (
            metrics.network_transmit_bytes_per_second
        ),
        "container_count": metrics.container_count,
    }


def write_metrics_csv(
    output_path: Path,
    vm_metrics: list[VMMetrics],
    node_metrics: list[NodeMetrics],
    pod_metrics: list[PodMetrics],
) -> None:
    rows: list[dict[str, object]] = []

    rows.extend(vm_metrics_to_row(metrics) for metrics in vm_metrics)
    rows.extend(node_metrics_to_row(metrics) for metrics in node_metrics)
    rows.extend(pod_metrics_to_row(metrics) for metrics in pod_metrics)

    fieldnames = [
        "metric_type",
        "timestamp",
        "cluster_name",
        "host",
        "hostname",
        "node_name",
        "node_role",
        "prometheus_instance",
        "latency",
        "namespace",
        "pod_name",
        "cpu_core_count",
        "cpu_usage_percent",
        "cpu_usage_cores",
        "cpu_usage_millicores",
        "memory_total_bytes",
        "memory_available_bytes",
        "memory_used_bytes",
        "memory_usage_percent",
        "memory_rss_bytes",
        "load_average_1m",
        "load_average_5m",
        "load_average_15m",
        "network_receive_bytes_per_second",
        "network_transmit_bytes_per_second",
        "container_count",
    ]

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)