#!/usr/bin/env bash
set -euo pipefail

echo "📊 Installing metrics-server + Prometheus + Grafana..."

# 1) metrics-server (needed for HPA)
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# 2) kube-prometheus-stack
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1 || true

kubectl create namespace monitoring 2>/dev/null || true

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set grafana.service.type=NodePort \
  --set grafana.service.nodePort=32081 \
  --set prometheus.service.type=ClusterIP \
  --wait

echo "✅ Observability installed."
echo "   - Grafana: http://localhost:8081"
echo "   - Get Grafana password:"
echo "     kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo"
