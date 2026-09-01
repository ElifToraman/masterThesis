from __future__ import annotations

import argparse
from pathlib import Path

from controller.decision_policy import (
    DecisionPolicy,
    write_decision,
)
from controller.image_resolver import resolve_image_for_registry
from controller.monitoring.snapshot_repository import (
    load_metrics_snapshot,
)
from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    DEFAULT_POLICY_CONFIG_FILE,
    DEFAULT_SUBMISSION_FILE,
    load_cluster_configs,
    load_policy_config,
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
        "--policy-config",
        type=Path,
        default=DEFAULT_POLICY_CONFIG_FILE,
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument(
        "--run-id",
        required=True,
    )
    parser.add_argument(
        "--monitoring-snapshot",
        type=Path,
        default=None,
        help=(
            "Run-specific placement monitoring snapshot. Defaults to "
            "results/runs/<run-id>/placement-monitoring/snapshot.json."
        ),
    )
    args = parser.parse_args(argv)

    controller_directory = Path(__file__).resolve().parents[1]
    submission = load_submission(args.submission)
    clusters = load_cluster_configs(args.cluster_config)
    policy_config = load_policy_config(args.policy_config)
    snapshot_file = (
        args.monitoring_snapshot.expanduser().resolve()
        if args.monitoring_snapshot is not None
        else (
            controller_directory
            / "results"
            / "runs"
            / args.run_id
            / "placement-monitoring"
            / "snapshot.json"
        )
    )
    snapshot = load_metrics_snapshot(
        input_file=snapshot_file,
        expected_run_id=args.run_id,
        maximum_age_seconds=float(
            policy_config.get(
                "maximumMonitoringSnapshotAgeSeconds",
                120,
            )
        ),
    )

    policy = DecisionPolicy(
        benchmark_file=(controller_directory / "results" / "benchmarks.jsonl"),
        expected_run_id=args.run_id,
        expected_images={
            name: resolve_image_for_registry(
                image=submission.function.image,
                registry=cluster.image_registry,
            )
            for name, cluster in clusters.items()
        },
        minimum_success_rate=float(
            policy_config["minimumBenchmarkSuccessRate"]
        ),
        maximum_benchmark_age_seconds=float(
            policy_config["maximumBenchmarkAgeSeconds"]
        ),
        default_required_cpu_cores=float(
            policy_config["defaultRequiredCpuCores"]
        ),
        default_required_memory_bytes=int(
            float(policy_config["defaultRequiredMemoryMiB"]) * 1024**2
        ),
        cpu_safety_factor=float(policy_config["cpuSafetyFactor"]),
        memory_safety_factor=float(policy_config["memorySafetyFactor"]),
        scoring_weights={
            str(name): float(weight)
            for name, weight in policy_config["scoringWeights"].items()
        },
        cold_start_reference_ms=float(policy_config["coldStartReferenceMs"]),
        deployment_reference_ms=float(policy_config["deploymentReferenceMs"]),
    )

    decision = policy.decide(
        submission=submission,
        snapshot=snapshot,
    )

    output_file = (
        controller_directory / "results" / "decisions" / "latest-decision.json"
    )

    write_decision(
        decision=decision,
        output_file=output_file,
    )

    write_decision(
        decision=decision,
        output_file=(
            controller_directory
            / "results"
            / "runs"
            / args.run_id
            / "decision.json"
        ),
    )

    print(f"Monitoring snapshot: {snapshot_file}")
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


if __name__ == "__main__":
    main()
