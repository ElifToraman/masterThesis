#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$HOME/masterThesis"

DECISION="$REPOSITORY_ROOT/controller/results/decisions/latest-decision.json"
EXECUTION="/tmp/phase11-execution.json"

cd "$REPOSITORY_ROOT"

echo "===== Input validation ====="

python3 controller/validate_inputs.py

echo
echo "===== Existing Phase 10 decision ====="

test -s "$DECISION"

jq -e '
  .phase == "placement-policy"
  and .deployment_performed == false
  and .selected_cluster != null
' "$DECISION" >/dev/null

SELECTED_CLUSTER=$(
  jq -r '.selected_cluster' "$DECISION"
)

DECISION_ID=$(
  jq -r '.decision_id' "$DECISION"
)

echo "Decision ID: $DECISION_ID"
echo "Selected cluster: $SELECTED_CLUSTER"

echo
echo "===== Deployment and execution ====="

python3 controller/deploy.py \
  --decision "$DECISION" \
  --output "$EXECUTION"

echo
echo "===== Execution artifact validation ====="

jq -e \
  --arg selected "$SELECTED_CLUSTER" \
  --arg decision_id "$DECISION_ID" \
  '
    .schema_version == 1
    and .phase == "deployment-and-execution"
    and .decision_id == $decision_id
    and .selected_cluster == $selected

    and .deployment_performed == true
    and .benchmarking_performed == false
    and .policy_evaluation_performed == false

    and .execution_successful == true
    and .runtime.service_ready == true

    and .successful_invocations
      == .invocation_count

    and .failed_invocations == 0

    and (
      [
        .invocations[]
        | select(.success != true)
      ]
      | length
    ) == 0

    and (
      .placement
      .kubernetes_observed_pods
      | length
    ) >= 1

    and (
      .placement
      .kubernetes_observed_nodes
      | length
    ) >= 1

    and (
      [
        .placement
        .kubernetes_observed_nodes[]
        | select(
            .eligible_for_serverless
            != true
          )
      ]
      | length
    ) == 0

    and (
      .actual_execution_evaluation
      .measured_value
      != null
    )

    and (
      .actual_execution_evaluation
      .satisfied
      | type
    ) == "boolean"

    and (
      .selection_evaluation.source
      == "phase10-decision"
    )

    and (
      .actual_execution_evaluation.source
      == "phase11-invocations"
    )

    and .deployment_retained == true
  ' "$EXECUTION" >/dev/null

echo "Execution artifact is valid."

NAMESPACE=$(
  jq -r '.runtime.namespace' "$EXECUTION"
)

SERVICE_NAME=$(
  jq -r '.runtime.service_name' "$EXECUTION"
)

echo
echo "===== Selected cluster deployment ====="

kubectl --context "$SELECTED_CLUSTER" \
  get kservice "$SERVICE_NAME" \
  -n "$NAMESPACE"

kubectl --context "$SELECTED_CLUSTER" \
  get pods \
  -n "$NAMESPACE" \
  -o wide

echo
echo "===== Returned pod and node verification ====="

while IFS=$'\t' read -r pod node; do
  actual_node=$(
    kubectl --context "$SELECTED_CLUSTER" \
      get pod "$pod" \
      -n "$NAMESPACE" \
      -o jsonpath='{.spec.nodeName}'
  )

  test "$actual_node" = "$node"

  workload_label=$(
    kubectl --context "$SELECTED_CLUSTER" \
      get node "$node" \
      -o jsonpath='{.metadata.labels.workload}'
  )

  test "$workload_label" = "serverless"

  echo "$pod -> $node -> workload=serverless"
done < <(
  jq -r '
    .placement.kubernetes_observed_pods[]
    | [.name, .node]
    | @tsv
  ' "$EXECUTION"
)

echo
echo "===== Non-selected cluster verification ====="

for context in vm1-cluster vm2-cluster; do
  if [[ "$context" == "$SELECTED_CLUSTER" ]]; then
    continue
  fi

  if kubectl --context "$context" \
    get namespace "$NAMESPACE" \
    >/dev/null 2>&1; then

    echo \
      "Runtime namespace unexpectedly exists in $context" \
      >&2

    exit 1
  fi

  echo "$context: no final runtime deployment"
done

echo
echo "===== Invocation results ====="

jq '
  .invocations[]
  | {
      request_number,
      success,
      http_status,
      response_time_ms,
      function_duration_ms,
      returned_cluster,
      returned_pod,
      returned_node,
      errors
    }
' "$EXECUTION"

echo
echo "===== Intent comparison ====="

jq '
  {
    phase10_selection_evaluation:
      .selection_evaluation,
    phase11_actual_execution_evaluation:
      .actual_execution_evaluation
  }
' "$EXECUTION"

echo
echo "===== Benchmark namespace isolation ====="

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
echo "SELECTED_CLUSTER=$SELECTED_CLUSTER"
echo "NAMESPACE=$NAMESPACE"
echo "SERVICE_NAME=$SERVICE_NAME"
echo "EXECUTION=$EXECUTION"

echo
echo "Phase 11 deployment-and-execution verification passed."
