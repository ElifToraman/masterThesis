from __future__ import annotations

import argparse
from pathlib import Path

from controller.intent_function_parser import (
    IntentFunctionParseError,
    parse_intent_function_payload,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an IntentFunction YAML file."
    )

    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to IntentFunction YAML file.",
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        raw_payload = arguments.file.read_text(
            encoding="utf-8",
        )

        submission = parse_intent_function_payload(
            raw_payload,
        )

    except (
        OSError,
        IntentFunctionParseError,
        KeyError,
        ValueError,
    ) as error:
        print(f"IntentFunction is invalid: {error}")
        return 1

    print("IntentFunction is valid")
    print(f"name: {submission.name}")

    function = submission.function

    print(
        "function: "
        f"{function.namespace}/{function.name}"
    )
    print(f"version: {function.version}")

    for objective in submission.intent.objectives:
        print(
            "objective: "
            f"{objective.name} -> "
            f"{objective.measured_by} "
            f"{objective.operator} "
            f"{objective.value} "
            f"{objective.unit or ''}".rstrip()
        )

    print(f"image: {function.image}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

