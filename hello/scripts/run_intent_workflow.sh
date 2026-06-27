#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

VM1_PUBLIC_IP="${VM1_PUBLIC_IP:-129.114.25.182}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/chameleon_new}"

FUNC_NAME="${FUNC_NAME:-hello}"

REGISTRY_VM1="${REGISTRY_VM1:-host.docker.internal:5000/elif}"
REGISTRY_VM2="${REGISTRY_VM2:-host.docker.internal:5001/elif}"

FLOATING_IP_VM1="${FLOATING_IP_VM1:-129.114.25.182}"
FLOATING_IP_VM2="${FLOATING_IP_VM2:-129.114.25.80}"

REMOTE_CONTROLLER_DIR="${REMOTE_CONTROLLER_DIR:-~/intent-controller}"

echo "===== Local developer workflow ====="
echo "1. Test registry tunnels"
echo "2. Build and push latest function image to both VM registries"
echo "3. Copy latest intent.json to VM-1 controller"
echo "4. Copy stable registry configuration to VM-1 controller"
echo "5. Trigger VM-1 controller"
echo

echo "Required tunnels:"
echo "  VM-1 registry/API: 127.0.0.1:5000 and 127.0.0.1:6443"
echo "  VM-2 registry/API: 127.0.0.1:5001 and 127.0.0.1:6444"
echo

echo "===== Makefile configuration ====="
make check
echo

echo "===== Testing registry tunnels ====="
make test-registries
echo

echo "===== Creating controller.env with stable registry names ====="
cat > /tmp/controller.env <<EOF
FUNC_NAME=${FUNC_NAME}
REGISTRY_VM1=${REGISTRY_VM1}
REGISTRY_VM2=${REGISTRY_VM2}
FLOATING_IP_VM1=${FLOATING_IP_VM1}
FLOATING_IP_VM2=${FLOATING_IP_VM2}
EOF

cat /tmp/controller.env
echo

echo "===== Building and pushing latest image to both registries ====="
make build-push-all
echo

echo "===== Make both candidate placements real and reachable before the controller decides ====="
make deploy-vm1-existing
make deploy-vm2-existing
echo

echo "===== Ensuring VM-1 intent controller folder exists ====="
ssh -i "$SSH_KEY" "cc@${VM1_PUBLIC_IP}" "mkdir -p ${REMOTE_CONTROLLER_DIR}/scripts"

echo "===== Copying intent.json to VM-1 controller ====="
scp -i "$SSH_KEY" intent.json "cc@${VM1_PUBLIC_IP}:${REMOTE_CONTROLLER_DIR}/intent.json"
echo

echo "===== Copying controller.env to VM-1 controller ====="
scp -i "$SSH_KEY" /tmp/controller.env "cc@${VM1_PUBLIC_IP}:${REMOTE_CONTROLLER_DIR}/controller.env"
echo

echo "===== Triggering VM-1 controller ====="
ssh -i "$SSH_KEY" "cc@${VM1_PUBLIC_IP}" \
  "cd ${REMOTE_CONTROLLER_DIR} && ./scripts/intent_deploy_vm1_controller.sh"
echo

echo "===== Getting selected function URL ====="
SELECTED_URL="$(ssh -i "$SSH_KEY" "cc@${VM1_PUBLIC_IP}" 'cat /tmp/selected_function_url.txt')"
echo "Selected function URL: $SELECTED_URL"
echo

echo "===== Invoking selected function from MacBook ====="
curl -s "$SELECTED_URL"
echo
