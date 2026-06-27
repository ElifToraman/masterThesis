#!/usr/bin/env bash
set -euo pipefail



KUBECONFIG_VM1="$HOME/.kube/vm1-config"
KUBECONFIG_VM2="$HOME/.kube/vm2-config"

REGISTRY_VM1="${REGISTRY_VM1:-host.docker.internal:5000/elif}"
REGISTRY_VM2="${REGISTRY_VM2:-host.docker.internal:5001/elif}"

FLOATING_IP_VM1="129.114.25.182"
FLOATING_IP_VM2="129.114.25.80"

F2_INTERNAL_URL="http://f2.default.svc.cluster.local"
F3_INTERNAL_URL="http://f3.default.svc.cluster.local"

deploy_chain() {
  local vm="$1"
  local kubeconfig="$2"
  local registry="$3"
  local floating_ip="$4"

  echo "Deploying whole chain to $vm using existing images..."
  echo "Registry: $registry"

  KUBECONFIG="$kubeconfig" kn service apply f3 \
    --image "${registry}/f3:latest" \
    --env VM_FLOATING_IP="$floating_ip"

  KUBECONFIG="$kubeconfig" kn service apply f2 \
    --image "${registry}/f2:latest" \
    --env VM_FLOATING_IP="$floating_ip" \
    --env F3_URL="$F3_INTERNAL_URL"

  KUBECONFIG="$kubeconfig" kn service apply f1 \
    --image "${registry}/f1:latest" \
    --env VM_FLOATING_IP="$floating_ip" \
    --env F2_URL="$F2_INTERNAL_URL"

  KUBECONFIG="$kubeconfig" kubectl wait ksvc f1 --for=condition=Ready --timeout=120s
  KUBECONFIG="$kubeconfig" kubectl wait ksvc f2 --for=condition=Ready --timeout=120s
  KUBECONFIG="$kubeconfig" kubectl wait ksvc f3 --for=condition=Ready --timeout=120s
}

deploy_chain "vm1" "$KUBECONFIG_VM1" "$REGISTRY_VM1" "$FLOATING_IP_VM1"
deploy_chain "vm2" "$KUBECONFIG_VM2" "$REGISTRY_VM2" "$FLOATING_IP_VM2"

echo
echo "VM-1 services:"
KUBECONFIG="$KUBECONFIG_VM1" kubectl get ksvc f1 f2 f3

echo
echo "VM-2 services:"
KUBECONFIG="$KUBECONFIG_VM2" kubectl get ksvc f1 f2 f3

