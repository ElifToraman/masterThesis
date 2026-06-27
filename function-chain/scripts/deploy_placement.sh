#!/usr/bin/env bash
set -euo pipefail

F1_VM="${1:?Usage: deploy_placement.sh vm1|vm2 vm1|vm2 vm1|vm2}"
F2_VM="${2:?Usage: deploy_placement.sh vm1|vm2 vm1|vm2 vm1|vm2}"
F3_VM="${3:?Usage: deploy_placement.sh vm1|vm2 vm1|vm2 vm1|vm2}"



KUBECONFIG_VM1="$HOME/.kube/vm1-config"
KUBECONFIG_VM2="$HOME/.kube/vm2-config"

REGISTRY_VM1="${REGISTRY_VM1:-host.docker.internal:5000/elif}"
REGISTRY_VM2="${REGISTRY_VM2:-host.docker.internal:5001/elif}"

FLOATING_IP_VM1="129.114.25.182"
FLOATING_IP_VM2="129.114.25.80"

internal_url() {
  local service="$1"
  echo "http://${service}.default.svc.cluster.local"
}

public_url() {
  local service="$1"
  local vm="$2"

  if [ "$vm" = "vm1" ]; then
    echo "http://${service}.default.${FLOATING_IP_VM1}.sslip.io"
  elif [ "$vm" = "vm2" ]; then
    echo "http://${service}.default.${FLOATING_IP_VM2}.sslip.io"
  else
    echo "Unknown VM: $vm" >&2
    exit 1
  fi
}

kubeconfig_for() {
  local vm="$1"

  if [ "$vm" = "vm1" ]; then
    echo "$KUBECONFIG_VM1"
  elif [ "$vm" = "vm2" ]; then
    echo "$KUBECONFIG_VM2"
  else
    echo "Unknown VM: $vm" >&2
    exit 1
  fi
}

registry_for() {
  local vm="$1"

  if [ "$vm" = "vm1" ]; then
    echo "$REGISTRY_VM1"
  elif [ "$vm" = "vm2" ]; then
    echo "$REGISTRY_VM2"
  else
    echo "Unknown VM: $vm" >&2
    exit 1
  fi
}

floating_ip_for() {
  local vm="$1"

  if [ "$vm" = "vm1" ]; then
    echo "$FLOATING_IP_VM1"
  elif [ "$vm" = "vm2" ]; then
    echo "$FLOATING_IP_VM2"
  else
    echo "Unknown VM: $vm" >&2
    exit 1
  fi
}

next_url_for() {
  local current_vm="$1"
  local next_service="$2"
  local next_vm="$3"

  if [ "$current_vm" = "$next_vm" ]; then
    internal_url "$next_service"
  else
    public_url "$next_service" "$next_vm"
  fi
}

deploy_f3() {
  local vm="$1"
  local kubeconfig registry floating_ip

  kubeconfig="$(kubeconfig_for "$vm")"
  registry="$(registry_for "$vm")"
  floating_ip="$(floating_ip_for "$vm")"

  echo "Deploying f3 to $vm"

  KUBECONFIG="$kubeconfig" kn service apply f3 \
    --image "${registry}/f3:latest" \
    --env VM_FLOATING_IP="$floating_ip"
}

deploy_f2() {
  local vm="$1"
  local f3_vm="$2"
  local kubeconfig registry floating_ip f3_url

  kubeconfig="$(kubeconfig_for "$vm")"
  registry="$(registry_for "$vm")"
  floating_ip="$(floating_ip_for "$vm")"
  f3_url="$(next_url_for "$vm" f3 "$f3_vm")"

  echo "Deploying f2 to $vm"
  echo "  F3_URL=$f3_url"

  KUBECONFIG="$kubeconfig" kn service apply f2 \
    --image "${registry}/f2:latest" \
    --env VM_FLOATING_IP="$floating_ip" \
    --env F3_URL="$f3_url"
}

deploy_f1() {
  local vm="$1"
  local f2_vm="$2"
  local kubeconfig registry floating_ip f2_url

  kubeconfig="$(kubeconfig_for "$vm")"
  registry="$(registry_for "$vm")"
  floating_ip="$(floating_ip_for "$vm")"
  f2_url="$(next_url_for "$vm" f2 "$f2_vm")"

  echo "Deploying f1 to $vm"
  echo "  F2_URL=$f2_url"

  KUBECONFIG="$kubeconfig" kn service apply f1 \
    --image "${registry}/f1:latest" \
    --env VM_FLOATING_IP="$floating_ip" \
    --env F2_URL="$f2_url"
}

echo "===== Split placement deployment ====="
echo "Using stable registry names: host.docker.internal:5000 and host.docker.internal:5001"
echo "Placement:"
echo "  f1 -> $F1_VM"
echo "  f2 -> $F2_VM"
echo "  f3 -> $F3_VM"
echo

echo "Cleaning previous f1/f2/f3 services from both VMs..."
KUBECONFIG="$KUBECONFIG_VM1" kubectl delete ksvc f1 f2 f3 --ignore-not-found
KUBECONFIG="$KUBECONFIG_VM2" kubectl delete ksvc f1 f2 f3 --ignore-not-found
echo

# Deploy downstream first so URLs exist when upstream functions call them.
deploy_f3 "$F3_VM"
deploy_f2 "$F2_VM" "$F3_VM"
deploy_f1 "$F1_VM" "$F2_VM"

ENTRY_URL="$(public_url f1 "$F1_VM")"

echo
echo "Selected entry URL:"
echo "$ENTRY_URL"
echo "$ENTRY_URL" > /tmp/selected_chain_url.txt

echo
echo "Current services on VM-1:"
KUBECONFIG="$KUBECONFIG_VM1" kubectl get ksvc f1 f2 f3 --ignore-not-found

echo
echo "Current services on VM-2:"
KUBECONFIG="$KUBECONFIG_VM2" kubectl get ksvc f1 f2 f3 --ignore-not-found
