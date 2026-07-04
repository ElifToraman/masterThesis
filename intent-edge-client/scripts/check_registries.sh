#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(
  cd "$(dirname "${BASH_SOURCE[0]}")"
  pwd
)"

CLIENT_ROOT="$(
  cd "$SCRIPT_DIRECTORY/.."
  pwd
)"

CONFIG_FILE="$CLIENT_ROOT/config/client.env"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Missing client configuration: $CONFIG_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: Required command is not installed: $command_name" >&2
    exit 1
  fi
}

check_registry() {
  local name="$1"
  local endpoint="$2"

  echo "===== $name ====="
  echo "Endpoint: http://${endpoint}"

  local status

  status="$(
    curl \
      --silent \
      --show-error \
      --output /dev/null \
      --write-out '%{http_code}' \
      "http://${endpoint}/v2/"
  )"

  echo "Registry API status: $status"

  if [[ "$status" != "200" ]]; then
    echo "ERROR: $name registry returned HTTP $status" >&2
    return 1
  fi

  echo "Catalog:"

  if command -v jq >/dev/null 2>&1; then
    curl -fsS \
      "http://${endpoint}/v2/_catalog" |
      jq
  else
    curl -fsS \
      "http://${endpoint}/v2/_catalog"

    echo
  fi

  echo
}

check_registry_from_docker() {
  local name="$1"
  local endpoint="$2"

  echo "===== $name from Docker Desktop ====="

  local status

  status="$(
    docker run --rm \
      curlimages/curl:8.9.1 \
      --silent \
      --show-error \
      --output /dev/null \
      --write-out '%{http_code}' \
      "http://${endpoint}/v2/"
  )"

  echo "Docker-side registry API status: $status"

  if [[ "$status" != "200" ]]; then
    echo "ERROR: Docker could not reach $name registry." >&2
    return 1
  fi

  echo
}

require_command curl
require_command docker

echo "Intent edge client registry check"
echo

echo "Expected SSH tunnels:"
echo "  127.0.0.1:5000 -> VM1 127.0.0.1:5000"
echo "  127.0.0.1:5001 -> VM2 127.0.0.1:5000"
echo

if command -v lsof >/dev/null 2>&1; then
  echo "===== Local listening ports ====="

  if ! lsof -nP \
    -iTCP:5000 \
    -sTCP:LISTEN; then

    echo "ERROR: Nothing is listening on MacBook port 5000." >&2
    exit 1
  fi

  if ! lsof -nP \
    -iTCP:5001 \
    -sTCP:LISTEN; then

    echo "ERROR: Nothing is listening on MacBook port 5001." >&2
    exit 1
  fi

  echo
fi

check_registry \
  "VM1 registry" \
  "127.0.0.1:5000"

check_registry \
  "VM2 registry" \
  "127.0.0.1:5001"

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker Desktop is not running." >&2
  exit 1
fi

check_registry_from_docker \
  "VM1 registry" \
  "host.docker.internal:5000"

check_registry_from_docker \
  "VM2 registry" \
  "host.docker.internal:5001"

echo "All registry tunnel checks passed."
