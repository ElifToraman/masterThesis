#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"
RUN_ID="phase9-$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIRECTORY="$REPOSITORY_ROOT/controller/results/benchmarks/$RUN_ID"
PROFILE="$RUN_DIRECTORY/profile.json"
REQUESTS="$RUN_DIRECTORY/requests.csv"
RESOURCES="$RUN_DIRECTORY/resource_samples.csv"

cd "$REPOSITORY_ROOT"

echo "===== Input validation ====="

python3 controller/validate_inputs.py

echo
echo "===== Prometheus readiness ====="

curl -fsS http://127.0.0.1:19091/-/ready
curl -fsS http://127.0.0.1:19092/-/ready

echo
echo "===== Running isolated benchmarks ====="

python3 controller/benchmark.py \
  --run-id "$RUN_ID"

echo
echo "===== Validating output files ====="

test -s "$PROFILE"
test -s "$REQUESTS"
test -s "$RESOURCES"

EXPECTED_WARMUPS=$(
  jq -r \
    '.benchmarking.warmup_requests' \
    controller/config/controller_config.json
)

EXPECTED_MEASURED=$(
  jq -r \
    '.benchmarking.measured_requests' \
    controller/config/controller_config.json
)

EXPECTED_TOTAL_ROWS=$(( (EXPECTED_WARMUPS + EXPECTED_MEASURED) * 2 ))

ACTUAL_REQUEST_ROWS=$(
  tail -n +2 "$REQUESTS" |
  wc -l
)

if [[ "$ACTUAL_REQUEST_ROWS" -ne "$EXPECTED_TOTAL_ROWS" ]]; then
  echo \
    "Unexpected request row count: expected=$EXPECTED_TOTAL_ROWS actual=$ACTUAL_REQUEST_ROWS" \
    >&2
  exit 1
fi

RESOURCE_ROWS=$(
  tail -n +2 "$RESOURCES" |
  wc -l
)

if [[ "$RESOURCE_ROWS" -lt 2 ]]; then
  echo "Insufficient resource samples: $RESOURCE_ROWS" >&2
  exit 1
fi

jq -e \
  --argjson expected_warmups "$EXPECTED_WARMUPS" \
  --argjson expected_measured "$EXPECTED_MEASURED" \
  '
    .schema_version == 1

    and .benchmarking_only == true

    and .placement_decision == null

    and (.clusters | length) == 2

    and .clusters["vm1-cluster"].status == "success"
    and .clusters["vm2-cluster"].status == "success"

    and (
      .clusters["vm1-cluster"].warmup_requests
      == $expected_warmups
    )

    and (
      .clusters["vm2-cluster"].warmup_requests
      == $expected_warmups
    )

    and (
      .clusters["vm1-cluster"].measured_requests
      == $expected_measured
    )

    and (
      .clusters["vm2-cluster"].measured_requests
      == $expected_measured
    )

    and (
      .clusters["vm1-cluster"].successful_requests
      == $expected_measured
    )

    and (
      .clusters["vm2-cluster"].successful_requests
      == $expected_measured
    )

    and (
      .clusters["vm1-cluster"].failed_requests
      == 0
    )

    and (
      .clusters["vm2-cluster"].failed_requests
      == 0
    )

    and (
      .clusters["vm1-cluster"].mean_latency_ms
      != null
    )

    and (
      .clusters["vm2-cluster"].mean_latency_ms
      != null
    )

    and (
      .clusters["vm1-cluster"].median_latency_ms
      != null
    )

    and (
      .clusters["vm2-cluster"].median_latency_ms
      != null
    )

    and (
      .clusters["vm1-cluster"].p95_latency_ms
      != null
    )

    and (
      .clusters["vm2-cluster"].p95_latency_ms
      != null
    )

    and (
      .clusters["vm1-cluster"].average_cpu_millicores
      != null
    )

    and (
      .clusters["vm2-cluster"].average_cpu_millicores
      != null
    )

    and (
      .clusters["vm1-cluster"].peak_cpu_millicores
      != null
    )

    and (
      .clusters["vm2-cluster"].peak_cpu_millicores
      != null
    )

    and (
      .clusters["vm1-cluster"].average_memory_mb
      != null
    )

    and (
      .clusters["vm2-cluster"].average_memory_mb
      != null
    )

    and (
      .clusters["vm1-cluster"].peak_memory_mb
      != null
    )

    and (
      .clusters["vm2-cluster"].peak_memory_mb
      != null
    )

    and (
      .clusters["vm1-cluster"].worker_node
      | startswith("vm1-cluster-worker")
    )

    and (
      .clusters["vm2-cluster"].worker_node
      | startswith("vm2-cluster-worker")
    )
  ' "$PROFILE" >/dev/null

echo
echo "===== Benchmark summaries ====="

jq '
  .clusters
  | to_entries[]
  | {
      cluster: .key,
      status: .value.status,
      pod: .value.pod,
      worker_node: .value.worker_node,
      mean_latency_ms: .value.mean_latency_ms,
      median_latency_ms: .value.median_latency_ms,
      p95_latency_ms: .value.p95_latency_ms,
      average_cpu_millicores:
        .value.average_cpu_millicores,
      peak_cpu_millicores:
        .value.peak_cpu_millicores,
      average_memory_mb:
        .value.average_memory_mb,
      peak_memory_mb:
        .value.peak_memory_mb
    }
' "$PROFILE"

echo
echo "===== Cleanup verification ====="

for context in vm1-cluster vm2-cluster; do
  if kubectl --context "$context" \
    get namespace benchmark \
    >/dev/null 2>&1; then

    echo \
      "Benchmark namespace still exists in $context" \
      >&2
    exit 1
  fi

  echo "$context: benchmark namespace deleted"
done

echo
echo "RUN_DIRECTORY=$RUN_DIRECTORY"
echo "Phase 9 benchmarking verification passed."
