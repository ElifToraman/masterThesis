# Function chain deployment Makefile
# f1 -> f2 -> f3

PLATFORM ?= linux/amd64
PORT ?= 8080
WORK_MS ?= 50

REGISTRY_VM1 ?= host.docker.internal:5000/elif
REGISTRY_VM2 ?= host.docker.internal:5001/elif

KUBECONFIG_VM1 ?= $(HOME)/.kube/vm1-config
KUBECONFIG_VM2 ?= $(HOME)/.kube/vm2-config

FLOATING_IP_VM1 ?= 129.114.25.182
FLOATING_IP_VM2 ?= 129.114.25.80

F2_INTERNAL_URL ?= http://f2.default.svc.cluster.local
F3_INTERNAL_URL ?= http://f3.default.svc.cluster.local

FUNCTIONS = f1 f2 f3

.PHONY: check test-registries show-tunnels \
        build-push-vm1 build-push-vm2 build-push-all \
        deploy-vm1 deploy-vm2 deploy-vm1-existing deploy-vm2-existing \
        invoke-vm1 invoke-vm2 \
        clean-vm1 clean-vm2 clean-all

check:
	@echo "REGISTRY_VM1=$(REGISTRY_VM1)"
	@echo "REGISTRY_VM2=$(REGISTRY_VM2)"
	@echo "KUBECONFIG_VM1=$(KUBECONFIG_VM1)"
	@echo "KUBECONFIG_VM2=$(KUBECONFIG_VM2)"
	@echo "FLOATING_IP_VM1=$(FLOATING_IP_VM1)"
	@echo "FLOATING_IP_VM2=$(FLOATING_IP_VM2)"
	@echo "PLATFORM=$(PLATFORM)"
	@echo "WORK_MS=$(WORK_MS)"

show-tunnels:
	@echo "Open these in two separate terminals and keep them running:"
	@echo ""
	@echo "VM-1:"
	@echo "ssh -i ~/.ssh/chameleon_new -N \\"
	@echo "  -o ExitOnForwardFailure=yes \\"
	@echo "  -L 127.0.0.1:5000:127.0.0.1:5000 \\"
	@echo "  -L 127.0.0.1:6443:10.56.1.249:6443 \\"
	@echo "  cc@129.114.25.182"
	@echo ""
	@echo "VM-2:"
	@echo "ssh -i ~/.ssh/chameleon_new -N \\"
	@echo "  -o ExitOnForwardFailure=yes \\"
	@echo "  -L 127.0.0.1:5001:127.0.0.1:5000 \\"
	@echo "  -L 127.0.0.1:6444:10.56.2.149:6443 \\"
	@echo "  cc@129.114.25.80"

test-registries:
	@echo "Testing VM-1 registry tunnel from Mac host..."
	curl -fsS http://127.0.0.1:5000/v2/_catalog
	@echo ""
	@echo "Testing VM-2 registry tunnel from Mac host..."
	curl -fsS http://127.0.0.1:5001/v2/_catalog
	@echo ""
	@echo "Testing VM-1 registry from Docker Desktop..."
	docker run --rm curlimages/curl:8.9.1 -fsS http://host.docker.internal:5000/v2/_catalog
	@echo ""
	@echo "Testing VM-2 registry from Docker Desktop..."
	docker run --rm curlimages/curl:8.9.1 -fsS http://host.docker.internal:5001/v2/_catalog

# ---------- BUILD + PUSH ----------

build-push-vm1:
	@set -e; \
	for f in $(FUNCTIONS); do \
	  echo "===== Building and pushing $$f to VM-1 registry ====="; \
	  cd $$f; \
	  rm -rf .func; \
	  func build --registry=$(REGISTRY_VM1) --platform=$(PLATFORM) --builder=s2i; \
	  docker push $(REGISTRY_VM1)/$${f}:latest; \
	  cd ..; \
	done

build-push-vm2:
	@set -e; \
	for f in $(FUNCTIONS); do \
	  echo "===== Building and pushing $$f to VM-2 registry ====="; \
	  cd $$f; \
	  rm -rf .func; \
	  func build --registry=$(REGISTRY_VM2) --platform=$(PLATFORM) --builder=s2i; \
	  docker push $(REGISTRY_VM2)/$${f}:latest; \
	  cd ..; \
	done

build-push-all: build-push-vm1 build-push-vm2

# ---------- DEPLOY CHAIN ----------

deploy-vm1:
	$(MAKE) build-push-vm1
	$(MAKE) deploy-vm1-existing

deploy-vm2:
	$(MAKE) build-push-vm2
	$(MAKE) deploy-vm2-existing

deploy-vm1-existing:
	KUBECONFIG=$(KUBECONFIG_VM1) kn service apply f3 \
	  --image $(REGISTRY_VM1)/f3:latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM1) \
	  --port $(PORT)

	KUBECONFIG=$(KUBECONFIG_VM1) kn service apply f2 \
	  --image $(REGISTRY_VM1)/f2:latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM1) \
	  --env F3_URL=$(F3_INTERNAL_URL) \
	  --port $(PORT)

	KUBECONFIG=$(KUBECONFIG_VM1) kn service apply f1 \
	  --image $(REGISTRY_VM1)/f1:latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM1) \
	  --env F2_URL=$(F2_INTERNAL_URL) \
	  --port $(PORT)

deploy-vm2-existing:
	KUBECONFIG=$(KUBECONFIG_VM2) kn service apply f3 \
	  --image $(REGISTRY_VM2)/f3:latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM2) \
	  --port $(PORT)

	KUBECONFIG=$(KUBECONFIG_VM2) kn service apply f2 \
	  --image $(REGISTRY_VM2)/f2:latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM2) \
	  --env F3_URL=$(F3_INTERNAL_URL) \
	  --port $(PORT)

	KUBECONFIG=$(KUBECONFIG_VM2) kn service apply f1 \
	  --image $(REGISTRY_VM2)/f1:latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM2) \
	  --env F2_URL=$(F2_INTERNAL_URL) \
	  --port $(PORT)

# ---------- INVOKE ENTRY FUNCTION ----------

invoke-vm1:
	@URL=$$(KUBECONFIG=$(KUBECONFIG_VM1) kubectl get ksvc f1 -o jsonpath='{.status.url}'); \
	echo "Invoking $$URL"; \
	curl -s -X POST "$$URL" \
	  -H "Content-Type: application/json" \
	  -d '{"work_ms":$(WORK_MS)}' | python3 -m json.tool

invoke-vm2:
	@URL=$$(KUBECONFIG=$(KUBECONFIG_VM2) kubectl get ksvc f1 -o jsonpath='{.status.url}'); \
	echo "Invoking $$URL"; \
	curl -s -X POST "$$URL" \
	  -H "Content-Type: application/json" \
	  -d '{"work_ms":$(WORK_MS)}' | python3 -m json.tool

# ---------- CLEAN ----------

clean-vm1:
	KUBECONFIG=$(KUBECONFIG_VM1) kubectl delete ksvc f1 f2 f3 --ignore-not-found

clean-vm2:
	KUBECONFIG=$(KUBECONFIG_VM2) kubectl delete ksvc f1 f2 f3 --ignore-not-found

clean-all: clean-vm1 clean-vm2
