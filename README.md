# Intent-Based Serverless Edge Testbed

> Master's Thesis: **Intent-Based Orchestration of Serverless Applications on the Edge Environment**  
> University of Klagenfurt — Supervisor: Dr. Reza Farahani  
> Baseline paper: Filinis et al. (2024) — *Intent-driven orchestration of serverless applications in the computing continuum.* Future Generation Computer Systems, 154, 72–86.

This repository contains a research prototype for intent-based orchestration of serverless function chains on edge environments.

The prototype uses two Chameleon Cloud virtual machines as independent edge clusters. Each VM runs a Kind-based Kubernetes cluster with Knative Serving, Kourier, a local Docker registry, and Prometheus/Grafana monitoring. VM-1 additionally hosts the intent controller. The controller reads a high-level latency intent, collects monitoring and probing data from both edge clusters, selects a suitable VM, deploys the complete function chain to that VM, and returns the selected public entry URL.

The current implementation focuses on **whole-chain placement** of a serverless function chain across two edge clusters.

---

## Current Prototype Summary

The evaluated application is a three-function serverless chain:

```text
f1 -> f2 -> f3
````

The client invokes only `f1`. Function `f1` calls `f2`, `f2` calls `f3`, and the response returns back through the chain.

The current controller supports whole-chain placement. This means all functions are deployed to the same selected VM:

```text
[f1, f2, f3] = [vm1, vm1, vm1]
```

or:

```text
[f1, f2, f3] = [vm2, vm2, vm2]
```

Automatic split placement, for example `[vm1, vm2, vm1]`, is not part of the current automatic controller and is treated as future work.

---

## Architecture

```text
Local Developer MacBook
├── ~/thesis/function-chain/
│   ├── f1/
│   ├── f2/
│   ├── f3/
│   ├── chain_intent.json
│   ├── application_descriptor.json
│   ├── controller_config.json
│   ├── chain_controller.env
│   ├── Makefile
│   └── scripts/run_chain_workflow.sh
│
├── Docker Desktop
│   └── builds linux/amd64 function images
│
├── kubectl / kn / func
│   └── build, deploy, inspect, and invoke services
│
└── SSH tunnels
    ├── 127.0.0.1:5000 -> VM-1 registry
    ├── 127.0.0.1:5001 -> VM-2 registry
    ├── 127.0.0.1:6443 -> VM-1 Kubernetes API
    └── 127.0.0.1:6444 -> VM-2 Kubernetes API


VM-1: 129.114.25.182 / 10.56.1.249
┌────────────────────────────────────────────┐
│ Ubuntu 22.04.5 LTS Chameleon KVM VM        │
│ Docker daemon                              │
│ registry:2 on 127.0.0.1:5000               │
│ kind: vm1-cluster                          │
│ Kubernetes v1.35.0                         │
│ Knative Serving v1.21.2 + Kourier          │
│ Prometheus + Grafana                       │
│                                            │
│ Intent controller                          │
│ ~/chain-controller/scripts/                │
│   chain_controller.sh                      │
│   collect_chain_metrics.sh                 │
│   decide_chain_placement.sh                │
│   deploy_chain_selected.sh                 │
└────────────────────────────────────────────┘


VM-2: 129.114.25.80 / 10.56.2.149
┌────────────────────────────────────────────┐
│ Ubuntu 22.04.5 LTS Chameleon KVM VM        │
│ Docker daemon                              │
│ registry:2 on 127.0.0.1:5000               │
│ kind: vm2-cluster                          │
│ Kubernetes v1.35.0                         │
│ Knative Serving v1.21.2 + Kourier          │
│ Prometheus + Grafana                       │
└────────────────────────────────────────────┘
```

---

## Testbed Configuration

| Node | Role                             |        Public IP |  Kind API endpoint | Configuration                  |
| ---- | -------------------------------- | ---------------: | -----------------: | ------------------------------ |
| VM-1 | Edge cluster + intent controller | `129.114.25.182` | `10.56.1.249:6443` | `m1.large`, Ubuntu 22.04.5 LTS |
| VM-2 | Edge cluster                     |  `129.114.25.80` | `10.56.2.149:6443` | `m1.large`, Ubuntu 22.04.5 LTS |

Both VMs use the same Chameleon flavor:

```text
4 vCPU
8 GB RAM
40 GB disk
```

---

## Software Stack

Each VM uses the following layered stack:

```text
Layer 4: Monitoring/control layer
         Prometheus, Grafana, and VM-1 intent controller

Layer 3: Serverless layer
         Knative Serving and Kourier

Layer 2: Kubernetes orchestration layer
         Kind Kubernetes cluster

Layer 1: Container runtime and registry layer
         Docker, Kind node container, local registry

Layer 0: Infrastructure layer
         Ubuntu 22.04.5 LTS on Chameleon KVM VM
```

Two container runtimes are involved. Docker runs on the VM and starts the Kind node container and local registry. Inside the Kind node, containerd runs Kubernetes workloads such as Knative, Kourier, Prometheus, and the function pods.

---

## Repository Structure

Recommended local repository structure:

```text
.
├── f1/
│   └── function source code for f1
├── f2/
│   └── function source code for f2
├── f3/
│   └── function source code for f3
├── scripts/
│   ├── run_chain_workflow.sh
│   ├── deploy_placement.sh
│   ├── deploy_both_all_chain_existing.sh
│   └── run_scheduling_experiment.sh
├── chain_intent.json
├── application_descriptor.json
├── controller_config.json
├── chain_controller.env
├── Makefile
└── README.md
```

VM-1 controller structure:

```text
~/chain-controller/
├── chain_intent.json
├── application_descriptor.json
├── controller_config.json
├── controller.env
└── scripts/
    ├── chain_controller.sh
    ├── collect_chain_metrics.sh
    ├── decide_chain_placement.sh
    └── deploy_chain_selected.sh
```

---

## Input Files

The controller input is intentionally separated into three files.

### `chain_intent.json`

The intent file contains the high-level objective and constraints. It should not contain probing or experiment parameters.

```json
{
  "apiVersion": "intent.dsg/v1alpha1",
  "kind": "Intent",
  "metadata": {
    "name": "function-chain-low-latency"
  },
  "spec": {
    "targetRef": {
      "kind": "ServerlessApplication",
      "name": "function-chain"
    },
    "objectives": [
      {
        "name": "low-latency",
        "metric": "chain_latency_ms",
        "operator": "<=",
        "value": 700,
        "measuredBy": "controller/warm_internal_chain_latency"
      }
    ],
    "properties": [
      {
        "name": "placement-scope",
        "value": "edge"
      }
    ],
    "constraints": [
      {
        "name": "placement-mode",
        "value": "whole-chain"
      }
    ]
  }
}
```

Meaning:

```text
Application: function-chain
Objective: low latency
Requirement: chain latency <= 700 ms
Measurement source: controller warm internal chain latency
Placement scope: edge
Placement mode: whole chain
```

### `application_descriptor.json`

The application descriptor contains the topology of the serverless application.

```json
{
  "application": "function-chain",
  "entrypoint": "f1",
  "functions": [
    {
      "name": "f1",
      "next": "f2"
    },
    {
      "name": "f2",
      "next": "f3"
    },
    {
      "name": "f3",
      "next": null
    }
  ]
}
```

Meaning:

```text
entrypoint = f1
chain = f1 -> f2 -> f3
```

### `controller_config.json`

The controller configuration contains experiment and probing parameters. These are not part of the high-level intent.

```json
{
  "work_ms": 50,
  "samples": 5,
  "ignore_first_sample": true,
  "decision_metric": "warm_internal_chain_latency",
  "candidate_targets": ["vm1", "vm2"]
}
```

Meaning:

```text
work_ms: simulated work per function
samples: number of probing requests per candidate VM
ignore_first_sample: ignore cold-start or scale-from-zero sample
decision_metric: metric used to select the VM
candidate_targets: edge VMs considered by the controller
```

### `chain_controller.env` / `controller.env`

The local workflow creates a stable registry environment file and copies it to VM-1 as `~/chain-controller/controller.env`.

```bash
REGISTRY_VM1=host.docker.internal:5000/elif
REGISTRY_VM2=host.docker.internal:5001/elif
```

These registry names are stable across home, university, and other network locations. They replace the older approach that used the MacBook LAN IP in image names.

---

## Stable Registry Design

Each VM runs a local Docker registry on `127.0.0.1:5000` inside the VM.

The MacBook reaches the registries through SSH tunnels:

```text
Mac 127.0.0.1:5000 -> VM-1 127.0.0.1:5000
Mac 127.0.0.1:5001 -> VM-2 127.0.0.1:5000
```

Docker Desktop on macOS reaches the Mac host through `host.docker.internal`. Therefore, images are built and pushed using stable registry names:

```text
host.docker.internal:5000/elif/f1:latest
host.docker.internal:5000/elif/f2:latest
host.docker.internal:5000/elif/f3:latest

host.docker.internal:5001/elif/f1:latest
host.docker.internal:5001/elif/f2:latest
host.docker.internal:5001/elif/f3:latest
```

This avoids changing registry names whenever the MacBook moves between different networks.

The Kind containerd configuration maps these stable image names to the in-VM registry container:

```text
host.docker.internal:5000 -> registry:5000   on VM-1
host.docker.internal:5001 -> registry:5000   on VM-2
```

Knative is also configured to skip tag-to-digest resolution for these local HTTP registry names.

---

## Required SSH Tunnels

Open these two tunnels from the MacBook and keep both terminals running.

### VM-1 tunnel

```bash
ssh -i ~/.ssh/chameleon_new -N \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5000:127.0.0.1:5000 \
  -L 127.0.0.1:6443:10.56.1.249:6443 \
  cc@129.114.25.182
```

### VM-2 tunnel

```bash
ssh -i ~/.ssh/chameleon_new -N \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5001:127.0.0.1:5000 \
  -L 127.0.0.1:6444:10.56.2.149:6443 \
  cc@129.114.25.80
```

Verify the Kubernetes API tunnels:

```bash
KUBECONFIG=~/.kube/vm1-config kubectl get nodes
KUBECONFIG=~/.kube/vm2-config kubectl get nodes
```

Verify the registry tunnels from the Mac host:

```bash
curl http://127.0.0.1:5000/v2/_catalog
curl http://127.0.0.1:5001/v2/_catalog
```

Verify the registry tunnels from Docker Desktop:

```bash
docker run --rm curlimages/curl:8.9.1 \
  http://host.docker.internal:5000/v2/_catalog

docker run --rm curlimages/curl:8.9.1 \
  http://host.docker.internal:5001/v2/_catalog
```

The Makefile provides the same checks:

```bash
make test-registries
```

---

## Docker Desktop Insecure Registries

Docker Desktop must allow the two local HTTP registry names:

```json
{
  "insecure-registries": [
    "host.docker.internal:5000",
    "host.docker.internal:5001"
  ]
}
```

Restart Docker Desktop after changing this setting.

---

## Kind Containerd Registry Mapping

Run once on VM-1 unless the Kind cluster is recreated:

```bash
docker exec vm1-cluster-control-plane mkdir -p \
  /etc/containerd/certs.d/host.docker.internal:5000

cat <<'EOF' | docker exec -i vm1-cluster-control-plane tee \
  /etc/containerd/certs.d/host.docker.internal:5000/hosts.toml
server = "http://host.docker.internal:5000"

[host."http://registry:5000"]
  capabilities = ["pull", "resolve"]
EOF

docker exec vm1-cluster-control-plane systemctl restart containerd
```

Run once on VM-2 unless the Kind cluster is recreated:

```bash
docker exec vm2-cluster-control-plane mkdir -p \
  /etc/containerd/certs.d/host.docker.internal:5001

cat <<'EOF' | docker exec -i vm2-cluster-control-plane tee \
  /etc/containerd/certs.d/host.docker.internal:5001/hosts.toml
server = "http://host.docker.internal:5001"

[host."http://registry:5000"]
  capabilities = ["pull", "resolve"]
EOF

docker exec vm2-cluster-control-plane systemctl restart containerd
```

---

## Knative Registry Skip Configuration

Run once per cluster unless the cluster is recreated.

VM-1:

```bash
KUBECONFIG=~/.kube/vm1-config kubectl patch configmap config-deployment \
  -n knative-serving \
  --type merge \
  -p '{"data":{"registries-skipping-tag-resolving":"host.docker.internal:5000,registry:5000,localhost:5000,127.0.0.1:5000"}}'

KUBECONFIG=~/.kube/vm1-config kubectl rollout restart deployment controller -n knative-serving
KUBECONFIG=~/.kube/vm1-config kubectl rollout status deployment controller -n knative-serving
```

VM-2:

```bash
KUBECONFIG=~/.kube/vm2-config kubectl patch configmap config-deployment \
  -n knative-serving \
  --type merge \
  -p '{"data":{"registries-skipping-tag-resolving":"host.docker.internal:5001,registry:5000,localhost:5000,127.0.0.1:5001"}}'

KUBECONFIG=~/.kube/vm2-config kubectl rollout restart deployment controller -n knative-serving
KUBECONFIG=~/.kube/vm2-config kubectl rollout status deployment controller -n knative-serving
```

---

## Prometheus Access

The controller runs on VM-1. Therefore, both Prometheus APIs must be reachable from VM-1.

On VM-1 terminal 1:

```bash
KUBECONFIG=~/.kube/vm1-config kubectl -n monitoring port-forward \
  svc/kube-prometheus-stack-prometheus 9091:9090
```

On VM-1 terminal 2:

```bash
KUBECONFIG=~/vm2-from-vm1-config kubectl -n monitoring port-forward \
  svc/kube-prometheus-stack-prometheus 9092:9090
```

Verify from VM-1:

```bash
curl -s "http://127.0.0.1:9091/api/v1/query?query=up" | head
curl -s "http://127.0.0.1:9092/api/v1/query?query=up" | head
```

The controller uses these endpoints:

```text
127.0.0.1:9091 -> VM-1 Prometheus
127.0.0.1:9092 -> VM-2 Prometheus
```

If these port-forwards are not running, latency probing still works, but Prometheus infrastructure metrics may show `NA` or zero values.

---

## Controller Scripts

The VM-1 controller is divided into four scripts.

### `chain_controller.sh`

Main orchestration script.

Responsibilities:

```text
1. Read chain_intent.json
2. Read application_descriptor.json
3. Read controller_config.json
4. Call collect_chain_metrics.sh
5. Call decide_chain_placement.sh
6. Call deploy_chain_selected.sh
7. Print the selected chain URL
```

Control flow:

```text
chain_controller.sh
  -> collect_chain_metrics.sh
  -> decide_chain_placement.sh
  -> deploy_chain_selected.sh
```

### `collect_chain_metrics.sh`

Measurement script.

Responsibilities:

```text
1. Read the intent and controller config
2. Query Prometheus metrics from VM-1 and VM-2
3. Actively probe the f1 public URL on VM-1 and VM-2
4. Measure external HTTP latency
5. Extract internal chain latency from the f1 response
6. Ignore the first sample if configured
7. Compute warm average latency values
8. Write /tmp/chain_controller_metrics_summary.env
```

Prometheus is used for infrastructure metrics:

```text
CPU usage
memory usage
available replicas
```

Latency is currently measured by active probing. The response field `chain_duration_ms` is used as the internal chain latency.

Example metric summary:

```text
VM1_WARM_INTERNAL=209.69
VM2_WARM_INTERNAL=213.19
VM1_WARM_EXTERNAL=218.24
VM2_WARM_EXTERNAL=222.56
VM1_WARM_VIOLATION_RATE=0.0
VM2_WARM_VIOLATION_RATE=0.0
```

### `decide_chain_placement.sh`

Decision script.

Responsibilities:

```text
1. Read /tmp/chain_controller_metrics_summary.env
2. Read the latency requirement from chain_intent.json
3. Read the decision metric from controller_config.json
4. Compare VM-1 and VM-2
5. Select the VM with the lower selected metric value
6. Check whether the selected value satisfies the intent
7. Write /tmp/chain_decision.env
```

Example decision:

```text
SELECTED_VM=vm1
SELECTED_VALUE=209.69
INTENT_SATISFIED=true
DECISION_METRIC=warm_internal_chain_latency
SLA_MS=700
```

### `deploy_chain_selected.sh`

Deployment script.

Responsibilities:

```text
1. Receive selected VM as input
2. Select the correct kubeconfig, registry, and floating IP
3. Deploy f3, f2, and f1 as Knative Services
4. Configure f1 to call f2 and f2 to call f3
5. Wait until all Knative services are ready
6. Write /tmp/selected_chain_url.txt
```

The active deployment script uses stable registry names:

```bash
REGISTRY_VM1="${REGISTRY_VM1:-host.docker.internal:5000/elif}"
REGISTRY_VM2="${REGISTRY_VM2:-host.docker.internal:5001/elif}"
```

Example selected URL:

```text
http://f1.default.129.114.25.182.sslip.io
```

---

## Function Chain

The application contains three functions:

```text
f1 -> f2 -> f3
```

The client invokes only `f1`.

When the complete chain is deployed on one VM, internal Kubernetes DNS is used for inter-function communication:

```text
http://f2.default.svc.cluster.local
http://f3.default.svc.cluster.local
```

The public entry URLs have the following form:

```text
VM-1: http://f1.default.129.114.25.182.sslip.io
VM-2: http://f1.default.129.114.25.80.sslip.io
```

Example request payload:

```json
{
  "work_ms": 50
}
```

Example response:

```json
{
  "function": "f1",
  "message": "function chain completed",
  "vm_floating_ip": "129.114.25.182",
  "work_ms": 50,
  "chain_duration_ms": 407.5,
  "f2_response": {
    "function": "f2",
    "vm_floating_ip": "129.114.25.182",
    "f3_response": {
      "function": "f3",
      "vm_floating_ip": "129.114.25.182"
    }
  }
}
```

The `vm_floating_ip` fields confirm where the functions executed.

---

## Makefile

The Makefile uses stable registry names:

```makefile
REGISTRY_VM1 ?= host.docker.internal:5000/elif
REGISTRY_VM2 ?= host.docker.internal:5001/elif
```

Useful commands:

```bash
make check
make show-tunnels
make test-registries

make build-push-vm1
make build-push-vm2
make build-push-all

make deploy-vm1
make deploy-vm2

make deploy-vm1-existing
make deploy-vm2-existing

make invoke-vm1
make invoke-vm2

make clean-vm1
make clean-vm2
```

---

## Running the Full Workflow

From the local developer laptop:

```bash
cd ~/thesis/function-chain
./scripts/run_chain_workflow.sh
```

The workflow performs the following steps:

```text
1. Check Makefile configuration
2. Test registry tunnels from the Mac host and Docker Desktop
3. Create controller.env with stable registry names
4. Build f1, f2, and f3 images for VM-1
5. Push images to the VM-1 registry
6. Build f1, f2, and f3 images for VM-2
7. Push images to the VM-2 registry
8. Copy chain_intent.json to VM-1
9. Copy application_descriptor.json to VM-1
10. Copy controller_config.json to VM-1
11. Copy controller.env to VM-1
12. Trigger the VM-1 controller through SSH
13. Collect Prometheus metrics and active latency measurements
14. Select the suitable VM
15. Deploy the complete function chain to the selected VM
16. Retrieve the selected f1 URL
17. Invoke the selected chain from the local laptop
```

Expected controller output contains sections similar to:

```text
===== Collecting monitoring and latency metrics =====
===== Deciding suitable VM for intent =====
===== Deploying selected whole-chain placement =====
===== Done =====
```

Example successful decision:

```text
Selected whole-chain placement: [vm1, vm1, vm1]
Selected metric value: 209.69 ms
Intent requirement: <= 700 ms
Intent satisfied: true
```

---

## Manual Invocation

After deployment, the selected URL is stored on VM-1:

```text
/tmp/selected_chain_url.txt
```

Retrieve it from the MacBook:

```bash
SELECTED_URL=$(ssh -i ~/.ssh/chameleon_new cc@129.114.25.182 \
  'cat /tmp/selected_chain_url.txt')
echo "$SELECTED_URL"
```

Invoke the selected chain:

```bash
curl -s -X POST "$SELECTED_URL" \
  -H "Content-Type: application/json" \
  -d '{"work_ms":50}' | python3 -m json.tool
```

Direct invocation examples:

```bash
curl -s -X POST "http://f1.default.129.114.25.182.sslip.io" \
  -H "Content-Type: application/json" \
  -d '{"work_ms":50}' | python3 -m json.tool

curl -s -X POST "http://f1.default.129.114.25.80.sslip.io" \
  -H "Content-Type: application/json" \
  -d '{"work_ms":50}' | python3 -m json.tool
```

---

## Checking Controller Cleanliness

The active VM-1 controller scripts should be:

```text
chain_controller.sh
collect_chain_metrics.sh
decide_chain_placement.sh
deploy_chain_selected.sh
```

Check that there is no old LAN-IP or MAC-IP logic:

```bash
cd ~/chain-controller

grep -RInE 'LAN_IP|MAC_IP|ipconfig|getifaddr|192\.168\.|143\.205\.|NEW_IP|\$\{MAC_IP\}|\$MAC_IP' . \
  --exclude='*.bak*'
```

Expected result:

```text
no output
```

---

## When the Mac Network Changes

No image registry changes are required.

The current setup uses stable registry names:

```text
host.docker.internal:5000/elif
host.docker.internal:5001/elif
```

Therefore, switching between home, university, or another Wi-Fi network does not require changing Docker registry names, containerd registry mappings, or Knative registry skip lists.

The only requirement is to reopen the SSH tunnels:

```bash
ssh -i ~/.ssh/chameleon_new -N \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5000:127.0.0.1:5000 \
  -L 127.0.0.1:6443:10.56.1.249:6443 \
  cc@129.114.25.182
```

```bash
ssh -i ~/.ssh/chameleon_new -N \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5001:127.0.0.1:5000 \
  -L 127.0.0.1:6444:10.56.2.149:6443 \
  cc@129.114.25.80
```

---

## Troubleshooting

| Problem                                        | Likely Cause                                                                    | Fix                                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `kubectl connection refused`                   | Kubernetes API SSH tunnel is closed                                             | Reopen the Kubernetes API tunnel                                                        |
| Docker push fails                              | Registry tunnel is closed or Docker Desktop cannot reach `host.docker.internal` | Run `make test-registries` and reopen tunnels                                           |
| `ImagePullBackOff`                             | Kind/containerd cannot map the stable registry name to `registry:5000`          | Check `/etc/containerd/certs.d/host.docker.internal:<port>/hosts.toml` in the Kind node |
| `RevisionFailed: Unable to fetch image`        | Knative tried HTTPS tag resolution on the HTTP registry                         | Patch `registries-skipping-tag-resolving` and restart the Knative controller            |
| `kn: command not found` on VM-1                | Knative CLI is missing on VM-1                                                  | Install `kn` on VM-1 or add it to PATH                                                  |
| Function runs on wrong VM                      | Wrong kubeconfig or registry selected                                           | Check `deploy_chain_selected.sh`, selected VM, and `controller.env`                     |
| First latency sample is very high              | Cold start or scale-from-zero                                                   | Use `ignore_first_sample=true`                                                          |
| Prometheus values show zero before probing     | Services may be scaled to zero or not scraped yet                               | Check after probing or verify Prometheus port-forward                                   |
| Prometheus values show `NA`                    | Prometheus port-forward is not running on VM-1                                  | Start port-forwards for `9091` and `9092`                                               |
| Final invocation differs from decision latency | New revision warm-up or transient network overhead                              | Repeat invocation or inspect warm samples                                               |

---

## Current Scope

Implemented:

```text
Two edge clusters
Three-function serverless chain
Low-latency intent
Separated intent, application descriptor, and controller config
Stable registry naming independent of Mac LAN IP
Prometheus infrastructure monitoring
Active latency probing
Warm latency decision metric
Whole-chain placement
Knative deployment
Local laptop invocation
```

Not yet implemented:

```text
Automatic per-function split placement
Continuous closed-loop re-optimization
Prometheus-native application latency metrics
Reinforcement-learning scheduler
Multi-application orchestration
```

---

## Notes

This repository contains a master's thesis research prototype. Some values such as VM public IPs and private Kind API addresses are environment-specific.

Do not commit private SSH keys, kubeconfig files, Chameleon credentials, or other secrets to the repository.

---

## References

* Chameleon Cloud: https://chameleoncloud.org
* Kind: https://kind.sigs.k8s.io
* Knative Serving: https://knative.dev/docs/serving/
* Kourier: https://github.com/knative-extensions/net-kourier
* Prometheus: https://prometheus.io
* Filinis et al. (2024). *Intent-driven orchestration of serverless applications in the computing continuum.* Future Generation Computer Systems, 154, 72–86.
