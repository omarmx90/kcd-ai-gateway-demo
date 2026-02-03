CLUSTER_NAME = ai-gateway-cluster
IMAGE_NAME   = ai-backend
NAMESPACE    = ai-gateway-demo

.PHONY: run destroy build cluster load-image kong-crds kong deploy observability status load dashboards verify

run: build cluster load-image kong-crds kong deploy observability dashboards status verify
	@echo "✅ AI Gateway demo is up. Try:"
	@echo "   curl -X POST -H 'Host: ai-gateway.local' -H 'Content-Type: application/json' \\"
	@echo "        -d '{\"text\":\"AI Gateways add governance to LLM workloads.\",\"max_words\":20}' \\"
	@echo "        http://localhost:8080/ai/summarize"

destroy:
	@echo "🧨 Deleting kind cluster $(CLUSTER_NAME)..."
	-kind delete cluster --name $(CLUSTER_NAME)
	@echo "✅ Destroy complete."

build:
	@echo "🐳 Building Docker image $(IMAGE_NAME):latest..."
	docker build -t $(IMAGE_NAME):latest ./app

cluster:
	@./scripts/create-cluster.sh

load-image:
	@echo "📦 Loading image into kind cluster..."
	kind load docker-image $(IMAGE_NAME):latest --name $(CLUSTER_NAME)

# 1) Install Kong CRDs via kubectl (no Helm ownership issues)
kong-crds:
	@./scripts/install-kong-crds.sh

# 2) Install Kong via Helm (installCRDs=false inside the script)
kong:
	@./scripts/install-kong.sh

# 3) Deploy your AI backend + ingress + HPA
deploy:
	@./scripts/deploy-backend.sh

observability:
	@./scripts/install-observability.sh
	@kubectl apply -f k8s/kong-prometheus-plugin.yaml
	@kubectl apply -f k8s/servicemonitor-backend.yaml

dashboards:
	@./scripts/install-grafana-dashboards.sh

status:
	@echo "🌐 Services in $(NAMESPACE):"
	@kubectl get pods,svc,ingress -n $(NAMESPACE) || true
	@echo "🌐 Kong pods:"
	@kubectl get pods -n kong || true
	@echo "📈 Monitoring pods:"
	@kubectl get pods -n monitoring || true

verify:
	@echo "🔎 Verifying gateway route..."
	@curl -s -o /dev/null -w "HTTP %{http_code}\n" \
		-X POST "http://localhost:8080/ai/summarize" \
		-H "Host: ai-gateway.local" \
		-H "Content-Type: application/json" \
		-d '{"text":"AI Gateways add governance to LLM workloads.","max_words":20}' || true

load:
	@echo "🔥 Generating load against AI Gateway (this should trigger HPA scaling)..."
	hey -z 60s -q 5 -c 10 \
		-H "Host: ai-gateway.local" \
		-m POST \
		-T "application/json" \
		-d '{"text":"AI Gateways add governance to LLM workloads and this is a long text to keep CPU busy.","max_words":20}' \
		http://localhost:8080/ai/summarize
	@echo "✅ Load test finished. Check HPA and pods:"
	@kubectl get hpa -n $(NAMESPACE) || true
	@kubectl get pods -n $(NAMESPACE) || true
 
