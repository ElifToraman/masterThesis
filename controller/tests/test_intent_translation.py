from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from controller.intent_function_parser import (
    IntentFunctionParseError,
    parse_intent_function_payload,
)
from controller.intent_translation import (
    IntentSemanticError,
    NormalizedLocationConstraint,
    load_normalized_intent,
    translate_intent,
    write_normalized_intent,
)


VALID_PAYLOAD = {
    "apiVersion": "intent.elif.dev/v1",
    "kind": "IntentFunction",
    "metadata": {"name": "hello-intent-function"},
    "spec": {
        "function": {
            "name": "hello",
            "namespace": "default",
            "serviceName": "hello",
            "version": "hello-v2",
            "runtime": "knative",
            "image": "elif/hello:v2",
        },
        "intent": {
            "targetRef": {
                "kind": "KnativeService",
                "name": "default/hello",
            },
            "objectives": [
                {
                    "name": "hello-p95-latency",
                    "operator": "<=",
                    "value": 50,
                    "unit": "ms",
                    "measuredBy": (
                        "benchmark/hello/p95_warm_latency_ms"
                    ),
                }
            ],
        },
    },
}


def location_constraint(
    *,
    values: list[str] | None = None,
    enforcement: str = "hard",
    operator: str = "in",
) -> dict:
    return {
        "type": "location",
        "name": "hello-location",
        "target": "hello",
        "operator": operator,
        "values": values or ["vm1-cluster"],
        "enforcement": enforcement,
        "priority": 0.5,
    }


class IntentTranslationTests(unittest.TestCase):
    def parse(self, payload: dict):
        return parse_intent_function_payload(json.dumps(payload))

    def test_translates_exact_binding_to_canonical_requirement(self) -> None:
        normalized = translate_intent(
            self.parse(VALID_PAYLOAD)
        ).objectives[0]

        self.assertEqual(normalized.metric_id, "application.latency")
        self.assertEqual(normalized.statistic, "p95")
        self.assertEqual(normalized.canonical_value, 0.05)
        self.assertEqual(normalized.canonical_unit, "seconds")
        self.assertEqual(normalized.enforcement, "soft")
        self.assertEqual(
            normalized.phases,
            frozenset({"placement", "runtime"}),
        )
        self.assertEqual(
            normalized.placement_source.field,
            "benchmark_p95_latency_ms",
        )
        self.assertEqual(
            normalized.placement_source.canonical_factor,
            0.001,
        )
        self.assertEqual(normalized.runtime_source.field, "p95_latency_ms")
        self.assertEqual(normalized.runtime_source.canonical_factor, 0.001)

    def test_human_readable_name_does_not_change_binding(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["name"] = (
            "throughput-and-cold-start"
        )

        normalized = translate_intent(self.parse(payload)).objectives[0]

        self.assertEqual(normalized.metric_id, "application.latency")
        self.assertEqual(normalized.statistic, "p95")

    def test_rejects_unsupported_binding_during_admission(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["measuredBy"] = (
            "benchmark/hello/unknown_metric"
        )

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            r"semantics are invalid: .*unsupported measuredBy",
        ):
            self.parse(payload)

    def test_rejects_unsupported_unit_during_admission(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["unit"] = "MiB"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            r"semantics are invalid: .*unit 'MiB' is not supported",
        ):
            self.parse(payload)

    def test_current_constraints_normalize_as_hard(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        requirement = copy.deepcopy(
            payload["spec"]["intent"]["objectives"][0]
        )
        requirement["name"] = "hard-latency-limit"
        payload["spec"]["intent"]["constraints"] = [requirement]

        normalized = translate_intent(self.parse(payload)).constraints[0]

        self.assertEqual(normalized.enforcement, "hard")

    def test_objective_can_explicitly_be_hard(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["enforcement"] = (
            "hard"
        )

        normalized = translate_intent(self.parse(payload)).objectives[0]

        self.assertEqual(normalized.enforcement, "hard")

    def test_constraint_can_explicitly_be_soft(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        requirement = copy.deepcopy(
            payload["spec"]["intent"]["objectives"][0]
        )
        requirement["name"] = "preferred-latency"
        requirement["enforcement"] = "soft"
        payload["spec"]["intent"]["constraints"] = [requirement]

        normalized = translate_intent(self.parse(payload)).constraints[0]

        self.assertEqual(normalized.enforcement, "soft")

    def test_rejects_unknown_enforcement_during_admission(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["enforcement"] = (
            "mandatory"
        )

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "enforcement must be 'hard' or 'soft'",
        ):
            self.parse(payload)

    def test_persists_and_loads_normalized_intent(self) -> None:
        source = json.dumps(VALID_PAYLOAD)
        normalized = translate_intent(
            parse_intent_function_payload(source)
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )

            loaded = load_normalized_intent(
                artifact,
                source_payload=source,
            )
            payload = json.loads(artifact.read_text(encoding="utf-8"))

        self.assertEqual(loaded, normalized)
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual(len(payload["sourceSha256"]), 64)

    def test_translates_typed_location_constraint(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["constraints"] = [
            location_constraint()
        ]

        normalized = translate_intent(self.parse(payload)).constraints[0]

        self.assertIsInstance(normalized, NormalizedLocationConstraint)
        self.assertEqual(normalized.constraint_id, "hello-location")
        self.assertEqual(normalized.target, "default/hello")
        self.assertEqual(normalized.operator, "in")
        self.assertEqual(normalized.values, ("vm1-cluster",))
        self.assertEqual(normalized.enforcement, "hard")
        self.assertEqual(normalized.priority, 0.5)
        self.assertEqual(
            normalized.phases,
            frozenset({"placement", "runtime"}),
        )

    def test_rejects_location_constraint_for_another_function(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        constraint = location_constraint()
        constraint["target"] = "another-function"
        payload["spec"]["intent"]["constraints"] = [constraint]

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "does not identify the submitted function",
        ):
            self.parse(payload)

    def test_rejects_unsupported_typed_constraint(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        constraint = location_constraint()
        constraint["type"] = "hardware"
        payload["spec"]["intent"]["constraints"] = [constraint]

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "type 'hardware' is unsupported",
        ):
            self.parse(payload)

    def test_rejects_unknown_location_constraint_field(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        constraint = location_constraint()
        constraint["measuredBy"] = "should-not-be-here"
        payload["spec"]["intent"]["constraints"] = [constraint]

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "contains unsupported fields: measuredBy",
        ):
            self.parse(payload)

    def test_rejects_invalid_location_operator(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["constraints"] = [
            location_constraint(operator="equals")
        ]

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "operator must be 'in' or 'notIn'",
        ):
            self.parse(payload)

    def test_persists_typed_location_constraint(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["constraints"] = [
            location_constraint(enforcement="soft")
        ]
        source = json.dumps(payload)
        normalized = translate_intent(self.parse(payload))

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )
            artifact_payload = json.loads(
                artifact.read_text(encoding="utf-8")
            )
            loaded = load_normalized_intent(
                artifact,
                source_payload=source,
            )

        self.assertEqual(
            artifact_payload["constraints"][0]["type"],
            "location",
        )
        self.assertEqual(loaded, normalized)

    def test_persists_legacy_metric_constraint_in_version_two(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        requirement = copy.deepcopy(
            payload["spec"]["intent"]["objectives"][0]
        )
        requirement["name"] = "latency-constraint"
        payload["spec"]["intent"]["constraints"] = [requirement]
        source = json.dumps(payload)
        normalized = translate_intent(self.parse(payload))

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )
            artifact_payload = json.loads(
                artifact.read_text(encoding="utf-8")
            )
            loaded = load_normalized_intent(
                artifact,
                source_payload=source,
            )

        self.assertEqual(
            artifact_payload["constraints"][0]["type"],
            "metric",
        )
        self.assertEqual(loaded, normalized)

    def test_loads_version_one_normalized_artifact(self) -> None:
        source = json.dumps(VALID_PAYLOAD)
        normalized = translate_intent(self.parse(VALID_PAYLOAD))

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )
            artifact_payload = json.loads(
                artifact.read_text(encoding="utf-8")
            )
            artifact_payload["schemaVersion"] = 1
            artifact.write_text(
                json.dumps(artifact_payload),
                encoding="utf-8",
            )

            loaded = load_normalized_intent(
                artifact,
                source_payload=source,
            )

        self.assertEqual(loaded, normalized)

    def test_persists_explicit_enforcement(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["enforcement"] = (
            "hard"
        )
        source = json.dumps(payload)
        normalized = translate_intent(
            parse_intent_function_payload(source)
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )
            loaded = load_normalized_intent(
                artifact,
                source_payload=source,
            )

        self.assertEqual(loaded.objectives[0].enforcement, "hard")

    def test_rejects_artifact_for_different_submission(self) -> None:
        source = json.dumps(VALID_PAYLOAD)
        normalized = translate_intent(
            parse_intent_function_payload(source)
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )

            with self.assertRaisesRegex(
                IntentSemanticError,
                "does not match the source submission",
            ):
                load_normalized_intent(
                    artifact,
                    source_payload=source + "\n",
                )

    def test_rejects_artifact_that_disagrees_with_registry(self) -> None:
        source = json.dumps(VALID_PAYLOAD)
        normalized = translate_intent(
            parse_intent_function_payload(source)
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "normalized-intent.json"
            write_normalized_intent(
                normalized,
                artifact,
                source_payload=source,
            )
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["objectives"][0]["placementSource"]["field"] = (
                "another_metric"
            )
            artifact.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                IntentSemanticError,
                "does not match the metric registry",
            ):
                load_normalized_intent(
                    artifact,
                    source_payload=source,
                )


if __name__ == "__main__":
    unittest.main()
