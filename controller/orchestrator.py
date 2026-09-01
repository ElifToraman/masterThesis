from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    DEFAULT_POLICY_CONFIG_FILE,
    DEFAULT_RUNTIME_CONFIG_FILE,
    DEFAULT_SUBMISSION_FILE,
    load_cluster_configs,
    load_policy_config,
    load_runtime_config,
    load_submission,
)


def run_step(name: str, command: list[str]) -> None:
    print()
    print(f"=== {name} ===")

    result = subprocess.run(
        command,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise SystemExit(
            f"{name} failed with exit code {result.returncode}"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark, select, deploy, and clean up "
            "an intent-based hello function submission."
        )
    )
    parser.add_argument(
        "--submission",
        type=Path,
        default=DEFAULT_SUBMISSION_FILE,
        help="Path to an IntentFunction YAML or JSON file.",
    )
    parser.add_argument(
        "--policy-config",
        type=Path,
        default=DEFAULT_POLICY_CONFIG_FILE,
        help="Path to the decision-policy JSON configuration.",
    )
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=DEFAULT_RUNTIME_CONFIG_FILE,
        help=(
            "Path to controller-owned benchmark, validation, "
            "and monitoring settings."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional caller-provided run identifier. "
            "The REST API uses this to correlate status and artifacts."
        ),
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
        help="Path to the controller cluster configuration.",
    )
    args = parser.parse_args(argv)

    submission_file = args.submission.expanduser().resolve()
    cluster_config_file = args.cluster_config.expanduser().resolve()
    policy_config_file = args.policy_config.expanduser().resolve()
    runtime_config_file = args.runtime_config.expanduser().resolve()

    # Validate every input before performing any cluster mutation.
    load_submission(submission_file)
    load_cluster_configs(cluster_config_file)
    load_policy_config(policy_config_file)
    load_runtime_config(runtime_config_file)

    run_id = args.run_id or uuid.uuid4().hex

    if not run_id.replace("-", "").isalnum():
        raise SystemExit(
            "run-id may contain only letters, numbers, and hyphens"
        )
    print(f"Orchestration run ID: {run_id}")

    placement_snapshot_file = (
        Path(__file__).resolve().parent
        / "results"
        / "runs"
        / run_id
        / "placement-monitoring"
        / "snapshot.json"
    )

    common_arguments = [
        "--submission",
        str(submission_file),
        "--cluster-config",
        str(cluster_config_file),
        "--run-id",
        run_id,
    ]

    run_step(
        "Benchmark candidate clusters",
        [
            sys.executable,
            "-m",
            "controller.scripts.run_benchmark",
            *common_arguments,
            "--runtime-config",
            str(runtime_config_file),
        ],
    )

    run_step(
        "Collect placement monitoring snapshot",
        [
            sys.executable,
            "-m",
            "controller.scripts.collect_placement_metrics",
            "--cluster-config",
            str(cluster_config_file),
            "--run-id",
            run_id,
        ],
    )

    run_step(
        "Select best cluster",
        [
            sys.executable,
            "-m",
            "controller.scripts.run_decision_policy",
            *common_arguments,
            "--policy-config",
            str(policy_config_file),
            "--monitoring-snapshot",
            str(placement_snapshot_file),
        ],
    )

    run_step(
        "Deploy selected function",
        [
            sys.executable,
            "-m",
            "controller.scripts.deploy_selected",
            *common_arguments,
            "--runtime-config",
            str(runtime_config_file),
        ],
    )

    run_step(
        "Remove old deployment from non-selected clusters",
        [
            sys.executable,
            "-m",
            "controller.scripts.cleanup_non_selected",
            *common_arguments,
        ],
    )


if __name__ == "__main__":
    main()
