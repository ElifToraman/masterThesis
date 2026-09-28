# Intent Controller

This directory contains the active Python controller for the master's thesis
prototype **Intent-Based Orchestration of Serverless Applications at the
Edge**.

The controller supports three independent benchmark functions—Dynamic HTML,
Graph PageRank, and gzip compression—plus `hello` as a smoke test. It does not
use a function chain.

For the complete architecture, technologies, algorithms, operating procedure,
result layout, and limitations, read the repository
[README](../README.md).

## Active Workflow

```text
REST IntentFunction submission
  -> validate and persist
  -> benchmark vm1-cluster and vm2-cluster
  -> collect physical VM, node, and pod metrics
  -> evaluate feasibility and intent
  -> select the lowest-score suitable cluster
  -> deploy and validate the submitted function
  -> clean the non-selected cluster
  -> continuously monitor the deployment and both clusters
  -> automatically re-evaluate persistent intent violations
  -> retain or migrate the placement and continue monitoring
```

## Main Files

| File | Responsibility |
|---|---|
| `api_service.py` | REST endpoints, asynchronous runs, monitor lifecycle, violation-triggered runs, migration evidence and recovery |
| `orchestrator.py` | Runs benchmark, placement monitoring, decision, deployment, and cleanup stages |
| `decision_policy.py` | Feasibility, intent evaluation, normalized weighted scoring, and placement |
| `deployer.py` | Builds and applies the Knative Service and waits for readiness |
| `execution_validator.py` | Invokes the final URL and writes execution evidence |
| `post_deployment_monitor.py` | Sliding-window probes, all-cluster resource snapshots, intent evaluation, and guarded remediation trigger |
| `runtime_config.py` | Loads submission, cluster, policy, and operational runtime configuration |
| `function_profiles.py` | Loads the allowlist, fixed HTTP inputs, response contracts, and per-function benchmark overrides |
| `intent_function_parser.py` | Parses and validates YAML/JSON IntentFunction documents |
| `image_resolver.py` | Maps the logical image to each local edge registry |
| `benchmarking/` | Temporary Knative deployment, concurrent load generation, resource sampling, and JSONL persistence |
| `monitoring/` | SSH physical-VM metrics, Prometheus node/pod metrics, and typed snapshot persistence |
| `scripts/collect_placement_metrics.py` | Collects one run-specific snapshot before the decision stage |
| `scripts/` | Executable orchestration stages and the reproducible closed-loop experiment runner |
| `config/clusters.yaml` | Cluster contexts, hosts, Prometheus endpoints, and registries |
| `config/policy.json` | Feasibility constants, normalization references, and score weights |
| `config/runtime.yaml` | Controller-owned benchmark, validation, continuous-monitor, and closed-loop guard settings |
| `config/function-profiles.yaml` | Reproducible invocation contract for each supported function |
| `config/knative-autoscaling-policy.json` | Common platform-level replica bounds for reproducible experiments |
| `examples/*-intent-function.yaml` | Independent hello and benchmark-function submissions |
| `systemd/` | Persistent API and Prometheus port-forward service templates |

Preview or apply the common Knative autoscaling policy on every configured
cluster from the controller VM:

```bash
python3 -m controller.scripts.configure_knative_autoscaling
python3 -m controller.scripts.configure_knative_autoscaling --apply
```

## REST Quick Start

The REST API runs as `intent-controller-api.service` on the controller VM at
`127.0.0.1:8088`.

On the Mac, open an SSH tunnel and keep it running:

```bash
ssh -N \
  -L 8088:127.0.0.1:8088 \
  -i ~/.ssh/chameleon_new \
  cc@129.114.27.169
```

In a second Mac terminal:

```bash
cd /Users/eliftoraman/masterThesis

curl -s http://127.0.0.1:8088/healthz \
  | python3 -m json.tool

curl -s -X POST \
  -H 'Content-Type: application/yaml' \
  --data-binary @controller/examples/graph-pagerank-intent-function.yaml \
  http://127.0.0.1:8088/v1/orchestrations \
  | python3 -m json.tool
```

Use the returned run ID:

```bash
RUN_ID="paste-run-id-here"

curl -s \
  "http://127.0.0.1:8088/v1/orchestrations/$RUN_ID" \
  | python3 -m json.tool

curl -s \
  "http://127.0.0.1:8088/v1/orchestrations/$RUN_ID/monitoring" \
  | python3 -m json.tool
```

After success, invoke the returned `function_url` directly from the Mac.

## Independent Function Pilot and Evaluation

Run five randomized repetitions of all three functions through the same REST
workflow:

```bash
python3 -m controller.scripts.run_independent_experiments \
  --phase pilot \
  --repetitions 5 \
  --random-seed 42 \
  --output controller/results/experiment-series/pilot.json
```

Manual submissions stop the prior monitor, and their orchestrator removes all
supported function deployments from every candidate before benchmarking. The
cleanup evidence is stored under the run. Automatic closed-loop runs skip
that isolation cleanup so an existing placement remains available during
re-evaluation.

Calibrate per-function thresholds from only the pilot manifest:

```bash
python3 -m controller.scripts.calibrate_latency_slos \
  --experiment-manifest controller/results/experiment-series/pilot.json \
  --minimum-samples-per-cluster 5 \
  --output controller/results/calibration/latency-slos.json
```

## Reproducible Closed-Loop Experiment

Keep the initial REST submission as a client action: submit from the Mac and
wait until its state is `succeeded`. Then SSH to the controller VM and attach
the controlled experiment to that parent run:

```bash
cd ~/masterThesis

PARENT_RUN_ID="paste-successful-run-id-here"

python3 -m controller.scripts.run_control_loop_experiment \
  --parent-run-id "$PARENT_RUN_ID" \
  --workers 8 \
  --function-load-concurrency 4 \
  --function-work 8 \
  --duration-seconds 240 \
  --require-migration
```

The runner reads the parent placement, applies bounded CPU contention to that
physical edge VM over SSH, and sends bounded concurrent CPU-work requests to
the selected `hello` URL. It watches the live monitoring endpoint, follows
the automatically created reevaluation run, stops both workloads, and waits
for the new monitor. It does not choose or force the destination cluster; the
normal decision policy still decides whether placement is migrated or
retained.

Each invocation records both exact workload commands, targets, UTC
timestamps, durations, concurrency and worker settings, monitoring
observations, reevaluation run ID, final status, placement outcome, and
recovery observation under:

```text
results/runs/<parent-run-id>/experiments/<experiment-id>/experiment.json
results/runs/<parent-run-id>/experiments/<experiment-id>/observations.jsonl
```

`--require-migration` makes the command return a non-zero status when the
closed loop works but the policy legitimately retains the same cluster. Omit
that option when the experiment is intended to demonstrate reevaluation
rather than require a changed placement.

## Evidence

```text
results/benchmarks.jsonl
results/runs/<run-id>/submission.yaml
results/runs/<run-id>/normalized-intent.json
results/runs/<run-id>/status.json
results/runs/<run-id>/orchestrator.log
results/runs/<run-id>/pre-run-cleanup.json
results/runs/<run-id>/placement-monitoring/snapshot.json
results/runs/<run-id>/placement-monitoring/raw-metrics/metrics_<index>.csv
results/runs/<run-id>/decision.json
results/runs/<run-id>/execution.json
results/runs/<automatic-run-id>/control-loop-trigger.json
results/runs/<root-run-id>/control-loop-events.jsonl
results/runs/<run-id>/post-deployment/samples.jsonl
results/runs/<run-id>/post-deployment/latest-summary.json
results/runs/<run-id>/post-deployment/raw-metrics/metrics_<n>.csv
results/runs/<parent-run-id>/experiments/<experiment-id>/experiment.json
results/runs/<parent-run-id>/experiments/<experiment-id>/observations.jsonl
```

Persistent violations create a new run ID. Follow the value in
`reevaluation_run_id` or inspect `control-loop-events.jsonl` to trace all
generations from the initial placement.

See [API.md](API.md) for the compact endpoint reference.
