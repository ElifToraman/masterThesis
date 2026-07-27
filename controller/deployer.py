from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

import yaml

from controller.image_resolver import resolve_image_for_registry
from controller.models import IntentFunction
from controller.runtime_config import ClusterRuntimeConfig


@dataclass(frozen=True)
class DeploymentResult:
    cluster_name: str
    service_name: str
    namespace: str
    image: str
    url: str


class KnativeDeployer:
    def __init__(
        self,
        clusters: dict[str, ClusterRuntimeConfig],
    ) -> None:
        self._clusters = clusters

    def deploy(
        self,
        *,
        cluster_name: str,
        submission: IntentFunction,
    ) -> DeploymentResult:
        cluster = self._clusters.get(cluster_name)

        if cluster is None:
            raise ValueError(
                f"Unknown cluster {cluster_name!r}"
            )

        image = resolve_image_for_registry(
            image=submission.function.image,
            registry=cluster.image_registry,
        )

        manifest = self._build_ksvc_manifest(
            submission=submission,
            image=image,
        )

        self._kubectl_apply(
            kubernetes_context=cluster.kubernetes_context,
            manifest=manifest,
        )

        self._wait_until_ready(
            kubernetes_context=cluster.kubernetes_context,
            service_name=submission.function.service_name,
            namespace=submission.function.namespace,
        )

        url = self._get_url(
            kubernetes_context=cluster.kubernetes_context,
            service_name=submission.function.service_name,
            namespace=submission.function.namespace,
        )

        return DeploymentResult(
            cluster_name=cluster_name,
            service_name=submission.function.service_name,
            namespace=submission.function.namespace,
            image=image,
            url=url,
        )

    def _build_ksvc_manifest(
        self,
        *,
        submission: IntentFunction,
        image: str,
    ) -> dict:
        properties = submission.intent.properties

        min_scale = str(properties.get("minScale", 0))
        max_scale = str(properties.get("maxScale", 10))
        container_port = int(properties.get("containerPort", 8080))

        annotations = {
            "autoscaling.knative.dev/min-scale": min_scale,
            "autoscaling.knative.dev/max-scale": max_scale,
        }

        return {
            "apiVersion": "serving.knative.dev/v1",
            "kind": "Service",
            "metadata": {
                "name": submission.function.service_name,
                "namespace": submission.function.namespace,
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": annotations,
                    },
                    "spec": {
                        "containers": [
                            {
                                "image": image,
                                "ports": [
                                    {
                                        "containerPort": container_port,
                                    }
                                ],
                            }
                        ],
                    },
                },
            },
        }

    def _kubectl_apply(
        self,
        *,
        kubernetes_context: str,
        manifest: dict,
    ) -> None:
        manifest_yaml = yaml.safe_dump(
            manifest,
            sort_keys=False,
        )

        result = subprocess.run(
            [
                "kubectl",
                "--context",
                kubernetes_context,
                "apply",
                "-f",
                "-",
            ],
            input=manifest_yaml,
            text=True,
            capture_output=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "kubectl apply failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        print(result.stdout.strip())

    def _wait_until_ready(
        self,
        *,
        kubernetes_context: str,
        service_name: str,
        namespace: str,
        timeout_seconds: int = 120,
    ) -> None:
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            result = subprocess.run(
                [
                    "kubectl",
                    "--context",
                    kubernetes_context,
                    "get",
                    "ksvc",
                    service_name,
                    "-n",
                    namespace,
                    "-o",
                    "json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            if result.returncode == 0:
                payload = json.loads(result.stdout)
                conditions = payload.get("status", {}).get("conditions", [])

                for condition in conditions:
                    if (
                        condition.get("type") == "Ready"
                        and condition.get("status") == "True"
                    ):
                        return

            time.sleep(2)

        raise TimeoutError(
            f"Knative Service {service_name!r} did not become Ready "
            f"on {kubernetes_context!r} within "
            f"{timeout_seconds} seconds."
        )

    def _get_url(
        self,
        *,
        kubernetes_context: str,
        service_name: str,
        namespace: str,
    ) -> str:
        result = subprocess.run(
            [
                "kubectl",
                "--context",
                kubernetes_context,
                "get",
                "ksvc",
                service_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.url}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Failed to get Knative Service URL\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        return result.stdout.strip()
