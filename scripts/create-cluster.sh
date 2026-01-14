#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="ai-gateway-cluster"

echo "🔧 Ensuring kind cluster ${CLUSTER_NAME} exists..."
if kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
  echo "✅ Cluster ${CLUSTER_NAME} already exists. Skipping creation."
  exit 0
fi

cat <<EOF | kind create cluster --name "${CLUSTER_NAME}" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 32080
        hostPort: 8080
        protocol: TCP
      - containerPort: 32081
        hostPort: 8081
        protocol: TCP
EOF

echo "✅ Cluster ${CLUSTER_NAME} created and mapped:"
echo "   - localhost:8080 → Kong proxy (NodePort 32080)"
echo "   - localhost:8081 → Grafana (NodePort 32081)"
 