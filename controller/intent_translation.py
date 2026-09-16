from __future__ import annotations

import json
import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, cast

from controller.models import (
    Enforcement,
    IntentFunction,
    LocationConstraint,
    LocationOperator,
    Objective,
)


EvaluationPhase = Literal["placement", "runtime"]
NORMALIZED_INTENT_SCHEMA_VERSION = 2
SUPPORTED_NORMALIZED_INTENT_SCHEMA_VERSIONS = {1, 2}


class IntentSemanticError(ValueError):
    """An intent is structurally valid but has unsupported semantics."""


@dataclass(frozen=True)
class MeasurementSource:
    field: str
    canonical_factor: float


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    supported_statistics: frozenset[str]
    canonical_unit: str
    unit_factors: Mapping[str, float]
    allowed_operators: frozenset[str]
    phases: frozenset[EvaluationPhase]
    placement_fields: Mapping[str, MeasurementSource]
    runtime_fields: Mapping[str, MeasurementSource]


@dataclass(frozen=True)
class MeasurementBinding:
    metric_id: str
    statistic: str


@dataclass(frozen=True)
class NormalizedRequirement:
    requirement_id: str
    metric_id: str
    statistic: str
    operator: str
    canonical_value: float
    canonical_unit: str
    enforcement: Enforcement
    phases: frozenset[EvaluationPhase]
    priority: float
    placement_source: MeasurementSource | None
    runtime_source: MeasurementSource | None
    source_measured_by: str


@dataclass(frozen=True)
class NormalizedLocationConstraint:
    constraint_id: str
    target: str
    operator: LocationOperator
    values: tuple[str, ...]
    enforcement: Enforcement
    priority: float
    phases: frozenset[EvaluationPhase]


NormalizedConstraint = NormalizedRequirement | NormalizedLocationConstraint


@dataclass(frozen=True)
class NormalizedIntent:
    objectives: tuple[NormalizedRequirement, ...]
    constraints: tuple[NormalizedConstraint, ...]


_LATENCY_UNITS = MappingProxyType(
    {
        "s": 1.0,
        "second": 1.0,
        "seconds": 1.0,
        "ms": 0.001,
        "millisecond": 0.001,
        "milliseconds": 0.001,
    }
)


METRIC_REGISTRY: Mapping[str, MetricDefinition] = MappingProxyType(
    {
        "application.latency": MetricDefinition(
            metric_id="application.latency",
            supported_statistics=frozenset({"p95"}),
            canonical_unit="seconds",
            unit_factors=_LATENCY_UNITS,
            allowed_operators=frozenset({"<", "<=", "==", ">=", ">"}),
            phases=frozenset({"placement", "runtime"}),
            placement_fields=MappingProxyType(
                {
                    "p95": MeasurementSource(
                        field="benchmark_p95_latency_ms",
                        canonical_factor=0.001,
                    )
                }
            ),
            runtime_fields=MappingProxyType(
                {
                    "p95": MeasurementSource(
                        field="p95_latency_ms",
                        canonical_factor=0.001,
                    )
                }
            ),
        ),
    }
)


# Version 1 submissions use measuredBy. Every accepted value is an exact alias
# for a canonical metric and statistic; human-readable text is not consulted.
MEASUREMENT_BINDINGS: Mapping[str, MeasurementBinding] = MappingProxyType(
    {
        "benchmark/hello/p95_warm_latency_ms": MeasurementBinding(
            metric_id="application.latency",
            statistic="p95",
        ),
    }
)


def translate_intent(submission: IntentFunction) -> NormalizedIntent:
    return NormalizedIntent(
        objectives=tuple(
            translate_requirement(requirement)
            for requirement in submission.intent.objectives
        ),
        constraints=tuple(
            translate_constraint(constraint, submission)
            for constraint in submission.intent.constraints
        ),
    )


def translate_constraint(
    constraint: Objective | LocationConstraint,
    submission: IntentFunction,
) -> NormalizedConstraint:
    if isinstance(constraint, Objective):
        return translate_requirement(constraint)

    supported_targets = {
        submission.function.name,
        submission.function.service_name,
        f"{submission.function.namespace}/{submission.function.name}",
        (
            f"{submission.function.namespace}/"
            f"{submission.function.service_name}"
        ),
        submission.intent.target_ref.name,
    }

    if constraint.target not in supported_targets:
        supported = ", ".join(sorted(supported_targets))
        raise IntentSemanticError(
            f"{constraint.name}: target {constraint.target!r} does not "
            f"identify the submitted function; expected one of {supported}"
        )

    return NormalizedLocationConstraint(
        constraint_id=constraint.name,
        target=(
            f"{submission.function.namespace}/"
            f"{submission.function.service_name}"
        ),
        operator=constraint.operator,
        values=constraint.values,
        enforcement=constraint.enforcement,
        priority=constraint.priority,
        phases=frozenset({"placement", "runtime"}),
    )


def translate_requirement(
    requirement: Objective,
) -> NormalizedRequirement:
    binding = MEASUREMENT_BINDINGS.get(requirement.measured_by)

    if binding is None:
        supported = ", ".join(sorted(MEASUREMENT_BINDINGS))
        raise IntentSemanticError(
            f"{requirement.name}: unsupported measuredBy "
            f"{requirement.measured_by!r}; supported values: {supported}"
        )

    definition = METRIC_REGISTRY.get(binding.metric_id)

    if definition is None:
        raise IntentSemanticError(
            f"{requirement.name}: metric binding references unknown metric "
            f"{binding.metric_id!r}"
        )

    if binding.statistic not in definition.supported_statistics:
        raise IntentSemanticError(
            f"{requirement.name}: statistic {binding.statistic!r} is not "
            f"supported for {definition.metric_id}"
        )

    if requirement.operator not in definition.allowed_operators:
        allowed = ", ".join(sorted(definition.allowed_operators))
        raise IntentSemanticError(
            f"{requirement.name}: operator {requirement.operator!r} is not "
            f"supported for {definition.metric_id}; expected one of {allowed}"
        )

    if requirement.unit is None:
        raise IntentSemanticError(
            f"{requirement.name}: unit is required for "
            f"{definition.metric_id}"
        )

    normalized_unit = requirement.unit.strip().lower()
    unit_factor = definition.unit_factors.get(normalized_unit)

    if unit_factor is None:
        allowed_units = ", ".join(sorted(definition.unit_factors))
        raise IntentSemanticError(
            f"{requirement.name}: unit {requirement.unit!r} is not supported "
            f"for {definition.metric_id}; expected one of {allowed_units}"
        )

    return NormalizedRequirement(
        requirement_id=requirement.name,
        metric_id=definition.metric_id,
        statistic=binding.statistic,
        operator=requirement.operator,
        canonical_value=requirement.value * unit_factor,
        canonical_unit=definition.canonical_unit,
        enforcement=requirement.enforcement,
        phases=definition.phases,
        priority=requirement.weight,
        placement_source=definition.placement_fields.get(binding.statistic),
        runtime_source=definition.runtime_fields.get(binding.statistic),
        source_measured_by=requirement.measured_by,
    )


def validate_location_constraint_clusters(
    intent: NormalizedIntent,
    cluster_names: set[str],
) -> None:
    for constraint in intent.constraints:
        if not isinstance(constraint, NormalizedLocationConstraint):
            continue

        unknown = sorted(set(constraint.values) - cluster_names)

        if unknown:
            raise IntentSemanticError(
                f"{constraint.constraint_id}: unknown cluster names: "
                f"{', '.join(unknown)}"
            )


def normalized_intent_to_dict(
    intent: NormalizedIntent,
    *,
    source_sha256: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": NORMALIZED_INTENT_SCHEMA_VERSION,
        "sourceSha256": source_sha256,
        "objectives": [
            _requirement_to_dict(requirement)
            for requirement in intent.objectives
        ],
        "constraints": [
            _constraint_to_dict(constraint)
            for constraint in intent.constraints
        ],
    }


def write_normalized_intent(
    intent: NormalizedIntent,
    output_file: Path,
    *,
    source_payload: str | bytes,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            normalized_intent_to_dict(
                intent,
                source_sha256=_source_sha256(source_payload),
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(output_file)


def load_normalized_intent(
    input_file: Path,
    *,
    source_payload: str | bytes | None = None,
) -> NormalizedIntent:
    try:
        payload = json.loads(input_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntentSemanticError(
            f"Could not read normalized intent {input_file}: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise IntentSemanticError("Normalized intent must be a JSON object")

    expected_fields = {
        "schemaVersion",
        "sourceSha256",
        "objectives",
        "constraints",
    }
    _require_exact_fields(payload, expected_fields, "normalized intent")

    schema_version = payload["schemaVersion"]

    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version
        not in SUPPORTED_NORMALIZED_INTENT_SCHEMA_VERSIONS
    ):
        raise IntentSemanticError(
            "Unsupported normalized intent schemaVersion "
            f"{schema_version!r}"
        )

    source_digest = _artifact_string(
        payload["sourceSha256"], "normalized intent.sourceSha256"
    )

    if len(source_digest) != 64 or any(
        character not in "0123456789abcdef" for character in source_digest
    ):
        raise IntentSemanticError(
            "normalized intent.sourceSha256 must be a lowercase SHA-256 digest"
        )

    if (
        source_payload is not None
        and source_digest != _source_sha256(source_payload)
    ):
        raise IntentSemanticError(
            "Normalized intent does not match the source submission"
        )

    objectives = _load_requirements(
        payload["objectives"],
        field_name="objectives",
    )
    constraints = (
        _load_requirements(
            payload["constraints"],
            field_name="constraints",
        )
        if schema_version == 1
        else _load_constraints(
            payload["constraints"],
            field_name="constraints",
        )
    )

    if not objectives:
        raise IntentSemanticError(
            "Normalized intent must contain at least one objective"
        )

    return NormalizedIntent(
        objectives=tuple(objectives),
        constraints=tuple(constraints),
    )


def _requirement_to_dict(
    requirement: NormalizedRequirement,
) -> dict[str, Any]:
    return {
        "requirementId": requirement.requirement_id,
        "metricId": requirement.metric_id,
        "statistic": requirement.statistic,
        "operator": requirement.operator,
        "canonicalValue": requirement.canonical_value,
        "canonicalUnit": requirement.canonical_unit,
        "enforcement": requirement.enforcement,
        "phases": sorted(requirement.phases),
        "priority": requirement.priority,
        "placementSource": _source_to_dict(requirement.placement_source),
        "runtimeSource": _source_to_dict(requirement.runtime_source),
        "sourceMeasuredBy": requirement.source_measured_by,
    }


def _constraint_to_dict(
    constraint: NormalizedConstraint,
) -> dict[str, Any]:
    if isinstance(constraint, NormalizedRequirement):
        return {
            "type": "metric",
            **_requirement_to_dict(constraint),
        }

    return {
        "type": "location",
        "constraintId": constraint.constraint_id,
        "target": constraint.target,
        "operator": constraint.operator,
        "values": list(constraint.values),
        "enforcement": constraint.enforcement,
        "priority": constraint.priority,
        "phases": sorted(constraint.phases),
    }


def _source_to_dict(source: MeasurementSource | None) -> dict | None:
    if source is None:
        return None

    return {
        "field": source.field,
        "canonicalFactor": source.canonical_factor,
    }


def _load_requirements(
    value: Any,
    *,
    field_name: str,
) -> list[NormalizedRequirement]:
    if not isinstance(value, list):
        raise IntentSemanticError(f"{field_name} must be a list")

    return [
        _load_requirement(
            item,
            field_name=f"{field_name}[{index}]",
        )
        for index, item in enumerate(value)
    ]


def _load_constraints(
    value: Any,
    *,
    field_name: str,
) -> list[NormalizedConstraint]:
    if not isinstance(value, list):
        raise IntentSemanticError(f"{field_name} must be a list")

    constraints: list[NormalizedConstraint] = []

    for index, item in enumerate(value):
        item_field = f"{field_name}[{index}]"

        if not isinstance(item, dict):
            raise IntentSemanticError(f"{item_field} must be an object")

        constraint_type = _artifact_string(
            item.get("type"),
            f"{item_field}.type",
        )

        if constraint_type == "metric":
            metric_payload = dict(item)
            metric_payload.pop("type")
            constraints.append(
                _load_requirement(
                    metric_payload,
                    field_name=item_field,
                )
            )
        elif constraint_type == "location":
            constraints.append(
                _load_location_constraint(
                    item,
                    field_name=item_field,
                )
            )
        else:
            raise IntentSemanticError(
                f"{item_field}.type {constraint_type!r} is unsupported"
            )

    return constraints


def _load_location_constraint(
    value: dict[str, Any],
    *,
    field_name: str,
) -> NormalizedLocationConstraint:
    expected_fields = {
        "type",
        "constraintId",
        "target",
        "operator",
        "values",
        "enforcement",
        "priority",
        "phases",
    }
    _require_exact_fields(value, expected_fields, field_name)
    constraint_id = _artifact_string(
        value["constraintId"],
        f"{field_name}.constraintId",
    )
    target = _artifact_string(
        value["target"],
        f"{field_name}.target",
    )
    operator_value = _artifact_string(
        value["operator"],
        f"{field_name}.operator",
    )

    if operator_value not in {"in", "notIn"}:
        raise IntentSemanticError(
            f"{field_name}.operator must be 'in' or 'notIn'"
        )

    values_payload = value["values"]

    if not isinstance(values_payload, list) or not values_payload:
        raise IntentSemanticError(
            f"{field_name}.values must be a non-empty string list"
        )

    values = tuple(
        _artifact_string(item, f"{field_name}.values[{index}]")
        for index, item in enumerate(values_payload)
    )

    if len(set(values)) != len(values):
        raise IntentSemanticError(
            f"{field_name}.values must not contain duplicates"
        )

    enforcement_value = _artifact_string(
        value["enforcement"],
        f"{field_name}.enforcement",
    )

    if enforcement_value not in {"hard", "soft"}:
        raise IntentSemanticError(
            f"{field_name}.enforcement must be 'hard' or 'soft'"
        )

    priority = _artifact_number(
        value["priority"],
        f"{field_name}.priority",
    )

    if priority <= 0:
        raise IntentSemanticError(f"{field_name}.priority must be positive")

    phases_payload = value["phases"]

    if not isinstance(phases_payload, list) or any(
        not isinstance(phase, str) for phase in phases_payload
    ):
        raise IntentSemanticError(f"{field_name}.phases must be a string list")

    phases = frozenset(phases_payload)

    if phases != frozenset({"placement", "runtime"}):
        raise IntentSemanticError(
            f"{field_name}.phases must be ['placement', 'runtime']"
        )

    return NormalizedLocationConstraint(
        constraint_id=constraint_id,
        target=target,
        operator=cast(LocationOperator, operator_value),
        values=values,
        enforcement=cast(Enforcement, enforcement_value),
        priority=priority,
        phases=phases,
    )


def _load_requirement(
    value: Any,
    *,
    field_name: str,
) -> NormalizedRequirement:
    if not isinstance(value, dict):
        raise IntentSemanticError(f"{field_name} must be an object")

    expected_fields = {
        "requirementId",
        "metricId",
        "statistic",
        "operator",
        "canonicalValue",
        "canonicalUnit",
        "enforcement",
        "phases",
        "priority",
        "placementSource",
        "runtimeSource",
        "sourceMeasuredBy",
    }
    _require_exact_fields(value, expected_fields, field_name)

    requirement_id = _artifact_string(
        value["requirementId"], f"{field_name}.requirementId"
    )
    metric_id = _artifact_string(
        value["metricId"], f"{field_name}.metricId"
    )
    statistic = _artifact_string(
        value["statistic"], f"{field_name}.statistic"
    )
    operator_value = _artifact_string(
        value["operator"], f"{field_name}.operator"
    )
    canonical_unit = _artifact_string(
        value["canonicalUnit"], f"{field_name}.canonicalUnit"
    )
    source_measured_by = _artifact_string(
        value["sourceMeasuredBy"],
        f"{field_name}.sourceMeasuredBy",
    )
    definition = METRIC_REGISTRY.get(metric_id)

    if definition is None:
        raise IntentSemanticError(
            f"{field_name}.metricId references unknown metric {metric_id!r}"
        )

    if statistic not in definition.supported_statistics:
        raise IntentSemanticError(
            f"{field_name}.statistic {statistic!r} is not supported"
        )

    if operator_value not in definition.allowed_operators:
        raise IntentSemanticError(
            f"{field_name}.operator {operator_value!r} is not supported"
        )

    if canonical_unit != definition.canonical_unit:
        raise IntentSemanticError(
            f"{field_name}.canonicalUnit must be "
            f"{definition.canonical_unit!r}"
        )

    enforcement = _artifact_string(
        value["enforcement"],
        f"{field_name}.enforcement",
    )

    if enforcement not in {"hard", "soft"}:
        raise IntentSemanticError(
            f"{field_name}.enforcement must be 'hard' or 'soft'"
        )

    phases_value = value["phases"]

    if not isinstance(phases_value, list) or any(
        not isinstance(phase, str) for phase in phases_value
    ):
        raise IntentSemanticError(f"{field_name}.phases must be a string list")

    phases = frozenset(phases_value)

    if phases != definition.phases:
        raise IntentSemanticError(
            f"{field_name}.phases do not match the metric registry"
        )

    binding = MEASUREMENT_BINDINGS.get(source_measured_by)

    if (
        binding is None
        or binding.metric_id != metric_id
        or binding.statistic != statistic
    ):
        raise IntentSemanticError(
            f"{field_name}.sourceMeasuredBy does not match its metric binding"
        )

    placement_source = _load_source(
        value["placementSource"],
        expected=definition.placement_fields.get(statistic),
        field_name=f"{field_name}.placementSource",
    )
    runtime_source = _load_source(
        value["runtimeSource"],
        expected=definition.runtime_fields.get(statistic),
        field_name=f"{field_name}.runtimeSource",
    )
    canonical_value = _artifact_number(
        value["canonicalValue"], f"{field_name}.canonicalValue"
    )
    priority = _artifact_number(
        value["priority"], f"{field_name}.priority"
    )

    if priority <= 0:
        raise IntentSemanticError(f"{field_name}.priority must be positive")

    return NormalizedRequirement(
        requirement_id=requirement_id,
        metric_id=metric_id,
        statistic=statistic,
        operator=operator_value,
        canonical_value=canonical_value,
        canonical_unit=canonical_unit,
        enforcement=cast(Enforcement, enforcement),
        phases=phases,
        priority=priority,
        placement_source=placement_source,
        runtime_source=runtime_source,
        source_measured_by=source_measured_by,
    )


def _load_source(
    value: Any,
    *,
    expected: MeasurementSource | None,
    field_name: str,
) -> MeasurementSource | None:
    if expected is None:
        if value is not None:
            raise IntentSemanticError(f"{field_name} must be null")
        return None

    if not isinstance(value, dict):
        raise IntentSemanticError(f"{field_name} must be an object")

    _require_exact_fields(value, {"field", "canonicalFactor"}, field_name)
    field = _artifact_string(value["field"], f"{field_name}.field")
    factor = _artifact_number(
        value["canonicalFactor"], f"{field_name}.canonicalFactor"
    )

    if field != expected.field or factor != expected.canonical_factor:
        raise IntentSemanticError(
            f"{field_name} does not match the metric registry"
        )

    return expected


def _require_exact_fields(
    payload: dict,
    expected: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(payload) - expected)
    missing = sorted(expected - set(payload))

    if unknown:
        raise IntentSemanticError(
            f"{field_name} contains unsupported fields: {', '.join(unknown)}"
        )

    if missing:
        raise IntentSemanticError(
            f"{field_name} is missing fields: {', '.join(missing)}"
        )


def _artifact_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntentSemanticError(f"{field_name} must be a non-empty string")
    return value


def _artifact_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntentSemanticError(f"{field_name} must be a number")

    number = float(value)

    if not math.isfinite(number):
        raise IntentSemanticError(f"{field_name} must be finite")

    return number


def _source_sha256(source_payload: str | bytes) -> str:
    encoded = (
        source_payload.encode("utf-8")
        if isinstance(source_payload, str)
        else source_payload
    )
    return sha256(encoded).hexdigest()
