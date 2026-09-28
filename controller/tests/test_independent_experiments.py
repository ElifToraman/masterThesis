from __future__ import annotations

import unittest
from pathlib import Path

from controller.scripts.run_independent_experiments import build_trial_schedule


class IndependentExperimentTests(unittest.TestCase):
    def test_schedule_has_each_function_once_per_repetition(self) -> None:
        submissions = {
            "dynamic-html": Path("dynamic.yaml"),
            "graph-pagerank": Path("graph.yaml"),
            "gzip-compression": Path("gzip.yaml"),
        }
        schedule = build_trial_schedule(
            submissions=submissions,
            repetitions=4,
            random_seed=42,
        )

        self.assertEqual(len(schedule), 12)
        for repetition in range(1, 5):
            names = {
                trial.function_name
                for trial in schedule
                if trial.repetition == repetition
            }
            self.assertEqual(names, set(submissions))

    def test_schedule_is_deterministic_for_seed(self) -> None:
        submissions = {"a": Path("a"), "b": Path("b"), "c": Path("c")}

        first = build_trial_schedule(
            submissions=submissions,
            repetitions=3,
            random_seed=7,
        )
        second = build_trial_schedule(
            submissions=submissions,
            repetitions=3,
            random_seed=7,
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
