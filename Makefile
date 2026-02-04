CLUSTER_NAME = ai-gateway-cluster
IMAGE_NAME   = ai-backend
NAMESPACE    = ai-gateway-demo

.PHONY: run destroy build cluster load-image kong-crds kong deploy observability dashboards status verify load grafana-pass

run: build cluster load-image kong-crds kong deploy observability dashboards status verify
	@echo "✅ AI Gateway demo is up."
	@echo "Try:"
	@echo "  curl -X POST -H 'Host: ai-gateway.local' -H 'Content-Type: application/json' \\"
	@echo "       -d '{\"text\":\"AI Gateways add governance to LLM workloads.\",\"max_words\":20}' \\"
	@echo "       http://localhost:8080/ai/summarize"
	@echo ""
	@echo "Grafana: http://localhost:8081 (user: admin)"
	@echo "Grafana password:"
	@echo "  kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo"

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

kong-crds:
	@./scripts/install-kong-crds.sh

kong:
	@./scripts/install-kong.sh

deploy:
	@./scripts/deploy-backend.sh

observability:
	@./scripts/install-observability.sh

dashboards:
	@./scripts/install-grafana-dashboards.sh

status:
	@echo "🌐 Services in $(NAMESPACE):"
	@kubectl get pods,svc,ingress -n $(NAMESPACE)
	@echo ""
	@echo "🦍 Kong:"
	@kubectl get pods,svc -n kong || true
	@echo ""
	@echo "📈 Monitoring:"
	@kubectl get pods,svc -n monitoring || true

grafana-pass:
	@kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

verify:
	@echo "🔎 Verifying cluster and demo components..."
	@kubectl config current-context | grep -q "kind-$(CLUSTER_NAME)" || (echo "❌ Not using kind context"; exit 1)
	@kubectl get ns $(NAMESPACE) >/dev/null
	@kubectl get pods -n kong | grep -q "Running" || (echo "❌ Kong not running"; exit 1)
	@kubectl get pods -n $(NAMESPACE) | grep -q "Running" || (echo "❌ Backend not running"; exit 1)
	@kubectl get ingress -n $(NAMESPACE) ai-gateway >/dev/null
	@echo "➡️  Checking gateway route (expect HTTP 200)..."
	@curl -s -o /dev/null -w "%{http_code}\n" -H "Host: ai-gateway.local" http://localhost:8080/health | grep -q "200" || (echo "❌ Gateway /health failed"; exit 1)
	@echo "➡️  Checking summarize endpoint (expect HTTP 200)..."
	@curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/ai/summarize \
		-H "Host: ai-gateway.local" \
		-H "Content-Type: application/json" \
		-d '{"text":"verify","max_words":10}' | grep -q "200" || (echo "❌ Gateway /ai/summarize failed"; exit 1)
	@echo "➡️  Checking metrics endpoint (expect HTTP 200)..."
	@curl -s -o /dev/null -w "%{http_code}\n" -H "Host: ai-gateway.local" http://localhost:8080/metrics | grep -q "200" || (echo "❌ /metrics failed"; exit 1)
	@echo "➡️  Checking Grafana (expect HTTP 200/302)..."
	@curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8081 | egrep -q "200|302" || (echo "❌ Grafana not reachable"; exit 1)
	@echo "✅ Verify complete."

load:
	@echo "🔥 Generating load against AI Gateway (should trigger HPA scaling)..."
	@hey -z 60s -q 10 -c 20 \
	  -H "Host: ai-gateway.local" \
	  -m POST \
	  -T "application/json" \
	  -d '{"text":"Load test - autoscaling demo.","max_words":20,"cpu_burn_ms":40}' \
	  http://localhost:8080/ai/summarize
	@echo "✅ Load test finished. Check HPA and pods:"
	@kubectl get hpa -n $(NAMESPACE) || true
	@kubectl get pods -n $(NAMESPACE) || true
