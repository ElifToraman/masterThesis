# Intent Controller REST API

The API currently accepts only the `hello` IntentFunction. It runs the
existing benchmark, monitoring, decision, Knative deployment, final
invocation, and cleanup workflow asynchronously.

The submitted IntentFunction contains the function and user intent.
Controller operational parameters are deliberately separate:

```text
controller/config/runtime.yaml
```

That file controls benchmark load, final invocation validation, and
post-deployment monitoring. REST clients cannot change those experiment
parameters inside their intent submission.

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
selected `hello` URL and collects selected-cluster VM, node, and function-pod
metrics. Read its sliding-window intent evaluation with:

```bash
curl \
  http://127.0.0.1:8088/v1/orchestrations/<run-id>/monitoring
```

Monitoring states are `warming-up`, `intent-satisfied`, `intent-violated`, and
`monitoring-failed`. Evidence is persisted under:

```text
controller/results/runs/<run-id>/post-deployment/samples.jsonl
controller/results/runs/<run-id>/post-deployment/latest-summary.json
```

The latest successful deployment monitor is resumed when the API restarts.
Monitoring reports violations but does not redeploy or migrate the function.

Only one orchestration may run at a time. A second submission receives
`409 Conflict`. This protects the shared cluster deployments and result
files.
