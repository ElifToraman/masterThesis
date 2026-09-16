from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from controller.intent_function_parser import parse_intent_function_payload
from controller.intent_translation import translate_intent
from controller.post_deployment_monitor import (
    PostDeploymentMonitor,
    PostDeploymentSample,
)
from controller.tests.test_intent_translation import (
    VALID_PAYLOAD,
    location_constraint,
)


class NormalizedPostDeploymentMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

    def monitor(self, payload: dict | None = None) -> PostDeploymentMonitor:
        submission = parse_intent_function_payload(
            json.dumps(payload or VALID_PAYLOAD)
        )
        return PostDeploymentMonitor(
            run_id="test-run",
            submission=submission,
            normalized_intent=translate_intent(submission),
            cluster_name="vm1",
            url="http://example.invalid",
            snapshot_collector=lambda: None,
            output_directory=Path(self.temporary_directory.name),
            interval_seconds=1,
            window_size=3,
            minimum_samples=1,
        )

    def test_runtime_measurement_is_converted_to_canonical_unit(self) -> None:
        monitor = self.monitor()
        requirement = monitor.normalized_intent.objectives[0]

        observed = monitor._observed_value(
            requirement,
            {"p95_latency_ms": 40.0},
        )

        self.assertEqual(observed, 0.04)

    def test_human_readable_name_cannot_select_another_measurement(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["name"] = (
            "throughput-cold-start"
        )
        monitor = self.monitor(payload)
        requirement = monitor.normalized_intent.objectives[0]

        observed = monitor._observed_value(
            requirement,
            {
                "p95_latency_ms": 40.0,
                "average_latency_ms": 5.0,
                "success_rate": 1.0,
            },
        )

        self.assertEqual(observed, 0.04)

    def test_evaluation_reports_normalized_semantics(self) -> None:
        monitor = self.monitor()
        evaluation = monitor._evaluate(
            monitor.normalized_intent.objectives[0],
            {"p95_latency_ms": 40.0},
        )

        self.assertEqual(evaluation.metric_id, "application.latency")
        self.assertEqual(evaluation.statistic, "p95")
        self.assertEqual(evaluation.target, 0.05)
        self.assertEqual(evaluation.observed, 0.04)
        self.assertEqual(evaluation.unit, "seconds")
        self.assertTrue(evaluation.supported)
        self.assertTrue(evaluation.satisfied)

    def test_summary_evaluates_runtime_requirement(self) -> None:
        monitor = self.monitor()
        sample = PostDeploymentSample(
            timestamp="2026-09-16T00:00:00+00:00",
            run_id="test-run",
            cluster_name="vm1",
            url="http://example.invalid",
            invocation_succeeded=True,
            status_code=200,
            latency_ms=40.0,
            error=None,
            vm_cpu_usage_percent=10.0,
            vm_memory_usage_percent=20.0,
            vm_available_cpu_cores=2.0,
            vm_available_memory_bytes=1024,
            function_pod_count=1,
            function_pod_cpu_millicores=10.0,
            function_pod_memory_bytes=1024,
            candidate_clusters=[],
        )

        summary = monitor._build_summary([sample])

        self.assertEqual(summary.state, "intent-satisfied")
        self.assertTrue(summary.intent_satisfied)
        self.assertEqual(summary.objective_evaluations[0].observed, 0.04)

    def test_soft_runtime_violation_is_best_effort(self) -> None:
        monitor = self.monitor()
        summary = monitor._build_summary(
            [self.sample(latency_ms=60.0)]
        )
        actions: list[str] = []
        monitor.violation_handler = lambda _: actions.append("triggered")
        monitor.consecutive_violation_windows = 1
        handled = monitor._handle_control_loop(summary)

        self.assertEqual(summary.state, "best-effort")
        self.assertFalse(summary.intent_satisfied)
        self.assertFalse(handled.reevaluation_triggered)
        self.assertEqual(actions, [])

    def test_hard_runtime_violation_triggers_violation_state(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["enforcement"] = (
            "hard"
        )
        summary = self.monitor(payload)._build_summary(
            [self.sample(latency_ms=60.0)]
        )

        self.assertEqual(summary.state, "intent-violated")
        self.assertFalse(summary.intent_satisfied)

    def test_hard_location_violation_is_detected_at_runtime(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["constraints"] = [
            location_constraint(values=["vm2"])
        ]
        summary = self.monitor(payload)._build_summary(
            [self.sample(latency_ms=40.0)]
        )
        evaluation = summary.constraint_evaluations[0]

        self.assertEqual(summary.state, "intent-violated")
        self.assertEqual(evaluation.constraint_type, "location")
        self.assertEqual(evaluation.observed_cluster, "vm1")
        self.assertFalse(evaluation.satisfied)

    def test_soft_location_violation_is_best_effort_at_runtime(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["constraints"] = [
            location_constraint(
                values=["vm2"],
                enforcement="soft",
            )
        ]
        summary = self.monitor(payload)._build_summary(
            [self.sample(latency_ms=40.0)]
        )

        self.assertEqual(summary.state, "best-effort")
        self.assertFalse(summary.intent_satisfied)

    def sample(self, *, latency_ms: float) -> PostDeploymentSample:
        return PostDeploymentSample(
            timestamp="2026-09-16T00:00:00+00:00",
            run_id="test-run",
            cluster_name="vm1",
            url="http://example.invalid",
            invocation_succeeded=True,
            status_code=200,
            latency_ms=latency_ms,
            error=None,
            vm_cpu_usage_percent=10.0,
            vm_memory_usage_percent=20.0,
            vm_available_cpu_cores=2.0,
            vm_available_memory_bytes=1024,
            function_pod_count=1,
            function_pod_cpu_millicores=10.0,
            function_pod_memory_bytes=1024,
            candidate_clusters=[],
        )


if __name__ == "__main__":
    unittest.main()
