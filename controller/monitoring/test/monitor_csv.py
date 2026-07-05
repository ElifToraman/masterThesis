from pathlib import Path
import csv

from vm import VM
from models import (
    NodeMetrics,
    PodMetrics,
    VMConfig,
    VMMetrics,
)


vm1 = VM(
    VMConfig(
        name="vm1-cluster",
        host="129.114.25.182",
        ssh_user="cc",
        ssh_key=Path.home() / ".ssh" / "chameleon_new",
        prometheus_url="http://127.0.0.1:19091",
    )
)

vm2 = VM(
    VMConfig(
        name="vm2-cluster",
        host="129.114.25.80",
        ssh_user="cc",
        ssh_key=Path.home() / ".ssh" / "chameleon_new",
        prometheus_url="http://127.0.0.1:19092",
    )
)


def bytes_to_gib(value: int) -> float:
    return value / (1024**3)


def bytes_to_mib(value: int | float) -> float:
    return value / (1024**2)


def bytes_to_kib(value: int | float) -> float:
    return value / 1024


def print_vm_metrics(metrics: VMMetrics) -> None:
    print(f"\nVM metrics for {metrics.cluster_name}")
    print("-" * 60)
    print(f"Host:              {metrics.host}")
    print(f"Hostname:          {metrics.hostname}")
    print(f"CPU cores:         {metrics.cpu_core_count}")
    print(f"CPU usage:         {metrics.cpu_usage_percent:.2f}%")
    print(f"Memory usage:      {metrics.memory_usage_percent:.2f}%")
    print(
        f"Memory used:       "
        f"{bytes_to_gib(metrics.memory_used_bytes):.2f} GiB"
    )
    print(
        f"Memory available:  "
        f"{bytes_to_gib(metrics.memory_available_bytes):.2f} GiB"
    )
    print(
        f"Memory total:      "
        f"{bytes_to_gib(metrics.memory_total_bytes):.2f} GiB"
    )
    print(
        f"Load average:      "
        f"{metrics.load_average_1m:.2f}, "
        f"{metrics.load_average_5m:.2f}, "
        f"{metrics.load_average_15m:.2f}"
    )
    print(f"Collected at:      {metrics.timestamp.isoformat()}")


def print_node_metrics(metrics: NodeMetrics) -> None:
    print(
        f"\nNode metrics for "
        f"{metrics.cluster_name}/{metrics.node_name}"
    )
    print("-" * 60)
    print(f"Role:              {metrics.node_role}")
    print(f"Prometheus target: {metrics.prometheus_instance}")
    print(f"CPU cores:         {metrics.cpu_core_count}")
    print(f"CPU usage:         {metrics.cpu_usage_percent:.2f}%")
    print(f"Memory usage:      {metrics.memory_usage_percent:.2f}%")
    print(
        f"Memory used:       "
        f"{bytes_to_gib(metrics.memory_used_bytes):.2f} GiB"
    )
    print(
        f"Memory available:  "
        f"{bytes_to_gib(metrics.memory_available_bytes):.2f} GiB"
    )
    print(
        f"Memory total:      "
        f"{bytes_to_gib(metrics.memory_total_bytes):.2f} GiB"
    )
    print(
        f"Load average:      "
        f"{metrics.load_average_1m:.2f}, "
        f"{metrics.load_average_5m:.2f}, "
        f"{metrics.load_average_15m:.2f}"
    )
    print(f"Collected at:      {metrics.timestamp.isoformat()}")


def print_pod_metrics(metrics: PodMetrics) -> None:
    print(
        f"\nPod metrics for "
        f"{metrics.namespace}/{metrics.pod_name}"
    )
    print("-" * 60)
    print(f"Cluster:           {metrics.cluster_name}")
    print(f"Node:              {metrics.node_name}")
    print(f"Containers:        {metrics.container_count}")
    print(
        f"CPU usage:         "
        f"{metrics.cpu_usage_millicores:.2f} millicores"
    )
    print(
        f"Memory working set: "
        f"{bytes_to_mib(metrics.memory_usage_bytes):.2f} MiB"
    )
    print(
        f"Memory RSS:        "
        f"{bytes_to_mib(metrics.memory_rss_bytes):.2f} MiB"
    )
    print(
        f"Network receive:   "
        f"{bytes_to_kib(metrics.network_receive_bytes_per_second):.2f} "
        f"KiB/s"
    )
    print(
        f"Network transmit:  "
        f"{bytes_to_kib(metrics.network_transmit_bytes_per_second):.2f} "
        f"KiB/s"
    )
    print(f"Collected at:      {metrics.timestamp.isoformat()}")


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


all_vm_metrics: list[VMMetrics] = []
all_node_metrics: list[NodeMetrics] = []
all_pod_metrics: list[PodMetrics] = []


for vm in (vm1, vm2):
    try:
        vm_metrics = vm.gather_metrics()
        all_vm_metrics.append(vm_metrics)
        print_vm_metrics(vm_metrics)
    except RuntimeError as exc:
        print(
            f"Could not collect VM metrics from "
            f"{vm.config.name}: {exc}"
        )

    try:
        node_metrics_by_name = vm.gather_all_node_metrics()
        pod_metrics_by_key = vm.gather_all_pod_metrics()
    except RuntimeError as exc:
        print(
            f"Could not collect cluster metrics from "
            f"{vm.config.name}: {exc}"
        )
        continue

    for node_name, node_metrics in sorted(
        node_metrics_by_name.items(),
    ):
        all_node_metrics.append(node_metrics)
        print_node_metrics(node_metrics)

        node_pods = [
            metrics
            for metrics in pod_metrics_by_key.values()
            if metrics.node_name == node_name
        ]

        for pod_metrics in sorted(
            node_pods,
            key=lambda metrics: (
                metrics.namespace,
                metrics.pod_name,
            ),
        ):
            all_pod_metrics.append(pod_metrics)
            print_pod_metrics(pod_metrics)


output_path = (
    Path(__file__).resolve().parent
    / "output"
    / "metrics.csv"
)

write_metrics_csv(
    output_path=output_path,
    vm_metrics=all_vm_metrics,
    node_metrics=all_node_metrics,
    pod_metrics=all_pod_metrics,
)

print(f"\nMetrics written to: {output_path}")