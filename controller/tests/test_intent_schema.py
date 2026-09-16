from __future__ import annotations

import copy
import json
import re
import unittest

from controller.intent_function_parser import (
    IntentFunctionParseError,
    parse_intent_function_payload,
)
from controller.tests.test_intent_translation import (
    VALID_PAYLOAD,
    location_constraint,
)


class StrictIntentSchemaTests(unittest.TestCase):
    def parse(self, payload: dict) -> None:
        parse_intent_function_payload(json.dumps(payload))

    def test_rejects_unknown_fields_in_every_mapping(self) -> None:
        cases = {
            "payload": lambda payload: payload.__setitem__(
                "unexpected", True
            ),
            "metadata": lambda payload: payload["metadata"].__setitem__(
                "labels", {}
            ),
            "spec": lambda payload: payload["spec"].__setitem__(
                "replicas", 1
            ),
            "spec.function": lambda payload: payload["spec"][
                "function"
            ].__setitem__("command", ["run"]),
            "spec.intent": lambda payload: payload["spec"][
                "intent"
            ].__setitem__("evaluation", {}),
            "spec.intent.targetRef": lambda payload: payload["spec"][
                "intent"
            ]["targetRef"].__setitem__("apiVersion", "serving.knative.dev/v1"),
            "spec.intent.objectives[0]": lambda payload: payload["spec"][
                "intent"
            ]["objectives"][0].__setitem__("priority", 1),
            "spec.intent.properties": lambda payload: payload["spec"][
                "intent"
            ].__setitem__("properties", {"replicas": 2}),
        }

        for field_name, mutate in cases.items():
            with self.subTest(field_name=field_name):
                payload = copy.deepcopy(VALID_PAYLOAD)
                mutate(payload)

                with self.assertRaisesRegex(
                    IntentFunctionParseError,
                    rf"{re.escape(field_name)} contains unsupported fields",
                ):
                    self.parse(payload)

    def test_rejects_duplicate_yaml_fields(self) -> None:
        raw_payload = """
apiVersion: intent.elif.dev/v1
kind: IntentFunction
kind: AnotherKind
metadata:
  name: hello-intent-function
spec:
  function:
    name: hello
    version: hello-v2
    image: elif/hello:v2
  intent:
    targetRef:
      kind: KnativeService
      name: default/hello
    objectives:
      - name: hello-p95-latency
        operator: "<="
        value: 50
        unit: ms
        measuredBy: benchmark/hello/p95_warm_latency_ms
"""

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "found duplicate field 'kind'",
        ):
            parse_intent_function_payload(raw_payload)

    def test_rejects_invalid_kubernetes_names(self) -> None:
        cases = {
            "metadata.name": ("metadata", "name", "Hello_App"),
            "spec.function.name": (
                "function",
                "name",
                "Hello",
            ),
            "spec.function.namespace": (
                "function",
                "namespace",
                "bad_namespace",
            ),
            "spec.function.serviceName": (
                "function",
                "serviceName",
                "hello.service",
            ),
            "spec.intent.objectives[0].name": (
                "objective",
                "name",
                "Latency SLO",
            ),
        }

        for field_name, (section, key, value) in cases.items():
            with self.subTest(field_name=field_name):
                payload = copy.deepcopy(VALID_PAYLOAD)

                if section == "metadata":
                    payload["metadata"][key] = value
                elif section == "function":
                    payload["spec"]["function"][key] = value
                else:
                    payload["spec"]["intent"]["objectives"][0][key] = value

                with self.assertRaisesRegex(
                    IntentFunctionParseError,
                    "must be a lowercase DNS",
                ):
                    self.parse(payload)

    def test_rejects_unsupported_runtime(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["function"]["runtime"] = "openfaas"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "Unsupported spec.function.runtime 'openfaas'",
        ):
            self.parse(payload)

    def test_rejects_unsupported_target_kind(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["targetRef"]["kind"] = "Deployment"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "Unsupported spec.intent.targetRef.kind 'Deployment'",
        ):
            self.parse(payload)

    def test_rejects_target_for_another_service(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["targetRef"]["name"] = "default/other"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "must identify the submitted service 'default/hello'",
        ):
            self.parse(payload)

    def test_rejects_non_namespaced_target(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["targetRef"]["name"] = "hello"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "must use the form namespace/name",
        ):
            self.parse(payload)

    def test_rejects_invalid_version_and_image(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["function"]["version"] = "version two"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "spec.function.version must contain only",
        ):
            self.parse(payload)

        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["function"]["image"] = "elif/hello:bad tag"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "valid container image reference",
        ):
            self.parse(payload)

    def test_rejects_missing_required_fields(self) -> None:
        cases = {
            "metadata": ("metadata", "name"),
            "spec.function": ("function", "image"),
            "spec.intent": ("intent", "objectives"),
            "spec.intent.targetRef": ("target", "kind"),
            "spec.intent.objectives[0]": (
                "objective",
                "measuredBy",
            ),
        }

        for field_name, (section, key) in cases.items():
            with self.subTest(field_name=field_name):
                payload = copy.deepcopy(VALID_PAYLOAD)

                if section == "metadata":
                    del payload["metadata"][key]
                elif section == "function":
                    del payload["spec"]["function"][key]
                elif section == "intent":
                    del payload["spec"]["intent"][key]
                elif section == "target":
                    del payload["spec"]["intent"]["targetRef"][key]
                else:
                    del payload["spec"]["intent"]["objectives"][0][key]

                with self.assertRaisesRegex(
                    IntentFunctionParseError,
                    rf"{re.escape(field_name)} is missing fields",
                ):
                    self.parse(payload)

    def test_rejects_wrong_collection_types(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"] = {}

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "spec.intent.objectives must be a list",
        ):
            self.parse(payload)

        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["properties"] = []

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "spec.intent.properties must be a mapping",
        ):
            self.parse(payload)

    def test_rejects_unsupported_api_version_and_kind(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["apiVersion"] = "intent.elif.dev/v2"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "Unsupported apiVersion 'intent.elif.dev/v2'",
        ):
            self.parse(payload)

        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["kind"] = "Deployment"

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "Unsupported kind 'Deployment'",
        ):
            self.parse(payload)

    def test_rejects_invalid_operator(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["operator"] = "~="

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "Unsupported objective operator",
        ):
            self.parse(payload)

    def test_rejects_boolean_autoscaling_values(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["properties"] = {"minScale": True}

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "minScale must be an integer",
        ):
            self.parse(payload)

    def test_rejects_duplicate_requirement_names(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        duplicate = copy.deepcopy(
            payload["spec"]["intent"]["objectives"][0]
        )
        payload["spec"]["intent"]["constraints"] = [duplicate]

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "requirement names must be unique",
        ):
            self.parse(payload)

    def test_rejects_empty_optional_strings(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["description"] = ""

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "description must not be empty",
        ):
            self.parse(payload)

    def test_rejects_boolean_and_non_finite_numbers(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        payload["spec"]["intent"]["objectives"][0]["value"] = True

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "value must be a number",
        ):
            self.parse(payload)

        raw_payload = json.dumps(VALID_PAYLOAD).replace(
            '"value": 50',
            '"value": .nan',
        )

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "value must be finite",
        ):
            parse_intent_function_payload(raw_payload)

    def test_location_constraint_name_uses_same_identifier_rules(self) -> None:
        payload = copy.deepcopy(VALID_PAYLOAD)
        constraint = location_constraint()
        constraint["name"] = "Hello Location"
        payload["spec"]["intent"]["constraints"] = [constraint]

        with self.assertRaisesRegex(
            IntentFunctionParseError,
            "name must be a lowercase DNS label",
        ):
            self.parse(payload)


if __name__ == "__main__":
    unittest.main()
