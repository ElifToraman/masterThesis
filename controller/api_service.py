from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from controller.post_deployment_monitor import (
    PostDeploymentMonitor,
)

logger = logging.getLogger(__name__)

CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CLUSTER_CONFIG_FILE = (
    CONTROLLER_DIRECTORY / "config" / "clusters.yaml"
)
DEFAULT_POLICY_CONFIG_FILE = (
    CONTROLLER_DIRECTORY / "config" / "policy.json"
)
DEFAULT_RUNTIME_CONFIG_FILE = (
    CONTROLLER_DIRECTORY / "config" / "runtime.yaml"
)
MAXIMUM_SUBMISSION_BYTES = 1024 * 1024


class SubmissionValidationError(RuntimeError):
    pass


class OrchestrationBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrchestrationStatus:
    run_id: str
    state: str
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    return_code: int | None
    error: str | None
    submission_file: str
    log_file: str
    decision_file: str
    execution_file: str
    monitoring_summary_file: str


CommandRunner = Callable[[list[str], Path], int]


class OrchestrationManager:
    def __init__(
        self,
        *,
        results_directory: Path,
        cluster_config_file: Path = DEFAULT_CLUSTER_CONFIG_FILE,
        policy_config_file: Path = DEFAULT_POLICY_CONFIG_FILE,
        runtime_config_file: Path = DEFAULT_RUNTIME_CONFIG_FILE,
        command_runner: CommandRunner | None = None,
        enable_post_deployment_monitoring: bool = True,
    ) -> None:
        self.results_directory = results_directory.resolve()
        self.cluster_config_file = cluster_config_file.resolve()
        self.policy_config_file = policy_config_file.resolve()
        self.runtime_config_file = runtime_config_file.resolve()
        self._command_runner = (
            command_runner or self._run_subprocess
        )
        self.enable_post_deployment_monitoring = (
            enable_post_deployment_monitoring
        )
        self._lock = threading.Lock()
        self._active_run_id: str | None = None
        self._monitor_lock = threading.Lock()
        self._monitor: PostDeploymentMonitor | None = None
        self._monitored_run_id: str | None = None

    def submit(self, raw_submission: bytes) -> OrchestrationStatus:
        validate_hello_submission(raw_submission)

        with self._lock:
            if self._active_run_id is not None:
                raise OrchestrationBusyError(
                    "Another orchestration is already running: "
                    f"{self._active_run_id}"
                )

            run_id = uuid.uuid4().hex
            self._active_run_id = run_id

        run_directory = self.results_directory / "runs" / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
        submission_file = run_directory / "submission.yaml"
        submission_file.write_bytes(raw_submission)

        status = self._status(
            run_id=run_id,
            state="accepted",
            submitted_at=_now(),
        )
        self._write_status(status)

        worker = threading.Thread(
            target=self._execute,
            args=(status,),
            name=f"orchestration-{run_id}",
            daemon=True,
        )
        worker.start()
        return status

    def get_status(
        self,
        run_id: str,
    ) -> OrchestrationStatus | None:
        if not _valid_run_id(run_id):
            return None

        status_file = (
            self.results_directory
            / "runs"
            / run_id
            / "status.json"
        )

        if not status_file.is_file():
            return None

        try:
            payload = json.loads(
                status_file.read_text(encoding="utf-8")
            )
            payload.setdefault(
                "monitoring_summary_file",
                str(
                    self.results_directory
                    / "runs"
                    / run_id
                    / "post-deployment"
                    / "latest-summary.json"
                ),
            )
            return OrchestrationStatus(**payload)
        except (
            json.JSONDecodeError,
            TypeError,
        ):
            logger.exception(
                "Invalid status file for run %s",
                run_id,
            )
            return None

    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    def monitored_run_id(self) -> str | None:
        with self._monitor_lock:
            return self._monitored_run_id

    def get_monitoring_summary(
        self,
        run_id: str,
    ) -> dict | None:
        status = self.get_status(run_id)

        if status is None:
            return None

        summary_file = Path(status.monitoring_summary_file)

        if not summary_file.is_file():
            return {
                "run_id": run_id,
                "state": (
                    "waiting-for-deployment"
                    if status.state in {"accepted", "running"}
                    else "not-started"
                ),
                "orchestration_state": status.state,
            }

        try:
            return json.loads(
                summary_file.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            return {
                "run_id": run_id,
                "state": "monitoring-data-invalid",
            }

    def get_run_payload(
        self,
        status: OrchestrationStatus,
    ) -> dict:
        payload = asdict(status)
        payload["status_url"] = (
            f"/v1/orchestrations/{status.run_id}"
        )
        payload["monitoring_url"] = (
            f"/v1/orchestrations/{status.run_id}/monitoring"
        )
        execution_file = Path(status.execution_file)

        if execution_file.is_file():
            try:
                execution = json.loads(
                    execution_file.read_text(
                        encoding="utf-8"
                    )
                )
                payload["selected_cluster"] = execution.get(
                    "cluster_name"
                )
                payload["function_url"] = execution.get("url")
            except json.JSONDecodeError:
                payload["execution_evidence_error"] = (
                    "execution.json is invalid"
                )

        return payload

    def _execute(
        self,
        accepted_status: OrchestrationStatus,
    ) -> None:
        run_id = accepted_status.run_id
        running = self._status(
            run_id=run_id,
            state="running",
            submitted_at=accepted_status.submitted_at,
            started_at=_now(),
        )
        self._write_status(running)

        command = [
            sys.executable,
            "-m",
            "controller.orchestrator",
            "--submission",
            running.submission_file,
            "--cluster-config",
            str(self.cluster_config_file),
            "--policy-config",
            str(self.policy_config_file),
            "--runtime-config",
            str(self.runtime_config_file),
            "--run-id",
            run_id,
        ]

        return_code: int | None = None
        error: str | None = None

        try:
            return_code = self._command_runner(
                command,
                Path(running.log_file),
            )
            if return_code != 0:
                error = (
                    "Orchestrator exited with return code "
                    f"{return_code}; see {running.log_file}"
                )
        except Exception as caught:
            logger.exception(
                "Orchestration run %s failed to execute",
                run_id,
            )
            error = f"{type(caught).__name__}: {caught}"

        final = self._status(
            run_id=run_id,
            state=(
                "succeeded"
                if return_code == 0 and error is None
                else "failed"
            ),
            submitted_at=accepted_status.submitted_at,
            started_at=running.started_at,
            finished_at=_now(),
            return_code=return_code,
            error=error,
        )
        self._write_status(final)

        if (
            final.state == "succeeded"
            and self.enable_post_deployment_monitoring
        ):
            try:
                self._start_monitoring(run_id)
            except Exception as monitoring_error:
                logger.exception(
                    "Could not start post-deployment monitoring "
                    "for run %s",
                    run_id,
                )
                self._write_monitoring_failure(
                    run_id,
                    monitoring_error,
                )

        with self._lock:
            if self._active_run_id == run_id:
                self._active_run_id = None

    def resume_latest_monitoring(self) -> str | None:
        if not self.enable_post_deployment_monitoring:
            return None

        statuses: list[OrchestrationStatus] = []

        for status_file in self.results_directory.glob(
            "runs/*/status.json"
        ):
            status = self.get_status(status_file.parent.name)

            if (
                status is not None
                and status.state == "succeeded"
                and Path(status.execution_file).is_file()
            ):
                statuses.append(status)

        if not statuses:
            return None

        latest = max(
            statuses,
            key=lambda status: (
                status.finished_at or status.submitted_at
            ),
        )
        self._start_monitoring(latest.run_id)
        return latest.run_id

    def shutdown(self) -> None:
        with self._monitor_lock:
            monitor = self._monitor
            self._monitor = None
            self._monitored_run_id = None

        if monitor is not None:
            monitor.stop()

    def _start_monitoring(self, run_id: str) -> None:
        from controller.monitoring.monitoring_service import (
            MonitoringService,
        )
        from controller.runtime_config import (
            load_cluster_configs,
            load_runtime_config,
            load_submission,
        )

        run_directory = self.results_directory / "runs" / run_id
        execution = json.loads(
            (run_directory / "execution.json").read_text(
                encoding="utf-8"
            )
        )
        submission = load_submission(
            run_directory / "submission.yaml"
        )
        clusters = load_cluster_configs(
            self.cluster_config_file
        )
        runtime_config = load_runtime_config(
            self.runtime_config_file
        )
        cluster_name = execution["cluster_name"]
        cluster = clusters.get(cluster_name)

        if cluster is None:
            raise RuntimeError(
                "Execution selected an unknown cluster: "
                f"{cluster_name}"
            )

        monitoring_properties = runtime_config[
            "postDeploymentMonitoring"
        ]

        monitoring_service = MonitoringService(
            vms=[cluster.create_vm()],
            output_directory=(
                run_directory
                / "post-deployment"
                / "raw-metrics"
            ),
        )
        monitor = PostDeploymentMonitor(
            run_id=run_id,
            submission=submission,
            cluster_name=cluster_name,
            url=str(execution["url"]),
            snapshot_collector=(
                monitoring_service.collect_snapshot
            ),
            output_directory=(
                run_directory / "post-deployment"
            ),
            interval_seconds=float(
                monitoring_properties.get(
                    "intervalSeconds",
                    10,
                )
            ),
            window_size=int(
                monitoring_properties.get(
                    "windowSize",
                    10,
                )
            ),
            minimum_samples=int(
                monitoring_properties.get(
                    "minimumSamples",
                    3,
                )
            ),
            request_timeout_seconds=float(
                monitoring_properties.get(
                    "requestTimeoutSeconds",
                    5,
                )
            ),
        )

        with self._monitor_lock:
            previous = self._monitor
            self._monitor = monitor
            self._monitored_run_id = run_id

        if previous is not None:
            previous.stop()

        monitor.start()
        logger.info(
            "Continuous post-deployment monitoring started "
            "for run %s on %s",
            run_id,
            cluster_name,
        )

    def _write_monitoring_failure(
        self,
        run_id: str,
        error: Exception,
    ) -> None:
        output_file = (
            self.results_directory
            / "runs"
            / run_id
            / "post-deployment"
            / "latest-summary.json"
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(
                {
                    "timestamp": _now(),
                    "run_id": run_id,
                    "state": "monitoring-failed",
                    "error": (
                        f"{type(error).__name__}: {error}"
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _run_subprocess(
        self,
        command: list[str],
        log_file: Path,
    ) -> int:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        with log_file.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                command,
                cwd=CONTROLLER_DIRECTORY.parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

        return result.returncode

    def _status(
        self,
        *,
        run_id: str,
        state: str,
        submitted_at: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        return_code: int | None = None,
        error: str | None = None,
    ) -> OrchestrationStatus:
        run_directory = self.results_directory / "runs" / run_id

        return OrchestrationStatus(
            run_id=run_id,
            state=state,
            submitted_at=submitted_at,
            started_at=started_at,
            finished_at=finished_at,
            return_code=return_code,
            error=error,
            submission_file=str(
                run_directory / "submission.yaml"
            ),
            log_file=str(
                run_directory / "orchestrator.log"
            ),
            decision_file=str(
                run_directory / "decision.json"
            ),
            execution_file=str(
                run_directory / "execution.json"
            ),
            monitoring_summary_file=str(
                run_directory
                / "post-deployment"
                / "latest-summary.json"
            ),
        )

    def _write_status(
        self,
        status: OrchestrationStatus,
    ) -> None:
        status_file = (
            self.results_directory
            / "runs"
            / status.run_id
            / "status.json"
        )
        temporary_file = status_file.with_suffix(".tmp")
        temporary_file.write_text(
            json.dumps(asdict(status), indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(status_file)


class ControllerAPIHandler(BaseHTTPRequestHandler):
    manager: OrchestrationManager

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "active_run_id": (
                        self.manager.active_run_id()
                    ),
                    "monitored_run_id": (
                        self.manager.monitored_run_id()
                    ),
                },
            )
            return

        prefix = "/v1/orchestrations/"

        if path.startswith(prefix):
            remainder = path.removeprefix(prefix)

            if remainder.endswith("/monitoring"):
                run_id = remainder.removesuffix(
                    "/monitoring"
                )
                summary = (
                    self.manager.get_monitoring_summary(
                        run_id
                    )
                )

                if summary is None:
                    self._send_error(
                        HTTPStatus.NOT_FOUND,
                        "Orchestration run was not found",
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    summary,
                )
                return

            run_id = remainder
            status = self.manager.get_status(run_id)

            if status is None:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "Orchestration run was not found",
                )
                return

            self._send_json(
                HTTPStatus.OK,
                self.manager.get_run_payload(status),
            )
            return

        self._send_error(
            HTTPStatus.NOT_FOUND,
            "Endpoint not found",
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/v1/orchestrations":
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "Endpoint not found",
            )
            return

        content_length = self.headers.get("Content-Length")

        try:
            length = int(content_length or "")
        except ValueError:
            self._send_error(
                HTTPStatus.LENGTH_REQUIRED,
                "A valid Content-Length header is required",
            )
            return

        if length <= 0 or length > MAXIMUM_SUBMISSION_BYTES:
            self._send_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Submission must be between 1 byte and 1 MiB",
            )
            return

        raw_submission = self.rfile.read(length)

        try:
            status = self.manager.submit(raw_submission)
        except SubmissionValidationError as error:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                str(error),
            )
            return
        except OrchestrationBusyError as error:
            self._send_error(
                HTTPStatus.CONFLICT,
                str(error),
            )
            return

        self._send_json(
            HTTPStatus.ACCEPTED,
            {
                **self.manager.get_run_payload(status),
            },
        )

    def log_message(
        self,
        format: str,
        *args,
    ) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
    ) -> None:
        self._send_json(
            status,
            {
                "error": status.phrase,
                "message": message,
            },
        )

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict,
    ) -> None:
        body = (
            json.dumps(payload, indent=2) + "\n"
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header(
            "Content-Type",
            "application/json",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)


def validate_hello_submission(raw_submission: bytes) -> None:
    try:
        from controller.intent_function_parser import (
            IntentFunctionParseError,
            parse_intent_function_payload,
        )

        submission = parse_intent_function_payload(
            raw_submission
        )
    except (
        UnicodeDecodeError,
        KeyError,
        TypeError,
        ValueError,
        IntentFunctionParseError,
    ) as error:
        raise SubmissionValidationError(
            f"Invalid IntentFunction submission: {error}"
        ) from error

    if (
        submission.function.name != "hello"
        or submission.function.service_name != "hello"
    ):
        raise SubmissionValidationError(
            "This controller currently accepts only the "
            "'hello' function with serviceName 'hello'"
        )


def create_server(
    *,
    host: str,
    port: int,
    manager: OrchestrationManager,
) -> ThreadingHTTPServer:
    handler = type(
        "ConfiguredControllerAPIHandler",
        (ControllerAPIHandler,),
        {"manager": manager},
    )
    return ThreadingHTTPServer((host, port), handler)


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id) and all(
        character.isalnum() or character == "-"
        for character in run_id
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Intent controller REST API"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument(
        "--policy-config",
        type=Path,
        default=DEFAULT_POLICY_CONFIG_FILE,
    )
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=DEFAULT_RUNTIME_CONFIG_FILE,
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s: %(message)s"
        ),
    )
    # Fail before opening the listening socket if configuration or
    # the YAML parser dependency is unavailable.
    from controller.runtime_config import (
        load_cluster_configs,
        load_policy_config,
        load_runtime_config,
    )

    load_cluster_configs(args.cluster_config)
    load_policy_config(args.policy_config)
    load_runtime_config(args.runtime_config)

    manager = OrchestrationManager(
        results_directory=(
            CONTROLLER_DIRECTORY / "results"
        ),
        cluster_config_file=args.cluster_config,
        policy_config_file=args.policy_config,
        runtime_config_file=args.runtime_config,
    )
    resumed_run = manager.resume_latest_monitoring()

    if resumed_run is not None:
        logger.info(
            "Resumed post-deployment monitoring for run %s",
            resumed_run,
        )
    server = create_server(
        host=args.host,
        port=args.port,
        manager=manager,
    )

    logger.info(
        "Controller API listening on http://%s:%d",
        args.host,
        args.port,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
