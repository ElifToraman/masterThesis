# monitoring_service.py

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .csv_writer import write_metrics_csv
from .models import MetricsSnapshot
from .vm import VM


logger = logging.getLogger(__name__)


class MonitoringService:
    def __init__(
        self,
        vms: list[VM],
        collection_interval_seconds: float = 10.0,
        output_directory: Path | None = None,
    ) -> None:
        self._vms = vms
        self._collection_interval_seconds = (
            collection_interval_seconds
        )

        self._output_directory = (
            output_directory
            if output_directory is not None
            else Path(__file__).resolve().parent / "output"
        )

        self._output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._csv_index = self._find_next_csv_index()

        self._snapshot: MetricsSnapshot | None = None
        self._snapshot_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _find_next_csv_index(self) -> int:
        indexes: list[int] = []

        for path in self._output_directory.glob("metrics_*.csv"):
            raw_index = path.stem.removeprefix("metrics_")

            try:
                indexes.append(int(raw_index))
            except ValueError:
                continue

        return max(indexes, default=-1) + 1

    def _write_numbered_csv(
        self,
        snapshot: MetricsSnapshot,
    ) -> None:
        output_path = (
            self._output_directory
            / f"metrics_{self._csv_index}.csv"
        )

        temporary_path = output_path.with_suffix(".tmp")

        write_metrics_csv(
            output_path=temporary_path,
            vm_metrics=list(
                snapshot.vm_metrics.values()
            ),
            node_metrics=list(
                snapshot.node_metrics.values()
            ),
            pod_metrics=list(
                snapshot.pod_metrics.values()
            ),
        )

        temporary_path.replace(output_path)

        logger.info(
            "Metrics written to %s",
            output_path,
        )

        self._csv_index += 1

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._monitoring_loop,
            name="monitoring-service",
            daemon=True,
        )

        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()

    def get_latest_snapshot(
        self,
    ) -> MetricsSnapshot | None:
        with self._snapshot_lock:
            return self._snapshot

    def wait_for_first_snapshot(
        self,
        timeout_seconds: float = 60.0,
    ) -> MetricsSnapshot:
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            snapshot = self.get_latest_snapshot()

            if snapshot is not None and snapshot.node_metrics:
                return snapshot

            if self._stop_event.wait(0.5):
                break

        raise RuntimeError(
            "No valid monitoring snapshot became available"
        )

    def require_latest_snapshot(
        self,
        maximum_age_seconds: float = 30.0,
    ) -> MetricsSnapshot:
        snapshot = self.get_latest_snapshot()

        if snapshot is None:
            raise RuntimeError(
                "No monitoring snapshot is available yet"
            )

        age_seconds = (
            datetime.now(timezone.utc)
            - snapshot.timestamp
        ).total_seconds()

        if age_seconds > maximum_age_seconds:
            raise RuntimeError(
                f"Monitoring snapshot is stale: "
                f"{age_seconds:.1f} seconds old"
            )

        if not snapshot.node_metrics:
            raise RuntimeError(
                "Latest monitoring snapshot has no node metrics"
            )

        return snapshot

    def collect_snapshot(self) -> MetricsSnapshot:
        vm_metrics = {}
        node_metrics = {}
        pod_metrics = {}

        for vm in self._vms:
            cluster_name = vm.config.name

            try:
                vm_result = vm.gather_metrics()
                vm_metrics[cluster_name] = vm_result
            except RuntimeError:
                logger.exception(
                    "Could not collect VM metrics from %s",
                    cluster_name,
                )

            try:
                nodes_result = vm.gather_all_node_metrics()

                for node_name, metrics in nodes_result.items():
                    key = f"{cluster_name}/{node_name}"
                    node_metrics[key] = metrics

            except RuntimeError:
                logger.exception(
                    "Could not collect node metrics from %s",
                    cluster_name,
                )

            try:
                pods_result = vm.gather_all_pod_metrics()

                for (
                    namespace,
                    pod_name,
                ), metrics in pods_result.items():
                    key = (
                        cluster_name,
                        namespace,
                        pod_name,
                    )
                    pod_metrics[key] = metrics

            except RuntimeError:
                logger.exception(
                    "Could not collect pod metrics from %s",
                    cluster_name,
                )

        return MetricsSnapshot(
            timestamp=datetime.now(timezone.utc),
            vm_metrics=vm_metrics,
            node_metrics=node_metrics,
            pod_metrics=pod_metrics,
        )

    def collect_and_store_snapshot(self) -> MetricsSnapshot:
        """Collect one snapshot and persist its VM/node/pod evidence.

        The placement path uses the background monitoring loop, while the
        post-deployment control loop calls this method directly. Keeping the
        write here ensures both paths produce the same numbered CSV format.
        """
        snapshot = self.collect_snapshot()

        if snapshot.node_metrics:
            self._write_numbered_csv(snapshot)

        return snapshot

    def _monitoring_loop(self) -> None:
        while not self._stop_event.is_set():
            loop_started_at = time.monotonic()
            collection_started_at = time.perf_counter()

            try:
                new_snapshot = self.collect_snapshot()

                collection_duration_seconds = (
                    time.perf_counter()
                    - collection_started_at
                )

                if not new_snapshot.node_metrics:
                    logger.error(
                        "No node metrics collected; "
                        "keeping previous snapshot. "
                        "Collection took %.3f seconds",
                        collection_duration_seconds,
                    )
                else:
                    with self._snapshot_lock:
                        self._snapshot = new_snapshot

                    self._write_numbered_csv(
                        new_snapshot
                    )

                    logger.info(
                        (
                            "Snapshot updated: "
                            "%d VMs, %d nodes, %d pods. "
                            "Collection took %.3f seconds"
                        ),
                        len(new_snapshot.vm_metrics),
                        len(new_snapshot.node_metrics),
                        len(new_snapshot.pod_metrics),
                        collection_duration_seconds,
                    )

            except Exception:
                collection_duration_seconds = (
                    time.perf_counter()
                    - collection_started_at
                )

                logger.exception(
                    (
                        "Monitoring collection failed "
                        "after %.3f seconds"
                    ),
                    collection_duration_seconds,
                )

            elapsed = time.monotonic() - loop_started_at

            remaining = max(
                0.0,
                self._collection_interval_seconds
                - elapsed,
            )

            self._stop_event.wait(remaining)
