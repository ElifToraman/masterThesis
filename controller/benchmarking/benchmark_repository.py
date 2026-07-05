# controller/benchmarking/benchmark_repository.py

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import ClusterBenchmarkResult


class BenchmarkRepository:
    def __init__(
        self,
        output_file: Path,
    ) -> None:
        self._output_file = output_file
        self._output_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._lock = threading.Lock()

    def save(
        self,
        result: ClusterBenchmarkResult,
    ) -> None:
        record = asdict(result)

        record["timestamp"] = (
            result.timestamp.isoformat()
        )
        record["warm_latency_samples_ms"] = list(
            result.warm_latency_samples_ms
        )
        record["average_warm_latency_ms"] = (
            result.average_warm_latency_ms
            if result.warm_latency_samples_ms
            else None
        )
        record["p50_warm_latency_ms"] = (
            result.p50_warm_latency_ms
            if result.warm_latency_samples_ms
            else None
        )
        record["p95_warm_latency_ms"] = (
            result.p95_warm_latency_ms
            if result.warm_latency_samples_ms
            else None
        )
        record["success_rate"] = result.success_rate

        with self._lock:
            with self._output_file.open(
                "a",
                encoding="utf-8",
            ) as file:
                file.write(json.dumps(record))
                file.write("\n")

    def find_latest(
        self,
        function_name: str,
        image_reference: str,
    ) -> dict[str, dict]:
        if not self._output_file.exists():
            return {}

        latest_by_cluster: dict[str, dict] = {}

        with self._lock:
            with self._output_file.open(
                encoding="utf-8",
            ) as file:
                for line in file:
                    if not line.strip():
                        continue

                    record = json.loads(line)

                    if (
                        record["function_name"]
                        != function_name
                    ):
                        continue

                    if (
                        record["image_reference"]
                        != image_reference
                    ):
                        continue

                    cluster_name = record["cluster_name"]
                    current = latest_by_cluster.get(
                        cluster_name
                    )

                    if (
                        current is None
                        or datetime.fromisoformat(
                            record["timestamp"]
                        )
                        > datetime.fromisoformat(
                            current["timestamp"]
                        )
                    ):
                        latest_by_cluster[
                            cluster_name
                        ] = record

        return latest_by_cluster
