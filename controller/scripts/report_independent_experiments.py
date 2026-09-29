"""Offline reports from saved trials; never submit requests or change SLOs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_benchmarks(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as error:
                raise ValueError(f"Invalid JSON at {path}:{number}") from error
            if not isinstance(record, dict):
                raise ValueError(f"Expected an object at {path}:{number}")
            records.append(record)
    return records


def saved_target(manifest: dict, function: str) -> tuple[float, str]:
    """Read a single <= p95 target from the ORIGINAL submission, not disk YAML."""
    snapshot = manifest["submissions"][function]
    raw = snapshot["yaml"]
    if hashlib.sha256(raw.encode()).hexdigest() != snapshot["sha256"]:
        raise ValueError("saved submission SHA-256 does not match")
    spec = yaml.safe_load(raw)["spec"]
    if spec["function"]["name"] != function:
        raise ValueError("saved submission function does not match trial")
    matches = [
        item for item in spec["intent"].get("objectives", [])
        if item.get("measuredBy") == f"benchmark/{function}/p95_warm_latency_ms"
    ]
    if len(matches) != 1 or matches[0].get("operator") != "<=":
        raise ValueError("report requires one supported <= benchmark p95 objective")
    objective = matches[0]
    factor = {"ms": 1, "seconds": 1000, "s": 1000}.get(objective.get("unit"))
    value = objective.get("value")
    if factor is None or not finite(value) or value <= 0:
        raise ValueError("unsupported target unit or invalid target")
    return value * factor, spec["function"]["version"]


def build_report(manifest: dict, records: list[dict], clusters: list[str]) -> dict:
    if not clusters or len(set(clusters)) != len(clusters):
        raise ValueError("Expected clusters must be nonempty and unique")
    schedule = manifest.get("schedule")
    trials = manifest.get("trials")
    if not isinstance(schedule, list) or not schedule or not isinstance(trials, list):
        raise ValueError("Manifest must have a nonempty schedule and a trials list")
    sequences = [s["sequence"] for s in schedule]
    if len(set(sequences)) != len(sequences):
        raise ValueError("Duplicate sequence in schedule")
    by_sequence = {}
    run_ids = set()
    for trial in trials:
        sequence = trial["sequence"]
        if sequence in by_sequence or sequence not in sequences:
            raise ValueError("Duplicate or unscheduled trial")
        run_id = trial.get("run_id")
        if run_id:
            if run_id in run_ids:
                raise ValueError("Duplicate run ID in manifest")
            run_ids.add(run_id)
        by_sequence[sequence] = trial

    grouped = defaultdict(list)
    for record in records:
        if record.get("run_id") in run_ids:
            grouped[(record["run_id"], record.get("cluster_name"))].append(record)

    warnings = []
    rows = []
    body_hashes = defaultdict(set)
    for scheduled in schedule:
        sequence = scheduled["sequence"]
        function = scheduled["function_name"]
        trial = by_sequence.get(sequence, {})
        if trial and any(trial.get(key) != scheduled.get(key)
                         for key in ("function_name", "repetition")):
            raise ValueError(f"Trial {sequence} differs from its schedule")
        issues = []
        try:
            target, version = saved_target(manifest, function)
        except (KeyError, TypeError, ValueError, yaml.YAMLError) as error:
            target, version = None, None
            issues.append(f"Cannot verify saved SLO: {error}")
        row = {
            "sequence": sequence, "repetition": scheduled.get("repetition"),
            "function_name": function, "run_id": trial.get("run_id"),
            "orchestration_state": trial.get("state", "not-started"),
            "selected_cluster": trial.get("selected_cluster"),
            "error": trial.get("error"), "target_p95_ms": target,
            "benchmark_results": [], "warnings": issues,
        }
        if row["orchestration_state"] != "succeeded":
            issues.append(f"Orchestration is {row['orchestration_state']}")
        for cluster in clusters:
            candidates = grouped.get((row["run_id"], cluster), [])
            benchmark = {"cluster_name": cluster, "status": "missing",
                         "p95_target_outcome": "undetermined"}
            if len(candidates) != 1:
                benchmark["status"] = "duplicate" if candidates else "missing"
                issues.append(f"{cluster}: {len(candidates)} benchmark records; expected one")
            else:
                source = candidates[0]
                for key in ("status", "error", "function_version", "request_body_sha256",
                            "average_warm_latency_ms", "p50_warm_latency_ms",
                            "p95_warm_latency_ms", "successful_requests", "failed_requests",
                            "success_rate", "benchmark_concurrency", "measurement_duration_seconds",
                            "resource_sample_count", "resource_metrics_method",
                            "average_cpu_usage_cores", "peak_cpu_usage_cores",
                            "average_memory_usage_bytes", "peak_memory_usage_bytes"):
                    benchmark[key] = source.get(key)
                valid = source.get("function_name") == function and version is not None
                valid = valid and source.get("function_version") == version
                p95 = source.get("p95_warm_latency_ms")
                if source.get("status") != "succeeded":
                    issues.append(f"{cluster}: benchmark did not succeed")
                elif not valid or not finite(p95):
                    issues.append(f"{cluster}: mismatched identity or invalid p95")
                elif target is not None:
                    benchmark["p95_target_outcome"] = "met" if p95 <= target else "missed"
                for key in ("average_cpu_usage_cores", "average_memory_usage_bytes"):
                    if source.get("status") == "succeeded" and not finite(source.get(key)):
                        issues.append(f"{cluster}: missing/invalid {key}")
                body_hash = source.get("request_body_sha256")
                if source.get("status") == "succeeded":
                    if isinstance(body_hash, str) and body_hash:
                        body_hashes[function].add(body_hash)
                    else:
                        issues.append(f"{cluster}: no request-body hash to verify consistency")
            row["benchmark_results"].append(benchmark)

        monitoring = trial.get("monitoring", {})
        summary = monitoring.get("summary", {})
        runtime = {key: summary.get(key) for key in (
            "timestamp", "window_size", "successful_probes", "failed_probes",
            "average_latency_ms", "p50_latency_ms", "p95_latency_ms",
            "objective_evaluations", "constraint_evaluations", "reevaluation_triggered",
        )}
        runtime.update(outcome="undetermined", p95_target_outcome="undetermined")
        requested = manifest.get("monitoring_policy", {}).get("samples")
        minimum = summary.get("required_window_size")
        size = summary.get("window_size")
        valid_window = all(type(n) is int and n > 0 for n in (requested, minimum, size))
        valid_window = valid_window and size >= max(requested, minimum)
        valid_window = valid_window and monitoring.get("state") == "complete"
        valid_window = valid_window and bool(row["run_id"]) and summary.get("run_id") == row["run_id"]
        valid_window = valid_window and not summary.get("reevaluation_triggered") and not summary.get("reevaluation_run_id")
        if valid_window:
            evaluations = summary.get("objective_evaluations", []) + summary.get("constraint_evaluations", [])
            supported = bool(evaluations) and all(e.get("supported") is True for e in evaluations)
            satisfied = summary.get("intent_satisfied")
            if supported and type(satisfied) is bool:
                evaluated = all(e.get("satisfied") is True for e in evaluations)
                if satisfied == evaluated:
                    runtime["outcome"] = "met" if satisfied else "missed"
                else:
                    issues.append("Runtime aggregate disagrees with individual evaluations")
            if finite(summary.get("p95_latency_ms")) and target is not None:
                runtime["p95_target_outcome"] = "met" if summary["p95_latency_ms"] <= target else "missed"
            else:
                issues.append("Runtime p95 or saved target unavailable")
            if monitoring.get("outcome") != runtime["outcome"]:
                issues.append("Saved runtime outcome disagrees with captured evaluation")
        else:
            issues.append("No valid completed runtime window for this run")
        if runtime["outcome"] == "undetermined":
            issues.append("Runtime requirement outcome is undetermined")
        if runtime["outcome"] == "met" and runtime["p95_target_outcome"] == "missed":
            issues.append("Reported runtime satisfaction differs from saved-target p95 comparison; inspect rounding and bindings")
        row["runtime"] = runtime
        rows.append(row)
        warnings.extend(f"Trial {sequence}: {message}" for message in issues)

    for function, hashes in body_hashes.items():
        if len(hashes) > 1:
            warnings.append(f"{function}: mixed request-body hashes; workloads are not comparable")
    unexpected = sorted({str(cluster) for run_id, cluster in grouped if cluster not in clusters})
    if unexpected:
        warnings.append(f"Unexpected benchmark clusters: {', '.join(unexpected)}")
    if not manifest.get("finished_at"):
        warnings.append("Series has no finish timestamp")
    benchmarks = [b for row in rows for b in row["benchmark_results"]]
    return {
        "schema_version": 1,
        "phase": manifest.get("phase"),
        "started_at": manifest.get("started_at"), "finished_at": manifest.get("finished_at"),
        "expected_clusters": clusters,
        "counts": {
            "scheduled_trials": len(rows),
            "orchestration_states": dict(Counter(row["orchestration_state"] for row in rows)),
            "runtime_outcomes": dict(Counter(row["runtime"]["outcome"] for row in rows)),
            "benchmark_p95_outcomes": dict(Counter(b["p95_target_outcome"] for b in benchmarks)),
            "cluster_selections": dict(Counter(row["selected_cluster"] for row in rows if row["selected_cluster"])),
        },
        "warnings": warnings, "trials": rows,
    }


def cell(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}" if finite(value) else "N/A"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    lines = ["# Independent-function evaluation report", "",
             f"Period: {report['started_at']} to {report['finished_at']}", "",
             "Generated offline from saved evidence; no requests submitted and no SLOs recalibrated.", "",
             "## Counts", ""]
    lines += [f"- {key}: {value}" for key, value in report["counts"].items()]
    lines += ["", "## Runtime results — one row per scheduled trial", "",
              "Latencies are milliseconds from captured periodic probes, not concurrent benchmark traffic.", "",
              "| Trial | Function | State | Cluster | Target p95 | Average | p50 | p95 | Probes OK/failed | Requirements |",
              "|---|---|---|---|---:|---:|---:|---:|---|---|"]
    for row in report["trials"]:
        runtime = row["runtime"]
        values = [row["sequence"], row["function_name"], row["orchestration_state"],
                  row["selected_cluster"], row["target_p95_ms"], runtime["average_latency_ms"],
                  runtime["p50_latency_ms"], runtime["p95_latency_ms"],
                  f"{cell(runtime['successful_probes'])}/{cell(runtime['failed_probes'])}", runtime["outcome"]]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "## Concurrent benchmark results", "",
              "P95 outcome checks only latency. Request failures and resources are reported separately.", "",
              "| Trial | Cluster | Status | Target p95 ms | p95 ms | P95 outcome | Requests OK/failed | Avg CPU cores | Avg memory MiB |",
              "|---|---|---|---:|---:|---|---|---:|---:|"]
    for row in report["trials"]:
        for benchmark in row["benchmark_results"]:
            memory = benchmark.get("average_memory_usage_bytes")
            values = [row["sequence"], benchmark["cluster_name"], benchmark["status"], row["target_p95_ms"],
                      benchmark.get("p95_warm_latency_ms"), benchmark["p95_target_outcome"],
                      f"{cell(benchmark.get('successful_requests'))}/{cell(benchmark.get('failed_requests'))}",
                      benchmark.get("average_cpu_usage_cores"), memory / 1024**2 if finite(memory) else None]
            lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "## Run IDs", ""]
    lines += [f"- Trial {r['sequence']}: `{r['run_id']}`" for r in report["trials"]]
    lines += ["", "## Evidence warnings", ""]
    lines += [f"- {cell(w)}" for w in report["warnings"]] or ["- No structural evidence warnings detected."]
    lines += ["", "## Interpretation limits", "",
              "Orchestration success, benchmark latency compliance, and runtime requirement satisfaction are different checks. "
              "Targets come from the saved submissions, never current intent files. Failed, missing, duplicate, and unfinished trials are not dropped. "
              "This report is not a full audit of raw Prometheus sample freshness or response correctness. "
              "Resource totals include regular pod containers (including the Knative proxy). "
              "A small number of trials/probes does not establish rare-event reliability, optimal placement, or superiority over another policy. "
              "No pooled request percentile or confidence interval is claimed.", "",
              "The evidence directory preserves the manifest and matching benchmark records. "
              "See report.json for input paths and hashes; copy this bundle to an independent backup location. "
              "The original experiment's exact code revision is not recorded by these legacy manifests.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--benchmarks", type=Path, help="Optional raw JSONL; omission is explicitly reported")
    parser.add_argument("--clusters", nargs="+", default=["vm1-cluster", "vm2-cluster"])
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory; existing paths are never overwritten")
    args = parser.parse_args(argv)
    try:
        manifest_path = args.manifest.expanduser().resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = load_benchmarks(args.benchmarks) if args.benchmarks else []
        # Reject non-standard NaN/Infinity before creating a partial output bundle.
        json.dumps([manifest, records], allow_nan=False)
        report = build_report(manifest, records, args.clusters)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["sources"] = {"manifest": {"path": str(manifest_path), "sha256": digest(manifest_path)}}
        if args.benchmarks:
            report["sources"]["benchmarks"] = {"path": str(args.benchmarks.resolve()), "sha256": digest(args.benchmarks)}
        output = args.output_dir.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=False)
        evidence = output / "evidence"
        evidence.mkdir()
        shutil.copyfile(manifest_path, evidence / "manifest.json")
        run_ids = {t["run_id"] for t in report["trials"] if t["run_id"]}
        selected = [r for r in records if r.get("run_id") in run_ids]
        benchmark_copy = evidence / "benchmarks.jsonl"
        benchmark_copy.write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in selected), encoding="utf-8")
        report["evidence_sha256"] = {p.name: digest(p) for p in sorted(evidence.iterdir())}
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (output / "report.md").write_text(render_markdown(report), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Report failed: {error}\n")
    print(f"Report: {output / 'report.md'}")
    print(f"Evidence warnings: {len(report['warnings'])}")


if __name__ == "__main__":
    main()
