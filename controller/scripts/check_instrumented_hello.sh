#!/usr/bin/env bash

set -euo pipefail

for context in vm1-cluster vm2-cluster; do
  echo
  echo "===== ${context} ====="

  url=$(
    kubectl --context "$context" \
      get kservice hello-instrumented \
      -o jsonpath='{.status.url}'
  )

  pod=$(
    kubectl --context "$context" \
      get pods \
      -l serving.knative.dev/service=hello-instrumented \
      -o jsonpath='{.items[0].metadata.name}'
  )

  node=$(
    kubectl --context "$context" \
      get pod "$pod" \
      -o jsonpath='{.spec.nodeName}'
  )

  request_id="phase6-${context}-$(date +%s%N)"

  response=$(
    curl -fsS \
      -H "X-Request-ID: ${request_id}" \
      "${url}?work_ms=100"
  )

  echo "$response" | jq

  echo "$response" |
  jq -e \
    --arg expected_cluster "$context" \
    --arg expected_pod "$pod" \
    --arg expected_node "$node" \
    --arg expected_request_id "$request_id" \
    '
      .cluster == $expected_cluster
      and .pod == $expected_pod
      and .node == $expected_node
      and .request_id == $expected_request_id
      and .knative_service == "hello-instrumented"
      and .work_requested_ms == 100
    ' >/dev/null

  echo "Placement verification passed:"
  echo "  cluster=${context}"
  echo "  node=${node}"
  echo "  pod=${pod}"
done

echo
echo "Phase 6 instrumented hello verification passed."
