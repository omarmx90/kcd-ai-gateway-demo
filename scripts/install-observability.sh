#!/usr/bin/env bash
set -euo pipefail

echo "📊 Installing metrics-server + Prometheus + Grafana..."

# 1) metrics-server
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Kind often needs insecure TLS + address preference
echo "🩹 Patching metrics-server for kind..."
kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname"}
]' >/dev/null 2>&1 || true

kubectl -n kube-system rollout status deploy/metrics-server --timeout=120s || true

# 2) kube-prometheus-stack (Prometheus + Grafana)
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
