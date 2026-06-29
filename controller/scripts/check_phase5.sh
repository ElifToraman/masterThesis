#!/usr/bin/env bash

set -euo pipefail

echo "===== Controller host ====="
hostname

echo
echo "===== Kubernetes contexts ====="

for context in vm1-cluster vm2-cluster; do
  echo "--- ${context} ---"
  kubectl --context "$context" get nodes --no-headers
done

echo
echo "===== Prometheus readiness ====="

for endpoint in \
  "vm1-cluster|http://127.0.0.1:19091" \
  "vm2-cluster|http://127.0.0.1:19092"; do

  cluster="${endpoint%%|*}"
  url="${endpoint#*|}"

  printf '%s: ' "$cluster"
  curl -fsS "${url}/-/ready"
done

echo
echo "===== Prometheus node counts ====="

for endpoint in \
  "vm1-cluster|http://127.0.0.1:19091" \
  "vm2-cluster|http://127.0.0.1:19092"; do

  cluster="${endpoint%%|*}"
  url="${endpoint#*|}"

  count=$(
    curl -fsSG \
      "${url}/api/v1/query" \
      --data-urlencode 'query=count(kube_node_info)' |
    jq -r '.data.result[0].value[1]'
  )

  echo "${cluster}: ${count} nodes"
done

echo
echo "===== Knative ingress ====="

curl -fsS \
  http://hello.default.129.114.25.182.sslip.io |
jq -c '{message, pod}'

curl -fsS \
  http://hello.default.129.114.25.80.sslip.io |
jq -c '{message, pod}'

echo
echo "===== Registries through SSH ====="

for host in vm1 vm2; do
  printf '%s: ' "$host"

  ssh "$host" \
    'curl -fsS http://127.0.0.1:5000/v2/_catalog' |
  jq -c '.repositories'
done

echo
echo "Phase 5 verification passed."
