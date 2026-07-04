#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


CONTROLLER_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = CONTROLLER_DIR / "config"

ALLOWED_METHODS = {"GET", "POST"}
ALLOWED_OPERATORS = {"<", "<=", "==", ">=", ">"}
ALLOWED_METRICS = {
    "response_time_mean_ms",
    "response_time_p50_ms",
    "response_time_p95_ms",
    "response_time_p99_ms",
}


class ValidationError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Invalid JSON in {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain a JSON object")

    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}") from exc
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"Invalid YAML in {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain a YAML mapping")

    return value


def require_positive_integer(
    value: Any,
    field_name: str,
    allow_zero: bool = False,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be an integer"
        )

    minimum = 0 if allow_zero else 1

    if value < minimum:
        raise ValidationError(
            f"{field_name} must be at least {minimum}"
        )

    return value


def validate_function_descriptor(
    descriptor: dict[str, Any],
) -> None:
    if descriptor.get("schema_version") != 1:
        raise ValidationError(
            "function_descriptor.schema_version must be 1"
        )

    for field in (
        "name",
        "service_name",
        "namespace",
        "image",
        "endpoint",
        "method",
    ):
        value = descriptor.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                f"function_descriptor.{field} must be a "
                "non-empty string"
            )

    if not descriptor["endpoint"].startswith("/"):
        raise ValidationError(
            "function_descriptor.endpoint must start with /"
        )

    if descriptor["method"].upper() not in ALLOWED_METHODS:
        raise ValidationError(
            "function_descriptor.method must be GET or POST"
        )

    workload = descriptor.get("workload")

    if not isinstance(workload, dict):
        raise ValidationError(
            "function_descriptor.workload must be an object"
        )

    require_positive_integer(
        workload.get("value"),
        "function_descriptor.workload.value",
        allow_zero=True,
    )

    maximum_value = require_positive_integer(
        workload.get("maximum_value"),
        "function_descriptor.workload.maximum_value",
    )

    if workload["value"] > maximum_value:
        raise ValidationError(
            "workload.value cannot exceed workload.maximum_value"
        )

    scheduling = descriptor.get("scheduling", {})
    selector = scheduling.get("node_selector", {})

    if selector.get("workload") != "serverless":
        raise ValidationError(
            "The function must select workload=serverless"
        )


def validate_intent(
    intent: dict[str, Any],
    descriptor: dict[str, Any],
) -> None:
    if intent.get("schema_version") != 1:
        raise ValidationError(
            "intent.schema_version must be 1"
        )

    if intent.get("function") != descriptor.get("name"):
        raise ValidationError(
            "intent.function must match "
            "function_descriptor.name"
        )

    objective = intent.get("objective")

    if not isinstance(objective, dict):
        raise ValidationError(
            "intent.objective must be an object"
        )

    if objective.get("metric") not in ALLOWED_METRICS:
        raise ValidationError(
            "Unsupported intent objective metric"
        )

    if objective.get("operator") not in ALLOWED_OPERATORS:
        raise ValidationError(
            "Unsupported intent objective operator"
        )

    value = objective.get("value")

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(
            "intent.objective.value must be numeric"
        )

    if value < 0:
        raise ValidationError(
            "intent.objective.value cannot be negative"
        )

    placement = intent.get("placement")

    if not isinstance(placement, dict):
        raise ValidationError(
            "intent.placement must be an object"
        )

    if placement.get("mode") != "single-cluster":
        raise ValidationError(
            "Only single-cluster placement is currently supported"
        )


def is_high_level_intent(
    intent: dict[str, Any],
) -> bool:
    return "function_name" in intent


def validate_high_level_intent(
    intent: dict[str, Any],
    function_catalog: dict[str, Any],
) -> None:
    function_name = intent.get("function_name")

    if not isinstance(function_name, str) or not function_name:
        raise ValidationError(
            "intent.function_name must be a non-empty string"
        )

    functions = function_catalog.get("functions")

    if not isinstance(functions, dict):
        raise ValidationError(
            "function_catalog.functions must be an object"
        )

    function = functions.get(function_name)

    if not isinstance(function, dict):
        available = ", ".join(sorted(functions))

        raise ValidationError(
            f"Unknown function: {function_name}. "
            f"Available functions: {available}"
        )

    if intent.get("objective") != "low_latency":
        raise ValidationError(
            "Only objective=low_latency is currently supported"
        )

    requirements = intent.get("requirements")

    if not isinstance(requirements, dict):
        raise ValidationError(
            "intent.requirements must be an object"
        )

    latency_ms = requirements.get("latency_ms")

    if (
        not isinstance(latency_ms, (int, float))
        or isinstance(latency_ms, bool)
    ):
        raise ValidationError(
            "intent.requirements.latency_ms must be numeric"
        )

    if latency_ms <= 0:
        raise ValidationError(
            "intent.requirements.latency_ms must be greater than zero"
        )

    if intent.get("policy") != "choose_lowest_latency":
        raise ValidationError(
            "Only policy=choose_lowest_latency is currently supported"
        )

    if intent.get("deploy_mode") != "existing":
        raise ValidationError(
            "Only deploy_mode=existing is currently supported"
        )

    if function.get("image_mode") != "existing":
        raise ValidationError(
            f"Function {function_name} is not registered "
            "as an existing image"
        )

    image = function.get("image")

    if not isinstance(image, str) or not image:
        raise ValidationError(
            f"Function {function_name} has no valid image"
        )

    endpoint = function.get("endpoint", "/")

    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ValidationError(
            f"Function {function_name} has an invalid endpoint"
        )

    method = function.get("method", "GET")

    if method not in {"GET", "POST"}:
        raise ValidationError(
            f"Function {function_name} has an unsupported method"
        )

    port = function.get("port", 8080)

    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or port <= 0
    ):
        raise ValidationError(
            f"Function {function_name} has an invalid port"
        )

    response_contract = function.get(
        "response_contract",
        "basic_http",
    )

    if response_contract not in {
        "basic_http",
        "instrumented",
    }:
        raise ValidationError(
            f"Function {function_name} has an unsupported "
            "response contract"
        )


def validate_controller_config(
    controller_config: dict[str, Any],
    infrastructure: dict[str, Any],
) -> None:
    if controller_config.get("schema_version") != 1:
        raise ValidationError(
            "controller_config.schema_version must be 1"
        )

    candidate_clusters = controller_config.get(
        "candidate_clusters"
    )

    if (
        not isinstance(candidate_clusters, list)
        or not candidate_clusters
    ):
        raise ValidationError(
            "candidate_clusters must be a non-empty list"
        )

    if len(candidate_clusters) != len(set(candidate_clusters)):
        raise ValidationError(
            "candidate_clusters contains duplicates"
        )

    configured_clusters = infrastructure.get("clusters", {})

    unknown_clusters = [
        cluster
        for cluster in candidate_clusters
        if cluster not in configured_clusters
    ]

    if unknown_clusters:
        raise ValidationError(
            "Unknown candidate clusters: "
            + ", ".join(unknown_clusters)
        )

    benchmarking = controller_config.get("benchmarking", {})
    feasibility = controller_config.get("feasibility", {})
    deployment = controller_config.get("deployment", {})

    require_positive_integer(
        benchmarking.get("warmup_requests"),
        "benchmarking.warmup_requests",
        allow_zero=True,
    )

    require_positive_integer(
        benchmarking.get("measured_requests"),
        "benchmarking.measured_requests",
    )

    require_positive_integer(
        benchmarking.get("request_timeout_seconds"),
        "benchmarking.request_timeout_seconds",
    )

    require_positive_integer(
        feasibility.get(
            "minimum_available_cpu_millicores"
        ),
        "feasibility.minimum_available_cpu_millicores",
        allow_zero=True,
    )

    require_positive_integer(
        feasibility.get("minimum_available_memory_mb"),
        "feasibility.minimum_available_memory_mb",
        allow_zero=True,
    )

    require_positive_integer(
        feasibility.get("minimum_ready_workers"),
        "feasibility.minimum_ready_workers",
    )

    require_positive_integer(
        deployment.get("timeout_seconds"),
        "deployment.timeout_seconds",
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate controller input files."
    )

    parser.add_argument(
        "--config-directory",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
    )

    parser.add_argument(
        "--intent",
        type=Path,
        help=(
            "Optional intent file. Supports the existing internal "
            "format and the high-level user format."
        ),
    )

    parser.add_argument(
        "--function-catalog",
        type=Path,
        help=(
            "Optional function catalog. Defaults to "
            "<config-directory>/function_catalog.json."
        ),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    config_dir = arguments.config_directory

    try:
        descriptor = load_json(
            config_dir / "function_descriptor.json"
        )

        intent_path = (
            arguments.intent
            if arguments.intent
            else config_dir / "intent.json"
        )

        catalog_path = (
            arguments.function_catalog
            if arguments.function_catalog
            else config_dir / "function_catalog.json"
        )

        intent = load_json(intent_path)

        controller_config = load_json(
            config_dir / "controller_config.json"
        )

        infrastructure = load_yaml(
            config_dir / "clusters.yaml"
        )

        if is_high_level_intent(intent):
            function_catalog = load_json(catalog_path)

            validate_high_level_intent(
                intent,
                function_catalog,
            )

            selected_function = function_catalog[
                "functions"
            ][intent["function_name"]]

            validated_function_name = intent[
                "function_name"
            ]

            validated_image = selected_function[
                "image"
            ]

            validated_intent_summary = (
                "low_latency: "
                "response_time_p95_ms <= "
                f"{intent['requirements']['latency_ms']} ms"
            )
        else:
            validate_function_descriptor(descriptor)
            validate_intent(intent, descriptor)

            validated_function_name = descriptor["name"]
            validated_image = descriptor["image"]

            validated_intent_summary = (
                f"{intent['objective']['metric']} "
                f"{intent['objective']['operator']} "
                f"{intent['objective']['value']}"
            )

        validate_controller_config(
            controller_config,
            infrastructure,
        )

    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Controller input validation passed.")
    print(f"Function: {validated_function_name}")
    print(f"Image: {validated_image}")
    print(f"Intent: {validated_intent_summary}")
    print(
        "Candidate clusters: "
        + ", ".join(
            controller_config["candidate_clusters"]
        )
    )
    print(
        "Benchmark requests: "
        f"{controller_config['benchmarking']['warmup_requests']} "
        "warmup + "
        f"{controller_config['benchmarking']['measured_requests']} "
        "measured"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
