from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.benchmarking.benchmark_service import BenchmarkService
from controller.benchmarking.models import BenchmarkRequest
from controller.execution_validator import validate_deployment
from controller.function_profiles import load_function_profiles
from controller.intent_function_parser import parse_intent_function_payload
from controller.intent_translation import translate_intent
from controller.post_deployment_monitor import PostDeploymentMonitor
from controller.tests.test_intent_translation import VALID_PAYLOAD


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class ProfiledInvocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_function_profiles()["graph-pagerank"].invocation
        self.response = FakeResponse(b'{"benchmark":"graph-pagerank","success":true}')

    @patch("urllib.request.urlopen")
    def test_benchmark_uses_profiled_post_request(self, urlopen) -> None:
        urlopen.return_value = self.response
        request = BenchmarkRequest(
            run_id="run-1",
            function_name="graph-pagerank",
            function_version="v1",
            benchmark_service_name="graph-pagerank-benchmark",
            namespace="default",
            image_reference="registry/graph-pagerank:v1",
            invocation=self.profile,
        )

        _latency, status = BenchmarkService(object())._invoke(
            "http://function.test",
            request,
        )

        sent_request = urlopen.call_args.args[0]
        self.assertEqual(status, 200)
        self.assertEqual(sent_request.method, "POST")
        self.assertEqual(json.loads(sent_request.data), {"seed": 42, "size": 10000})

    @patch("urllib.request.urlopen")
    def test_final_validation_checks_response_contract(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(b'{"benchmark":"wrong","success":true}')

        result = validate_deployment(
            run_id="run-1",
            cluster_name="vm1-cluster",
            service_name="graph-pagerank",
            namespace="default",
            image="registry/graph-pagerank:v1",
            url="http://function.test",
            invocation=self.profile,
            maximum_attempts=1,
        )

        self.assertFalse(result.success)
        self.assertIn("response validation failed", result.error)
        self.assertEqual(result.request_method, "POST")

    @patch("urllib.request.urlopen")
    def test_monitor_uses_profile_and_validates_response(self, urlopen) -> None:
        urlopen.return_value = self.response
        submission = parse_intent_function_payload(json.dumps(VALID_PAYLOAD))

        with tempfile.TemporaryDirectory() as directory:
            monitor = PostDeploymentMonitor(
                run_id="run-1",
                submission=submission,
                normalized_intent=translate_intent(submission),
                cluster_name="vm1-cluster",
                url="http://function.test",
                snapshot_collector=lambda: None,
                output_directory=Path(directory),
                invocation_profile=self.profile,
            )
            succeeded, status, _latency, error = monitor._invoke()

        sent_request = urlopen.call_args.args[0]
        self.assertTrue(succeeded)
        self.assertEqual(status, 200)
        self.assertIsNone(error)
        self.assertEqual(sent_request.method, "POST")


if __name__ == "__main__":
    unittest.main()
