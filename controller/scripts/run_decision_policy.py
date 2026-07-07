from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from controller.decision_policy import (
    DecisionPolicy,
    write_decision,
)
from controller.intent_function_parser import (
    parse_intent_function_payload,
)
from controller.monitoring.models import VMConfig
from controller.monitoring.monitoring_service import MonitoringService
from controller.monitoring.vm import VM


def create_vms() -> list[VM]:
    return [
        VM(
            VMConfig(
                name="vm1-cluster",
                host="129.114.25.182",
                ssh_user="cc",
                ssh_key=Path.home() / ".ssh" / "chameleon_new",
                prometheus_url="http://127.0.0.1:19091",
            )
        ),
        VM(
            VMConfig(
                name="vm2-cluster",
                host="129.114.25.80",
                ssh_user="cc",
                ssh_key=Path.home() / ".ssh" / "chameleon_new",
                prometheus_url="http://127.0.0.1:19092",
            )
        ),
    ]


def main() -> None:
    controller_directory = Path(__file__).resolve().parents[1]

    submission_file = (
        controller_directory
        / "examples"
        / "hello-intent-function.yaml"
    )

    submission = parse_intent_function_payload(
        submission_file.read_text(encoding="utf-8")
    )

    monitoring = MonitoringService(
        vms=create_vms(),
        collection_interval_seconds=10,
        output_directory=(
            controller_directory
            / "monitoring"
            / "metrics"
        ),
    )

    monitoring.start()

    try:
        snapshot = monitoring.wait_for_first_snapshot(
            timeout_seconds=60,
        )

        policy = DecisionPolicy(
            benchmark_file=(
                controller_directory
                / "results"
                / "benchmarks.jsonl"
            ),
        )

        decision = policy.decide(
            submission=submission,
            snapshot=snapshot,
        )

        output_file = (
            controller_directory
            / "results"
            / "decisions"
            / "latest-decision.json"
        )

        write_decision(
            decision=decision,
            output_file=output_file,
        )

        print(f"Decision mode: {decision.decision_mode}")
        print(f"Selected cluster: {decision.selected_cluster}")
        print(f"Reason: {decision.reason}")
        print(f"Decision file: {output_file}")

        print()
        print("Candidates:")

        for candidate in decision.candidates:
            print(
                "-",
                candidate.cluster_name,
                "feasible=",
                candidate.feasible,
                "intent_satisfied=",
                candidate.intent_satisfied,
                "p95=",
                candidate.benchmark_p95_latency_ms,
                "available_cpu=",
                candidate.available_cpu_cores,
                "available_memory_mb=",
                round(candidate.available_memory_bytes / 1024 / 1024, 2),
                "score=",
                candidate.score,
                "rejections=",
                candidate.rejection_reasons,
            )

    finally:
        monitoring.stop()


if __name__ == "__main__":
    main()
