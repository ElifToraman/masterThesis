from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    load_cluster_configs,
)


DEFAULT_POLICY_FILE = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "knative-autoscaling-policy.json"
)


@dataclass(frozen=True)
class KnativeAutoscalingPolicy:
    namespace: str
    config_map: str
    min_scale: int
    max_scale: int

    def __post_init__(self) -> None:
        if not self.namespace.strip():
            raise ValueError("namespace must not be empty")

        if not self.config_map.strip():
            raise ValueError("configMap must not be empty")

        for field_name, value in (
            ("minScale", self.min_scale),
            ("maxScale", self.max_scale),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")

            if value < 0:
                raise ValueError(
                    f"{field_name} must be zero or greater"
                )

        if self.max_scale > 0 and self.min_scale > self.max_scale:
            raise ValueError("minScale must not exceed maxScale")


def load_policy(path: Path) -> KnativeAutoscalingPolicy:
    payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Autoscaling policy must be a JSON object")

    expected_fields = {
        "namespace",
        "configMap",
        "minScale",
        "maxScale",
    }
    unknown_fields = sorted(set(payload) - expected_fields)
    missing_fields = sorted(expected_fields - set(payload))

    if unknown_fields:
        raise ValueError(
            "Unsupported autoscaling policy fields: "
            + ", ".join(unknown_fields)
        )

    if missing_fields:
        raise ValueError(
            "Missing autoscaling policy fields: "
            + ", ".join(missing_fields)
        )

    return KnativeAutoscalingPolicy(
        namespace=_require_string(payload["namespace"], "namespace"),
        config_map=_require_string(payload["configMap"], "configMap"),
        min_scale=payload["minScale"],
        max_scale=payload["maxScale"],
    )


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")

    return value


def _kubectl(
    *,
    context: str,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--context", context, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def read_bounds(
    *,
    context: str,
    policy: KnativeAutoscalingPolicy,
) -> tuple[str | None, str | None]:
    result = _kubectl(
        context=context,
        arguments=[
            "-n",
            policy.namespace,
            "get",
            "configmap",
            policy.config_map,
            "-o",
            "json",
        ],
    )
    payload = json.loads(result.stdout)
    data = payload.get("data", {})

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{context}: ConfigMap data must be an object"
        )

    return data.get("min-scale"), data.get("max-scale")


def apply_policy(
    *,
    context: str,
    policy: KnativeAutoscalingPolicy,
) -> None:
    patch = {
        "data": {
            "min-scale": str(policy.min_scale),
            "max-scale": str(policy.max_scale),
        }
    }
    _kubectl(
        context=context,
        arguments=[
            "-n",
            policy.namespace,
            "patch",
            "configmap",
            policy.config_map,
            "--type",
            "merge",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        ],
    )


def configure(
    *,
    cluster_config: Path,
    policy_file: Path,
    apply: bool,
) -> None:
    clusters = load_cluster_configs(cluster_config)
    policy = load_policy(policy_file)

    if not clusters:
        raise ValueError("No clusters are configured")

    desired = (str(policy.min_scale), str(policy.max_scale))
    current_by_cluster: dict[str, tuple[str | None, str | None]] = {}

    # Read every cluster first so an unreachable cluster cannot leave a
    # supposedly common policy only partially applied.
    for cluster_name, cluster in clusters.items():
        current_by_cluster[cluster_name] = read_bounds(
            context=cluster.kubernetes_context,
            policy=policy,
        )

    for cluster_name, cluster in clusters.items():
        current = current_by_cluster[cluster_name]
        print(
            f"{cluster_name} ({cluster.kubernetes_context}): "
            f"minScale={current[0] or '<inherited>'}, "
            f"maxScale={current[1] or '<inherited>'} "
            f"-> minScale={desired[0]}, maxScale={desired[1]}"
        )

        if apply and current != desired:
            apply_policy(
                context=cluster.kubernetes_context,
                policy=policy,
            )

    if not apply:
        print("Dry run only; pass --apply to update every cluster.")
        return

    for cluster_name, cluster in clusters.items():
        actual = read_bounds(
            context=cluster.kubernetes_context,
            policy=policy,
        )

        if actual != desired:
            raise RuntimeError(
                f"{cluster_name}: expected bounds {desired}, got {actual}"
            )

        print(
            f"{cluster_name}: verified minScale={actual[0]}, "
            f"maxScale={actual[1]}"
        )


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Configure identical Knative autoscaling bounds on every "
            "cluster in controller/config/clusters.yaml."
        )
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_FILE,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the policy; without this flag the command is a dry run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)

    try:
        configure(
            cluster_config=arguments.cluster_config,
            policy_file=arguments.policy,
            apply=arguments.apply,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Knative autoscaling configuration failed: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
