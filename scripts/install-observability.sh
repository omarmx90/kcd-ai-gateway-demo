#!/usr/bin/env bash
set -euo pipefail

echo "📊 Installing metrics-server + Prometheus + Grafana..."

# ----------------------------
# 1) metrics-server (Kind-safe)
# ----------------------------
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Patch metrics-server for kind / local clusters (kubelet TLS + address types)
kubectl -n kube-system patch deployment metrics-server \
  --type='json' \
  -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"},
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname"}
  ]' || true

echo "⏳ Waiting for metrics-server to be ready..."
kubectl -n kube-system rollout status deployment/metrics-server --timeout=120s || true

echo "⏳ Waiting for metrics.k8s.io API to become available..."
for i in {1..30}; do
  if kubectl get --raw /apis/metrics.k8s.io/v1beta1 >/dev/null 2>&1; then
    echo "✅ metrics.k8s.io is available."
    break
  fi
  sleep 2
done

# ---------------------------------------------
# 2) kube-prometheus-stack (Prometheus + Grafana)
# ---------------------------------------------
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1 || true
kubectl create namespace monitoring 2>/dev/null || true

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set grafana.service.type=NodePort \
  --set grafana.service.nodePort=32081

echo "✅ Observability installed."
echo "   - Grafana: http://localhost:8081"
echo "   - Get Grafana password:"
echo "     kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo"

# ----------------------------
# 3) Scrape: Kong + Backend
# ----------------------------
kubectl apply -f k8s/kong-prometheus-plugin.yaml
kubectl apply -f k8s/servicemonitor-backend.yaml

# ----------------------------
# 4) Dashboards preload
# ----------------------------
./scripts/install-grafana-dashboards.sh

echo "✅ Observability fully configured."
