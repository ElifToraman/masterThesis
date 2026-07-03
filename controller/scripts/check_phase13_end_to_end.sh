#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"
RUN_ID="phase13-$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_LOG="/tmp/phase13-orchestrator-output.txt"

PIPELINE_DIRECTORY="$REPOSITORY_ROOT/controller/results/pipelines/$RUN_ID"
EXPERIMENT_DIRECTORY="$REPOSITORY_ROOT/controller/results/experiments/$RUN_ID"

PIPELINE_MANIFEST="$PIPELINE_DIRECTORY/pipeline.json"
MONITORING="$PIPELINE_DIRECTORY/monitoring.json"
DECISION="$PIPELINE_DIRECTORY/decision.json"
EXECUTION="$PIPELINE_DIRECTORY/execution.json"

cd "$REPOSITORY_ROOT"

echo "===== Existing benchmark profile ====="

test -s \
  controller/results/benchmarks/latest-profile.json

jq -e '
  .benchmarking_only == true
  and .placement_decision == null
  and (.clusters | length) == 2
' controller/results/benchmarks/latest-profile.json \
>/dev/null

echo "Existing benchmark profile is valid."

echo
echo "===== End-to-end controller run ====="

python3 controller/orchestrator.py \
  --run-id "$RUN_ID" |
tee "$OUTPUT_LOG"

echo
echo "===== Pipeline artifact validation ====="

test -s "$PIPELINE_MANIFEST"
test -s "$MONITORING"
test -s "$DECISION"
test -s "$EXECUTION"
test -d "$EXPERIMENT_DIRECTORY"

jq -e \
  --arg run_id "$RUN_ID" \
  '
    .schema_version == 1
    and .run_id == $run_id
    and .phase == "end-to-end-integration"
    and .status == "success"
    and .benchmarking_performed == false
    and .total_duration_ms > 0

    and (
      .components.monitoring.return_code == 0
    )

    and (
      .components.placement_policy.return_code == 0
    )

    and (
      .components
      .deployment_and_execution
      .return_code == 0
    )

    and (
      .components.result_collection.return_code == 0
    )

    and .decision.selected_cluster != null
    and .decision.timing.duration_ms > 0

    and .execution.execution_successful == true

    and (
      .execution
      .deployment_timing
      .duration_ms > 0
    )

    and (
      .execution
      .actual_execution_evaluation
      .measured_value != null
    )
  ' "$PIPELINE_MANIFEST" >/dev/null

echo "Pipeline manifest is valid."

echo
echo "===== Cross-phase consistency ====="

DECISION_ID=$(
  jq -r '.decision_id' "$DECISION"
)

EXECUTION_DECISION_ID=$(
  jq -r '.decision_id' "$EXECUTION"
)

test \
  "$DECISION_ID" \
  = \
  "$EXECUTION_DECISION_ID"

SELECTED_CLUSTER=$(
  jq -r '.selected_cluster' "$DECISION"
)

EXECUTION_CLUSTER=$(
  jq -r '.selected_cluster' "$EXECUTION"
)

test \
  "$SELECTED_CLUSTER" \
  = \
  "$EXECUTION_CLUSTER"

echo "Decision ID: $DECISION_ID"
echo "Selected cluster: $SELECTED_CLUSTER"

echo
echo "===== Monitoring validation ====="

jq -e '
  .clusters["vm1-cluster"].status == "healthy"
  and .clusters["vm2-cluster"].status == "healthy"
' "$MONITORING" >/dev/null

echo "Both monitored clusters were healthy."

echo
echo "===== Timing validation ====="

DECISION_TIME=$(
  jq -r '.timing.duration_ms' "$DECISION"
)

DEPLOYMENT_TIME=$(
  jq -r \
    '.deployment_timing.duration_ms' \
    "$EXECUTION"
)

TOTAL_TIME=$(
  jq -r \
    '.total_duration_ms' \
    "$PIPELINE_MANIFEST"
)

echo "Decision time:   ${DECISION_TIME}ms"
echo "Deployment time: ${DEPLOYMENT_TIME}ms"
echo "Total time:      ${TOTAL_TIME}ms"

python3 - \
  "$DECISION_TIME" \
  "$DEPLOYMENT_TIME" \
  "$TOTAL_TIME" <<'PY'
import sys

decision = float(sys.argv[1])
deployment = float(sys.argv[2])
total = float(sys.argv[3])

if decision <= 0:
    raise SystemExit("Decision time must be positive")

if deployment <= 0:
    raise SystemExit("Deployment time must be positive")

if total <= 0:
    raise SystemExit("Total time must be positive")

if total < decision + deployment:
    raise SystemExit(
        "Total pipeline time is unexpectedly smaller "
        "than decision plus deployment time"
    )
PY

echo
echo "===== Experiment output validation ====="

REQUIRED_FILES=(
  benchmark_results.csv
  benchmark_profiles.json
  placement_results.csv
  decision_explanation.json
  monitoring_snapshot.json
  placement_decision.json
  execution_result.json
)

for file in "${REQUIRED_FILES[@]}"; do
  test -s "$EXPERIMENT_DIRECTORY/$file"
  echo "$file: present"
done

echo
echo "===== Placement CSV timing validation ====="

python3 - \
  "$EXPERIMENT_DIRECTORY/placement_results.csv" \
  "$SELECTED_CLUSTER" <<'PY'
import csv
import sys

path = sys.argv[1]
expected_cluster = sys.argv[2]

with open(
    path,
    newline="",
    encoding="utf-8",
) as file:
    row = next(csv.DictReader(file))

if row["selected_cluster"] != expected_cluster:
    raise SystemExit(
        "Placement CSV selected cluster mismatch"
    )

if not row["decision_time_ms"]:
    raise SystemExit(
        "decision_time_ms is empty"
    )

if not row["deployment_time_ms"]:
    raise SystemExit(
        "deployment_time_ms is empty"
    )

if float(row["decision_time_ms"]) <= 0:
    raise SystemExit(
        "decision_time_ms must be positive"
    )

if float(row["deployment_time_ms"]) <= 0:
    raise SystemExit(
        "deployment_time_ms must be positive"
    )

print(
    f"decision_time_ms="
    f"{row['decision_time_ms']}"
)

print(
    f"deployment_time_ms="
    f"{row['deployment_time_ms']}"
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
PY

echo
echo "===== Selected deployment validation ====="

NAMESPACE=$(
  jq -r '.runtime.namespace' "$EXECUTION"
)

SERVICE_NAME=$(
  jq -r '.runtime.service_name' "$EXECUTION"
)

kubectl --context "$SELECTED_CLUSTER" \
  get kservice "$SERVICE_NAME" \
  -n "$NAMESPACE"

kubectl --context "$SELECTED_CLUSTER" \
  get pods \
  -n "$NAMESPACE" \
  -o wide

echo
echo "===== Non-selected cluster validation ====="

for context in vm1-cluster vm2-cluster; do
  if [[ "$context" == "$SELECTED_CLUSTER" ]]; then
    continue
  fi

  if kubectl --context "$context" \
    get namespace "$NAMESPACE" \
    >/dev/null 2>&1; then

    echo \
      "Runtime namespace exists on non-selected cluster: $context" \
      >&2

    exit 1
  fi

  echo "$context: no final runtime deployment"
done

echo
echo "===== Benchmark isolation ====="

for context in vm1-cluster vm2-cluster; do
  if kubectl --context "$context" \
    get namespace benchmark \
    >/dev/null 2>&1; then

    echo \
      "Unexpected benchmark namespace in $context" \
      >&2

    exit 1
  fi

  echo "$context: no benchmark namespace"
done

echo
echo "===== Final pipeline summary ====="

jq '
  {
    run_id,
    status,
    total_duration_ms,
    benchmarking_performed,
    decision,
    execution: {
      execution_id:
        .execution.execution_id,
      selected_cluster:
        .execution.selected_cluster,
      execution_successful:
        .execution.execution_successful,
      deployment_timing:
        .execution.deployment_timing,
      actual_execution_evaluation:
        .execution.actual_execution_evaluation,
      runtime:
        .execution.runtime,
      placement:
        .execution.placement
    }
  }
' "$PIPELINE_MANIFEST"

echo
echo "RUN_ID=$RUN_ID"
echo "PIPELINE_DIRECTORY=$PIPELINE_DIRECTORY"
echo "EXPERIMENT_DIRECTORY=$EXPERIMENT_DIRECTORY"

echo
echo "Phase 13 end-to-end verification passed."
