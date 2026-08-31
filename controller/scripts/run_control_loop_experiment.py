from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request
from uuid import uuid4

from controller.runtime_config import (
    CONTROLLER_DIRECTORY,
    DEFAULT_CLUSTER_CONFIG_FILE,
    load_cluster_configs,
)


DEFAULT_API_BASE_URL = "http://127.0.0.1:8088"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(url: str, *, timeout_seconds: float = 15) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
            url,
            timeout=timeout_seconds,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {error.code} from {url}: {body}"
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not read {url}: {error}") from error

    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")

    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload) + "\n")


def stress_program(
    *,
    workers: int,
    duration_seconds: float,
    pid_file: str,
) -> str:
    return f'''\
import multiprocessing as mp
import os
import signal
import time

workers = {workers!r}
duration_seconds = {duration_seconds!r}
pid_file = {pid_file!r}
context = mp.get_context("fork")
stop = context.Event()


def burn(stop_event, deadline):
    value = 1
    while not stop_event.is_set() and time.monotonic() < deadline:
        for _ in range(10000):
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF


def request_stop(_signum, _frame):
    stop.set()


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)
deadline = time.monotonic() + duration_seconds
processes = [
    context.Process(target=burn, args=(stop, deadline))
    for _ in range(workers)
]

with open(pid_file, "w", encoding="utf-8") as output:
    output.write(str(os.getpid()))

print(
    f"stress-started pid={{os.getpid()}} workers={{workers}} "
    f"duration_seconds={{duration_seconds}}",
    flush=True,
)

try:
    for process in processes:
        process.start()

    while (
        time.monotonic() < deadline
        and not stop.is_set()
        and any(process.is_alive() for process in processes)
    ):
        time.sleep(0.25)
finally:
    stop.set()

    for process in processes:
        process.join(timeout=2)

    for process in processes:
        if process.is_alive():
            process.terminate()

    for process in processes:
        process.join(timeout=2)

    try:
        os.unlink(pid_file)
    except FileNotFoundError:
        pass

print("stress-finished", flush=True)
'''


def function_load_program(
    *,
    function_url: str,
    concurrency: int,
    work: int,
    duration_seconds: float,
    request_timeout_seconds: float,
) -> str:
    return f'''\
from concurrent.futures import ThreadPoolExecutor
import json
import signal
import threading
import time
import urllib.error
import urllib.request

function_url = {function_url!r}
concurrency = {concurrency!r}
work = {work!r}
duration_seconds = {duration_seconds!r}
request_timeout_seconds = {request_timeout_seconds!r}
separator = "&" if "?" in function_url else "?"
target_url = f"{{function_url}}{{separator}}work={{work}}"
deadline = time.monotonic() + duration_seconds
stop = threading.Event()


def request_stop(_signum, _frame):
    stop.set()


def worker():
    successful = 0
    failed = 0
    latencies = []

    while not stop.is_set() and time.monotonic() < deadline:
        started_at = time.perf_counter()

        try:
            with urllib.request.urlopen(
                target_url,
                timeout=request_timeout_seconds,
            ) as response:
                response.read()

                if 200 <= response.status < 300:
                    successful += 1
                else:
                    failed += 1
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
        ):
            failed += 1

        latencies.append(
            round((time.perf_counter() - started_at) * 1000, 3)
        )

    return successful, failed, latencies


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)
print(
    f"function-load-started concurrency={{concurrency}} work={{work}} "
    f"duration_seconds={{duration_seconds}} target={{target_url}}",
    flush=True,
)

with ThreadPoolExecutor(max_workers=concurrency) as executor:
    results = list(executor.map(lambda _index: worker(), range(concurrency)))

successful = sum(result[0] for result in results)
failed = sum(result[1] for result in results)
latencies = [
    latency
    for result in results
    for latency in result[2]
]
summary = {{
    "event": "function-load-finished",
    "target_url": target_url,
    "concurrency": concurrency,
    "work": work,
    "successful_requests": successful,
    "failed_requests": failed,
    "average_latency_ms": (
        round(sum(latencies) / len(latencies), 3)
        if latencies
        else None
    ),
    "maximum_latency_ms": max(latencies) if latencies else None,
}}
print(json.dumps(summary), flush=True)
'''


def ssh_prefix(*, key: Path, user: str, host: str) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key.expanduser()),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        f"{user}@{host}",
    ]


def compact_monitoring(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": now(),
        "state": payload.get("state"),
        "cluster_name": payload.get("cluster_name"),
        "window_size": payload.get("window_size"),
        "p95_latency_ms": payload.get("p95_latency_ms"),
        "intent_satisfied": payload.get("intent_satisfied"),
        "consecutive_violation_windows": payload.get(
            "consecutive_violation_windows"
        ),
        "required_violation_windows": payload.get(
            "required_violation_windows"
        ),
        "reevaluation_triggered": payload.get(
            "reevaluation_triggered"
        ),
        "reevaluation_run_id": payload.get("reevaluation_run_id"),
    }


def stop_stress(
    *,
    process: subprocess.Popen[str] | None,
    ssh_command: list[str],
    pid_file: str,
) -> tuple[int | None, str, str]:
    if process is None:
        return None, "", ""

    if process.poll() is None:
        quoted_pid_file = shlex.quote(pid_file)
        remote_stop = (
            f"if test -f {quoted_pid_file}; then "
            f"kill -TERM \"$(cat {quoted_pid_file})\"; fi"
        )
        try:
            subprocess.run(
                [*ssh_command, remote_stop],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            pass

    try:
        stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.terminate()

        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()

    return process.returncode, stdout.strip(), stderr.strip()


def stop_local_process(
    process: subprocess.Popen[str] | None,
) -> tuple[int | None, str, str]:
    if process is None:
        return None, "", ""

    if process.poll() is None:
        process.terminate()

    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()

    return process.returncode, stdout.strip(), stderr.strip()


def raise_interrupted(signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt(f"received signal {signum}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a reproducible intent-violation experiment for an "
            "existing successful REST orchestration. The script stresses "
            "the selected physical VM and records the resulting automatic "
            "control-loop run."
        )
    )
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--function-load-concurrency",
        type=int,
        default=4,
    )
    parser.add_argument(
        "--function-work",
        type=int,
        default=8,
        help=(
            "Millions of CPU-loop iterations requested by each hello "
            "load invocation."
        ),
    )
    parser.add_argument(
        "--function-request-timeout-seconds",
        type=float,
        default=60,
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=240,
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600,
    )
    parser.add_argument(
        "--recovery-timeout-seconds",
        type=float,
        default=180,
    )
    parser.add_argument(
        "--require-migration",
        action="store_true",
        help="Return a non-zero status if reevaluation retains placement.",
    )
    args = parser.parse_args(argv)

    for name in (
        "workers",
        "function_load_concurrency",
        "function_work",
        "function_request_timeout_seconds",
        "duration_seconds",
        "poll_seconds",
        "timeout_seconds",
        "recovery_timeout_seconds",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)

    for signal_name in ("SIGHUP", "SIGTERM", "SIGINT"):
        signal_value = getattr(signal, signal_name, None)

        if signal_value is not None:
            signal.signal(signal_value, raise_interrupted)

    api_base_url = args.api_base_url.rstrip("/")
    parent_url = (
        f"{api_base_url}/v1/orchestrations/{args.parent_run_id}"
    )
    parent_monitoring_url = f"{parent_url}/monitoring"
    parent_status = read_json(parent_url)

    if parent_status.get("state") != "succeeded":
        raise RuntimeError(
            "The parent orchestration must be succeeded before the "
            f"experiment starts; state={parent_status.get('state')!r}"
        )

    selected_cluster = parent_status.get("selected_cluster")

    if not isinstance(selected_cluster, str) or not selected_cluster:
        raise RuntimeError("Parent status has no selected_cluster")

    function_url = parent_status.get("function_url")

    if not isinstance(function_url, str) or not function_url:
        raise RuntimeError("Parent status has no function_url")

    clusters = load_cluster_configs(args.cluster_config)
    cluster = clusters.get(selected_cluster)

    if cluster is None:
        raise RuntimeError(
            f"Selected cluster {selected_cluster!r} is not configured"
        )

    parent_directory = (
        CONTROLLER_DIRECTORY
        / "results"
        / "runs"
        / args.parent_run_id
    )

    if not parent_directory.is_dir():
        raise RuntimeError(
            "The parent result directory is unavailable. Run this script "
            "on the controller VM from the checked-out repository: "
            f"{parent_directory}"
        )

    experiment_id = uuid4().hex
    experiment_directory = (
        parent_directory / "experiments" / experiment_id
    )
    evidence_file = experiment_directory / "experiment.json"
    observations_file = experiment_directory / "observations.jsonl"
    pid_file = f"/tmp/intent-control-loop-{experiment_id}.pid"
    remote_program = stress_program(
        workers=args.workers,
        duration_seconds=args.duration_seconds,
        pid_file=pid_file,
    )
    remote_command = f"python3 -c {shlex.quote(remote_program)}"
    ssh_command = ssh_prefix(
        key=cluster.ssh_key,
        user=cluster.ssh_user,
        host=cluster.host,
    )
    full_stress_command = [*ssh_command, remote_command]
    local_load_program = function_load_program(
        function_url=function_url,
        concurrency=args.function_load_concurrency,
        work=args.function_work,
        duration_seconds=args.duration_seconds,
        request_timeout_seconds=(
            args.function_request_timeout_seconds
        ),
    )
    full_function_load_command = [
        sys.executable,
        "-c",
        local_load_program,
    ]
    baseline_monitoring = read_json(parent_monitoring_url)
    evidence: dict[str, Any] = {
        "experiment_id": experiment_id,
        "state": "running",
        "started_at": now(),
        "finished_at": None,
        "parent_run_id": args.parent_run_id,
        "parent_selected_cluster": selected_cluster,
        "parent_function_url": function_url,
        "api_base_url": api_base_url,
        "baseline_monitoring": compact_monitoring(
            baseline_monitoring
        ),
        "stress": {
            "target_cluster": selected_cluster,
            "target_host": cluster.host,
            "ssh_user": cluster.ssh_user,
            "ssh_key": str(cluster.ssh_key.expanduser()),
            "workers": args.workers,
            "duration_seconds": args.duration_seconds,
            "remote_pid_file": pid_file,
            "command_argv": full_stress_command,
            "command": shlex.join(full_stress_command),
            "started_at": None,
            "finished_at": None,
            "return_code": None,
            "stdout": None,
            "stderr": None,
        },
        "function_load": {
            "target_url": function_url,
            "work": args.function_work,
            "concurrency": args.function_load_concurrency,
            "duration_seconds": args.duration_seconds,
            "request_timeout_seconds": (
                args.function_request_timeout_seconds
            ),
            "command_argv": full_function_load_command,
            "command": shlex.join(full_function_load_command),
            "started_at": None,
            "finished_at": None,
            "return_code": None,
            "stdout": None,
            "stderr": None,
        },
        "trigger_observation": None,
        "reevaluation_run_id": None,
        "reevaluation": None,
        "recovery_monitoring": None,
        "outcome": None,
        "error": None,
        "observations_file": str(observations_file),
    }
    write_json(evidence_file, evidence)

    print(f"Experiment ID: {experiment_id}")
    print(f"Evidence: {evidence_file}")
    print(f"Parent run: {args.parent_run_id}")
    print(f"Stress target: {selected_cluster} ({cluster.host})")
    print(
        f"Host stress: {args.workers} workers for at most "
        f"{args.duration_seconds:g} seconds"
    )
    print(
        "Function load: "
        f"concurrency={args.function_load_concurrency} "
        f"work={args.function_work} for at most "
        f"{args.duration_seconds:g} seconds"
    )

    stress_process: subprocess.Popen[str] | None = None
    function_load_process: subprocess.Popen[str] | None = None
    reevaluation_run_id: str | None = None
    experiment_error: str | None = None

    try:
        evidence["stress"]["started_at"] = now()
        write_json(evidence_file, evidence)
        stress_process = subprocess.Popen(
            full_stress_command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        evidence["function_load"]["started_at"] = now()
        write_json(evidence_file, evidence)
        function_load_process = subprocess.Popen(
            full_function_load_command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + args.timeout_seconds
        previous_signature: tuple[Any, ...] | None = None

        while time.monotonic() < deadline:
            monitoring = read_json(parent_monitoring_url)
            observation = compact_monitoring(monitoring)
            append_jsonl(observations_file, observation)
            signature = (
                observation["state"],
                observation["consecutive_violation_windows"],
                observation["reevaluation_run_id"],
            )

            if signature != previous_signature:
                print(
                    "Monitor: "
                    f"state={observation['state']} "
                    f"p95={observation['p95_latency_ms']} ms "
                    "consecutive="
                    f"{observation['consecutive_violation_windows']} "
                    "reevaluation="
                    f"{observation['reevaluation_run_id']}"
                )
                previous_signature = signature

            value = observation.get("reevaluation_run_id")

            if isinstance(value, str) and value:
                reevaluation_run_id = value
                evidence["trigger_observation"] = observation
                evidence["reevaluation_run_id"] = value
                write_json(evidence_file, evidence)
                break

            if stress_process.poll() is not None:
                raise RuntimeError(
                    "Host stress finished before reevaluation was "
                    "triggered"
                )

            if function_load_process.poll() is not None:
                raise RuntimeError(
                    "Function load finished before reevaluation was "
                    "triggered"
                )

            time.sleep(args.poll_seconds)

        if reevaluation_run_id is None:
            raise RuntimeError(
                "Timed out waiting for an automatic reevaluation run"
            )

        print(f"Automatic reevaluation: {reevaluation_run_id}")
        reevaluation_url = (
            f"{api_base_url}/v1/orchestrations/"
            f"{reevaluation_run_id}"
        )

        while time.monotonic() < deadline:
            reevaluation = read_json(reevaluation_url)
            reevaluation_state = reevaluation.get("state")
            print(f"Reevaluation state: {reevaluation_state}")

            if reevaluation_state in {"succeeded", "failed"}:
                evidence["reevaluation"] = reevaluation
                break

            time.sleep(args.poll_seconds)
        else:
            raise RuntimeError(
                "Timed out waiting for reevaluation to finish"
            )

        if evidence["reevaluation"].get("state") != "succeeded":
            raise RuntimeError("Automatic reevaluation failed")

    except KeyboardInterrupt:
        experiment_error = "Experiment interrupted by the user"
    except Exception as error:
        experiment_error = str(error)
    finally:
        load_return_code, load_stdout, load_stderr = (
            stop_local_process(function_load_process)
        )
        evidence["function_load"].update(
            {
                "finished_at": now(),
                "return_code": load_return_code,
                "stdout": load_stdout,
                "stderr": load_stderr,
            }
        )
        return_code, stdout, stderr = stop_stress(
            process=stress_process,
            ssh_command=ssh_command,
            pid_file=pid_file,
        )
        evidence["stress"].update(
            {
                "finished_at": now(),
                "return_code": return_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        )

    if experiment_error is None and reevaluation_run_id is not None:
        recovery_url = (
            f"{api_base_url}/v1/orchestrations/"
            f"{reevaluation_run_id}/monitoring"
        )
        recovery_deadline = (
            time.monotonic() + args.recovery_timeout_seconds
        )

        while time.monotonic() < recovery_deadline:
            try:
                recovery = read_json(recovery_url)
            except RuntimeError:
                time.sleep(args.poll_seconds)
                continue

            recovery_observation = compact_monitoring(recovery)
            evidence["recovery_monitoring"] = recovery_observation

            if recovery_observation["state"] in {
                "intent-satisfied",
                "intent-violated",
                "monitoring-failed",
            }:
                break

            time.sleep(args.poll_seconds)

    reevaluation = evidence.get("reevaluation") or {}
    trigger = reevaluation.get("control_loop_trigger") or {}
    outcome = trigger.get("outcome")

    if experiment_error is not None:
        evidence["state"] = "failed"
        evidence["outcome"] = "experiment-failed"
        evidence["error"] = experiment_error
    elif outcome:
        evidence["state"] = "succeeded"
        evidence["outcome"] = outcome
    else:
        evidence["state"] = "succeeded"
        evidence["outcome"] = "reevaluation-succeeded"

    evidence["finished_at"] = now()
    write_json(evidence_file, evidence)

    print(f"Experiment state: {evidence['state']}")
    print(f"Outcome: {evidence['outcome']}")
    print(
        "Selected cluster after reevaluation: "
        f"{reevaluation.get('selected_cluster')}"
    )
    print(f"Final evidence: {evidence_file}")

    if experiment_error is not None:
        print(f"Error: {experiment_error}")
        return 1

    if args.require_migration and outcome != "migrated":
        print(
            "The control loop completed, but placement was retained "
            "instead of migrated."
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
