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
class IntentProperties:
    min_scale: int | None = None
    max_scale: int | None = None
    container_port: int = 8080

    def __post_init__(self) -> None:
        if (
            self.min_scale is not None
            and (
                isinstance(self.min_scale, bool)
                or not isinstance(self.min_scale, int)
            )
        ):
            raise ValueError("minScale must be an integer")

        if (
            self.max_scale is not None
            and (
                isinstance(self.max_scale, bool)
                or not isinstance(self.max_scale, int)
            )
        ):
            raise ValueError("maxScale must be an integer")

        if self.min_scale is not None and self.min_scale < 0:
            raise ValueError("minScale must be zero or greater")

        if self.max_scale is not None and self.max_scale < 0:
            raise ValueError("maxScale must be zero or greater")

        # Knative defines max-scale 0 as unlimited, so only positive upper
        # bounds participate in this relationship check.
        if (
            self.min_scale is not None
            and self.max_scale is not None
            and self.max_scale > 0
            and self.min_scale > self.max_scale
        ):
            raise ValueError("minScale must not exceed maxScale")

        if (
            isinstance(self.container_port, bool)
            or not isinstance(self.container_port, int)
        ):
            raise ValueError("containerPort must be an integer")

        if not 1 <= self.container_port <= 65535:
            raise ValueError(
                "containerPort must be between 1 and 65535"
            )


@dataclass(frozen=True)
class Intent:
    target_ref: TargetRef
    objectives: list[Objective]
    constraints: list[Objective] = field(default_factory=list)
    properties: IntentProperties = field(default_factory=IntentProperties)

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
