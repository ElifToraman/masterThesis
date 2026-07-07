from __future__ import annotations

import json
import subprocess
from pathlib import Path

from controller.intent_function_parser import parse_intent_function_payload


CLUSTERS = [
    "vm1-cluster",
    "vm2-cluster",
]


def service_exists(
    *,
    cluster: str,
    service_name: str,
    namespace: str,
) -> bool:
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            cluster,
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
    cluster: str,
    service_name: str,
    namespace: str,
) -> str:
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            cluster,
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
            f"Failed cleanup on {cluster}: {output}"
        )

    return output


def main() -> None:
    controller_directory = Path(__file__).resolve().parents[1]

    decision_file = (
        controller_directory
        / "results"
        / "decisions"
        / "latest-decision.json"
    )

    submission_file = (
        controller_directory
        / "examples"
        / "hello-intent-function.yaml"
    )

    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    selected_cluster = decision["selected_cluster"]

    submission = parse_intent_function_payload(
        submission_file.read_text(encoding="utf-8")
    )

    service_name = submission.function.service_name
    namespace = submission.function.namespace

    for cluster in CLUSTERS:
        if cluster == selected_cluster:
            print(f"{cluster}: selected cluster, keeping {service_name}")
            continue

        if not service_exists(
            cluster=cluster,
            service_name=service_name,
            namespace=namespace,
        ):
            print(f"{cluster}: no stale {service_name} deployment found")
            continue

        output = delete_service(
            cluster=cluster,
            service_name=service_name,
            namespace=namespace,
        )

        print(f"{cluster}: {output}")


if __name__ == "__main__":
    main()
