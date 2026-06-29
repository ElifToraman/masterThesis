#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <kube-context> <local-port> <prometheus-service>" >&2
  exit 2
fi

CONTEXT="$1"
LOCAL_PORT="$2"
PROMETHEUS_SERVICE="$3"

KUBECTL_BIN="${KUBECTL_BIN:-$(command -v kubectl)}"
KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

if [[ ! -f "$KUBECONFIG" ]]; then
  echo "Kubeconfig does not exist: $KUBECONFIG" >&2
  exit 1
fi

if ! "$KUBECTL_BIN" \
  --kubeconfig "$KUBECONFIG" \
  --context "$CONTEXT" \
  get namespace monitoring >/dev/null 2>&1; then
  echo "Monitoring namespace is unavailable in context: $CONTEXT" >&2
  exit 1
fi

if ! "$KUBECTL_BIN" \
  --kubeconfig "$KUBECONFIG" \
  --context "$CONTEXT" \
  -n monitoring \
  get service "$PROMETHEUS_SERVICE" >/dev/null 2>&1; then
  echo "Prometheus service not found: $PROMETHEUS_SERVICE" >&2
  exit 1
fi

echo "Forwarding ${CONTEXT}/${PROMETHEUS_SERVICE} to 127.0.0.1:${LOCAL_PORT}"

exec "$KUBECTL_BIN" \
  --kubeconfig "$KUBECONFIG" \
  --context "$CONTEXT" \
  --namespace monitoring \
  port-forward \
  --address 127.0.0.1 \
  "service/${PROMETHEUS_SERVICE}" \
  "${LOCAL_PORT}:9090"
