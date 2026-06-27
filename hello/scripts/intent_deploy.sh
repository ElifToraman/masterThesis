#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "===== Intent ====="
cat intent.json
echo

echo "===== Checking current Makefile configuration ====="
make check
echo

echo "===== Collecting metrics and making decision ====="
./scripts/decide_deploy.sh intent.json
SELECTED_VM="$(cat /tmp/selected_vm.txt)"
INTENT_SATISFIED="$(cat /tmp/intent_satisfied.txt)"
SELECTED_VALUE="$(cat /tmp/selected_metric_value.txt)"
echo

echo "===== Deployment decision output ====="
echo "Selected VM: $SELECTED_VM"
echo "Intent satisfied: $INTENT_SATISFIED"
echo "Selected metric value: $SELECTED_VALUE"
echo

echo "===== Deploying based on decision ====="

if [ "$SELECTED_VM" = "vm1" ]; then
  echo "Deploying function to VM-1..."
  make deploy-vm1-existing
  echo "Invoking VM-1 function..."
  make invoke-vm1

elif [ "$SELECTED_VM" = "vm2" ]; then
  echo "Deploying function to VM-2..."
  make deploy-vm2-existing
  echo "Invoking VM-2 function..."
  make invoke-vm2

else
  echo "Unknown selected VM: $SELECTED_VM"
  exit 1
fi

echo
echo "===== Done ====="
echo "Function deployed and invoked on: $SELECTED_VM"
echo "Intent satisfied: $INTENT_SATISFIED"