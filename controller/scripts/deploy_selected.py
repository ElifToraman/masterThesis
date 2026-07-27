from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from controller.deployer import KnativeDeployer
from controller.execution_validator import (
    validate_deployment,
    write_execution_validation,
)
from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    DEFAULT_RUNTIME_CONFIG_FILE,
    DEFAULT_SUBMISSION_FILE,
    load_cluster_configs,
    load_runtime_config,
    load_submission,
)


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
        "--runtime-config",
        type=Path,
        default=DEFAULT_RUNTIME_CONFIG_FILE,
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
    selected_cluster = decision.get("selected_cluster")

    if not selected_cluster:
        raise SystemExit("No selected_cluster in latest decision file.")

    submission = load_submission(args.submission)
    clusters = load_cluster_configs(args.cluster_config)
    runtime_config = load_runtime_config(args.runtime_config)
    run_id = args.run_id or uuid.uuid4().hex

    result = KnativeDeployer(clusters).deploy(
        cluster_name=selected_cluster,
        submission=submission,
    )

    print(f"Deployment cluster: {result.cluster_name}")
    print(f"Service: {result.service_name}")
    print(f"Namespace: {result.namespace}")
    print(f"Image: {result.image}")
    print(f"URL: {result.url}")

    validation_properties = runtime_config["validation"]

    validation = validate_deployment(
        run_id=run_id,
        cluster_name=result.cluster_name,
        service_name=result.service_name,
        namespace=result.namespace,
        image=result.image,
        url=result.url,
        maximum_attempts=int(
            validation_properties.get("maximumAttempts", 5)
        ),
        timeout_seconds=float(
            validation_properties.get("timeoutSeconds", 10)
        ),
        retry_interval_seconds=float(
            validation_properties.get(
                "retryIntervalSeconds",
                2,
            )
        ),
    )

    latest_execution_file = (
        controller_directory
        / "results"
        / "executions"
        / "latest-execution.json"
    )
    run_execution_file = (
        controller_directory
        / "results"
        / "runs"
        / run_id
        / "execution.json"
    )

    write_execution_validation(
        result=validation,
        output_file=latest_execution_file,
    )
    write_execution_validation(
        result=validation,
        output_file=run_execution_file,
    )

    print(
        "Final invocation: "
        f"success={validation.success} "
        f"status={validation.status_code} "
        f"latency={validation.latency_ms} ms"
    )
    print(f"Execution evidence: {run_execution_file}")

    if not validation.success:
        raise SystemExit(
            "The deployed hello service did not pass "
            "final invocation validation."
        )


if __name__ == "__main__":
    main()
