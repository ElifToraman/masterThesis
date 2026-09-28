# Shared build/push targets for independent benchmark functions.

PLATFORM ?= linux/amd64
IMAGE_TAG ?= v1
PYTHON ?= python3

REGISTRY_VM1 ?= host.docker.internal:5000/elif
REGISTRY_VM2 ?= host.docker.internal:5001/elif

KUBECONFIG_VM1 ?= $(HOME)/.kube/vm1-config
KUBECONFIG_VM2 ?= $(HOME)/.kube/vm2-config

IMAGE_VM1 := $(REGISTRY_VM1)/$(FUNC_NAME):$(IMAGE_TAG)
IMAGE_VM2 := $(REGISTRY_VM2)/$(FUNC_NAME):$(IMAGE_TAG)

.PHONY: check test build-vm1 build-push-vm1 build-push-all \
        verify-images clean-vm1 clean-vm2 clean-all

check:
	@echo "FUNC_NAME=$(FUNC_NAME)"
	@echo "IMAGE_VM1=$(IMAGE_VM1)"
	@echo "IMAGE_VM2=$(IMAGE_VM2)"
	@echo "PLATFORM=$(PLATFORM)"

test:
	$(PYTHON) -m pytest -q

build-vm1:
	func build \
	  --path=. \
	  --image=$(IMAGE_VM1) \
	  --platform=$(PLATFORM) \
	  --builder=s2i

build-push-vm1: build-vm1
	docker push $(IMAGE_VM1)

build-push-all: build-push-vm1
	docker tag $(IMAGE_VM1) $(IMAGE_VM2)
	docker push $(IMAGE_VM2)

verify-images:
	curl -fsS http://127.0.0.1:5000/v2/elif/$(FUNC_NAME)/tags/list
	@echo
	curl -fsS http://127.0.0.1:5001/v2/elif/$(FUNC_NAME)/tags/list
	@echo

clean-vm1:
	KUBECONFIG=$(KUBECONFIG_VM1) kubectl delete ksvc $(FUNC_NAME) \
	  --namespace=default --ignore-not-found

clean-vm2:
	KUBECONFIG=$(KUBECONFIG_VM2) kubectl delete ksvc $(FUNC_NAME) \
	  --namespace=default --ignore-not-found

clean-all: clean-vm1 clean-vm2
