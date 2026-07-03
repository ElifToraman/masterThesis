#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = CONTROLLER_DIRECTORY.parent

DEFAULT_BENCHMARK_PROFILE = (
    CONTROLLER_DIRECTORY
    / "results"
    / "benchmarks"
    / "latest-profile.json"
)

DEFAULT_PIPELINES_DIRECTORY = (
    CONTROLLER_DIRECTORY
    / "results"
    / "pipelines"
)

DEFAULT_EXPERIMENTS_DIRECTORY = (
    CONTROLLER_DIRECTORY
    / "results"
    / "experiments"
)


class OrchestrationError(RuntimeError):
    """Raised when an end-to-end component fails."""


def utc_now(milliseconds: bool = False) -> str:
    timespec = "milliseconds" if milliseconds else "seconds"

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
    )


def default_run_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    return f"pipeline-{timestamp}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(
            f"Cannot read JSON file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise OrchestrationError(
            f"{path} must contain a JSON object"
        )

    return value


def write_json(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            value,
            file,
            indent=2,
        )
        file.write("\n")


def run_component(
    name: str,
    command: list[str],
) -> dict[str, Any]:
    print()
    print(f"===== {name} =====")
    print("$ " + " ".join(command))

    started_at = utc_now(milliseconds=True)
    started_perf = time.perf_counter()

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        text=True,
        check=False,
    )

    finished_at = utc_now(milliseconds=True)

    duration_ms = round(
        (
            time.perf_counter()
            - started_perf
        )
        * 1000,
        3,
    )

    result = {
        "name": name,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "return_code": completed.returncode,
        "command": command,
    }

    if completed.returncode != 0:
        raise OrchestrationError(
            f"{name} failed with return code "
            f"{completed.returncode}"
        )

    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run monitoring, placement, selected-cluster "
            "deployment and result collection in order."
        )
    )

    parser.add_argument(
        "--run-id",
        default=default_run_id(),
    )

    parser.add_argument(
        "--benchmark-profile",
        type=Path,
        default=DEFAULT_BENCHMARK_PROFILE,
    )

    parser.add_argument(
        "--pipelines-directory",
        type=Path,
        default=DEFAULT_PIPELINES_DIRECTORY,
    )

    parser.add_argument(
        "--experiments-directory",
        type=Path,
        default=DEFAULT_EXPERIMENTS_DIRECTORY,
    )

    parser.add_argument(
        "--cleanup-after",
        action="store_true",
        help=(
            "Delete the selected runtime deployment "
            "after execution."
        ),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    run_id = arguments.run_id

    pipeline_directory = (
        arguments.pipelines_directory
        / run_id
    )

    experiment_directory = (
        arguments.experiments_directory
        / run_id
    )

    monitoring_path = (
        pipeline_directory
        / "monitoring.json"
    )

    decision_path = (
        pipeline_directory
        / "decision.json"
    )

    execution_path = (
        pipeline_directory
        / "execution.json"
    )

    manifest_path = (
        pipeline_directory
        / "pipeline.json"
    )

    latest_manifest_path = (
        arguments.pipelines_directory
        / "latest-pipeline.json"
    )

    if pipeline_directory.exists():
        print(
            "ERROR: Pipeline directory already exists: "
            f"{pipeline_directory}",
            file=sys.stderr,
        )
        return 2

    if experiment_directory.exists():
        print(
            "ERROR: Experiment directory already exists: "
            f"{experiment_directory}",
            file=sys.stderr,
        )
        return 2

    if not arguments.benchmark_profile.is_file():
        print(
            "ERROR: Benchmark profile does not exist: "
            f"{arguments.benchmark_profile}",
            file=sys.stderr,
        )
        return 2

    pipeline_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    pipeline_started_at = utc_now(milliseconds=True)
    pipeline_started_perf = time.perf_counter()

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "phase": "end-to-end-integration",
        "status": "running",
        "started_at": pipeline_started_at,
        "completed_at": None,
        "total_duration_ms": None,
        "benchmarking_performed": False,
        "benchmark_profile": str(
            arguments.benchmark_profile.resolve()
        ),
        "paths": {
            "pipeline_directory": str(
                pipeline_directory.resolve()
            ),
            "monitoring": str(
                monitoring_path.resolve()
            ),
            "decision": str(
                decision_path.resolve()
            ),
            "execution": str(
                execution_path.resolve()
            ),
            "experiment_directory": str(
                experiment_directory.resolve()
            ),
        },
        "components": {},
        "decision": None,
        "execution": None,
        "error": None,
    }

    write_json(
        manifest_path,
        manifest,
    )

    python = sys.executable

    try:
        manifest["components"]["input_validation"] = (
            run_component(
                "Input validation",
                [
                    python,
                    "controller/validate_inputs.py",
                ],
            )
        )

        manifest["components"]["monitoring"] = (
            run_component(
                "Monitoring collection",
                [
                    python,
                    "controller/monitoring.py",
                    "--output",
                    str(monitoring_path),
                ],
            )
        )

        manifest["components"]["placement_policy"] = (
            run_component(
                "Placement policy",
                [
                    python,
                    "controller/policy.py",
                    "--monitoring-snapshot",
                    str(monitoring_path),
                    "--benchmark-profile",
                    str(arguments.benchmark_profile),
                    "--output",
                    str(decision_path),
                ],
            )
        )

        decision = load_json(
            decision_path
        )

        if not decision.get("selected_cluster"):
            raise OrchestrationError(
                "Placement policy did not select a cluster."
            )

        deployment_command = [
            python,
            "controller/deploy.py",
            "--decision",
            str(decision_path),
            "--output",
            str(execution_path),
        ]

        if arguments.cleanup_after:
            deployment_command.append(
                "--cleanup-after"
            )

        manifest["components"][
            "deployment_and_execution"
        ] = run_component(
            "Selected-cluster deployment and execution",
            deployment_command,
        )

        execution = load_json(
            execution_path
        )

        if (
            execution.get("decision_id")
            != decision.get("decision_id")
        ):
            raise OrchestrationError(
                "Execution decision ID does not match "
                "the placement decision."
            )

        if (
            execution.get("selected_cluster")
            != decision.get("selected_cluster")
        ):
            raise OrchestrationError(
                "Execution cluster does not match "
                "the placement decision."
            )

        if execution.get(
            "execution_successful"
        ) is not True:
            raise OrchestrationError(
                "Deployment or execution was not successful."
            )

        manifest["components"]["result_collection"] = (
            run_component(
                "Result collection",
                [
                    python,
                    "controller/result_collector.py",
                    "--experiment-id",
                    run_id,
                    "--benchmark-profile",
                    str(arguments.benchmark_profile),
                    "--monitoring-snapshot",
                    str(monitoring_path),
                    "--decision",
                    str(decision_path),
                    "--execution",
                    str(execution_path),
                    "--results-directory",
                    str(
                        arguments.experiments_directory
                    ),
                ],
            )
        )

        if not experiment_directory.is_dir():
            raise OrchestrationError(
                "Result collector did not create "
                "the experiment directory."
            )

        manifest["decision"] = {
            "decision_id": decision.get(
                "decision_id"
            ),
            "decision_mode": decision.get(
                "decision_mode"
            ),
            "selected_cluster": decision.get(
                "selected_cluster"
            ),
            "objective_satisfied": decision.get(
                "objective_satisfied"
            ),
            "reason": decision.get("reason"),
            "timing": decision.get("timing"),
        }

        manifest["execution"] = {
            "execution_id": execution.get(
                "execution_id"
            ),
            "selected_cluster": execution.get(
                "selected_cluster"
            ),
            "execution_successful": execution.get(
                "execution_successful"
            ),
            "deployment_timing": execution.get(
                "deployment_timing"
            ),
            "actual_execution_evaluation": (
                execution.get(
                    "actual_execution_evaluation"
                )
            ),
            "runtime": execution.get("runtime"),
            "placement": execution.get(
                "placement"
            ),
        }

        manifest["status"] = "success"

    except OrchestrationError as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

    finally:
        manifest["completed_at"] = utc_now(
            milliseconds=True
        )

        manifest["total_duration_ms"] = round(
            (
                time.perf_counter()
                - pipeline_started_perf
            )
            * 1000,
            3,
        )

        write_json(
            manifest_path,
            manifest,
        )

        latest_manifest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copyfile(
            manifest_path,
            latest_manifest_path,
        )

    print()
    print("===== Pipeline summary =====")
    print(f"Run ID: {run_id}")
    print(f"Status: {manifest['status']}")

    if manifest.get("decision"):
        print(
            "Selected cluster: "
            f"{manifest['decision']['selected_cluster']}"
        )

        print(
            "Decision mode: "
            f"{manifest['decision']['decision_mode']}"
        )

    if manifest.get("execution"):
        actual = manifest[
            "execution"
        ].get(
            "actual_execution_evaluation",
            {},
        )

        print(
            "Execution successful: "
            f"{manifest['execution']['execution_successful']}"
        )

        print(
            "Actual metric value: "
            f"{actual.get('measured_value')}"
        )

        print(
            "Actual intent satisfied: "
            f"{actual.get('satisfied')}"
        )

    print(
        "Total pipeline time: "
        f"{manifest['total_duration_ms']}ms"
    )

    print(
        "PIPELINE_DIRECTORY="
        f"{pipeline_directory.resolve()}"
    )

    print(
        "EXPERIMENT_DIRECTORY="
        f"{experiment_directory.resolve()}"
    )

    return (
        0
        if manifest["status"] == "success"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
