# ─────────────────────────────────────────────────────────────────────────
# TaskScheduler — build, deploy, smoke-test.
#
# Common targets:
#   make cluster            # create kind cluster (small profile)
#   make cluster PROFILE=big
#   make images             # build all 7 images
#   make load               # kind-load all images into ts-cluster
#   make install            # apply CRDs + RBAC + Deployments + DaemonSets
#   make examples           # apply tasktemplates + sample workflow
#   make smoke              # full e2e: cluster + images + install + run a workflow
#   make logs-scheduler / make logs-controller
#   make clean              # delete the kind cluster
# ─────────────────────────────────────────────────────────────────────────

CLUSTER     ?= ts-cluster
PROFILE     ?= small
NAMESPACE   ?= default
WF          ?= linear-pipeline-1
WF_FILE     ?= examples/linear-3task.yaml

# Image names + tags (must match k8s manifests and examples/tasktemplates.yaml).
CONTROL_PLANE_IMG := ts-control-plane:v1
FILESERVER_IMG    := ts-fileserver:v1
BWPROBE_IMG       := ts-bw-probe:v1
THERMAL_IMG       := ts-thermal:v1
TASK_IO_IMG       := ts-task-io:v1
TASK_MEM_IMG      := ts-task-mem:v1
TASK_CPU_IMG      := ts-task-cpu:v1

ALL_IMAGES := $(CONTROL_PLANE_IMG) $(FILESERVER_IMG) $(BWPROBE_IMG) $(THERMAL_IMG) \
              $(TASK_IO_IMG) $(TASK_MEM_IMG) $(TASK_CPU_IMG)

.PHONY: help cluster images load install uninstall examples smoke clean \
        image-control-plane image-fileserver image-bw-probe image-thermal \
        image-tasks load-images test logs-scheduler logs-controller \
        watch-workflow push install-remote k3s-server k3s-agent k3s-uninstall \
        images-multiarch verify-cluster verify-cluster-run

# ── Two-host k3s cluster (cluster.env required) ──
k3s-server:
	./scripts/k3s-server.sh

k3s-agent:
	./scripts/k3s-agent.sh

k3s-uninstall:
	./scripts/k3s-uninstall.sh

# Sanity-check a running cluster: nodes, labels, control-plane,
# DaemonSets, bandwidth + thermal annotations.
verify-cluster:
	./scripts/verify-cluster.sh

# Same as verify-cluster, but also submits linear-pipeline-1 and waits
# for it to FINISH, printing pod placement.
verify-cluster-run:
	./scripts/verify-cluster.sh --run

# Re-tag and push all 7 images to the LAN registry on pc1.
# Override REGISTRY=<host:port> to push elsewhere; defaults from cluster.env.
REGISTRY ?= $(shell . ./cluster.env 2>/dev/null && echo $$REGISTRY)
push:
	@if [ -z "$(REGISTRY)" ]; then echo "✗ REGISTRY not set (need cluster.env or REGISTRY=host:port)"; exit 2; fi
	@echo "Pushing images to $(REGISTRY)..."
	@for img in $(ALL_IMAGES); do \
		base=$${img%:*}; tag=$${img#*:}; \
		echo "  $$img -> $(REGISTRY)/$$base:$$tag"; \
		docker tag $$img $(REGISTRY)/$$base:$$tag; \
		docker push $(REGISTRY)/$$base:$$tag; \
	done

# Build all images for BOTH linux/amd64 and linux/arm64 (laptop is amd64,
# Mac mini M2 is arm64) and push to the registry in one shot. Requires
# `docker buildx create --use` once before running this.
images-multiarch:
	@if [ -z "$(REGISTRY)" ]; then echo "✗ REGISTRY not set"; exit 2; fi
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-control-plane:v1 -f images/control-plane/Dockerfile . --push
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-fileserver:v1 ./images/fileserver --push
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-bw-probe:v1 ./images/bw-probe --push
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-thermal:v1 ./images/thermal --push
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-task-io:v1  ./tasks/task_io  --push
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-task-mem:v1 ./tasks/task_mem --push
	docker buildx build --platform linux/amd64,linux/arm64 \
	    -t $(REGISTRY)/ts-task-cpu:v1 ./tasks/task_cpu --push

# Apply manifests to a remote cluster, rewriting image refs to use the
# registry prefix. Run from pc1 (kubeconfig already points at the cluster).
install-remote:
	@if [ -z "$(REGISTRY)" ]; then echo "✗ REGISTRY not set"; exit 2; fi
	kubectl kustomize --load-restrictor=LoadRestrictionsNone manifests/ \
	  | sed -E 's|image: (ts-[a-z0-9-]+:v1)|image: $(REGISTRY)/\1|g' \
	  | kubectl apply -f -
	@echo "Waiting for Deployments + DaemonSets to be Ready..."
	kubectl -n ts-system rollout status deploy/ts-controller --timeout=180s
	kubectl -n ts-system rollout status deploy/ts-scheduler  --timeout=180s
	kubectl -n ts-system rollout status ds/ts-fileserver     --timeout=180s
	kubectl -n ts-system rollout status ds/ts-bw-probe       --timeout=180s
	kubectl -n ts-system rollout status ds/ts-thermal        --timeout=180s

help:
	@echo "Targets: cluster images load install examples smoke clean test"
	@echo "         logs-scheduler logs-controller watch-workflow"
	@echo "Variables: PROFILE=$(PROFILE) NAMESPACE=$(NAMESPACE) WF=$(WF)"

# ── Cluster lifecycle ──
cluster:
	./setup_cluster.sh $(PROFILE)

clean:
	kind delete cluster --name $(CLUSTER) || true

# ── Image builds ──
images: image-control-plane image-fileserver image-bw-probe image-thermal image-tasks

image-control-plane:
	docker build -t $(CONTROL_PLANE_IMG) -f images/control-plane/Dockerfile .

image-fileserver:
	docker build -t $(FILESERVER_IMG) ./images/fileserver

image-bw-probe:
	docker build -t $(BWPROBE_IMG) ./images/bw-probe

image-thermal:
	docker build -t $(THERMAL_IMG) ./images/thermal

image-tasks:
	docker build -t $(TASK_IO_IMG)  ./tasks/task_io
	docker build -t $(TASK_MEM_IMG) ./tasks/task_mem
	docker build -t $(TASK_CPU_IMG) ./tasks/task_cpu

# ── Load all images into the kind cluster ──
load:
	@for img in $(ALL_IMAGES); do \
		echo "Loading $$img..."; \
		kind load docker-image $$img --name $(CLUSTER); \
	done

# ── Install / uninstall the platform ──
install:
	kubectl kustomize --load-restrictor=LoadRestrictionsNone manifests/ | kubectl apply -f -
	@echo "Waiting for control-plane Deployments to become Ready..."
	kubectl -n ts-system rollout status deploy/ts-controller --timeout=120s
	kubectl -n ts-system rollout status deploy/ts-scheduler  --timeout=120s
	kubectl -n ts-system rollout status ds/ts-fileserver     --timeout=120s
	kubectl -n ts-system rollout status ds/ts-bw-probe       --timeout=120s
	kubectl -n ts-system rollout status ds/ts-thermal        --timeout=120s

uninstall:
	kubectl kustomize --load-restrictor=LoadRestrictionsNone manifests/ | kubectl delete --ignore-not-found -f -

# ── Examples + smoke ──
examples:
	kubectl apply -f examples/tasktemplates.yaml
	kubectl apply -f $(WF_FILE)

smoke: images load install examples
	@echo ""
	@echo "=== Smoke test: watching workflow '$(WF)' ==="
	@$(MAKE) watch-workflow WF=$(WF)

watch-workflow:
	@echo "Waiting for workflow '$(WF)' to reach FINISHED (Ctrl-C to abort)..."
	@for i in $$(seq 1 120); do \
		STATE=$$(kubectl -n $(NAMESPACE) get workflow $(WF) -o jsonpath='{.status.state}' 2>/dev/null); \
		FIN=$$(kubectl -n $(NAMESPACE) get workflow $(WF) -o jsonpath='{.status.tasksFinished}' 2>/dev/null); \
		TOT=$$(kubectl -n $(NAMESPACE) get workflow $(WF) -o jsonpath='{.status.tasksTotal}' 2>/dev/null); \
		echo "  [t=$$i] state=$$STATE  $$FIN/$$TOT"; \
		case "$$STATE" in \
		  FINISHED) echo "✓ Workflow $(WF) FINISHED"; \
		            kubectl -n $(NAMESPACE) get workflow $(WF) -o yaml | sed -n '/status:/,$$p'; \
		            exit 0;; \
		  FAILED)   echo "✗ Workflow $(WF) FAILED"; \
		            kubectl -n $(NAMESPACE) get workflow $(WF) -o yaml | sed -n '/status:/,$$p'; \
		            exit 1;; \
		esac; \
		sleep 5; \
	done; \
	echo "✗ Timed out after 10 minutes"; exit 1

# ── Diagnostics ──
logs-scheduler:
	kubectl -n ts-system logs -l app.kubernetes.io/name=ts-scheduler --tail=200 -f

logs-controller:
	kubectl -n ts-system logs -l app.kubernetes.io/name=ts-controller --tail=200 -f

# ── Simulation tests (unchanged path) ──
test:
	python3 test_simulation.py
