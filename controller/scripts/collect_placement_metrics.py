from __future__ import annotations

import argparse
from pathlib import Path

from controller.monitoring.monitoring_service import MonitoringService
from controller.monitoring.snapshot_repository import (
    write_metrics_snapshot,
)
from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    load_cluster_configs,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect and persist one run-specific placement-monitoring snapshot."
        )
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    if not args.run_id.replace("-", "").isalnum():
        raise SystemExit(
            "run-id may contain only letters, numbers, and hyphens"
        )

    controller_directory = Path(__file__).resolve().parents[1]
    run_directory = controller_directory / "results" / "runs" / args.run_id
    monitoring_directory = run_directory / "placement-monitoring"
    snapshot_file = monitoring_directory / "snapshot.json"
    clusters = load_cluster_configs(args.cluster_config)

    monitoring = MonitoringService(
        vms=[cluster.create_vm() for cluster in clusters.values()],
        output_directory=monitoring_directory / "raw-metrics",
    )
    snapshot = monitoring.collect_and_store_snapshot()

    write_metrics_snapshot(
        snapshot=snapshot,
        run_id=args.run_id,
        output_file=snapshot_file,
    )

    if not snapshot.node_metrics:
        raise SystemExit(
            "Placement monitoring collected no Kubernetes node metrics"
        )

    attempted_clusters = sorted(clusters)
    observed_clusters = sorted(
        set(snapshot.vm_metrics)
        | {metric.cluster_name for metric in snapshot.node_metrics.values()}
    )

    print(f"Placement snapshot: {snapshot_file}")
    print(f"Attempted clusters: {', '.join(attempted_clusters)}")
    print(f"Observed clusters: {', '.join(observed_clusters)}")
    print(f"VM metrics: {len(snapshot.vm_metrics)}")
    print(f"Node metrics: {len(snapshot.node_metrics)}")
    print(f"Pod metrics: {len(snapshot.pod_metrics)}")


if __name__ == "__main__":
    main()
