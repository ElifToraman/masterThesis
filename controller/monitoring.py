#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIRECTORY = CONTROLLER_DIRECTORY / "config"
DEFAULT_RESULTS_DIRECTORY = (
    CONTROLLER_DIRECTORY / "results" / "monitoring"
)


class MonitoringError(RuntimeError):
    """Raised when monitoring information cannot be collected."""


REMOTE_VM_METRICS_SCRIPT = r"""
import json
import os
import shutil
import time


def read_cpu():
    with open("/proc/stat", "r", encoding="utf-8") as file:
        values = file.readline().split()[1:]

    numbers = [int(value) for value in values]
    total = sum(numbers)

    idle = numbers[3]
    if len(numbers) > 4:
        idle += numbers[4]

    return total, idle


def read_network():
    received = 0
    transmitted = 0

    with open("/proc/net/dev", "r", encoding="utf-8") as file:
        lines = file.readlines()[2:]

    for line in lines:
        interface, values = line.split(":", 1)
        interface = interface.strip()

        if interface == "lo":
            continue

        fields = values.split()
        received += int(fields[0])
        transmitted += int(fields[8])

    return received, transmitted


def read_memory():
    values = {}

    with open("/proc/meminfo", "r", encoding="utf-8") as file:
        for line in file:
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024

    total = values["MemTotal"]
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = total - available

    return total, available, used


cpu_total_1, cpu_idle_1 = read_cpu()
network_rx_1, network_tx_1 = read_network()

sample_seconds = 1.0
time.sleep(sample_seconds)

cpu_total_2, cpu_idle_2 = read_cpu()
network_rx_2, network_tx_2 = read_network()

total_delta = cpu_total_2 - cpu_total_1
idle_delta = cpu_idle_2 - cpu_idle_1

if total_delta > 0:
    cpu_usage_percent = 100.0 * (
        1.0 - idle_delta / total_delta
    )
else:
    cpu_usage_percent = 0.0

cpu_usage_percent = max(0.0, min(100.0, cpu_usage_percent))

memory_total, memory_available, memory_used = read_memory()
memory_usage_percent = (
    100.0 * memory_used / memory_total
    if memory_total > 0
    else 0.0
)

disk = shutil.disk_usage("/")
load_1m, load_5m, load_15m = os.getloadavg()
cpu_count = os.cpu_count() or 1

available_cpu_millicores = (
    cpu_count * 1000.0 * (1.0 - cpu_usage_percent / 100.0)
)

result = {
    "cpu_count": cpu_count,
    "cpu_usage_percent": round(cpu_usage_percent, 3),
    "available_cpu_millicores": round(
        available_cpu_millicores,
        3,
    ),
    "memory_total_bytes": memory_total,
    "memory_available_bytes": memory_available,
    "memory_used_bytes": memory_used,
    "memory_usage_percent": round(memory_usage_percent, 3),
    "load_1m": round(load_1m, 3),
    "load_5m": round(load_5m, 3),
    "load_15m": round(load_15m, 3),
    "disk_total_bytes": disk.total,
    "disk_used_bytes": disk.used,
    "disk_free_bytes": disk.free,
    "disk_usage_percent": round(
        100.0 * disk.used / disk.total,
        3,
    ),
    "network_received_bytes": network_rx_2,
    "network_transmitted_bytes": network_tx_2,
    "network_receive_bytes_per_second": round(
        (network_rx_2 - network_rx_1) / sample_seconds,
        3,
    ),
    "network_transmit_bytes_per_second": round(
        (network_tx_2 - network_tx_1) / sample_seconds,
        3,
    ),
}

print(json.dumps(result))
"""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def bytes_to_mb(value: int | float | None) -> float | None:
    if value is None:
        return None

    return round(float(value) / (1024 * 1024), 3)


def bytes_to_gb(value: int | float | None) -> float | None:
    if value is None:
        return None

    return round(float(value) / (1024 * 1024 * 1024), 3)


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result) or math.isinf(result):
        return None

    return result


def run_command(
    command: list[str],
    timeout_seconds: int = 30,
    input_text: str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise MonitoringError(
            f"Command timed out: {' '.join(command)}"
        ) from exc

    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "unknown command failure"
        )

        raise MonitoringError(
            f"Command failed: {' '.join(command)}: {message}"
        )

    return completed.stdout


def kubectl_json(
    context: str,
    arguments: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    output = run_command(
        [
            "kubectl",
            "--context",
            context,
            *arguments,
            "-o",
            "json",
        ],
        timeout_seconds=timeout_seconds,
    )

    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise MonitoringError(
            f"kubectl returned invalid JSON for {context}"
        ) from exc

    if not isinstance(value, dict):
        raise MonitoringError(
            f"Unexpected kubectl response for {context}"
        )

    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringError(f"Cannot load {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise MonitoringError(f"{path} must contain an object")

    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise MonitoringError(f"Cannot load {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise MonitoringError(f"{path} must contain a mapping")

    return value


def parse_cpu_millicores(quantity: str | None) -> float | None:
    if not quantity:
        return None

    quantity = str(quantity).strip()

    try:
        if quantity.endswith("n"):
            return float(quantity[:-1]) / 1_000_000

        if quantity.endswith("u"):
            return float(quantity[:-1]) / 1_000

        if quantity.endswith("m"):
            return float(quantity[:-1])

        return float(quantity) * 1000
    except ValueError:
        return None


def parse_memory_bytes(quantity: str | None) -> int | None:
    if not quantity:
        return None

    quantity = str(quantity).strip()

    binary_suffixes = {
        "Ei": 1024**6,
        "Pi": 1024**5,
        "Ti": 1024**4,
        "Gi": 1024**3,
        "Mi": 1024**2,
        "Ki": 1024,
    }

    decimal_suffixes = {
        "E": 1000**6,
        "P": 1000**5,
        "T": 1000**4,
        "G": 1000**3,
        "M": 1000**2,
        "K": 1000,
    }

    try:
        for suffix, multiplier in binary_suffixes.items():
            if quantity.endswith(suffix):
                return int(
                    float(quantity[: -len(suffix)]) * multiplier
                )

        for suffix, multiplier in decimal_suffixes.items():
            if quantity.endswith(suffix):
                return int(
                    float(quantity[: -len(suffix)]) * multiplier
                )

        return int(float(quantity))
    except ValueError:
        return None


def node_is_ready(node: dict[str, Any]) -> bool:
    conditions = node.get("status", {}).get("conditions", [])

    return any(
        condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )


def node_role(labels: dict[str, str]) -> str:
    if (
        "node-role.kubernetes.io/control-plane" in labels
        or "node-role.kubernetes.io/master" in labels
    ):
        return "control-plane"

    if "node-role.kubernetes.io/worker" in labels:
        return "worker"

    return "unknown"


def collect_vm_metrics(
    ssh_alias: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    output = run_command(
        ["ssh", ssh_alias, "python3", "-"],
        timeout_seconds=max(timeout_seconds, 15),
        input_text=REMOTE_VM_METRICS_SCRIPT,
    )

    try:
        metrics = json.loads(output)
    except json.JSONDecodeError as exc:
        raise MonitoringError(
            f"Invalid VM metrics returned by {ssh_alias}"
        ) from exc

    metrics["memory_total_mb"] = bytes_to_mb(
        metrics.get("memory_total_bytes")
    )
    metrics["memory_available_mb"] = bytes_to_mb(
        metrics.get("memory_available_bytes")
    )
    metrics["memory_used_mb"] = bytes_to_mb(
        metrics.get("memory_used_bytes")
    )

    metrics["disk_total_gb"] = bytes_to_gb(
        metrics.get("disk_total_bytes")
    )
    metrics["disk_used_gb"] = bytes_to_gb(
        metrics.get("disk_used_bytes")
    )
    metrics["disk_free_gb"] = bytes_to_gb(
        metrics.get("disk_free_bytes")
    )

    return metrics


class PrometheusClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def ready(self) -> bool:
        try:
            response = self.session.get(
                f"{self.base_url}/-/ready",
                timeout=self.timeout_seconds,
            )
        except requests.RequestException:
            return False

        return response.status_code == 200

    def query(self, expression: str) -> list[dict[str, Any]]:
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/query",
                params={"query": expression},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MonitoringError(
                f"Prometheus query failed: {expression}: {exc}"
            ) from exc

        if payload.get("status") != "success":
            raise MonitoringError(
                f"Prometheus returned failure for: {expression}"
            )

        result = payload.get("data", {}).get("result", [])

        return result if isinstance(result, list) else []


def prometheus_value(row: dict[str, Any]) -> float | None:
    value = row.get("value")

    if not isinstance(value, list) or len(value) < 2:
        return None

    return finite_float(value[1])


def prometheus_scalar(
    client: PrometheusClient,
    expression: str,
) -> float | None:
    rows = client.query(expression)

    if not rows:
        return None

    return prometheus_value(rows[0])


def prometheus_vector_by_label(
    client: PrometheusClient,
    expression: str,
    label: str,
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}

    for row in client.query(expression):
        metric = row.get("metric", {})
        key = metric.get(label)

        if key:
            values[str(key)] = prometheus_value(row)

    return values


def prometheus_function_vector(
    client: PrometheusClient,
    expression: str,
) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}

    for row in client.query(expression):
        metric = row.get("metric", {})
        pod = metric.get("pod")

        if not pod:
            continue

        values[str(pod)] = {
            "node": metric.get("node"),
            "value": prometheus_value(row),
        }

    return values


def collect_prometheus_metrics(
    client: PrometheusClient,
    namespace: str,
    service_name: str,
) -> dict[str, Any]:
    if not client.ready():
        raise MonitoringError(
            f"Prometheus is not ready: {client.base_url}"
        )

    cluster_cpu_query = (
        'sum(rate(container_cpu_usage_seconds_total{'
        'container!="",image!=""}[2m])) * 1000'
    )

    cluster_memory_query = (
        'sum(container_memory_working_set_bytes{'
        'container!="",image!=""})'
    )

    node_cpu_query = (
        'sum by(node) ('
        'rate(container_cpu_usage_seconds_total{'
        'container!="",image!=""}[2m])'
        ') * 1000'
    )

    node_memory_query = (
        'sum by(node) ('
        'container_memory_working_set_bytes{'
        'container!="",image!=""}'
        ')'
    )

    function_cpu_query = (
        'sum by(node,pod) ('
        'rate(container_cpu_usage_seconds_total{'
        f'namespace="{namespace}",'
        f'pod=~"{service_name}-.*",'
        'container="user-container"'
        '}[2m])'
        ') * 1000'
    )

    function_memory_query = (
        'sum by(node,pod) ('
        'container_memory_working_set_bytes{'
        f'namespace="{namespace}",'
        f'pod=~"{service_name}-.*",'
        'container="user-container"'
        '}'
        ')'
    )

    return {
        "ready": True,
        "cluster_container_cpu_millicores": prometheus_scalar(
            client,
            cluster_cpu_query,
        ),
        "cluster_container_memory_bytes": prometheus_scalar(
            client,
            cluster_memory_query,
        ),
        "average_exporter_load_1m": prometheus_scalar(
            client,
            "avg(node_load1)",
        ),
        "node_container_cpu_millicores": (
            prometheus_vector_by_label(
                client,
                node_cpu_query,
                "node",
            )
        ),
        "node_container_memory_bytes": (
            prometheus_vector_by_label(
                client,
                node_memory_query,
                "node",
            )
        ),
        "function_cpu": prometheus_function_vector(
            client,
            function_cpu_query,
        ),
        "function_memory": prometheus_function_vector(
            client,
            function_memory_query,
        ),
    }


def collect_knative_state(
    context: str,
    namespace: str,
    service_name: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deployments = kubectl_json(
        context,
        ["get", "deployments", "-n", "knative-serving"],
        timeout_seconds,
    )

    deployment_states = []

    for deployment in deployments.get("items", []):
        metadata = deployment.get("metadata", {})
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})

        desired = int(spec.get("replicas", 1) or 0)
        available = int(status.get("availableReplicas", 0) or 0)

        deployment_states.append(
            {
                "name": metadata.get("name"),
                "desired_replicas": desired,
                "available_replicas": available,
                "ready": available >= desired,
            }
        )

    knative_ready = bool(deployment_states) and all(
        deployment["ready"]
        for deployment in deployment_states
    )

    service_ready = False
    service_url = None
    service_revision = None
    service_error = None

    try:
        service = kubectl_json(
            context,
            [
                "get",
                "kservice",
                service_name,
                "-n",
                namespace,
            ],
            timeout_seconds,
        )

        service_status = service.get("status", {})
        conditions = service_status.get("conditions", [])

        service_ready = any(
            condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )

        service_url = service_status.get("url")
        service_revision = service_status.get(
            "latestReadyRevisionName"
        )
    except MonitoringError as exc:
        service_error = str(exc)

    return {
        "ready": knative_ready,
        "deployments": deployment_states,
        "function_service": {
            "name": service_name,
            "namespace": namespace,
            "ready": service_ready,
            "url": service_url,
            "latest_ready_revision": service_revision,
            "error": service_error,
        },
    }


def collect_kubernetes_state(
    context: str,
    namespace: str,
    service_name: str,
    timeout_seconds: int,
    prometheus_metrics: dict[str, Any],
) -> dict[str, Any]:
    nodes_response = kubectl_json(
        context,
        ["get", "nodes"],
        timeout_seconds,
    )

    pods_response = kubectl_json(
        context,
        ["get", "pods", "--all-namespaces"],
        timeout_seconds,
    )

    running_pods_per_node: dict[str, int] = defaultdict(int)

    for pod in pods_response.get("items", []):
        spec = pod.get("spec", {})
        status = pod.get("status", {})

        node_name = spec.get("nodeName")
        phase = status.get("phase")

        if node_name and phase == "Running":
            running_pods_per_node[node_name] += 1

    node_cpu_usage = prometheus_metrics.get(
        "node_container_cpu_millicores",
        {},
    )

    node_memory_usage = prometheus_metrics.get(
        "node_container_memory_bytes",
        {},
    )

    nodes: list[dict[str, Any]] = []

    total_allocatable_cpu = 0.0
    total_allocatable_memory = 0
    ready_node_count = 0
    ready_worker_count = 0

    for node in nodes_response.get("items", []):
        metadata = node.get("metadata", {})
        spec = node.get("spec", {})
        status = node.get("status", {})

        name = metadata.get("name")
        labels = metadata.get("labels", {})
        role = node_role(labels)
        ready = node_is_ready(node)
        unschedulable = bool(spec.get("unschedulable", False))

        allocatable = status.get("allocatable", {})

        allocatable_cpu = parse_cpu_millicores(
            allocatable.get("cpu")
        )
        allocatable_memory = parse_memory_bytes(
            allocatable.get("memory")
        )

        if allocatable_cpu is not None:
            total_allocatable_cpu += allocatable_cpu

        if allocatable_memory is not None:
            total_allocatable_memory += allocatable_memory

        if ready:
            ready_node_count += 1

        eligible_worker = (
            role == "worker"
            and ready
            and not unschedulable
            and labels.get("workload") == "serverless"
        )

        if eligible_worker:
            ready_worker_count += 1

        taints = [
            {
                "key": taint.get("key"),
                "value": taint.get("value"),
                "effect": taint.get("effect"),
            }
            for taint in spec.get("taints", [])
        ]

        container_memory_bytes = node_memory_usage.get(name)

        nodes.append(
            {
                "name": name,
                "role": role,
                "ready": ready,
                "unschedulable": unschedulable,
                "eligible_for_serverless": eligible_worker,
                "labels": {
                    "workload": labels.get("workload"),
                    "worker_id": labels.get("worker-id"),
                    "cluster": labels.get("cluster"),
                },
                "taints": taints,
                "allocatable_cpu_millicores": allocatable_cpu,
                "allocatable_memory_bytes": allocatable_memory,
                "allocatable_memory_mb": bytes_to_mb(
                    allocatable_memory
                ),
                "running_pods": running_pods_per_node.get(
                    name,
                    0,
                ),
                "container_cpu_usage_millicores": (
                    node_cpu_usage.get(name)
                ),
                "container_memory_usage_bytes": (
                    container_memory_bytes
                ),
                "container_memory_usage_mb": bytes_to_mb(
                    container_memory_bytes
                ),
            }
        )

    function_pods_response = kubectl_json(
        context,
        [
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            f"serving.knative.dev/service={service_name}",
        ],
        timeout_seconds,
    )

    function_cpu = prometheus_metrics.get("function_cpu", {})
    function_memory = prometheus_metrics.get(
        "function_memory",
        {},
    )

    function_pods = []

    for pod in function_pods_response.get("items", []):
        metadata = pod.get("metadata", {})
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        labels = metadata.get("labels", {})

        pod_name = metadata.get("name")
        container_statuses = status.get(
            "containerStatuses",
            [],
        )

        ready = bool(container_statuses) and all(
            container.get("ready", False)
            for container in container_statuses
        )

        restart_count = sum(
            int(container.get("restartCount", 0))
            for container in container_statuses
        )

        cpu_entry = function_cpu.get(pod_name, {})
        memory_entry = function_memory.get(pod_name, {})
        memory_bytes = memory_entry.get("value")

        function_pods.append(
            {
                "name": pod_name,
                "namespace": metadata.get("namespace"),
                "node": spec.get("nodeName"),
                "phase": status.get("phase"),
                "ready": ready,
                "restart_count": restart_count,
                "pod_ip": status.get("podIP"),
                "host_ip": status.get("hostIP"),
                "revision": labels.get(
                    "serving.knative.dev/revision"
                ),
                "cpu_usage_millicores": cpu_entry.get(
                    "value"
                ),
                "memory_usage_bytes": memory_bytes,
                "memory_usage_mb": bytes_to_mb(memory_bytes),
            }
        )

    return {
        "node_count": len(nodes),
        "ready_node_count": ready_node_count,
        "ready_worker_count": ready_worker_count,
        "kubernetes_reported_allocatable_cpu_millicores_sum": (
            round(total_allocatable_cpu, 3)
        ),
        "kubernetes_reported_allocatable_memory_bytes_sum": (
            total_allocatable_memory
        ),
        "kubernetes_reported_allocatable_memory_mb_sum": (
            bytes_to_mb(total_allocatable_memory)
        ),
        "allocatable_sum_warning": (
            "Kind nodes share one physical VM. Kubernetes-reported "
            "allocatable capacity must not be treated as independent "
            "physical capacity or used directly for cluster comparison."
        ),
        "nodes": nodes,
        "function": {
            "service_name": service_name,
            "namespace": namespace,
            "replica_count": len(function_pods),
            "pods": function_pods,
        },
    }


def collect_cluster(
    cluster_name: str,
    infrastructure: dict[str, Any],
    controller_config: dict[str, Any],
    function_descriptor: dict[str, Any],
) -> dict[str, Any]:
    started_at = utc_now()
    errors: list[str] = []

    timeout_seconds = int(
        controller_config.get("monitoring", {}).get(
            "query_timeout_seconds",
            10,
        )
    )

    minimum_ready_workers = int(
        controller_config.get("feasibility", {}).get(
            "minimum_ready_workers",
            1,
        )
    )

    context = infrastructure["kubeconfig_context"]
    prometheus_url = infrastructure["prometheus"]["url"]
    ssh_alias = infrastructure["infrastructure"]["ssh_alias"]

    namespace = function_descriptor["namespace"]
    service_name = function_descriptor["service_name"]

    try:
        nodes_test = kubectl_json(
            context,
            ["get", "nodes"],
            timeout_seconds,
        )
        cluster_reachable = bool(nodes_test.get("items"))
    except MonitoringError as exc:
        return {
            "status": "unavailable",
            "reachable": False,
            "collected_at": started_at,
            "errors": [str(exc)],
        }

    try:
        vm_metrics = collect_vm_metrics(
            ssh_alias,
            timeout_seconds,
        )
    except MonitoringError as exc:
        errors.append(str(exc))
        vm_metrics = {}

    prometheus_client = PrometheusClient(
        prometheus_url,
        timeout_seconds,
    )

    try:
        prometheus_metrics = collect_prometheus_metrics(
            prometheus_client,
            namespace,
            service_name,
        )
    except MonitoringError as exc:
        errors.append(str(exc))
        prometheus_metrics = {
            "ready": False,
            "node_container_cpu_millicores": {},
            "node_container_memory_bytes": {},
            "function_cpu": {},
            "function_memory": {},
        }

    try:
        knative = collect_knative_state(
            context,
            namespace,
            service_name,
            timeout_seconds,
        )
    except MonitoringError as exc:
        errors.append(str(exc))
        knative = {
            "ready": False,
            "deployments": [],
            "function_service": {
                "ready": False,
            },
        }

    try:
        kubernetes = collect_kubernetes_state(
            context,
            namespace,
            service_name,
            timeout_seconds,
            prometheus_metrics,
        )
    except MonitoringError as exc:
        errors.append(str(exc))
        kubernetes = {
            "node_count": 0,
            "ready_node_count": 0,
            "ready_worker_count": 0,
            "nodes": [],
            "function": {
                "replica_count": 0,
                "pods": [],
            },
        }

    summary = {
        "reachable": cluster_reachable,
        "knative_ready": bool(knative.get("ready")),
        "prometheus_ready": bool(
            prometheus_metrics.get("ready")
        ),
        "ready_nodes": kubernetes.get(
            "ready_node_count",
            0,
        ),
        "ready_workers": kubernetes.get(
            "ready_worker_count",
            0,
        ),
        "available_cpu_millicores": vm_metrics.get(
            "available_cpu_millicores"
        ),
        "available_memory_mb": vm_metrics.get(
            "memory_available_mb"
        ),
        "cpu_load_percent": vm_metrics.get(
            "cpu_usage_percent"
        ),
        "memory_load_percent": vm_metrics.get(
            "memory_usage_percent"
        ),
        "load_1m": vm_metrics.get("load_1m"),
        "cluster_container_cpu_usage_millicores": (
            prometheus_metrics.get(
                "cluster_container_cpu_millicores"
            )
        ),
        "cluster_container_memory_usage_mb": bytes_to_mb(
            prometheus_metrics.get(
                "cluster_container_memory_bytes"
            )
        ),
        "function_ready": bool(
            knative.get(
                "function_service",
                {},
            ).get("ready")
        ),
        "function_replicas": kubernetes.get(
            "function",
            {},
        ).get("replica_count", 0),
    }

    healthy = (
        summary["reachable"]
        and summary["knative_ready"]
        and summary["prometheus_ready"]
        and summary["ready_nodes"] == 3
        and summary["ready_workers"] >= minimum_ready_workers
    )

    return {
        "display_name": infrastructure.get(
            "display_name",
            cluster_name,
        ),
        "status": "healthy" if healthy else "degraded",
        "reachable": cluster_reachable,
        "collected_at": started_at,
        "errors": errors,
        "infrastructure": {
            "kubeconfig_context": context,
            "ssh_alias": ssh_alias,
            "private_ip": infrastructure[
                "infrastructure"
            ].get("private_ip"),
            "public_ip": infrastructure[
                "infrastructure"
            ].get("public_ip"),
            "prometheus_url": prometheus_url,
        },
        "summary": summary,
        "vm": vm_metrics,
        "knative": knative,
        "prometheus": {
            "ready": prometheus_metrics.get("ready", False),
            "url": prometheus_url,
            "average_exporter_load_1m": (
                prometheus_metrics.get(
                    "average_exporter_load_1m"
                )
            ),
        },
        "kubernetes": kubernetes,
    }


def default_output_path() -> Path:
    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    return (
        DEFAULT_RESULTS_DIRECTORY
        / f"monitoring-snapshot-{timestamp}.json"
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect physical VM, Kubernetes, Knative and "
            "Prometheus monitoring information."
        )
    )

    parser.add_argument(
        "--config-directory",
        type=Path,
        default=DEFAULT_CONFIG_DIRECTORY,
    )

    parser.add_argument(
        "--cluster",
        action="append",
        dest="clusters",
        help="Collect one cluster. May be repeated.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON file.",
    )

    parser.add_argument(
        "--print-json",
        action="store_true",
    )

    parser.add_argument(
        "--no-save",
        action="store_true",
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    config_directory = arguments.config_directory

    try:
        infrastructure_config = load_yaml(
            config_directory / "clusters.yaml"
        )
        controller_config = load_json(
            config_directory / "controller_config.json"
        )
        function_descriptor = load_json(
            config_directory / "function_descriptor.json"
        )
    except MonitoringError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    infrastructure_clusters = infrastructure_config.get(
        "clusters",
        {},
    )

    selected_clusters = (
        arguments.clusters
        if arguments.clusters
        else controller_config["candidate_clusters"]
    )

    unknown_clusters = [
        cluster
        for cluster in selected_clusters
        if cluster not in infrastructure_clusters
    ]

    if unknown_clusters:
        print(
            "ERROR: unknown clusters: "
            + ", ".join(unknown_clusters),
            file=sys.stderr,
        )
        return 2

    snapshot = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "capacity_model": {
            "placement_capacity_source": (
                "physical_chameleon_vm_metrics"
            ),
            "kubernetes_usage_source": (
                "prometheus_kubelet_cadvisor"
            ),
            "reason": (
                "All Kind nodes in a candidate cluster share one "
                "physical Chameleon VM."
            ),
        },
        "clusters": {},
    }

    for cluster_name in selected_clusters:
        print(
            f"Collecting monitoring data for {cluster_name}...",
            file=sys.stderr,
        )

        snapshot["clusters"][cluster_name] = collect_cluster(
            cluster_name,
            infrastructure_clusters[cluster_name],
            controller_config,
            function_descriptor,
        )

    output_path = arguments.output or default_output_path()

    if not arguments.no_save:
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with output_path.open("w", encoding="utf-8") as file:
            json.dump(snapshot, file, indent=2)
            file.write("\n")

        print(f"Snapshot written to: {output_path}")

    for cluster_name, cluster in snapshot["clusters"].items():
        summary = cluster.get("summary", {})

        print(
            f"{cluster_name}: "
            f"status={cluster.get('status')}, "
            f"ready_workers={summary.get('ready_workers')}, "
            f"cpu={summary.get('cpu_load_percent')}%, "
            f"memory={summary.get('memory_load_percent')}%, "
            f"available_cpu="
            f"{summary.get('available_cpu_millicores')}m, "
            f"available_memory="
            f"{summary.get('available_memory_mb')}MB"
        )

    if arguments.print_json:
        print(json.dumps(snapshot, indent=2))

    statuses = [
        cluster.get("status")
        for cluster in snapshot["clusters"].values()
    ]

    return 0 if all(
        status == "healthy"
        for status in statuses
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
