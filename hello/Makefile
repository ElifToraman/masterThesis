# Thesis serverless deploy targets for the hello function
# Two VMs, each running its own Knative cluster and local registry.
# Stable registry names are used through Docker Desktop host.docker.internal.

FUNC_NAME ?= hello
PLATFORM  ?= linux/amd64
PORT      ?= 8080

REGISTRY_VM1 ?= host.docker.internal:5000/elif
REGISTRY_VM2 ?= host.docker.internal:5001/elif

KUBECONFIG_VM1 ?= $(HOME)/.kube/vm1-config
KUBECONFIG_VM2 ?= $(HOME)/.kube/vm2-config

FLOATING_IP_VM1 ?= 129.114.25.182
FLOATING_IP_VM2 ?= 129.114.25.80

.PHONY: help \
        show-tunnels test-registries check \
        prepare-vm1 prepare-vm2 \
        build-push-vm1 build-push-vm2 build-push-all \
        deploy-vm1 deploy-vm2 \
        deploy-vm1-existing deploy-vm2-existing \
        url-vm1 url-vm2 \
        invoke-vm1 invoke-vm2 \
        logs-vm1 logs-vm2 \
        clean-vm1 clean-vm2 clean-all

help:
	@echo "Targets:"
	@echo "  check                         print configuration"
	@echo "  show-tunnels                  print required SSH tunnels"
	@echo "  test-registries               test registry tunnels"
	@echo "  build-push-vm1 / build-push-vm2"
	@echo "  build-push-all                build and push hello to both VM registries"
	@echo "  deploy-vm1 / deploy-vm2       build, push, and deploy to that VM"
	@echo "  deploy-vm1-existing / deploy-vm2-existing"
	@echo "  url-vm1 / url-vm2             print the function URL on that VM"
	@echo "  invoke-vm1 / invoke-vm2       curl the function on that VM"
	@echo "  logs-vm1 / logs-vm2           show pod logs from that VM"
	@echo "  clean-vm1 / clean-vm2         delete the Knative service"

check:
	@echo "FUNC_NAME=$(FUNC_NAME)"
	@echo "REGISTRY_VM1=$(REGISTRY_VM1)"
	@echo "REGISTRY_VM2=$(REGISTRY_VM2)"
	@echo "KUBECONFIG_VM1=$(KUBECONFIG_VM1)"
	@echo "KUBECONFIG_VM2=$(KUBECONFIG_VM2)"
	@echo "FLOATING_IP_VM1=$(FLOATING_IP_VM1)"
	@echo "FLOATING_IP_VM2=$(FLOATING_IP_VM2)"
	@echo "PLATFORM=$(PLATFORM)"
	@echo "PORT=$(PORT)"

show-tunnels:
	@echo "Open these in two separate MacBook terminals:"
	@echo
	@echo "VM-1 tunnel:"
	@echo "ssh -i ~/.ssh/chameleon_new -N \\"
	@echo "  -o ExitOnForwardFailure=yes \\"
	@echo "  -L 127.0.0.1:5000:127.0.0.1:5000 \\"
	@echo "  -L 127.0.0.1:6443:10.56.1.249:6443 \\"
	@echo "  cc@129.114.25.182"
	@echo
	@echo "VM-2 tunnel:"
	@echo "ssh -i ~/.ssh/chameleon_new -N \\"
	@echo "  -o ExitOnForwardFailure=yes \\"
	@echo "  -L 127.0.0.1:5001:127.0.0.1:5000 \\"
	@echo "  -L 127.0.0.1:6444:10.56.2.149:6443 \\"
	@echo "  cc@129.114.25.80"

test-registries:
	@echo "Testing VM-1 registry tunnel from Mac host..."
	curl -fsS http://127.0.0.1:5000/v2/_catalog
	@echo
	@echo "Testing VM-2 registry tunnel from Mac host..."
	curl -fsS http://127.0.0.1:5001/v2/_catalog
	@echo
	@echo "Testing VM-1 registry from Docker Desktop..."
	docker run --rm curlimages/curl:8.9.1 -fsS http://host.docker.internal:5000/v2/_catalog
	@echo
	@echo "Testing VM-2 registry from Docker Desktop..."
	docker run --rm curlimages/curl:8.9.1 -fsS http://host.docker.internal:5001/v2/_catalog
	@echo

# ---------- PREPARE FUNC.YAML ----------
# func build reads func.yaml, so these targets set the image metadata before building.
# The file is local project metadata. It may end up pointing to the last VM you built for.
# Deployment targets below still pass the image explicitly with kn service apply.

prepare-vm1:
	@echo "Preparing func.yaml for VM-1..."
	@sed -i '' 's|^registry: .*|registry: $(REGISTRY_VM1)|' func.yaml
	@sed -i '' 's|^image: .*|image: $(REGISTRY_VM1)/$(FUNC_NAME):latest|' func.yaml
	@sed -i '' 's|^  image: .*|  image: $(REGISTRY_VM1)/$(FUNC_NAME):latest|' func.yaml
	@sed -i '' '/name: VM_FLOATING_IP/{n;s|value: .*|value: $(FLOATING_IP_VM1)|;}' func.yaml
	@grep -nE "registry|image|VM_FLOATING_IP|value" func.yaml

prepare-vm2:
	@echo "Preparing func.yaml for VM-2..."
	@sed -i '' 's|^registry: .*|registry: $(REGISTRY_VM2)|' func.yaml
	@sed -i '' 's|^image: .*|image: $(REGISTRY_VM2)/$(FUNC_NAME):latest|' func.yaml
	@sed -i '' 's|^  image: .*|  image: $(REGISTRY_VM2)/$(FUNC_NAME):latest|' func.yaml
	@sed -i '' '/name: VM_FLOATING_IP/{n;s|value: .*|value: $(FLOATING_IP_VM2)|;}' func.yaml
	@grep -nE "registry|image|VM_FLOATING_IP|value" func.yaml

# ---------- BUILD + PUSH ----------
build-push-vm1: prepare-vm1
	rm -rf .func
	func build \
	  --registry=$(REGISTRY_VM1) \
	  --platform=$(PLATFORM) \
	  --builder=s2i
	docker push $(REGISTRY_VM1)/$(FUNC_NAME):latest

build-push-vm2: prepare-vm2
	rm -rf .func
	func build \
	  --registry=$(REGISTRY_VM2) \
	  --platform=$(PLATFORM) \
	  --builder=s2i
	docker push $(REGISTRY_VM2)/$(FUNC_NAME):latest

build-push-all: build-push-vm1
	docker tag $(REGISTRY_VM1)/$(FUNC_NAME):latest $(REGISTRY_VM2)/$(FUNC_NAME):latest
	docker push $(REGISTRY_VM2)/$(FUNC_NAME):latest

# ---------- DEPLOY ----------
deploy-vm1: build-push-vm1 deploy-vm1-existing

deploy-vm2: build-push-vm2 deploy-vm2-existing

deploy-vm1-existing:
	KUBECONFIG=$(KUBECONFIG_VM1) kn service apply $(FUNC_NAME) \
	  --image $(REGISTRY_VM1)/$(FUNC_NAME):latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM1) \
	  --port $(PORT)
	KUBECONFIG=$(KUBECONFIG_VM1) kubectl wait \
	  --for=condition=Ready ksvc/$(FUNC_NAME) \
	  --timeout=180s

deploy-vm2-existing:
	KUBECONFIG=$(KUBECONFIG_VM2) kn service apply $(FUNC_NAME) \
	  --image $(REGISTRY_VM2)/$(FUNC_NAME):latest \
	  --env VM_FLOATING_IP=$(FLOATING_IP_VM2) \
	  --port $(PORT)
	KUBECONFIG=$(KUBECONFIG_VM2) kubectl wait \
	  --for=condition=Ready ksvc/$(FUNC_NAME) \
	  --timeout=180s

# ---------- INSPECT ----------
url-vm1:
	@KUBECONFIG=$(KUBECONFIG_VM1) kubectl get ksvc $(FUNC_NAME) \
	  -o jsonpath='{.status.url}' && echo

url-vm2:
	@KUBECONFIG=$(KUBECONFIG_VM2) kubectl get ksvc $(FUNC_NAME) \
	  -o jsonpath='{.status.url}' && echo

# ---------- CALL ----------
invoke-vm1:
	@curl -s "$$(KUBECONFIG=$(KUBECONFIG_VM1) kubectl get ksvc $(FUNC_NAME) -o jsonpath='{.status.url}')" && echo

invoke-vm2:
	@curl -s "$$(KUBECONFIG=$(KUBECONFIG_VM2) kubectl get ksvc $(FUNC_NAME) -o jsonpath='{.status.url}')" && echo

# ---------- LOGS ----------
logs-vm1:
	KUBECONFIG=$(KUBECONFIG_VM1) kubectl logs \
	  -l serving.knative.dev/service=$(FUNC_NAME) \
	  -c user-container \
	  --tail=50

logs-vm2:
	KUBECONFIG=$(KUBECONFIG_VM2) kubectl logs \
	  -l serving.knative.dev/service=$(FUNC_NAME) \
	  -c user-container \
	  --tail=50

# ---------- CLEAN ----------
clean-vm1:
	KUBECONFIG=$(KUBECONFIG_VM1) kubectl delete ksvc $(FUNC_NAME) --ignore-not-found

clean-vm2:
	KUBECONFIG=$(KUBECONFIG_VM2) kubectl delete ksvc $(FUNC_NAME) --ignore-not-found

clean-all: clean-vm1 clean-vm2
