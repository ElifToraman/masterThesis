#!/bin/bash
# bootstrap.sh — Run this on your Mac whenever you change networks.
# It detects your current LAN IP, updates the Makefile, adds containerd
# trust entries on both VMs, updates Docker Desktop's insecure-registries,
# and opens SSH tunnels.
#
# Usage:
#   chmod +x bootstrap.sh   (first time only)
#   ./bootstrap.sh           (every time you start a session or move networks)

set -euo pipefail

# ─── CONFIG ───────────────────────────────────────────────────────────
SSH_KEY="$HOME/.ssh/chameleon_new"
SSH_USER="cc"

VM1_FLOATING_IP="129.114.25.182"
VM2_FLOATING_IP="129.114.25.80"

VM1_API_PORT="46005"
VM2_API_PORT="40017"

VM1_KIND_NODE="vm1-cluster-control-plane"
VM2_KIND_NODE="vm2-cluster-control-plane"

MAKEFILE_PATH="$HOME/thesis/hello/Makefile"

DOCKER_CONFIG="$HOME/.docker/daemon.json"
# ─────────────────────────────────────────────────────────────────────

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'  # no color

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

# ─── STEP 1: Detect current LAN IP ──────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Thesis testbed bootstrap"
echo "═══════════════════════════════════════════════════════"
echo ""

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || true)
if [ -z "$LAN_IP" ]; then
    LAN_IP=$(ipconfig getifaddr en1 2>/dev/null || true)
fi
if [ -z "$LAN_IP" ]; then
    err "Could not detect LAN IP on en0 or en1."
    echo "    Run 'ifconfig | grep \"inet \"' and find your local IP."
    echo "    Then set it manually: LAN_IP=x.x.x.x ./bootstrap.sh"
    exit 1
fi

info "Detected LAN IP: $LAN_IP"

# ─── STEP 2: Update Docker Desktop insecure-registries ───────────────
echo ""
echo "── Updating Docker Desktop insecure-registries ──"

# Read current daemon.json, add new entries if missing
if [ ! -f "$DOCKER_CONFIG" ]; then
    warn "No $DOCKER_CONFIG found. Creating one."
    echo '{}' > "$DOCKER_CONFIG"
fi

# Use python3 (available on all Macs) to merge insecure-registries
python3 << PYEOF
import json, sys

config_path = "$DOCKER_CONFIG"
lan_ip = "$LAN_IP"

with open(config_path, 'r') as f:
    config = json.load(f)

registries = set(config.get('insecure-registries', []))
new_entries = [f"{lan_ip}:5000", f"{lan_ip}:5001"]

added = []
for entry in new_entries:
    if entry not in registries:
        registries.add(entry)
        added.append(entry)

config['insecure-registries'] = sorted(list(registries))

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')

if added:
    print(f"  Added to insecure-registries: {', '.join(added)}")
    print(f"  ⚠️  You need to restart Docker Desktop for this to take effect.")
    print(f"     Docker Desktop → Settings → Docker Engine → Apply & restart")
    print(f"     OR just restart Docker Desktop from the menu bar.")
else:
    print(f"  Already configured for {lan_ip}:5000 and {lan_ip}:5001")
PYEOF

# ─── STEP 3: Update Makefile ─────────────────────────────────────────
echo ""
echo "── Updating Makefile registry addresses ──"

if [ -f "$MAKEFILE_PATH" ]; then
    # Extract current registry IP from Makefile
    CURRENT_IP=$(grep 'REGISTRY_VM1' "$MAKEFILE_PATH" | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || true)

    if [ "$CURRENT_IP" = "$LAN_IP" ]; then
        info "Makefile already uses $LAN_IP. No change needed."
    elif [ -n "$CURRENT_IP" ]; then
        sed -i '' "s|$CURRENT_IP|$LAN_IP|g" "$MAKEFILE_PATH"
        info "Updated Makefile: $CURRENT_IP → $LAN_IP"
    else
        warn "Could not find an IP in Makefile. Check $MAKEFILE_PATH manually."
    fi
else
    warn "Makefile not found at $MAKEFILE_PATH. Skipping."
fi

# ─── STEP 4: Add containerd hosts.toml on both VMs ──────────────────
echo ""
echo "── Configuring containerd trust on VM-1 ──"

add_containerd_trust() {
    local vm_ip="$1"
    local kind_node="$2"
    local registry_addr="$3"

    ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$SSH_USER@$vm_ip" \
        "docker exec $kind_node mkdir -p /etc/containerd/certs.d/$registry_addr && \
         echo '[host.\"http://registry:5000\"]' | docker exec -i $kind_node cp /dev/stdin /etc/containerd/certs.d/$registry_addr/hosts.toml" \
        2>/dev/null

    if [ $? -eq 0 ]; then
        info "VM ($vm_ip): containerd trusts $registry_addr → registry:5000"
    else
        err "VM ($vm_ip): failed to configure containerd for $registry_addr"
    fi
}

add_containerd_trust "$VM1_FLOATING_IP" "$VM1_KIND_NODE" "${LAN_IP}:5000"

echo ""
echo "── Configuring containerd trust on VM-2 ──"

add_containerd_trust "$VM2_FLOATING_IP" "$VM2_KIND_NODE" "${LAN_IP}:5001"

# ─── STEP 5: Check if tunnels are already running ────────────────────
echo ""
echo "── Checking existing SSH tunnels ──"

TUNNEL_VM1_OK=false
TUNNEL_VM2_OK=false

if lsof -i :5000 -P -n 2>/dev/null | grep -q LISTEN; then
    TUNNEL_VM1_OK=true
    info "VM-1 registry tunnel already listening on :5000"
else
    warn "VM-1 registry tunnel not running (port 5000)"
fi

if lsof -i :5001 -P -n 2>/dev/null | grep -q LISTEN; then
    TUNNEL_VM2_OK=true
    info "VM-2 registry tunnel already listening on :5001"
else
    warn "VM-2 registry tunnel not running (port 5001)"
fi

if lsof -i :${VM1_API_PORT} -P -n 2>/dev/null | grep -q LISTEN; then
    info "VM-1 API tunnel already listening on :${VM1_API_PORT}"
else
    warn "VM-1 API tunnel not running (port ${VM1_API_PORT})"
fi

if lsof -i :${VM2_API_PORT} -P -n 2>/dev/null | grep -q LISTEN; then
    info "VM-2 API tunnel already listening on :${VM2_API_PORT}"
else
    warn "VM-2 API tunnel not running (port ${VM2_API_PORT})"
fi

# ─── STEP 6: Print tunnel commands ──────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Bootstrap complete!"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  LAN IP:  $LAN_IP"
echo "  VM-1:    $VM1_FLOATING_IP (API port $VM1_API_PORT)"
echo "  VM-2:    $VM2_FLOATING_IP (API port $VM2_API_PORT)"
echo ""

if [ "$TUNNEL_VM1_OK" = true ] && [ "$TUNNEL_VM2_OK" = true ]; then
    info "All tunnels are running. You're ready to go."
    echo ""
    echo "  Quick test:"
    echo "    export KUBECONFIG=~/.kube/vm1-config && kubectl get nodes"
    echo "    export KUBECONFIG=~/.kube/vm2-config && kubectl get nodes"
else
    echo "  Open these SSH tunnels in separate terminals:"
    echo ""
    echo "  ┌─ Terminal A (VM-1) ──────────────────────────────────────┐"
    echo "  │ ssh -i $SSH_KEY \\                                       │"
    echo "  │   -L ${LAN_IP}:5000:localhost:5000 \\                     │"
    echo "  │   -L 127.0.0.1:${VM1_API_PORT}:localhost:${VM1_API_PORT} \\│"
    echo "  │   ${SSH_USER}@${VM1_FLOATING_IP}                         │"
    echo "  └──────────────────────────────────────────────────────────┘"
    echo ""
    echo "  ┌─ Terminal B (VM-2) ──────────────────────────────────────┐"
    echo "  │ ssh -i $SSH_KEY \\                                       │"
    echo "  │   -L ${LAN_IP}:5001:localhost:5000 \\                     │"
    echo "  │   -L 127.0.0.1:${VM2_API_PORT}:localhost:${VM2_API_PORT} \\│"
    echo "  │   ${SSH_USER}@${VM2_FLOATING_IP}                         │"
    echo "  └──────────────────────────────────────────────────────────┘"
    echo ""
    echo "  After opening tunnels, test with:"
    echo "    export KUBECONFIG=~/.kube/vm1-config && kubectl get nodes"
    echo "    export KUBECONFIG=~/.kube/vm2-config && kubectl get nodes"
fi

echo ""
