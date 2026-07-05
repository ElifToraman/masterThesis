from __future__ import annotations

import subprocess

from .models import BenchmarkRequest
from .platform import FunctionPlatform


class KnativePlatform(FunctionPlatform):
    def _run_kubectl(
        self,
        kubernetes_context: str,
        arguments: list[str],
        input_text: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "kubectl",
            "--context",
            kubernetes_context,
            *arguments,
        ]

        try:
            result = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )

        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"kubectl command timed out after "
                f"{timeout_seconds} seconds: "
                f"{' '.join(command)}"
            ) from error

        except FileNotFoundError as error:
            raise RuntimeError(
                "kubectl executable was not found"
            ) from error

        if result.returncode != 0:
            raise RuntimeError(
                f"kubectl command failed\n"
                f"Command: {' '.join(command)}\n"
                f"Exit code: {result.returncode}\n"
                f"stdout: {result.stdout.strip()}\n"
                f"stderr: {result.stderr.strip()}"
            )

        return result

    def service_exists(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
    ) -> bool:
        result = self._run_kubectl(
            kubernetes_context=kubernetes_context,
            arguments=[
                "get",
                "ksvc",
                service_name,
                "-n",
                namespace,
                "--ignore-not-found",
                "-o",
                "name",
            ],
        )

        return bool(result.stdout.strip())

    def deploy_service(
        self,
        kubernetes_context: str,
        request: BenchmarkRequest,
    ) -> None:
        raise NotImplementedError

    def wait_until_ready(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
        timeout_seconds: float,
    ) -> None:
        raise NotImplementedError

    def get_service_url(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
    ) -> str:
        raise NotImplementedError

    def delete_service(
        self,
        kubernetes_context: str,
        namespace: str,
        service_name: str,
    ) -> None:
        raise NotImplementedError
