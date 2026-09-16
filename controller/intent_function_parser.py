from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

import yaml

from controller.intent_translation import (
    IntentSemanticError,
    NormalizedIntent,
    translate_intent,
)
from controller.models import (
    FunctionDescriptor,
    Intent,
    IntentFunction,
    IntentProperties,
    LocationConstraint,
    Objective,
    TargetRef,
)


SUPPORTED_API_VERSIONS = {"intent.elif.dev/v1"}
SUPPORTED_RUNTIMES = {"knative"}
SUPPORTED_TARGET_KINDS = {"KnativeService"}
_DNS_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$"
)
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IMAGE_REFERENCE_PATTERN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?"
    r"(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"(?:@sha256:[a-fA-F0-9]{64})?$"
)


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}

    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)

        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error

        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate field {key!r}",
                key_node.start_mark,
            )

        mapping[key] = loader.construct_object(
            value_node,
            deep=deep,
        )

    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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


def _require_dns_label(
    value: Any,
    field_name: str,
) -> str:
    name = _require_string(value, field_name)

    if len(name) > 63 or _DNS_LABEL_PATTERN.fullmatch(name) is None:
        raise IntentFunctionParseError(
            f"{field_name} must be a lowercase DNS label of at most "
            "63 characters"
        )

    return name


def _require_dns_subdomain(
    value: Any,
    field_name: str,
) -> str:
    name = _require_string(value, field_name)

    if (
        len(name) > 253
        or any(
            not part
            or len(part) > 63
            or _DNS_LABEL_PATTERN.fullmatch(part) is None
            for part in name.split(".")
        )
    ):
        raise IntentFunctionParseError(
            f"{field_name} must be a lowercase DNS subdomain of at most "
            "253 characters"
        )

    return name


def _require_version(
    value: Any,
    field_name: str,
) -> str:
    version = _require_string(value, field_name)

    if len(version) > 128 or _VERSION_PATTERN.fullmatch(version) is None:
        raise IntentFunctionParseError(
            f"{field_name} must contain only letters, numbers, '.', '_', "
            "or '-' and be at most 128 characters"
        )

    return version


def _require_namespaced_name(
    value: Any,
    field_name: str,
) -> str:
    reference = _require_string(value, field_name)
    parts = reference.split("/")

    if len(parts) != 2:
        raise IntentFunctionParseError(
            f"{field_name} must use the form namespace/name"
        )

    _require_dns_label(parts[0], f"{field_name}.namespace")
    _require_dns_label(parts[1], f"{field_name}.name")
    return reference


def _require_image_reference(
    value: Any,
    field_name: str,
) -> str:
    reference = _require_string(value, field_name)

    if (
        len(reference) > 255
        or _IMAGE_REFERENCE_PATTERN.fullmatch(reference) is None
    ):
        raise IntentFunctionParseError(
            f"{field_name} must be a valid container image reference "
            "of at most 255 characters"
        )

    return reference


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

    if not value.strip():
        raise IntentFunctionParseError(
            f"{field_name} must not be empty"
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


def _require_string_list(
    value: Any,
    field_name: str,
) -> tuple[str, ...]:
    items = _require_list(value, field_name)
    return tuple(
        _require_string(item, f"{field_name}[{index}]")
        for index, item in enumerate(items)
    )


def _require_exact_fields(
    payload: Mapping[str, Any],
    expected: set[str],
    optional: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(payload) - expected - optional)
    missing = sorted(expected - set(payload))

    if unknown:
        raise IntentFunctionParseError(
            f"{field_name} contains unsupported fields: "
            f"{', '.join(unknown)}"
        )

    if missing:
        raise IntentFunctionParseError(
            f"{field_name} is missing fields: {', '.join(missing)}"
        )


def _parse_requirement(
    payload: Any,
    field_name: str,
    *,
    default_enforcement: str,
) -> Objective:
    requirement = _require_mapping(payload, field_name)
    _require_exact_fields(
        requirement,
        {"name", "operator", "value", "measuredBy"},
        {"description", "unit", "weight", "enforcement"},
        field_name,
    )

    try:
        return Objective(
            name=_require_dns_label(
                requirement["name"],
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
            enforcement=_require_string(
                requirement.get(
                    "enforcement",
                    default_enforcement,
                ),
                f"{field_name}.enforcement",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"{field_name} is invalid: {error}"
        ) from error


def _parse_constraint(
    payload: Any,
    field_name: str,
) -> Objective | LocationConstraint:
    constraint = _require_mapping(payload, field_name)
    constraint_type = constraint.get("type")

    if constraint_type is None:
        return _parse_requirement(
            constraint,
            field_name,
            default_enforcement="hard",
        )

    constraint_type = _require_string(
        constraint_type,
        f"{field_name}.type",
    )

    if constraint_type != "location":
        raise IntentFunctionParseError(
            f"{field_name}.type {constraint_type!r} is unsupported; "
            "expected 'location'"
        )

    _require_exact_fields(
        constraint,
        {"type", "name", "target", "operator", "values"},
        {"description", "enforcement", "priority"},
        field_name,
    )

    try:
        return LocationConstraint(
            name=_require_dns_label(
                constraint["name"],
                f"{field_name}.name",
            ),
            target=_require_string(
                constraint["target"],
                f"{field_name}.target",
            ),
            operator=_require_string(
                constraint["operator"],
                f"{field_name}.operator",
            ),
            values=_require_string_list(
                constraint["values"],
                f"{field_name}.values",
            ),
            description=_optional_string(
                constraint.get("description"),
                f"{field_name}.description",
            ),
            enforcement=_require_string(
                constraint.get("enforcement", "hard"),
                f"{field_name}.enforcement",
            ),
            priority=_require_number(
                constraint.get("priority", 1.0),
                f"{field_name}.priority",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"{field_name} is invalid: {error}"
        ) from error


def _parse_intent_function_payload(
    raw_payload: str | bytes,
) -> IntentFunction:
    try:
        payload = yaml.load(
            raw_payload,
            Loader=_StrictSafeLoader,
        )
    except (UnicodeError, yaml.YAMLError) as error:
        raise IntentFunctionParseError(
            f"Invalid YAML payload: {error}"
        ) from error

    payload = _require_mapping(payload, "payload")
    _require_exact_fields(
        payload,
        {"apiVersion", "kind", "metadata", "spec"},
        set(),
        "payload",
    )

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

    if kind != "IntentFunction":
        raise IntentFunctionParseError(
            f"Unsupported kind {kind!r}; expected 'IntentFunction'"
        )

    metadata = _require_mapping(
        _require_field(payload, "metadata", "metadata"),
        "metadata",
    )
    spec = _require_mapping(
        _require_field(payload, "spec", "spec"),
        "spec",
    )
    _require_exact_fields(
        metadata,
        {"name"},
        set(),
        "metadata",
    )
    _require_exact_fields(
        spec,
        {"function", "intent"},
        set(),
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
    _require_exact_fields(
        function_payload,
        {"name", "version", "image"},
        {"namespace", "serviceName", "runtime"},
        "spec.function",
    )
    _require_exact_fields(
        intent_payload,
        {"targetRef", "objectives"},
        {"constraints", "properties"},
        "spec.intent",
    )

    function_name = _require_dns_label(
        function_payload["name"],
        "spec.function.name",
    )

    try:
        function = FunctionDescriptor(
            name=function_name,
            namespace=_require_dns_label(
                function_payload.get("namespace", "default"),
                "spec.function.namespace",
            ),
            service_name=_require_dns_label(
                function_payload.get("serviceName", function_name),
                "spec.function.serviceName",
            ),
            version=_require_version(
                function_payload["version"],
                "spec.function.version",
            ),
            runtime=_require_string(
                function_payload.get("runtime", "knative"),
                "spec.function.runtime",
            ),
            image=_require_image_reference(
                function_payload["image"],
                "spec.function.image",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"spec.function is invalid: {error}"
        ) from error

    if function.runtime not in SUPPORTED_RUNTIMES:
        supported = ", ".join(sorted(SUPPORTED_RUNTIMES))
        raise IntentFunctionParseError(
            f"Unsupported spec.function.runtime {function.runtime!r}; "
            f"expected one of {supported}"
        )

    target_ref_payload = _require_mapping(
        _require_field(
            intent_payload,
            "targetRef",
            "spec.intent.targetRef",
        ),
        "spec.intent.targetRef",
    )
    _require_exact_fields(
        target_ref_payload,
        {"kind", "name"},
        set(),
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
            name=_require_namespaced_name(
                target_ref_payload["name"],
                "spec.intent.targetRef.name",
            ),
        )
    except ValueError as error:
        raise IntentFunctionParseError(
            f"spec.intent.targetRef is invalid: {error}"
        ) from error

    if target_ref.kind not in SUPPORTED_TARGET_KINDS:
        supported = ", ".join(sorted(SUPPORTED_TARGET_KINDS))
        raise IntentFunctionParseError(
            f"Unsupported spec.intent.targetRef.kind "
            f"{target_ref.kind!r}; expected one of {supported}"
        )

    expected_target_name = (
        f"{function.namespace}/{function.service_name}"
    )

    if target_ref.name != expected_target_name:
        raise IntentFunctionParseError(
            "spec.intent.targetRef.name must identify the submitted "
            f"service {expected_target_name!r}"
        )

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
            default_enforcement="soft",
        )
        for index, item in enumerate(objectives_payload)
    ]

    constraints_payload = _require_list(
        intent_payload.get("constraints", []),
        "spec.intent.constraints",
    )
    constraints = [
        _parse_constraint(
            item,
            f"spec.intent.constraints[{index}]",
        )
        for index, item in enumerate(constraints_payload)
    ]
    requirement_names = [
        requirement.name
        for requirement in [*objectives, *constraints]
    ]
    duplicate_requirement_names = sorted(
        {
            name
            for name in requirement_names
            if requirement_names.count(name) > 1
        }
    )

    if duplicate_requirement_names:
        raise IntentFunctionParseError(
            "spec.intent requirement names must be unique; duplicates: "
            f"{', '.join(duplicate_requirement_names)}"
        )

    properties_payload = _require_mapping(
        intent_payload.get("properties", {}),
        "spec.intent.properties",
    )

    supported_properties = {
        "minScale",
        "maxScale",
        "containerPort",
    }
    _require_exact_fields(
        properties_payload,
        set(),
        supported_properties,
        "spec.intent.properties",
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

        submission = IntentFunction(
            api_version=api_version,
            kind=kind,
            name=_require_dns_subdomain(
                metadata["name"],
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

    return submission


def parse_and_translate_intent_function_payload(
    raw_payload: str | bytes,
) -> tuple[IntentFunction, NormalizedIntent]:
    submission = _parse_intent_function_payload(raw_payload)

    try:
        normalized_intent = translate_intent(submission)
    except IntentSemanticError as error:
        raise IntentFunctionParseError(
            f"IntentFunction semantics are invalid: {error}"
        ) from error

    return submission, normalized_intent


def parse_intent_function_payload(
    raw_payload: str | bytes,
    *,
    validate_semantics: bool = True,
) -> IntentFunction:
    if not validate_semantics:
        return _parse_intent_function_payload(raw_payload)

    submission, _ = parse_and_translate_intent_function_payload(raw_payload)
    return submission
