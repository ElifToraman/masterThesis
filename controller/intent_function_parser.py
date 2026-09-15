from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import yaml

from controller.models import (
    FunctionDescriptor,
    Intent,
    IntentFunction,
    IntentProperties,
    Objective,
    TargetRef,
)


SUPPORTED_API_VERSIONS = {"intent.elif.dev/v1"}


class IntentFunctionParseError(RuntimeError):
    """A user-facing error caused by an invalid IntentFunction document."""


def _require_mapping(
    value: Any,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentFunctionParseError(
            f"{field_name} must be a mapping"
        )

    if any(not isinstance(key, str) for key in value):
        raise IntentFunctionParseError(
            f"{field_name} field names must be strings"
        )

    return dict(value)


def _require_list(
    value: Any,
    field_name: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise IntentFunctionParseError(
            f"{field_name} must be a list"
        )

    return value


def _require_field(
    payload: Mapping[str, Any],
    key: str,
    field_name: str,
) -> Any:
    if key not in payload:
        raise IntentFunctionParseError(
            f"{field_name} is required"
        )

    return payload[key]


def _require_string(
    value: Any,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise IntentFunctionParseError(
            f"{field_name} must be a string"
        )

    if not value.strip():
        raise IntentFunctionParseError(
            f"{field_name} must not be empty"
        )

    return value


def _optional_string(
    value: Any,
    field_name: str,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise IntentFunctionParseError(
            f"{field_name} must be a string"
        )

    return value


def _require_number(
    value: Any,
    field_name: str,
) -> float:
    # bool is a subclass of int in Python, but true/false are not meaningful
    # numeric intent targets.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntentFunctionParseError(
            f"{field_name} must be a number"
        )

    try:
        number = float(value)
    except OverflowError as error:
        raise IntentFunctionParseError(
            f"{field_name} must be finite"
        ) from error

    if not math.isfinite(number):
        raise IntentFunctionParseError(
            f"{field_name} must be finite"
        )

    return number


def _require_integer(
    value: Any,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntentFunctionParseError(
            f"{field_name} must be an integer"
        )

    return value


def _parse_requirement(
    payload: Any,
    field_name: str,
) -> Objective:
    requirement = _require_mapping(payload, field_name)

    try:
        return Objective(
            name=_require_string(
                _require_field(requirement, "name", f"{field_name}.name"),
                f"{field_name}.name",
            ),
            description=_optional_string(
                requirement.get("description"),
                f"{field_name}.description",
            ),
            operator=_require_string(
                _require_field(
                    requirement,
                    "operator",
                    f"{field_name}.operator",
                ),
                f"{field_name}.operator",
            ),
            value=_require_number(
                _require_field(requirement, "value", f"{field_name}.value"),
                f"{field_name}.value",
            ),
            unit=_optional_string(
                requirement.get("unit"),
                f"{field_name}.unit",
            ),
            measured_by=_require_string(
                _require_field(
                    requirement,
                    "measuredBy",
                    f"{field_name}.measuredBy",
                ),
                f"{field_name}.measuredBy",
            ),
            weight=_require_number(
                requirement.get("weight", 1.0),
                f"{field_name}.weight",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"{field_name} is invalid: {error}"
        ) from error


def parse_intent_function_payload(
    raw_payload: str | bytes,
) -> IntentFunction:
    try:
        payload = yaml.safe_load(raw_payload)
    except (UnicodeError, yaml.YAMLError) as error:
        raise IntentFunctionParseError(
            f"Invalid YAML payload: {error}"
        ) from error

    payload = _require_mapping(payload, "payload")

    api_version = _require_string(
        _require_field(payload, "apiVersion", "apiVersion"),
        "apiVersion",
    )

    if api_version not in SUPPORTED_API_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_API_VERSIONS))
        raise IntentFunctionParseError(
            f"Unsupported apiVersion {api_version!r}; expected {supported}"
        )

    kind = _require_string(
        _require_field(payload, "kind", "kind"),
        "kind",
    )

    metadata = _require_mapping(
        _require_field(payload, "metadata", "metadata"),
        "metadata",
    )
    spec = _require_mapping(
        _require_field(payload, "spec", "spec"),
        "spec",
    )
    function_payload = _require_mapping(
        _require_field(spec, "function", "spec.function"),
        "spec.function",
    )
    intent_payload = _require_mapping(
        _require_field(spec, "intent", "spec.intent"),
        "spec.intent",
    )

    function_name = _require_string(
        _require_field(function_payload, "name", "spec.function.name"),
        "spec.function.name",
    )

    try:
        function = FunctionDescriptor(
            name=function_name,
            namespace=_require_string(
                function_payload.get("namespace", "default"),
                "spec.function.namespace",
            ),
            service_name=_require_string(
                function_payload.get("serviceName", function_name),
                "spec.function.serviceName",
            ),
            version=_require_string(
                _require_field(
                    function_payload,
                    "version",
                    "spec.function.version",
                ),
                "spec.function.version",
            ),
            runtime=_require_string(
                function_payload.get("runtime", "knative"),
                "spec.function.runtime",
            ),
            image=_require_string(
                _require_field(
                    function_payload,
                    "image",
                    "spec.function.image",
                ),
                "spec.function.image",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"spec.function is invalid: {error}"
        ) from error

    target_ref_payload = _require_mapping(
        _require_field(
            intent_payload,
            "targetRef",
            "spec.intent.targetRef",
        ),
        "spec.intent.targetRef",
    )

    try:
        target_ref = TargetRef(
            kind=_require_string(
                _require_field(
                    target_ref_payload,
                    "kind",
                    "spec.intent.targetRef.kind",
                ),
                "spec.intent.targetRef.kind",
            ),
            name=_require_string(
                _require_field(
                    target_ref_payload,
                    "name",
                    "spec.intent.targetRef.name",
                ),
                "spec.intent.targetRef.name",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"spec.intent.targetRef is invalid: {error}"
        ) from error

    objectives_payload = _require_list(
        _require_field(
            intent_payload,
            "objectives",
            "spec.intent.objectives",
        ),
        "spec.intent.objectives",
    )

    if not objectives_payload:
        raise IntentFunctionParseError(
            "spec.intent.objectives must contain at least one item"
        )

    objectives = [
        _parse_requirement(
            item,
            f"spec.intent.objectives[{index}]",
        )
        for index, item in enumerate(objectives_payload)
    ]

    constraints_payload = _require_list(
        intent_payload.get("constraints", []),
        "spec.intent.constraints",
    )
    constraints = [
        _parse_requirement(
            item,
            f"spec.intent.constraints[{index}]",
        )
        for index, item in enumerate(constraints_payload)
    ]
    properties_payload = _require_mapping(
        intent_payload.get("properties", {}),
        "spec.intent.properties",
    )

    supported_properties = {
        "minScale",
        "maxScale",
        "containerPort",
    }
    unknown_properties = sorted(
        set(properties_payload) - supported_properties
    )

    if unknown_properties:
        names = ", ".join(unknown_properties)
        raise IntentFunctionParseError(
            f"spec.intent.properties contains unsupported fields: {names}"
        )

    try:
        properties = IntentProperties(
            min_scale=(
                _require_integer(
                    properties_payload["minScale"],
                    "spec.intent.properties.minScale",
                )
                if "minScale" in properties_payload
                else None
            ),
            max_scale=(
                _require_integer(
                    properties_payload["maxScale"],
                    "spec.intent.properties.maxScale",
                )
                if "maxScale" in properties_payload
                else None
            ),
            container_port=(
                _require_integer(
                    properties_payload["containerPort"],
                    "spec.intent.properties.containerPort",
                )
                if "containerPort" in properties_payload
                else 8080
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"spec.intent.properties is invalid: {error}"
        ) from error

    try:
        intent = Intent(
            target_ref=target_ref,
            objectives=objectives,
            constraints=constraints,
            properties=properties,
        )

        return IntentFunction(
            api_version=api_version,
            kind=kind,
            name=_require_string(
                _require_field(metadata, "name", "metadata.name"),
                "metadata.name",
            ),
            metadata=metadata,
            function=function,
            intent=intent,
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"IntentFunction is invalid: {error}"
        ) from error
