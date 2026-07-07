from __future__ import annotations

import json
import subprocess
from pathlib import Path

from controller.intent_function_parser import parse_intent_function_payload


CLUSTERS = [
    "vm1-cluster",
    "vm2-cluster",
]


def main() -> None:
    controller_directory = Path(__file__).resolve().parents[1]

    decision_file = (
        controller_directory
        / "results"
        / "decisions"
        / "latest-decision.json"
    )

    submission_file = (
        controller_directory
        / "examples"
        / "hello-intent-function.yaml"
    )

    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    selected_cluster = decision["selected_cluster"]

    submission = parse_intent_function_payload(
        submission_file.read_text(encoding="utf-8")
    )

    for cluster in CLUSTERS:
        if cluster == selected_cluster:
            continue

        result = subprocess.run(
            [
                "kubectl",
                "--context",
                cluster,
                "delete",
                "ksvc",
                submission.function.service_name,
                "-n",
                submission.function.namespace,
                "--ignore-not-found",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        output = result.stdout.strip() or result.stderr.strip()

        if output:
            print(f"{cluster}: {output}")


if __name__ == "__main__":
    main()
