#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIRECTORY = CONTROLLER_DIRECTORY / "config"
DEFAULT_RESULTS_DIRECTORY = (
    CONTROLLER_DIRECTORY / "results" / "benchmarks"
)


REQUEST_FIELDS = [
    "run_id",
    "cluster",
    "request_type",
    "request_number",
    "request_id",
    "started_at",
    "finished_at",
    "http_status",
    "success",
    "response_time_ms",
    "function_duration_ms",
    "cluster_returned",
    "pod",
    "node",
    "revision",
    "error",
]


RESOURCE_FIELDS = [
    "run_id",
    "cluster",
    "sample_timestamp",
    "sample_epoch",
    "pod",
    "node",
    "cpu_millicores",
    "memory_mb",
]


class BenchmarkError(RuntimeError):
    """Raised when a cluster benchmark cannot be completed."""


def utc_now(
    milliseconds: bool = False,
) -> str:
    timespec = "milliseconds" if milliseconds else "seconds"

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
    )


def epoch_to_utc(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(
            timestamp,
            timezone.utc,
        )
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result) or math.isinf(result):
        return None

    return result


def rounded(
    value: float | None,
    digits: int = 3,
) -> float | None:
    if value is None:
        return None

    return round(value, digits)


def percentile(
    values: list[float],
    fraction: float,
) -> float | None:
    if not values:
        return None

    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return ordered[lower_index]

    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    weight = position - lower_index

    return lower_value + (
        upper_value - lower_value
    ) * weight


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(
            f"Cannot load JSON file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise BenchmarkError(
            f"{path} must contain a JSON object"
        )

    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkError(
            f"Cannot load YAML file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise BenchmarkError(
            f"{path} must contain a YAML mapping"
        )

    return value


def run_command(
    command: list[str],
    timeout_seconds: int = 30,
    input_text: str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError(
            f"Command timed out: {' '.join(command)}"
        ) from exc

    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "unknown command error"
        )

        raise BenchmarkError(
            f"Command failed: {' '.join(command)}: "
            f"{message}"
        )

    return completed.stdout


def kubectl_json(
    context: str,
    arguments: list[str],
    timeout_seconds: int = 30,
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
        raise BenchmarkError(
            f"kubectl returned invalid JSON for {context}"
        ) from exc

    if not isinstance(value, dict):
        raise BenchmarkError(
            f"Unexpected kubectl output for {context}"
        )

    return value


def apply_manifest(
    context: str,
    manifest: dict[str, Any],
    timeout_seconds: int = 60,
) -> None:
    run_command(
        [
            "kubectl",
            "--context",
            context,
            "apply",
            "-f",
            "-",
        ],
        input_text=json.dumps(manifest),
        timeout_seconds=timeout_seconds,
    )


def delete_namespace(
    context: str,
    namespace: str,
) -> None:
    run_command(
        [
            "kubectl",
            "--context",
            context,
            "delete",
            "namespace",
            namespace,
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=180s",
        ],
        timeout_seconds=210,
    )


def build_namespace_manifest(
    namespace: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {
                "purpose": "serverless-benchmark",
            },
        },
    }


def build_service_manifest(
    cluster_name: str,
    namespace: str,
    service_name: str,
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    container = descriptor["container"]
    resources = container["resources"]

    requests_config = resources["requests"]
    limits_config = resources["limits"]

    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "namespace": namespace,
            "labels": {
                "purpose": "serverless-benchmark",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "autoscaling.knative.dev/min-scale": "1",
                        "autoscaling.knative.dev/max-scale": "1",
                    },
                },
                "spec": {
                    "containerConcurrency": 1,
                    "timeoutSeconds": int(
                        container["timeout_seconds"]
                    ),
                    "nodeSelector": descriptor[
                        "scheduling"
                    ]["node_selector"],
                    "containers": [
                        {
                            "name": "user-container",
                            "image": descriptor["image"],
                            "imagePullPolicy": "Always",
                            "ports": [
                                {
                                    "containerPort": int(
                                        container["port"]
                                    ),
                                }
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": (
                                        f"{requests_config['cpu_millicores']}m"
                                    ),
                                    "memory": (
                                        f"{requests_config['memory_mb']}Mi"
                                    ),
                                },
                                "limits": {
                                    "cpu": (
                                        f"{limits_config['cpu_millicores']}m"
                                    ),
                                    "memory": (
                                        f"{limits_config['memory_mb']}Mi"
                                    ),
                                },
                            },
                            "env": [
                                {
                                    "name": "CLUSTER_NAME",
                                    "value": cluster_name,
                                },
                                {
                                    "name": "NODE_NAME",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "spec.nodeName"
                                            ),
                                        },
                                    },
                                },
                                {
                                    "name": "POD_NAME",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "metadata.name"
                                            ),
                                        },
                                    },
                                },
                                {
                                    "name": "POD_NAMESPACE",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "metadata.namespace"
                                            ),
                                        },
                                    },
                                },
                                {
                                    "name": "POD_UID",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "metadata.uid"
                                            ),
                                        },
                                    },
                                },
                                {
                                    "name": "POD_IP",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "status.podIP"
                                            ),
                                        },
                                    },
                                },
                                {
                                    "name": "HOST_IP",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "status.hostIP"
                                            ),
                                        },
                                    },
                                },
                            ],
                        }
                    ],
                },
            },
        },
    }


def wait_for_service(
    context: str,
    namespace: str,
    service_name: str,
    timeout_seconds: int,
) -> None:
    run_command(
        [
            "kubectl",
            "--context",
            context,
            "wait",
            "--for=condition=Ready",
            f"kservice/{service_name}",
            "-n",
            namespace,
            f"--timeout={timeout_seconds}s",
        ],
        timeout_seconds=timeout_seconds + 30,
    )


def get_service_url(
    context: str,
    namespace: str,
    service_name: str,
) -> str:
    service = kubectl_json(
        context,
        [
            "get",
            "kservice",
            service_name,
            "-n",
            namespace,
        ],
    )

    service_url = (
        service.get("status", {})
        .get("url")
    )

    if not service_url:
        raise BenchmarkError(
            f"No URL reported for "
            f"{context}/{namespace}/{service_name}"
        )

    return str(service_url)


def pod_is_ready(
    pod: dict[str, Any],
) -> bool:
    status = pod.get("status", {})

    if status.get("phase") != "Running":
        return False

    container_statuses = status.get(
        "containerStatuses",
        [],
    )

    return bool(container_statuses) and all(
        container.get("ready", False)
        for container in container_statuses
    )


def get_ready_pod(
    context: str,
    namespace: str,
    service_name: str,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        response = kubectl_json(
            context,
            [
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                (
                    "serving.knative.dev/service="
                    f"{service_name}"
                ),
            ],
        )

        ready_pods = [
            pod
            for pod in response.get("items", [])
            if pod_is_ready(pod)
        ]

        if ready_pods:
            pod = ready_pods[0]
            metadata = pod.get("metadata", {})
            spec = pod.get("spec", {})
            status = pod.get("status", {})
            labels = metadata.get("labels", {})

            return {
                "name": metadata.get("name"),
                "node": spec.get("nodeName"),
                "namespace": metadata.get("namespace"),
                "pod_ip": status.get("podIP"),
                "revision": labels.get(
                    "serving.knative.dev/revision"
                ),
            }

        time.sleep(2)

    raise BenchmarkError(
        f"No Ready benchmark pod found in "
        f"{context}/{namespace}"
    )


def build_request_url(
    service_url: str,
    endpoint: str,
) -> str:
    normalized_endpoint = endpoint

    if not normalized_endpoint.startswith("/"):
        normalized_endpoint = "/" + normalized_endpoint

    return service_url.rstrip("/") + normalized_endpoint


def invoke_request(
    session: requests.Session,
    run_id: str,
    cluster_name: str,
    request_type: str,
    request_number: int,
    request_url: str,
    method: str,
    workload_parameter: str,
    workload_value: int,
    timeout_seconds: int,
    expected_pod: str,
    expected_node: str,
) -> dict[str, Any]:
    request_id = (
        f"{run_id}-{cluster_name}-"
        f"{request_type}-{request_number}-"
        f"{uuid.uuid4().hex[:10]}"
    )

    started_at = utc_now(milliseconds=True)
    started_perf = time.perf_counter()

    http_status = 0
    response_payload: dict[str, Any] = {}
    errors: list[str] = []

    try:
        if method.upper() == "GET":
            response = session.get(
                request_url,
                params={
                    workload_parameter: workload_value,
                },
                headers={
                    "X-Request-ID": request_id,
                },
                timeout=timeout_seconds,
            )
        else:
            response = session.post(
                request_url,
                json={
                    workload_parameter: workload_value,
                },
                headers={
                    "X-Request-ID": request_id,
                },
                timeout=timeout_seconds,
            )

        http_status = response.status_code

        try:
            payload = response.json()

            if isinstance(payload, dict):
                response_payload = payload
            else:
                errors.append(
                    "Response JSON was not an object"
                )
        except ValueError:
            errors.append(
                "Response body was not valid JSON"
            )

    except requests.RequestException as exc:
        errors.append(str(exc))

    finished_at = utc_now(milliseconds=True)
    response_time_ms = (
        time.perf_counter() - started_perf
    ) * 1000

    if http_status != 200:
        errors.append(
            f"Unexpected HTTP status {http_status}"
        )

    if response_payload.get("request_id") != request_id:
        errors.append("Request ID mismatch")

    if response_payload.get("cluster") != cluster_name:
        errors.append("Cluster mismatch")

    if response_payload.get("pod") != expected_pod:
        errors.append("Pod mismatch")

    if response_payload.get("node") != expected_node:
        errors.append("Node mismatch")

    if (
        response_payload.get("work_requested_ms")
        != workload_value
    ):
        errors.append("Workload value mismatch")

    return {
        "run_id": run_id,
        "cluster": cluster_name,
        "request_type": request_type,
        "request_number": request_number,
        "request_id": request_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "http_status": http_status,
        "success": not errors,
        "response_time_ms": rounded(
            response_time_ms
        ),
        "function_duration_ms": finite_float(
            response_payload.get(
                "work_duration_ms"
            )
        ),
        "cluster_returned": response_payload.get(
            "cluster"
        ),
        "pod": response_payload.get("pod"),
        "node": response_payload.get("node"),
        "revision": response_payload.get(
            "knative_revision"
        ),
        "error": "; ".join(errors),
    }


def run_request_group(
    session: requests.Session,
    run_id: str,
    cluster_name: str,
    request_type: str,
    request_count: int,
    request_url: str,
    descriptor: dict[str, Any],
    benchmark_config: dict[str, Any],
    pod: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    workload = descriptor["workload"]
    delay_seconds = (
        int(
            benchmark_config[
                "inter_request_delay_ms"
            ]
        )
        / 1000
    )

    for request_number in range(
        1,
        request_count + 1,
    ):
        row = invoke_request(
            session=session,
            run_id=run_id,
            cluster_name=cluster_name,
            request_type=request_type,
            request_number=request_number,
            request_url=request_url,
            method=descriptor["method"],
            workload_parameter=workload["parameter"],
            workload_value=int(workload["value"]),
            timeout_seconds=int(
                benchmark_config[
                    "request_timeout_seconds"
                ]
            ),
            expected_pod=str(pod["name"]),
            expected_node=str(pod["node"]),
        )

        rows.append(row)

        print(
            f"{cluster_name} "
            f"{request_type} "
            f"{request_number}/{request_count}: "
            f"success={row['success']} "
            f"latency={row['response_time_ms']}ms",
            flush=True,
        )

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return rows


class PrometheusClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.trust_env = False

    def ready(self) -> bool:
        try:
            response = self.session.get(
                f"{self.base_url}/-/ready",
                timeout=self.timeout_seconds,
            )
        except requests.RequestException:
            return False

        return response.status_code == 200

    def query_range(
        self,
        expression: str,
        start_epoch: float,
        end_epoch: float,
        step_seconds: int,
    ) -> list[dict[str, Any]]:
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/query_range",
                params={
                    "query": expression,
                    "start": start_epoch,
                    "end": end_epoch,
                    "step": step_seconds,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            raise BenchmarkError(
                f"Prometheus range query failed: "
                f"{expression}: {exc}"
            ) from exc

        if payload.get("status") != "success":
            raise BenchmarkError(
                f"Prometheus rejected query: "
                f"{expression}"
            )

        result = (
            payload.get("data", {})
            .get("result", [])
        )

        return result if isinstance(
            result,
            list,
        ) else []


def add_metric_samples(
    sample_map: dict[
        tuple[int, str, str],
        dict[str, Any],
    ],
    result: list[dict[str, Any]],
    field_name: str,
) -> None:
    for series in result:
        metric = series.get("metric", {})
        pod = str(metric.get("pod", "unknown"))
        node = str(metric.get("node", "unknown"))

        for sample in series.get("values", []):
            if (
                not isinstance(sample, list)
                or len(sample) < 2
            ):
                continue

            sample_epoch = finite_float(sample[0])
            sample_value = finite_float(sample[1])

            if (
                sample_epoch is None
                or sample_value is None
            ):
                continue

            timestamp_key = int(sample_epoch)
            key = (
                timestamp_key,
                pod,
                node,
            )

            row = sample_map.setdefault(
                key,
                {
                    "sample_epoch": timestamp_key,
                    "sample_timestamp": epoch_to_utc(
                        timestamp_key
                    ),
                    "pod": pod,
                    "node": node,
                    "cpu_millicores": None,
                    "memory_mb": None,
                },
            )

            row[field_name] = rounded(sample_value)


def collect_resource_samples(
    run_id: str,
    cluster_name: str,
    prometheus_url: str,
    namespace: str,
    pod: dict[str, Any],
    window_start_epoch: float,
    window_end_epoch: float,
    step_seconds: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    client = PrometheusClient(
        prometheus_url,
        timeout_seconds,
    )

    if not client.ready():
        raise BenchmarkError(
            f"Prometheus is not ready: "
            f"{prometheus_url}"
        )

    pod_name = pod["name"]

    cpu_expression = (
        "sum by(node,pod) ("
        "rate(container_cpu_usage_seconds_total{"
        f'namespace="{namespace}",'
        f'pod="{pod_name}",'
        'container="user-container"'
        "}[1m])"
        ") * 1000"
    )

    memory_expression = (
        "sum by(node,pod) ("
        "container_memory_working_set_bytes{"
        f'namespace="{namespace}",'
        f'pod="{pod_name}",'
        'container="user-container"'
        "}"
        ") / 1024 / 1024"
    )

    cpu_result = client.query_range(
        cpu_expression,
        window_start_epoch,
        window_end_epoch,
        step_seconds,
    )

    memory_result = client.query_range(
        memory_expression,
        window_start_epoch,
        window_end_epoch,
        step_seconds,
    )

    sample_map: dict[
        tuple[int, str, str],
        dict[str, Any],
    ] = {}

    add_metric_samples(
        sample_map,
        cpu_result,
        "cpu_millicores",
    )

    add_metric_samples(
        sample_map,
        memory_result,
        "memory_mb",
    )

    rows = []

    for key in sorted(sample_map):
        row = sample_map[key]
        row["run_id"] = run_id
        row["cluster"] = cluster_name
        rows.append(row)

    cpu_values = [
        row["cpu_millicores"]
        for row in rows
        if row["cpu_millicores"] is not None
    ]

    memory_values = [
        row["memory_mb"]
        for row in rows
        if row["memory_mb"] is not None
    ]

    if not cpu_values:
        raise BenchmarkError(
            f"No CPU samples returned for "
            f"{cluster_name}/{pod_name}"
        )

    if not memory_values:
        raise BenchmarkError(
            f"No memory samples returned for "
            f"{cluster_name}/{pod_name}"
        )

    return rows


def summarize_cluster(
    cluster_name: str,
    namespace: str,
    service_name: str,
    service_url: str,
    pod: dict[str, Any],
    request_rows: list[dict[str, Any]],
    resource_rows: list[dict[str, Any]],
    benchmark_started_at: str,
    benchmark_finished_at: str,
    resource_window_started_at: str,
    resource_window_finished_at: str,
) -> dict[str, Any]:
    warmup_rows = [
        row
        for row in request_rows
        if row["request_type"] == "warmup"
    ]

    measured_rows = [
        row
        for row in request_rows
        if row["request_type"] == "measured"
    ]

    successful_rows = [
        row
        for row in measured_rows
        if row["success"]
    ]

    failed_rows = [
        row
        for row in measured_rows
        if not row["success"]
    ]

    latency_values = [
        float(row["response_time_ms"])
        for row in successful_rows
    ]

    function_duration_values = [
        float(row["function_duration_ms"])
        for row in successful_rows
        if row["function_duration_ms"] is not None
    ]

    cpu_values = [
        float(row["cpu_millicores"])
        for row in resource_rows
        if row["cpu_millicores"] is not None
    ]

    memory_values = [
        float(row["memory_mb"])
        for row in resource_rows
        if row["memory_mb"] is not None
    ]

    measured_count = len(measured_rows)

    success_rate = (
        100 * len(successful_rows) / measured_count
        if measured_count
        else 0
    )

    return {
        "status": "success",
        "cluster": cluster_name,
        "benchmark_namespace": namespace,
        "benchmark_service": service_name,
        "service_url": service_url,
        "pod": pod["name"],
        "worker_node": pod["node"],
        "revision": pod["revision"],
        "benchmark_started_at": benchmark_started_at,
        "benchmark_finished_at": benchmark_finished_at,
        "resource_window_started_at": (
            resource_window_started_at
        ),
        "resource_window_finished_at": (
            resource_window_finished_at
        ),
        "warmup_requests": len(warmup_rows),
        "measured_requests": measured_count,
        "successful_requests": len(successful_rows),
        "failed_requests": len(failed_rows),
        "success_rate_percent": rounded(
            success_rate
        ),
        "mean_latency_ms": rounded(
            statistics.mean(latency_values)
            if latency_values
            else None
        ),
        "median_latency_ms": rounded(
            statistics.median(latency_values)
            if latency_values
            else None
        ),
        "p95_latency_ms": rounded(
            percentile(
                latency_values,
                0.95,
            )
        ),
        "minimum_latency_ms": rounded(
            min(latency_values)
            if latency_values
            else None
        ),
        "maximum_latency_ms": rounded(
            max(latency_values)
            if latency_values
            else None
        ),
        "average_function_duration_ms": rounded(
            statistics.mean(
                function_duration_values
            )
            if function_duration_values
            else None
        ),
        "average_cpu_millicores": rounded(
            statistics.mean(cpu_values)
            if cpu_values
            else None
        ),
        "peak_cpu_millicores": rounded(
            max(cpu_values)
            if cpu_values
            else None
        ),
        "average_memory_mb": rounded(
            statistics.mean(memory_values)
            if memory_values
            else None
        ),
        "peak_memory_mb": rounded(
            max(memory_values)
            if memory_values
            else None
        ),
        "resource_sample_count": len(resource_rows),
    }


def benchmark_cluster(
    run_id: str,
    cluster_name: str,
    infrastructure: dict[str, Any],
    descriptor: dict[str, Any],
    controller_config: dict[str, Any],
    request_rows: list[dict[str, Any]],
    resource_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    benchmark_config = controller_config[
        "benchmarking"
    ]
    deployment_config = controller_config[
        "deployment"
    ]

    context = infrastructure[
        "kubeconfig_context"
    ]
    prometheus_url = infrastructure[
        "prometheus"
    ]["url"]

    namespace = benchmark_config["namespace"]
    service_name = benchmark_config[
        "service_name"
    ]

    deployment_timeout = int(
        deployment_config["timeout_seconds"]
    )

    print(
        f"\n===== Benchmarking {cluster_name} =====",
        flush=True,
    )

    print(
        f"Removing stale namespace "
        f"{context}/{namespace}...",
        flush=True,
    )

    delete_namespace(
        context,
        namespace,
    )

    apply_manifest(
        context,
        build_namespace_manifest(namespace),
    )

    apply_manifest(
        context,
        build_service_manifest(
            cluster_name,
            namespace,
            service_name,
            descriptor,
        ),
    )

    print(
        "Waiting for Knative Service readiness...",
        flush=True,
    )

    wait_for_service(
        context,
        namespace,
        service_name,
        deployment_timeout,
    )

    service_url = get_service_url(
        context,
        namespace,
        service_name,
    )

    pod = get_ready_pod(
        context,
        namespace,
        service_name,
    )

    print(
        f"Service URL: {service_url}",
        flush=True,
    )
    print(
        f"Pod: {pod['name']}",
        flush=True,
    )
    print(
        f"Worker node: {pod['node']}",
        flush=True,
    )

    pre_wait = int(
        benchmark_config[
            "metrics_pre_wait_seconds"
        ]
    )

    print(
        f"Waiting {pre_wait}s for initial "
        "Prometheus samples...",
        flush=True,
    )

    time.sleep(pre_wait)

    session = requests.Session()
    session.trust_env = False

    request_url = build_request_url(
        service_url,
        descriptor["endpoint"],
    )

    benchmark_started_at = utc_now()
    resource_window_start_epoch = time.time()

    cluster_request_rows = run_request_group(
        session=session,
        run_id=run_id,
        cluster_name=cluster_name,
        request_type="warmup",
        request_count=int(
            benchmark_config["warmup_requests"]
        ),
        request_url=request_url,
        descriptor=descriptor,
        benchmark_config=benchmark_config,
        pod=pod,
    )

    cluster_request_rows.extend(
        run_request_group(
            session=session,
            run_id=run_id,
            cluster_name=cluster_name,
            request_type="measured",
            request_count=int(
                benchmark_config[
                    "measured_requests"
                ]
            ),
            request_url=request_url,
            descriptor=descriptor,
            benchmark_config=benchmark_config,
            pod=pod,
        )
    )

    benchmark_finished_at = utc_now()

    post_wait = int(
        benchmark_config[
            "metrics_post_wait_seconds"
        ]
    )

    print(
        f"Waiting {post_wait}s for final "
        "Prometheus samples...",
        flush=True,
    )

    time.sleep(post_wait)

    resource_window_end_epoch = time.time()

    cluster_resource_rows = collect_resource_samples(
        run_id=run_id,
        cluster_name=cluster_name,
        prometheus_url=prometheus_url,
        namespace=namespace,
        pod=pod,
        window_start_epoch=(
            resource_window_start_epoch
        ),
        window_end_epoch=(
            resource_window_end_epoch
        ),
        step_seconds=int(
            benchmark_config[
                "prometheus_step_seconds"
            ]
        ),
        timeout_seconds=int(
            benchmark_config[
                "request_timeout_seconds"
            ]
        ),
    )

    request_rows.extend(cluster_request_rows)
    resource_rows.extend(cluster_resource_rows)

    return summarize_cluster(
        cluster_name=cluster_name,
        namespace=namespace,
        service_name=service_name,
        service_url=service_url,
        pod=pod,
        request_rows=cluster_request_rows,
        resource_rows=cluster_resource_rows,
        benchmark_started_at=benchmark_started_at,
        benchmark_finished_at=benchmark_finished_at,
        resource_window_started_at=epoch_to_utc(
            resource_window_start_epoch
        ),
        resource_window_finished_at=epoch_to_utc(
            resource_window_end_epoch
        ),
    )


def write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(rows)


def default_run_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    return f"benchmark-{timestamp}"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark one function independently "
            "on candidate Knative clusters."
        )
    )

    parser.add_argument(
        "--config-directory",
        type=Path,
        default=DEFAULT_CONFIG_DIRECTORY,
    )

    parser.add_argument(
        "--results-directory",
        type=Path,
        default=DEFAULT_RESULTS_DIRECTORY,
    )

    parser.add_argument(
        "--run-id",
        default=default_run_id(),
    )

    parser.add_argument(
        "--cluster",
        action="append",
        dest="clusters",
        help=(
            "Benchmark one configured cluster. "
            "May be repeated."
        ),
    )

    parser.add_argument(
        "--keep",
        action="store_true",
        help=(
            "Keep benchmark namespaces after the run."
        ),
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
        descriptor = load_json(
            config_directory / "function_descriptor.json"
        )
    except BenchmarkError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2

    configured_clusters = infrastructure_config.get(
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
        if cluster not in configured_clusters
    ]

    if unknown_clusters:
        print(
            "ERROR: Unknown clusters: "
            + ", ".join(unknown_clusters),
            file=sys.stderr,
        )
        return 2

    run_directory = (
        arguments.results_directory
        / arguments.run_id
    )

    if run_directory.exists():
        print(
            f"ERROR: Run directory already exists: "
            f"{run_directory}",
            file=sys.stderr,
        )
        return 2

    run_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    request_rows: list[dict[str, Any]] = []
    resource_rows: list[dict[str, Any]] = []

    profile: dict[str, Any] = {
        "schema_version": 1,
        "run_id": arguments.run_id,
        "generated_at": utc_now(),
        "function": descriptor["name"],
        "image": descriptor["image"],
        "work_ms": descriptor[
            "workload"
        ]["value"],
        "warmup_requests": controller_config[
            "benchmarking"
        ]["warmup_requests"],
        "measured_requests": controller_config[
            "benchmarking"
        ]["measured_requests"],
        "benchmarking_only": True,
        "placement_decision": None,
        "clusters": {},
        "files": {
            "requests_csv": "requests.csv",
            "resource_samples_csv": (
                "resource_samples.csv"
            ),
            "profile_json": "profile.json",
        },
    }

    cleanup_enabled = (
        bool(
            controller_config[
                "benchmarking"
            ].get(
                "cleanup_after_run",
                True,
            )
        )
        and not arguments.keep
    )

    for cluster_name in selected_clusters:
        cluster_config = configured_clusters[
            cluster_name
        ]

        context = cluster_config[
            "kubeconfig_context"
        ]

        namespace = controller_config[
            "benchmarking"
        ]["namespace"]

        try:
            cluster_profile = benchmark_cluster(
                run_id=arguments.run_id,
                cluster_name=cluster_name,
                infrastructure=cluster_config,
                descriptor=descriptor,
                controller_config=controller_config,
                request_rows=request_rows,
                resource_rows=resource_rows,
            )

            profile["clusters"][
                cluster_name
            ] = cluster_profile

        except BenchmarkError as exc:
            profile["clusters"][
                cluster_name
            ] = {
                "status": "failed",
                "cluster": cluster_name,
                "error": str(exc),
            }

            print(
                f"ERROR: {cluster_name}: {exc}",
                file=sys.stderr,
            )

        finally:
            if cleanup_enabled:
                print(
                    f"Cleaning benchmark namespace "
                    f"from {cluster_name}...",
                    flush=True,
                )

                try:
                    delete_namespace(
                        context,
                        namespace,
                    )
                except BenchmarkError as exc:
                    print(
                        f"WARNING: cleanup failed for "
                        f"{cluster_name}: {exc}",
                        file=sys.stderr,
                    )

    requests_path = (
        run_directory / "requests.csv"
    )

    resources_path = (
        run_directory / "resource_samples.csv"
    )

    profile_path = (
        run_directory / "profile.json"
    )

    write_csv(
        requests_path,
        REQUEST_FIELDS,
        request_rows,
    )

    write_csv(
        resources_path,
        RESOURCE_FIELDS,
        resource_rows,
    )

    profile["completed_at"] = utc_now()

    with profile_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            profile,
            file,
            indent=2,
        )
        file.write("\n")

    latest_profile_path = (
        arguments.results_directory
        / "latest-profile.json"
    )

    shutil.copyfile(
        profile_path,
        latest_profile_path,
    )

    print()
    print(f"Run directory: {run_directory}")
    print(f"Raw requests: {requests_path}")
    print(f"Resource samples: {resources_path}")
    print(f"Aggregated profile: {profile_path}")

    for cluster_name, cluster in profile[
        "clusters"
    ].items():
        print(
            f"{cluster_name}: "
            f"status={cluster.get('status')}, "
            f"p95={cluster.get('p95_latency_ms')}ms, "
            f"avg_cpu="
            f"{cluster.get('average_cpu_millicores')}m, "
            f"peak_cpu="
            f"{cluster.get('peak_cpu_millicores')}m, "
            f"avg_memory="
            f"{cluster.get('average_memory_mb')}MB, "
            f"peak_memory="
            f"{cluster.get('peak_memory_mb')}MB"
        )

    statuses = [
        cluster.get("status")
        for cluster in profile[
            "clusters"
        ].values()
    ]

    return 0 if all(
        status == "success"
        for status in statuses
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
