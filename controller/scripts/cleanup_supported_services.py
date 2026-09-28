from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controller.function_profiles import (
    DEFAULT_FUNCTION_PROFILES_FILE,
    load_function_profiles,
)
from controller.runtime_config import (
    DEFAULT_CLUSTER_CONFIG_FILE,
    load_cluster_configs,
)
from controller.scripts.cleanup_non_selected import (
    delete_service,
    service_exists,
)


def cleanup_supported_services(
    *,
    clusters,
    service_names: list[str],
    namespace: str = "default",
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    for cluster_name, cluster in sorted(clusters.items()):
        for service_name in sorted(service_names):
            exists = service_exists(
                kubernetes_context=cluster.kubernetes_context,
                service_name=service_name,
                namespace=namespace,
            )
            output = None
            if exists:
                output = delete_service(
                    kubernetes_context=cluster.kubernetes_context,
                    service_name=service_name,
                    namespace=namespace,
                )
            action = {
                "cluster_name": cluster_name,
                "kubernetes_context": cluster.kubernetes_context,
                "namespace": namespace,
                "service_name": service_name,
                "existed": exists,
                "deleted": exists,
                "output": output,
            }
            actions.append(action)
            print(
                f"{cluster_name}/{service_name}: "
                f"{'deleted' if exists else 'not present'}"
            )

    return actions


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=("Remove supported function services before an independent run.")
    )
    parser.add_argument(
        "--cluster-config",
        type=Path,
        default=DEFAULT_CLUSTER_CONFIG_FILE,
    )
    parser.add_argument(
        "--function-profiles",
        type=Path,
        default=DEFAULT_FUNCTION_PROFILES_FILE,
    )
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    clusters = load_cluster_configs(args.cluster_config)
    profiles = load_function_profiles(args.function_profiles)
    actions = cleanup_supported_services(
        clusters=clusters,
        service_names=list(profiles),
        namespace=args.namespace,
    )
    evidence = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "purpose": "independent-run-preparation",
        "actions": actions,
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


if __name__ == "__main__":
    main()
