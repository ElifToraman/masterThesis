from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml

from controller.monitoring.models import VMConfig
from controller.monitoring.vm import VM


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CLUSTER_CONFIG_FILE = (
    CONTROLLER_DIRECTORY / "config" / "clusters.yaml"
)
DEFAULT_SUBMISSION_FILE = (
    CONTROLLER_DIRECTORY
    / "examples"
    / "hello-intent-function.yaml"
)
DEFAULT_POLICY_CONFIG_FILE = (
    CONTROLLER_DIRECTORY / "config" / "policy.json"
)
DEFAULT_RUNTIME_CONFIG_FILE = (
    CONTROLLER_DIRECTORY / "config" / "runtime.yaml"
)


class RuntimeConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClusterRuntimeConfig:
    name: str
    kubernetes_context: str
    host: str
    ssh_user: str
    ssh_key: Path
    prometheus_url: str
    image_registry: str

    def create_vm(self) -> VM:
        return VM(
            VMConfig(
                name=self.name,
                host=self.host,
                ssh_user=self.ssh_user,
                ssh_key=self.ssh_key,
                prometheus_url=self.prometheus_url,
            )
        )


def load_cluster_configs(
    config_file: Path = DEFAULT_CLUSTER_CONFIG_FILE,
) -> dict[str, ClusterRuntimeConfig]:
    config_file = config_file.expanduser().resolve()

    if not config_file.is_file():
        raise RuntimeConfigError(
            f"Cluster configuration does not exist: {config_file}"
        )

    try:
        payload = yaml.safe_load(
            config_file.read_text(encoding="utf-8")
        )
    except yaml.YAMLError as error:
        raise RuntimeConfigError(
            f"Invalid cluster configuration YAML: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeConfigError(
            "Cluster configuration must be a mapping"
        )

    raw_clusters = payload.get("clusters")

    if not isinstance(raw_clusters, list) or not raw_clusters:
        raise RuntimeConfigError(
            "Cluster configuration must contain a non-empty "
            "'clusters' list"
        )

    clusters: dict[str, ClusterRuntimeConfig] = {}

    for index, item in enumerate(raw_clusters):
        if not isinstance(item, dict):
            raise RuntimeConfigError(
                f"clusters[{index}] must be a mapping"
            )

        cluster = _parse_cluster(item, index)

        if cluster.name in clusters:
            raise RuntimeConfigError(
                f"Duplicate cluster name: {cluster.name}"
            )

        clusters[cluster.name] = cluster

    return clusters


def load_submission(
    submission_file: Path,
):
    from controller.intent_function_parser import (
        parse_intent_function_payload,
    )

    submission_file = submission_file.expanduser().resolve()

    if not submission_file.is_file():
        raise RuntimeConfigError(
            f"Submission file does not exist: {submission_file}"
        )

    return parse_intent_function_payload(
        submission_file.read_text(encoding="utf-8")
    )


def load_policy_config(
    config_file: Path = DEFAULT_POLICY_CONFIG_FILE,
) -> dict[str, Any]:
    config_file = config_file.expanduser().resolve()

    if not config_file.is_file():
        raise RuntimeConfigError(
            f"Policy configuration does not exist: {config_file}"
        )

    try:
        payload = json.loads(
            config_file.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise RuntimeConfigError(
            f"Invalid policy configuration JSON: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeConfigError(
            "Policy configuration must be a mapping"
        )

    return payload


def load_runtime_config(
    config_file: Path = DEFAULT_RUNTIME_CONFIG_FILE,
) -> dict[str, dict[str, Any]]:
    config_file = config_file.expanduser().resolve()

    if not config_file.is_file():
        raise RuntimeConfigError(
            f"Runtime configuration does not exist: {config_file}"
        )

    try:
        payload = yaml.safe_load(
            config_file.read_text(encoding="utf-8")
        )
    except yaml.YAMLError as error:
        raise RuntimeConfigError(
            f"Invalid runtime configuration YAML: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeConfigError(
            "Runtime configuration must be a mapping"
        )

    benchmark = _require_section(payload, "benchmark")
    validation = _require_section(payload, "validation")
    monitoring = _require_section(
        payload,
        "postDeploymentMonitoring",
    )

    _require_integer(
        benchmark,
        "warmupRequests",
        minimum=0,
        section="benchmark",
    )
    _require_integer(
        benchmark,
        "concurrency",
        minimum=1,
        section="benchmark",
    )
    _require_number(
        benchmark,
        "durationSeconds",
        minimum_exclusive=0,
        section="benchmark",
    )
    _require_number(
        benchmark,
        "resourceSampleIntervalSeconds",
        minimum_exclusive=0,
        section="benchmark",
    )
    _require_number(
        benchmark,
        "requestTimeoutSeconds",
        minimum_exclusive=0,
        section="benchmark",
    )
    _require_number(
        benchmark,
        "deploymentTimeoutSeconds",
        minimum_exclusive=0,
        section="benchmark",
    )

    _require_integer(
        validation,
        "maximumAttempts",
        minimum=1,
        section="validation",
    )
    _require_number(
        validation,
        "timeoutSeconds",
        minimum_exclusive=0,
        section="validation",
    )
    _require_number(
        validation,
        "retryIntervalSeconds",
        minimum=0,
        section="validation",
    )

    window_size = _require_integer(
        monitoring,
        "windowSize",
        minimum=1,
        section="postDeploymentMonitoring",
    )
    minimum_samples = _require_integer(
        monitoring,
        "minimumSamples",
        minimum=1,
        section="postDeploymentMonitoring",
    )
    _require_number(
        monitoring,
        "intervalSeconds",
        minimum_exclusive=0,
        section="postDeploymentMonitoring",
    )
    _require_number(
        monitoring,
        "requestTimeoutSeconds",
        minimum_exclusive=0,
        section="postDeploymentMonitoring",
    )

    if minimum_samples > window_size:
        raise RuntimeConfigError(
            "postDeploymentMonitoring.minimumSamples "
            "must not exceed "
            "postDeploymentMonitoring.windowSize"
        )

    return {
        "benchmark": benchmark,
        "validation": validation,
        "postDeploymentMonitoring": monitoring,
    }


def _parse_cluster(
    item: dict[str, Any],
    index: int,
) -> ClusterRuntimeConfig:
    prefix = f"clusters[{index}]"

    def required(name: str) -> str:
        value = item.get(name)

        if not isinstance(value, str) or not value.strip():
            raise RuntimeConfigError(
                f"{prefix}.{name} must be a non-empty string"
            )

        return value.strip()

    return ClusterRuntimeConfig(
        name=required("name"),
        kubernetes_context=required("kubernetesContext"),
        host=required("host"),
        ssh_user=required("sshUser"),
        ssh_key=Path(required("sshKey")).expanduser(),
        prometheus_url=required("prometheusUrl"),
        image_registry=required("imageRegistry"),
    )


def _require_section(
    payload: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    value = payload.get(name)

    if not isinstance(value, dict):
        raise RuntimeConfigError(
            f"Runtime configuration section {name!r} "
            "must be a mapping"
        )

    return value


def _require_integer(
    section_payload: dict[str, Any],
    name: str,
    *,
    section: str,
    minimum: int,
) -> int:
    value = section_payload.get(name)

    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeConfigError(
            f"{section}.{name} must be an integer"
        )

    if value < minimum:
        raise RuntimeConfigError(
            f"{section}.{name} must be at least {minimum}"
        )

    return value


def _require_number(
    section_payload: dict[str, Any],
    name: str,
    *,
    section: str,
    minimum: float | None = None,
    minimum_exclusive: float | None = None,
) -> float:
    value = section_payload.get(name)

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise RuntimeConfigError(
            f"{section}.{name} must be a number"
        )

    numeric_value = float(value)

    if minimum is not None and numeric_value < minimum:
        raise RuntimeConfigError(
            f"{section}.{name} must be at least {minimum}"
        )

    if (
        minimum_exclusive is not None
        and numeric_value <= minimum_exclusive
    ):
        raise RuntimeConfigError(
            f"{section}.{name} must be greater than "
            f"{minimum_exclusive}"
        )

    return numeric_value
