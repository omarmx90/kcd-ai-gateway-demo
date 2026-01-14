CLUSTER_NAME = ai-gateway-cluster
IMAGE_NAME   = ai-backend
NAMESPACE    = ai-gateway-demo

.PHONY: run destroy build cluster load-image deploy kong observability status load


run: build cluster load-image deploy kong observability status
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

deploy:
	@./scripts/deploy-backend.sh

kong:
	@./scripts/install-kong.sh

observability:
	@./scripts/install-observability.sh

status:
	@echo "🌐 Services in $(NAMESPACE):"
	@kubectl get pods,svc,ingress -n $(NAMESPACE)

load:
	@echo "🔥 Generating load against AI Gateway (this should trigger HPA scaling)..."
	hey -z 60s -q 5 -c 10 \
	  -H "Host: ai-gateway.local" \
	  -m POST \
	  -T "application/json" \
	  -d '{"text":"AI Gateways add governance to LLM workloads and this is a long text to keep CPU busy.","max_words":20}' \
http://localhost:8080/ai/summarize
	@echo "✅ Load test finished. Check HPA and pods:"
	@kubectl get hpa -n ai-gateway-demo
	@kubectl get pods -n ai-gateway-demo
 