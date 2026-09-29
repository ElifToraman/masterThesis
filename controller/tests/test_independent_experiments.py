from __future__ import annotations

import unittest
import argparse
import copy
import fcntl
import hashlib
import io
import json
import tempfile
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from controller.scripts import run_independent_experiments as runner
from controller.scripts.run_independent_experiments import build_trial_schedule


class IndependentExperimentTests(unittest.TestCase):
    def test_schedule_has_each_function_once_per_repetition(self) -> None:
        submissions = {
            "dynamic-html": Path("dynamic.yaml"),
            "graph-pagerank": Path("graph.yaml"),
            "gzip-compression": Path("gzip.yaml"),
        }
        schedule = build_trial_schedule(
            submissions=submissions,
            repetitions=4,
            random_seed=42,
        )

        self.assertEqual(len(schedule), 12)
        for repetition in range(1, 5):
            names = {
                trial.function_name
                for trial in schedule
                if trial.repetition == repetition
            }
            self.assertEqual(names, set(submissions))

    def test_schedule_is_deterministic_for_seed(self) -> None:
        submissions = {"a": Path("a"), "b": Path("b"), "c": Path("c")}

        first = build_trial_schedule(
            submissions=submissions,
            repetitions=3,
            random_seed=7,
        )
        second = build_trial_schedule(
            submissions=submissions,
            repetitions=3,
            random_seed=7,
        )

        self.assertEqual(first, second)


class ResumableExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "experiment.json"
        payload = "test: original\n"
        self.manifest = {
            "schema_version": 2,
            "api_url": "http://api.test",
            "phase": "evaluation",
            "schedule": [
                {
                    "sequence": 1,
                    "repetition": 1,
                    "function_name": "example",
                    "submission_file": "/unused/example.yaml",
                }
            ],
            "submissions": {
                "example": {
                    "yaml": payload,
                    "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                }
            },
            "trials": [],
            "finished_at": None,
        }
        self.args = argparse.Namespace(
            get_attempts=2,
            retry_seconds=0.01,
            poll_seconds=0.01,
            run_timeout_seconds=1,
            retry_failed=False,
            continue_on_failure=False,
        )
        self.stdout = redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.sleep = patch.object(runner.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def saved_trial(self, state="accepted", run_id="run-1"):
        result = dict(
            self.manifest["schedule"][0],
            state=state,
            run_id=run_id,
            error=None,
            selected_cluster=None,
            function_url=None,
            started_at="start",
            finished_at=None,
        )
        self.manifest["trials"] = [result]
        return result

    def status(self, state="succeeded", run_id="run-1"):
        return {
            "run_id": run_id,
            "state": state,
            "selected_cluster": "vm1-cluster",
            "function_url": "http://function.test",
            "finished_at": "finish",
            "error": "benchmark failed" if state == "failed" else None,
        }

    def run_series(self):
        runner.run_series(self.output, self.manifest, self.args)

    def test_run_id_is_saved_before_poll_and_snapshot_is_submitted(self):
        def request(url, **kwargs):
            if url.endswith("/healthz"):
                return {"status": "ok", "active_run_id": None}
            if kwargs.get("method") == "POST":
                self.assertEqual(kwargs["body"], b"test: original\n")
                self.assertEqual(
                    json.loads(self.output.read_text())["trials"][0]["state"],
                    "submitting",
                )
                return {"run_id": "run-1", "state": "accepted"}
            self.assertEqual(
                json.loads(self.output.read_text())["trials"][0]["run_id"], "run-1"
            )
            return self.status()

        with patch.object(runner, "request_json", side_effect=request):
            self.run_series()
        self.assertEqual(self.manifest["trials"][0]["state"], "succeeded")

    def test_resume_skips_successful_trials_without_network(self):
        self.saved_trial("succeeded")
        with patch.object(runner, "request_json") as request:
            self.run_series()
            request.assert_not_called()

    def test_resume_polls_existing_run_without_post(self):
        self.saved_trial("client-failed")
        with patch.object(
            runner, "request_json", return_value=self.status()
        ) as request:
            self.run_series()
        request.assert_called_once_with("http://api.test/v1/orchestrations/run-1")
        self.assertEqual(self.manifest["trials"][0]["state"], "succeeded")

    def test_transient_poll_failure_is_retried(self):
        self.saved_trial()
        with patch.object(
            runner,
            "request_json",
            side_effect=[
                runner.TransientRequestError("timeout"),
                self.status(),
            ],
        ) as request:
            self.run_series()
        self.assertEqual(request.call_count, 2)

    def test_exhausted_poll_retries_preserve_run_for_later_resume(self):
        self.saved_trial()
        with patch.object(
            runner, "request_json", side_effect=runner.TransientRequestError("timeout")
        ):
            with self.assertRaises(runner.TransientRequestError):
                self.run_series()
        saved = json.loads(self.output.read_text())
        self.assertEqual(saved["trials"][0]["run_id"], "run-1")
        self.assertEqual(saved["trials"][0]["state"], "accepted")
        self.assertEqual(len(saved["client_events"]), 1)

    def test_unknown_post_outcome_is_not_retried_now_or_on_resume(self):
        with patch.object(
            runner,
            "request_json",
            side_effect=[
                {"status": "ok", "active_run_id": None},
                runner.TransientRequestError("reset"),
            ],
        ) as request:
            with self.assertRaises(runner.TransientRequestError):
                self.run_series()
        self.assertEqual(request.call_count, 2)
        with patch.object(runner, "request_json") as request:
            with self.assertRaisesRegex(RuntimeError, "uncertain submission"):
                self.run_series()
            request.assert_not_called()

    def test_failed_trial_requires_explicit_retry(self):
        self.saved_trial("failed")
        with patch.object(
            runner, "request_json", return_value=self.status("failed")
        ) as request:
            with self.assertRaisesRegex(RuntimeError, "--retry-failed"):
                self.run_series()
        self.assertEqual(request.call_count, 1)

    def test_explicit_retry_preserves_failed_attempt(self):
        self.saved_trial("failed")
        self.args.retry_failed = True
        with patch.object(
            runner,
            "request_json",
            side_effect=[
                self.status("failed"),
                {"status": "ok", "active_run_id": None},
                {"run_id": "run-2"},
                self.status(run_id="run-2"),
            ],
        ):
            self.run_series()
        result = self.manifest["trials"][0]
        self.assertEqual(result["run_id"], "run-2")
        self.assertEqual(result["previous_attempts"][0]["run_id"], "run-1")
        self.assertEqual(result["previous_attempts"][0]["state"], "failed")

    def test_retry_flag_does_not_repeat_a_run_that_actually_succeeded(self):
        self.saved_trial("failed")
        self.args.retry_failed = True
        with patch.object(
            runner, "request_json", return_value=self.status()
        ) as request:
            self.run_series()
        self.assertEqual(request.call_count, 1)

    def test_active_unrelated_run_prevents_submission(self):
        with patch.object(
            runner,
            "request_json",
            return_value={
                "status": "ok",
                "active_run_id": "someone-else",
            },
        ) as request:
            with self.assertRaisesRegex(RuntimeError, "active run"):
                self.run_series()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.manifest["trials"], [])

    def test_mismatched_status_id_stops_without_new_post(self):
        self.saved_trial()
        with patch.object(
            runner, "request_json", return_value=self.status(run_id="wrong")
        ):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                self.run_series()

    def test_interrupt_preserves_run_id(self):
        self.saved_trial()
        with patch.object(runner, "request_json", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_series()
        self.assertEqual(
            json.loads(self.output.read_text())["trials"][0]["run_id"], "run-1"
        )

    def test_modified_snapshot_is_rejected_before_network(self):
        self.manifest["submissions"]["example"]["yaml"] = "changed"
        with patch.object(runner, "request_json") as request:
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.run_series()
            request.assert_not_called()

    def test_legacy_resume_is_rejected_without_changing_evidence(self):
        self.manifest["schema_version"] = 1
        runner.write_manifest(self.output, self.manifest)
        original = self.output.read_bytes()
        with self.assertRaisesRegex(SystemExit, "version-2"):
            runner.main(["--resume", str(self.output)])
        self.assertEqual(original, self.output.read_bytes())

    def test_existing_output_is_not_overwritten(self):
        self.output.write_text("original evidence")
        with self.assertRaisesRegex(SystemExit, "Output already exists"):
            runner.main(["--output", str(self.output)])
        self.assertEqual(self.output.read_text(), "original evidence")

    def test_manifest_lock_prevents_concurrent_runner(self):
        with self.output.with_suffix(".json.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(SystemExit, "Another runner"):
                runner.main(["--output", str(self.output)])

    def test_evaluation_phase_uses_frozen_evaluation_examples(self):
        with patch.object(runner, "run_series") as run:
            runner.main(
                [
                    "--phase",
                    "evaluation",
                    "--repetitions",
                    "1",
                    "--output",
                    str(self.output),
                ]
            )
        manifest = run.call_args.args[1]
        self.assertEqual(manifest["phase"], "evaluation")
        self.assertEqual(len(manifest["schedule"]), 3)
        for name, path in runner.EVALUATION_EXAMPLES.items():
            self.assertEqual(manifest["submissions"][name]["yaml"], path.read_text())

    def test_resume_uses_saved_snapshot_even_when_original_file_missing(self):
        runner.write_manifest(self.output, self.manifest)
        with patch.object(runner, "run_series") as run:
            runner.main(["--resume", str(self.output)])
        self.assertEqual(
            run.call_args.args[1]["submissions"], self.manifest["submissions"]
        )

    def test_resume_rejects_schedule_or_endpoint_overrides(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--resume", str(self.output), "--phase", "pilot"])

    def test_cli_series_resumes_after_second_trial_disconnect_without_duplicates(self):
        posted_bodies = []
        disconnect = True

        def request(url, **kwargs):
            if url.endswith("/healthz"):
                return {"status": "ok", "active_run_id": None}
            if kwargs.get("method") == "POST":
                if posted_bodies:
                    prior = json.loads(self.output.read_text())["trials"][-2]
                    self.assertEqual(prior["monitoring"]["state"], "complete")
                posted_bodies.append(kwargs["body"])
                return {"run_id": f"run-{len(posted_bodies)}"}
            if url.endswith("/monitoring"):
                return {
                    "run_id": url.split("/")[-2],
                    "state": "intent-satisfied",
                    "window_size": 10,
                    "required_window_size": 3,
                    "intent_satisfied": True,
                    "objective_evaluations": [{"supported": True, "satisfied": True}],
                }
            run_id = url.rsplit("/", 1)[1]
            if disconnect and run_id == "run-2":
                raise runner.TransientRequestError("connection reset")
            return self.status(run_id=run_id)

        with patch.object(runner, "request_json", side_effect=request):
            with self.assertRaisesRegex(SystemExit, "connection reset"):
                runner.main(
                    [
                        "--phase",
                        "evaluation",
                        "--repetitions",
                        "1",
                        "--get-attempts",
                        "1",
                        "--output",
                        str(self.output),
                    ]
                )
            interrupted = json.loads(self.output.read_text())
            self.assertEqual(interrupted["trials"][0]["state"], "succeeded")
            self.assertEqual(interrupted["trials"][1]["run_id"], "run-2")
            disconnect = False
            runner.main(["--resume", str(self.output)])
        completed = json.loads(self.output.read_text())
        self.assertEqual(len(posted_bodies), 3)
        self.assertEqual(len(set(posted_bodies)), 3)
        self.assertEqual([t["state"] for t in completed["trials"]], ["succeeded"] * 3)
        self.assertIsNotNone(completed["finished_at"])
        self.assertTrue(
            all(t["monitoring"]["outcome"] == "met" for t in completed["trials"])
        )

    def test_poll_timeout_preserves_id(self):
        self.saved_trial()
        with patch.object(runner.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(RuntimeError, "polling timeout"):
                self.run_series()
        self.assertEqual(
            json.loads(self.output.read_text())["trials"][0]["run_id"], "run-1"
        )

    def test_continue_on_failure_keeps_failure_without_retry(self):
        self.saved_trial("failed")
        self.args.continue_on_failure = True
        with patch.object(
            runner, "request_json", return_value=self.status("failed")
        ) as request:
            self.run_series()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.manifest["trials"][0]["state"], "failed")
        self.assertIsNotNone(self.manifest["finished_at"])

    def test_post_response_without_id_remains_uncertain(self):
        with patch.object(
            runner,
            "request_json",
            side_effect=[
                {"status": "ok", "active_run_id": None},
                {"state": "accepted"},
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "did not include run_id"):
                self.run_series()
        self.assertIsNone(self.manifest["trials"][0]["run_id"])
        self.assertEqual(self.manifest["trials"][0]["state"], "submitting")

    def test_connection_reset_is_classified_as_transient(self):
        with patch.object(
            runner.urllib.request, "urlopen", side_effect=ConnectionResetError("reset")
        ):
            with self.assertRaises(runner.TransientRequestError):
                runner.request_json("http://api.test")

    def test_http_503_is_transient_but_404_is_not_retried(self):
        for code, error_type in (
            (503, runner.TransientRequestError),
            (404, RuntimeError),
        ):
            with self.subTest(code=code):
                error = urllib.error.HTTPError(
                    "http://api.test", code, "error", {}, io.BytesIO(b"oops")
                )
                with patch.object(
                    runner.urllib.request, "urlopen", side_effect=error
                ) as request:
                    with self.assertRaises(error_type):
                        runner.get_json(
                            "http://api.test",
                            attempts=1 if code == 503 else 3,
                            retry_seconds=1,
                        )
                self.assertEqual(request.call_count, 1)

    def test_duplicate_trial_is_rejected(self):
        self.saved_trial()
        self.manifest["trials"].append(copy.deepcopy(self.manifest["trials"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            runner.validate_manifest(self.manifest)


class MonitoringWindowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "series.json"
        self.result = {
            "sequence": 1,
            "repetition": 1,
            "function_name": "example",
            "submission_file": "/unused.yaml",
            "run_id": "run-1",
            "state": "succeeded",
        }
        self.manifest = {
            "schema_version": 2,
            "api_url": "http://api.test",
            "monitoring_policy": {"samples": 10, "timeout_seconds": 5},
            "schedule": [
                {
                    k: self.result[k]
                    for k in (
                        "sequence",
                        "repetition",
                        "function_name",
                        "submission_file",
                    )
                }
            ],
            "submissions": {
                "example": {"yaml": "", "sha256": hashlib.sha256(b"").hexdigest()}
            },
            "trials": [self.result],
        }
        self.args = argparse.Namespace(
            get_attempts=2,
            retry_seconds=1,
            poll_seconds=1,
            run_timeout_seconds=5,
            retry_failed=False,
            continue_on_failure=False,
        )
        self.clock = 0

        def sleep(seconds):
            self.clock += seconds

        for patcher in (
            patch.object(runner.time, "monotonic", side_effect=lambda: self.clock),
            patch.object(runner.time, "sleep", side_effect=sleep),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        stdout = redirect_stdout(io.StringIO())
        stdout.__enter__()
        self.addCleanup(stdout.__exit__, None, None, None)

    def summary(self, size=10, met=True, **overrides):
        return {
            "run_id": "run-1",
            "timestamp": "saved-observation-time",
            "state": "intent-satisfied" if met else "best-effort",
            "window_size": size,
            "required_window_size": 3,
            "intent_satisfied": met,
            "p95_latency_ms": 30 if met else 60,
            "successful_probes": size,
            "failed_probes": 0,
            "objective_evaluations": [
                {"supported": True, "satisfied": met, "target": 0.05}
            ],
            **overrides,
        }

    def health(self, **overrides):
        return {
            "status": "ok",
            "active_run_id": None,
            "monitored_run_id": "run-1",
            **overrides,
        }

    def wait(self):
        runner.wait_for_monitoring(self.output, self.manifest, self.result, self.args)

    def test_waits_past_minimum_until_full_window_and_saves_exact_summary(self):
        full = self.summary()
        with patch.object(
            runner, "get_json", side_effect=[self.summary(3), self.health(), full]
        ) as read:
            self.wait()
        self.assertEqual(read.call_count, 3)
        self.assertEqual(self.result["monitoring"]["outcome"], "met")
        saved = json.loads(self.output.read_text())["trials"][0]["monitoring"]
        self.assertEqual(saved["summary"], full)
        self.assertEqual(saved["state"], "complete")

    def test_full_missed_window_is_recorded_without_waiting_for_pass(self):
        with patch.object(
            runner, "get_json", return_value=self.summary(met=False)
        ) as read:
            self.wait()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.result["monitoring"]["outcome"], "missed")
        self.assertEqual(self.result["state"], "succeeded")

    def test_repeated_small_window_times_out_and_preserves_summary(self):
        def read(url, **kwargs):
            return self.health() if url.endswith("healthz") else self.summary(1)

        with patch.object(runner, "get_json", side_effect=read):
            with self.assertRaises(TimeoutError):
                self.wait()
        self.assertEqual(self.clock, 5)
        self.assertEqual(self.result["monitoring"]["state"], "timed-out")
        self.assertEqual(self.result["monitoring"]["summary"]["window_size"], 1)

    def test_resume_completes_monitoring_without_redeploying(self):
        self.result["monitoring"] = {
            "state": "interrupted",
            "attempts": [{"state": "interrupted"}],
        }
        with (
            patch.object(runner, "get_json", return_value=self.summary()) as read,
            patch.object(runner, "request_json") as post,
        ):
            runner.run_series(self.output, self.manifest, self.args)
        post.assert_not_called()
        self.assertTrue(read.call_args.args[0].endswith("/run-1/monitoring"))
        self.assertEqual(len(self.result["monitoring"]["attempts"]), 2)
        self.assertEqual(self.result["monitoring"]["state"], "complete")

    def test_resume_skips_already_captured_window(self):
        self.result["monitoring"] = {"state": "complete", "outcome": "missed"}
        with patch.object(runner, "get_json") as read:
            runner.run_series(self.output, self.manifest, self.args)
        read.assert_not_called()

    def test_monitoring_failure_stops_even_with_continue_on_failure(self):
        self.args.continue_on_failure = True
        with patch.object(
            runner, "get_json", return_value=self.summary(state="monitoring-failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "monitoring-failed"):
                runner.run_series(self.output, self.manifest, self.args)
        self.assertIsNone(self.manifest["finished_at"])
        self.assertEqual(self.result["monitoring"]["state"], "interrupted")

    def test_reevaluation_stops_series_instead_of_mixing_runs(self):
        with patch.object(
            runner, "get_json", return_value=self.summary(reevaluation_run_id="child")
        ):
            with self.assertRaisesRegex(RuntimeError, "re-evaluation"):
                self.wait()
        self.assertEqual(
            self.result["monitoring"]["summary"]["reevaluation_run_id"], "child"
        )

    def test_another_run_taking_over_stops_wait(self):
        with patch.object(
            runner,
            "get_json",
            side_effect=[self.summary(2), self.health(monitored_run_id="other")],
        ):
            with self.assertRaisesRegex(RuntimeError, "taken over"):
                self.wait()

    def test_wrong_run_summary_is_rejected(self):
        with patch.object(
            runner, "get_json", return_value=self.summary(run_id="wrong")
        ):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                self.wait()
        self.assertNotIn("summary", self.result["monitoring"])

    def test_not_started_then_complete_is_supported(self):
        with patch.object(
            runner,
            "get_json",
            side_effect=[
                {"run_id": "run-1", "state": "not-started"},
                self.health(monitored_run_id=None),
                self.summary(),
            ],
        ):
            self.wait()
        self.assertEqual(self.result["monitoring"]["state"], "complete")

    def test_unsupported_evaluation_is_undetermined_not_pass(self):
        with patch.object(
            runner,
            "get_json",
            return_value=self.summary(
                met=False, objective_evaluations=[{"supported": False}]
            ),
        ):
            self.wait()
        self.assertEqual(self.result["monitoring"]["outcome"], "undetermined")

    def test_read_retries_obey_remaining_monitoring_budget(self):
        with patch.object(
            runner, "request_json", side_effect=runner.TransientRequestError("offline")
        ) as request:
            with self.assertRaises(TimeoutError):
                runner.get_json(
                    "http://api.test", attempts=10, retry_seconds=2, deadline=3
                )
        self.assertEqual(self.clock, 3)
        self.assertEqual(
            [c.kwargs["timeout_seconds"] for c in request.call_args_list], [3, 1]
        )

    def test_resume_cannot_override_saved_monitoring_policy(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--resume", str(self.output), "--monitoring-samples", "1"])

    def test_invalid_policy_rejected_before_network(self):
        self.manifest["monitoring_policy"]["samples"] = 0
        with patch.object(runner, "get_json") as read:
            with self.assertRaisesRegex(ValueError, "positive integer"):
                runner.run_series(self.output, self.manifest, self.args)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
