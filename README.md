# Intent-based Orchestration of Serverless Applications on the Edge Environment

- Google Doc: https://docs.google.com/document/d/1xbqqjmtBV4V2OLD5ANaIbH84hBZWOgD_vCm3HCb3diQ/edit?tab=t.0
- Start-Talk Presentation: https://docs.google.com/presentation/d/1HWVVsK8waD55ea7L2RZmUNY0H8EtgBZ93Fs5xUBCtwU/edit?usp=sharing (27.04.2026)
- Overleaf Mini-Survey Paper : https://www.overleaf.com/project/694e5cc4b66cecf94dedbf6b
- Overleaf Paper : https://www.overleaf.com/project/6973a2af12c63cd8bd7f011a

# Edge-Cloud Testbed Setup on Chameleon Cloud

A multi-cluster Kubernetes testbed for edge-cloud federation research, built on Chameleon Cloud using Kind, Submariner, Knative, and Prometheus.

---

## Architecture Overview

```
VM1 (edge-vm)                        VM2 (cloud-vm)
10.52.0.213                          10.52.2.163
┌─────────────────────────┐          ┌─────────────────────────┐
│  Kind cluster (edge)    │          │  Kind cluster (cloud)   │
│  10.244.0.0/16 pods     │          │  10.245.0.0/16 pods     │
│  10.96.0.0/16 services  │          │  10.97.0.0/16 services  │
│                         │          │                         │
│  ┌─────────────────┐    │          │  ┌─────────────────┐    │
│  │ Knative Serving │    │          │  │ Knative Serving │    │
│  │ + Kourier       │    │          │  │ + Kourier       │    │
│  └─────────────────┘    │          │  └─────────────────┘    │
│  ┌─────────────────┐    │          │  ┌─────────────────┐    │
│  │ Prometheus      │    │          │  │ Prometheus      │    │
│  │ + Grafana       │    │          │  │ + Grafana       │    │
│  └─────────────────┘    │          │  └─────────────────┘    │
│  ┌─────────────────┐    │          │  ┌─────────────────┐    │
│  │ Submariner GW   │◄───┼──────────┼──│ Submariner GW   │    │
│  │ (libreswan)     │    │IPsec/NAT │  │ (libreswan)     │    │
│  └─────────────────┘    │          │  └─────────────────┘    │
└─────────────────────────┘          └─────────────────────────┘
```

---

## Prerequisites

- 2 x Chameleon Cloud VMs (Ubuntu 22.04, recommended: m1.large or larger)
- SSH access to both VMs
- Both VMs on the same Chameleon network segment (10.52.x.x)

---

## Step 1 — Provision VMs on Chameleon Cloud

Reserve two bare-metal or KVM nodes on [chameleoncloud.org](https://chameleoncloud.org).

```bash
# Note your floating IPs after reservation
# VM1 (edge):  e.g. 129.114.x.x  → internal 10.52.0.213
# VM2 (cloud): e.g. 129.114.x.x  → internal 10.52.2.163

ssh -i ~/.ssh/chameleon.pem cc@<edge-floating-ip>
ssh -i ~/.ssh/chameleon.pem cc@<cloud-floating-ip>
```

---

## Step 2 — Install Dependencies (both VMs)

```bash
# Docker
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker $USER
newgrp docker

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl && sudo mv kubectl /usr/local/bin/

# Kind
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.24.0/kind-linux-amd64
chmod +x kind && sudo mv kind /usr/local/bin/

# Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# subctl (Submariner CLI)
curl -Ls https://get.submariner.io | bash
export PATH=$PATH:~/.local/bin
echo 'export PATH=$PATH:~/.local/bin' >> ~/.bashrc
```

---

## Step 3 — Create Kind Clusters

### On edge-vm

```bash
cat > kind-edge.yaml << EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  podSubnet: "10.244.0.0/16"
  serviceSubnet: "10.96.0.0/16"
  apiServerAddress: "10.52.0.213"
  apiServerPort: 6443
EOF

kind create cluster --name edge --config kind-edge.yaml
kubectl cluster-info --context kind-edge
```

### On cloud-vm

```bash
cat > kind-cloud.yaml << EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  podSubnet: "10.245.0.0/16"
  serviceSubnet: "10.97.0.0/16"
  apiServerAddress: "10.52.2.163"
  apiServerPort: 6443
EOF

kind create cluster --name cloud --config kind-cloud.yaml
kubectl cluster-info --context kind-cloud
```

> **Important:** Pod and service CIDRs must not overlap between clusters.

---

## Step 4 — Install Submariner

Submariner federates the two clusters so pods can communicate across cluster boundaries using DNS (`svc.clusterset.local`).

### On cloud-vm — deploy the broker

```bash
subctl deploy-broker
# This creates a broker-info.subm file — copy it to edge-vm
scp broker-info.subm cc@<edge-internal-ip>:~
```

### On cloud-vm — join cloud cluster

```bash
subctl join broker-info.subm \
  --clusterid cloud \
  --natt=true \
  --cable-driver libreswan
```

### On edge-vm — join edge cluster

```bash
subctl join broker-info.subm \
  --clusterid edge \
  --natt=true \
  --cable-driver libreswan
```

### Verify connection

```bash
# On either VM
subctl show connections
# Expected: STATUS = connected
```

---

## Step 5 — Install Knative Serving (both VMs)

```bash
# Install CRDs and core
kubectl apply -f https://github.com/knative/serving/releases/download/knative-v1.21.2/serving-crds.yaml
kubectl apply -f https://github.com/knative/serving/releases/download/knative-v1.21.2/serving-core.yaml

# Install Kourier as the ingress
kubectl apply -f https://github.com/knative/net-kourier/releases/download/knative-v1.16.0/kourier.yaml

# Configure Knative to use Kourier
kubectl patch configmap/config-network \
  -n knative-serving \
  --type merge \
  -p '{"data":{"ingress-class":"kourier.ingress.networking.knative.dev"}}'

# Verify
kubectl get pods -n knative-serving
kubectl get pods -n kourier-system
```

---

## Step 6 — Install Prometheus + Grafana (both VMs)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.enabled=true \
  --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.resources.requests.memory=256Mi \
  --set prometheus.prometheusSpec.resources.requests.cpu=100m

kubectl get pods -n monitoring
```

---

## Step 7 — Deploy Test Workloads

### Single-cluster test (nginx pod)

```bash
# On edge-vm
kubectl create deployment hello-edge --image=nginx
kubectl expose deployment hello-edge --port 80 --target-port 80

# Test locally
kubectl run test --rm -it --image=busybox -- wget -O- http://hello-edge
```

### Export service for cross-cluster access

```bash
# On edge-vm
subctl export service hello-edge
kubectl get serviceexports -A

# On cloud-vm — verify import and test
kubectl get serviceimports -A
kubectl run tmp-shell --rm -it --image=busybox -- \
  wget -O- http://hello-edge.default.svc.clusterset.local
```

### Knative hello-world service

```bash
# On edge-vm
kubectl apply -f - <<EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello-knative
  namespace: default
spec:
  template:
    spec:
      containers:
      - image: gcr.io/knative-samples/helloworld-go
        env:
        - name: TARGET
          value: "Edge Cluster"
EOF

kubectl get ksvc hello-knative
```

---

## Step 8 — Cross-Cluster Knative Invocation

This allows a pod on cloud to invoke a Knative service running on edge, through Submariner's service mesh.

```bash
# On edge-vm — export Kourier's internal gateway
subctl export service kourier-internal -n kourier-system

# On cloud-vm — call edge's Knative service
kubectl run curl-test --rm -it --image=curlimages/curl -- \
  curl -s \
  -H "Host: hello-knative.default.svc.cluster.local" \
  http://kourier-internal.kourier-system.svc.clusterset.local

# Expected output: Hello Edge Cluster!
```

---

## Troubleshooting

### Issue 1: Submariner shows `connected` but cross-cluster traffic fails

**Symptom:** `subctl show connections` shows `connected` with RTT, but `wget` from a pod returns `No route to host`.

**Root cause:** Submariner's health checker uses ICMP ping (`NET_RAW` capability) to verify tunnel liveness. Inside Kind containers, this capability is restricted, causing health checks to fail silently while the control plane still reports `connected`.

**Fix — disable connection health check:**

```bash
# On both cloud-vm and edge-vm
kubectl patch submariner submariner \
  -n submariner-operator \
  --type=merge \
  -p '{"spec":{"connectionHealthCheck":{"enabled":false}}}'

# Restart gateway pods on both VMs
kubectl rollout restart daemonset/submariner-gateway -n submariner-operator
kubectl rollout status daemonset/submariner-gateway -n submariner-operator

# Verify connection
subctl show connections
```

### Issue 2: DNS resolves but connection refused

**Symptom:** `nslookup hello-edge.default.svc.clusterset.local` returns an IP, but `wget` hangs or fails.

**Root cause:** The ServiceImport's `use-clusterset-ip` annotation defaults to `false`, causing DNS to return the remote cluster's ClusterIP directly instead of routing through Submariner's proxy.

**Fix:**

```bash
kubectl annotate serviceimport <service-name> \
  lighthouse.submariner.io/use-clusterset-ip=true \
  --overwrite -n default
```

### Issue 3: Kourier 404 on install

**Symptom:** `error: unable to read URL .../knative-v1.21.2/kourier.yaml, server reported 404`

**Fix:** Kourier releases don't always align with Knative serving releases. Use v1.16.0 of Kourier with Knative v1.21:

```bash
kubectl apply -f https://github.com/knative/net-kourier/releases/download/knative-v1.16.0/kourier.yaml
```

### Issue 4: Submariner gateway pod has no `ping` or `wget`

This is expected — the Submariner gateway image is minimal. Use `/dev/tcp` for connectivity tests:

```bash
kubectl exec -n submariner-operator $GWPOD -- \
  bash -c "echo > /dev/tcp/<ip>/80" 2>&1
```

### Issue 5: Cross-cluster VXLAN tunnel RX always 0

**Symptom:** `subctl show connections` shows `connected`, but `ip -s link show vxlan-tunnel` shows RX bytes = 0.

**Root cause:** Submariner was installed with `--cable-driver vxlan` but the health checker requires `NET_RAW`. The actual working cable driver for this Kind-on-VM setup is `libreswan` (IPsec).

**Fix — re-join with libreswan:**

```bash
subctl join broker-info.subm \
  --clusterid <cluster-id> \
  --natt=true \
  --cable-driver libreswan
```

---

## Verification Checklist

```bash
# 1. Both clusters healthy
kubectl get nodes                          # Ready

# 2. Submariner connected
subctl show connections                    # STATUS: connected

# 3. Cross-cluster DNS works
kubectl run tmp --rm -it --image=busybox -- \
  nslookup hello-edge.default.svc.clusterset.local

# 4. Cross-cluster traffic works
kubectl run tmp --rm -it --image=busybox -- \
  wget -O- http://hello-edge.default.svc.clusterset.local

# 5. Knative working
kubectl get ksvc                           # READY: True

# 6. Cross-cluster Knative invocation
kubectl run curl-test --rm -it --image=curlimages/curl -- \
  curl -s \
  -H "Host: hello-knative.default.svc.cluster.local" \
  http://kourier-internal.kourier-system.svc.clusterset.local

# 7. Prometheus running
kubectl get pods -n monitoring             # All Running
```

---

## Component Versions

| Component | Version |
|-----------|---------|
| Kubernetes (edge) | v1.33.1 |
| Kubernetes (cloud) | v1.35.1 |
| Kind | v0.24.0 |
| Submariner | v0.23.1 |
| Knative Serving | v1.21.2 |
| Kourier | v1.16.0 |
| kube-prometheus-stack | latest |
| Cable driver | libreswan |

---

## References

- [Chameleon Cloud](https://chameleoncloud.org)
- [Kind documentation](https://kind.sigs.k8s.io)
- [Submariner documentation](https://submariner.io)
- [Knative documentation](https://knative.dev)
- [subctl CLI reference](https://submariner.io/operations/deployment/subctl)
