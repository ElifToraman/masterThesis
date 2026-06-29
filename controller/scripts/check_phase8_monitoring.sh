#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"
OUTPUT_FILE="/tmp/phase8-monitoring-snapshot.json"

cd "$REPOSITORY_ROOT"

python3 controller/monitoring.py \
  --output "$OUTPUT_FILE"

jq -e '
  .schema_version == 1

  and (.clusters | length) == 2

  and .clusters["vm1-cluster"].status == "healthy"
  and .clusters["vm2-cluster"].status == "healthy"

  and (
    .clusters["vm1-cluster"]
    .summary.reachable == true
  )

  and (
    .clusters["vm2-cluster"]
    .summary.reachable == true
  )

  and (
    .clusters["vm1-cluster"]
    .summary.knative_ready == true
  )

  and (
    .clusters["vm2-cluster"]
    .summary.knative_ready == true
  )

  and (
    .clusters["vm1-cluster"]
    .summary.prometheus_ready == true
  )

  and (
    .clusters["vm2-cluster"]
    .summary.prometheus_ready == true
  )

  and (
    .clusters["vm1-cluster"]
    .summary.ready_nodes == 3
  )

  and (
    .clusters["vm2-cluster"]
    .summary.ready_nodes == 3
  )

  and (
    .clusters["vm1-cluster"]
    .summary.ready_workers == 2
  )

  and (
    .clusters["vm2-cluster"]
    .summary.ready_workers == 2
  )

  and (
    .clusters["vm1-cluster"]
    .summary.available_cpu_millicores != null
  )

  and (
    .clusters["vm2-cluster"]
    .summary.available_cpu_millicores != null
  )

  and (
    .clusters["vm1-cluster"]
    .summary.available_memory_mb != null
  )

  and (
    .clusters["vm2-cluster"]
    .summary.available_memory_mb != null
  )

  and (
    .clusters["vm1-cluster"]
    .kubernetes.function.replica_count >= 1
  )

  and (
    .clusters["vm2-cluster"]
    .kubernetes.function.replica_count >= 1
  )
' "$OUTPUT_FILE" >/dev/null

echo "===== Cluster monitoring summaries ====="

jq '
  .clusters
  | to_entries[]
  | {
      cluster: .key,
      status: .value.status,
      ready_workers: .value.summary.ready_workers,
      cpu_load_percent:
        .value.summary.cpu_load_percent,
      memory_load_percent:
        .value.summary.memory_load_percent,
      available_cpu_millicores:
        .value.summary.available_cpu_millicores,
      available_memory_mb:
        .value.summary.available_memory_mb,
      function_replicas:
        .value.summary.function_replicas
    }
' "$OUTPUT_FILE"

echo
echo "Phase 8 monitoring verification passed."
