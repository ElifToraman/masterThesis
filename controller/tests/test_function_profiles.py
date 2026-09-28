from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from controller.function_profiles import (
    DEFAULT_FUNCTION_PROFILES_FILE,
    FunctionProfileError,
    load_function_profiles,
    require_function_profile,
)


class FunctionProfileTests(unittest.TestCase):
    def test_default_profiles_are_valid(self) -> None:
        profiles = load_function_profiles(DEFAULT_FUNCTION_PROFILES_FILE)

        self.assertEqual(
            set(profiles),
            {"hello", "dynamic-html", "graph-pagerank", "gzip-compression"},
        )
        page_rank = profiles["graph-pagerank"]
        self.assertEqual(page_rank.invocation.method, "POST")
        self.assertEqual(page_rank.benchmark["concurrency"], 2)
        self.assertEqual(
            json.loads(page_rank.invocation.request_body),
            {"seed": 42, "size": 10000},
        )

    def test_expected_json_validation(self) -> None:
        profile = load_function_profiles()["gzip-compression"].invocation

        self.assertIsNone(
            profile.validate_response_body(
                b'{"benchmark":"gzip-compression","success":true}'
            )
        )
        self.assertIn(
            "expected 'gzip-compression'",
            profile.validate_response_body(b'{"benchmark":"other","success":true}'),
        )

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(FunctionProfileError, "Unsupported function"):
            require_function_profile(load_function_profiles(), "unknown")

    def test_unknown_configuration_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "profiles.yaml"
            config.write_text(
                "functions:\n  example:\n    invocation:\n"
                "      method: GET\n      path: /\n      extra: true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FunctionProfileError,
                "unsupported fields: extra",
            ):
                load_function_profiles(config)


if __name__ == "__main__":
    unittest.main()
