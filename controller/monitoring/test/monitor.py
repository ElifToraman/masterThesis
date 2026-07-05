from pathlib import Path

from controller.monitoring.models import (
    NodeMetrics,
    PodMetrics,
    VMConfig,
    VMMetrics,
)
from controller.monitoring.vm import VM

#python3 -m controller.monitoring.test.monitor

vm1 = VM(
    VMConfig(
        name="vm1-cluster",
        host="129.114.25.182",
        ssh_user="cc",
        ssh_key=Path.home() / ".ssh" / "chameleon_new",
        prometheus_url="http://127.0.0.1:19091"
    )
)

vm2 = VM(
    VMConfig(
        name="vm2-cluster",
        host="129.114.25.80",
        ssh_user="cc",
        ssh_key=Path.home() / ".ssh" / "chameleon_new",
        prometheus_url="http://127.0.0.1:19092"
    )
)

def bytes_to_gib(value: int) -> float:
    return value / (1024 ** 3)


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

def bytes_to_mib(value: int | float) -> float:
    return value / (1024**2)


def bytes_to_kib(value: int | float) -> float:
    return value / 1024

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
        f"Memory working set:"
        f" {bytes_to_mib(metrics.memory_usage_bytes):.2f} MiB"
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

for vm in (vm1, vm2):
    vm_metrics = vm.gather_metrics()
    node_metrics_by_name = vm.gather_all_node_metrics()
    pod_metrics_by_key = vm.gather_all_pod_metrics()

    for node_name, node_metrics in node_metrics_by_name.items():
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
            print_pod_metrics(pod_metrics)
