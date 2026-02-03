CLUSTER_NAME = ai-gateway-cluster
IMAGE_NAME   = ai-backend
NAMESPACE    = ai-gateway-demo

KONG_NS      = kong
MON_NS       = monitoring

GATEWAY_URL  = http://localhost:8080
GRAFANA_URL  = http://localhost:8081

.PHONY: run destroy build cluster load-image kong-crds kong deploy observability dashboards status load verify

run: build cluster load-image kong-crds kong deploy observability dashboards status verify
	@echo "✅ AI Gateway demo is up."
	@echo "   Test:"
	@echo "   curl -s -X POST $(GATEWAY_URL)/ai/summarize \\"
	@echo "     -H 'Host: ai-gateway.local' -H 'Content-Type: application/json' \\"
	@echo "     -d '{\"text\":\"AI Gateways add governance to LLM workloads.\",\"max_words\":20}' | jq"

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
	@kubectl get pods,svc,ingress -n $(NAMESPACE) || true
	@echo ""
	@echo "🦍 Kong:"
	@kubectl get pods,svc -n $(KONG_NS) || true
	@echo ""
	@echo "📊 Monitoring:"
	@kubectl get pods,svc -n $(MON_NS) || true

load:
	@echo "🔥 Generating load against AI Gateway (should trigger HPA scaling)..."
	@command -v hey >/dev/null 2>&1 || (echo "❌ 'hey' not found. Install it (brew install hey)"; exit 1)
	hey -z 60s -q 5 -c 10 \
	  -H "Host: ai-gateway.local" \
	  -m POST \
	  -T "application/json" \
	  -d '{"text":"AI Gateways add governance to LLM workloads and this is a long text to keep CPU busy.","max_words":20}' \
	  $(GATEWAY_URL)/ai/summarize
	@echo "✅ Load test finished. Check HPA and pods:"
	@kubectl get hpa -n $(NAMESPACE) || true
	@kubectl get pods -n $(NAMESPACE) || true

verify:
	@set -e; \
	echo "🧪 VERIFY: Starting checks..."; \
	echo ""; \
	\
	echo "1) ✅ kubectl context must be kind-$(CLUSTER_NAME)"; \
	ctx=$$(kubectl config current-context); \
	echo "   current-context=$$ctx"; \
	echo "$$ctx" | grep -q "kind-$(CLUSTER_NAME)" || (echo "❌ Wrong context. Expected kind-$(CLUSTER_NAME)"; exit 1); \
	echo ""; \
	\
	echo "2) ✅ Namespaces exist"; \
	kubectl get ns $(NAMESPACE) >/dev/null; \
	kubectl get ns $(KONG_NS) >/dev/null; \
	kubectl get ns $(MON_NS) >/dev/null; \
	echo "   ok"; \
	echo ""; \
	\
	echo "3) ✅ Backend pod Ready"; \
	kubectl wait --for=condition=Ready pod -l app=ai-backend -n $(NAMESPACE) --timeout=180s >/dev/null; \
	echo "   ok"; \
	echo ""; \
	\
	echo "4) ✅ Kong pod Ready"; \
	kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=kong -n $(KONG_NS) --timeout=240s >/dev/null; \
	echo "   ok"; \
	echo ""; \
	\
	echo "5) ✅ Ingress exists + host rule"; \
	kubectl get ingress ai-gateway -n $(NAMESPACE) >/dev/null; \
	host=$$(kubectl get ingress ai-gateway -n $(NAMESPACE) -o jsonpath='{.spec.rules[0].host}'); \
	echo "   ingress.host=$$host"; \
	test "$$host" = "ai-gateway.local" || (echo "❌ Ingress host is $$host (expected ai-gateway.local)"; exit 1); \
	echo ""; \
	\
	echo "6) ✅ Gateway route returns HTTP 200"; \
	code=$$(curl -s -o /dev/null -w "%{http_code}" -X POST $(GATEWAY_URL)/ai/summarize \
	  -H "Host: ai-gateway.local" -H "Content-Type: application/json" \
	  -d '{"text":"verify call","max_words":10}'); \
	echo "   status=$$code"; \
	test "$$code" = "200" || (echo "❌ Gateway returned $$code (expected 200). Check ingress/kong."; exit 1); \
	echo ""; \
	\
	echo "7) ✅ Grafana reachable on $(GRAFANA_URL)"; \
	gcode=$$(curl -s -o /dev/null -w "%{http_code}" $(GRAFANA_URL)/login); \
	echo "   status=$$gcode"; \
	test "$$gcode" = "200" || test "$$gcode" = "302" || (echo "❌ Grafana not reachable (HTTP $$gcode)"; exit 1); \
	echo ""; \
	\
	echo "8) ✅ metrics-server working (kubectl top)"; \
	kubectl top nodes >/dev/null 2>&1 || (echo "❌ kubectl top nodes failed. metrics-server not ready."; exit 1); \
	echo "   ok"; \
	echo ""; \
	\
	echo "9) ✅ HPA has metrics (not <unknown>)"; \
	hpa_line=$$(kubectl get hpa -n $(NAMESPACE) ai-backend-hpa --no-headers 2>/dev/null || true); \
	echo "   $$hpa_line"; \
	echo "$$hpa_line" | grep -q "<unknown>" && (echo "❌ HPA metrics are <unknown>. Add CPU requests/limits + wait metrics-server."; exit 1) || true; \
	echo ""; \
	\
	echo "10) ✅ (Best-effort) Prometheus service exists"; \
	kubectl get svc -n $(MON_NS) kube-prometheus-stack-prometheus >/dev/null 2>&1 && echo "   prometheus svc ok" || echo "   (skipped) prometheus svc not found"; \
	echo ""; \
	\
	echo "✅ VERIFY: All required checks passed."
