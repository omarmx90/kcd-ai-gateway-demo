#!/usr/bin/env bash
set -euo pipefail

echo "📊 Preloading Grafana dashboards (ConfigMaps)..."

kubectl create namespace monitoring 2>/dev/null || true

kubectl delete configmap grafana-dash-kong -n monitoring --ignore-not-found
kubectl delete configmap grafana-dash-ai-backend -n monitoring --ignore-not-found

kubectl create configmap grafana-dash-kong \
  -n monitoring \
  --from-file=kong.json=k8s/grafana/dashboards/kong.json

kubectl create configmap grafana-dash-ai-backend \
  -n monitoring \
  --from-file=ai-backend.json=k8s/grafana/dashboards/ai-backend.json

kubectl label configmap grafana-dash-kong -n monitoring grafana_dashboard=1 --overwrite
kubectl label configmap grafana-dash-ai-backend -n monitoring grafana_dashboard=1 --overwrite

echo "✅ Dashboards ConfigMaps created. Grafana sidecar will auto-import them."
