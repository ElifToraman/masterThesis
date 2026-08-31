# Intent Controller REST API

The API currently accepts only the `hello` IntentFunction. It runs the
existing benchmark, monitoring, decision, Knative deployment, final
invocation, and cleanup workflow asynchronously.

The submitted IntentFunction contains the function and user intent.
Controller operational parameters are deliberately separate:

```text
controller/config/runtime.yaml
```

That file controls benchmark load, final invocation validation,
post-deployment monitoring, and automatic control-loop guards. REST clients
cannot change those experiment parameters inside their intent submission.

Start it on the controller VM:

```bash
cd ~/masterThesis
python3 -m controller.api_service \
  --host 127.0.0.1 \
  --port 8088 \
  --runtime-config controller/config/runtime.yaml
```

From the Mac, create an SSH tunnel:

```bash
ssh -i ~/.ssh/chameleon_new \
  -L 8088:127.0.0.1:8088 \
  cc@129.114.27.169
```

Check health:

```bash
curl http://127.0.0.1:8088/healthz
```

Submit the `hello` YAML:

```bash
curl -i \
  -X POST \
  -H 'Content-Type: application/yaml' \
  --data-binary @controller/examples/hello-intent-function.yaml \
  http://127.0.0.1:8088/v1/orchestrations
```

The response is `202 Accepted` and includes a `run_id`. Check progress:

```bash
curl http://127.0.0.1:8088/v1/orchestrations/<run-id>
```

Possible states are `accepted`, `running`, `succeeded`, and `failed`.
Artifacts and the orchestrator log are stored under
`controller/results/runs/<run-id>/`.

After successful deployment, the status response also contains
`selected_cluster` and `function_url`. The API continuously invokes the
selected `hello` URL and collects VM, node, and pod metrics from every
configured candidate cluster. Read its sliding-window intent evaluation with:

```bash
curl \
  http://127.0.0.1:8088/v1/orchestrations/<run-id>/monitoring
```

Monitoring states are `warming-up`, `intent-satisfied`, `intent-violated`, and
`monitoring-failed`. Evidence is persisted under:

```text
controller/results/runs/<run-id>/post-deployment/samples.jsonl
controller/results/runs/<run-id>/post-deployment/latest-summary.json
controller/results/runs/<run-id>/post-deployment/raw-metrics/metrics_<n>.csv
```

The latest successful deployment monitor is resumed when the API restarts.
When the configured number of consecutive windows is `intent-violated`, the
API automatically submits the original intent as a correlated re-evaluation
run. The new run benchmarks and evaluates every cluster, deploys and validates
the selected placement, cleans non-selected clusters, and continues
monitoring. If a different cluster is selected, this performs a migration.

The parent monitoring response exposes `reevaluation_run_id`. The automatic
run's status response contains `control_loop_trigger`. Evidence is stored in:

```text
controller/results/runs/<automatic-run-id>/control-loop-trigger.json
controller/results/runs/<root-run-id>/control-loop-events.jsonl
controller/results/control-loop-events.jsonl
```

For a repeatable supervisor demonstration, first submit normally through the
REST API, wait for success, and then run this on the controller VM:

```bash
python3 -m controller.scripts.run_control_loop_experiment \
  --parent-run-id <successful-run-id> \
  --workers 8 \
  --function-load-concurrency 4 \
  --function-work 8 \
  --duration-seconds 240 \
  --require-migration
```

This records the controlled external workload and follows the automatic run;
it does not bypass the REST API or override the placement policy.

Only one orchestration may run at a time. A second submission receives
`409 Conflict`. This protects the shared cluster deployments and result
files.
