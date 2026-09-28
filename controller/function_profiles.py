from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import yaml


CONTROLLER_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_FUNCTION_PROFILES_FILE = (
    CONTROLLER_DIRECTORY / "config" / "function-profiles.yaml"
)

_PROFILE_FIELDS = {"invocation", "benchmark"}
_INVOCATION_FIELDS = {
    "method",
    "path",
    "jsonBody",
    "expectedJson",
}
_BENCHMARK_FIELDS = {
    "warmupRequests",
    "concurrency",
    "durationSeconds",
    "resourceSampleIntervalSeconds",
    "requestTimeoutSeconds",
    "deploymentTimeoutSeconds",
}


class FunctionProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class InvocationProfile:
    method: str
    path: str
    request_body: bytes | None
    content_type: str | None
    expected_json: Mapping[str, str | int | float | bool | None]

    def url_for(self, service_url: str) -> str:
        base = service_url.rstrip("/") + "/"
        return urljoin(base, self.path.lstrip("/"))

    def make_request(self, service_url: str):
        import urllib.request

        headers: dict[str, str] = {}
        if self.content_type is not None:
            headers["Content-Type"] = self.content_type

        return urllib.request.Request(
            url=self.url_for(service_url),
            data=self.request_body,
            headers=headers,
            method=self.method,
        )

    def validate_response_body(self, body: bytes) -> str | None:
        if not self.expected_json:
            return None

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            return f"response is not valid JSON: {error}"

        if not isinstance(payload, dict):
            return "response JSON must be an object"

        for key, expected in self.expected_json.items():
            if key not in payload:
                return f"response JSON is missing {key!r}"
            if payload[key] != expected:
                return (
                    f"response JSON field {key!r} was {payload[key]!r}; "
                    f"expected {expected!r}"
                )

        return None


@dataclass(frozen=True)
class FunctionProfile:
    name: str
    invocation: InvocationProfile
    benchmark: Mapping[str, int | float]


def load_function_profiles(
    config_file: Path = DEFAULT_FUNCTION_PROFILES_FILE,
) -> dict[str, FunctionProfile]:
    config_file = config_file.expanduser().resolve()
    if not config_file.is_file():
        raise FunctionProfileError(
            f"Function profile configuration does not exist: {config_file}"
        )

    try:
        payload = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise FunctionProfileError(f"Invalid function profile YAML: {error}") from error

    if not isinstance(payload, dict) or set(payload) != {"functions"}:
        raise FunctionProfileError(
            "Function profile configuration must contain only 'functions'"
        )

    raw_profiles = payload["functions"]
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise FunctionProfileError("functions must be a non-empty mapping")

    profiles: dict[str, FunctionProfile] = {}
    for name, raw_profile in raw_profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise FunctionProfileError(
                "Function profile names must be non-empty strings"
            )
        profiles[name] = _parse_profile(name, raw_profile)

    return profiles


def require_function_profile(
    profiles: Mapping[str, FunctionProfile],
    function_name: str,
) -> FunctionProfile:
    profile = profiles.get(function_name)
    if profile is None:
        supported = ", ".join(sorted(profiles))
        raise FunctionProfileError(
            f"Unsupported function {function_name!r}; supported functions: {supported}"
        )
    return profile


def _parse_profile(name: str, value: Any) -> FunctionProfile:
    if not isinstance(value, dict):
        raise FunctionProfileError(f"functions.{name} must be a mapping")
    _reject_unknown_fields(value, _PROFILE_FIELDS, f"functions.{name}")
    if "invocation" not in value:
        raise FunctionProfileError(f"functions.{name}.invocation is required")

    invocation = _parse_invocation(name, value["invocation"])
    benchmark = _parse_benchmark(name, value.get("benchmark", {}))
    return FunctionProfile(
        name=name,
        invocation=invocation,
        benchmark=MappingProxyType(benchmark),
    )


def _parse_invocation(name: str, value: Any) -> InvocationProfile:
    prefix = f"functions.{name}.invocation"
    if not isinstance(value, dict):
        raise FunctionProfileError(f"{prefix} must be a mapping")
    _reject_unknown_fields(value, _INVOCATION_FIELDS, prefix)

    method = value.get("method")
    if method not in {"GET", "POST"}:
        raise FunctionProfileError(f"{prefix}.method must be GET or POST")

    path = value.get("path", "/")
    if not isinstance(path, str) or not path.startswith("/"):
        raise FunctionProfileError(f"{prefix}.path must start with '/'")
    parsed_path = urlparse(path)
    if parsed_path.scheme or parsed_path.netloc or parsed_path.fragment:
        raise FunctionProfileError(f"{prefix}.path must be a relative HTTP path")

    json_body = value.get("jsonBody")
    if json_body is not None and not isinstance(json_body, dict):
        raise FunctionProfileError(f"{prefix}.jsonBody must be a mapping")
    if method == "GET" and json_body is not None:
        raise FunctionProfileError(f"{prefix}.jsonBody is not allowed for GET")

    expected = value.get("expectedJson", {})
    if not isinstance(expected, dict):
        raise FunctionProfileError(f"{prefix}.expectedJson must be a mapping")
    for key, expected_value in expected.items():
        if not isinstance(key, str) or not key:
            raise FunctionProfileError(
                f"{prefix}.expectedJson keys must be non-empty strings"
            )
        if not isinstance(expected_value, (str, int, float, bool, type(None))):
            raise FunctionProfileError(f"{prefix}.expectedJson.{key} must be a scalar")

    request_body = (
        json.dumps(json_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if json_body is not None
        else None
    )
    return InvocationProfile(
        method=method,
        path=path,
        request_body=request_body,
        content_type="application/json" if request_body is not None else None,
        expected_json=MappingProxyType(dict(expected)),
    )


def _parse_benchmark(name: str, value: Any) -> dict[str, int | float]:
    prefix = f"functions.{name}.benchmark"
    if not isinstance(value, dict):
        raise FunctionProfileError(f"{prefix} must be a mapping")
    _reject_unknown_fields(value, _BENCHMARK_FIELDS, prefix)

    parsed: dict[str, int | float] = {}
    for key, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise FunctionProfileError(f"{prefix}.{key} must be numeric")
        if key in {"warmupRequests"}:
            if not isinstance(raw, int) or raw < 0:
                raise FunctionProfileError(
                    f"{prefix}.{key} must be a non-negative integer"
                )
        elif key == "concurrency":
            if not isinstance(raw, int) or raw < 1:
                raise FunctionProfileError(f"{prefix}.{key} must be a positive integer")
        elif raw <= 0:
            raise FunctionProfileError(f"{prefix}.{key} must be positive")
        parsed[key] = raw

    return parsed


def _reject_unknown_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
    prefix: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise FunctionProfileError(
            f"{prefix} contains unsupported fields: {', '.join(unknown)}"
        )
