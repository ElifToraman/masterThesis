from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from controller.api_service import (
    OrchestrationManager,
    SubmissionValidationError,
    validate_supported_submission,
)
from controller.intent_translation import load_normalized_intent
from controller.tests.test_intent_translation import (
    VALID_PAYLOAD,
    location_constraint,
)


class APINormalizedIntentTests(unittest.TestCase):
    def test_all_benchmark_examples_are_accepted(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"

        for name in (
            "dynamic-html",
            "graph-pagerank",
            "gzip-compression",
        ):
            with self.subTest(function=name):
                submission, normalized = validate_supported_submission(
                    (examples / f"{name}-intent-function.yaml").read_bytes()
                )
                self.assertEqual(submission.function.name, name)
                self.assertEqual(
                    normalized.objectives[0].metric_id,
                    "application.latency",
                )

    def test_rejects_function_without_controller_profile(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["function"]["name"] = "unknown"
        payload["spec"]["function"]["serviceName"] = "unknown"
        payload["spec"]["intent"]["targetRef"]["name"] = "default/unknown"

        with self.assertRaisesRegex(
            SubmissionValidationError,
            "Unsupported function 'unknown'",
        ):
            validate_supported_submission(json.dumps(payload).encode("utf-8"))

    def test_rejects_service_name_that_differs_from_profile_name(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["function"]["serviceName"] = "other-service"
        payload["spec"]["intent"]["targetRef"]["name"] = (
            "default/other-service"
        )

        with self.assertRaisesRegex(
            SubmissionValidationError,
            "serviceName must equal",
        ):
            validate_supported_submission(json.dumps(payload).encode("utf-8"))

    def test_rejects_metric_binding_for_another_function(self) -> None:
        example = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "graph-pagerank-intent-function.yaml"
        )
        payload = example.read_text(encoding="utf-8").replace(
            "benchmark/graph-pagerank/p95_warm_latency_ms",
            "benchmark/hello/p95_warm_latency_ms",
        )

        with self.assertRaisesRegex(
            SubmissionValidationError,
            "measuredBy must match the submitted function",
        ):
            validate_supported_submission(payload.encode("utf-8"))

    def test_submission_persists_and_forwards_normalized_intent(self) -> None:
        commands: list[list[str]] = []
        command_received = threading.Event()

        def command_runner(command: list[str], _: Path) -> int:
            commands.append(command)
            command_received.set()
            return 1

        source = json.dumps(VALID_PAYLOAD).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            manager = OrchestrationManager(
                results_directory=Path(directory),
                command_runner=command_runner,
                enable_post_deployment_monitoring=False,
            )
            status = manager.submit(source)
            artifact = Path(status.normalized_intent_file)

            self.assertTrue(artifact.is_file())
            normalized = load_normalized_intent(
                artifact,
                source_payload=source,
            )
            self.assertEqual(
                normalized.objectives[0].metric_id,
                "application.latency",
            )
            self.assertTrue(command_received.wait(timeout=2))

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                final_status = manager.get_status(status.run_id)
                if final_status is not None and final_status.state == "failed":
                    break
                time.sleep(0.01)
            else:
                self.fail("orchestration worker did not finish")

        command = commands[0]
        argument_index = command.index("--normalized-intent")
        self.assertEqual(command[argument_index + 1], str(artifact))

    def test_rejects_unknown_location_before_creating_run(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["constraints"] = [
            location_constraint(values=["unknown-cluster"])
        ]

        with tempfile.TemporaryDirectory() as directory:
            results_directory = Path(directory)
            manager = OrchestrationManager(
                results_directory=results_directory,
                command_runner=lambda _command, _directory: 1,
                enable_post_deployment_monitoring=False,
            )

            with self.assertRaisesRegex(
                SubmissionValidationError,
                "unknown cluster names: unknown-cluster",
            ):
                manager.submit(json.dumps(payload).encode("utf-8"))

            self.assertFalse((results_directory / "runs").exists())


if __name__ == "__main__":
    unittest.main()
