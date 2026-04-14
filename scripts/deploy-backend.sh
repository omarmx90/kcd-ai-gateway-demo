#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="ai-gateway-demo"

echo "Deploying AI backend into namespace ${NAMESPACE}..."

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/deploy-backend.yaml
kubectl apply -f k8s/service-backend.yaml

kubectl apply -f k8s/kong-pii-sanitizer-plugin.yaml
kubectl apply -f k8s/ingress-gateway.yaml
kubectl apply -f k8s/hpa-backend.yaml

echo "Backend + policy + Ingress applied."
echo "Tip: OpenAI + Gemini — export OPENAI_API_KEY / GOOGLE_API_KEY, run ./scripts/create-llm-secret-from-env.sh (or make llm-secret), then kubectl rollout restart deployment/ai-backend -n ${NAMESPACE}"
