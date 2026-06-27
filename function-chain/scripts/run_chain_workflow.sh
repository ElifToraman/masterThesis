#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

VM1_PUBLIC_IP="129.114.25.182"
SSH_KEY="$HOME/.ssh/chameleon_new"

WORK_MS="${WORK_MS:-50}"

echo "===== Local developer workflow for function chain ====="
echo "1. Build and push f1/f2/f3 images to both VM registries"
echo "2. Copy intent, application descriptor, and controller config to VM-1"
echo "3. Copy stable registry configuration to VM-1 controller"
echo "4. Trigger VM-1 chain controller"
echo "5. Invoke selected chain entry URL from MacBook"
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
cat > /tmp/chain_controller.env <<EOF
REGISTRY_VM1=host.docker.internal:5000/elif
REGISTRY_VM2=host.docker.internal:5001/elif
EOF

cat /tmp/chain_controller.env
echo

echo "===== Building and pushing f1/f2/f3 to both registries ====="
make build-push-all
echo

echo "===== Make both chain candidate placements real and reachable before the controller decides ====="
make deploy-vm1-existing
make deploy-vm2-existing
echo

echo "===== Ensuring VM-1 chain controller folder exists ====="
ssh -i "$SSH_KEY" "cc@${VM1_PUBLIC_IP}" 'mkdir -p ~/chain-controller/scripts'

echo "===== Copying intent, application descriptor, and controller config to VM-1 ====="
scp -i "$SSH_KEY" chain_intent.json "cc@${VM1_PUBLIC_IP}:~/chain-controller/chain_intent.json"
scp -i "$SSH_KEY" application_descriptor.json "cc@${VM1_PUBLIC_IP}:~/chain-controller/application_descriptor.json"
scp -i "$SSH_KEY" controller_config.json "cc@${VM1_PUBLIC_IP}:~/chain-controller/controller_config.json"
echo

echo "===== Copying controller.env to VM-1 chain controller ====="
scp -i "$SSH_KEY" /tmp/chain_controller.env "cc@${VM1_PUBLIC_IP}:~/chain-controller/controller.env"
echo

echo "===== Triggering VM-1 chain controller ====="
ssh -i "$SSH_KEY" "cc@${VM1_PUBLIC_IP}" \
  'cd ~/chain-controller && ./scripts/chain_controller.sh'
echo

echo "===== Getting selected chain URL ====="
SELECTED_URL="$(ssh -i "$SSH_KEY" "cc@${VM1_PUBLIC_IP}" 'cat /tmp/selected_chain_url.txt')"
echo "Selected chain URL: $SELECTED_URL"
echo

echo "===== Invoking selected chain from MacBook ====="
curl -s -X POST "$SELECTED_URL" \
  -H "Content-Type: application/json" \
  -d "{\"work_ms\":${WORK_MS}}" | python3 -m json.tool
