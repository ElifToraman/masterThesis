from __future__ import annotations

from typing import Any

import yaml

from controller.models import (
    FunctionDescriptor,
    Intent,
    IntentFunction,
    Objective,
    TargetRef,
)


class IntentFunctionParseError(RuntimeError):
    pass


def _require_mapping(
    value: Any,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntentFunctionParseError(
            f"{field_name} must be a mapping"
        )

    return value


def _require_list(
    value: Any,
    field_name: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise IntentFunctionParseError(
            f"{field_name} must be a list"
        )

    return value


def parse_intent_function_payload(
    raw_payload: str | bytes,
) -> IntentFunction:
    try:
        payload = yaml.safe_load(raw_payload)
    except yaml.YAMLError as error:
        raise IntentFunctionParseError(
            f"Invalid YAML payload: {error}"
        ) from error

    payload = _require_mapping(
        payload,
        "payload",
    )

    metadata = _require_mapping(
        payload.get("metadata", {}),
        "metadata",
    )

    spec = _require_mapping(
        payload.get("spec", {}),
        "spec",
    )

    function_payload = _require_mapping(
        spec.get("function", {}),
        "spec.function",
    )

    intent_payload = _require_mapping(
        spec.get("intent", {}),
        "spec.intent",
    )


    function = FunctionDescriptor(
        name=str(function_payload["name"]),
        namespace=str(function_payload.get("namespace", "default")),
        service_name=str(
            function_payload.get(
                "serviceName",
                function_payload["name"],
            )
        ),
        version=str(function_payload["version"]),
        runtime=str(function_payload.get("runtime", "knative")),
        image=str(function_payload["image"]),
    )
    
    target_ref_payload = _require_mapping(
        intent_payload.get("targetRef", {}),
        "spec.intent.targetRef",
    )

    target_ref = TargetRef(
        kind=str(target_ref_payload["kind"]),
        name=str(target_ref_payload["name"]),
    )

    objectives_payload = _require_list(
        intent_payload.get("objectives", []),
        "spec.intent.objectives",
    )

    objectives: list[Objective] = []

    for index, item in enumerate(objectives_payload):
        objective_payload = _require_mapping(
            item,
            f"spec.intent.objectives[{index}]",
        )

        objectives.append(
            Objective(
                name=str(objective_payload["name"]),
                description=objective_payload.get("description"),
                operator=str(objective_payload["operator"]),
                value=float(objective_payload["value"]),
                unit=objective_payload.get("unit"),
                measured_by=str(objective_payload["measuredBy"]),
                weight=float(objective_payload.get("weight", 1.0)),
            )
        )

    intent = Intent(
        target_ref=target_ref,
        objectives=objectives,
        properties=dict(intent_payload.get("properties", {})),
    )

    return IntentFunction(
        api_version=str(payload["apiVersion"]),
        kind=str(payload["kind"]),
        name=str(metadata["name"]),
        metadata=metadata,
        function=function,
        intent=intent,
    )