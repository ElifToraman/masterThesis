from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

import yaml

from controller.models import IntentFunction


CLUSTER_IMAGE_REGISTRIES = {
    "vm1-cluster": "host.docker.internal:5000",
    "vm2-cluster": "host.docker.internal:5001",
}


@dataclass(frozen=True)
class DeploymentResult:
    cluster_name: str
    service_name: str
    namespace: str
    image: str
    url: str


class KnativeDeployer:
    def deploy(
        self,
        *,
        cluster_name: str,
        submission: IntentFunction,
    ) -> DeploymentResult:
        image = self._image_for_cluster(
            cluster_name=cluster_name,
            image=submission.function.image,
        )

        manifest = self._build_ksvc_manifest(
            submission=submission,
            image=image,
        )

        self._kubectl_apply(
            cluster_name=cluster_name,
            manifest=manifest,
        )

        self._wait_until_ready(
            cluster_name=cluster_name,
            service_name=submission.function.service_name,
            namespace=submission.function.namespace,
        )

        url = self._get_url(
            cluster_name=cluster_name,
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

    def _image_for_cluster(
        self,
        *,
        cluster_name: str,
        image: str,
    ) -> str:
        registry = CLUSTER_IMAGE_REGISTRIES.get(cluster_name)

        if registry is None:
            raise ValueError(
                f"No image registry configured for cluster {cluster_name!r}"
            )

        image_without_registry = image

        if "/" in image:
            first_part, remainder = image.split("/", maxsplit=1)

            if "." in first_part or ":" in first_part:
                image_without_registry = remainder

        return f"{registry}/{image_without_registry}"

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
        cluster_name: str,
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
                cluster_name,
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
        cluster_name: str,
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
                    cluster_name,
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
            f"on {cluster_name!r} within {timeout_seconds} seconds."
        )

    def _get_url(
        self,
        *,
        cluster_name: str,
        service_name: str,
        namespace: str,
    ) -> str:
        result = subprocess.run(
            [
                "kubectl",
                "--context",
                cluster_name,
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
