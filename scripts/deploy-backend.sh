#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="ai-gateway-demo"

echo "🚀 Deploying AI backend into namespace ${NAMESPACE}..."

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/deploy-backend.yaml
kubectl apply -f k8s/service-backend.yaml
kubectl apply -f k8s/ingress-gateway.yaml
kubectl apply -f k8s/hpa-backend.yaml

echo "✅ Backend and Ingress applied."
