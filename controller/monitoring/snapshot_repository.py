from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, TypeVar

from .models import MetricsSnapshot, NodeMetrics, PodMetrics, VMMetrics


SCHEMA_VERSION = 1


class SnapshotRepositoryError(RuntimeError):
    pass


Metric = TypeVar("Metric", VMMetrics, NodeMetrics, PodMetrics)


def write_metrics_snapshot(
    *,
    snapshot: MetricsSnapshot,
    run_id: str,
    output_file: Path,
) -> None:
    if not run_id:
        raise SnapshotRepositoryError("run_id must not be empty")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp": _format_timestamp(snapshot.timestamp),
        "vm_metrics": [
            _metric_payload(metric) for metric in snapshot.vm_metrics.values()
        ],
        "node_metrics": [
            _metric_payload(metric)
            for metric in snapshot.node_metrics.values()
        ],
        "pod_metrics": [
            _metric_payload(metric) for metric in snapshot.pod_metrics.values()
        ],
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(output_file)


def load_metrics_snapshot(
    *,
    input_file: Path,
    expected_run_id: str | None = None,
    maximum_age_seconds: float | None = None,
) -> MetricsSnapshot:
    if not input_file.is_file():
        raise SnapshotRepositoryError(
            f"Monitoring snapshot does not exist: {input_file}"
        )

    try:
        payload = json.loads(input_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SnapshotRepositoryError(
            f"Monitoring snapshot is not valid JSON: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise SnapshotRepositoryError(
            "Monitoring snapshot must contain a JSON object"
        )

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotRepositoryError(
            "Unsupported monitoring snapshot schema version: "
            f"{payload.get('schema_version')!r}"
        )

    run_id = payload.get("run_id")

    if not isinstance(run_id, str) or not run_id:
        raise SnapshotRepositoryError(
            "Monitoring snapshot has no valid run_id"
        )

    if expected_run_id is not None and run_id != expected_run_id:
        raise SnapshotRepositoryError(
            "Monitoring snapshot belongs to run "
            f"{run_id!r}, expected {expected_run_id!r}"
        )

    timestamp = _parse_timestamp(
        payload.get("timestamp"),
        field_name="timestamp",
    )

    if maximum_age_seconds is not None:
        if maximum_age_seconds <= 0:
            raise SnapshotRepositoryError(
                "maximum_age_seconds must be positive"
            )

        age_seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()

        if age_seconds < -5:
            raise SnapshotRepositoryError(
                "Monitoring snapshot timestamp is in the future"
            )

        if age_seconds > maximum_age_seconds:
            raise SnapshotRepositoryError(
                "Monitoring snapshot is stale: "
                f"{age_seconds:.1f} seconds old; maximum is "
                f"{maximum_age_seconds:.1f} seconds"
            )

    vm_metrics = _load_metrics(
        payload=payload,
        field_name="vm_metrics",
        metric_type=VMMetrics,
        key_builder=lambda metric: metric.cluster_name,
    )
    node_metrics = _load_metrics(
        payload=payload,
        field_name="node_metrics",
        metric_type=NodeMetrics,
        key_builder=lambda metric: f"{metric.cluster_name}/{metric.node_name}",
    )
    pod_metrics = _load_metrics(
        payload=payload,
        field_name="pod_metrics",
        metric_type=PodMetrics,
        key_builder=lambda metric: (
            metric.cluster_name,
            metric.namespace,
            metric.pod_name,
        ),
    )

    if not node_metrics:
        raise SnapshotRepositoryError(
            "Monitoring snapshot contains no Kubernetes node metrics"
        )

    return MetricsSnapshot(
        timestamp=timestamp,
        vm_metrics=vm_metrics,
        node_metrics=node_metrics,
        pod_metrics=pod_metrics,
    )


def _metric_payload(metric: Metric) -> dict[str, Any]:
    payload = asdict(metric)
    payload["timestamp"] = _format_timestamp(metric.timestamp)
    return payload


def _load_metrics(
    *,
    payload: dict[str, Any],
    field_name: str,
    metric_type: type[Metric],
    key_builder,
) -> dict[Any, Metric]:
    raw_metrics = payload.get(field_name)

    if not isinstance(raw_metrics, list):
        raise SnapshotRepositoryError(
            f"Monitoring snapshot field {field_name!r} must be a list"
        )

    metrics: dict[Any, Metric] = {}

    for index, raw_metric in enumerate(raw_metrics):
        if not isinstance(raw_metric, dict):
            raise SnapshotRepositoryError(
                f"{field_name}[{index}] must be an object"
            )

        values = dict(raw_metric)
        values["timestamp"] = _parse_timestamp(
            values.get("timestamp"),
            field_name=f"{field_name}[{index}].timestamp",
        )

        try:
            metric = metric_type(**values)
        except (TypeError, ValueError) as error:
            raise SnapshotRepositoryError(
                f"Invalid {field_name}[{index}]: {error}"
            ) from error

        key = key_builder(metric)

        if key in metrics:
            raise SnapshotRepositoryError(
                f"Duplicate metric key in {field_name}: {key!r}"
            )

        metrics[key] = metric

    return metrics


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise SnapshotRepositoryError(
            "Monitoring timestamps must include a timezone"
        )

    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise SnapshotRepositoryError(
            f"{field_name} must be an ISO-8601 string"
        )

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SnapshotRepositoryError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from error

    if parsed.tzinfo is None:
        raise SnapshotRepositoryError(f"{field_name} must include a timezone")

    return parsed.astimezone(timezone.utc)
