#!/usr/bin/env python3

from __future__ import annotations

import argparse
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

DEFAULT_CONFIG_DIRECTORY = (
    CONTROLLER_DIRECTORY / "config"
)

DEFAULT_DECISION = (
    CONTROLLER_DIRECTORY
    / "results"
    / "decisions"
    / "latest-decision.json"
)

DEFAULT_RESULTS_DIRECTORY = (
    CONTROLLER_DIRECTORY
    / "results"
    / "deployments"
)


class DeploymentError(RuntimeError):
    """Raised when deployment or execution fails."""


def utc_now(milliseconds: bool = False) -> str:
    timespec = "milliseconds" if milliseconds else "seconds"

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
    )


def default_execution_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    return f"execution-{timestamp}"


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result) or math.isinf(result):
        return None

    return result


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


def objective_satisfied(
    measured_value: float,
    operator: str,
    target_value: float,
) -> bool:
    if operator == "<":
        return measured_value < target_value

    if operator == "<=":
        return measured_value <= target_value

    if operator == "==":
        return measured_value == target_value

    if operator == ">=":
        return measured_value >= target_value

    if operator == ">":
        return measured_value > target_value

    raise DeploymentError(
        f"Unsupported objective operator: {operator}"
    )


def calculate_actual_metric(
    metric_name: str,
    response_times_ms: list[float],
) -> float:
    if not response_times_ms:
        raise DeploymentError(
            "No successful response times are available."
        )

    if metric_name == "response_time_mean_ms":
        return statistics.mean(response_times_ms)

    if metric_name == "response_time_p50_ms":
        return statistics.median(response_times_ms)

    if metric_name == "response_time_p95_ms":
        value = percentile(response_times_ms, 0.95)

        if value is None:
            raise DeploymentError(
                "Cannot calculate p95 response time."
            )

        return value

    if metric_name == "response_time_p99_ms":
        value = percentile(response_times_ms, 0.99)

        if value is None:
            raise DeploymentError(
                "Cannot calculate p99 response time."
            )

        return value

    raise DeploymentError(
        f"Unsupported intent metric: {metric_name}"
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError(
            f"Cannot load JSON file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise DeploymentError(
            f"{path} must contain a JSON object"
        )

    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise DeploymentError(
            f"Cannot load YAML file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise DeploymentError(
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
        raise DeploymentError(
            f"Command timed out: {' '.join(command)}"
        ) from exc

    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "unknown command error"
        )

        raise DeploymentError(
            f"Command failed: {' '.join(command)}: "
            f"{message}"
        )

    return completed.stdout


def command_succeeds(
    command: list[str],
    timeout_seconds: int = 20,
) -> bool:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False

    return completed.returncode == 0


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
        raise DeploymentError(
            f"kubectl returned invalid JSON for {context}"
        ) from exc

    if not isinstance(value, dict):
        raise DeploymentError(
            f"Unexpected kubectl output for {context}"
        )

    return value


def namespace_exists(
    context: str,
    namespace: str,
) -> bool:
    return command_succeeds(
        [
            "kubectl",
            "--context",
            context,
            "get",
            "namespace",
            namespace,
        ]
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


def apply_manifest(
    context: str,
    manifest: dict[str, Any],
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
        timeout_seconds=90,
        input_text=json.dumps(manifest),
    )


def build_namespace_manifest(
    namespace: str,
    decision_id: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {
                "purpose": "intent-runtime",
                "managed-by": "thesis-controller",
                "placement-decision": decision_id,
            },
        },
    }


def build_service_manifest(
    cluster_name: str,
    namespace: str,
    service_name: str,
    decision_id: str,
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    container = descriptor["container"]
    resources = container["resources"]
    requests_config = resources["requests"]
    limits_config = resources["limits"]
    autoscaling = descriptor["autoscaling"]

    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "namespace": namespace,
            "labels": {
                "purpose": "intent-runtime",
                "managed-by": "thesis-controller",
                "placement-decision": decision_id,
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "labels": {
                        "purpose": "intent-runtime",
                        "placement-decision": decision_id,
                    },
                    "annotations": {
                        "autoscaling.knative.dev/min-scale": str(
                            autoscaling["minimum_scale"]
                        ),
                        "autoscaling.knative.dev/max-scale": str(
                            autoscaling["maximum_scale"]
                        ),
                    },
                },
                "spec": {
                    "containerConcurrency": int(
                        container["container_concurrency"]
                    ),
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
                                            "fieldPath": "spec.nodeName",
                                        },
                                    },
                                },
                                {
                                    "name": "POD_NAME",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": "metadata.name",
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
                                            "fieldPath": "metadata.uid",
                                        },
                                    },
                                },
                                {
                                    "name": "POD_IP",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": "status.podIP",
                                        },
                                    },
                                },
                                {
                                    "name": "HOST_IP",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": "status.hostIP",
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


def get_service(
    context: str,
    namespace: str,
    service_name: str,
) -> dict[str, Any]:
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

    status = service.get("status", {})
    conditions = status.get("conditions", [])

    ready = any(
        condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )

    service_url = status.get("url")

    if not ready or not service_url:
        raise DeploymentError(
            f"Knative Service is not Ready: "
            f"{context}/{namespace}/{service_name}"
        )

    return {
        "ready": ready,
        "url": str(service_url),
        "latest_created_revision": status.get(
            "latestCreatedRevisionName"
        ),
        "latest_ready_revision": status.get(
            "latestReadyRevisionName"
        ),
    }


def wait_for_ready_pod(
    context: str,
    namespace: str,
    service_name: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> None:
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

        for pod in response.get("items", []):
            status = pod.get("status", {})

            if status.get("phase") != "Running":
                continue

            container_statuses = status.get(
                "containerStatuses",
                [],
            )

            if container_statuses and all(
                container.get("ready", False)
                for container in container_statuses
            ):
                return

        time.sleep(poll_interval_seconds)

    raise DeploymentError(
        f"No Ready pod found for "
        f"{context}/{namespace}/{service_name}"
    )


def get_pod_location(
    context: str,
    namespace: str,
    pod_name: str,
) -> dict[str, Any]:
    pod = kubectl_json(
        context,
        [
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
        ],
    )

    metadata = pod.get("metadata", {})
    spec = pod.get("spec", {})
    status = pod.get("status", {})
    labels = metadata.get("labels", {})

    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "node": spec.get("nodeName"),
        "phase": status.get("phase"),
        "pod_ip": status.get("podIP"),
        "host_ip": status.get("hostIP"),
        "revision": labels.get(
            "serving.knative.dev/revision"
        ),
    }


def node_is_ready(node: dict[str, Any]) -> bool:
    conditions = node.get(
        "status",
        {},
    ).get(
        "conditions",
        [],
    )

    return any(
        condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )


def get_node_state(
    context: str,
    node_name: str,
) -> dict[str, Any]:
    node = kubectl_json(
        context,
        [
            "get",
            "node",
            node_name,
        ],
    )

    metadata = node.get("metadata", {})
    spec = node.get("spec", {})
    labels = metadata.get("labels", {})

    control_plane = (
        "node-role.kubernetes.io/control-plane" in labels
        or "node-role.kubernetes.io/master" in labels
    )

    ready = node_is_ready(node)
    unschedulable = bool(
        spec.get("unschedulable", False)
    )

    eligible = (
        ready
        and not unschedulable
        and not control_plane
        and labels.get("workload") == "serverless"
    )

    return {
        "name": node_name,
        "ready": ready,
        "unschedulable": unschedulable,
        "control_plane": control_plane,
        "workload_label": labels.get("workload"),
        "worker_id": labels.get("worker-id"),
        "cluster_label": labels.get("cluster"),
        "eligible_for_serverless": eligible,
    }


def request_url(
    service_url: str,
    endpoint: str,
) -> str:
    normalized_endpoint = endpoint

    if not normalized_endpoint.startswith("/"):
        normalized_endpoint = "/" + normalized_endpoint

    return (
        service_url.rstrip("/")
        + normalized_endpoint
    )


def invoke_function(
    session: requests.Session,
    context: str,
    namespace: str,
    execution_id: str,
    selected_cluster: str,
    request_number: int,
    url: str,
    descriptor: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    workload = descriptor["workload"]

    request_id = (
        f"{execution_id}-{request_number}-"
        f"{uuid.uuid4().hex[:10]}"
    )

    started_at = utc_now(milliseconds=True)
    started_perf = time.perf_counter()

    http_status = 0
    payload: dict[str, Any] = {}
    errors: list[str] = []

    try:
        if descriptor["method"].upper() == "GET":
            response = session.get(
                url,
                params={
                    workload["parameter"]: workload["value"],
                },
                headers={
                    "X-Request-ID": request_id,
                },
                timeout=timeout_seconds,
            )
        else:
            response = session.post(
                url,
                json={
                    workload["parameter"]: workload["value"],
                },
                headers={
                    "X-Request-ID": request_id,
                },
                timeout=timeout_seconds,
            )

        http_status = response.status_code

        try:
            decoded = response.json()

            if isinstance(decoded, dict):
                payload = decoded
            else:
                errors.append(
                    "Response JSON is not an object"
                )
        except ValueError:
            errors.append(
                "Response body is not valid JSON"
            )

    except requests.RequestException as exc:
        errors.append(str(exc))

    response_time_ms = round(
        (
            time.perf_counter()
            - started_perf
        )
        * 1000,
        3,
    )

    finished_at = utc_now(milliseconds=True)

    if http_status != 200:
        errors.append(
            f"Unexpected HTTP status {http_status}"
        )

    returned_cluster = payload.get("cluster")
    returned_pod = payload.get("pod")
    returned_node = payload.get("node")

    if payload.get("request_id") != request_id:
        errors.append("Request ID mismatch")

    if returned_cluster != selected_cluster:
        errors.append(
            f"Cluster mismatch: expected="
            f"{selected_cluster}, actual="
            f"{returned_cluster}"
        )

    if (
        payload.get("work_requested_ms")
        != workload["value"]
    ):
        errors.append(
            "Returned workload value does not match "
            "the requested workload."
        )

    kubernetes_pod = None
    kubernetes_node = None

    if returned_pod:
        try:
            kubernetes_pod = get_pod_location(
                context,
                namespace,
                str(returned_pod),
            )
        except DeploymentError as exc:
            errors.append(str(exc))
    else:
        errors.append(
            "Response does not contain a pod name"
        )

    if kubernetes_pod is not None:
        actual_node = kubernetes_pod.get("node")

        if returned_node != actual_node:
            errors.append(
                f"Node mismatch: response={returned_node}, "
                f"Kubernetes={actual_node}"
            )

        if actual_node:
            try:
                kubernetes_node = get_node_state(
                    context,
                    str(actual_node),
                )

                if not kubernetes_node[
                    "eligible_for_serverless"
                ]:
                    errors.append(
                        f"Pod executed on an ineligible node: "
                        f"{actual_node}"
                    )

            except DeploymentError as exc:
                errors.append(str(exc))
    elif not returned_node:
        errors.append(
            "Response does not contain a node name"
        )

    returned_service = (
        payload.get("knative_service")
        or payload.get("K_SERVICE")
    )

    returned_revision = (
        payload.get("knative_revision")
        or payload.get("K_REVISION")
    )

    return {
        "request_number": request_number,
        "request_id": request_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "http_status": http_status,
        "success": not errors,
        "response_time_ms": response_time_ms,
        "function_duration_ms": finite_float(
            payload.get("work_duration_ms")
        ),
        "returned_cluster": returned_cluster,
        "returned_pod": returned_pod,
        "returned_node": returned_node,
        "returned_service": returned_service,
        "returned_revision": returned_revision,
        "kubernetes_pod": kubernetes_pod,
        "kubernetes_node": kubernetes_node,
        "errors": errors,
        "response": payload,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy a function only to the cluster "
            "selected by an existing Phase 10 decision."
        )
    )

    parser.add_argument(
        "--config-directory",
        type=Path,
        default=DEFAULT_CONFIG_DIRECTORY,
    )

    parser.add_argument(
        "--decision",
        type=Path,
        default=DEFAULT_DECISION,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--execution-id",
        default=default_execution_id(),
    )

    parser.add_argument(
        "--cleanup-after",
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

        descriptor = load_json(
            config_directory / "function_descriptor.json"
        )

        decision = load_json(arguments.decision)

        selected_cluster = decision.get(
            "selected_cluster"
        )

        if not selected_cluster:
            raise DeploymentError(
                "Phase 10 decision contains no selected cluster."
            )

        if decision.get("phase") != "placement-policy":
            raise DeploymentError(
                "Input file is not a Phase 10 placement decision."
            )

        if decision.get("deployment_performed") is not False:
            raise DeploymentError(
                "Phase 10 decision must not already contain "
                "a deployment."
            )

        candidate_clusters = controller_config[
            "candidate_clusters"
        ]

        if selected_cluster not in candidate_clusters:
            raise DeploymentError(
                f"Selected cluster is not configured: "
                f"{selected_cluster}"
            )

        clusters = infrastructure_config["clusters"]

        if selected_cluster not in clusters:
            raise DeploymentError(
                f"Selected cluster is missing from "
                f"clusters.yaml: {selected_cluster}"
            )

        selected_context = clusters[
            selected_cluster
        ]["kubeconfig_context"]

        deployment_config = controller_config[
            "deployment"
        ]

        namespace = deployment_config["namespace"]
        service_name = deployment_config[
            "service_name"
        ]

        timeout_seconds = int(
            deployment_config["timeout_seconds"]
        )

        poll_interval_seconds = int(
            deployment_config[
                "poll_interval_seconds"
            ]
        )

        invocation_count = int(
            deployment_config["invocation_count"]
        )

        inter_request_delay_seconds = (
            int(
                deployment_config.get(
                    "inter_request_delay_ms",
                    0,
                )
            )
            / 1000
        )

        request_timeout_seconds = int(
            deployment_config[
                "request_timeout_seconds"
            ]
        )

        if invocation_count < 1:
            raise DeploymentError(
                "Invocation count must be at least one."
            )

        print(
            f"Using Phase 10 decision: "
            f"{decision.get('decision_id')}"
        )
        print(
            f"Decision mode: "
            f"{decision.get('decision_mode')}"
        )
        print(
            f"Selected cluster: {selected_cluster}"
        )
        print(
            f"Selected kubeconfig context: "
            f"{selected_context}"
        )

        cleanup_non_selected = bool(
            deployment_config.get(
                "cleanup_non_selected_clusters",
                True,
            )
        )

        if cleanup_non_selected:
            print(
                "Removing stale intent-runtime namespaces..."
            )

            for cluster_name in candidate_clusters:
                context = clusters[
                    cluster_name
                ]["kubeconfig_context"]

                delete_namespace(
                    context,
                    namespace,
                )
        else:
            delete_namespace(
                selected_context,
                namespace,
            )

        deployment_started_at = utc_now(
            milliseconds=True
        )
        deployment_started_perf = time.perf_counter()

        print(
            f"Creating {namespace} only on "
            f"{selected_cluster}..."
        )

        apply_manifest(
            selected_context,
            build_namespace_manifest(
                namespace,
                str(decision["decision_id"]),
            ),
        )

        print(
            f"Deploying {service_name} only on "
            f"{selected_cluster}..."
        )

        apply_manifest(
            selected_context,
            build_service_manifest(
                cluster_name=selected_cluster,
                namespace=namespace,
                service_name=service_name,
                decision_id=str(
                    decision["decision_id"]
                ),
                descriptor=descriptor,
            ),
        )

        print(
            "Waiting for the Knative Service "
            "to become Ready..."
        )

        wait_for_service(
            selected_context,
            namespace,
            service_name,
            timeout_seconds,
        )

        wait_for_ready_pod(
            selected_context,
            namespace,
            service_name,
            timeout_seconds,
            poll_interval_seconds,
        )

        service = get_service(
            selected_context,
            namespace,
            service_name,
        )

        deployment_ready_at = utc_now(
            milliseconds=True
        )

        deployment_time_ms = round(
            (
                time.perf_counter()
                - deployment_started_perf
            )
            * 1000,
            3,
        )

        url = request_url(
            service["url"],
            descriptor["endpoint"],
        )

        print(f"Function URL: {service['url']}")

        session = requests.Session()
        session.trust_env = False

        invocations: list[dict[str, Any]] = []

        for request_number in range(
            1,
            invocation_count + 1,
        ):
            result = invoke_function(
                session=session,
                context=selected_context,
                namespace=namespace,
                execution_id=arguments.execution_id,
                selected_cluster=selected_cluster,
                request_number=request_number,
                url=url,
                descriptor=descriptor,
                timeout_seconds=request_timeout_seconds,
            )

            invocations.append(result)

            print(
                f"Invocation "
                f"{request_number}/{invocation_count}: "
                f"success={result['success']}, "
                f"latency={result['response_time_ms']}ms, "
                f"pod={result['returned_pod']}, "
                f"node={result['returned_node']}"
            )

            if (
                inter_request_delay_seconds > 0
                and request_number < invocation_count
            ):
                time.sleep(
                    inter_request_delay_seconds
                )

        successful_invocations = [
            result
            for result in invocations
            if result["success"]
        ]

        failed_invocations = [
            result
            for result in invocations
            if not result["success"]
        ]

        response_times_ms = [
            float(result["response_time_ms"])
            for result in successful_invocations
        ]

        objective = decision["intent"]["objective"]
        metric_name = objective["metric"]
        operator = objective["operator"]
        target_value = float(objective["value"])

        actual_metric_value = calculate_actual_metric(
            metric_name,
            response_times_ms,
        )

        actual_satisfied = objective_satisfied(
            actual_metric_value,
            operator,
            target_value,
        )

        selected_candidate = (
            decision.get("candidates", {})
            .get(selected_cluster, {})
        )

        selection_objective = (
            selected_candidate.get(
                "objective",
                {},
            )
        )

        non_selected_clusters: dict[
            str,
            dict[str, Any],
        ] = {}

        for cluster_name in candidate_clusters:
            if cluster_name == selected_cluster:
                continue

            context = clusters[
                cluster_name
            ]["kubeconfig_context"]

            non_selected_clusters[
                cluster_name
            ] = {
                "context": context,
                "runtime_namespace_exists": (
                    namespace_exists(
                        context,
                        namespace,
                    )
                ),
            }

        non_selected_clean = all(
            not state["runtime_namespace_exists"]
            for state in non_selected_clusters.values()
        )

        observed_pods: dict[
            str,
            dict[str, Any],
        ] = {}

        observed_nodes: dict[
            str,
            dict[str, Any],
        ] = {}

        for result in successful_invocations:
            pod = result.get("kubernetes_pod")
            node = result.get("kubernetes_node")

            if pod and pod.get("name"):
                observed_pods[
                    str(pod["name"])
                ] = pod

            if node and node.get("name"):
                observed_nodes[
                    str(node["name"])
                ] = node

        nodes_eligible = bool(
            observed_nodes
        ) and all(
            node["eligible_for_serverless"]
            for node in observed_nodes.values()
        )

        execution_successful = (
            len(successful_invocations)
            == invocation_count
            and not failed_invocations
            and non_selected_clean
            and nodes_eligible
        )

        keep_after_execution = bool(
            deployment_config.get(
                "keep_after_execution",
                True,
            )
        )

        cleanup_after_execution = (
            arguments.cleanup_after
            or not keep_after_execution
        )

        artifact = {
            "schema_version": 1,
            "execution_id": arguments.execution_id,
            "generated_at": utc_now(),
            "phase": "deployment-and-execution",
            "decision_id": decision.get(
                "decision_id"
            ),
            "decision_mode": decision.get(
                "decision_mode"
            ),
            "selected_cluster": selected_cluster,
            "selected_context": selected_context,
            "deployment_performed": True,
            "benchmarking_performed": False,
            "policy_evaluation_performed": False,
            "execution_successful": (
                execution_successful
            ),
            "runtime": {
                "namespace": namespace,
                "service_name": service_name,
                "image": descriptor["image"],
                "service_url": service["url"],
                "request_url": url,
                "service_ready": service["ready"],
                "latest_created_revision": (
                    service[
                        "latest_created_revision"
                    ]
                ),
                "latest_ready_revision": (
                    service[
                        "latest_ready_revision"
                    ]
                ),
            },
            "deployment_timing": {
                "started_at": deployment_started_at,
                "service_ready_at": deployment_ready_at,
                "duration_ms": deployment_time_ms,
                "scope": (
                    "Namespace creation through "
                    "Knative Service and pod readiness"
                ),
            },
            "selection_evaluation": {
                "source": "phase10-decision",
                "metric": metric_name,
                "operator": operator,
                "target_value": target_value,
                "measured_value": (
                    selection_objective.get(
                        "measured_value"
                    )
                ),
                "satisfied": decision.get(
                    "objective_satisfied"
                ),
            },
            "actual_execution_evaluation": {
                "source": "phase11-invocations",
                "metric": metric_name,
                "operator": operator,
                "target_value": target_value,
                "measured_value": round(
                    actual_metric_value,
                    3,
                ),
                "satisfied": actual_satisfied,
                "sample_count": len(
                    response_times_ms
                ),
                "response_times_ms": (
                    response_times_ms
                ),
                "note": (
                    "This is an execution-validation "
                    "sample, not a replacement for the "
                    "Phase 9 benchmark profile."
                ),
            },
            "placement": {
                "controller_selected_cluster": (
                    selected_cluster
                ),
                "kubernetes_observed_pods": list(
                    observed_pods.values()
                ),
                "kubernetes_observed_nodes": list(
                    observed_nodes.values()
                ),
            },
            "non_selected_clusters": (
                non_selected_clusters
            ),
            "invocation_count": invocation_count,
            "successful_invocations": len(
                successful_invocations
            ),
            "failed_invocations": len(
                failed_invocations
            ),
            "invocations": invocations,
            "cleanup_after_execution": (
                cleanup_after_execution
            ),
            "deployment_retained": (
                not cleanup_after_execution
            ),
        }

        output_path = (
            arguments.output
            if arguments.output
            else (
                DEFAULT_RESULTS_DIRECTORY
                / f"{arguments.execution_id}.json"
            )
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with output_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                artifact,
                file,
                indent=2,
            )
            file.write("\n")

        latest_path = (
            DEFAULT_RESULTS_DIRECTORY
            / "latest-execution.json"
        )

        latest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copyfile(
            output_path,
            latest_path,
        )

        if cleanup_after_execution:
            print(
                "Deleting selected runtime namespace..."
            )

            delete_namespace(
                selected_context,
                namespace,
            )

        print()
        print(f"Execution artifact: {output_path}")
        print(
            f"Execution successful: "
            f"{execution_successful}"
        )
        print(
            f"Actual {metric_name}: "
            f"{actual_metric_value:.3f}ms"
        )
        print(
            f"Actual intent satisfied: "
            f"{actual_satisfied}"
        )
        print(
            f"Deployment retained: "
            f"{not cleanup_after_execution}"
        )

        return 0 if execution_successful else 1

    except DeploymentError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
