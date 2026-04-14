#!/usr/bin/env bash
# Verify Prometheus is configured to scrape the FastAPI /metrics and list expected series names.
set -euo pipefail

NS_APP="ai-gateway-demo"
NS_MON="monitoring"
SM_NAME="ai-backend"

echo "=== 1) ServiceMonitor CR ==="
if ! kubectl get servicemonitor -n "${NS_MON}" "${SM_NAME}" -o name >/dev/null 2>&1; then
  echo "ServiceMonitor ${NS_MON}/${SM_NAME} missing. Run: make observability (applies k8s/servicemonitor-backend.yaml)"
  exit 1
fi
echo "ServiceMonitor present."

echo ""
echo "=== 2) Backend Service port 'http' (required by ServiceMonitor) ==="
if ! kubectl get svc -n "${NS_APP}" ai-backend -o jsonpath='{.spec.ports[?(@.name=="http")].port}' 2>/dev/null | grep -q .; then
  echo "Service ai-backend missing port name http"
  exit 1
fi
kubectl get svc -n "${NS_APP}" ai-backend -o jsonpath='{.spec.ports[?(@.name=="http")].port}' && echo " (http)"

echo ""
echo "=== 3) Sample /metrics from pod (metric name grep) ==="
kubectl exec -n "${NS_APP}" deploy/ai-backend -- sh -c \
  "wget -qO- http://127.0.0.1:8000/metrics 2>/dev/null || python -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode())\"" \
  | grep -E '^ai_' | cut -d'{' -f1 | sort -u

echo ""
echo "=== 4) Prometheus UI (manual) ==="
echo "  kubectl -n ${NS_MON} port-forward svc/kube-prometheus-stack-prometheus 9090:9090"
echo "  Open http://localhost:9090/targets — job for ai-gateway-demo/ai-backend should be UP."
echo "  Query e.g.: ai_requests_total, ai_estimated_cost_microdollars_total"

echo ""
echo "=== 5) Grafana ==="
echo "  make dashboards   # refresh ConfigMaps after editing k8s/grafana/dashboards/*.json"
echo "  Dashboard: AI Gateway - Backend Metrics (uid ai-backend-demo)"
