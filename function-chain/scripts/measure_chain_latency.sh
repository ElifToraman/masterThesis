#!/usr/bin/env bash
set -euo pipefail

SAMPLES="${1:-5}"
WORK_MS="${2:-50}"
SLA_MS="${3:-500}"

KUBECONFIG_VM1="${HOME}/.kube/vm1-config"
KUBECONFIG_VM2="${HOME}/.kube/vm2-config"

RESULTS_FILE="/tmp/chain_latency_results.csv"

get_url() {
  local kubeconfig="$1"
  local service="$2"
  KUBECONFIG="$kubeconfig" kubectl get ksvc "$service" -o jsonpath='{.status.url}'
}

measure_one() {
  local label="$1"
  local url="$2"
  local work_ms="$3"
  local sla_ms="$4"
  local sample="$5"

  tmp_body="$(mktemp)"

  external_seconds="$(
    curl -s \
      -o "$tmp_body" \
      -w "%{time_total}" \
      -X POST "$url" \
      -H "Content-Type: application/json" \
      -d "{\"work_ms\":${work_ms}}"
  )"

  external_ms="$(
    python3 - <<PY
print(round(float("${external_seconds}") * 1000, 2))
PY
  )"

  internal_ms="$(
    python3 - "$tmp_body" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    print(data.get("chain_duration_ms", "NA"))
except Exception:
    print("NA")
PY
  )"

  violation="$(
    python3 - <<PY
external_ms = float("${external_ms}")
sla_ms = float("${sla_ms}")
print("true" if external_ms > sla_ms else "false")
PY
  )"

  echo "${label},${sample},${external_ms},${internal_ms},${violation}" | tee -a "$RESULTS_FILE"

  rm -f "$tmp_body"
}

VM1_URL="$(get_url "$KUBECONFIG_VM1" f1)"
VM2_URL="$(get_url "$KUBECONFIG_VM2" f1)"

rm -f "$RESULTS_FILE"

echo "target,sample,external_latency_ms,internal_chain_duration_ms,sla_violation" > "$RESULTS_FILE"

echo "SAMPLES=${SAMPLES}"
echo "WORK_MS=${WORK_MS}"
echo "SLA_MS=${SLA_MS}"
echo "VM1_URL=${VM1_URL}"
echo "VM2_URL=${VM2_URL}"
echo
echo "target,sample,external_latency_ms,internal_chain_duration_ms,sla_violation"

for i in $(seq 1 "$SAMPLES"); do
  measure_one "vm1_all_chain" "$VM1_URL" "$WORK_MS" "$SLA_MS" "$i"
done

for i in $(seq 1 "$SAMPLES"); do
  measure_one "vm2_all_chain" "$VM2_URL" "$WORK_MS" "$SLA_MS" "$i"
done

echo
echo "===== Summary ====="

python3 - <<PY
import csv
from statistics import mean

path = "${RESULTS_FILE}"

rows = []
with open(path) as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

for target in ["vm1_all_chain", "vm2_all_chain"]:
    subset = [r for r in rows if r["target"] == target]

    external = [float(r["external_latency_ms"]) for r in subset]
    internal = [float(r["internal_chain_duration_ms"]) for r in subset if r["internal_chain_duration_ms"] != "NA"]
    violations = [r["sla_violation"] == "true" for r in subset]

    warm_subset = [r for r in subset if int(r["sample"]) > 1]
    warm_external = [float(r["external_latency_ms"]) for r in warm_subset]
    warm_internal = [float(r["internal_chain_duration_ms"]) for r in warm_subset if r["internal_chain_duration_ms"] != "NA"]
    warm_violations = [r["sla_violation"] == "true" for r in warm_subset]

    print(f"{target}:")
    print(f"  avg external latency:      {round(mean(external), 2)} ms")
    print(f"  avg internal chain:        {round(mean(internal), 2)} ms")
    print(f"  violation rate external:   {round(sum(violations)/len(violations)*100, 2)}%")
    print(f"  warm avg external latency: {round(mean(warm_external), 2)} ms")
    print(f"  warm avg internal chain:   {round(mean(warm_internal), 2)} ms")
    print(f"  warm violation rate:       {round(sum(warm_violations)/len(warm_violations)*100, 2)}%")
    print()
PY

echo "Raw results saved to: $RESULTS_FILE"
