#!/usr/bin/env bash
set -euo pipefail

INTENT_FILE="${1:-intent.json}"

OBJECTIVE="$(python3 -c 'import json; print(json.load(open("'"$INTENT_FILE"'"))["objective"])')"
POLICY="$(python3 -c 'import json; print(json.load(open("'"$INTENT_FILE"'"))["policy"])')"
MAX_LATENCY_MS="$(python3 -c 'import json; print(json.load(open("'"$INTENT_FILE"'"))["requirements"].get("latency_ms", 999999))')"

echo "Intent objective: $OBJECTIVE"
echo "Policy: $POLICY"
echo "Latency requirement: <= ${MAX_LATENCY_MS} ms"
echo

METRICS="$(./scripts/collect_metrics.sh)"

VM1_CPU="$(echo "$METRICS" | grep VM1_CPU | cut -d= -f2)"
VM2_CPU="$(echo "$METRICS" | grep VM2_CPU | cut -d= -f2)"
VM1_MEMORY_BYTES="$(echo "$METRICS" | grep VM1_MEMORY_BYTES | cut -d= -f2)"
VM2_MEMORY_BYTES="$(echo "$METRICS" | grep VM2_MEMORY_BYTES | cut -d= -f2)"
VM1_LATENCY_MS="$(echo "$METRICS" | grep VM1_LATENCY_MS | cut -d= -f2)"
VM2_LATENCY_MS="$(echo "$METRICS" | grep VM2_LATENCY_MS | cut -d= -f2)"

echo "Collected metrics:"
echo "  VM-1 CPU:          $VM1_CPU cores"
echo "  VM-2 CPU:          $VM2_CPU cores"
echo "  VM-1 memory:       $VM1_MEMORY_BYTES bytes"
echo "  VM-2 memory:       $VM2_MEMORY_BYTES bytes"
echo "  VM-1 latency:      $VM1_LATENCY_MS ms"
echo "  VM-2 latency:      $VM2_LATENCY_MS ms"
echo

if [ "$POLICY" = "choose_lowest_latency" ]; then
  DECISION_OUTPUT="$(python3 - <<EOF
vm1_latency=float("$VM1_LATENCY_MS")
vm2_latency=float("$VM2_LATENCY_MS")
limit=float("$MAX_LATENCY_MS")

if vm1_latency <= vm2_latency:
    selected="vm1"
    selected_latency=vm1_latency
else:
    selected="vm2"
    selected_latency=vm2_latency

satisfied = selected_latency <= limit

print(selected)
print("true" if satisfied else "false")
print(selected_latency)
EOF
)"

elif [ "$POLICY" = "choose_lowest_cpu" ]; then
  DECISION_OUTPUT="$(python3 - <<EOF
vm1_cpu=float("$VM1_CPU")
vm2_cpu=float("$VM2_CPU")

if vm1_cpu <= vm2_cpu:
    selected="vm1"
    selected_value=vm1_cpu
else:
    selected="vm2"
    selected_value=vm2_cpu

print(selected)
print("true")
print(selected_value)
EOF
)"

else
  echo "Unsupported policy: $POLICY"
  exit 1
fi

SELECTED_VM="$(echo "$DECISION_OUTPUT" | sed -n '1p')"
SATISFIED="$(echo "$DECISION_OUTPUT" | sed -n '2p')"
SELECTED_VALUE="$(echo "$DECISION_OUTPUT" | sed -n '3p')"

echo "Decision:"
echo "  Selected deployment target: $SELECTED_VM"
echo "  Intent satisfied: $SATISFIED"
echo "  Selected metric value: $SELECTED_VALUE"

echo "$SELECTED_VM" > /tmp/selected_vm.txt
echo "$SATISFIED" > /tmp/intent_satisfied.txt
echo "$SELECTED_VALUE" > /tmp/selected_metric_value.txt