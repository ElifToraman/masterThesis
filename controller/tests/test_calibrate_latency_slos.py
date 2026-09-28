from __future__ import annotations

import unittest

from controller.scripts.calibrate_latency_slos import calibrate_latency_slos


def record(function: str, cluster: str, latency: float, run: int) -> dict:
    return {
        "status": "succeeded",
        "run_id": f"run-{run}",
        "function_name": function,
        "function_version": "v1",
        "cluster_name": cluster,
        "success_rate": 1.0,
        "p95_warm_latency_ms": latency,
        "request_body_sha256": f"{function}-body",
    }


class CalibrateLatencySLOTests(unittest.TestCase):
    def test_recommends_slo_from_slowest_cluster_distribution(self) -> None:
        records = []
        for run, latency in enumerate((10, 11, 12, 13, 14), start=1):
            records.append(record("dynamic-html", "vm1", latency, run))
        for run, latency in enumerate((20, 21, 22, 23, 24), start=6):
            records.append(record("dynamic-html", "vm2", latency, run))

        report = calibrate_latency_slos(
            records,
            functions=["dynamic-html"],
            minimum_samples_per_cluster=5,
            distribution_percentile=95,
            safety_margin=1.2,
        )

        result = report["functions"]["dynamic-html"]
        self.assertEqual(result["recommended_p95_latency_slo_ms"], 29)
        self.assertEqual(result["clusters"]["vm1"]["sample_count"], 5)

    def test_rejects_insufficient_pilot_samples(self) -> None:
        records = [record("dynamic-html", "vm1", 10, 1)]

        with self.assertRaisesRegex(RuntimeError, "requires 5"):
            calibrate_latency_slos(
                records,
                functions=["dynamic-html"],
                minimum_samples_per_cluster=5,
            )

    def test_rejects_mixed_function_versions(self) -> None:
        records = [
            record("dynamic-html", "vm1", latency, run)
            for run, latency in enumerate((10, 11, 12, 13, 14), start=1)
        ]
        records[-1]["function_version"] = "v2"

        with self.assertRaisesRegex(RuntimeError, "mixes versions"):
            calibrate_latency_slos(
                records,
                functions=["dynamic-html"],
                minimum_samples_per_cluster=5,
            )


if __name__ == "__main__":
    unittest.main()
