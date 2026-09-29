from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from controller.scripts.report_independent_experiments import (
    build_report, load_benchmarks, main, render_markdown,
)


def fixture():
    raw = """spec:
  function: {name: graph-pagerank, version: v1}
  intent:
    objectives:
      - measuredBy: benchmark/graph-pagerank/p95_warm_latency_ms
        operator: '<='
        value: 412
        unit: ms
"""
    trial = {"sequence": 1, "repetition": 1, "function_name": "graph-pagerank",
             "run_id": "run-1", "state": "succeeded", "selected_cluster": "vm1",
             "monitoring": {"state": "complete", "outcome": "met", "summary": {
                 "run_id": "run-1", "window_size": 10, "required_window_size": 3,
                 "successful_probes": 10, "failed_probes": 0, "p95_latency_ms": 300,
                 "intent_satisfied": True, "objective_evaluations": [
                     {"supported": True, "satisfied": True}], "constraint_evaluations": [],
             }}}
    manifest = {"schedule": [{k: trial[k] for k in ("sequence", "repetition", "function_name")}],
                "trials": [trial], "monitoring_policy": {"samples": 10},
                "submissions": {"graph-pagerank": {"yaml": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}},
                "finished_at": "2026-09-29T08:00:00Z"}
    record = {"run_id": "run-1", "cluster_name": "vm1", "function_name": "graph-pagerank",
              "function_version": "v1", "request_body_sha256": "body-hash", "status": "succeeded",
              "p95_warm_latency_ms": 350, "successful_requests": 100, "failed_requests": 0,
              "average_cpu_usage_cores": 1.2, "average_memory_usage_bytes": 40 * 1024**2}
    return manifest, [record, dict(record, cluster_name="vm2")]


class ExperimentReportTests(unittest.TestCase):
    def test_success_and_original_target(self):
        manifest, records = fixture()
        report = build_report(manifest, records, ["vm1", "vm2"])
        self.assertEqual(report["counts"]["benchmark_p95_outcomes"], {"met": 2})
        self.assertEqual(report["counts"]["runtime_outcomes"], {"met": 1})
        self.assertEqual(report["trials"][0]["target_p95_ms"], 412)
        self.assertEqual(report["warnings"], [])
        self.assertIn("40.000", render_markdown(report))

    def test_missing_and_failed_benchmarks_not_dropped(self):
        manifest, records = fixture()
        records[0]["status"] = "failed"
        report = build_report(manifest, records[:1], ["vm1", "vm2"])
        self.assertEqual(report["counts"]["benchmark_p95_outcomes"], {"undetermined": 2})
        self.assertEqual(len(report["trials"][0]["benchmark_results"]), 2)
        self.assertTrue(report["warnings"])

    def test_slo_miss_is_not_orchestration_failure(self):
        manifest, records = fixture()
        records[0]["p95_warm_latency_ms"] = 600
        monitor = manifest["trials"][0]["monitoring"]
        monitor["outcome"] = "missed"
        monitor["summary"].update(p95_latency_ms=600, intent_satisfied=False)
        monitor["summary"]["objective_evaluations"][0]["satisfied"] = False
        report = build_report(manifest, records, ["vm1", "vm2"])
        self.assertEqual(report["counts"]["orchestration_states"], {"succeeded": 1})
        self.assertEqual(report["counts"]["runtime_outcomes"], {"missed": 1})
        self.assertEqual(report["counts"]["benchmark_p95_outcomes"], {"missed": 1, "met": 1})

    def test_duplicate_candidate_not_silently_selected(self):
        manifest, records = fixture()
        report = build_report(manifest, records + [records[0]], ["vm1", "vm2"])
        self.assertEqual(report["trials"][0]["benchmark_results"][0]["status"], "duplicate")

    def test_unrelated_records_ignored(self):
        manifest, records = fixture()
        records.append(dict(records[0], run_id="old-run", p95_warm_latency_ms=9999))
        self.assertEqual(build_report(manifest, records, ["vm1", "vm2"])["warnings"], [])

    def test_unfinished_schedule_preserved(self):
        manifest, records = fixture()
        manifest["schedule"].append(dict(manifest["schedule"][0], sequence=2, repetition=2))
        report = build_report(manifest, records, ["vm1", "vm2"])
        self.assertEqual(report["counts"]["scheduled_trials"], 2)
        self.assertEqual(report["trials"][1]["orchestration_state"], "not-started")

    def test_bad_submission_hash_is_not_a_pass(self):
        manifest, records = fixture()
        manifest["submissions"]["graph-pagerank"]["sha256"] = "changed"
        report = build_report(manifest, records, ["vm1", "vm2"])
        self.assertIsNone(report["trials"][0]["target_p95_ms"])
        self.assertEqual(report["counts"]["benchmark_p95_outcomes"], {"undetermined": 2})

    def test_wrong_version_and_mixed_workloads_warn(self):
        manifest, records = fixture()
        records[0]["function_version"] = "v2"
        records[1]["request_body_sha256"] = "different-body"
        report = build_report(manifest, records, ["vm1", "vm2"])
        self.assertTrue(any("mixed request-body" in warning for warning in report["warnings"]))
        self.assertEqual(report["trials"][0]["benchmark_results"][0]["p95_target_outcome"], "undetermined")

    def test_invalid_runtime_windows_are_not_passes(self):
        for change in ({"run_id": "wrong"}, {"window_size": 3}, {"reevaluation_triggered": True}):
            with self.subTest(change=change):
                manifest, records = fixture()
                manifest["trials"][0]["monitoring"]["summary"].update(change)
                report = build_report(manifest, records, ["vm1", "vm2"])
                self.assertEqual(report["counts"]["runtime_outcomes"], {"undetermined": 1})

    def test_contradictory_or_unsupported_runtime_evidence(self):
        for change in ({"supported": False}, {"satisfied": False}):
            manifest, records = fixture()
            manifest["trials"][0]["monitoring"]["summary"]["objective_evaluations"][0].update(change)
            report = build_report(manifest, records, ["vm1", "vm2"])
            self.assertEqual(report["counts"]["runtime_outcomes"], {"undetermined": 1})

    def test_duplicate_run_rejected(self):
        manifest, records = fixture()
        manifest["schedule"].append(dict(manifest["schedule"][0], sequence=2))
        manifest["trials"].append(dict(manifest["trials"][0], sequence=2))
        with self.assertRaisesRegex(ValueError, "Duplicate run"):
            build_report(manifest, records, ["vm1", "vm2"])

    def test_cli_preserves_inputs_and_refuses_overwrite(self):
        manifest, records = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest))
            benchmarks = root / "source.jsonl"
            benchmarks.write_text("\n".join(json.dumps(r) for r in records))
            self.assertEqual(load_benchmarks(benchmarks), records)
            output = root / "report"
            args = ["--manifest", str(source), "--benchmarks", str(benchmarks),
                    "--clusters", "vm1", "vm2", "--output-dir", str(output)]
            main(args)
            self.assertEqual((output / "evidence/manifest.json").read_bytes(), source.read_bytes())
            self.assertEqual(len(load_benchmarks(output / "evidence/benchmarks.jsonl")), 2)
            with self.assertRaises(SystemExit) as error:
                main(args)
            self.assertEqual(error.exception.code, 2)

    def test_seconds_target(self):
        manifest, records = fixture()
        snapshot = manifest["submissions"]["graph-pagerank"]
        snapshot["yaml"] = snapshot["yaml"].replace("value: 412", "value: 0.412").replace("unit: ms", "unit: seconds")
        snapshot["sha256"] = hashlib.sha256(snapshot["yaml"].encode()).hexdigest()
        self.assertAlmostEqual(build_report(manifest, records, ["vm1", "vm2"])["trials"][0]["target_p95_ms"], 412)

    def test_malformed_jsonl_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad.jsonl"
            source.write_text("{}\nnot-json\n")
            with self.assertRaisesRegex(ValueError, ":2"):
                load_benchmarks(source)


if __name__ == "__main__":
    unittest.main()
