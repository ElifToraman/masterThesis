from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from controller.intent_function_parser import (
    parse_and_translate_intent_function_payload,
)
from controller.function_profiles import (
    DEFAULT_FUNCTION_PROFILES_FILE,
    load_function_profiles,
    require_function_profile,
)
from controller.intent_translation import (
    load_normalized_intent,
    validate_location_constraint_clusters,
    write_normalized_intent,
)
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
            "an intent-based function submission."
        )
    )
    parser.add_argument(
        "--submission",
        type=Path,
        default=DEFAULT_SUBMISSION_FILE,
        help="Path to an IntentFunction YAML or JSON file.",
    )
    parser.add_argument(
        "--normalized-intent",
        type=Path,
        default=None,
        help=(
            "Path to the normalized intent artifact. Defaults to "
            "results/runs/<run-id>/normalized-intent.json."
        ),
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
        "--function-profiles",
        type=Path,
        default=DEFAULT_FUNCTION_PROFILES_FILE,
        help="Path to controller-owned function invocation profiles.",
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
    function_profiles_file = args.function_profiles.expanduser().resolve()

    # Validate every non-intent input before performing any cluster mutation.
    clusters = load_cluster_configs(cluster_config_file)
    load_policy_config(policy_config_file)
    load_runtime_config(runtime_config_file)
    profiles = load_function_profiles(function_profiles_file)

    run_id = args.run_id or uuid.uuid4().hex

    if not run_id.replace("-", "").isalnum():
        raise SystemExit(
            "run-id may contain only letters, numbers, and hyphens"
        )
    print(f"Orchestration run ID: {run_id}")

    run_directory = (
        Path(__file__).resolve().parent / "results" / "runs" / run_id
    )
    normalized_intent_file = (
        args.normalized_intent.expanduser().resolve()
        if args.normalized_intent is not None
        else run_directory / "normalized-intent.json"
    )
    source_payload = submission_file.read_bytes()

    if normalized_intent_file.is_file():
        normalized_intent = load_normalized_intent(
            normalized_intent_file,
            source_payload=source_payload,
        )
        submission = load_submission(
            submission_file,
            validate_semantics=False,
        )
    else:
        submission, normalized_intent = (
            parse_and_translate_intent_function_payload(source_payload)
        )
        write_normalized_intent(
            normalized_intent,
            normalized_intent_file,
            source_payload=source_payload,
        )

    validate_location_constraint_clusters(
        normalized_intent,
        set(clusters),
    )
    require_function_profile(profiles, submission.function.name)

    placement_snapshot_file = (
        run_directory
        / "placement-monitoring"
        / "snapshot.json"
    )

    control_loop_trigger_file = run_directory / "control-loop-trigger.json"
    if not control_loop_trigger_file.is_file():
        run_step(
            "Remove supported deployments for independent-run isolation",
            [
                sys.executable,
                "-m",
                "controller.scripts.cleanup_supported_services",
                "--cluster-config",
                str(cluster_config_file),
                "--function-profiles",
                str(function_profiles_file),
                "--namespace",
                submission.function.namespace,
                "--output",
                str(run_directory / "pre-run-cleanup.json"),
            ],
        )
    else:
        print(
            "Skipping independent-run cleanup for automatic control-loop "
            "re-evaluation."
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
            "--function-profiles",
            str(function_profiles_file),
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
            "--normalized-intent",
            str(normalized_intent_file),
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
            "--function-profiles",
            str(function_profiles_file),
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
