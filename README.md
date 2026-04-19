# Intent-based Orchestration of Serverless Applications on the Edge Environment

- Google Doc: https://docs.google.com/document/d/1xbqqjmtBV4V2OLD5ANaIbH84hBZWOgD_vCm3HCb3diQ/edit?tab=t.0
- Start-Talk Presentation: https://docs.google.com/presentation/d/1HWVVsK8waD55ea7L2RZmUNY0H8EtgBZ93Fs5xUBCtwU/edit?usp=sharing (27.04.2026)
- Overleaf Mini-Survey Paper : https://www.overleaf.com/project/694e5cc4b66cecf94dedbf6b
- Overleaf Paper : https://www.overleaf.com/project/6973a2af12c63cd8bd7f011a

# Edge-Cloud Serverless Testbed on Chameleon Cloud

> Master's Thesis: Intent-based Orchestration of Serverless Applications on the Edge Environment

A multi-cluster Kubernetes testbed for edge-cloud federation research, built on Chameleon Cloud using Kind, Submariner, Knative, and Prometheus.

---

## Architecture Overview

### Physical Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Chameleon Cloud                              │
│                                                                     │
│   VM1 (edge-vm)                        VM2 (cloud-vm)              │
│   10.52.0.213                          10.52.2.163                  │
│   Ubuntu 22.04 · x86_64               Ubuntu 22.04 · x86_64        │
│                                                                     │
│  ┌──────────────────────────┐         ┌──────────────────────────┐  │
│  │  Kind cluster (edge)     │         │  Kind cluster (cloud)    │  │
│  │  k8s v1.33.1             │         │  k8s v1.35.1             │  │
│  │  pods:    10.244.0.0/16  │         │  pods:    10.245.0.0/16  │  │
│  │  services:10.96.0.0/16   │         │  services:10.97.0.0/16   │  │
│  │                          │         │                          │  │
│  │  ┌────────────────────┐  │         │  ┌────────────────────┐  │  │
│  │  │  Knative Serving   │  │         │  │  Knative Serving   │  │  │
│  │  │  + Kourier v1.16   │  │         │  │  + Kourier v1.16   │  │  │
│  │  └────────────────────┘  │         │  └────────────────────┘  │  │
│  │  ┌────────────────────┐  │         │  ┌────────────────────┐  │  │
│  │  │  Prometheus        │  │         │  │  Prometheus        │  │  │
│  │  │  + Grafana         │  │         │  │  + Grafana         │  │  │
│  │  └────────────────────┘  │         │  └────────────────────┘  │  │
│  │  ┌────────────────────┐  │         │  ┌────────────────────┐  │  │
│  │  │  Submariner GW     │◄─┼─────────┼──│  Submariner GW     │  │  │
│  │  │  (libreswan/IPsec) │  │  tunnel │  │  (libreswan/IPsec) │  │  │
│  │  └────────────────────┘  │         │  └────────────────────┘  │  │
│  └──────────────────────────┘         └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Software Stack (each VM)

```
┌─────────────────────────────────────┐
│  Layer 4: Knative Serving + Kourier │  ← serverless functions
├─────────────────────────────────────┤
│  Layer 3: Kubernetes (Kind cluster) │  ← orchestration
├─────────────────────────────────────┤
│  Layer 2: Kind (kindest/node image) │  ← k8s in Docker
├─────────────────────────────────────┤
│  Layer 1: Docker 28.2.2             │  ← container runtime
├─────────────────────────────────────┤
│  Ubuntu 22.04 LTS · x86_64          │  ← VM OS
└─────────────────────────────────────┘
```

---

## Component Versions

| Component | edge-vm | cloud-vm |
|-----------|---------|----------|
| OS | Ubuntu 22.04 LTS x86_64 | Ubuntu 22.04 LTS x86_64 |
| Docker | 28.2.2 | 28.2.2 |
| Kind image | kindest/node:v1.33.1 | kindest/node:v1.35.1 |
| Kubernetes | v1.33.1 | v1.35.1 |
| Knative Serving | v1.21.2 | v1.21.2 |
| Kourier | v1.16.0 | v1.16.0 |
| Submariner | v0.23.1 | v0.23.1 |
| Cable driver | libreswan | libreswan |
| Prometheus stack | kube-prometheus-stack | kube-prometheus-stack |

---

## Prerequisites

- 2 x Chameleon Cloud VMs (Ubuntu 22.04, m1.large or larger)
- SSH access to both VMs
- Both VMs on the same Chameleon network segment (10.52.x.x)
- Docker Hub account (for pushing your own function images)

---

## Step 1 — Provision VMs on Chameleon Cloud

Reserve two KVM nodes on [chameleoncloud.org](https://chameleoncloud.org).

```bash
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

### On cloud-vm — deploy the broker

```bash
subctl deploy-broker
scp broker-info.subm cc@<edge-internal-ip>:~
```

### On cloud-vm — join

```bash
subctl join broker-info.subm \
  --clusterid cloud \
  --natt=true \
  --cable-driver libreswan
```

### On edge-vm — join

```bash
subctl join broker-info.subm \
  --clusterid edge \
  --natt=true \
  --cable-driver libreswan
```

### Verify

```bash
subctl show connections
# Expected: STATUS = connected, RTT avg shown
```

---

## Step 5 — Install Knative Serving (both VMs)

```bash
kubectl apply -f https://github.com/knative/serving/releases/download/knative-v1.21.2/serving-crds.yaml
kubectl apply -f https://github.com/knative/serving/releases/download/knative-v1.21.2/serving-core.yaml
kubectl apply -f https://github.com/knative/net-kourier/releases/download/knative-v1.16.0/kourier.yaml

kubectl patch configmap/config-network \
  -n knative-serving \
  --type merge \
  -p '{"data":{"ingress-class":"kourier.ingress.networking.knative.dev"}}'

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

## Step 7 — Deploy and Test Workloads

### Task 1 — Cross-cluster pod deployment (plain Kubernetes)

Deploy a pod to cloud cluster FROM edge-vm using kubeconfig:

```bash
# On edge-vm — target cloud cluster
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml \
  run hello-from-edge --image=nginx --restart=Never

# Verify pod runs on cloud-control-plane
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml \
  get pods -o wide
# Expected: hello-from-edge Running on cloud-control-plane
```

### Task 2 — Knative serverless function (your own code)

Write, build, and deploy a Python function as a Knative Service:

```bash
# 1. Write the function locally (on your laptop)
mkdir hello-knative && cd hello-knative

cat > app.py << 'EOF'
from flask import Flask
import os

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def hello():
    target = os.environ.get('TARGET', 'World')
    return f'Hello {target}! This is my Knative serverless function.\n'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
EOF

cat > requirements.txt << 'EOF'
flask>=2.0.0
EOF

cat > Dockerfile << 'EOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
EXPOSE 8080
CMD ["python", "app.py"]
EOF

# 2. Build for linux/amd64 (Chameleon VMs are x86)
docker buildx build --platform linux/amd64 \
  -t <your-dockerhub>/hello-knative:v1 --push .

# 3. Deploy as Knative Service on edge-vm
kubectl apply -f - << EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello-python
  namespace: default
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/min-scale: "1"
    spec:
      containers:
      - image: <your-dockerhub>/hello-knative:v1
        env:
        - name: TARGET
          value: "Edge Cluster"
        ports:
        - containerPort: 8080
EOF

# 4. Verify Knative resources
kubectl get ksvc hello-python      # READY: True
kubectl get revisions              # shows revision versions
kubectl get routes                 # shows traffic routing

# 5. Invoke
kubectl exec <any-running-pod> -- \
  curl -s http://hello-python.default.svc.cluster.local
# Output: Hello Edge Cluster! This is my Knative serverless function.
```

### Task 3 — Deploy Knative function from edge TO cloud

```bash
# From edge-vm — deploy to cloud cluster
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml apply -f - << EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello-python
  namespace: default
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/min-scale: "1"
    spec:
      containers:
      - image: <your-dockerhub>/hello-knative:v1
        env:
        - name: TARGET
          value: "Cloud Cluster - deployed from Edge"
        ports:
        - containerPort: 8080
EOF

# Verify on cloud — from edge
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml get ksvc hello-python
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml get revisions
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml get routes
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml get pods -o wide
# Pod runs on cloud-control-plane

# Invoke from inside the cloud pod
kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml \
  exec $(kubectl --kubeconfig=/home/cc/cloud-kubeconfig.yaml \
  get pods -l serving.knative.dev/service=hello-python \
  -o jsonpath='{.items[0].metadata.name}') \
  -c user-container -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080').read().decode())"
# Output: Hello Cloud Cluster - deployed from Edge! This is my Knative serverless function.
```

### Cross-cluster Knative invocation via Submariner

```bash
# On edge-vm — export Kourier gateway
subctl export service kourier-internal -n kourier-system

# On cloud-vm — invoke edge's function
kubectl run curl-test --rm -it --image=curlimages/curl -- \
  curl -s \
  -H "Host: hello-python.default.svc.cluster.local" \
  http://kourier-internal.kourier-system.svc.clusterset.local
# Output: Hello Edge Cluster! This is my Knative serverless function.
```

---

## Stack Verification

Run this on both VMs to confirm all layers:

```bash
echo "=== LAYER 1: DOCKER ===" && docker --version
echo "=== LAYER 2: KIND IN DOCKER ===" && docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
echo "=== LAYER 3: KUBERNETES ===" && kubectl get nodes -o wide
echo "=== LAYER 4: KNATIVE ===" && kubectl get pods -n knative-serving
echo "=== LAYER 5: FUNCTIONS ===" && kubectl get ksvc -A
```

### Verified output — edge-vm

```
LAYER 1: Docker version 28.2.2
LAYER 2: edge-control-plane   kindest/node:v1.33.1   Up 3 weeks
LAYER 3: edge-control-plane   Ready   control-plane   v1.33.1   172.19.0.2
LAYER 4: activator, autoscaler, controller, net-kourier-controller, webhook — all 1/1 Running
LAYER 5: hello-python True, hello-knative True
```

### Verified output — cloud-vm

```
LAYER 1: Docker version 28.2.2
LAYER 2: cloud-control-plane   kindest/node:v1.35.1   Up 4 weeks
LAYER 3: cloud-control-plane   Ready   control-plane   v1.35.1   172.18.0.2
LAYER 4: activator, autoscaler, controller, net-kourier-controller, webhook — all 1/1 Running
LAYER 5: hello-python True, hello-knative True, hello-from-edge True
```

---

## Troubleshooting

### Submariner connected but traffic fails

```bash
# Disable health check (NET_RAW not available in Kind)
kubectl patch submariner submariner \
  -n submariner-operator \
  --type=merge \
  -p '{"spec":{"connectionHealthCheck":{"enabled":false}}}'

kubectl rollout restart daemonset/submariner-gateway -n submariner-operator
subctl show connections
```

### DNS resolves but connection refused

```bash
kubectl annotate serviceimport <service-name> \
  lighthouse.submariner.io/use-clusterset-ip=true \
  --overwrite -n default
```

### Docker image exec format error

```bash
# Rebuild for correct architecture
docker buildx build --platform linux/amd64 \
  -t <your-image>:v2 --push .
```

### Kourier 404 on install

```bash
# Use v1.16.0 with Knative v1.21
kubectl apply -f https://github.com/knative/net-kourier/releases/download/knative-v1.16.0/kourier.yaml
```

### Cross-cluster VXLAN RX always 0

```bash
# Re-join with libreswan instead of vxlan
subctl join broker-info.subm \
  --clusterid <id> --natt=true --cable-driver libreswan
```

---

## Verification Checklist

```bash
kubectl get nodes                    # Ready
subctl show connections              # connected
kubectl get pods -n knative-serving  # all Running
kubectl get pods -n monitoring       # all Running
kubectl get ksvc -A                  # READY: True
subctl show all                      # full status
```

---

## References

- [Chameleon Cloud](https://chameleoncloud.org)
- [Kind](https://kind.sigs.k8s.io)
- [Submariner](https://submariner.io)
- [Knative](https://knative.dev)
- [Prometheus](https://prometheus.io)
- Filinis et al. (2024) — Intent-driven orchestration of serverless applications in the computing continuum. *Future Generation Computer Systems*, 154, 72–86.
