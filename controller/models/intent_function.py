from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_OPERATORS = {
    "<",
    "<=",
    "==",
    ">=",
    ">",
}


@dataclass(frozen=True)
class FunctionDescriptor:
    name: str
    namespace: str
    service_name: str
    version: str
    runtime: str
    image: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Function name must not be empty")

        if not self.namespace.strip():
            raise ValueError("Function namespace must not be empty")

        if not self.service_name.strip():
            raise ValueError("Function service_name must not be empty")

        if not self.version.strip():
            raise ValueError("Function version must not be empty")

        if not self.runtime.strip():
            raise ValueError("Function runtime must not be empty")

        if not self.image.strip():
            raise ValueError("Function image must not be empty")

@dataclass(frozen=True)
class TargetRef:
    kind: str
    name: str

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("targetRef.kind must not be empty")

        if not self.name.strip():
            raise ValueError("targetRef.name must not be empty")


@dataclass(frozen=True)
class Objective:
    name: str
    operator: str
    value: float
    measured_by: str
    unit: str | None = None
    description: str | None = None
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Objective name must not be empty")

        if self.operator not in SUPPORTED_OPERATORS:
            raise ValueError(
                f"Unsupported objective operator: {self.operator}"
            )

        if not self.measured_by.strip():
            raise ValueError("Objective measured_by must not be empty")

        if self.weight <= 0:
            raise ValueError("Objective weight must be positive")


@dataclass(frozen=True)
class Intent:
    target_ref: TargetRef
    objectives: list[Objective]
    constraints: list[Objective] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.objectives:
            raise ValueError("Intent must contain at least one objective")


@dataclass(frozen=True)
class IntentFunction:
    api_version: str
    kind: str
    name: str
    function: FunctionDescriptor
    intent: Intent
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind != "IntentFunction":
            raise ValueError(
                f"Unsupported kind {self.kind!r}; expected 'IntentFunction'"
            )

        if not self.name.strip():
            raise ValueError("metadata.name must not be empty")
