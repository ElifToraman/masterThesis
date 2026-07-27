# Intent Controller

This directory contains the active Python controller for the master's thesis
prototype **Intent-Based Orchestration of Serverless Applications at the
Edge**.

The current controller supports the plain `hello` Knative function. It does
not use `hello-instrumented` or a function chain.

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
  -> deploy and validate hello
  -> clean the non-selected cluster
  -> continuously monitor the selected deployment
```

## Main Files

| File | Responsibility |
|---|---|
| `api_service.py` | REST endpoints, asynchronous runs, status persistence, monitor lifecycle and recovery |
| `orchestrator.py` | Runs benchmark, decision, deployment, and cleanup stages |
| `decision_policy.py` | Feasibility, intent evaluation, normalized weighted scoring, and placement |
| `deployer.py` | Builds and applies the Knative Service and waits for readiness |
| `execution_validator.py` | Invokes the final URL and writes execution evidence |
| `post_deployment_monitor.py` | Sliding-window live probes, resource snapshots, and intent evaluation |
| `runtime_config.py` | Loads submission, cluster, policy, and operational runtime configuration |
| `intent_function_parser.py` | Parses and validates YAML/JSON IntentFunction documents |
| `image_resolver.py` | Maps the logical image to each local edge registry |
| `benchmarking/` | Temporary Knative deployment, concurrent load generation, resource sampling, and JSONL persistence |
| `monitoring/` | SSH physical-VM metrics and Prometheus node/pod metrics |
| `scripts/` | Executable orchestration stages |
| `config/clusters.yaml` | Cluster contexts, hosts, Prometheus endpoints, and registries |
| `config/policy.json` | Feasibility constants, normalization references, and score weights |
| `config/runtime.yaml` | Controller-owned benchmark, validation, and continuous-monitor settings |
| `examples/hello-intent-function.yaml` | Active user submission example |
| `systemd/` | Persistent API and Prometheus port-forward service templates |
| `tests/` | Controller unit tests |

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
  --data-binary @controller/examples/hello-intent-function.yaml \
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

## Evidence

```text
results/benchmarks.jsonl
results/runs/<run-id>/submission.yaml
results/runs/<run-id>/status.json
results/runs/<run-id>/orchestrator.log
results/runs/<run-id>/decision.json
results/runs/<run-id>/execution.json
results/runs/<run-id>/post-deployment/samples.jsonl
results/runs/<run-id>/post-deployment/latest-summary.json
```

## Tests

From the repository root:

```bash
python3 -m unittest discover \
  -s controller/tests \
  -p 'test_*.py' \
  -v
```

See [API.md](API.md) for the compact endpoint reference.
