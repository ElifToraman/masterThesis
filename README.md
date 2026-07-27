# Intent-Based Orchestration of Serverless Applications at the Edge

Master's thesis research prototype for automatically placing a Knative
serverless function on one of multiple independent edge clusters according to
a user-defined intent.

The current evaluated application is the plain `hello` function. The older
`hello-instrumented` function and function-chain experiments are not part of
the current controller workflow.

## Current Status

The implemented system supports the following end-to-end flow:

```text
User submits hello function + intent through REST
  -> controller validates and stores the request
  -> controller benchmarks every candidate cluster
  -> controller collects VM, Kubernetes node, and pod metrics
  -> decision policy checks feasibility and intent satisfaction
  -> decision policy selects one edge cluster
  -> controller deploys hello as a Knative Service
  -> controller invokes and validates the final deployment
  -> controller removes hello from non-selected clusters
  -> controller continuously monitors the selected deployment
  -> user invokes the returned Knative URL
```

The controller selects a **cluster**. Kubernetes and Knative remain
responsible for placing pods on nodes inside that cluster. This is the intended
separation of responsibilities for the current thesis scope.

Continuous monitoring currently detects and reports an intent violation. It
does not yet automatically migrate the service to another cluster.

## Testbed Architecture

```text
MacBook client
  |
  | HTTP through an SSH tunnel
  | Mac 127.0.0.1:8088 -> Controller 127.0.0.1:8088
  v
Controller VM: 129.114.27.169
  |
  |-- REST API and asynchronous run manager
  |-- orchestrator
  |-- monitoring and benchmarking
  |-- decision policy
  |-- Knative deployer and execution validator
  |-- continuous post-deployment monitor
  |
  | kubectl context + SSH + Prometheus
  +-------------------------> vm1-cluster
  |                            Edge VM: 129.114.25.182
  |                            Kind + Kubernetes + Knative
  |                            Prometheus
  |                            local registry
  |
  +-------------------------> vm2-cluster
                               Edge VM: 129.114.25.80
                               Kind + Kubernetes + Knative
                               Prometheus
                               local registry
```

### Machine Responsibilities

| Machine | Responsibility |
|---|---|
| MacBook | Stores the development repository, submits the YAML through REST, checks status, and invokes the final URL |
| Controller VM | Runs the API, orchestrator, monitoring, benchmarking, policy, deployment, cleanup, validation, and post-deployment monitor |
| Edge VM 1 | Hosts the independent `vm1-cluster` Kind/Knative cluster and its local image registry |
| Edge VM 2 | Hosts the independent `vm2-cluster` Kind/Knative cluster and its local image registry |

Normal operation does not require the user to SSH into either edge VM. The
controller accesses them automatically.

## Technologies

| Technology | Role in the system |
|---|---|
| Chameleon Cloud | Provides the controller and edge virtual machines |
| Ubuntu 22.04 | VM operating system |
| Docker | Runs Kind nodes and local registries; builds the function image |
| Local Docker registries | Store a cluster-accessible copy of the `hello` image for each edge VM |
| Kind | Creates a multi-node Kubernetes cluster inside each edge VM |
| Kubernetes | Manages nodes, pods, services, resources, and scheduling inside each selected cluster |
| Knative Serving | Deploys the serverless function, provides the public service URL, revisions, scale-to-zero, and min/max scale annotations |
| Kourier/Envoy | Provides Knative ingress and routes requests to the function |
| Prometheus | Supplies Kubernetes node and pod metrics |
| SSH | Gives the controller VM access to physical VM metrics and protects access to the REST API |
| Python 3 | Implements the API, models, monitoring, benchmarking, policy, deployment, validation, and orchestration |
| PyYAML | Parses the IntentFunction and cluster configuration |
| `requests` / `urllib` | Executes benchmark, validation, API, and post-deployment HTTP requests |
| systemd | Keeps the controller API and optional Prometheus port forwards alive across logout/reboot |
| JSON, YAML, JSONL, CSV | Configuration, user input, status, decisions, benchmark evidence, and monitoring evidence |

No external web framework is required for the controller API. It uses Python's
threaded HTTP server because the API is deliberately small and research
oriented.

## Repository Layout

```text
.
├── README.md
├── controller/
│   ├── api_service.py
│   ├── orchestrator.py
│   ├── decision_policy.py
│   ├── deployer.py
│   ├── execution_validator.py
│   ├── post_deployment_monitor.py
│   ├── runtime_config.py
│   ├── image_resolver.py
│   ├── intent_function_parser.py
│   ├── benchmarking/
│   ├── monitoring/
│   ├── models/
│   ├── scripts/
│   ├── config/
│   │   ├── clusters.yaml
│   │   ├── policy.json
│   │   └── runtime.yaml
│   ├── examples/
│   │   └── hello-intent-function.yaml
│   ├── systemd/
│   ├── tests/
│   ├── API.md
│   └── README.md
└── hello/
    ├── function/
    ├── tests/
    ├── Makefile
    └── func.yaml
```

## User Input: IntentFunction

The REST client sends a single YAML or JSON document containing both the
function description and its intent.

The active example is:

```text
controller/examples/hello-intent-function.yaml
```

Its main structure is:

```yaml
apiVersion: intent.elif.dev/v1
kind: IntentFunction

metadata:
  name: hello-intent-function

spec:
  function:
    name: hello
    namespace: default
    serviceName: hello
    version: hello-latest
    runtime: knative
    image: elif/hello:latest

  intent:
    targetRef:
      kind: KnativeService
      name: default/hello

    objectives:
      - name: hello-p95-latency
        description: P95 warm latency must be <= 50 ms
        operator: "<="
        value: 50
        unit: ms
        measuredBy: benchmark/hello/p95_warm_latency_ms
```

The parser validates the document and converts it into typed Python models.
The REST endpoint currently intentionally accepts only the plain `hello`
function. Benchmark duration, validation retries, and monitoring intervals are
not user intent, so they are kept in the controller-owned runtime
configuration instead of this submission.

### Objective Fields

| Field | Meaning |
|---|---|
| `name` | Human-readable objective identifier |
| `measuredBy` | Metric that the policy evaluates |
| `operator` | One of `<`, `<=`, `==`, `>=`, or `>` |
| `value` | Target value |
| `unit` | Optional measurement unit |
| `weight` | Optional positive priority used in multi-objective scoring; default is `1.0` |

Constraints use the same representation under `intent.constraints`.
Unsupported objectives and constraints are not silently ignored: they make a
cluster infeasible and appear in its rejection reasons.

## Controller Configuration

Cluster-specific information is centralized in:

```text
controller/config/clusters.yaml
```

For each cluster it defines:

- logical cluster name
- Kubernetes context
- edge VM address and SSH credentials
- Prometheus endpoint
- local image registry address

Policy parameters are centralized in:

```text
controller/config/policy.json
```

This includes:

- minimum acceptable benchmark success rate
- maximum benchmark age
- default CPU and memory requirement
- CPU and memory safety factors
- cold-start and deployment normalization references
- scoring weights

The algorithm is therefore configurable without modifying
`decision_policy.py`. The weights are thesis experiment parameters and should
be justified with sensitivity analysis and evaluation results.

Operational evaluation settings are centralized in:

```text
controller/config/runtime.yaml
```

This controller-owned file defines:

- benchmark warm-up requests, concurrency, duration, timeouts, and resource
  sampling interval;
- final invocation attempts, timeout, and retry interval;
- post-deployment monitoring interval, window size, minimum samples, and
  request timeout.

This separation keeps the responsibilities clear:

```text
IntentFunction = what the user wants
runtime.yaml   = how the controller measures and verifies it
policy.json    = how the controller evaluates and ranks candidates
clusters.yaml  = which infrastructure the controller manages
```

The API validates all three controller configuration files at startup. Every
orchestration uses the configured runtime profile; a REST client cannot
silently change the experiment methodology inside its intent.

## REST API Lifecycle

The API runs continuously on the controller VM:

```text
127.0.0.1:8088
```

It is installed as:

```text
intent-controller-api.service
```

The service starts:

```bash
python3 -m controller.api_service \
  --host 127.0.0.1 \
  --port 8088
```

Binding to loopback avoids publishing an unauthenticated research API on the
internet. The Mac reaches it through an SSH tunnel.

### API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/healthz` | API health, active orchestration, and monitored run |
| `POST` | `/v1/orchestrations` | Submit an IntentFunction and start a run |
| `GET` | `/v1/orchestrations/<run-id>` | Read run status, selected cluster, and final URL |
| `GET` | `/v1/orchestrations/<run-id>/monitoring` | Read the latest post-deployment monitoring window |

Run states are:

```text
accepted -> running -> succeeded
                    \-> failed
```

`POST` returns `202 Accepted` immediately. The API validates and stores the
submission, creates a run ID, and starts a background worker. That worker
launches `controller.orchestrator` as a separate Python process and redirects
its output into the run log.

Only one orchestration may execute at once. A simultaneous second submission
receives `409 Conflict`, preventing competing deployments from modifying the
same clusters.

## End-to-End Controller Workflow

### 1. Submission and Validation

`controller/api_service.py`:

1. receives up to 1 MiB of YAML or JSON;
2. validates the IntentFunction;
3. confirms that the submission targets the supported `hello` function;
4. creates `controller/results/runs/<run-id>/`;
5. saves the exact request as `submission.yaml`;
6. writes `status.json` with state `accepted`;
7. starts an asynchronous orchestration worker.

### 2. Orchestration

`controller/orchestrator.py` runs these commands sequentially:

```text
controller.scripts.run_benchmark
controller.scripts.run_decision_policy
controller.scripts.deploy_selected
controller.scripts.cleanup_non_selected
```

All stages receive the same submission, cluster configuration, runtime
configuration, policy configuration where applicable, and run ID. If a stage
exits unsuccessfully, later stages do not run and the API marks the run
`failed`.

### 3. Benchmarking

For each configured cluster, the benchmark service:

1. rewrites the logical image name for that cluster's registry;
2. deploys a temporary Knative Service called `hello-benchmark`;
3. waits for it to become Ready;
4. records deployment duration;
5. records the first invocation latency;
6. sends warm-up requests;
7. generates a concurrent, duration-based workload;
8. measures successes, failures, latency distribution, and throughput;
9. samples benchmark pod CPU and memory;
10. deletes the temporary service in a `finally` cleanup path.

The two clusters are isolated from each other. A benchmark failure on one
cluster is saved as evidence and does not abort benchmarking of the healthy
cluster.

Recorded measurements include:

- deployment duration
- first-invocation/cold-start latency
- average, p50, and p95 warm latency
- successful and failed request counts
- success rate
- benchmark duration and concurrency
- throughput
- average and peak CPU
- average and peak memory

Results are appended to:

```text
controller/results/benchmarks.jsonl
```

Each record carries the orchestration run ID, function version, cluster, and
resolved image reference.

### 4. Monitoring

The controller collects a coherent `MetricsSnapshot` for every candidate.

#### Physical VM metrics

Collected over SSH from `/proc` and `/proc/meminfo`:

- reachability
- SSH response time
- physical CPU core count and CPU usage
- total, used, and available physical memory
- memory usage percentage
- one-, five-, and fifteen-minute load average

Physical VM capacity is used for feasibility. This prevents double-counting
Kind worker containers that share the same underlying VM. For example, two
Kind workers inside one 4-core/8-GB VM do not become 8 cores/16 GB in the
policy.

#### Kubernetes node metrics

Collected through Prometheus:

- node identity and role
- CPU usage
- memory usage
- load average
- query latency

These metrics describe cluster load, but they are not summed as independent
physical capacity.

#### Pod metrics

Collected through Prometheus:

- pod, namespace, and node
- CPU usage
- memory working set and RSS
- network receive/transmit rate
- container count

These metrics describe current workloads and the resource behaviour of
`hello`.

### 5. Decision Policy

`controller/decision_policy.py` combines:

- the submitted intent;
- the current monitoring snapshot;
- fresh benchmark records for the same run and expected image;
- resource requirements and safety factors;
- configurable normalized scoring weights.

#### Benchmark validity

A record is accepted only when it matches:

- the submitted function name;
- the function version;
- the current run ID;
- the cluster's resolved image reference;
- the configured maximum age.

This prevents a stale or unrelated benchmark from influencing a new
placement.

#### Feasibility

A cluster can be rejected for:

- unreachable VM or missing VM metrics;
- missing node metrics;
- missing or failed benchmark;
- benchmark success rate below the configured minimum;
- insufficient physical CPU or memory after safety factors;
- unsupported objective or constraint;
- violated hard constraint.

#### Supported intent measurements

The policy can evaluate:

- p95, p50, and average warm latency;
- first-invocation/cold-start latency;
- deployment duration;
- success rate;
- throughput;
- physical VM CPU or memory usage;
- available physical CPU or memory.

Multiple objectives are evaluated together. An objective's `weight` changes
its contribution to the objective component of the score. Constraints are
hard feasibility requirements.

#### Normalized score

Lower score is better. Each component is converted to a dimensionless value
between `0` and `1` before weighting:

```text
score =
  objectives_weight  * normalized_objective_penalty
  + load_weight      * normalized_load
  + cold_start_weight * normalized_cold_start
  + deployment_weight * normalized_deployment_time
  + headroom_weight  * normalized_headroom_penalty
```

The configured weights are normalized by their sum.

Selection order:

1. Select the lowest-score feasible cluster that satisfies all objectives.
2. If no feasible cluster satisfies every objective, select the lowest-score
   feasible cluster in `best-effort` mode.
3. If no cluster is feasible, fail without deploying.

The complete decision, candidate measurements, scores, and rejection reasons
are saved per run.

### 6. Knative Deployment

The deployer:

1. reads the selected cluster from the run-specific decision;
2. resolves the image for that cluster's local registry;
3. builds a Knative Service manifest;
4. applies it using the selected Kubernetes context;
5. includes Knative `min-scale` and `max-scale` annotations;
6. waits for the service's Ready condition;
7. reads the Knative URL.

Kubernetes/Knative decides which worker node hosts the pod. The controller
does not override the internal scheduler.

### 7. Final Invocation Verification

Deployment success means more than a Ready condition. The execution validator
invokes the returned URL with configurable retries and records:

- success/failure
- number of attempts
- HTTP status
- latency
- response body
- error, if any

If final invocation fails, the orchestration is marked failed.

### 8. Cleanup

After successful deployment and validation, the controller deletes the same
Knative Service from every non-selected cluster. The selected cluster keeps
the only active deployment.

### 9. Continuous Post-Deployment Monitoring

After a successful orchestration, the API starts a background monitor for the
selected deployment. At each interval it:

1. invokes the live Knative URL;
2. records status and response latency;
3. collects selected-cluster VM, node, and pod metrics;
4. adds the observation to a bounded sliding window;
5. calculates success rate, average, p50, and p95 latency;
6. re-evaluates supported objectives and constraints;
7. persists the sample and latest summary.

Monitoring states:

| State | Meaning |
|---|---|
| `waiting-for-deployment` | Orchestration has not completed |
| `warming-up` | Fewer than the configured minimum samples exist |
| `intent-satisfied` | All current live objective and constraint evaluations pass |
| `intent-violated` | At least one live evaluation fails |
| `monitoring-failed` | The monitoring loop could not start |

When the API restarts, it finds the latest successful execution and resumes
its monitor. Starting a newer successful run stops the previous monitor and
monitors the new deployment.

## Local Registries and Image Resolution

The user submits a logical image:

```text
elif/hello:latest
```

The controller resolves it per candidate:

```text
vm1-cluster -> host.docker.internal:5000/elif/hello:latest
vm2-cluster -> host.docker.internal:5001/elif/hello:latest
```

The same image must already be present in both registries before a complete
two-cluster benchmark. Image build and push utilities live under `hello/`.
The controller currently orchestrates existing images; it does not build
source code received in the REST request.

## Running a Complete Test from the Mac

The API and orchestration run on the controller VM. The following client
commands run on the Mac.

### Mac Terminal 1: create the API tunnel

```bash
ssh -N \
  -L 8088:127.0.0.1:8088 \
  -i ~/.ssh/chameleon_new \
  cc@129.114.27.169
```

No output is expected. Keep this terminal open.

### Mac Terminal 2: health check

```bash
curl -s http://127.0.0.1:8088/healthz \
  | python3 -m json.tool
```

### Mac Terminal 2: submit hello

```bash
cd /Users/eliftoraman/masterThesis

curl -s -X POST \
  -H 'Content-Type: application/yaml' \
  --data-binary @controller/examples/hello-intent-function.yaml \
  http://127.0.0.1:8088/v1/orchestrations \
  | python3 -m json.tool
```

Copy the returned run ID:

```bash
RUN_ID="paste-run-id-here"
```

### Mac Terminal 2: check progress

```bash
curl -s \
  "http://127.0.0.1:8088/v1/orchestrations/$RUN_ID" \
  | python3 -m json.tool
```

Repeat until `state` is `succeeded` or `failed`.

### Mac Terminal 2: invoke the selected deployment

With `jq`:

```bash
FUNCTION_URL=$(
  curl -s \
    "http://127.0.0.1:8088/v1/orchestrations/$RUN_ID" \
    | jq -r '.function_url'
)

echo "$FUNCTION_URL"
curl -i "$FUNCTION_URL"
```

The expected response has HTTP 200 and contains:

```json
{
  "message": "Hello, world"
}
```

### Mac Terminal 2: inspect continuous monitoring

```bash
curl -s \
  "http://127.0.0.1:8088/v1/orchestrations/$RUN_ID/monitoring" \
  | python3 -m json.tool
```

The first state may be `warming-up`. Check again after the configured minimum
number of samples.

Stop only the Mac tunnel with `Ctrl+C` in Terminal 1. This does not stop the
controller API, monitoring service, or deployed function.

## Operating the Controller VM

The normal client does not need these commands. They are useful for
administration and debugging.

```bash
ssh -i ~/.ssh/chameleon_new cc@129.114.27.169
```

Check the API:

```bash
systemctl status intent-controller-api.service --no-pager
curl -s http://127.0.0.1:8088/healthz
```

Restart it:

```bash
sudo systemctl restart intent-controller-api.service
```

Read logs:

```bash
journalctl -u intent-controller-api.service -n 100 --no-pager
```

Check service placement:

```bash
kubectl --context vm1-cluster get ksvc hello -n default
kubectl --context vm2-cluster get ksvc hello -n default
```

Only the selected cluster should contain the Ready `hello` service.

## Evidence and Result Files

Global append-only benchmark evidence:

```text
controller/results/benchmarks.jsonl
```

Latest convenience outputs:

```text
controller/results/decisions/latest-decision.json
controller/results/executions/latest-execution.json
```

Run-specific evidence:

```text
controller/results/runs/<run-id>/
├── submission.yaml
├── status.json
├── orchestrator.log
├── decision.json
├── execution.json
└── post-deployment/
    ├── samples.jsonl
    ├── latest-summary.json
    └── raw-metrics/
```

The run ID connects submission, benchmark, decision, deployment, execution,
and monitoring evidence.

## Running Tests

From the repository root:

```bash
python3 -m unittest discover \
  -s controller/tests \
  -p 'test_*.py' \
  -v
```

The unit tests cover:

- REST submission and status behaviour
- rejection of simultaneous runs
- benchmark calculations and failure handling
- decision feasibility, freshness, weights, constraints, and scoring
- execution validation
- post-deployment sliding-window monitoring

Unit tests use mocks and temporary directories where appropriate. They do not
replace the live two-cluster experiment.

## Implemented Design Improvements

The current controller addresses the main earlier prototype problems:

| Earlier problem | Current solution |
|---|---|
| Kind worker capacity was double-counted | Physical VM cores and memory are used for feasibility |
| Old benchmark could be reused | Age, run ID, function version, and image reference are validated |
| Objective weight was unused | Weighted objective penalty is part of the normalized score |
| Unsupported objectives were ignored | Unsupported objectives/constraints reject the candidate |
| Score mixed incompatible units | All score components are normalized to `[0, 1]` |
| Algorithm constants were hardcoded | Parameters and weights are in `policy.json` |
| Only sequential benchmark requests | Configurable concurrent, duration-based workload |
| Failure on one cluster aborted everything | Per-cluster failures are saved and healthy candidates continue |
| Final URL was not verified | Final HTTP invocation evidence is required |
| No live submission | Asynchronous REST API accepts YAML/JSON |
| Port/API processes depended on a shell | systemd units provide restart and boot persistence |
| No post-deployment view | Continuous sliding-window monitoring and REST reporting |

## Current Limitations and Future Work

The following are deliberately not yet implemented:

- automatic migration or redeployment after `intent-violated`;
- a full monitor-analyse-plan-execute remediation loop;
- a controller-defined autoscaling algorithm beyond Knative annotations;
- service-chain placement;
- split placement across clusters;
- network-aware routing or flow scheduling;
- building arbitrary uploaded source code;
- multiple simultaneous orchestration jobs;
- authentication, authorization, TLS, and a public multi-user API;
- a database or distributed run queue;
- learned or experimentally optimized policy weights.

The next major control feature is:

```text
live monitor detects violation
  -> collect fresh evidence
  -> benchmark feasible alternative clusters
  -> run placement policy again
  -> migrate/redeploy if the expected improvement exceeds a threshold
  -> validate new deployment
  -> remove old deployment
  -> continue monitoring
```

Migration should include hysteresis, cooldown, and minimum-improvement
thresholds to avoid oscillation between clusters.

## Related Documentation

- Controller-specific index: `controller/README.md`
- REST endpoint reference: `controller/API.md`
- Active example submission:
  `controller/examples/hello-intent-function.yaml`
- Thesis system explanation:
  `docs/Intent_Based_Orchestration_System_Explanation.docx`
