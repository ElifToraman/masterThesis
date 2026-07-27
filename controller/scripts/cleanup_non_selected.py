from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    DEFAULT_SUBMISSION_FILE,
    load_cluster_configs,
    load_submission,
)


def service_exists(
    *,
    kubernetes_context: str,
    service_name: str,
    namespace: str,
) -> bool:
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kubernetes_context,
            "get",
            "ksvc",
            service_name,
            "-n",
            namespace,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    return result.returncode == 0


def delete_service(
    *,
    kubernetes_context: str,
    service_name: str,
    namespace: str,
) -> str:
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kubernetes_context,
            "delete",
            "ksvc",
            service_name,
            "-n",
            namespace,
            "--ignore-not-found",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    output = result.stdout.strip() or result.stderr.strip()

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed cleanup on {kubernetes_context}: {output}"
        )

    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--submission",
        type=Path,
        default=DEFAULT_SUBMISSION_FILE,
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument(
        "--run-id",
        default=None,
    )
    args = parser.parse_args(argv)

    controller_directory = Path(__file__).resolve().parents[1]

    decision_file = (
        controller_directory
        / "results"
        / "runs"
        / args.run_id
        / "decision.json"
        if args.run_id
        else controller_directory
        / "results"
        / "decisions"
        / "latest-decision.json"
    )

    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    selected_cluster = decision["selected_cluster"]

    submission = load_submission(args.submission)
    clusters = load_cluster_configs(args.cluster_config)

    service_name = submission.function.service_name
    namespace = submission.function.namespace

    for cluster_name, cluster in clusters.items():
        if cluster_name == selected_cluster:
            print(
                f"{cluster_name}: selected cluster, "
                f"keeping {service_name}"
            )
            continue

        if not service_exists(
            kubernetes_context=cluster.kubernetes_context,
            service_name=service_name,
            namespace=namespace,
        ):
            print(
                f"{cluster_name}: no stale "
                f"{service_name} deployment found"
            )
            continue

        output = delete_service(
            kubernetes_context=cluster.kubernetes_context,
            service_name=service_name,
            namespace=namespace,
        )

        print(f"{cluster_name}: {output}")


if __name__ == "__main__":
    main()
