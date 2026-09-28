from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from controller.benchmarking.benchmark_repository import BenchmarkRepository
from controller.benchmarking.benchmark_resource_sampler import (
    BenchmarkResourceSample,
    BenchmarkResourceSampler,
)
from controller.benchmarking.benchmark_service import BenchmarkService
from controller.benchmarking.models import BenchmarkRequest
from controller.function_profiles import load_function_profiles


POD = "graph-pagerank-benchmark-00001-deployment-new"
CONTAINERS = ("user-container", "queue-proxy")
ARGUMENTS = {
    "cluster_name": "vm1-cluster",
    "namespace": "default",
    "benchmark_service_name": "graph-pagerank-benchmark",
}


def row(container, value, pod=POD):
    return {"metric": {"pod": pod, "container": container}, "value": [1000, str(value)]}


def complete_metrics(cpu=0.5):
    return {
        "cpu": [row(name, cpu) for name in CONTAINERS],
        "memory": [row(name, 1024) for name in CONTAINERS],
        "cpu_timestamp": [row(name, 990) for name in CONTAINERS],
        "memory_timestamp": [row(name, 990) for name in CONTAINERS],
    }


def request():
    return BenchmarkRequest(
        run_id="test-resource-metrics",
        function_name="graph-pagerank",
        function_version="v1",
        benchmark_service_name="graph-pagerank-benchmark",
        namespace="default",
        image_reference="registry/graph-pagerank:v1",
        invocation=load_function_profiles()["graph-pagerank"].invocation,
        warmup_requests=0,
        measurement_duration_seconds=60,
    )


class BenchmarkResourceSamplerTests(unittest.TestCase):
    def setUp(self):
        self.vm = Mock()
        self.vm._run_ssh_command.return_value = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": POD},
                            "status": {"phase": "Running"},
                            "spec": {
                                "containers": [{"name": name} for name in CONTAINERS]
                            },
                        }
                    ]
                }
            ),
        )
        self.vm.query_prometheus_many.return_value = complete_metrics()
        self.sampler = BenchmarkResourceSampler({"vm1-cluster": self.vm})
        self.sampler.prepare(**ARGUMENTS)
        clock = patch(
            "controller.benchmarking.benchmark_resource_sampler.time.time",
            return_value=1000,
        )
        clock.start()
        self.addCleanup(clock.stop)

    def test_requires_cpu_and_memory_for_user_and_proxy(self):
        for metric in complete_metrics():
            with self.subTest(metric=metric):
                values = complete_metrics()
                values[metric] = [row("queue-proxy", 1)]
                self.vm.query_prometheus_many.return_value = values
                self.assertIsNone(self.sampler.sample(**ARGUMENTS))

    def test_real_zero_cpu_is_valid_when_observed(self):
        self.vm.query_prometheus_many.return_value = complete_metrics(cpu=0)
        sample = self.sampler.sample(**ARGUMENTS)
        self.assertEqual(sample.cpu_usage_cores, 0)
        self.assertEqual(sample.memory_usage_bytes, 2048)

    def test_deleted_previous_run_does_not_contribute(self):
        values = complete_metrics()
        for rows in values.values():
            rows.append(
                row("user-container", 999999, pod="graph-pagerank-benchmark-old")
            )
        self.vm.query_prometheus_many.return_value = values
        sample = self.sampler.sample(**ARGUMENTS)
        self.assertEqual(sample.cpu_usage_cores, 1)
        self.assertEqual(sample.memory_usage_bytes, 2048)

    def test_rejects_nan_negative_and_stale_observations(self):
        for field, value in [("cpu", "NaN"), ("cpu", -1), ("cpu_timestamp", 900)]:
            with self.subTest(field=field, value=value):
                values = complete_metrics()
                values[field][0] = row("user-container", value)
                self.vm.query_prometheus_many.return_value = values
                self.assertIsNone(self.sampler.sample(**ARGUMENTS))

    def test_no_live_pods_is_an_explicit_error(self):
        self.vm._run_ssh_command.return_value.stdout = '{"items": []}'
        with self.assertRaisesRegex(RuntimeError, "No running benchmark containers"):
            self.sampler.prepare(**ARGUMENTS)

    def test_summarize_missing_samples_does_not_fabricate_zero(self):
        self.assertIsNone(self.sampler.summarize([]))


class BenchmarkResourceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.platform = Mock()
        self.platform.service_exists.return_value = False
        self.platform.get_service_url.return_value = "http://example.invalid"
        self.sampler = Mock()
        self.service = BenchmarkService(self.platform, self.sampler)

    @patch("controller.benchmarking.benchmark_service.time.sleep")
    def test_duplicate_and_partially_updated_scrapes_are_not_new_samples(self, sleep):
        future = Mock()
        future.done.side_effect = [False, False, False, False, True]
        first = BenchmarkResourceSample(0.5, 2048, (10, 10, 10, 10))
        partial = BenchmarkResourceSample(0.5, 2048, (20, 20, 10, 10))
        second = BenchmarkResourceSample(0.6, 2048, (20, 20, 20, 20))
        self.sampler.sample.side_effect = [first, first, partial, second]
        samples = self.service._sample_while_running(
            futures=[future],
            cluster_name="vm1-cluster",
            request=request(),
        )
        self.assertEqual(samples, [first, second])

    @patch("controller.benchmarking.benchmark_service.time.sleep")
    def test_resource_warmup_keeps_load_separate_from_measured_requests(self, sleep):
        self.sampler.sample.side_effect = [
            None,
            BenchmarkResourceSample(0, 10, (10, 10)),
        ]
        with patch.object(
            self.service, "_duration_worker", return_value=([], 4, 0)
        ) as worker:
            self.service._wait_for_resource_metrics(
                cluster_name="vm1-cluster",
                endpoint="http://example.invalid",
                request=request(),
            )
        self.sampler.prepare.assert_called_once_with(**ARGUMENTS)
        self.assertTrue(worker.call_args.args[3].is_set())
        self.assertEqual(self.sampler.sample.call_count, 2)

    def test_resource_warmup_timeout_stops_load(self):
        with patch.object(
            self.service, "_duration_worker", return_value=([], 0, 0)
        ) as worker:
            with patch(
                "controller.benchmarking.benchmark_service.time.monotonic",
                side_effect=[0, 91],
            ):
                with self.assertRaisesRegex(RuntimeError, "Timed out waiting"):
                    self.service._wait_for_resource_metrics(
                        cluster_name="vm1-cluster",
                        endpoint="http://example.invalid",
                        request=request(),
                    )
        self.assertTrue(worker.call_args.args[3].is_set())

    def run_benchmark(self, samples):
        with patch.object(self.service, "_invoke", return_value=(10, 200)):
            with patch.object(
                self.service, "_wait_for_resource_metrics", return_value=20
            ):
                with patch.object(
                    self.service,
                    "_run_measured_load",
                    return_value=([10, 20], 2, 0, 60, samples),
                ):
                    return self.service.benchmark_cluster(
                        "vm1-cluster", "vm1-cluster", request()
                    )

    def test_insufficient_resources_fail_and_still_cleanup(self):
        with self.assertRaisesRegex(
            RuntimeError, "Insufficient complete resource samples"
        ):
            self.run_benchmark([BenchmarkResourceSample(0.5, 2048, (10, 10))])
        self.platform.delete_service.assert_called_once()

    def test_complete_resource_evidence_is_persisted(self):
        samples = [
            BenchmarkResourceSample(cpu, 2048, (i, i))
            for i, cpu in enumerate((0.5, 1, 1.5))
        ]
        self.sampler.summarize.side_effect = BenchmarkResourceSampler.summarize
        result = self.run_benchmark(samples)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmarks.jsonl"
            BenchmarkRepository(path).save(result)
            saved = json.loads(path.read_text())
        self.assertEqual(saved["resource_sample_count"], 3)
        self.assertEqual(saved["resource_warmup_duration_seconds"], 20)
        self.assertEqual(
            saved["resource_metrics_method"], "complete-container-rate-1m-v1"
        )
        self.assertEqual(saved["average_cpu_usage_cores"], 1)
        self.assertEqual(saved["peak_cpu_usage_cores"], 1.5)
        self.platform.delete_service.assert_called_once()


if __name__ == "__main__":
    unittest.main()
