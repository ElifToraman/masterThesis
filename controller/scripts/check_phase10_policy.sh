#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"
MONITORING="/tmp/phase10-monitoring.json"
DECISION="/tmp/phase10-decision.json"
PROFILE="$REPOSITORY_ROOT/controller/results/benchmarks/latest-profile.json"
INTENT="$REPOSITORY_ROOT/controller/config/intent.json"

cd "$REPOSITORY_ROOT"

echo "===== Input validation ====="

python3 controller/validate_inputs.py

echo
echo "===== Fresh monitoring snapshot ====="

python3 controller/monitoring.py \
  --output "$MONITORING"

echo
echo "===== Placement policy ====="

python3 controller/policy.py \
  --monitoring-snapshot "$MONITORING" \
  --benchmark-profile "$PROFILE" \
  --output "$DECISION"

echo
echo "===== Verifying policy separation ====="

jq -e '
  .deployment_performed == false
  and .benchmarking_performed == false
  and .benchmark.placement_decision == null
  and .selected_cluster != null
' "$DECISION" >/dev/null

echo "Policy made no deployment."

echo
echo "===== Verifying feasibility ====="

jq -e '
  .candidates["vm1-cluster"].feasible == true
  and .candidates["vm2-cluster"].feasible == true
  and (
    .candidates["vm1-cluster"]
    .image.available == true
  )
  and (
    .candidates["vm2-cluster"]
    .image.available == true
  )
' "$DECISION" >/dev/null

echo "Both candidates passed feasibility checks."

echo
echo "===== Independently calculating expected selection ====="

OBJECTIVE_OPERATOR=$(
  jq -r '.objective.operator' "$INTENT"
)

OBJECTIVE_TARGET=$(
  jq -r '.objective.value' "$INTENT"
)

OBJECTIVE_METRIC=$(
  jq -r '.objective.metric' "$INTENT"
)

case "$OBJECTIVE_METRIC" in
  response_time_mean_ms)
    PROFILE_FIELD="mean_latency_ms"
    ;;
  response_time_p50_ms)
    PROFILE_FIELD="median_latency_ms"
    ;;
  response_time_p95_ms)
    PROFILE_FIELD="p95_latency_ms"
    ;;
  response_time_p99_ms)
    PROFILE_FIELD="p99_latency_ms"
    ;;
  *)
    echo \
      "Unsupported verification metric: $OBJECTIVE_METRIC" \
      >&2
    exit 1
    ;;
esac

EXPECTED_CLUSTER=$(
  jq -r \
    --arg field "$PROFILE_FIELD" \
    '
      .clusters
      | to_entries
      | map(
          select(
            .value.status == "success"
            and .value[$field] != null
          )
        )
      | sort_by(
          .value[$field],
          .value.average_cpu_millicores,
          .value.average_memory_mb
        )
      | .[0].key
    ' "$PROFILE"
)

SELECTED_CLUSTER=$(
  jq -r '.selected_cluster' "$DECISION"
)

echo "Expected cluster: $EXPECTED_CLUSTER"
echo "Selected cluster: $SELECTED_CLUSTER"

test "$SELECTED_CLUSTER" = "$EXPECTED_CLUSTER"

SATISFYING_COUNT=$(
  jq \
    --arg field "$PROFILE_FIELD" \
    --arg operator "$OBJECTIVE_OPERATOR" \
    --argjson target "$OBJECTIVE_TARGET" \
    '
      [
        .clusters
        | to_entries[]
        | select(.value.status == "success")
        | .value[$field]
        | select(
            if $operator == "<=" then . <= $target
            elif $operator == "<" then . < $target
            elif $operator == ">=" then . >= $target
            elif $operator == ">" then . > $target
            elif $operator == "==" then . == $target
            else false
            end
          )
      ]
      | length
    ' "$PROFILE"
)

if [[ "$SATISFYING_COUNT" -gt 0 ]]; then
  jq -e '
    .decision_mode == "intent-satisfied"
    and .objective_satisfied == true
  ' "$DECISION" >/dev/null
else
  jq -e '
    .decision_mode == "best-effort"
    and .objective_satisfied == false
  ' "$DECISION" >/dev/null
fi

echo
echo "===== Decision summary ====="

jq '
  {
    decision_mode,
    selected_cluster,
    objective_satisfied,
    reason,
    candidates: (
      .candidates
      | with_entries(
          .value = {
            feasible: .value.feasible,
            rank: .value.rank,
            measured_value:
              .value.objective.measured_value,
            target_value:
              .value.objective.target_value,
            satisfied:
              .value.objective.satisfied,
            violation:
              .value.objective.violation
          }
        )
    )
  }
' "$DECISION"

echo
echo "===== Confirming no benchmark deployments ====="

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
echo "Phase 10 placement-policy verification passed."
