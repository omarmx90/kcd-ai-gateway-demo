#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="kong"

echo "🌍 Installing Kong Gateway in namespace ${NAMESPACE}..."

helm repo add kong https://charts.konghq.com >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1 || true

kubectl create namespace "${NAMESPACE}" 2>/dev/null || true

helm upgrade --install kong kong/kong \
  --namespace "${NAMESPACE}" \
  --set ingressController.enabled=true \
  --set ingressController.installCRDs=false \
  --set ingressController.ingressClass=kong \
  --set proxy.type=NodePort \
  --set proxy.http.nodePort=32080 \
  --set env.kong_DATABASE=off \
  --set env.kong_PROXY_ACCESS_LOG=/dev/stdout \
  --set env.kong_ADMIN_ACCESS_LOG=/dev/stdout \
  --set env.kong_PROXY_ERROR_LOG=/dev/stderr \
  --set env.kong_ADMIN_ERROR_LOG=/dev/stderr \
  --set env.kong_LOG_LEVEL=info \
  --skip-crds

echo "✅ Kong installed (Ingress Controller enabled, CRDs skipped)."
