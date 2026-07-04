#!/usr/bin/env bash

set -euo pipefail

# Directory containing this script:
# <repository>/intent-edge-client
CLIENT_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")"
  pwd
)"

# Parent directory:
# <repository>
REPOSITORY_ROOT="$(
  cd "$CLIENT_ROOT/.."
  pwd
)"

CONFIG_FILE="$CLIENT_ROOT/config/client.env"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Client configuration not found: $CONFIG_FILE" >&2
  exit 1
fi

# Load Controller VM and registry configuration.
# shellcheck disable=SC1090
source "$CONFIG_FILE"

SUBMISSIONS_DIRECTORY="$CLIENT_ROOT/submissions"
RESPONSES_DIRECTORY="$CLIENT_ROOT/responses"
RESULTS_DIRECTORY="$CLIENT_ROOT/results"
LOGS_DIRECTORY="$CLIENT_ROOT/logs"
SCRIPTS_DIRECTORY="$CLIENT_ROOT/scripts"

mkdir -p \
  "$SUBMISSIONS_DIRECTORY" \
  "$RESPONSES_DIRECTORY" \
  "$RESULTS_DIRECTORY" \
  "$LOGS_DIRECTORY" \
  "$SCRIPTS_DIRECTORY"

export CLIENT_ROOT
export REPOSITORY_ROOT
export SUBMISSIONS_DIRECTORY
export RESPONSES_DIRECTORY
export RESULTS_DIRECTORY
export LOGS_DIRECTORY
export SCRIPTS_DIRECTORY

export CONTROLLER_HOST
export CONTROLLER_USER
export CONTROLLER_SSH_KEY
export CONTROLLER_REPOSITORY

export VM1_PUSH_REGISTRY
export VM2_PUSH_REGISTRY
export RUNTIME_REGISTRY

echo "Intent edge client initialized."
echo
printf '%-27s %s\n' \
  "Repository root:" "$REPOSITORY_ROOT" \
  "Client root:" "$CLIENT_ROOT" \
  "Controller:" "${CONTROLLER_USER}@${CONTROLLER_HOST}" \
  "Controller repository:" "$CONTROLLER_REPOSITORY" \
  "VM1 push registry:" "$VM1_PUSH_REGISTRY" \
  "VM2 push registry:" "$VM2_PUSH_REGISTRY" \
  "Runtime registry:" "$RUNTIME_REGISTRY" \
  "Submissions:" "$SUBMISSIONS_DIRECTORY" \
  "Responses:" "$RESPONSES_DIRECTORY" \
  "Results:" "$RESULTS_DIRECTORY" \
  "Logs:" "$LOGS_DIRECTORY"
