#!/usr/bin/env bash
set -euo pipefail

echo "📊 Installing metrics-server + Prometheus + Grafana..."
# 1) metrics-server 

kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# 2) kube-prometheus-stack (Prometheus + Grafana)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1 || true
kubectl create namespace monitoring 2>/dev/null || true

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
 --namespace monitoring \
 --set grafana.service.type=NodePort \
 --set grafana.service.nodePort=32082
 
echo "✅ Observability installed."
echo "   - Grafana NodePort: 32082 (http://localhost:8081 una vez mapeado el puerto)"
