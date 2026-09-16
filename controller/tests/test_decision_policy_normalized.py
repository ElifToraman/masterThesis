from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from controller.decision_policy import DecisionPolicy
from controller.intent_function_parser import parse_intent_function_payload
from controller.intent_translation import translate_intent
from controller.monitoring.models import (
    MetricsSnapshot,
    NodeMetrics,
    VMMetrics,
)
from controller.tests.test_intent_translation import (
    VALID_PAYLOAD,
    location_constraint,
)


class NormalizedDecisionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DecisionPolicy(
            benchmark_file=Path("unused-benchmarks.jsonl")
        )

    def requirement(self, payload: dict | None = None):
        submission = parse_intent_function_payload(
            json.dumps(payload or VALID_PAYLOAD)
        )
        return translate_intent(submission).objectives[0]

    def test_placement_measurement_is_converted_to_canonical_unit(self) -> None:
        observed = self.policy._requirement_observed_value(
            requirement=self.requirement(),
            observed_metrics={"benchmark_p95_latency_ms": 40.0},
        )

        self.assertEqual(observed, 0.04)

    def test_compares_canonical_measurement_with_canonical_threshold(
        self,
    ) -> None:
        requirement = self.requirement()

        self.assertTrue(
            self.policy._requirement_satisfied(
                requirement=requirement,
                observed_metrics={"benchmark_p95_latency_ms": 40.0},
            )
        )
        self.assertFalse(
            self.policy._requirement_satisfied(
                requirement=requirement,
                observed_metrics={"benchmark_p95_latency_ms": 60.0},
            )
        )

    def test_human_readable_name_cannot_select_another_measurement(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["name"] = (
            "throughput-cold-start"
        )
        observed = self.policy._requirement_observed_value(
            requirement=self.requirement(payload),
            observed_metrics={
                "benchmark_p95_latency_ms": 40.0,
                "benchmark_first_invocation_latency_ms": 900.0,
                "benchmark_throughput_requests_per_second": 1000.0,
            },
        )

        self.assertEqual(observed, 0.04)

    def test_missing_registered_measurement_is_not_supported(self) -> None:
        observed = self.policy._requirement_observed_value(
            requirement=self.requirement(),
            observed_metrics={"benchmark_p95_latency_ms": None},
        )

        self.assertIsNone(observed)

    def test_weighted_score_uses_normalized_values(self) -> None:
        score = self.policy._weighted_objective_score(
            objectives=(self.requirement(),),
            observed_metrics={"benchmark_p95_latency_ms": 25.0},
        )

        self.assertEqual(score, 0.5)

    def candidate(
        self,
        *,
        enforcement: str,
        latency_ms: float,
        constraints: list[dict] | None = None,
    ):
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["enforcement"] = (
            enforcement
        )
        payload["spec"]["intent"]["constraints"] = constraints or []
        submission = parse_intent_function_payload(json.dumps(payload))
        timestamp = datetime.now(timezone.utc)
        snapshot = MetricsSnapshot(
            timestamp=timestamp,
            vm_metrics={
                "cluster-a": VMMetrics(
                    timestamp=timestamp,
                    cluster_name="cluster-a",
                    host="192.0.2.1",
                    hostname="cluster-a",
                    ssh_latency_ms=1.0,
                    cpu_usage_percent=10.0,
                    memory_total_bytes=8 * 1024**3,
                    memory_available_bytes=6 * 1024**3,
                    memory_used_bytes=2 * 1024**3,
                    memory_usage_percent=25.0,
                    load_average_1m=0.1,
                    load_average_5m=0.1,
                    load_average_15m=0.1,
                    cpu_core_count=4,
                )
            },
            node_metrics={
                "node-a": NodeMetrics(
                    timestamp=timestamp,
                    cluster_name="cluster-a",
                    prometheus_query_latency_ms=1.0,
                    node_name="node-a",
                    node_role="worker",
                    prometheus_instance="node-a:9100",
                    cpu_usage_percent=10.0,
                    cpu_core_count=4,
                    memory_total_bytes=8 * 1024**3,
                    memory_available_bytes=6 * 1024**3,
                    memory_used_bytes=2 * 1024**3,
                    memory_usage_percent=25.0,
                    load_average_1m=0.1,
                    load_average_5m=0.1,
                    load_average_15m=0.1,
                )
            },
            pod_metrics={},
        )
        benchmark = {
            "success_rate": 1.0,
            "average_warm_latency_ms": latency_ms,
            "p50_warm_latency_ms": latency_ms,
            "p95_warm_latency_ms": latency_ms,
            "first_invocation_latency_ms": latency_ms,
            "deployment_duration_ms": 1000.0,
            "average_cpu_usage_cores": 0.1,
            "peak_cpu_usage_cores": 0.2,
            "average_memory_usage_bytes": 64 * 1024**2,
            "peak_memory_usage_bytes": 96 * 1024**2,
        }

        return self.policy._build_candidate(
            cluster_name="cluster-a",
            submission=submission,
            normalized_intent=translate_intent(submission),
            snapshot=snapshot,
            benchmark=benchmark,
        )

    def test_hard_requirement_violation_rejects_candidate(self) -> None:
        candidate = self.candidate(
            enforcement="hard",
            latency_ms=60.0,
        )

        self.assertFalse(candidate.feasible)
        self.assertIn(
            "hard_requirement_violated:hello-p95-latency",
            candidate.rejection_reasons,
        )

    def test_soft_requirement_violation_keeps_candidate_feasible(self) -> None:
        candidate = self.candidate(
            enforcement="soft",
            latency_ms=60.0,
        )

        self.assertTrue(candidate.feasible)
        self.assertFalse(candidate.intent_satisfied)
        self.assertEqual(candidate.objective_score, 1.0)

    def test_hard_location_constraint_rejects_other_cluster(self) -> None:
        candidate = self.candidate(
            enforcement="hard",
            latency_ms=40.0,
            constraints=[
                location_constraint(values=["vm2-cluster"])
            ],
        )

        self.assertFalse(candidate.feasible)
        self.assertIn(
            "hard_requirement_violated:hello-location",
            candidate.rejection_reasons,
        )

    def test_soft_location_constraint_penalizes_other_cluster(self) -> None:
        candidate = self.candidate(
            enforcement="hard",
            latency_ms=40.0,
            constraints=[
                location_constraint(
                    values=["vm2-cluster"],
                    enforcement="soft",
                )
            ],
        )

        self.assertTrue(candidate.feasible)
        self.assertFalse(candidate.intent_satisfied)
        self.assertEqual(candidate.objective_score, 1.0)

    def test_location_constraint_accepts_named_cluster(self) -> None:
        candidate = self.candidate(
            enforcement="hard",
            latency_ms=40.0,
            constraints=[
                location_constraint(values=["cluster-a"])
            ],
        )

        self.assertTrue(candidate.feasible)
        self.assertTrue(candidate.intent_satisfied)

    def test_location_not_in_excludes_named_cluster(self) -> None:
        candidate = self.candidate(
            enforcement="hard",
            latency_ms=40.0,
            constraints=[
                location_constraint(
                    values=["cluster-a"],
                    operator="notIn",
                )
            ],
        )

        self.assertFalse(candidate.feasible)


if __name__ == "__main__":
    unittest.main()
