from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from controller.benchmarking.models.cluster_benchmark_result import percentile


CONTROLLER_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK_FILE = CONTROLLER_DIRECTORY / "results" / "benchmarks.jsonl"
DEFAULT_OUTPUT_FILE = (
    CONTROLLER_DIRECTORY / "results" / "calibration" / "latency-slos.json"
)
DEFAULT_FUNCTIONS = (
    "dynamic-html",
    "graph-pagerank",
    "gzip-compression",
)


def load_successful_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        raise RuntimeError(f"Benchmark evidence does not exist: {path}")

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Invalid JSON on {path}:{line_number}: {error}"
            ) from error
        if isinstance(record, dict) and record.get("status") == "succeeded":
            records.append(record)

    return records


def load_run_ids_from_manifest(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Cannot read experiment manifest {path}: {error}"
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("trials"), list):
        raise RuntimeError(f"Invalid experiment manifest: {path}")
    return {
        trial["run_id"]
        for trial in payload["trials"]
        if isinstance(trial, dict)
        and trial.get("state") == "succeeded"
        and isinstance(trial.get("run_id"), str)
        and trial["run_id"]
    }


def calibrate_latency_slos(
    records: Iterable[dict[str, Any]],
    *,
    functions: Iterable[str] = DEFAULT_FUNCTIONS,
    run_ids: set[str] | None = None,
    minimum_samples_per_cluster: int = 5,
    distribution_percentile: float = 95.0,
    safety_margin: float = 1.20,
) -> dict[str, Any]:
    if minimum_samples_per_cluster < 1:
        raise ValueError("minimum_samples_per_cluster must be positive")
    if not 0 < distribution_percentile <= 100:
        raise ValueError("distribution_percentile must be in (0, 100]")
    if safety_margin < 1:
        raise ValueError("safety_margin must be at least 1")

    selected_functions = tuple(dict.fromkeys(functions))
    grouped: dict[str, dict[str, list[float]]] = {
        name: defaultdict(list) for name in selected_functions
    }
    versions: dict[str, set[str]] = {name: set() for name in selected_functions}
    body_hashes: dict[str, set[str]] = {name: set() for name in selected_functions}

    for record in records:
        function_name = record.get("function_name")
        if function_name not in grouped:
            continue
        if run_ids is not None and record.get("run_id") not in run_ids:
            continue
        if float(record.get("success_rate", 0)) < 0.95:
            continue

        cluster_name = record.get("cluster_name")
        value = record.get("p95_warm_latency_ms")
        version = record.get("function_version")
        if not isinstance(cluster_name, str) or not cluster_name:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(float(value)) or float(value) <= 0:
            continue
        if isinstance(version, str) and version:
            versions[function_name].add(version)
        body_hash = record.get("request_body_sha256")
        if isinstance(body_hash, str) and body_hash:
            body_hashes[function_name].add(body_hash)
        grouped[function_name][cluster_name].append(float(value))

    results: dict[str, Any] = {}
    for function_name in selected_functions:
        if len(versions[function_name]) > 1:
            raise RuntimeError(
                f"Pilot evidence for {function_name} mixes versions: "
                f"{', '.join(sorted(versions[function_name]))}"
            )
        if len(body_hashes[function_name]) > 1:
            raise RuntimeError(
                f"Pilot evidence for {function_name} mixes request bodies"
            )
        if not grouped[function_name]:
            raise RuntimeError(f"No successful pilot evidence for {function_name}")

        cluster_results: dict[str, Any] = {}
        conservative_cluster_values: list[float] = []
        for cluster_name, samples in sorted(grouped[function_name].items()):
            if len(samples) < minimum_samples_per_cluster:
                raise RuntimeError(
                    f"{function_name}/{cluster_name} has {len(samples)} pilot "
                    f"samples; requires {minimum_samples_per_cluster}"
                )
            observed = percentile(samples, distribution_percentile)
            conservative_cluster_values.append(observed)
            cluster_results[cluster_name] = {
                "sample_count": len(samples),
                "minimum_p95_warm_latency_ms": round(min(samples), 3),
                "median_p95_warm_latency_ms": round(percentile(samples, 50), 3),
                "calibration_percentile_p95_warm_latency_ms": round(
                    observed,
                    3,
                ),
                "maximum_p95_warm_latency_ms": round(max(samples), 3),
            }

        baseline = max(conservative_cluster_values)
        recommended = math.ceil(baseline * safety_margin)
        results[function_name] = {
            "function_version": next(iter(versions[function_name]), None),
            "request_body_sha256": next(
                iter(body_hashes[function_name]),
                None,
            ),
            "clusters": cluster_results,
            "recommended_p95_latency_slo_ms": recommended,
            "intent_binding": (f"benchmark/{function_name}/p95_warm_latency_ms"),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "minimum_success_rate": 0.95,
            "minimum_samples_per_cluster": minimum_samples_per_cluster,
            "distribution_percentile": distribution_percentile,
            "safety_margin": safety_margin,
            "selection": (
                "maximum per-cluster calibration percentile multiplied "
                "by the safety margin and rounded up to milliseconds"
            ),
        },
        "run_ids": sorted(run_ids) if run_ids is not None else None,
        "functions": results,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Recommend per-function p95 SLOs from pilot evidence."
    )
    parser.add_argument(
        "--benchmark-file",
        type=Path,
        default=DEFAULT_BENCHMARK_FILE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
    )
    parser.add_argument(
        "--function",
        action="append",
        dest="functions",
        choices=DEFAULT_FUNCTIONS,
    )
    parser.add_argument("--run-id", action="append", dest="run_ids")
    parser.add_argument("--experiment-manifest", type=Path)
    parser.add_argument("--minimum-samples-per-cluster", type=int, default=5)
    parser.add_argument("--distribution-percentile", type=float, default=95)
    parser.add_argument("--safety-margin", type=float, default=1.20)
    args = parser.parse_args(argv)

    run_ids = set(args.run_ids or [])
    if args.experiment_manifest is not None:
        run_ids.update(load_run_ids_from_manifest(args.experiment_manifest))

    report = calibrate_latency_slos(
        load_successful_records(args.benchmark_file),
        functions=args.functions or DEFAULT_FUNCTIONS,
        run_ids=run_ids or None,
        minimum_samples_per_cluster=args.minimum_samples_per_cluster,
        distribution_percentile=args.distribution_percentile,
        safety_margin=args.safety_margin,
    )

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)

    print(f"Calibration report: {output}")
    for function_name, result in report["functions"].items():
        print(
            f"{function_name}: recommended p95 <= "
            f"{result['recommended_p95_latency_slo_ms']} ms"
        )


if __name__ == "__main__":
    main()
