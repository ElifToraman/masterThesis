from __future__ import annotations

import json
from pathlib import Path

from controller.deployer import KnativeDeployer
from controller.intent_function_parser import parse_intent_function_payload


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
    selected_cluster = decision.get("selected_cluster")

    if not selected_cluster:
        raise SystemExit("No selected_cluster in latest decision file.")

    submission = parse_intent_function_payload(
        submission_file.read_text(encoding="utf-8")
    )

    result = KnativeDeployer().deploy(
        cluster_name=selected_cluster,
        submission=submission,
    )

    print(f"Deployment cluster: {result.cluster_name}")
    print(f"Service: {result.service_name}")
    print(f"Namespace: {result.namespace}")
    print(f"Image: {result.image}")
    print(f"URL: {result.url}")


if __name__ == "__main__":
    main()
