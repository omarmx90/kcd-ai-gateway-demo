CLUSTER_NAME = ai-gateway-cluster
IMAGE_NAME   = ai-backend
NAMESPACE    = ai-gateway-demo

.PHONY: help run destroy build cluster load-image kong-crds kong deploy observability dashboards status verify verify-metrics load grafana-pass llm-secret demo-scenarios keynote-cloud

help:
	@echo "KCD AI Gateway demo - Makefile targets"
	@echo ""
	@echo "  make help             This list"
	@echo "  make run              Image, kind, Kong, app, Prometheus/Grafana, verify"
	@echo "  make verify           Health, summarize, metrics, /chat, PII, Grafana"
	@echo "  make verify-metrics   ServiceMonitor + port http + ai_* on pod /metrics"
	@echo "  make status           Pods/SVC/Ingress (app, kong, monitoring)"
	@echo "  make dashboards       Re-apply Grafana dashboard ConfigMaps"
	@echo "  make observability    metrics-server + kube-prometheus-stack + ServiceMonitor"
	@echo "  make deploy           Backend, Kong plugins, ingress, HPA"
	@echo "  make load             hey load test (HPA)"
	@echo "  make demo-scenarios   scripts/demo-five-scenarios.sh"
	@echo "  make keynote-cloud    scripts/demo-keynote-two-cloud-calls.sh"
	@echo "  make llm-secret       Create ai-llm-credentials from env"
	@echo "  make grafana-pass     Print Grafana admin password"
	@echo "  make destroy          kind delete cluster"
	@echo ""
	@echo "Low-level: build, cluster, load-image, kong-crds, kong"

run: build cluster load-image kong-crds kong deploy observability dashboards status verify
	@echo "AI Gateway demo is up."
	@echo "Try:"
	@echo "  curl -X POST -H 'Host: ai-gateway.local' -H 'X-Application-Id: my-app' -H 'Content-Type: application/json' \\"
	@echo "       -d '{\"text\":\"AI Gateways add governance to LLM workloads.\",\"max_words\":20}' \\"
	@echo "       http://localhost:8080/ai/summarize"
	@echo ""
	@echo "Chat UI: http://ai-gateway.local:8080/chat (hosts: ai-gateway.local -> 127.0.0.1)"
	@echo "Grafana: http://localhost:8081 (user: admin)"
	@echo "Grafana password:"
	@echo "  kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo"

destroy:
	@echo "Deleting kind cluster $(CLUSTER_NAME)..."
	-kind delete cluster --name $(CLUSTER_NAME)
	@echo "Destroy complete."

build:
	@echo "Building Docker image $(IMAGE_NAME):latest..."
	docker build -t $(IMAGE_NAME):latest ./app

cluster:
	@./scripts/create-cluster.sh

load-image:
	@echo "Loading image into kind cluster..."
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
	@echo "Services in $(NAMESPACE):"
	@kubectl get pods,svc,ingress -n $(NAMESPACE)
	@echo ""
	@echo "Kong:"
	@kubectl get pods,svc -n kong || true
	@echo ""
	@echo "Monitoring:"
	@kubectl get pods,svc -n monitoring || true

grafana-pass:
	@kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

llm-secret:
	@./scripts/create-llm-secret-from-env.sh

demo-scenarios:
	@chmod +x scripts/demo-five-scenarios.sh 2>/dev/null || true
	@./scripts/demo-five-scenarios.sh

keynote-cloud:
	@chmod +x scripts/demo-keynote-two-cloud-calls.sh 2>/dev/null || true
	@./scripts/demo-keynote-two-cloud-calls.sh

verify-metrics:
	@chmod +x scripts/verify-backend-metrics.sh 2>/dev/null || true
	@./scripts/verify-backend-metrics.sh

verify:
	@echo "Verifying cluster and demo components..."
	@kubectl config current-context | grep -q "kind-$(CLUSTER_NAME)" || (echo "Not using kind context"; exit 1)
	@kubectl get ns $(NAMESPACE) >/dev/null
	@kubectl get pods -n kong | grep -q "Running" || (echo "Kong not running"; exit 1)
	@kubectl get pods -n $(NAMESPACE) | grep -q "Running" || (echo "Backend not running"; exit 1)
	@kubectl get ingress -n $(NAMESPACE) ai-gateway >/dev/null
	@echo "Waiting for gateway /health (HTTP 200)..."
	@tries=0; until [ $$tries -ge 45 ]; do code=$$(curl -s -o /dev/null -w "%{http_code}" -H "Host: ai-gateway.local" -H "X-Application-Id: make-verify" http://localhost:8080/health || true); echo "   /health HTTP $$code"; [ "$$code" = "200" ] && break; tries=$$((tries+1)); sleep 2; done; [ $$tries -lt 45 ] || (echo "Gateway /health failed"; exit 1)
	@echo "Checking summarize (HTTP 200)..."
	@curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/ai/summarize \
		-H "Host: ai-gateway.local" \
		-H "X-Application-Id: make-verify" \
		-H "Content-Type: application/json" \
		-d '{"text":"verify","max_words":10}' | grep -q "200" || (echo "Gateway /ai/summarize failed"; exit 1)
	@echo "Checking /metrics (HTTP 200)..."
	@curl -s -o /dev/null -w "%{http_code}\n" -H "Host: ai-gateway.local" -H "X-Application-Id: make-verify" http://localhost:8080/metrics | grep -q "200" || (echo "/metrics failed"; exit 1)
	@echo "Checking /chat (HTTP 200)..."
	@curl -s -o /dev/null -w "%{http_code}\n" -H "Host: ai-gateway.local" -H "X-Application-Id: make-verify" http://localhost:8080/chat | grep -q "200" || (echo "/chat failed"; exit 1)
	@echo "Verifying PII (email redacted)..."
	@curl -s "http://localhost:8080/ai/summarize" \
		-H "Host: ai-gateway.local" \
		-H "X-Application-Id: make-verify" \
		-H "Content-Type: application/json" \
		-d '{"text":"Contact me at john.doe@example.com ASAP.","max_words":20}' \
		| tee /tmp/ai_out.json >/dev/null || true
	@echo "   Output:"
	@cat /tmp/ai_out.json || true
	@echo ""
	@echo "   Checking [REDACTED_EMAIL]:"
	@cat /tmp/ai_out.json | grep -q "\[REDACTED_EMAIL\]" && echo "PII redaction OK" || echo "No redaction (check Kong plugin)"
	@echo "Checking /metrics inside pod..."
	@kubectl -n $(NAMESPACE) get svc ai-backend -o jsonpath='{.spec.ports[0].port}' >/dev/null 2>&1 && echo "ai-backend service OK" || true
	@kubectl -n $(NAMESPACE) exec deploy/ai-backend -- sh -c "python -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8000/metrics\").read(2000).decode())'" \
		| grep -E "ai_requests_total|ai_pii_redactions_total|ai_moderation_decisions_total|ai_llm_failover_total|ai_requests_per_application_total|ai_estimated_request_cost_usd|ai_estimated_cost_microdollars_total|ai_estimated_input_tokens_total|ai_estimated_output_tokens_total" >/dev/null \
		&& echo "Metric names present" || echo "Some metric names missing in /metrics"
	@echo "Waiting for Grafana..."
	@tries=0; until [ $$tries -ge 30 ]; do code=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081 || true); echo "   Grafana HTTP $$code"; echo $$code | egrep -q "200|302" && break; tries=$$((tries+1)); sleep 4; done; [ $$tries -lt 30 ] || (echo "Grafana not reachable"; exit 1)
	@curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8081 | egrep -q "200|302" || (echo "Grafana not reachable"; exit 1)
	@echo "Verify complete."

load:
	@echo "Generating load (HPA)..."
	@hey -z 60s -c 60 -q 60 \
	  -host ai-gateway.local \
	  -H "X-Application-Id: load-test" \
	  -m POST \
	  -T "application/json" \
	  -d '{"text":"autoscaling demo","max_words":20,"cpu_burn_ms":300}' \
	  http://localhost:8080/ai/summarize
	@echo ""
	@echo "HPA status:"
	@kubectl get hpa -n $(NAMESPACE)
	@echo ""
	@echo "Pods:"
	@kubectl get pods -n $(NAMESPACE)
