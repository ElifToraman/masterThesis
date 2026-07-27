from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ExecutionValidationResult:
    timestamp: str
    run_id: str
    cluster_name: str
    service_name: str
    namespace: str
    image: str
    url: str
    success: bool
    attempts: int
    status_code: int | None
    latency_ms: float | None
    response_body: str | None
    error: str | None


def validate_deployment(
    *,
    run_id: str,
    cluster_name: str,
    service_name: str,
    namespace: str,
    image: str,
    url: str,
    maximum_attempts: int = 5,
    timeout_seconds: float = 10.0,
    retry_interval_seconds: float = 2.0,
) -> ExecutionValidationResult:
    if maximum_attempts <= 0:
        raise ValueError(
            "maximum_attempts must be greater than zero"
        )

    last_status: int | None = None
    last_latency: float | None = None
    last_body: str | None = None
    last_error: str | None = None

    for attempt in range(1, maximum_attempts + 1):
        started_at = time.perf_counter()

        try:
            with urllib.request.urlopen(
                url,
                timeout=timeout_seconds,
            ) as response:
                body = response.read()
                latency_ms = (
                    time.perf_counter() - started_at
                ) * 1000
                status_code = response.status

                last_status = status_code
                last_latency = round(latency_ms, 3)
                last_body = body.decode(
                    "utf-8",
                    errors="replace",
                )[:4096]
                last_error = None

                if 200 <= status_code < 300:
                    return _result(
                        run_id=run_id,
                        cluster_name=cluster_name,
                        service_name=service_name,
                        namespace=namespace,
                        image=image,
                        url=url,
                        success=True,
                        attempts=attempt,
                        status_code=last_status,
                        latency_ms=last_latency,
                        response_body=last_body,
                        error=None,
                    )

        except urllib.error.HTTPError as error:
            error_body = error.read()
            last_status = error.code
            last_latency = round(
                (
                    time.perf_counter() - started_at
                )
                * 1000,
                3,
            )
            last_body = error_body.decode(
                "utf-8",
                errors="replace",
            )[:4096]
            last_error = f"HTTP {error.code}"
        except (
            TimeoutError,
            urllib.error.URLError,
        ) as error:
            last_status = None
            last_latency = round(
                (
                    time.perf_counter() - started_at
                )
                * 1000,
                3,
            )
            last_body = None
            last_error = str(error)

        if attempt < maximum_attempts:
            time.sleep(retry_interval_seconds)

    return _result(
        run_id=run_id,
        cluster_name=cluster_name,
        service_name=service_name,
        namespace=namespace,
        image=image,
        url=url,
        success=False,
        attempts=maximum_attempts,
        status_code=last_status,
        latency_ms=last_latency,
        response_body=last_body,
        error=last_error or "Deployment invocation failed",
    )


def write_execution_validation(
    *,
    result: ExecutionValidationResult,
    output_file: Path,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(asdict(result), indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(output_file)


def _result(
    *,
    run_id: str,
    cluster_name: str,
    service_name: str,
    namespace: str,
    image: str,
    url: str,
    success: bool,
    attempts: int,
    status_code: int | None,
    latency_ms: float | None,
    response_body: str | None,
    error: str | None,
) -> ExecutionValidationResult:
    return ExecutionValidationResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        cluster_name=cluster_name,
        service_name=service_name,
        namespace=namespace,
        image=image,
        url=url,
        success=success,
        attempts=attempts,
        status_code=status_code,
        latency_ms=latency_ms,
        response_body=response_body,
        error=error,
    )
