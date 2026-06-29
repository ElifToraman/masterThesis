#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"

cd "$REPOSITORY_ROOT"

echo "===== JSON syntax ====="

for file in \
  controller/config/function_descriptor.json \
  controller/config/intent.json \
  controller/config/controller_config.json; do

  jq empty "$file"
  echo "$file: valid JSON"
done

echo
echo "===== Semantic validation ====="

python3 controller/validate_inputs.py

echo
echo "===== Infrastructure cross-check ====="

for context in $(
  jq -r \
    '.candidate_clusters[]' \
    controller/config/controller_config.json
); do
  kubectl --context "$context" get nodes >/dev/null
  echo "$context: Kubernetes reachable"
done

echo
echo "===== Function image availability ====="

IMAGE_REPOSITORY="elif/hello-instrumented"
IMAGE_TAG="v1"

for host in vm1 vm2; do
  tags=$(
    ssh "$host" \
      "curl -fsS \
      http://127.0.0.1:5000/v2/${IMAGE_REPOSITORY}/tags/list"
  )

  echo "$tags" |
  jq -e \
    --arg tag "$IMAGE_TAG" \
    '.tags | index($tag) != null' >/dev/null

  echo "$host: ${IMAGE_REPOSITORY}:${IMAGE_TAG} available"
done

echo
echo "Phase 7 input verification passed."
