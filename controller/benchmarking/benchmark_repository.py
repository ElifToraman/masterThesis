from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from controller.benchmarking.models import (
    ClusterBenchmarkResult,
)


class BenchmarkRepository:
    def __init__(
        self,
        output_file: Path,
    ) -> None:
        self.output_file = output_file
        self.output_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def save(
        self,
        result: ClusterBenchmarkResult,
    ) -> None:
        record = asdict(result)

        record["timestamp"] = (
            result.timestamp.isoformat()
        )

        record["total_requests"] = (
            result.total_requests
        )
        record["success_rate"] = (
            result.success_rate
        )
        record["average_warm_latency_ms"] = (
            result.average_warm_latency_ms
        )
        record["p50_warm_latency_ms"] = (
            result.p50_warm_latency_ms
        )
        record["p95_warm_latency_ms"] = (
            result.p95_warm_latency_ms
        )

        with self.output_file.open(
            "a",
            encoding="utf-8",
        ) as file:
            json.dump(record, file)
            file.write("\n")

    def find_latest(
        self,
        function_name: str,
        function_version: str,
    ) -> dict[str, dict[str, Any]]:
        latest_by_cluster: dict[str, dict[str, Any]] = {}

        if not self.output_file.exists():
            return latest_by_cluster

        with self.output_file.open(
            "r",
            encoding="utf-8",
        ) as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if (
                    record.get("function_name")
                    != function_name
                ):
                    continue

                if (
                    record.get("function_version")
                    != function_version
                ):
                    continue

                cluster_name = record.get(
                    "cluster_name"
                )

                if not isinstance(
                    cluster_name,
                    str,
                ):
                    continue

                current = latest_by_cluster.get(
                    cluster_name
                )

                if current is None:
                    latest_by_cluster[
                        cluster_name
                    ] = record
                    continue

                if _parse_timestamp(
                    record.get("timestamp")
                ) > _parse_timestamp(
                    current.get("timestamp")
                ):
                    latest_by_cluster[
                        cluster_name
                    ] = record

        return latest_by_cluster


def _parse_timestamp(
    value: Any,
) -> datetime:
    if not isinstance(value, str):
        return datetime.min

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min