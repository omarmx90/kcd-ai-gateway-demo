#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="kong"
RELEASE="kong"

echo "🌍 Installing Kong Gateway in namespace ${NAMESPACE}..."
echo "🔎 Current kube context: $(kubectl config current-context)"

helm repo add kong https://charts.konghq.com >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1 || true

kubectl create namespace "${NAMESPACE}" 2>/dev/null || true

echo "➡️  Installing/Upgrading Kong via Helm (CRDs are kubectl-managed)..."
helm upgrade --install kong kong/kong \
  --namespace "${NAMESPACE}" \
  --set ingressController.enabled=true \
  --set ingressController.installCRDs=false \
  --set ingressController.ingressClass=kong \
  --set proxy.type=NodePort \
  --set proxy.http.nodePort=32080 \
  --set serviceMonitor.enabled=true \
  --set serviceMonitor.labels.release=kube-prometheus-stack \
  --set env.kong_DATABASE=off \
  --set env.kong_PROXY_ACCESS_LOG=/dev/stdout \
  --set env.kong_ADMIN_ACCESS_LOG=/dev/stdout \
  --set env.kong_PROXY_ERROR_LOG=/dev/stderr \
  --set env.kong_ADMIN_ERROR_LOG=/dev/stderr \
  --set env.kong_LOG_LEVEL=info \
  --skip-crds \
  --wait

echo "⏳ Waiting for Kong to be ready..."
kubectl -n "${NAMESPACE}" rollout status deploy/kong-kong --timeout=240s
kubectl -n "${NAMESPACE}" get pods
echo "✅ Kong installed."
