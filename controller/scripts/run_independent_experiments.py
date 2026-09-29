from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import math
import random
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTROLLER_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_EXAMPLES = {
    name: CONTROLLER_DIRECTORY / "examples" / f"{name}-intent-function.yaml"
    for name in ("dynamic-html", "graph-pagerank", "gzip-compression")
}
EVALUATION_EXAMPLES = {
    name: CONTROLLER_DIRECTORY
    / "examples"
    / "evaluation"
    / f"{name}-intent-function.yaml"
    for name in DEFAULT_EXAMPLES
}


class TransientRequestError(RuntimeError):
    """A read-only request may safely be retried after this error."""


@dataclass(frozen=True)
class Trial:
    sequence: int
    repetition: int
    function_name: str
    submission_file: str


def build_trial_schedule(
    *,
    submissions: dict[str, Path],
    repetitions: int,
    random_seed: int,
) -> list[Trial]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not submissions:
        raise ValueError("at least one submission is required")

    generator = random.Random(random_seed)
    schedule: list[Trial] = []
    for repetition in range(1, repetitions + 1):
        names = sorted(submissions)
        generator.shuffle(names)
        for name in names:
            schedule.append(
                Trial(
                    sequence=len(schedule) + 1,
                    repetition=repetition,
                    function_name=name,
                    submission_file=str(submissions[name].resolve()),
                )
            )
    return schedule


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        url=url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        try:
            payload = error.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException):
            payload = "(error response body unavailable)"
        finally:
            error.close()
        error_type = (
            TransientRequestError
            if error.code in {408, 429, 500, 502, 503, 504}
            else RuntimeError
        )
        raise error_type(f"HTTP {error.code} from {url}: {payload}") from error
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        raise TransientRequestError(f"Cannot reach {url}: {error}") from error

    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Non-JSON response from {url}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return value


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_json(
    url: str, *, attempts: int, retry_seconds: float, deadline: float | None = None
) -> dict[str, Any]:
    """Retry transient GET failures only. POST is deliberately never retried."""
    for attempt in range(1, attempts + 1):
        try:
            if deadline is None:
                return request_json(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Monitoring wait timed out")
            return request_json(url, timeout_seconds=min(30, remaining))
        except TransientRequestError:
            if attempt == attempts:
                raise
            print(f"Temporary GET failure; retry {attempt}/{attempts - 1}", flush=True)
            delay = retry_seconds
            if deadline is not None:
                delay = min(delay, max(0, deadline - time.monotonic()))
            time.sleep(delay)
    raise ValueError("attempts must be positive")


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 2:
        raise ValueError(
            "Automatic resume requires a version-2 manifest created by this runner. "
            "Older pilot evidence is unchanged; reconcile older runs manually."
        )
    schedule = manifest["schedule"]
    policy = manifest.get("monitoring_policy")
    if policy is not None:
        if type(policy.get("samples")) is not int or policy["samples"] < 1:
            raise ValueError("Monitoring samples must be a positive integer")
        timeout = policy.get("timeout_seconds")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Monitoring timeout must be positive and finite")
    if not schedule or [t["sequence"] for t in schedule] != list(
        range(1, len(schedule) + 1)
    ):
        raise ValueError("Invalid experiment schedule")
    for trial in schedule:
        snapshot = manifest["submissions"][trial["function_name"]]
        digest = hashlib.sha256(snapshot["yaml"].encode("utf-8")).hexdigest()
        if digest != snapshot["sha256"]:
            raise ValueError("Submission snapshot checksum mismatch")
    seen = set()
    run_ids = set()
    for result in manifest["trials"]:
        sequence = result["sequence"]
        if sequence in seen or not 1 <= sequence <= len(schedule):
            raise ValueError("Duplicate or invalid trial sequence")
        seen.add(sequence)
        if any(result[key] != value for key, value in schedule[sequence - 1].items()):
            raise ValueError("Trial does not match saved schedule")
        run_id = result.get("run_id")
        if run_id:
            if run_id in run_ids:
                raise ValueError("Duplicate run ID in manifest")
            run_ids.add(run_id)
        elif result.get("state") in {"succeeded", "failed"}:
            raise ValueError("Terminal controller result has no run ID")


def wait_for_monitoring(
    output: Path,
    manifest: dict[str, Any],
    result: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    policy = manifest.get("monitoring_policy")
    if policy is None:
        return  # Older manifests retain their original experiment protocol.
    monitoring = result.setdefault("monitoring", {"attempts": []})
    if monitoring.get("state") == "complete":
        return
    monitoring["state"] = "waiting"
    attempt = {"started_at": utc_now(), "state": "waiting"}
    monitoring["attempts"].append(attempt)
    write_manifest(output, manifest)
    print(
        f"  monitoring run={result['run_id']}: waiting for {policy['samples']} probes",
        flush=True,
    )
    deadline = time.monotonic() + policy["timeout_seconds"]
    base = manifest["api_url"].rstrip("/")

    def read(url: str) -> dict[str, Any]:
        return get_json(
            url,
            attempts=args.get_attempts,
            retry_seconds=args.retry_seconds,
            deadline=deadline,
        )

    try:
        while time.monotonic() < deadline:
            summary = read(f"{base}/v1/orchestrations/{result['run_id']}/monitoring")
            if time.monotonic() >= deadline:
                raise TimeoutError("Monitoring wait timed out")
            if summary.get("run_id") != result["run_id"]:
                raise RuntimeError("Monitoring response does not match saved run ID")
            monitoring["summary"] = summary
            write_manifest(output, manifest)
            if summary.get("reevaluation_triggered") or summary.get(
                "reevaluation_run_id"
            ):
                raise RuntimeError(
                    "Control-loop re-evaluation started; inspect its run before continuing"
                )
            state = summary.get("state")
            if state in {"monitoring-failed", "monitoring-data-invalid"}:
                raise RuntimeError(f"Monitoring cannot complete: {state}")
            size = summary.get("window_size", 0)
            minimum = summary.get("required_window_size", 1)
            if (
                type(size) is not int
                or size < 0
                or type(minimum) is not int
                or minimum < 1
            ):
                raise RuntimeError("Invalid monitoring window sizes")
            if size >= max(policy["samples"], minimum) and state in {
                "intent-satisfied",
                "best-effort",
                "intent-violated",
                "no-runtime-requirements",
            }:
                satisfied = summary.get("intent_satisfied")
                evaluations = summary.get("objective_evaluations", []) + summary.get(
                    "constraint_evaluations", []
                )
                supported = bool(evaluations) and all(
                    e.get("supported") is True for e in evaluations
                )
                outcome = "undetermined"
                if supported and type(satisfied) is bool:
                    outcome = "met" if satisfied else "missed"
                monitoring.update(
                    state="complete",
                    completed_at=utc_now(),
                    intent_satisfied=satisfied,
                    outcome=outcome,
                )
                attempt.update(state="complete", finished_at=utc_now())
                write_manifest(output, manifest)
                print(
                    f"  monitoring complete: probes={size}, runtime target={outcome}",
                    flush=True,
                )
                return
            health = read(f"{base}/healthz")
            if (
                health.get("status") != "ok"
                or "monitored_run_id" not in health
                or "active_run_id" not in health
            ):
                raise RuntimeError("Unexpected API monitoring health response")
            if health["monitored_run_id"] not in {None, result["run_id"]} or health[
                "active_run_id"
            ] not in {None, result["run_id"]}:
                raise RuntimeError(
                    "Another run has taken over; monitoring for this trial is incomplete"
                )
            time.sleep(min(args.poll_seconds, max(0, deadline - time.monotonic())))
        raise TimeoutError(
            "Monitoring wait timed out before the requested probe window was complete"
        )
    except (Exception, KeyboardInterrupt) as error:
        state = "timed-out" if isinstance(error, TimeoutError) else "interrupted"
        monitoring["state"] = state
        attempt.update(
            state=state, finished_at=utc_now(), error=f"{type(error).__name__}: {error}"
        )
        write_manifest(output, manifest)
        raise


def run_series(
    output: Path, manifest: dict[str, Any], args: argparse.Namespace
) -> None:
    endpoint = manifest["api_url"].rstrip("/") + "/v1/orchestrations"

    def read(url: str) -> dict[str, Any]:
        return get_json(
            url, attempts=args.get_attempts, retry_seconds=args.retry_seconds
        )

    def poll(result: dict[str, Any]) -> None:
        deadline = time.monotonic() + args.run_timeout_seconds
        while time.monotonic() < deadline:
            status = read(f"{endpoint}/{result['run_id']}")
            if status.get("run_id") != result["run_id"]:
                raise RuntimeError("Status response does not match saved run ID")
            state = status.get("state")
            if state not in {"accepted", "running", "succeeded", "failed"}:
                raise RuntimeError(f"Unexpected controller state: {state!r}")
            result["state"] = state
            if state in {"succeeded", "failed"}:
                for key in ("selected_cluster", "function_url", "error", "finished_at"):
                    result[key] = status.get(key)
                result["finished_at"] = result["finished_at"] or utc_now()
            write_manifest(output, manifest)
            if state in {"succeeded", "failed"}:
                return
            time.sleep(args.poll_seconds)
        raise RuntimeError(
            f"Run {result['run_id']} exceeded the polling timeout; resume to check it again"
        )

    validate_manifest(manifest)
    results = {t["sequence"]: t for t in manifest["trials"]}
    manifest["finished_at"] = None
    write_manifest(output, manifest)
    for trial in manifest["schedule"]:
        result = results.get(trial["sequence"])
        if result and not result.get("run_id"):
            raise RuntimeError(
                f"Trial {trial['sequence']} has an uncertain submission outcome and no saved run ID. "
                "No POST was retried. Inspect API/controller run history before manually reconciling "
                "this manifest; do not start another series blindly."
            )
        try:
            if result:
                # Reconcile even a prior client failure using the authoritative controller state.
                if result["state"] != "succeeded":
                    poll(result)
                if result["state"] == "succeeded":
                    wait_for_monitoring(output, manifest, result, args)
                    print(
                        f"[{trial['sequence']}] trial complete; no resubmission",
                        flush=True,
                    )
                    continue
                if not args.retry_failed:
                    if args.continue_on_failure:
                        continue
                    raise RuntimeError(
                        "Saved trial failed; use --retry-failed to explicitly retry it"
                    )

            health = read(manifest["api_url"].rstrip("/") + "/healthz")
            if health.get("status") != "ok" or "active_run_id" not in health:
                raise RuntimeError("Unexpected API health response")
            if health["active_run_id"] is not None:
                raise RuntimeError(
                    "API has an active run; wait for it before resuming this series"
                )

            previous_attempts = []
            if result:
                previous_attempts = result.get("previous_attempts", []) + [
                    {
                        key: value
                        for key, value in result.items()
                        if key != "previous_attempts"
                    }
                ]
                manifest["trials"].remove(result)
            result = {
                **trial,
                "started_at": utc_now(),
                "finished_at": None,
                "run_id": None,
                "state": "submitting",
                "selected_cluster": None,
                "function_url": None,
                "error": None,
                "previous_attempts": previous_attempts,
            }
            manifest["trials"].append(result)
            manifest["trials"].sort(key=lambda item: item["sequence"])
            write_manifest(output, manifest)
            print(
                f"[{trial['sequence']}/{len(manifest['schedule'])}] {trial['function_name']}",
                flush=True,
            )
            snapshot = manifest["submissions"][trial["function_name"]]
            accepted = request_json(
                endpoint,
                method="POST",
                body=snapshot["yaml"].encode("utf-8"),
                content_type="application/yaml",
            )
            run_id = accepted.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise RuntimeError("Submission response did not include run_id")
            result["run_id"] = run_id
            result["state"] = "accepted"
            write_manifest(output, manifest)  # Persist BEFORE the first status request.
            print(f"  accepted run={run_id}", flush=True)
            poll(result)
            if result["state"] == "succeeded":
                wait_for_monitoring(output, manifest, result, args)
        except (Exception, KeyboardInterrupt) as error:
            # Keep the controller state/run ID intact; a network failure is not a failed run.
            manifest.setdefault("client_events", []).append(
                {
                    "timestamp": utc_now(),
                    "sequence": trial["sequence"],
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            write_manifest(output, manifest)
            raise
        print(
            f"  state={result['state']} cluster={result['selected_cluster']}",
            flush=True,
        )
        if result["state"] != "succeeded" and not args.continue_on_failure:
            raise RuntimeError(
                "Trial failed; inspect evidence before resuming with --retry-failed"
            )

    manifest["finished_at"] = utc_now()
    write_manifest(output, manifest)
    succeeded = sum(t["state"] == "succeeded" for t in manifest["trials"])
    print(
        f"Series finished: {succeeded}/{len(manifest['schedule'])} succeeded. Manifest: {output}"
    )
    if manifest.get("monitoring_policy"):
        outcomes = [t.get("monitoring", {}).get("outcome") for t in manifest["trials"]]
        print(
            f"Runtime monitoring: {outcomes.count('met')} met, "
            f"{outcomes.count('missed')} missed, {outcomes.count('undetermined')} undetermined"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run randomized, sequential, independent benchmark-function "
            "orchestrations through the REST API."
        )
    )
    parser.add_argument("--api-url", default=None)
    parser.add_argument(
        "--phase",
        choices=("pilot", "evaluation"),
        default=None,
    )
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--run-timeout-seconds", type=float, default=1800)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--resume", type=Path, help="Resume a saved version-2 manifest")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry controller-confirmed failed trials on resume",
    )
    parser.add_argument("--get-attempts", type=int, default=5)
    parser.add_argument("--retry-seconds", type=float, default=2)
    parser.add_argument(
        "--monitoring-samples",
        type=int,
        default=None,
        help="Probes required per new trial (default 10, matching the current full window)",
    )
    parser.add_argument(
        "--monitoring-timeout-seconds",
        type=float,
        default=None,
        help="Maximum monitoring wait per trial/resume attempt (default 300 seconds)",
    )
    parser.add_argument(
        "--submission",
        action="append",
        metavar="FUNCTION=PATH",
        help="Override or add a named submission file.",
    )
    args = parser.parse_args(argv)

    if args.get_attempts < 1 or any(
        not math.isfinite(value) or value <= 0
        for value in (args.poll_seconds, args.run_timeout_seconds, args.retry_seconds)
    ):
        parser.error("GET attempts and timing settings must be positive and finite")
    if args.retry_failed and not args.resume:
        parser.error("--retry-failed requires --resume")
    if args.monitoring_samples is not None and args.monitoring_samples < 1:
        parser.error("--monitoring-samples must be positive")
    if args.monitoring_timeout_seconds is not None and (
        not math.isfinite(args.monitoring_timeout_seconds)
        or args.monitoring_timeout_seconds <= 0
    ):
        parser.error("--monitoring-timeout-seconds must be positive and finite")
    if args.resume and any(
        value is not None
        for value in (
            args.output,
            args.api_url,
            args.phase,
            args.repetitions,
            args.random_seed,
            args.submission,
            args.monitoring_samples,
            args.monitoring_timeout_seconds,
        )
    ):
        parser.error(
            "--resume uses the saved URL, phase, schedule, submissions and monitoring policy; do not override them"
        )

    phase = args.phase or "pilot"
    started_at = datetime.now(timezone.utc)
    output = (
        (
            args.resume
            or args.output
            or (
                CONTROLLER_DIRECTORY
                / "results"
                / "experiment-series"
                / f"{phase}-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
            )
        )
        .expanduser()
        .resolve()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    # A sidecar lock serializes writers even when atomic replace changes the manifest inode.
    with output.with_suffix(output.suffix + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another runner is using this manifest") from None
        try:
            if args.resume:
                manifest = json.loads(output.read_text(encoding="utf-8"))
                validate_manifest(manifest)
                if "monitoring_policy" not in manifest:
                    print(
                        "Legacy manifest: preserving its original no-monitoring-wait protocol.",
                        flush=True,
                    )
                manifest.setdefault("resumed_at", []).append(utc_now())
            else:
                if output.exists():
                    raise ValueError(
                        "Output already exists; use --resume or choose a new output path"
                    )
                submissions = dict(
                    EVALUATION_EXAMPLES if phase == "evaluation" else DEFAULT_EXAMPLES
                )
                for raw in args.submission or []:
                    name, separator, path = raw.partition("=")
                    if not separator or not name or not path:
                        raise ValueError("--submission must use FUNCTION=PATH")
                    submissions[name] = Path(path).expanduser()
                repetitions = args.repetitions if args.repetitions is not None else 5
                random_seed = args.random_seed if args.random_seed is not None else 42
                schedule = build_trial_schedule(
                    submissions=submissions,
                    repetitions=repetitions,
                    random_seed=random_seed,
                )
                snapshots = {}
                for name, path in submissions.items():
                    payload = path.read_bytes()
                    snapshots[name] = {
                        "path": str(path.resolve()),
                        "yaml": payload.decode("utf-8"),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                manifest = {
                    "schema_version": 2,
                    "phase": phase,
                    "started_at": started_at.isoformat(),
                    "finished_at": None,
                    "api_url": (args.api_url or "http://127.0.0.1:8088").rstrip("/"),
                    "repetitions": repetitions,
                    "random_seed": random_seed,
                    "schedule": [asdict(trial) for trial in schedule],
                    "trials": [],
                    "submissions": snapshots,
                    "monitoring_policy": {
                        "samples": args.monitoring_samples
                        if args.monitoring_samples is not None
                        else 10,
                        "timeout_seconds": args.monitoring_timeout_seconds
                        if args.monitoring_timeout_seconds is not None
                        else 300,
                    },
                }
                write_manifest(output, manifest)
            print(f"Manifest: {output}", flush=True)
            run_series(output, manifest, args)
        except (Exception, KeyboardInterrupt) as error:
            raise SystemExit(
                f"Experiment stopped: {type(error).__name__}: {error}\n"
                f"Evidence preserved: {output}\n"
                "After checking the cause, use --resume with this manifest."
            ) from error


if __name__ == "__main__":
    main()
