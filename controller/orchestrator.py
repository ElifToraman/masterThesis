from __future__ import annotations

import subprocess
import sys


def run_step(name: str, command: list[str]) -> None:
    print()
    print(f"=== {name} ===")

    result = subprocess.run(
        command,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise SystemExit(
            f"{name} failed with exit code {result.returncode}"
        )


def main() -> None:
    run_step(
        "Benchmark candidate clusters",
        [
            sys.executable,
            "-m",
            "controller.scripts.run_benchmark",
        ],
    )

    run_step(
        "Select best cluster",
        [
            sys.executable,
            "-m",
            "controller.scripts.run_decision_policy",
        ],
    )

    run_step(
        "Deploy selected function",
        [
            sys.executable,
            "-m",
            "controller.scripts.deploy_selected",
        ],
    )


if __name__ == "__main__":
    main()
