from __future__ import annotations

import argparse
import json
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
        payload = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {payload}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Cannot reach {url}: {error}") from error

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run randomized, sequential, independent benchmark-function "
            "orchestrations through the REST API."
        )
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8088")
    parser.add_argument(
        "--phase",
        choices=("pilot", "evaluation"),
        default="pilot",
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--run-timeout-seconds", type=float, default=1800)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--submission",
        action="append",
        metavar="FUNCTION=PATH",
        help="Override or add a named submission file.",
    )
    args = parser.parse_args(argv)

    submissions = dict(DEFAULT_EXAMPLES)
    for raw in args.submission or []:
        if "=" not in raw:
            raise SystemExit("--submission must use FUNCTION=PATH")
        name, path = raw.split("=", 1)
        if not name or not path:
            raise SystemExit("--submission must use FUNCTION=PATH")
        submissions[name] = Path(path)

    missing = [str(path) for path in submissions.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"Submission files do not exist: {', '.join(missing)}")

    schedule = build_trial_schedule(
        submissions=submissions,
        repetitions=args.repetitions,
        random_seed=args.random_seed,
    )
    started_at = datetime.now(timezone.utc)
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else (
            CONTROLLER_DIRECTORY
            / "results"
            / "experiment-series"
            / f"{args.phase}-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        )
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "phase": args.phase,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "api_url": args.api_url.rstrip("/"),
        "repetitions": args.repetitions,
        "random_seed": args.random_seed,
        "schedule": [asdict(trial) for trial in schedule],
        "trials": [],
    }
    write_manifest(output, manifest)

    endpoint = f"{args.api_url.rstrip('/')}/v1/orchestrations"
    for trial in schedule:
        print(
            f"[{trial.sequence}/{len(schedule)}] repetition "
            f"{trial.repetition}: {trial.function_name}"
        )
        trial_started = datetime.now(timezone.utc)
        result: dict[str, Any] = {
            **asdict(trial),
            "started_at": trial_started.isoformat(),
            "finished_at": None,
            "run_id": None,
            "state": "submitting",
            "selected_cluster": None,
            "function_url": None,
            "error": None,
        }
        manifest["trials"].append(result)
        write_manifest(output, manifest)

        try:
            accepted = request_json(
                endpoint,
                method="POST",
                body=Path(trial.submission_file).read_bytes(),
                content_type="application/yaml",
            )
            run_id = accepted.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise RuntimeError("Submission response did not include run_id")
            result["run_id"] = run_id
            result["state"] = accepted.get("state", "accepted")
            status_url = f"{endpoint}/{run_id}"
            deadline = time.monotonic() + args.run_timeout_seconds

            while time.monotonic() < deadline:
                status = request_json(status_url)
                result["state"] = status.get("state")
                if result["state"] in {"succeeded", "failed"}:
                    result["selected_cluster"] = status.get("selected_cluster")
                    result["function_url"] = status.get("function_url")
                    result["error"] = status.get("error")
                    break
                time.sleep(args.poll_seconds)
            else:
                raise RuntimeError(
                    f"Run {run_id} did not finish within "
                    f"{args.run_timeout_seconds} seconds"
                )
        except Exception as error:
            result["state"] = "client-failed"
            result["error"] = f"{type(error).__name__}: {error}"

        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_manifest(output, manifest)
        print(
            f"  run={result['run_id']} state={result['state']} "
            f"cluster={result['selected_cluster']}"
        )

        if result["state"] != "succeeded" and not args.continue_on_failure:
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_manifest(output, manifest)
            raise SystemExit(
                f"Experiment stopped after failed trial; manifest: {output}"
            )

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_manifest(output, manifest)
    print(f"Experiment series complete: {output}")


if __name__ == "__main__":
    main()
