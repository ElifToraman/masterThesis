#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"
EXPERIMENT_ID="phase12-$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_LOG="/tmp/phase12-result-collector-output.txt"

cd "$REPOSITORY_ROOT"

BENCHMARK_PROFILE="$REPOSITORY_ROOT/controller/results/benchmarks/latest-profile.json"
DECISION="$REPOSITORY_ROOT/controller/results/decisions/latest-decision.json"
EXECUTION="$REPOSITORY_ROOT/controller/results/deployments/latest-execution.json"

echo "===== Existing artifact validation ====="

test -s "$BENCHMARK_PROFILE"
test -s "$DECISION"
test -s "$EXECUTION"

MONITORING_SOURCE=$(
  jq -r '.inputs.monitoring_snapshot' "$DECISION"
)

test -s "$MONITORING_SOURCE"

DECISION_ID=$(
  jq -r '.decision_id' "$DECISION"
)

EXECUTION_DECISION_ID=$(
  jq -r '.decision_id' "$EXECUTION"
)

test "$DECISION_ID" = "$EXECUTION_DECISION_ID"

SELECTED_CLUSTER=$(
  jq -r '.selected_cluster' "$DECISION"
)

EXECUTION_CLUSTER=$(
  jq -r '.selected_cluster' "$EXECUTION"
)

test "$SELECTED_CLUSTER" = "$EXECUTION_CLUSTER"

echo "Decision ID: $DECISION_ID"
echo "Selected cluster: $SELECTED_CLUSTER"
echo "Monitoring source: $MONITORING_SOURCE"

echo
echo "===== Collecting Phase 12 outputs ====="

python3 controller/result_collector.py \
  --experiment-id "$EXPERIMENT_ID" \
  --monitoring-snapshot "$MONITORING_SOURCE" |
tee "$OUTPUT_LOG"

EXPERIMENT_DIRECTORY=$(
  grep '^EXPERIMENT_DIRECTORY=' "$OUTPUT_LOG" |
  tail -n 1 |
  cut -d= -f2-
)

test -n "$EXPERIMENT_DIRECTORY"
test -d "$EXPERIMENT_DIRECTORY"

echo
echo "===== Checking generated files ====="

REQUIRED_FILES=(
  benchmark_results.csv
  benchmark_profiles.json
  placement_results.csv
  decision_explanation.json
  intent.json
  function_descriptor.json
  monitoring_snapshot.json
  benchmark_profile_raw.json
  benchmark_requests_raw.csv
  resource_samples.csv
  placement_decision.json
  execution_result.json
)

for file in "${REQUIRED_FILES[@]}"; do
  test -s "$EXPERIMENT_DIRECTORY/$file"
  echo "$file: present"
done

echo
echo "===== Benchmark CSV validation ====="

BENCHMARK_RUN_ID=$(
  jq -r '.run_id' "$BENCHMARK_PROFILE"
)

SOURCE_REQUESTS="$REPOSITORY_ROOT/controller/results/benchmarks/$BENCHMARK_RUN_ID/requests.csv"

EXPECTED_BENCHMARK_ROWS=$(
  tail -n +2 "$SOURCE_REQUESTS" |
  wc -l
)

ACTUAL_BENCHMARK_ROWS=$(
  tail -n +2 \
    "$EXPERIMENT_DIRECTORY/benchmark_results.csv" |
  wc -l
)

echo "Expected benchmark rows: $EXPECTED_BENCHMARK_ROWS"
echo "Actual benchmark rows:   $ACTUAL_BENCHMARK_ROWS"

test \
  "$ACTUAL_BENCHMARK_ROWS" \
  -eq \
  "$EXPECTED_BENCHMARK_ROWS"

python3 - \
  "$EXPERIMENT_DIRECTORY/benchmark_results.csv" \
  "$BENCHMARK_PROFILE" <<'PY'
import csv
import json
import sys
from collections import Counter

csv_path = sys.argv[1]
profile_path = sys.argv[2]

with open(
    csv_path,
    newline="",
    encoding="utf-8",
) as file:
    rows = list(csv.DictReader(file))

with open(
    profile_path,
    encoding="utf-8",
) as file:
    profile = json.load(file)

required_columns = {
    "timestamp",
    "cluster",
    "node",
    "pod",
    "function",
    "sample_type",
    "sample",
    "response_time_ms",
    "cpu_millicores",
    "memory_mb",
    "http_status",
    "resource_sample_timestamp",
    "resource_sample_offset_ms",
}

missing = required_columns - set(rows[0])

if missing:
    raise SystemExit(
        f"Missing benchmark columns: {sorted(missing)}"
    )

counts = Counter(
    (row["cluster"], row["sample_type"])
    for row in rows
)

for key, value in sorted(counts.items()):
    print(f"{key[0]} {key[1]}: {value}")

expected_warmups = int(profile["warmup_requests"])
expected_measured = int(profile["measured_requests"])

for cluster in profile["clusters"]:
    if counts[(cluster, "warmup")] != expected_warmups:
        raise SystemExit(
            f"Unexpected warmup count for {cluster}"
        )

    if counts[(cluster, "measured")] != expected_measured:
        raise SystemExit(
            f"Unexpected measured count for {cluster}"
        )

resource_matches = sum(
    1
    for row in rows
    if row["resource_sample_match"] != "none"
)

if resource_matches == 0:
    raise SystemExit(
        "No benchmark request was associated "
        "with a resource sample."
    )

print(
    f"Requests with associated resource samples: "
    f"{resource_matches}/{len(rows)}"
)
PY

echo
echo "===== Benchmark profile validation ====="

jq -e '
  .schema_version == 1
  and (.clusters | length) == 2
  and (
    .clusters["vm1-cluster"]
    .p95_latency_ms != null
  )
  and (
    .clusters["vm2-cluster"]
    .p95_latency_ms != null
  )
  and (
    .clusters["vm1-cluster"]
    .average_cpu_millicores != null
  )
  and (
    .clusters["vm2-cluster"]
    .average_cpu_millicores != null
  )
  and (
    .clusters["vm1-cluster"]
    .average_memory_mb != null
  )
  and (
    .clusters["vm2-cluster"]
    .average_memory_mb != null
  )
' "$EXPERIMENT_DIRECTORY/benchmark_profiles.json" \
>/dev/null

echo "Benchmark profiles are valid."

echo
echo "===== Placement CSV validation ====="

PLACEMENT_ROWS=$(
  tail -n +2 \
    "$EXPERIMENT_DIRECTORY/placement_results.csv" |
  wc -l
)

test "$PLACEMENT_ROWS" -eq 1

python3 - \
  "$EXPERIMENT_DIRECTORY/placement_results.csv" \
  "$SELECTED_CLUSTER" \
  "$DECISION_ID" <<'PY'
import csv
import sys

path = sys.argv[1]
expected_cluster = sys.argv[2]
expected_decision_id = sys.argv[3]

with open(
    path,
    newline="",
    encoding="utf-8",
) as file:
    rows = list(csv.DictReader(file))

if len(rows) != 1:
    raise SystemExit(
        f"Expected one placement row, got {len(rows)}"
    )

row = rows[0]

required_columns = {
    "experiment_id",
    "timestamp",
    "function",
    "intent_target_value",
    "selected_cluster",
    "predicted_latency_ms",
    "actual_latency_ms",
    "predicted_intent_satisfied",
    "actual_intent_satisfied",
    "vm1_available_cpu_millicores",
    "vm1_available_memory_mb",
    "vm1_load_1m",
    "vm2_available_cpu_millicores",
    "vm2_available_memory_mb",
    "vm2_load_1m",
    "decision_time_ms",
    "deployment_time_ms",
    "execution_node",
    "execution_pod",
}

missing = required_columns - set(row)

if missing:
    raise SystemExit(
        f"Missing placement columns: {sorted(missing)}"
    )

if row["selected_cluster"] != expected_cluster:
    raise SystemExit(
        "Placement row selected cluster mismatch"
    )

if row["decision_id"] != expected_decision_id:
    raise SystemExit(
        "Placement row decision ID mismatch"
    )

if not row["predicted_latency_ms"]:
    raise SystemExit(
        "Predicted latency is empty"
    )

if not row["actual_latency_ms"]:
    raise SystemExit(
        "Actual latency is empty"
    )

if not row["execution_node"]:
    raise SystemExit(
        "Execution node is empty"
    )

if not row["execution_pod"]:
    raise SystemExit(
        "Execution pod is empty"
    )

print(
    f"selected_cluster={row['selected_cluster']}"
)
print(
    f"predicted_latency_ms="
    f"{row['predicted_latency_ms']}"
)
print(
    f"actual_latency_ms="
    f"{row['actual_latency_ms']}"
)
print(
    f"predicted_intent_satisfied="
    f"{row['predicted_intent_satisfied']}"
)
print(
    f"actual_intent_satisfied="
    f"{row['actual_intent_satisfied']}"
)
print(
    f"execution_node={row['execution_node']}"
)
print(
    f"execution_pod={row['execution_pod']}"
)

if not row["decision_time_ms"]:
    print(
        "decision_time_ms is empty because "
        "Phase 10 did not record it."
    )

if not row["deployment_time_ms"]:
    print(
        "deployment_time_ms is empty because "
        "Phase 11 did not record it."
    )
PY

echo
echo "===== Decision explanation validation ====="

jq -e \
  --arg decision_id "$DECISION_ID" \
  --arg selected "$SELECTED_CLUSTER" \
  '
    .schema_version == 1
    and .selected_cluster == $selected
    and (
      .placement_decision.decision_id
      == $decision_id
    )
    and (
      .actual_execution.selected_cluster
      == $selected
    )
    and (
      .actual_execution.execution_successful
      == true
    )
    and .reason != null
    and (.candidates | length) == 2
  ' "$EXPERIMENT_DIRECTORY/decision_explanation.json" \
>/dev/null

echo "Decision explanation is valid."

echo
echo "===== Cumulative placement history ====="

HISTORY="$REPOSITORY_ROOT/controller/results/placement_results.csv"

test -s "$HISTORY"

grep -q \
  "^$EXPERIMENT_ID," \
  "$HISTORY"

echo "Experiment appended to cumulative placement_results.csv."

echo
echo "===== Export summary ====="

jq '
  {
    experiment_id,
    function,
    selected_cluster,
    decision_mode,
    predicted_intent_satisfied,
    reason,
    actual_execution: {
      execution_successful:
        .actual_execution.execution_successful,
      actual_execution_evaluation:
        .actual_execution
        .actual_execution_evaluation,
      placement:
        .actual_execution.placement
    }
  }
' "$EXPERIMENT_DIRECTORY/decision_explanation.json"

echo
echo "EXPERIMENT_DIRECTORY=$EXPERIMENT_DIRECTORY"
echo "Phase 12 result-collection verification passed."
